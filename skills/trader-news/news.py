#!/usr/bin/env python3
"""News monitor for trader-news (India / NSE & BSE). Standard library only.

Watches Yahoo Finance (.NS) headlines and NSE / BSE corporate announcements for
the stocks you watch or hold and prints only what is new since the last check,
newest first, in Indian Standard Time (IST), with how far each stock has moved
from its previous close and tags on anything that touches your tickers.

    python news.py                                        # watchlist + plan + portfolio tickers
    python news.py --tickers RELIANCE TCS SBIN INFY
    python news.py --tickers RELIANCE --all --hours 72    # everything in the last 3 days, seen or not
    python news.py --no-nse                               # headlines only (skip NSE/BSE announcements)
    python news.py --no-moves                             # skip the price line per ticker

Your tickers, when --tickers is not given: every hit in trader/watchlist.json
(trader-scanner), the symbol in trader/plan.json (trader-exit) and the
positions in trader/portfolio.json.

Sources:
  - Yahoo Finance headline RSS (.NS symbol):
    https://feeds.finance.yahoo.com/rss/2.0/headline?s=<SYMBOL>.NS&region=IN&lang=en-IN
  - NSE Corporate Announcements (replaces US SEC 8-K):
    https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=<SYMBOL>&from_date=DD-MM-YYYY&to_date=DD-MM-YYYY
    Uses the NSE cookie dance (GET https://www.nseindia.com first with a browser
    User-Agent and cookie jar, then call the JSON API). If NSE blocks or returns
    401/403, falls back to NSE's official archive RSS feed
    (https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml) and
    BSE corporate announcements without crashing.
  - The move: Yahoo Finance's v8 chart endpoint (unofficial, can lag), last
    price against previous close:
    https://query2.finance.yahoo.com/v8/finance/chart/<SYMBOL>.NS?range=1d&interval=5m

Times print in Indian Standard Time (IST, Asia/Kolkata, UTC+05:30).
State: trader/state/news.json remembers what you have seen (30 days), so an
item shows once. Offline: --offline DIR reads yahoo-<SYMBOL>.xml,
chart-<SYMBOL>.json, and nse-<SYMBOL>.json from DIR.

Headlines are leads, not facts: open the story or exchange filing before acting.
Educational tool, not SEBI-registered investment advice. Trading loses most people money.
"""

import argparse
import datetime as _dt
import email.utils
import gzip
import hashlib
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

try:
    from zoneinfo import ZoneInfo
    try:
        IST_TZ = ZoneInfo("Asia/Kolkata")
    except Exception:
        IST_TZ = _dt.timezone(_dt.timedelta(hours=5, minutes=30))
except ImportError:
    IST_TZ = _dt.timezone(_dt.timedelta(hours=5, minutes=30))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

IST_OFFSET = _dt.timedelta(hours=5, minutes=30)

YAHOO_RSS = "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%s&region=IN&lang=en-IN"
CHART = "https://%s.finance.yahoo.com/v8/finance/chart/%s?range=1d&interval=5m"
NSE_HOME = "https://www.nseindia.com/market-data/live-equity-market"
NSE_ANN_API = "https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=%s&from_date=%s&to_date=%s"
NSE_RSS_FALLBACK = "https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml"
BSE_ANN_API = ("https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
               "?pageno=1&strCat=-1&strPrevDate=%s&strScrip=&strSearch=P&strToDate=%s&strType=C&subcategory=-1")

YAHOO_UA = "Mozilla/5.0 (compatible; day-trader-skill/2.0; +https://github.com/Jakeschincariol/day-trader-skill)"
NSE_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0"
KEEP_DAYS = 30
MOVE_PCT = 3.0  # a stock this far from previous close is flagged as MOVING
DISCLAIMER = "Educational tool, not SEBI-registered investment advice. Trading loses most people money."

