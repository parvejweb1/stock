#!/usr/bin/env python3
"""Chart Scanner for trader-scanner (India / NSE & BSE): scan charts for setups. Standard library only.

    python scan.py universe                                    # Nifty 500 (default) -> trader/universe.txt
    python scan.py universe --universe nifty50                 # Nifty 50 / nifty500 / all (NSE EQUITY_L.csv)
    python scan.py daily                                       # before the open: daily setups -> trader/watchlist.json
    python scan.py daily --universe nifty50                    # scan Nifty 50 daily setups
    python scan.py daily --symbols RELIANCE,TCS,SBIN,INFY      # or --watchlist FILE, or --limit 100
    python scan.py orb                                         # after 09:35 IST: opening-range breakouts so far
    python scan.py orb --at 10:15 --or-minutes 15              # cut the clock at 10:15 IST
    python scan.py orb --universe nifty50 --now "2026-10-30 10:15"

universe  Downloads NSE's official equity lists from archives.nseindia.com:
            - nifty50:  content/indices/ind_nifty50list.csv (~50 stocks)
            - nifty500: content/indices/ind_nifty500list.csv (~500 stocks, default)
            - all:      content/equities/EQUITY_L.csv (~2,350 active EQ series stocks)
          Keeps only SERIES == "EQ" (regular rolling-settlement equity where both
          intraday MIS and delivery CNC are allowed; drops BE/BZ trade-to-trade,
          REIT/InvIT RR/IV, SME, and DUMMY demerger placeholders).

daily     Pre-market or post-close scan of daily CLOSES (Yahoo spark: 20 symbols a
          request, 3 months of 1-day bars, .NS symbols, closes only). Useful for
          both swing trades (CNC delivery) and intraday (MIS) watchlists.
          Liquidity from the spark meta: regularMarketPrice >= --min-price (₹50)
          and regularMarketVolume >= --min-volume (5,00,000 shares). Setups:
            breakout_20d     the last close is above every close of the 20 before it
            bull_flag        up more than 10 % over the 10 sessions before the
                             last 5, then the last 5 closes inside a 5 % range
            reclaim_ma20     closed under the 20-day average, then closed above it
            oversold         RSI 14 (Wilder) under 30
            oversold_bounce  RSI 14 was under 30 yesterday and today closed up
          Today's daily bar is dropped until the session's close (15:30 IST).

orb       Intraday opening-range breakouts, 5-minute bars (MIS day trading). The
          opening range is the first --or-minutes (15) of the NSE session:
          09:15 to 09:30 IST (the 09:15, 09:20 and 09:25 bars). Stage 1: spark
          closes for every symbol (1 day of 5-minute bars, 20 a request); a chart
          goes on only if a completed bar after the range closed at or above the
          highest close of the range. Stage 2: each candidate's v8 chart (real
          highs and lows): range high = highest high of the range bars, a hit =
          the first completed bar to CLOSE above it, at that bar's close time.
          Stage 2 fetches at most --max-confirm (200) charts, most traded first
          (price x volume so far). A chart is usable only when every 5-minute bar
          from 09:15 to the cutoff is present. Floors: --min-price 50, --min-volume 0.

Bars: null closes are dropped, a bar off the 5-minute grid is dropped, and a bar
counts only once it is complete (start + 5 minutes <= the clock). Times are
Indian Standard Time (IST, Asia/Kolkata, UTC+05:30). --now pins the clock
("2026-10-30 10:15", IST; ...Z for UTC).

Educational tool, not SEBI-registered investment advice. Trading loses most people money.
Exit code 0 when the scan found something, 1 when it did not or could not run.
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

try:
    from zoneinfo import ZoneInfo
    try:
        IST_TZ = ZoneInfo("Asia/Kolkata")
    except Exception:
        IST_TZ = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
except ImportError:
    IST_TZ = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

YAHOO_UA = "Mozilla/5.0 (compatible; day-trader-skill/2.0; +https://github.com/Jakeschincariol/day-trader-skill)"
NSE_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0"
UA = YAHOO_UA
YAHOO_HOSTS = ("https://query2.finance.yahoo.com", "https://query1.finance.yahoo.com")
UNIVERSE_URLS = {
    "nifty50": ("Nifty 50", "https://archives.nseindia.com/content/indices/ind_nifty50list.csv", "ind_nifty50list.csv"),
    "nifty500": ("Nifty 500", "https://archives.nseindia.com/content/indices/ind_nifty500list.csv", "ind_nifty500list.csv"),
    "all": ("NSE All Equities (EQUITY_L)", "https://archives.nseindia.com/content/equities/EQUITY_L.csv", "EQUITY_L.csv"),
}
SPARK_MAX = 20      # Yahoo's spark answers 400 above 20 symbols
PAUSE = 0.3         # seconds between requests: be polite to free endpoints
BACKOFF = 5.0       # seconds to wait before the one retry after a 429
SLEEP = time.sleep  # tests swap this out
BAR_MIN = 5
MAX_CONFIRM = 200   # stage-2 v8 charts per orb scan, most traded first
MIN_DAILY_BARS = 30
DEFAULT_UNIVERSE_NAME = "nifty500"
DEFAULT_UNIVERSE_FILE = "trader/universe.txt"
NOTE = ("A scan is a list of charts that match a rule, not a list of stocks to buy. "
        "Educational tool, not SEBI-registered investment advice. Trading loses most people money.")

NSE_HOLIDAYS_2026 = {
    "2026-01-15": "Municipal Corporation Election - Maharashtra",
    "2026-01-26": "Republic Day",
    "2026-03-03": "Holi",
    "2026-03-26": "Shri Ram Navami",
    "2026-03-31": "Shri Mahavir Jayanti",
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
MUHURAT_2026 = "2026-11-08"  # Sun evening special session; check NSE circular, do not auto-open
IST_OFFSET = datetime.timedelta(hours=5, minutes=30)
OPEN = datetime.time(9, 15)
CLOSE = datetime.time(15, 30)

SETUPS = [
    ("breakout_20d", "20-day closing-high breakout"),
    ("bull_flag", "bull flag"),
    ("reclaim_ma20", "reclaimed the 20-day average"),
    ("oversold", "oversold (RSI under 30)"),
    ("oversold_bounce", "oversold bounce"),
]
LABEL = dict(SETUPS)


class ScanError(Exception):
    pass


# ---------------------------------------------------------------- time (IST)

def to_ist(utc):
    """Naive or aware UTC -> naive IST (Asia/Kolkata, UTC+05:30)."""
    if utc.tzinfo is not None:
        utc = utc.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return utc + IST_OFFSET


def ist_to_utc(local):
    """Naive IST -> naive UTC."""
    return local.replace(tzinfo=None) - IST_OFFSET


def ts_to_ist(ts):
    """Unix seconds -> naive IST time. Never uses the machine's time zone."""
    return to_ist(datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=int(ts)))


