#!/usr/bin/env python3
"""Price alerts and the NSE clock for trader-alerts (India / NSE). Standard library only.

    python alerts.py clock                             # is the NSE open? next open / close (IST)
    python alerts.py quote RELIANCE --write            # quotes -> trader/prices.json
    python alerts.py quote --holdings --write          # every ticker in your portfolio and plan
    python alerts.py add --from-plan                   # stop, target (+ flat-by 15:10 for intraday; stop+target for swing)
    python alerts.py add --symbol RELIANCE --stop 1150.00
    python alerts.py add --symbol RELIANCE --target 1185.00
    python alerts.py add --at 15:10 --label "flat by close"
    python alerts.py add --square-off --symbol RELIANCE
    python alerts.py add --pre-open
    python alerts.py add --opening-bell
    python alerts.py add --closing-bell
    python alerts.py add --symbol TCS --pct 3
    python alerts.py check                             # rings what hit, once; exit 1 if nothing rang
    python alerts.py check --now "2026-10-30 15:10"
    python alerts.py list
    python alerts.py reset reliance-plan-stop

Rule types:
  above / target  the price is at or above "price" (INR)
  below / stop    the price is at or below "price" (INR)
  pct_move        the price moved "pct" percent or more from the previous close
                  (or from "ref" if you give one); "direction": "up" or "down"
  pre_open        NSE pre-open session started today at 09:00 IST (rings once per trading day)
  opening_bell    the NSE opened today at 09:15 IST (rings once per trading day)
  closing_bell    the NSE closed today at 15:30 IST (rings once per trading day)
  time            the IST clock passed "at" (HH:MM) on a trading day
                  (rings once per trading day), e.g. 15:10 MIS auto square-off

A rule with "date" is only watched on that IST date (intraday rules from a plan
are for the plan's day only; swing rules omit "date" so they stay active across
holding days). An above / below alert rings ONCE and then stays quiet until you
reset it, unless the rule has "rearm": true (it re-arms when the price crosses
back). A pct_move alert rings at most once per day.

Quotes (free, delayed, unofficial; for alerts only, never for timing an order):
  yahoo   https://query2.finance.yahoo.com/v8/finance/chart/RELIANCE.NS (falls
          back to query1 on a 429 or a 5xx; one retry with a pause on a 429;
          one request per ticker, half a second apart; the default)
  --offline DIR reads DIR/<source>-<TICKER>.json|csv (or .json.gz) instead.

NSE calendar: 2026 trading holidays (with optional live fetch cached to
trader/cache/nse_holidays.json). 2027 holiday list is not yet published in code
("2027 holidays unknown, check NSE"). Muhurat trading on Sun 2026-11-08 is
treated as a special session (do not auto-open; check NSE circular).

Educational tool, not SEBI-registered investment advice. Trading loses most people money.
"""

import argparse
import csv
import datetime
import gzip
import http.cookiejar
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

try:
    from zoneinfo import ZoneInfo
    IST_TZ = ZoneInfo("Asia/Kolkata")
except Exception:
    IST_TZ = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

IST_OFFSET = datetime.timedelta(hours=5, minutes=30)

UA = "Mozilla/5.0"
NSE_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0"
PAUSE = 0.5    # seconds between requests: be polite to free endpoints
BACKOFF = 2.0  # seconds to wait before the one retry after a 429
YAHOO_HOSTS = ("query2.finance.yahoo.com", "query1.finance.yahoo.com")
HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

NSE_HOLIDAYS_2026 = {
    "2026-01-15": "Municipal Corporation Election / Makar Sankranti",
    "2026-01-26": "Republic Day",
    "2026-03-03": "Holi",
    "2026-03-26": "Shri Ram Navami",
    "2026-03-31": "Id-Ul-Fitr (Ramzan Id)",
    "2026-04-03": "Good Friday",
    "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2026-05-01": "Maharashtra Day",
    "2026-05-28": "Bakri Id",
    "2026-06-26": "Muharram",
    "2026-09-14": "Ganesh Chaturthi",
    "2026-10-02": "Mahatma Gandhi Jayanti",
    "2026-10-20": "Dussehra",
    "2026-11-10": "Diwali-Balipratipada",
    "2026-11-24": "Prakash Gurpurb Sri Guru Nanak Dev",
    "2026-12-25": "Christmas",
}
MUHURAT_SESSIONS = {
    "2026-11-08": "Diwali Laxmi Pujan (Muhurat trading: special session, check NSE)",
}
CALENDAR_YEARS = (2026,)
PRE_OPEN_START = datetime.time(9, 0)
PRE_OPEN_MATCH = datetime.time(9, 8)
OPEN = datetime.time(9, 15)
FLAT_BY_DEFAULT = "15:10"
CLOSE = datetime.time(15, 30)
CLOSING_SESSION_START = datetime.time(15, 40)
CLOSING_SESSION_END = datetime.time(16, 0)
HOLIDAY_CACHE_PATH = "trader/cache/nse_holidays.json"