KEYWORDS = [
    ("results", (r"\bresults?\b|\bearnings?\b|\bquarterly\b|\bQ[1-4]\s*FY|\bnet profit\b|\bPAT\b"
                 r"|\bEBITDA\b|\brevenue\b|\bEPS\b|\bguidance\b|\bun-?audited financial")),
    ("board meeting", r"\bboard meeting\b|\boutcome of board\b|\bAGM\b|\bEGM\b|\bpostal ballot\b"),
    ("dividend", r"\bdividend\b|\binterim dividend\b|\bfinal dividend\b|\brecord date\b"),
    ("bonus/split", (r"\bbonus\b|\bstock split\b|\bsub-?division\b|\brights issue\b"
                     r"|\bbuyback\b|\bQIP\b|\bpreferential\b|\ballotment\b")),
    ("pledge", r"\bpledg(?:e|ed|ing)\b|\bencumbrance\b|\binvocation\b|\brelease of pledge\b"),
    ("SAST", (r"\bSAST\b|\bReg(?:ulation)?\s*29\b|\bsubstantial acquisition\b|\binsider trading\b"
              r"|\bPIT\b|\bbulk deal\b|\bblock deal\b|\bstake\b")),
    ("order win", (r"\border(?:\s+win|\s+worth|\s+book)?\b|\bletter of award\b|\bLOA\b|\bLOI\b"
                   r"|\bbagged\b|\bcontract worth\b|\bwork order\b")),
    ("rating", (r"\bcredit rating\b|\bCRISIL\b|\bICRA\b|\bCARE\b|\bIndia Ratings\b|\bFitch\b|\bMoody"
                r"|\bupgrade[sd]?\b|\bdowngrade[sd]?\b|price target|\boutperform\b|\bunderperform\b")),
    ("deal", (r"\bacqui(?:re|res|red|sition)\b|\bmerger\b|\bdemerger\b|\bNCLT\b|\bslump sale\b"
              r"|\bjoint venture\b|\bJV\b|\bMoU\b|\bbuyout\b|\btakeover\b")),
    ("people", (r"\bCEO\b|\bCFO\b|\bMD\b|\bManaging Director\b|\bKey Managerial\b|\bKMP\b"
                r"|\bresign|\bsteps down\b|\bappoint|\bauditor\b")),
    ("regulatory/SEBI", (r"\bSEBI\b|\bshow cause\b|\bpenalty\b|\badjudication\b|\bUSFDA\b|\bFDA\b"
                         r"|\bForm 483\b|\bEIR\b|\bCCI\b|\bNCLAT\b|\bGST\b|\btax demand\b"
                         r"|\bhalt\b|\bsuspen|\bASM\b|\bGSM\b")),
    ("macro/RBI", (r"\bRBI\b|\bMPC\b|\brepo rate\b|\bCRR\b|\bCPI\b|\bWPI\b|\binflation\b"
                   r"|\bGDP\b|\bFII\b|\bFPI\b|\bDII\b|\brupee\b|\bNifty\b|\bSensex\b|\btariffs?\b")),
]

ROUTINE_NSE_SUBJECTS = re.compile(
    r"copy of newspaper|loss/duplicate|share certificate|closure of trading window"
    r"|esop|esos|investor grievance|compliance certificate|reg\.\s*74",
    re.I,
)
IMPORTANT_NSE_SUBJECTS = re.compile(
    r"financial result|outcome of board|dividend|bonus|split|sub-?division|buyback"
    r"|pledge|encumbrance|sast|reg\w*\s*29|acquisition|merger|demerger|order|award|contract"
    r"|credit rating|resignation|auditor|sebi|penalty|litigation|default|insolvency|press release",
    re.I,
)


class NewsError(Exception):
    pass


# ---------------------------------------------------------------- time (IST)

def to_ist(utc):
    """Aware or naive UTC -> naive IST wall-clock time (Asia/Kolkata, UTC+05:30)."""
    if utc.tzinfo is not None:
        utc = utc.astimezone(_dt.timezone.utc)
    return utc.replace(tzinfo=None) + IST_OFFSET


to_eastern = to_ist  # backwards-compatible alias


def _indian_int(n):
    s = str(abs(int(n)))
    if len(s) <= 3:
        out = s
    else:
        last3 = s[-3:]
        rest = s[:-3]
        groups = []
        while rest:
            groups.append(rest[-2:])
            rest = rest[:-2]
        out = ",".join(reversed(groups)) + "," + last3
    return ("-" + out) if n < 0 else out