# Keep aliases so any internal callers or tests work cleanly
to_eastern = to_ist
ts_to_eastern = ts_to_ist


def parse_now(text):
    """ISO time -> naive UTC. A time without an offset is taken as IST (Asia/Kolkata)."""
    if not text:
        return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    t = text.strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise ScanError('--now wants a time like "2026-10-30 10:15" (IST) or ...Z (UTC)')
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def parse_hhmm(text, flag="--at"):
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", text or "")
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ScanError("%s wants an IST time like 10:15" % flag)
    return datetime.time(int(m.group(1)), int(m.group(2)))


def load_nse_holidays(cache_path="trader/cache/nse_holidays.json"):
    """Return {YYYY-MM-DD: description}. Uses cached live NSE holiday-master if available,
    layered over NSE_HOLIDAYS_2026 fallback."""
    holidays = dict(NSE_HOLIDAYS_2026)
    if cache_path and os.path.isfile(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as fh:
                cached = json.load(fh)
            if isinstance(cached, dict):
                holidays.update(cached)
        except Exception:
            pass
    return holidays


def trading_day(d):
    """(is_trading_day, reason). Weekends, Muhurat note, and NSE holiday list."""
    iso = d.isoformat()
    if iso == MUHURAT_2026:
        return False, "Muhurat trading special session on Sun 2026-11-08 (check NSE circular; not a normal session)"
    if d.weekday() >= 5:
        return False, "weekend"
    holidays = load_nse_holidays()
    name = holidays.get(iso)
    if name:
        return False, "NSE holiday: " + name
    if d.year >= 2027:
        return True, "2027 holidays unknown, check NSE"
    return True, ""


def session(d):
    """(open_time, close_time) in IST, or None if closed all day. No early-close days on NSE."""
    ok, _ = trading_day(d)
    if not ok:
        return None
    return OPEN, CLOSE


def clock_label(t):
    """datetime or time -> '10:15 IST'."""
    return "%02d:%02d IST" % (t.hour, t.minute)


def day_label(d):
    return "%s %s" % (d.strftime("%a"), d.isoformat())


# ---------------------------------------------------------------- formatting & symbols

def round_tick(x):
    """Round price to nearest ₹0.05 tick."""
    return round(round(float(x) * 20.0) / 20.0, 2)


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


def money(x):
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


def _p(x):
    val = round(float(x), 2)
    whole = int(abs(val))
    frac = int(round((abs(val) - whole) * 100))
    return "%s.%02d" % (_indian_int(whole), frac)


def norm_symbol(sym):
    """Strip .NS / .BO suffix to keep plain NSE symbol (except ^ indices)."""
    s = sym.strip().upper()
    if s.startswith("^"):
        return s
    for suf in (".NS", ".BO"):
        if s.endswith(suf):
            return s[:-len(suf)]
    return s


def to_yahoo(sym):
    """Plain NSE symbol -> Yahoo symbol (RELIANCE -> RELIANCE.NS, ^NSEI -> ^NSEI)."""
    s = sym.strip().upper()
    if s.startswith("^") or s.endswith(".NS") or s.endswith(".BO"):
        return s
    return s + ".NS"


# ---------------------------------------------------------------- network

def http_get(url, timeout=25):
    is_nse = "nseindia.com" in url
    headers = (
        {
            "User-Agent": NSE_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if is_nse
        else {"User-Agent": YAHOO_UA, "Accept": "*/*"}
    )
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            data = gzip.decompress(data)
        return data.decode("utf-8", "replace")


def yahoo_get(path):
    """GET a Yahoo Finance path. query2 first, query1 on a 429 or 5xx, and one
    more round after BACKOFF seconds if a 429 was seen. 4xx other than 429 is final."""
    last = "no answer"
    for attempt in (0, 1):
        saw_429 = False
        for host in YAHOO_HOSTS:
            try:
                return http_get(host + path)
            except urllib.error.HTTPError as e:
                last = "HTTP %d" % e.code
                try:
                    body = e.read().decode("utf-8", "replace") if 400 <= e.code < 500 and e.code != 429 else ""
                except Exception:
                    body = ""
                finally:
                    e.close()
                if e.code == 429:
                    saw_429 = True
                    continue
                if e.code >= 500:
                    continue
                if body.strip().startswith("{"):
                    return body
                raise ScanError("Yahoo answered %s for %s" % (last, path.split("?")[0]))
            except (urllib.error.URLError, OSError) as e:
                last = str(getattr(e, "reason", e))
                continue
        if attempt == 0 and saw_429:
            SLEEP(BACKOFF)
            continue
        break
    raise ScanError("Yahoo answered %s for %s" % (last, path.split("?")[0]))


def spark_path(symbols, rng, interval):
    encoded = ",".join(urllib.parse.quote(to_yahoo(s), safe="") for s in symbols)
    return "/v7/finance/spark?symbols=%s&range=%s&interval=%s" % (encoded, rng, interval)


def chart_path(sym, rng, interval):
    return "/v8/finance/chart/%s?range=%s&interval=%s" % (
        urllib.parse.quote(to_yahoo(sym), safe=""), rng, interval)


# ---------------------------------------------------------------- parsing

def series_from_result(res):
    """A Yahoo chart result (v8 or one spark response) -> plain lists."""
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0] or {}
    t = res.get("timestamp") or []
    n = len(t)

    def col(k):
        v = q.get(k)
        return list(v) + [None] * (n - len(v)) if isinstance(v, list) else None
    return {"t": list(t), "c": col("close") or [None] * n, "o": col("open"), "h": col("high"),
            "l": col("low"), "v": col("volume"), "meta": res.get("meta") or {}}


def parse_spark(text_or_doc, wanted=None):
    """Spark JSON -> {SYM: series}. `wanted` maps Yahoo symbols (RELIANCE.NS) back to plain NSE symbols (RELIANCE)."""
    doc = json.loads(text_or_doc) if isinstance(text_or_doc, str) else text_or_doc
    sp = (doc or {}).get("spark") if isinstance(doc, dict) else None
    if not isinstance(sp, dict):
        raise ScanError("Yahoo's spark answer is not the shape this tool knows")
    err = sp.get("error")
    out = {}
    for item in sp.get("result") or []:
        resp = item.get("response") or []
        if not resp:
            continue
        ysym = (item.get("symbol") or (resp[0].get("meta") or {}).get("symbol") or "").upper()
        sym = (wanted or {}).get(ysym, norm_symbol(ysym))
        out[sym] = series_from_result(resp[0])
    if err and not out:
        raise ScanError("Yahoo spark: %s" % (err.get("description") or err.get("code") or err))
    return out


def parse_chart(text_or_doc, sym=""):
    """v8 chart JSON -> series. Raises ScanError for Yahoo's 'no data' answers."""
    try:
        doc = json.loads(text_or_doc) if isinstance(text_or_doc, str) else text_or_doc
        chart = doc["chart"]
    except (ValueError, KeyError, TypeError):
        raise ScanError("Yahoo returned something unexpected for %s" % sym)
    res = chart.get("result")
    if not res:
        err = chart.get("error") or {}
        raise ScanError("no data for %s: %s" % (sym, err.get("description", "unknown ticker")))
    return series_from_result(res[0])


def _read_maybe_gz(path):
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return fh.read()
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _stem(name):
    for ext in (".json.gz", ".json"):
        if name.endswith(ext):
            return name[:-len(ext)]
    return name


def _absorb(doc, name_hint, data, errors):
    """One parsed offline JSON doc -> data / errors."""
    if isinstance(doc, dict) and "spark" in doc:
        data.update(parse_spark(doc))
    elif isinstance(doc, dict) and "chart" in doc:
        sym = name_hint
        if not sym:
            try:
                sym = (doc["chart"].get("result") or [{}])[0]["meta"]["symbol"]
            except (AttributeError, KeyError, TypeError, IndexError):
                raise ScanError("an offline v8 chart without a symbol: name the file <TICKER>.json")
        sym = norm_symbol(sym)
        try:
            data[sym] = parse_chart(doc, sym)
        except ScanError as e:
            errors[sym] = str(e)
    elif isinstance(doc, dict):
        for k, v in doc.items():
            _absorb(v, k, data, errors)
    else:
        raise ScanError("offline file %s is not a spark answer, a v8 chart or a bundle" % name_hint)


def load_offline(path):
    """A folder of <T>.json(.gz) files or one file -> ({SYM: series}, {SYM: error})."""
    data, errors = {}, {}
    if os.path.isdir(path):
        names = sorted(f for f in os.listdir(path) if f.endswith((".json", ".json.gz")))
        if not names:
            raise ScanError("no .json files in %s" % path)
        for f in names:
            try:
                doc = json.loads(_read_maybe_gz(os.path.join(path, f)))
            except ValueError as e:
                raise ScanError("%s is not valid JSON: %s" % (f, e))
            if isinstance(doc, dict) and ("chart" in doc or "spark" in doc):
                _absorb(doc, "" if "spark" in doc else _stem(f), data, errors)
            else:
                _absorb(doc, "", data, errors)
    elif os.path.isfile(path):
        try:
            doc = json.loads(_read_maybe_gz(path))
        except ValueError as e:
            raise ScanError("%s is not valid JSON: %s" % (path, e))
        _absorb(doc, "", data, errors)
    else:
        raise ScanError("no offline file or folder at %s" % path)
    return data, errors


# ---------------------------------------------------------------- universe (NSE)

def parse_nse_csv(text):
    """Parse NSE index CSV (ind_nifty50list.csv / ind_nifty500list.csv) or EQUITY_L.csv.
    Returns (kept [(SYM, name)], counts)."""
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    if not reader.fieldnames:
        raise ScanError("empty NSE CSV file")
    headers = [h.strip() for h in reader.fieldnames if h]
    has_sym = any(h in ("Symbol", "SYMBOL") for h in headers)
    if not has_sym:
        raise ScanError("not an NSE equity CSV (headers: %s)" % ", ".join(headers[:6]))
    kept = {}
    counts = {"rows": 0, "keep": 0}
    for raw in reader:
        row = {(k or "").strip(): (v or "").strip() for k, v in raw.items() if k}
        sym = (row.get("Symbol") or row.get("SYMBOL") or "").upper()
        if not sym:
            continue
        counts["rows"] += 1
        series = (row.get("Series") or row.get("SERIES") or "EQ").upper()
        name = " ".join((row.get("Company Name") or row.get("NAME OF COMPANY") or sym).split())
        if series != "EQ":
            tag = "series %s (non-EQ)" % series
            counts[tag] = counts.get(tag, 0) + 1
            continue
        if sym.startswith("DUMMY"):
            counts["dummy placeholder"] = counts.get("dummy placeholder", 0) + 1
            continue
        counts["keep"] += 1
        kept[sym] = name
    return sorted(kept.items()), counts


def fetch_universe_list(preset, offline=None):
    """Download or read an NSE universe preset ('nifty50', 'nifty500', 'all').
    Returns (label, kept [(SYM, name)], counts)."""
    key = (preset or DEFAULT_UNIVERSE_NAME).strip().lower()
    if key not in UNIVERSE_URLS:
        raise ScanError("--universe preset must be nifty50, nifty500, or all (or a file path)")
    label, url, fname = UNIVERSE_URLS[key]
    if offline:
        p = os.path.join(offline, fname) if os.path.isdir(offline) else offline
        if not os.path.exists(p):
            raise ScanError("no %s in %s" % (fname, offline))
        with open(p, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    else:
        try:
            text = http_get(url, timeout=45)
        except urllib.error.HTTPError as e:
            raise ScanError("archives.nseindia.com answered HTTP %d for %s" % (e.code, fname))
        except (urllib.error.URLError, OSError) as e:
            raise ScanError("could not reach archives.nseindia.com: %s" % getattr(e, "reason", e))
    kept, counts = parse_nse_csv(text)
    return label, kept, counts


# ---------------------------------------------------------------- daily setups

def rsi(closes, n=14):
    """Wilder's RSI on closes."""
    if len(closes) < n + 1:
        return None
    g = [max(0.0, closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    lo = [max(0.0, closes[i - 1] - closes[i]) for i in range(1, len(closes))]
    ag, al = sum(g[:n]) / n, sum(lo[:n]) / n
    for i in range(n, len(g)):
        ag = (ag * (n - 1) + g[i]) / n
        al = (al * (n - 1) + lo[i]) / n
    return 100 - 100 / (1 + ag / al) if al else 100.0


def daily_closes(series, now_utc):
    """[(date, close)] of COMPLETE daily bars: null closes dropped, today's bar dropped
    until the session closes (15:30 IST), anything dated after the clock dropped."""
    now = to_ist(now_utc)
    today = now.date()
    s = session(today)
    today_done = s is None or now.time() >= s[1]
    out = {}
    for ts, c in zip(series["t"], series["c"]):
        if c is None:
            continue
        d = ts_to_ist(ts).date()
        if d > today or (d == today and not today_done):
            continue
        out[d] = float(c)
    return sorted(out.items())


def find_setups(closes):
    """closes (oldest first, >= 30) -> (setup keys, numbers)."""
    c = closes
    last = c[-1]
    hi20 = max(c[-21:-1])
    ma20 = sum(c[-20:]) / 20.0
    ma20_prev = sum(c[-21:-1]) / 20.0
    run = c[-6] / c[-16] - 1
    rng5 = (max(c[-5:]) - min(c[-5:])) / last
    r = rsi(c)
    r_prev = rsi(c[:-1])
    tags = []
    if last > hi20:
        tags.append("breakout_20d")
    if run > 0.10 and rng5 < 0.05:
        tags.append("bull_flag")
    if c[-2] < ma20_prev and last > ma20:
        tags.append("reclaim_ma20")
    if r is not None and r < 30:
        tags.append("oversold")
    if r_prev is not None and r_prev < 30 and last > c[-2]:
        tags.append("oversold_bounce")
    return tags, {"rsi": r, "rsi_prev": r_prev, "high20": hi20, "ma20": ma20,
                  "run_pct": run * 100, "range5_pct": rng5 * 100}


def scan_daily(data, symbols, now_utc, min_price, min_volume):
    """Pure. data = {SYM: series}. Returns (hits, counts, last_date)."""
    hits, dates = [], {}
    counts = {"requested": len(symbols), "fetched": 0, "illiquid": 0, "short": 0, "scanned": 0}
    for sym in symbols:
        s = data.get(sym)
        if not s:
            continue
        counts["fetched"] += 1
        bars = daily_closes(s, now_utc)
        meta = s.get("meta") or {}
        price = meta.get("regularMarketPrice")
        if price is None and bars:
            price = bars[-1][1]
        vol = meta.get("regularMarketVolume")
        if price is None or price < min_price or (min_volume > 0 and (vol is None or vol < min_volume)):
            counts["illiquid"] += 1
            continue
        if len(bars) < MIN_DAILY_BARS:
            counts["short"] += 1
            continue
        counts["scanned"] += 1
        d_last = bars[-1][0]
        dates[d_last] = dates.get(d_last, 0) + 1
        closes = [c for _, c in bars]
        tags, nums = find_setups(closes)
        if tags:
            hits.append({
                "symbol": sym,
                "yahoo": to_yahoo(sym),
                "setups": tags,
                "price": round_tick(closes[-1]),
                "close_date": d_last.isoformat(),
                "rsi": round(nums["rsi"], 1),
                "volume": int(vol) if vol is not None else None,
            })
    hits.sort(key=lambda h: (-(h["price"] * (h["volume"] or 0)), h["symbol"]))  # most ₹ turnover first
    last_date = max(dates, key=lambda d: (dates[d], d)) if dates else None
    counts["hits"] = len(hits)
    return hits, counts, last_date


# ---------------------------------------------------------------- opening-range breakout

def slot(day, k):
    return datetime.datetime.combine(day, OPEN) + datetime.timedelta(minutes=BAR_MIN * k)


def session_bars(series, day, cutoff):
    """5-minute bars of `day` that are complete at `cutoff` (naive IST),
    on the grid, with a close. Sorted, one per slot."""
    s = session(day)
    close_t = s[1] if s else CLOSE
    out = {}
    hs, ls = series.get("h"), series.get("l")
    for i, ts in enumerate(series["t"]):
        c = series["c"][i]
        if c is None:
            continue
        ist = ts_to_ist(ts)
        if ist.date() != day or ist.second or ist.minute % BAR_MIN:
            continue  # another day, or the live tick off the grid
        if ist.time() < OPEN or ist.time() >= close_t:
            continue  # pre-open, post-close
        if ist + datetime.timedelta(minutes=BAR_MIN) > cutoff:
            continue  # still in progress at the cutoff
        v = series.get("v")
        out[ist] = {"start": ist, "c": float(c),
                    "h": None if not hs or hs[i] is None else float(hs[i]),
                    "l": None if not ls or ls[i] is None else float(ls[i]),
                    "v": (v[i] or 0) if v else 0}
    return [out[k] for k in sorted(out)]


def usable(bars, day, cutoff, n_range):
    """Every slot from 09:15 to the cutoff is there, and at least the range."""
    want = int((cutoff - slot(day, 0)).total_seconds() // (BAR_MIN * 60))
    s = session(day)
    if s:
        want = min(want, int((datetime.datetime.combine(day, s[1]) - slot(day, 0)).total_seconds()
                             // (BAR_MIN * 60)))
    if want < n_range or len(bars) != want:
        return False
    return all(b["start"] == slot(day, k) for k, b in enumerate(bars))


def orb_candidate(bars, n_range):
    """Stage 1, closes only: some completed bar after the range closed at or above
    the highest close of the range."""
    top = max(b["c"] for b in bars[:n_range])
    return any(b["c"] >= top for b in bars[n_range:])


def orb_hit(bars, n_range):
    """Stage 2, real highs and lows: the first completed bar to CLOSE above the range high."""
    rng = bars[:n_range]
    if any(b["h"] is None or b["l"] is None for b in rng):
        return None
    oh = max(b["h"] for b in rng)
    ol = min(b["l"] for b in rng)
    for k in range(n_range, len(bars)):
        b = bars[k]
        if b["c"] > oh:
            closes_at = b["start"] + datetime.timedelta(minutes=BAR_MIN)
            return {"price": round_tick(b["c"]), "at": clock_label(closes_at),
                    "bar_start": b["start"].strftime("%H:%M"), "bar_index": k,
                    "or_high": round_tick(oh), "or_low": round_tick(ol),
                    "pct_over": round((b["c"] / oh - 1) * 100, 2), "_t": closes_at}
    return None


def orb_window(now_utc, at, or_minutes, until, explicit_day=None):
    """-> (day, cutoff naive IST) or raise ScanError with the plain message."""
    now = to_ist(now_utc)
    day = explicit_day or now.date()
    if or_minutes < BAR_MIN or or_minutes % BAR_MIN or or_minutes > 120:
        raise ScanError("--or-minutes wants a multiple of 5 from 5 to 120 (15 is the classic: 09:15–09:30 IST)")
    ok, why = trading_day(day)
    if not ok:
        raise ScanError("No session on %s (%s): the market is closed. Run orb on an NSE trading day "
                        "after the opening range, or replay a past trading day with --now." % (day_label(day), why))
    s = session(day)
    range_end = slot(day, or_minutes // BAR_MIN)
    first_close = range_end + datetime.timedelta(minutes=BAR_MIN)
    close_dt = datetime.datetime.combine(day, s[1])
    if at is not None:
        cutoff = datetime.datetime.combine(day, at)
        if explicit_day is None and cutoff > now + datetime.timedelta(seconds=59):
            raise ScanError("--at %s is later than now (%s)." % (at.strftime("%H:%M"), clock_label(now)))
        if cutoff > close_dt:
            raise ScanError("--at %s is after the %s close on %s." % (at.strftime("%H:%M"), clock_label(close_dt),
                                                                       day_label(day)))
    else:
        cutoff = now.replace(second=0, microsecond=0)
        if cutoff >= close_dt:
            raise ScanError("NSE closed at %s on %s. Replay the session with --at 10:15 or --now \"%s 10:15\"."
                            % (clock_label(close_dt), day_label(day), day.isoformat()))
    if cutoff < first_close:
        raise ScanError("It is %s: the %d-minute opening range runs 09:15 to %s and the first bar after it "
                        "closes at %s. Run orb again after %s."
                        % (clock_label(cutoff), or_minutes, clock_label(range_end), clock_label(first_close),
                           clock_label(first_close)))
    if until is not None and cutoff.time() > until:
        raise ScanError("It is %s, after %s, your last time for new entries (no_entries_after): an "
                        "opening-range breakout found now is expired. Run orb tomorrow after %s, or replay "
                        "today with --at 10:15." % (clock_label(cutoff), until.strftime("%H:%M"),
                                                      clock_label(first_close)))
    return day, cutoff


def scan_orb(stage1, symbols, day, cutoff, or_minutes, min_price=0.0, min_volume=0, confirm=None,
             max_confirm=None):
    """stage1 = {SYM: series} with closes (spark or v8). confirm(sym) -> series with highs/lows
    (None = use the stage-1 series). Returns (hits, counts, failed {sym: why})."""
    n_range = or_minutes // BAR_MIN
    counts = {"requested": len(symbols), "fetched": 0, "unusable": 0, "illiquid": 0, "scanned": 0,
              "candidates": 0, "confirmed": 0, "unconfirmed": 0}
    hits, failed, cands = [], {}, []
    for sym in symbols:
        s = stage1.get(sym)
        if not s:
            continue
        counts["fetched"] += 1
        bars = session_bars(s, day, cutoff)
        if not usable(bars, day, cutoff, n_range):
            counts["unusable"] += 1
            continue
        vol = sum(b["v"] for b in bars) if s.get("v") else (s.get("meta") or {}).get("regularMarketVolume") or 0
        if bars[-1]["c"] < min_price or vol < min_volume:
            counts["illiquid"] += 1
            continue
        counts["scanned"] += 1
        if orb_candidate(bars, n_range):
            cands.append((sym, bars, bars[-1]["c"] * vol))
    counts["candidates"] = len(cands)
    if confirm is not None and max_confirm and len(cands) > max_confirm:
        cands.sort(key=lambda x: -x[2])
        counts["unconfirmed"] = len(cands) - max_confirm
        cands = cands[:max_confirm]
    for sym, bars, _ in cands:
        full = bars
        if confirm is not None:
            try:
                s2 = confirm(sym)
            except ScanError as e:
                failed[sym] = str(e)
                continue
            full = session_bars(s2, day, cutoff)
            if not usable(full, day, cutoff, n_range):
                failed[sym] = "the v8 chart has a missing bar"
                continue
        counts["confirmed"] += 1
        h = orb_hit(full, n_range)
        if h:
            hits.append(dict({"symbol": sym, "yahoo": to_yahoo(sym), "setups": ["orb"]}, **h))
    hits.sort(key=lambda x: (x["_t"], x["symbol"]))
    for h in hits:
        del h["_t"]
    counts["hits"] = len(hits)
    return hits, counts, failed


# ---------------------------------------------------------------- fetching

def progress(a, msg):
    if not getattr(a, "json", False) and not getattr(a, "quiet", False):
        print(msg, file=sys.stderr)


def fetch_spark(symbols, rng, interval, pause, a=None):
    """Batches of 20. -> ({SYM: series}, {SYM: error})."""
    data, failed = {}, {}
    batches = [symbols[i:i + SPARK_MAX] for i in range(0, len(symbols), SPARK_MAX)]
    for k, batch in enumerate(batches):
        if k and pause:
            SLEEP(pause)
        wanted = {to_yahoo(s).upper(): s for s in batch}
        try:
            got = parse_spark(yahoo_get(spark_path(batch, rng, interval)), wanted)
        except (ScanError, ValueError) as e:
            for s in batch:
                failed[s] = str(e)
            continue
        for s in batch:
            if s in got:
                data[s] = got[s]
            else:
                failed[s] = "no data from Yahoo"
        if a is not None and len(batches) > 25 and (k + 1) % 25 == 0:
            progress(a, "  spark %d of %d requests" % (k + 1, len(batches)))
    return data, failed


def read_symbols(a, offline_syms=None):
    if a.symbols:
        syms = [norm_symbol(s) for s in re.split(r"[,\s]+", a.symbols.upper()) if s]
    elif a.watchlist:
        syms = symbols_from_file(a.watchlist)
    elif offline_syms is not None and not a.universe:
        syms = sorted(offline_syms)
    else:
        u = (a.universe or "").strip()
        if u.lower() in UNIVERSE_URLS:
            # Explicit preset: fetch or read from cached universe-<preset>.txt
            cache_file = "trader/universe-%s.txt" % u.lower()
            if os.path.exists(cache_file):
                syms = symbols_from_file(cache_file)
            else:
                label, kept, _ = fetch_universe_list(u.lower())
                os.makedirs("trader", exist_ok=True)
                with open(cache_file, "w", encoding="utf-8") as fh:
                    for sym, name in kept:
                        fh.write("%s|%s\n" % (sym, name))
                if not os.path.exists(DEFAULT_UNIVERSE_FILE):
                    with open(DEFAULT_UNIVERSE_FILE, "w", encoding="utf-8") as fh:
                        for sym, name in kept:
                            fh.write("%s|%s\n" % (sym, name))
                syms = [sym for sym, _ in kept]
        elif u and os.path.exists(u):
            syms = symbols_from_file(u)
        elif not u and os.path.exists(DEFAULT_UNIVERSE_FILE):
            syms = symbols_from_file(DEFAULT_UNIVERSE_FILE)
        elif not u:
            # Default nifty500 when trader/universe.txt does not exist yet
            label, kept, _ = fetch_universe_list(DEFAULT_UNIVERSE_NAME)
            os.makedirs(os.path.dirname(DEFAULT_UNIVERSE_FILE), exist_ok=True)
            with open(DEFAULT_UNIVERSE_FILE, "w", encoding="utf-8") as fh:
                for sym, name in kept:
                    fh.write("%s|%s\n" % (sym, name))
            syms = [sym for sym, _ in kept]
        else:
            raise ScanError("no file or preset '%s': use --universe nifty50|nifty500|all or a file path" % u)
    out = []
    for s in syms:
        s = norm_symbol(s)
        if s and s not in out:
            out.append(s)
    if a.limit:
        out = out[:a.limit]
    if not out:
        raise ScanError("no symbols to scan")
    return out


def symbols_from_file(path):
    if not os.path.exists(path):
        raise ScanError("no file at %s" % path)
    with open(path, encoding="utf-8-sig") as fh:
        text = fh.read()
    if text.lstrip().startswith(("{", "[")):
        try:
            doc = json.loads(text)
        except ValueError as e:
            raise ScanError("%s is not valid JSON: %s" % (path, e))
        items = doc.get("hits", []) if isinstance(doc, dict) else doc
        return [norm_symbol(str(x.get("symbol") if isinstance(x, dict) else x)) for x in items if x]
    out = []
    for ln in text.splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            out.append(norm_symbol(ln.split("|")[0].strip()))
    return out


def read_config(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8-sig") as fh:
            cfg = json.load(fh)
    except ValueError as e:
        raise ScanError("%s is not valid JSON: %s" % (path, e))
    return cfg if isinstance(cfg, dict) else {}


def write_json(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


# ---------------------------------------------------------------- commands

def cmd_universe(a):
    t0 = time.time()
    preset = (a.universe or DEFAULT_UNIVERSE_NAME).strip().lower()
    label, kept, counts = fetch_universe_list(preset, offline=a.offline)
    if not a.no_write:
        d = os.path.dirname(a.out)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as fh:
            for sym, name in kept:
                fh.write("%s|%s\n" % (sym, name))
        # Also write preset cache so `--universe <preset>` stays in sync
        preset_cache = "trader/universe-%s.txt" % preset
        if os.path.abspath(a.out) != os.path.abspath(preset_cache):
            with open(preset_cache, "w", encoding="utf-8") as fh:
                for sym, name in kept:
                    fh.write("%s|%s\n" % (sym, name))
    dropped = {k: v for k, v in counts.items() if k not in ("keep", "rows")}
    if a.json:
        print(json.dumps({"universe": preset, "label": label, "count": len(kept),
                          "rows": counts.get("rows", 0), "dropped": dropped,
                          "out": None if a.no_write else a.out,
                          "seconds": round(time.time() - t0, 1)}, indent=2, ensure_ascii=False))
    else:
        print("NSE %s list: %s rows." % (label, _indian_int(counts.get("rows", 0))))
        print("Kept %s NSE EQ-series stocks (both intraday MIS and delivery CNC eligible)." % _indian_int(len(kept)))
        if dropped:
            print("Dropped: " + ", ".join("%s %s" % (_indian_int(v), k)
                                          for k, v in sorted(dropped.items(), key=lambda kv: -kv[1])) + ".")
        if not a.no_write:
            print("Wrote %s (one SYMBOL|Name a line)." % a.out)
    return 0 if kept else 1


def cmd_daily(a):
    t0 = time.time()
    now = parse_now(a.now)
    offline_syms = None
    if a.offline:
        data, failed = load_offline(a.offline)
        offline_syms = sorted(set(data) | set(failed))
    symbols = read_symbols(a, offline_syms)
    if not a.offline:
        progress(a, "Fetching daily closes for %s NSE symbols (%d spark requests)..."
                 % (_indian_int(len(symbols)), (len(symbols) + SPARK_MAX - 1) // SPARK_MAX))
        data, failed = fetch_spark(symbols, "3mo", "1d", a.pause, a)
    hits, counts, last_date = scan_daily(data, symbols, now, a.min_price, a.min_volume)
    nfailed = len(symbols) - counts["fetched"]
    as_of = ("%s close" % last_date.isoformat()) if last_date else None
    local = to_ist(now)
    doc = {
        "created_by": "trader-scanner",
        "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "now_ist": local.strftime("%Y-%m-%d %H:%M:%S IST"),
        "exchange": "NSE",
        "mode": "daily",
        "as_of": as_of,
        "universe": len(symbols),
        "scanned": counts["scanned"],
        "failed": nfailed,
        "source": "yahoo",
        "filters": {"min_price": a.min_price, "min_volume": a.min_volume},
        "note": NOTE,
        "hits": hits,
    }
    if a.offline:
        doc["offline"] = os.path.basename(a.offline.rstrip("/\\"))
    if not a.no_write:
        write_json(a.out, doc)
    if a.json:
        print(json.dumps(dict(doc, counts=counts, seconds=round(time.time() - t0, 1)), indent=2, ensure_ascii=False))
        return 0 if hits else 1
    print("Daily scan (swing CNC & intraday MIS watchlist), %s IST, closes through %s (Yahoo spark, .NS daily closes)." % (
        local.strftime("%a %Y-%m-%d %H:%M"), last_date.isoformat() if last_date else "nothing"))
    print("Fetched %s of %s charts%s. %s passed the liquidity filter (₹%s+ and %s+ shares)%s; "
          "%s scanned." % (_indian_int(counts["fetched"]), _indian_int(len(symbols)),
                           (" (%s failed or had no data)" % _indian_int(nfailed)) if nfailed else "",
                           _indian_int(counts["fetched"] - counts["illiquid"]), _n(a.min_price),
                           _indian_int(a.min_volume),
                           (", %d had under %d days" % (counts["short"], MIN_DAILY_BARS)) if counts["short"] else "",
                           _indian_int(counts["scanned"])))
    if hits:
        per = [(k, sum(1 for h in hits if k in h["setups"])) for k, _ in SETUPS]
        print("%d chart%s matched a setup: %s." % (len(hits), "" if len(hits) == 1 else "s",
                                                  ", ".join("%d %s" % (n, LABEL[k]) for k, n in per if n)))
        print("Most traded first (₹ turnover = price x volume):")
    else:
        print("No setups.")
    for h in hits[:a.show]:
        print("  %-12s %12s  RSI %5.1f  %s" % (h["symbol"], money(h["price"]), h["rsi"],
                                               ", ".join(LABEL[t] for t in h["setups"])))
    if len(hits) > a.show:
        print("  ... and %d more in the watchlist (--show N for more)." % (len(hits) - a.show))
    if not a.no_write:
        print("Wrote %s (mode daily, %d hit%s)." % (a.out, len(hits), "" if len(hits) == 1 else "s"))
    print(NOTE + " Next: pick one chart, then /trader-news SYMBOL.")
    return 0 if hits else 1


def _n(x):
    x = float(x)
    return str(int(x)) if x == int(x) else "%.2f" % x


def cmd_orb(a):
    t0 = time.time()
    now = parse_now(a.now)
    at = parse_hhmm(a.at) if a.at else None
    cfg = read_config(a.config)
    until = parse_hhmm(a.until or cfg.get("no_entries_after") or "14:45", "--until")
    data = failed = None
    explicit_day = None
    offline_syms = None
    if a.offline:
        data, failed = load_offline(a.offline)
        offline_syms = sorted(set(data) | set(failed))
        if not a.now:
            days = [ts_to_ist(s["t"][-1]).date() for s in data.values() if s["t"]]
            if days:
                explicit_day = max(days)
                if at is None:
                    raise ScanError("offline replay: give --at HH:MM or --now \"YYYY-MM-DD HH:MM\"")
    elif a.now:
        explicit_day = to_ist(now).date()
    day, cutoff = orb_window(now, at, a.or_minutes, until, explicit_day)
    symbols = read_symbols(a, offline_syms)
    confirm = None
    if not a.offline:
        today = to_ist(parse_now(None)).date()
        diff_days = (today - day).days
        if diff_days > 55:
            raise ScanError("Yahoo's free 5-minute history is at most 60 days: replay %s with --offline" % day.isoformat())
        rng = "1d" if day == today else ("5d" if diff_days <= 4 else "1mo")
        progress(a, "Stage 1: 5-minute closes for %s NSE symbols (%d spark requests)..."
                 % (_indian_int(len(symbols)), (len(symbols) + SPARK_MAX - 1) // SPARK_MAX))
        data, failed = fetch_spark(symbols, rng, "%dm" % BAR_MIN, a.pause, a)
        done = []

        def confirm(sym):
            if a.pause:
                SLEEP(a.pause)
            done.append(sym)
            if len(done) % 50 == 0:
                progress(a, "  stage 2: %d v8 charts so far" % len(done))
            return parse_chart(yahoo_get(chart_path(sym, rng, "%dm" % BAR_MIN)), sym)
    hits, counts, failed2 = scan_orb(data, symbols, day, cutoff, a.or_minutes, a.min_price, a.min_volume, confirm,
                                     a.max_confirm)
    failed = dict(failed or {})
    failed.update(failed2)
    nfailed = len([s for s in symbols if s in failed])
    as_of = "%s %s IST" % (day.isoformat(), cutoff.strftime("%H:%M"))
    local = to_ist(now)
    doc = {
        "created_by": "trader-scanner",
        "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "now_ist": local.strftime("%Y-%m-%d %H:%M:%S IST"),
        "exchange": "NSE",
        "mode": "orb",
        "as_of": as_of,
        "universe": len(symbols),
        "scanned": counts["scanned"],
        "failed": nfailed,
        "unconfirmed": counts["unconfirmed"],
        "source": "yahoo",
        "or_minutes": a.or_minutes,
        "interval": "%dm" % BAR_MIN,
        "filters": {"min_price": a.min_price, "min_volume": a.min_volume},
        "note": NOTE,
        "hits": hits,
    }
    if a.offline:
        doc["offline"] = os.path.basename(a.offline.rstrip("/\\"))
    if not a.no_write:
        write_json(a.out, doc)
    if a.json:
        print(json.dumps(dict(doc, counts=counts, seconds=round(time.time() - t0, 1)), indent=2, ensure_ascii=False))
        return 0 if hits else 1
    print("Opening-range breakout scan (intraday MIS), %s %s: %d-minute range (09:15 to %s), 5-minute bars, Yahoo (.NS)."
          % (day_label(day), clock_label(cutoff), a.or_minutes, clock_label(slot(day, a.or_minutes // BAR_MIN))))
    print("Fetched %s of %s charts%s; %s usable%s; %s candidates, %s confirmed with real highs and lows."
          % (_indian_int(counts["fetched"]), _indian_int(len(symbols)),
             (" (%s failed or had no data)" % _indian_int(nfailed)) if nfailed else "",
             _indian_int(counts["scanned"]),
             (" (%s under the price / volume floor)" % _indian_int(counts["illiquid"])) if counts["illiquid"] else "",
             _indian_int(counts["candidates"]), _indian_int(counts["confirmed"])))
    if counts["unconfirmed"]:
        print("PARTIAL: %s more candidates were not checked (only the %s most traded are; --max-confirm 0 "
              "checks all)." % (_indian_int(counts["unconfirmed"]), _indian_int(a.max_confirm)))
    if hits:
        print("%d of %s charts closed above their opening-range high by %s:"
              % (len(hits), ("the %s checked" % _indian_int(counts["confirmed"])) if counts["unconfirmed"]
                 else _indian_int(counts["scanned"]), clock_label(cutoff)))
        for h in hits[:a.show]:
            print("  %9s  %-12s %12s  range ₹%s to ₹%s  %+.2f%% over" % (
                h["at"], h["symbol"], money(h["price"]), _p(h["or_low"]), _p(h["or_high"]), h["pct_over"]))
        if len(hits) > a.show:
            print("  ... and %d more in the watchlist (--show N for more)." % (len(hits) - a.show))
    else:
        print("No chart closed above its opening-range high by %s." % clock_label(cutoff))
    if not a.no_write:
        print("Wrote %s (mode orb, %d hit%s)." % (a.out, len(hits), "" if len(hits) == 1 else "s"))
    print(NOTE + " Next: pick one chart, then /trader-news SYMBOL and /trader-entry SYMBOL.")
    return 0 if hits else 1


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Chart Scanner (trader-scanner, India NSE/BSE): daily setups and opening-range breakouts.")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("universe", help="download NSE list (nifty50, nifty500, or all) -> trader/universe.txt")
    p.add_argument("--universe", default=DEFAULT_UNIVERSE_NAME,
                   help="nifty50 | nifty500 | all (default: %s)" % DEFAULT_UNIVERSE_NAME)
    p.add_argument("--out", default=DEFAULT_UNIVERSE_FILE)
    p.add_argument("--offline", metavar="DIR", help="folder or CSV file for offline testing")
    p.add_argument("--no-write", action="store_true")
    p.add_argument("--pause", type=float, default=PAUSE)
    p.add_argument("--json", action="store_true")

    def common(p, min_price, min_volume):
        g = p.add_mutually_exclusive_group()
        g.add_argument("--symbols", help="comma-separated NSE symbols instead of the universe (e.g. RELIANCE,TCS,SBIN)")
        g.add_argument("--watchlist", metavar="FILE", help="a watchlist.json (hits) or a text file of symbols")
        p.add_argument("--universe", default=None,
                       help="nifty50 | nifty500 | all, or a file path (default: trader/universe.txt or nifty500)")
        p.add_argument("--limit", type=int, help="scan only the first N symbols")
        p.add_argument("--min-price", type=float, default=min_price, help="minimum price in ₹ (default ₹%s)" % _n(min_price))
        p.add_argument("--min-volume", type=int, default=min_volume)
        p.add_argument("--offline", metavar="PATH", help="fixture folder or file instead of the network")
        p.add_argument("--now", help='pretend it is this time ("2026-10-30 10:15" IST, or ...Z)')
        p.add_argument("--out", default="trader/watchlist.json")
        p.add_argument("--no-write", action="store_true")
        p.add_argument("--pause", type=float, default=PAUSE, help="seconds between requests (%.1f)" % PAUSE)
        p.add_argument("--show", type=int, default=50, help="hits to print (all are written)")
        p.add_argument("--quiet", action="store_true")
        p.add_argument("--json", action="store_true")

    p = sub.add_parser("daily", help="pre-market / post-close setups from daily closes (swing CNC & intraday MIS)")
    common(p, 50.0, 500000)
    p = sub.add_parser("orb", help="intraday opening-range breakouts so far today (09:15–09:30 IST range)")
    common(p, 50.0, 0)
    p.add_argument("--or-minutes", type=int, default=15)
    p.add_argument("--max-confirm", type=int, default=MAX_CONFIRM,
                   help="v8 charts to fetch in stage 2, most traded first (%d; 0 = all)" % MAX_CONFIRM)
    p.add_argument("--at", help="cut the clock at this IST time, HH:MM")
    p.add_argument("--until", help="no new entries after HH:MM IST (default: config no_entries_after, 14:45)")
    p.add_argument("--config", default="trader/config.json")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"universe": cmd_universe, "daily": cmd_daily, "orb": cmd_orb}[a.cmd](a)
    except ScanError as e:
        print("scan.py: %s" % e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("scan.py: stopped", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