class AlertError(Exception):
    pass


# ---------------------------------------------------------------- formatting & symbols

def fmt_inr(val):
    """Format a number in INR with ₹ and Indian grouping (e.g. ₹1,234.50 or ₹1,00,000.00)."""
    if val is None:
        return "₹0.00"
    v = float(val)
    neg = "-" if v < 0 else ""
    v = abs(v)
    whole, frac = ("%0.2f" % v).split(".")
    if len(whole) > 3:
        last3 = whole[-3:]
        rest = whole[:-3]
        groups = []
        while len(rest) > 2:
            groups.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            groups.insert(0, rest)
        whole = ",".join(groups + [last3])
    return "%s₹%s.%s" % (neg, whole, frac)


def _n(x):
    x = float(x)
    return str(int(x)) if x == int(x) else "%.2f" % x


def clean_symbol(sym):
    """Plain NSE symbol for storage/display: 'RELIANCE.NS' -> 'RELIANCE', '^NSEI' -> '^NSEI'."""
    s = str(sym or "").strip().upper()
    if s.endswith(".NS"):
        return s[:-3]
    if s.endswith(".BO"):
        return s[:-3]
    return s


def yahoo_symbol(sym):
    """Yahoo chart ticker: 'RELIANCE' -> 'RELIANCE.NS', '^NSEI' -> '^NSEI', 'TCS.BO' -> 'TCS.BO'."""
    s = str(sym or "").strip().upper()
    if not s:
        return s
    if s.startswith("^") or s.endswith(".NS") or s.endswith(".BO"):
        return s
    return s + ".NS"


# ---------------------------------------------------------------- time & NSE calendar

def to_ist(utc):
    """Naive UTC -> naive IST (UTC+05:30, no DST)."""
    return utc.replace(tzinfo=None) + IST_OFFSET


def ist_to_utc(local):
    """Naive IST -> naive UTC."""
    return local.replace(tzinfo=None) - IST_OFFSET


# Backward-compatible aliases
to_eastern = to_ist
eastern_to_utc = ist_to_utc


def parse_now(text):
    """ISO time -> naive UTC. A time without an offset is taken as IST (Asia/Kolkata)."""
    if not text:
        return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    t = text.strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise AlertError("--now wants a time like \"2026-11-02 10:15\" (IST) or ...Z (UTC)")
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def _fetch_nse_holidays_live(cache_path=HOLIDAY_CACHE_PATH, timeout=6):
    """Try live NSE holiday-master API with cookie handshake and cache to trader/cache/."""
    holidays = dict(NSE_HOLIDAYS_2026)
    if cache_path and os.path.exists(cache_path):
        try:
            with open(cache_path, encoding="utf-8-sig") as fh:
                cached = json.load(fh)
            if isinstance(cached, dict) and isinstance(cached.get("holidays"), dict):
                holidays.update(cached["holidays"])
                return holidays
        except Exception:
            pass
    try:
        cj = http.cookiejar.CookieJar()
        op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        hdrs = {
            "User-Agent": NSE_UA,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.5",
            "Connection": "keep-alive",
        }
        op.open(urllib.request.Request("https://www.nseindia.com/market-data/live-equity-market", headers=hdrs),
                timeout=timeout).read()
        api_hdrs = dict(hdrs, Referer="https://www.nseindia.com/market-data/live-equity-market")
        r = op.open(urllib.request.Request("https://www.nseindia.com/api/holiday-master?type=trading",
                                           headers=api_hdrs), timeout=timeout)
        data = json.loads(r.read().decode("utf-8", "replace"))
        for item in data.get("CM", []):
            raw_date = item.get("tradingDate", "")
            desc = item.get("description", "NSE holiday")
            try:
                dt = datetime.datetime.strptime(raw_date, "%d-%b-%Y").date().isoformat()
                if dt not in MUHURAT_SESSIONS:
                    holidays[dt] = desc
            except ValueError:
                pass
        if cache_path:
            _write(cache_path, {"as_of": datetime.date.today().isoformat(), "holidays": holidays})
    except Exception:
        pass
    return holidays


def trading_day(d, holidays=None):
    """(is_trading_day, reason). Weekends, Muhurat special session, and the NSE holiday list."""
    iso = d.isoformat()
    if iso in MUHURAT_SESSIONS:
        return False, "special session, check NSE (" + MUHURAT_SESSIONS[iso] + ")"
    if d.weekday() >= 5:
        return False, "weekend"
    hols = holidays if holidays is not None else NSE_HOLIDAYS_2026
    name = hols.get(iso)
    if name:
        return False, "NSE holiday: " + name
    return True, ""


def session(d, holidays=None):
    """(open_time, close_time) in IST, or None if closed all day."""
    ok, _ = trading_day(d, holidays)
    if not ok:
        return None
    return OPEN, CLOSE