def inr(x):
    """Format INR as ₹1,23,456.75."""
    val = round(float(x), 2)
    sign = "-" if val < 0 else ""
    val = abs(val)
    whole = int(val)
    frac = int(round((val - whole) * 100))
    if frac == 100:
        whole += 1
        frac = 0
    return "%s₹%s.%02d" % (sign, _indian_int(whole), frac)


# ---------------------------------------------------------------- symbols & inputs

def norm_symbol(ticker):
    s = (ticker or "").strip().upper()
    if s.startswith("^"):
        return s
    for suf in (".NS", ".BO"):
        if s.endswith(suf):
            return s[:-len(suf)]
    return s


def yahoo_symbol(ticker):
    s = (ticker or "").strip().upper()
    if s.startswith("^") or s.endswith(".NS") or s.endswith(".BO"):
        return s
    return s + ".NS"


def load_json(path):
    if path and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8-sig") as fh:
                return json.load(fh)
        except Exception:
            return None
    return None


def load_universe_names(path="trader/universe.txt"):
    names = {}
    if path and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8-sig") as fh:
                for ln in fh:
                    if "|" in ln and not ln.startswith("#"):
                        sym, nm = ln.strip().split("|", 1)
                        names[norm_symbol(sym)] = nm.strip()
        except Exception:
            pass
    return names


def watch_from_files(portfolio="trader/portfolio.json", watchlist="trader/watchlist.json",
                     plan="trader/plan.json", universe="trader/universe.txt"):
    """{ticker: name or None}: the scanner's hits, the planned trade and your holdings."""
    out = {}
    unames = load_universe_names(universe)
    wl = load_json(watchlist) or {}
    for h in wl.get("hits", []):
        sym = h.get("symbol") or h.get("ticker")
        if sym:
            s = norm_symbol(sym)
            out[s] = h.get("name") or unames.get(s)
    pl = load_json(plan) or {}
    if pl.get("symbol"):
        s = norm_symbol(pl["symbol"])
        out.setdefault(s, pl.get("name") or unames.get(s))
    pf = load_json(portfolio) or {}
    for p in pf.get("positions", []):
        sym = p.get("ticker") or p.get("symbol")
        if sym:
            s = norm_symbol(sym)
            out[s] = p.get("name") or out.get(s) or unames.get(s)
    return out


# ---------------------------------------------------------------- fetching