def market_status(now_utc, fetch_holidays=False):
    local = to_ist(now_utc)
    d = local.date()
    hols = _fetch_nse_holidays_live() if fetch_holidays else NSE_HOLIDAYS_2026
    ok, reason = trading_day(d, hols)
    cal_known = d.year in CALENDAR_YEARS
    st = {
        "now_ist": local.strftime("%Y-%m-%d %H:%M"),
        "date": d.isoformat(),
        "weekday": local.strftime("%A"),
        "calendar_known": cal_known,
        "open": False,
        "session": "closed",
        "reason": reason,
    }
    if not cal_known and d.year == 2027:
        st["calendar_note"] = "2027 holidays unknown, check NSE"
    elif not cal_known:
        st["calendar_note"] = "%d holidays unknown, check NSE" % d.year

    s = session(d, hols)
    if s:
        o, c = s
        st["pre_open"] = PRE_OPEN_START.strftime("%H:%M")
        st["opens"] = o.strftime("%H:%M")
        st["flat_by"] = FLAT_BY_DEFAULT
        st["closes"] = c.strftime("%H:%M")
        t = local.time()
        if o <= t < c:
            st["open"] = True
            st["session"] = "regular"
            st["reason"] = "regular session"
        elif PRE_OPEN_START <= t < PRE_OPEN_MATCH:
            st["session"] = "pre_open_entry"
            st["reason"] = "pre-open order entry (09:00-09:08)"
        elif PRE_OPEN_MATCH <= t < o:
            st["session"] = "pre_open_matching"
            st["reason"] = "pre-open matching (09:08-09:15)"
        elif t < PRE_OPEN_START:
            st["session"] = "before_open"
            st["reason"] = "before the open"
        else:
            st["session"] = "after_close"
            st["reason"] = "after the close"
    # next open
    nd = d if (s and local.time() < s[0]) else d + datetime.timedelta(days=1)
    for _ in range(15):
        if session(nd, hols):
            st["next_open"] = "%s 09:15" % nd.isoformat()
            break
        nd += datetime.timedelta(days=1)
    return st


# ---------------------------------------------------------------- quotes

def stooq_url(sym):
    return "https://stooq.com/q/l/?s=%s.in&f=sd2t2ohlcv&h&e=csv" % clean_symbol(sym).lower()


def yahoo_url(sym, host=YAHOO_HOSTS[0]):
    ysym = yahoo_symbol(sym)
    enc = urllib.parse.quote(ysym, safe=".-_")
    return "https://%s/v8/finance/chart/%s?range=1d&interval=1d" % (host, enc)


def parse_stooq(text, sym):
    """Symbol,Date,Time,Open,High,Low,Close,Volume -> quote. N/D means no data."""
    csym = clean_symbol(sym)
    rows = list(csv.DictReader(io.StringIO(text.strip())))
    if not rows:
        raise AlertError("Stooq returned no rows for %s" % csym)
    r = {k.strip().lower(): (v or "").strip() for k, v in rows[0].items() if k}
    close = r.get("close", "")
    if not close or close.upper() == "N/D":
        raise AlertError("Stooq has no data for %s (check the symbol)" % csym)

    def f(k):
        v = r.get(k, "")
        try:
            return float(v)
        except ValueError:
            return None
    return {
        "symbol": csym,
        "yahoo": yahoo_symbol(sym),
        "price": float(close),
        "open": f("open"),
        "high": f("high"),
        "low": f("low"),
        "prev_close": None,
        "time": ("%s %s" % (r.get("date", ""), r.get("time", ""))).strip(),
        "currency": "INR",
        "source": "stooq",
    }


def parse_yahoo(text, sym):
    csym = clean_symbol(sym)
    ysym = yahoo_symbol(sym)
    try:
        data = json.loads(text)
        res = data["chart"]["result"]
    except (ValueError, KeyError, TypeError):
        raise AlertError("Yahoo returned something unexpected for %s" % ysym)
    if not res:
        err = (data.get("chart") or {}).get("error") or {}
        raise AlertError("Yahoo has no data for %s: %s" % (ysym, err.get("description", "unknown symbol")))
    m = res[0].get("meta", {})
    price = m.get("regularMarketPrice")
    if price is None:
        raise AlertError("Yahoo has no price for %s" % ysym)
    t = m.get("regularMarketTime")
    when = (datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if t else "")
    q = (res[0].get("indicators", {}).get("quote") or [{}])[0]
    opens = [x for x in (q.get("open") or []) if x is not None]
    return {
        "symbol": csym,
        "yahoo": ysym,
        "price": float(price),
        "open": opens[0] if opens else m.get("regularMarketOpen"),
        "high": m.get("regularMarketDayHigh"),
        "low": m.get("regularMarketDayLow"),
        "prev_close": m.get("previousClose") or m.get("chartPreviousClose"),
        "time": when,
        "currency": m.get("currency") or "INR",
        "source": "yahoo",
    }