class Fetcher(object):
    def __init__(self, offline=None, pause=0.2):
        self.offline = offline
        self.pause = pause
        self._last = 0.0
        self._cj = http.cookiejar.CookieJar()
        self._nse_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self._cj))
        self._nse_warmed = False

    def _throttle(self):
        gap = time.time() - self._last
        if gap < self.pause:
            time.sleep(self.pause - gap)
        self._last = time.time()

    def _warm_nse(self):
        if self._nse_warmed or self.offline:
            return
        self._throttle()
        headers = {
            "User-Agent": NSE_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        try:
            req = urllib.request.Request(NSE_HOME, headers=headers)
            with self._nse_opener.open(req, timeout=15) as r:
                r.read()
            self._nse_warmed = True
        except Exception as exc:
            raise NewsError("NSE cookie handshake failed: %s" % exc)

    def get(self, url, name, nse_api=False):
        if self.offline:
            path = os.path.join(self.offline, name)
            if not os.path.isfile(path):
                raise NewsError("offline: no file %s" % path)
            with open(path, "rb") as fh:
                return fh.read()
        if nse_api:
            self._warm_nse()
            self._throttle()
            headers = {
                "User-Agent": NSE_UA,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
            }
            req = urllib.request.Request(url, headers=headers)
            try:
                with self._nse_opener.open(req, timeout=20) as resp:
                    data = resp.read()
                    if resp.headers.get("Content-Encoding") == "gzip":
                        data = gzip.decompress(data)
                    return data
            except urllib.error.HTTPError as exc:
                raise NewsError("HTTP %s for %s" % (exc.code, url))
            except urllib.error.URLError as exc:
                raise NewsError("could not reach %s: %s" % (url, exc.reason))

        self._throttle()
        is_nse_arch = "nseindia.com" in url or "bseindia.com" in url
        headers = {
            "User-Agent": NSE_UA if is_nse_arch else YAHOO_UA,
            "Accept": "*/*",
            "Accept-Encoding": "gzip",
        }
        if "bseindia.com" in url:
            headers["Origin"] = "https://www.bseindia.com"
            headers["Referer"] = "https://www.bseindia.com/"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                data = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
                return data
        except urllib.error.HTTPError as exc:
            raise NewsError("HTTP %s for %s" % (exc.code, url))
        except urllib.error.URLError as exc:
            raise NewsError("could not reach %s: %s" % (url, exc.reason))


# ---------------------------------------------------------------- parsing

def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _text(el, name):
    for c in el.iter():
        if _local(c.tag) == name:
            return (c.text or "").strip()
    return ""


def parse_time(value, default_ist=False):
    """RFC 822 (RSS), ISO 8601, or NSE timestamp ('31-Oct-2026 17:34:03' / '2026-10-31 17:34:03')
    -> aware UTC datetime, or None."""
    value = (value or "").strip()
    if not value:
        return None
    # Try NSE 'DD-Mon-YYYY HH:MM:SS' or 'DD-Mon-YYYY HH:MM'
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            naive = _dt.datetime.strptime(value, fmt)
            # NSE timestamps and naive --now inputs are IST
            return (naive - IST_OFFSET).replace(tzinfo=_dt.timezone.utc)
        except ValueError:
            pass
    try:
        d = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        d = None
    if d is None:
        try:
            d = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        if default_ist:
            return (d - IST_OFFSET).replace(tzinfo=_dt.timezone.utc)
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d.astimezone(_dt.timezone.utc)


def parse_rss(data, ticker):
    """Yahoo headline RSS -> items."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise NewsError("RSS for %s is not valid XML: %s" % (ticker, exc))
    out = []
    for it in root.iter("item"):
        title = _text(it, "title")
        link = _text(it, "link")
        guid = _text(it, "guid") or link
        out.append({"id": "rss:" + guid, "source": "Yahoo Finance", "kind": "headline",
                    "ticker": ticker, "title": title, "summary": _text(it, "description"),
                    "link": link, "time": parse_time(_text(it, "pubDate"))})
    return out


def parse_nse_announcements(data, ticker):
    """NSE corporate-announcements JSON -> items."""
    try:
        rows = json.loads(data.decode("utf-8", "replace") if isinstance(data, bytes) else data)
    except ValueError as exc:
        raise NewsError("NSE announcements for %s is not valid JSON: %s" % (ticker, exc))
    if not isinstance(rows, list):
        return []
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        sym = norm_symbol(r.get("symbol") or ticker)
        desc = " ".join((r.get("desc") or "Announcement").split())
        text = " ".join((r.get("attchmntText") or "").split())
        seq = str(r.get("seq_id") or r.get("dt") or hashlib.sha1((desc + text).encode("utf-8")).hexdigest()[:12])
        link = (r.get("attchmntFile") or "").strip()
        if not link:
            link = "https://www.nseindia.com/companies-listing/corporate-filings-announcements"
        when = parse_time(r.get("sort_date") or r.get("an_dt") or r.get("exchdisstime"), default_ist=True)
        short_text = text[:160] + ("..." if len(text) > 160 else "")
        title = "%s — %s: %s" % (sym, desc, short_text) if short_text else "%s — %s" % (sym, desc)
        combined = "%s %s" % (desc, text)
        important = bool(IMPORTANT_NSE_SUBJECTS.search(combined)) and not bool(ROUTINE_NSE_SUBJECTS.search(desc))
        out.append({
            "id": "nse:" + seq,
            "source": "NSE Filing",
            "kind": "filing",
            "category": desc,
            "company": r.get("sm_name") or sym,
            "ticker": sym,
            "title": title,
            "summary": text,
            "important": important,
            "link": link,
            "time": when,
        })
    return out


def parse_nse_rss_fallback(data, watch):
    """Parse https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml and filter to watched stocks."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise NewsError("NSE RSS fallback is not valid XML: %s" % exc)
    out = []
    for it in root.iter("item"):
        comp = _text(it, "title")
        link = _text(it, "link")
        desc = _text(it, "description")
        pub = _text(it, "pubDate")
        matched = None
        comp_low = comp.lower()
        link_up = link.upper()
        for sym, nm in watch.items():
            if ("/" + sym + "_") in link_up or (nm and nm.lower()[:12] in comp_low):
                matched = sym
                break
        if not matched:
            continue
        subj = "Announcement"
        if "|SUBJECT:" in desc:
            body, subj = desc.split("|SUBJECT:", 1)
            desc = body.strip()
            subj = subj.strip()
        guid = link or (matched + "|" + pub)
        combined = "%s %s" % (subj, desc)
        important = bool(IMPORTANT_NSE_SUBJECTS.search(combined)) and not bool(ROUTINE_NSE_SUBJECTS.search(subj))
        out.append({
            "id": "nserss:" + guid,
            "source": "NSE Filing",
            "kind": "filing",
            "category": subj,
            "company": comp,
            "ticker": matched,
            "title": "%s — %s: %s" % (matched, subj, desc[:160]),
            "summary": desc,
            "important": important,
            "link": link or "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
            "time": parse_time(pub, default_ist=True),
        })
    return out


def parse_chart(data, ticker):
    """Yahoo chart JSON -> {"ticker", "price", "prev_close", "pct", "time"} or raise."""
    try:
        doc = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
        meta = doc["chart"]["result"][0]["meta"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise NewsError("chart for %s is not usable" % ticker)
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    if not price or not prev:
        raise NewsError("chart for %s has no price" % ticker)
    when = meta.get("regularMarketTime")
    return {
        "ticker": ticker,
        "yahoo": yahoo_symbol(ticker),
        "price": round(float(price), 2),
        "prev_close": round(float(prev), 2),
        "pct": round((float(price) / float(prev) - 1) * 100, 2),
        "time": _dt.datetime.fromtimestamp(when, _dt.timezone.utc) if when else None,
    }


def collect_moves(fetcher, watch, errors=None):
    errors = [] if errors is None else errors
    out = []
    for t in sorted(watch):
        name = "chart-%s.json" % t
        ysym = urllib.parse.quote(yahoo_symbol(t), safe="")
        try:
            try:
                data = fetcher.get(CHART % ("query2", ysym), name)
            except NewsError as exc:
                if fetcher.offline or not re.search(r"HTTP (429|5\d\d)", str(exc)):
                    raise
                data = fetcher.get(CHART % ("query1", ysym), name)
            out.append(parse_chart(data, t))
        except NewsError as exc:
            if not (fetcher.offline and "no file" in str(exc)):
                errors.append("price for %s: %s" % (t, exc))
    return out


def mention_patterns(watch):
    pats = {}
    for t, name in watch.items():
        alts = [
            r"\(%s\)" % re.escape(t),
            r"\b%s\.NS\b" % re.escape(t),
            r"\b(?:NSE|BSE)\s*:\s*%s\b" % re.escape(t),
        ]
        if len(t) >= 4:
            alts.append(r"\b%s\b" % re.escape(t))
        if name and len(name) >= 4:
            # Strip trailing "Limited" / "Ltd." for cleaner matching in headlines
            clean_name = re.sub(r"\s+(?:limited|ltd\.?)$", "", name, flags=re.I).strip()
            if len(clean_name) >= 4:
                alts.append(r"\b%s\b" % re.escape(clean_name).replace(r"\ ", r"[\s-]"))
        pats[t] = re.compile("|".join(alts), re.I)
    return pats


def tag(items, watch):
    """Which of your tickers each item touches, and why it matters."""
    pats = mention_patterns(watch)
    for it in items:
        hay = "%s %s %s" % (it["title"], it.get("summary") or "", it.get("category") or "")
        touches = {it["ticker"]} if it["ticker"] in watch else set()
        for t, rx in pats.items():
            if rx.search(hay):
                touches.add(t)
        it["touches"] = sorted(touches)
        it["tags"] = [label for label, rx in KEYWORDS if re.search(rx, hay, re.I)]
    return items


def dedupe(items):
    """One item per story: same id, or same link, or same title the same day."""
    seen, out = {}, []
    for it in items:
        keys = [it["id"], it["link"],
                "%s|%s" % (re.sub(r"\W+", " ", it["title"].lower()).strip(),
                           to_ist(it["time"]).date() if it["time"] else "")]
        hit = next((seen[k] for k in keys if k and k in seen), None)
        if hit is not None:
            for t in it.get("touches", []):
                if t not in hit["touches"]:
                    hit["touches"].append(t)
            hit["touches"].sort()
            continue
        for k in keys:
            if k:
                seen[k] = it
        out.append(it)
    return out


def fingerprint(it):
    return hashlib.sha1(it["id"].encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- the check

def collect(fetcher, watch, now, nse=True, hours=48, errors=None):
    errors = [] if errors is None else errors
    items = []
    for t in sorted(watch):
        ysym = urllib.parse.quote(yahoo_symbol(t), safe="")
        try:
            items += parse_rss(fetcher.get(YAHOO_RSS % ysym, "yahoo-%s.xml" % t), t)
        except NewsError as exc:
            if not (fetcher.offline and "no file" in str(exc)):
                errors.append("Yahoo headlines for %s: %s" % (t, exc))
    if nse:
        now_ist_dt = to_ist(now)
        lookback_days = max(3, int(hours // 24) + 2)
        from_d = (now_ist_dt.date() - _dt.timedelta(days=lookback_days)).strftime("%d-%m-%Y")
        to_d = now_ist_dt.date().strftime("%d-%m-%Y")
        nse_api_failed = False
        for t in sorted(watch):
            url = NSE_ANN_API % (urllib.parse.quote(t, safe=""), from_d, to_d)
            try:
                data = fetcher.get(url, "nse-%s.json" % t, nse_api=True)
                items += parse_nse_announcements(data, t)
            except NewsError as exc:
                if fetcher.offline and "no file" in str(exc):
                    continue
                nse_api_failed = True
                errors.append("NSE announcements API for %s: %s" % (t, exc))
                break
        if nse_api_failed and not fetcher.offline:
            # Fallback to NSE's cookie-free archive RSS feed
            try:
                rss_data = fetcher.get(NSE_RSS_FALLBACK, "nse-online.xml", nse_api=False)
                items += parse_nse_rss_fallback(rss_data, watch)
            except NewsError as exc:
                errors.append("NSE RSS fallback: %s" % exc)
    return items


def select(items, state, now, hours=48, show_all=False):
    """Items within the window; only unseen ones unless show_all."""
    seen = state.setdefault("seen", {})
    cutoff = now - _dt.timedelta(hours=hours)
    ahead = now + _dt.timedelta(minutes=10)  # a pinned --now: nothing from after it
    out = []
    for it in items:
        if it["time"] is not None and (it["time"] < cutoff or it["time"] > ahead):
            continue
        fp = fingerprint(it)
        it["new"] = fp not in seen
        if it["new"] or show_all:
            out.append(it)
    out.sort(key=lambda i: (i["time"] or now), reverse=True)
    return out


def remember(items, state, now):
    seen = state.setdefault("seen", {})
    stamp = now.isoformat()
    for it in items:
        seen.setdefault(fingerprint(it), stamp)
    cutoff = (now - _dt.timedelta(days=KEEP_DAYS)).isoformat()
    state["seen"] = {k: v for k, v in seen.items() if v >= cutoff}
    state["last_check"] = stamp
    state["last_check_ist"] = to_ist(now).strftime("%Y-%m-%d %H:%M:%S IST")


def render_moves(moves, move_pct=MOVE_PCT):
    out = []
    for m in moves:
        flag = "MOVING" if abs(m["pct"]) >= move_pct else ""
        when = (" at %s IST" % to_ist(m["time"]).strftime("%H:%M")) if m.get("time") else ""
        out.append("%-12s %12s %+6.2f%% vs previous close %s%s  %s" % (
            m["ticker"], inr(m["price"]), m["pct"], inr(m["prev_close"]),
            when, flag))
    return out


def render(items, errors, now, moves=None, move_pct=MOVE_PCT):
    out = render_moves(moves or [], move_pct)
    if out:
        out.append("")
    if not items:
        out.append("Nothing new.")
    for it in items:
        when = to_ist(it["time"]).strftime("%Y-%m-%d %H:%M IST") if it["time"] else "time unknown"
        mark = "!" if it.get("important") else " "
        who = ",".join(it["touches"]) or it["ticker"]
        out.append("%s %s  %-12s %-14s %s" % (mark, when, who, it["source"], it["title"]))
        extra = []
        if it["tags"]:
            extra.append("tags: " + ", ".join(it["tags"]))
        extra.append(it["link"])
        out.append("      " + "  ".join(extra))
    for e in errors:
        out.append("note: %s" % e)
    out.append("Checked %s. Headlines and filings are leads, not facts; open the source before acting. %s"
               % (to_ist(now).strftime("%Y-%m-%d %H:%M IST"), DISCLAIMER))
    return "\n".join(out) + "\n"


def _json_ready(items):
    out = []
    for it in items:
        d = dict(it)
        d["time"] = it["time"].isoformat() if it["time"] else None
        d["time_ist"] = to_ist(it["time"]).strftime("%Y-%m-%d %H:%M:%S IST") if it["time"] else None
        out.append(d)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tickers", nargs="*", help="NSE symbols (default: watchlist + plan + portfolio)")
    ap.add_argument("--portfolio", default="trader/portfolio.json")
    ap.add_argument("--watchlist", default="trader/watchlist.json")
    ap.add_argument("--plan", default="trader/plan.json")
    ap.add_argument("--state", default="trader/state/news.json")
    ap.add_argument("--hours", type=float, default=48, help="ignore items older than this (default 48)")
    ap.add_argument("--all", action="store_true", help="show seen items too")
    ap.add_argument("--no-nse", action="store_true", help="headlines only (skip NSE corporate announcements)")
    ap.add_argument("--no-sec", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--no-moves", action="store_true", help="skip the price line per ticker")
    ap.add_argument("--move-pct", type=float, default=MOVE_PCT, help="flag a move this big (percent)")
    ap.add_argument("--config", default="trader/config.json")
    ap.add_argument("--offline", metavar="DIR")
    ap.add_argument("--now", help="IST or ISO time, for reproducible runs (e.g. \"2026-10-30 10:30\")")
    ap.add_argument("--dry-run", action="store_true", help="do not update the state file")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    known = watch_from_files(args.portfolio, args.watchlist, args.plan)
    unames = load_universe_names()
    if args.tickers:
        raw = []
        for tok in args.tickers:
            raw.extend(s for s in re.split(r"[,\s]+", tok) if s)
        watch = {}
        for t in raw:
            s = norm_symbol(t)
            watch[s] = known.get(s) or unames.get(s)
    else:
        watch = known
    if not watch:
        print("news: no tickers. Pass --tickers RELIANCE TCS, or run /trader-scanner first (it writes "
              "trader/watchlist.json).", file=sys.stderr)
        return 2
    now = parse_time(args.now, default_ist=True) if args.now else _dt.datetime.now(_dt.timezone.utc)
    if now is None:
        print('news: --now wants a time like "2026-10-30 10:30" (IST) or ...Z (UTC)', file=sys.stderr)
        return 2
    state = load_json(args.state) or {}
    errors = []
    nse = not (args.no_nse or args.no_sec)
    fetcher = Fetcher(offline=args.offline)
    items = collect(fetcher, watch, now, nse=nse, hours=args.hours, errors=errors)
    items = dedupe(tag(items, watch))
    shown = select(items, state, now, hours=args.hours, show_all=args.all)
    moves = [] if args.no_moves else collect_moves(fetcher, watch, errors)
    if args.json:
        mv = [dict(m, time=m["time"].isoformat() if m.get("time") else None,
                   time_ist=to_ist(m["time"]).strftime("%Y-%m-%d %H:%M:%S IST") if m.get("time") else None,
                   moving=abs(m["pct"]) >= args.move_pct) for m in moves]
        print(json.dumps({
            "checked": now.isoformat(),
            "now_ist": to_ist(now).strftime("%Y-%m-%d %H:%M:%S IST"),
            "moves": mv,
            "items": _json_ready(shown),
            "notes": errors,
        }, indent=2, ensure_ascii=False))
    else:
        sys.stdout.write(render(shown, errors, now, moves, args.move_pct))
    if not args.dry_run:
        remember(items, state, now)
        folder = os.path.dirname(args.state)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(args.state, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
    return 0 if any(i["new"] for i in shown) else 1


if __name__ == "__main__":
    sys.exit(main())