_urlopen = urllib.request.urlopen   # tests swap these two
_sleep = time.sleep


def _get(url, timeout=20):
    """(HTTP status, body). A network failure is an AlertError."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with _urlopen(req, timeout=timeout) as r:
            return 200, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        finally:
            try:
                e.close()
            except Exception:
                pass
        return e.code, body
    except urllib.error.URLError as e:
        raise AlertError("could not reach %s: %s" % (url.split("/")[2], e.reason))


def fetch(url, timeout=20):
    code, text = _get(url, timeout)
    if code != 200:
        raise AlertError("%s answered HTTP %d" % (url.split("?")[0], code))
    return text


def fetch_yahoo(sym, timeout=20):
    """query2 first. A 429 waits BACKOFF seconds and tries once more; a 429 or a 5xx
    after that moves on to query1. A 404 comes back as Yahoo's own error JSON."""
    last = None
    for host in YAHOO_HOSTS:
        url = yahoo_url(sym, host)
        code, text = _get(url, timeout)
        if code == 429:
            _sleep(BACKOFF)
            code, text = _get(url, timeout)
        if code == 200:
            return text
        if code == 404 and text.strip().startswith("{"):
            return text
        last = (url, code)
        if code != 429 and code < 500:
            break
    raise AlertError("%s answered HTTP %d" % (last[0].split("?")[0], last[1]))


def _offline_text(folder, source, sym):
    ext = "csv" if source == "stooq" else "json"
    csym = clean_symbol(sym)
    for candidate in (csym, yahoo_symbol(sym)):
        base = os.path.join(folder, "%s-%s.%s" % (source, candidate, ext))
        if os.path.exists(base):
            with open(base, encoding="utf-8-sig") as fh:
                return fh.read()
        if os.path.exists(base + ".gz"):
            with gzip.open(base + ".gz", "rt", encoding="utf-8-sig") as fh:
                return fh.read()
    raise AlertError("no offline quote file %s" % os.path.join(folder, "%s-%s.%s" % (source, csym, ext)))


def get_quotes(symbols, source="yahoo", offline=None, pause=PAUSE):
    """{SYM: quote} and [errors]. One request per ticker, with a pause between."""
    quotes, errors = {}, []
    for i, raw_sym in enumerate(symbols):
        csym = clean_symbol(raw_sym)
        try:
            if offline:
                text = _offline_text(offline, source, raw_sym)
            else:
                if i and pause:
                    _sleep(pause)
                text = fetch(stooq_url(raw_sym)) if source == "stooq" else fetch_yahoo(raw_sym)
            quotes[csym] = parse_stooq(text, raw_sym) if source == "stooq" else parse_yahoo(text, raw_sym)
        except AlertError as e:
            errors.append(str(e))
    return quotes, errors


def write_prices(path, quotes, now_utc, source):
    """Write trader/prices.json keeping field names unchanged for trader-budget and trader-risk."""
    old = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8-sig") as fh:
                old = json.load(fh)
        except (ValueError, OSError):
            old = {}
    prices = dict(old.get("prices", {}))
    detail = dict(old.get("detail", {}))
    stamp = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    for s, q in quotes.items():
        prices[s] = q["price"]
        detail[s] = {
            "yahoo": q.get("yahoo") or yahoo_symbol(s),
            "prev_close": q.get("prev_close"),
            "time": q.get("time"),
            "currency": q.get("currency") or "INR",
            "source": q.get("source"),
            "fetched": stamp,
        }
    doc = {
        "as_of": stamp,
        "source": source,
        "note": ("Delayed NSE quotes (INR) for alerts and sizing checks. Your broker's price is the one that fills. "
                 "detail.fetched is when each price was read."),
        "prices": prices,
        "detail": detail,
    }
    _write(path, doc)
    return doc


def _write(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def _read(path, default):
    if not path or not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return json.load(fh)
    except ValueError as e:
        raise AlertError("%s is not valid JSON: %s" % (path, e))


# ---------------------------------------------------------------- rules

PRICE_TYPES = {"above", "below", "stop", "target", "pct_move", "pre_open", "opening_bell", "closing_bell", "time"}
NO_QUOTE = {"pre_open", "opening_bell", "closing_bell", "time"}


def _hhmm(text):
    m = HHMM.match(str(text or "").strip())
    if not m:
        return None
    return datetime.time(int(m.group(1)), int(m.group(2)))


def validate(rule):
    t = rule.get("type")
    if t not in PRICE_TYPES:
        raise AlertError("rule %s: unknown type %r" % (rule.get("id"), t))
    if t not in NO_QUOTE and not rule.get("symbol"):
        raise AlertError("rule %s needs a symbol" % rule.get("id"))
    if t in ("above", "below", "stop", "target") and not isinstance(rule.get("price"), (int, float)):
        raise AlertError("rule %s needs a numeric price" % rule.get("id"))
    if t == "pct_move" and not isinstance(rule.get("pct"), (int, float)):
        raise AlertError("rule %s needs a numeric pct" % rule.get("id"))
    if t == "time" and _hhmm(rule.get("at")) is None:
        raise AlertError("rule %s needs \"at\" as an IST time like \"15:10\"" % rule.get("id"))
    if rule.get("date") and not re.match(r"^\d{4}-\d{2}-\d{2}$", str(rule["date"])):
        raise AlertError("rule %s: date must be YYYY-MM-DD" % rule.get("id"))


def active(rule, today):
    """A rule with a "date" is watched on that IST date only."""
    return not rule.get("date") or str(rule["date"]) == today


def _rang_today(stamp, today):
    """Did a rule that rang at this UTC stamp ring on this IST date?"""
    if not stamp:
        return False
    try:
        utc = datetime.datetime.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return stamp[:10] == today
    return to_ist(utc).date().isoformat() == today


def ring_time(rule, status):
    """The time a time rule rings today (IST)."""
    return _hhmm(rule.get("at"))


def evaluate(rules, quotes, status, state, now_utc):
    """Pure function. Returns (rings, new_state). Each ring: {id, symbol, message}."""
    state = json.loads(json.dumps(state or {}))
    rung = state.setdefault("rung", {})
    today = status["date"]
    local = to_ist(now_utc)
    stamp = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    rings = []
    for r in rules:
        validate(r)
        rid = r.get("id") or "%s-%s" % (clean_symbol(r.get("symbol", "market")), r["type"])
        if not active(r, today):
            continue
        typ = {"stop": "below", "target": "above"}.get(r["type"], r["type"])
        if typ == "pre_open":
            if status.get("opens") and PRE_OPEN_START <= local.time() < OPEN and not _rang_today(rung.get(rid), today):
                rings.append({
                    "id": rid,
                    "symbol": "",
                    "message": "Pre-open session: NSE pre-open is active (%s IST, order entry 09:00-09:08, matching 09:08-09:15, regular open 09:15)."
                    % status["now_ist"],
                })
                rung[rid] = stamp
            continue
        if typ == "opening_bell":
            if status["open"] and not _rang_today(rung.get(rid), today):
                rings.append({
                    "id": rid,
                    "symbol": "",
                    "message": "Opening bell: the NSE is open (%s IST, regular session 09:15-15:30)." % status["now_ist"],
                })
                rung[rid] = stamp
            continue
        if typ == "closing_bell":
            if status.get("closes") and local.time() >= CLOSE and not _rang_today(rung.get(rid), today):
                rings.append({
                    "id": rid,
                    "symbol": "",
                    "message": "Closing bell: the NSE regular session has closed (%s IST, closed at %s)."
                    % (status["now_ist"], status["closes"]),
                })
                rung[rid] = stamp
            continue
        if typ == "time":
            at = ring_time(r, status)
            if status.get("opens") and local.time() >= at and not _rang_today(rung.get(rid), today):
                label = r.get("label") or "time alert"
                msg = "%s%s: it is %s IST (set for %s)" % (
                    label[:1].upper(), label[1:], local.strftime("%H:%M"), at.strftime("%H:%M")
                )
                msg += (", the market closes at %s." % status["closes"]) if status["open"] else \
                    ", the market has closed."
                sym = clean_symbol(r.get("symbol") or "")
                if sym:
                    msg += (" If %s (MIS intraday) is still open, square it off before broker auto square-off: "
                            "trader-broker flatten --symbol %s builds the sell ticket, then trader-risk and your yes."
                            % (sym, sym))
                rings.append({"id": rid, "symbol": sym, "message": msg})
                rung[rid] = stamp
            continue
        sym = clean_symbol(r["symbol"])
        q = quotes.get(sym)
        if not q:
            continue
        p = q["price"]
        if typ in ("above", "below"):
            hit = p >= r["price"] if typ == "above" else p <= r["price"]
            if hit and rid not in rung:
                word = "at or above" if typ == "above" else "at or below"
                label = {"stop": "stop", "target": "target"}.get(r["type"], "alert level")
                rings.append({
                    "id": rid,
                    "symbol": sym,
                    "message": "%s %s %s: %s your %s %s%s."
                    % (sym, "is" if status["open"] else "last", fmt_inr(p), word, label, fmt_inr(r["price"]),
                       (" (" + r["note"] + ")") if r.get("note") else ""),
                })
                rung[rid] = stamp
            elif not hit and rid in rung and r.get("rearm"):
                del rung[rid]
        elif typ == "pct_move":
            ref = r.get("ref") or q.get("prev_close") or q.get("open")
            if not ref:
                continue
            move = 100.0 * (p / float(ref) - 1)
            want = r.get("direction")
            hit = abs(move) >= r["pct"] and (not want or (want == "up") == (move > 0))
            if hit and not _rang_today(rung.get(rid), today):
                rings.append({
                    "id": rid,
                    "symbol": sym,
                    "message": "%s moved %+.2f%% to %s (from %s)." % (sym, move, fmt_inr(p), fmt_inr(float(ref))),
                })
                rung[rid] = stamp
    state["last_check"] = stamp
    return rings, state


def rules_from_plan(plan, cfg_flat_by=None, cfg_trade_type=None):
    """trader/plan.json -> stop, target (+ flat-by-close time alert for intraday; stop+target only for swing)."""
    if not isinstance(plan, dict) or not plan.get("symbol"):
        raise AlertError("the plan has no symbol (run trader-exit first)")
    sym = clean_symbol(plan["symbol"])
    trade_type = str(plan.get("trade_type") or cfg_trade_type or "intraday").strip().lower()
    product = str(plan.get("product") or "").strip().upper()
    is_swing = (trade_type == "swing" or product == "CNC")
    out = []
    for key, typ, word in (("stop", "stop", "plan stop"), ("target", "target", "plan target")):
        v = plan.get(key)
        if not isinstance(v, (int, float)) or v <= 0:
            raise AlertError("the plan has no %s (run trader-exit)" % key)
        label = plan.get("%s_label" % key)
        note = word + (": " + label if label else "")
        if is_swing:
            note += " [swing/CNC]"
        out.append({
            "id": "%s-plan-%s" % (sym.lower(), key),
            "symbol": sym,
            "type": typ,
            "price": float(v),
            "note": note,
        })
    if not is_swing:
        flat = plan.get("flat_by") or cfg_flat_by or FLAT_BY_DEFAULT
        if _hhmm(flat) is None:
            raise AlertError("the plan's flat_by %r is not a time like 15:10" % flat)
        out.append({
            "id": "%s-flat-by" % sym.lower(),
            "symbol": sym,
            "type": "time",
            "at": flat,
            "label": "flat by close",
        })
        if plan.get("date"):
            for r in out:
                r["date"] = str(plan["date"])
    return out


# ---------------------------------------------------------------- commands

def holdings(a):
    syms = []
    pf = _read(a.portfolio, {})
    for p in pf.get("positions", []) if isinstance(pf, dict) else []:
        syms.append(clean_symbol(p.get("ticker") or p.get("symbol") or ""))
    plan = _read(a.plan, {})
    if isinstance(plan, dict) and plan.get("symbol"):
        syms.append(clean_symbol(plan["symbol"]))
    return [s for s in syms if s]


def _dedupe(items):
    out = []
    for s in items:
        if s and s not in out:
            out.append(s)
    return out


def cmd_clock(a):
    st = market_status(parse_now(a.now), fetch_holidays=True)
    if a.json:
        print(json.dumps(st, indent=2))
    else:
        print("IST %s (%s): NSE %s, %s." % (
            st["now_ist"], st["weekday"], "OPEN" if st["open"] else "closed", st["reason"]
        ))
        if st.get("opens"):
            print("Today's session: pre-open %s, regular %s to %s (intraday MIS flat-by %s)." % (
                st["pre_open"], st["opens"], st["closes"], st["flat_by"]
            ))
        if not st["open"] and st.get("next_open"):
            print("Next open: %s IST." % st["next_open"])
        if not st["calendar_known"]:
            print(st.get("calendar_note") or ("Holiday list covers %s only: check nseindia.com for this date."
                                              % " and ".join(map(str, CALENDAR_YEARS))))
    return 0 if st["open"] else 1


def cmd_quote(a):
    syms = _dedupe([clean_symbol(s) for s in a.symbols] + (holdings(a) if a.holdings else []))
    if not syms:
        raise AlertError("which symbols? alerts.py quote RELIANCE TCS, or --holdings")
    now = parse_now(a.now)
    quotes, errors = get_quotes(syms, a.source, a.offline)
    if a.write and quotes:
        write_prices(a.write, quotes, now, a.source)
    if a.json:
        print(json.dumps({"quotes": quotes, "errors": errors}, indent=2))
    else:
        for s in syms:
            q = quotes.get(s)
            if q:
                chg = ""
                if q.get("prev_close"):
                    chg = " (%+.2f%% vs prev close %s)" % (
                        100.0 * (q["price"] / q["prev_close"] - 1), fmt_inr(q["prev_close"])
                    )
                print("%-10s %s%s  %s  [%s: %s]" % (
                    s, fmt_inr(q["price"]), chg, q.get("time", ""), q["source"], q.get("yahoo", s)
                ))
        for e in errors:
            print("error: " + e)
        if a.write and quotes:
            print("Wrote %d price(s) to %s." % (len(quotes), a.write))
        print("Quotes are free, delayed and unofficial: for alerts only. Your broker's price is the one that fills.")
    return 0 if quotes else 1


def cmd_check(a):
    doc = _read(a.rules, None)
    if not doc or not doc.get("rules"):
        raise AlertError("no rules in %s: add some with alerts.py add --from-plan" % a.rules)
    rules = doc["rules"]
    now = parse_now(a.now)
    status = market_status(now)
    watched = [r for r in rules if active(r, status["date"])]
    syms = _dedupe([clean_symbol(r.get("symbol", "")) for r in watched if r.get("type") not in NO_QUOTE])
    quotes, errors = (get_quotes(syms, a.source, a.offline) if syms else ({}, []))
    state = _read(a.state, {})
    rings, state = evaluate(rules, quotes, status, state, now)
    if not a.no_write:
        _write(a.state, state)
        if quotes:
            write_prices(a.prices_out, quotes, now, a.source)
    if a.json:
        print(json.dumps({
            "market": status,
            "rings": rings,
            "errors": errors,
            "quotes": {s: q["price"] for s, q in quotes.items()},
        }, indent=2))
    else:
        print("NSE %s (%s IST, %s)." % (
            "OPEN" if status["open"] else "closed", status["now_ist"], status["reason"]
        ))
        for r in rings:
            print("ALERT " + r["message"])
        if not rings:
            print("Nothing hit. %d rule(s) watched today%s." % (
                len(watched),
                (", " + ", ".join("%s %s" % (s, fmt_inr(quotes[s]["price"])) for s in syms if s in quotes))
                if quotes else "",
            ))
        for e in errors:
            print("error: " + e)
        if rings:
            print("An alert is information, not an order. Check your broker position and record any fill "
                  "(ticket.py fill). To close an open position yourself: ticket.py flatten, then trader-risk "
                  "and your yes.")
    return 0 if rings else 1


def cmd_add(a):
    doc = _read(a.rules, {"rules": []})
    doc.setdefault("rules", [])
    if a.from_plan:
        plan = _read(a.plan, None)
        if plan is None:
            raise AlertError("no plan at %s (run trader-exit first)" % a.plan)
        cfg = _read(a.config, {}) if a.config else {}
        cfg = cfg or {}
        new = rules_from_plan(plan, cfg.get("flat_by"), cfg.get("trade_type"))
        ids = {r["id"] for r in new}
        # Also remove any old flat-by rule for this symbol if converting intraday -> swing
        sym_flat_id = "%s-flat-by" % clean_symbol(plan.get("symbol", "")).lower()
        remove_ids = set(ids) | {sym_flat_id}
        doc["rules"] = [r for r in doc["rules"] if r.get("id") not in remove_ids] + new
        state = _read(a.state, {})
        if state.get("rung"):
            for i in remove_ids:
                state["rung"].pop(i, None)
            _write(a.state, state)
        _write(a.rules, doc)
        for r in new:
            print("Added %s: %s" % (r["id"], json.dumps(r)))
        print("Run alerts.py check every few minutes while the trade is open. "
              "Free quotes are delayed; your broker's stop/target orders are what actually execute.")
        return 0
    if a.pre_open:
        rule = {"id": a.id or "pre-open", "type": "pre_open"}
    elif a.opening_bell:
        rule = {"id": a.id or "opening-bell", "type": "opening_bell"}
    elif a.closing_bell:
        rule = {"id": a.id or "closing-bell", "type": "closing_bell"}
    elif a.square_off:
        cfg = (_read(a.config, {}) if a.config else {}) or {}
        at_time = a.at or cfg.get("flat_by") or FLAT_BY_DEFAULT
        sym = clean_symbol(a.symbol) if a.symbol else ""
        rid = a.id or ("%s-flat-by" % sym.lower() if sym else "flat-by-%s" % at_time.replace(":", ""))
        rule = {"id": rid, "type": "time", "at": at_time, "label": a.label or "flat by close"}
        if sym:
            rule["symbol"] = sym
    elif a.at:
        if _hhmm(a.at) is None:
            raise AlertError("--at wants an IST time like 15:10")
        rule = {
            "id": a.id or ("time-%s" % a.at.replace(":", "")),
            "type": "time",
            "at": a.at,
            "label": a.label or "time alert",
        }
        if a.symbol:
            rule["symbol"] = clean_symbol(a.symbol)
        if a.before_close:
            rule["before_close"] = True
    else:
        if not a.symbol:
            raise AlertError("--symbol is required (or --from-plan, --at, --square-off, --pre-open, --opening-bell, --closing-bell)")
        sym = clean_symbol(a.symbol)
        if a.above is not None:
            rule = {"type": "above", "price": a.above}
        elif a.below is not None:
            rule = {"type": "below", "price": a.below}
        elif a.stop is not None:
            rule = {"type": "stop", "price": a.stop}
        elif a.target is not None:
            rule = {"type": "target", "price": a.target}
        elif a.pct is not None:
            rule = {"type": "pct_move", "pct": a.pct}
            if a.direction:
                rule["direction"] = a.direction
        else:
            raise AlertError("give one of --above, --below, --stop, --target or --pct")
        val = rule.get("price", rule.get("pct"))
        rule = dict({"id": a.id or ("%s-%s-%s" % (sym.lower(), rule["type"], _n(val)))}, symbol=sym, **rule)
        if a.rearm:
            rule["rearm"] = True
        if a.note:
            rule["note"] = a.note
    if a.date:
        rule["date"] = a.date
    validate(rule)
    if any(r.get("id") == rule["id"] for r in doc["rules"]):
        raise AlertError("a rule with id %s already exists" % rule["id"])
    doc["rules"].append(rule)
    _write(a.rules, doc)
    print("Added %s: %s" % (rule["id"], json.dumps(rule)))
    return 0


def cmd_list(a):
    doc = _read(a.rules, {"rules": []})
    state = _read(a.state, {})
    if not doc.get("rules"):
        print("No rules yet.")
        return 1
    for r in doc["rules"]:
        rid = r.get("id")
        rung = state.get("rung", {}).get(rid)
        print("%-26s %s%s" % (
            rid, json.dumps({k: v for k, v in r.items() if k != "id"}),
            ("  (rang %s)" % rung) if rung else "",
        ))
    return 0


def cmd_reset(a):
    state = _read(a.state, {})
    if a.id not in state.get("rung", {}):
        print("%s has not rung (nothing to reset)." % a.id)
        return 1
    del state["rung"][a.id]
    _write(a.state, state)
    print("Re-armed %s." % a.id)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Price alerts and the NSE clock (trader-alerts, India/IST).")
    sub = ap.add_subparsers(dest="cmd")

    def net(p):
        p.add_argument("--source", default="yahoo", choices=["yahoo", "stooq"])
        p.add_argument("--offline", metavar="DIR", help="read <source>-<TICKER>.json|csv files, no network")
        p.add_argument("--now", help="pretend it is this time (no offset = IST Asia/Kolkata)")
        p.add_argument("--json", action="store_true")

    p = sub.add_parser("clock", help="is the NSE open?")
    p.add_argument("--now", help="pretend it is this time (no offset = IST Asia/Kolkata)")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("quote", help="latest delayed NSE quotes (.NS)")
    net(p)
    p.add_argument("symbols", nargs="*")
    p.add_argument("--holdings", action="store_true", help="add symbols from your portfolio and plan")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p.add_argument("--plan", default="trader/plan.json")
    p.add_argument("--write", nargs="?", const="trader/prices.json", metavar="PATH",
                   help="save to prices.json (default trader/prices.json)")
    p = sub.add_parser("check", help="check every rule, ring what hit")
    net(p)
    p.add_argument("--rules", default="trader/alerts.json")
    p.add_argument("--state", default="trader/state/alerts.json")
    p.add_argument("--prices-out", default="trader/prices.json")
    p.add_argument("--no-write", action="store_true")
    p = sub.add_parser("add", help="add a rule")
    p.add_argument("--rules", default="trader/alerts.json")
    p.add_argument("--state", default="trader/state/alerts.json")
    p.add_argument("--symbol")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--above", type=float)
    g.add_argument("--below", type=float)
    g.add_argument("--stop", type=float)
    g.add_argument("--target", type=float)
    g.add_argument("--pct", type=float)
    g.add_argument("--pre-open", action="store_true", help="ring at NSE pre-open 09:00 IST")
    g.add_argument("--opening-bell", action="store_true", help="ring at NSE opening bell 09:15 IST")
    g.add_argument("--closing-bell", action="store_true", help="ring at NSE closing bell 15:30 IST")
    g.add_argument("--square-off", action="store_true", help="ring at MIS square-off time (default 15:10 IST)")
    g.add_argument("--at", metavar="HH:MM", help="a time alert, IST")
    g.add_argument("--from-plan", action="store_true",
                   help="stop, target (+ flat-by for intraday; stop+target for swing) from trader/plan.json")
    p.add_argument("--plan", default="trader/plan.json")
    p.add_argument("--config", default="trader/config.json", help="flat_by and trade_type when the plan has none")
    p.add_argument("--label", help="what a time alert says, e.g. \"flat by close\"")
    p.add_argument("--before-close", action="store_true",
                   help="kept for compatibility (India has no early-close days)")
    p.add_argument("--date", help="watch this rule on this IST date only (YYYY-MM-DD)")
    p.add_argument("--direction", choices=["up", "down"])
    p.add_argument("--rearm", action="store_true")
    p.add_argument("--note")
    p.add_argument("--id")
    p = sub.add_parser("list", help="list the rules")
    p.add_argument("--rules", default="trader/alerts.json")
    p.add_argument("--state", default="trader/state/alerts.json")
    p = sub.add_parser("reset", help="re-arm a rule that rang")
    p.add_argument("id")
    p.add_argument("--state", default="trader/state/alerts.json")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"clock": cmd_clock, "quote": cmd_quote, "check": cmd_check, "add": cmd_add,
                "list": cmd_list, "reset": cmd_reset}[a.cmd](a)
    except AlertError as e:
        print("alerts.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
