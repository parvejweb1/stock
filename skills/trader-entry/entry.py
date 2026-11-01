#!/usr/bin/env python3
"""Entry Timer for trader-entry (India / NSE): watches a setup bar by bar and
says the moment to get in. Standard library only. Long side only. It never places an order.

    python entry.py check SBIN                           # opening-range breakout, 5-minute bars (intraday MIS)
    python entry.py check SBIN --now "2026-10-30 10:30" --write
    python entry.py check RELIANCE --setup level --level 1175.00
    python entry.py check SBIN --interval 1m --min-volume-x 1.5 --write
    python entry.py check SBIN --swing                   # daily-bar close above 20-day high (CNC delivery)
    python entry.py check SBIN --swing --level 955.00 --write
    python entry.py watch SBIN --every 30 --until 14:45  # one line per closed bar
    python entry.py watchlist                            # every hit in trader/watchlist.json

Modes (from trader/config.json "trade_type", overridden by --intraday or --swing):
  intraday (product MIS):
    orb    opening-range breakout. The range is the high and low of the first
           --or-minutes (default 15: the 09:15, 09:20 and 09:25 five-minute bars).
           It triggers on the first COMPLETED bar after the range that CLOSES
           above the range high. A wick above the high is not a trigger.
    level  the first completed bar that closes above --level.
  swing (product CNC, daily bars):
    20d / orb  triggers when the completed daily bar closes above the prior
               20-trading-day high (or --level if given).
    level      triggers when the completed daily bar closes above --level.

Educational tool, not SEBI-registered investment advice. Trading loses most people money.
"""

import argparse
import datetime
import gzip
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

try:
    from zoneinfo import ZoneInfo
    IST_TZ = ZoneInfo("Asia/Kolkata")
except Exception:
    IST_TZ = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

IST_OFFSET = datetime.timedelta(hours=5, minutes=30)

# Approximate Indian brokerage & statutory charges (Groww/Zerodha-like)
APPROX_CHARGES = {
    "intraday_brokerage_pct": 0.0005,
    "intraday_brokerage_max_inr": 20.0,
    "delivery_brokerage_pct": 0.001,
    "delivery_brokerage_min_inr": 5.0,
    "delivery_brokerage_max_inr": 20.0,
    "stt_delivery_pct": 0.001,       # 0.1% buy + sell
    "stt_intraday_sell_pct": 0.00025, # 0.025% sell
    "exchange_txn_pct": 0.0000297,   # ~0.00297%
    "sebi_per_crore_inr": 10.0,
    "stamp_delivery_buy_pct": 0.00015,
    "stamp_intraday_buy_pct": 0.00003,
    "gst_pct": 0.18,
    "dp_charge_inr": 15.93,
}

DISCLAIMER = "Educational tool, not SEBI-registered investment advice. Trading loses most people money."
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
PAUSE = 0.5      # seconds between requests to the free endpoint
BACKOFF = 5.0    # seconds before the one retry after an HTTP 429
GRACE = 2        # seconds after a bar closes before watch asks for it
SETTLE = 60      # seconds a closed bar may wait for Yahoo's data to catch up
HOSTS = ("query2", "query1")
INTERVALS = {"1m": 60, "5m": 300, "1d": 86400}
DEFAULTS = {
    "no_entries_before": "09:30",
    "no_entries_after": "14:45",
    "trade_type": "intraday",
    "product": "MIS",
    "swing_max_hold_days": 10,
}
EPOCH = datetime.datetime(1970, 1, 1)

NSE_HOLIDAYS_2026 = {
    "2026-01-15": "Municipal Corporation Election / Pongal",
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
    "2026-11-10": "Diwali Balipratipada",
    "2026-11-24": "Prakash Gurpurb Sri Guru Nanak Dev",
    "2026-12-25": "Christmas",
}
MUHURAT_2026 = {
    "2026-11-08": "Diwali Laxmi Pujan (Muhurat trading special session — check NSE circular)",
}
CALENDAR_YEARS = (2026,)
OPEN = datetime.time(9, 15)
CLOSE = datetime.time(15, 30)


class EntryError(Exception):
    pass


# ---------------------------------------------------------------- time & formatting

def to_ist(utc):
    """Naive UTC -> naive IST (UTC+05:30, no DST)."""
    return utc.replace(tzinfo=None) + IST_OFFSET


def ist_to_utc(local):
    """Naive IST -> naive UTC."""
    return local.replace(tzinfo=None) - IST_OFFSET


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)


def utc_of(ts):
    """Unix seconds -> naive UTC."""
    return EPOCH + datetime.timedelta(seconds=int(ts))


def ts_of(utc):
    return int((utc.replace(tzinfo=None) - EPOCH).total_seconds())


def iso(utc):
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def round_tick(x):
    """Round to nearest ₹0.05 tick."""
    if x is None:
        return None
    return round(round(float(x) * 20.0) / 20.0, 2) + 0.0


def r2(x):
    return None if x is None else round(float(x), 2) + 0.0


def format_inr(x):
    """Format number as ₹1,23,456.75 (Indian grouping)."""
    if x is None:
        return "-"
    val = float(x)
    neg = val < 0
    val = abs(val)
    s = "%.2f" % val
    whole, dec = s.split(".")
    if len(whole) > 3:
        last3 = whole[-3:]
        rest = whole[:-3]
        groups = []
        while len(rest) > 2:
            groups.append(rest[-2:])
            rest = rest[:-2]
        if rest:
            groups.append(rest)
        whole = ",".join(reversed(groups)) + "," + last3
    return ("%s₹%s.%s" % ("-" if neg else "", whole, dec))


def parse_now(text):
    """--now -> naive UTC. "2026-10-30 10:30" (or just 10:30 for today) is IST."""
    if not text:
        return utc_now()
    t = text.strip()
    for suffix in (" IST", " ET"):
        if t.upper().endswith(suffix):
            t = t[:-len(suffix)].strip()
    if re.match(r"^\d{1,2}:\d{2}$", t):
        t = "%s %s" % (to_ist(utc_now()).date().isoformat(), t.zfill(5))
    t = t.replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise EntryError('--now wants an IST time like "2026-10-30 10:30" (or ...Z for UTC)')
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def parse_hhmm(text, what):
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", str(text))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise EntryError("%s wants an IST time like 14:45, not %r" % (what, text))
    return datetime.time(int(m.group(1)), int(m.group(2)))


def hm(t):
    return "%02d:%02d" % (t.hour, t.minute)


def short(t):
    return "%d:%02d" % (t.hour, t.minute)


def ampm(t):
    h = t.hour % 12 or 12
    return "%d:%02d %s" % (h, t.minute, "AM" if t.hour < 12 else "PM")


def load_nse_holidays():
    """Return holiday dict {YYYY-MM-DD: description}, reading trader/cache/nse_holidays.json if present."""
    holidays = dict(NSE_HOLIDAYS_2026)
    cache_path = os.path.join("trader", "cache", "nse_holidays.json")
    if os.path.exists(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as fh:
                cached = json.load(fh)
            if isinstance(cached, dict):
                holidays.update(cached)
        except Exception:
            pass
    return holidays


def trading_day(d):
    """(is_trading_day, reason). Weekends, Muhurat special session, and NSE holiday list."""
    ds = d.isoformat()
    if ds in MUHURAT_2026:
        return False, MUHURAT_2026[ds]
    if d.weekday() >= 5:
        return False, "weekend"
    holidays = load_nse_holidays()
    name = holidays.get(ds)
    if name:
        return False, "NSE holiday: " + name
    return True, ""


def session(d):
    """(open_time, close_time) in IST, or None if closed all day."""
    ok, _ = trading_day(d)
    if not ok:
        return None
    return OPEN, CLOSE


def next_open(d):
    nd = d + datetime.timedelta(days=1)
    for _ in range(15):
        if session(nd):
            return "%s 09:15" % nd.isoformat()
        nd += datetime.timedelta(days=1)
    return None


# ---------------------------------------------------------------- symbols & Yahoo

def plain_symbol(sym):
    s = str(sym or "").strip().upper()
    if s.endswith(".NS") or s.endswith(".BO"):
        return s[:-3]
    return s


def yahoo_symbol(sym):
    s = str(sym or "").strip().upper()
    if not s:
        raise EntryError("symbol cannot be empty")
    if s.startswith("^") or s.endswith(".NS") or s.endswith(".BO"):
        return s
    return s + ".NS"


def chart_path(sym, rng, interval):
    ysym = urllib.parse.quote(yahoo_symbol(sym), safe="")
    return "/v8/finance/chart/%s?range=%s&interval=%s" % (ysym, rng, interval)


def chart_range(day, today, interval):
    """The smallest Yahoo range that still holds the session of `day`."""
    if interval == "1d":
        back = (today - day).days
        if back <= 90:
            return "6mo"
        if back <= 300:
            return "1y"
        return "2y"
    back = (today - day).days
    if back <= 0:
        return "1d"
    if back <= 6:
        return "5d"
    if interval == "1m":
        raise EntryError("Yahoo keeps 1-minute bars for about a week: use --interval 5m for %s" % day)
    if back <= 28:
        return "1mo"
    if back <= 58:
        return "60d"
    raise EntryError("Yahoo keeps 5-minute bars for about 60 days: %s is too far back" % day)


def _open_url(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _sleep(seconds):
    time.sleep(seconds)


def yahoo_get(path):
    """GET a Yahoo path: query2 first, query1 when query2 answers 429 or 5xx."""
    last = "no answer"
    for host in HOSTS:
        url = "https://%s.finance.yahoo.com%s" % (host, path)
        for attempt in (0, 1):
            try:
                return _open_url(url)
            except urllib.error.HTTPError as e:
                code, body = e.code, None
                try:
                    if code == 404:
                        body = e.read().decode("utf-8", "replace")
                except Exception:
                    body = None
                finally:
                    e.close()
                if code == 404:
                    if body:
                        return body
                    raise EntryError("Yahoo has no chart at %s (HTTP 404)" % url.split("?")[0])
                last = "HTTP %d" % code
                if code == 429 and attempt == 0:
                    _sleep(BACKOFF)
                    continue
                if code == 429 or code >= 500:
                    break
                raise EntryError("Yahoo answered HTTP %d for %s" % (code, url.split("?")[0]))
            except urllib.error.URLError as e:
                last = "could not connect: %s" % (e.reason,)
                break
            except OSError as e:
                last = "network error: %s" % (e,)
                break
    raise EntryError("Yahoo did not answer (%s on query2 and query1): wait a minute and try again" % last)


def parse_chart(text, sym):
    """Yahoo chart JSON v8 -> {symbol, yahoo, meta, bars, prev_close, data_time}."""
    psym = plain_symbol(sym)
    ysym = yahoo_symbol(sym)
    try:
        data = json.loads(text)
        res = data["chart"]["result"]
    except (ValueError, KeyError, TypeError):
        raise EntryError("Yahoo returned something unexpected for %s" % psym)
    if not res:
        err = (data.get("chart") or {}).get("error") or {}
        raise EntryError("Yahoo has no data for %s: %s" % (psym, err.get("description") or "unknown ticker"))
    r = res[0] or {}
    meta = r.get("meta") or {}
    q = ((r.get("indicators") or {}).get("quote") or [{}])[0] or {}

    def at(key, i):
        arr = q.get(key) or []
        return arr[i] if i < len(arr) else None

    bars = []
    for i, t in enumerate(r.get("timestamp") or []):
        c = at("close", i)
        if t is None or c is None:
            continue
        o, h, lo = at("open", i), at("high", i), at("low", i)
        bars.append({"t": int(t), "o": float(c if o is None else o), "h": float(c if h is None else h),
                     "l": float(c if lo is None else lo), "c": float(c), "v": int(at("volume", i) or 0)})
    meta_sym = str(meta.get("symbol") or ysym).upper()
    return {"symbol": plain_symbol(meta_sym), "yahoo": meta_sym, "meta": meta, "bars": bars,
            "prev_close": meta.get("previousClose") or meta.get("chartPreviousClose"),
            "data_time": meta.get("regularMarketTime")}


def read_text(path):
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return fh.read()
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def offline_file(path, sym):
    psym = plain_symbol(sym)
    ysym = yahoo_symbol(sym)
    if os.path.isdir(path):
        for name in (psym + ".json", psym + ".json.gz", ysym + ".json", ysym + ".json.gz"):
            p = os.path.join(path, name)
            if os.path.exists(p):
                return p
        raise EntryError("no offline file for %s in %s (want %s.json)" % (psym, path, psym))
    if not os.path.exists(path):
        raise EntryError("no offline file %s" % path)
    return path


def get_chart(sym, interval, day=None, offline=None, today=None):
    """Bars for one ticker, from Yahoo or a saved file."""
    psym = plain_symbol(sym)
    ysym = yahoo_symbol(sym)
    if offline:
        f = offline_file(offline, sym)
        try:
            text = read_text(f)
        except (OSError, ValueError) as e:
            raise EntryError("could not read %s: %s" % (f, e))
    else:
        today = today or to_ist(utc_now()).date()
        text = yahoo_get(chart_path(sym, chart_range(day or today, today, interval), interval))
    ch = parse_chart(text, sym)
    if ch["symbol"] != psym and ch["yahoo"] != ysym:
        raise EntryError("the chart file holds %s, not %s" % (ch["yahoo"], ysym))
    gran = ch["meta"].get("dataGranularity")
    if gran and gran != interval:
        raise EntryError("%s has %s bars, not %s: use --interval %s" % (psym, gran, interval, gran))
    return ch


# ---------------------------------------------------------------- the rule

def read_config(path):
    cfg = dict(DEFAULTS)
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except ValueError as e:
            raise EntryError("%s is not valid JSON: %s" % (path, e))
        for k in DEFAULTS:
            if doc.get(k) is not None:
                cfg[k] = doc[k]
    for k in ("no_entries_before", "no_entries_after"):
        parse_hhmm(cfg[k], k)
    return cfg


def evaluate_swing(chart, now_utc, setup="20d", level=None, min_volume_x=None, cfg=None):
    """Evaluate a daily-bar swing setup (close above prior 20-day high or --level)."""
    cfg = cfg or dict(DEFAULTS)
    local = to_ist(now_utc)
    day = local.date()
    dt = chart.get("data_time")
    data_time_str = to_ist(utc_of(dt)).strftime("%Y-%m-%d %H:%M:%S") if dt else None

    daily_by_date = {}
    for b in chart["bars"]:
        b_ist = to_ist(utc_of(b["t"]))
        b_date = b_ist.date()
        daily_by_date[b_date] = dict(b, date=b_date, et=b_ist)

    sorted_dates = sorted(daily_by_date)
    eff_setup = "level" if level is not None else ("20d" if setup in ("orb", "20d") else setup)

    # Determine which session date we are evaluating:
    # On a weekend/holiday, evaluate the latest completed trading day on or before `day`.
    eval_day = day
    ok_today, why_today = trading_day(day)
    if not ok_today:
        prior_dates = [d for d in sorted_dates if d <= day]
        if prior_dates:
            eval_day = prior_dates[-1]

    res = {
        "symbol": chart["symbol"],
        "yahoo": chart["yahoo"],
        "trade_type": "swing",
        "product": "CNC",
        "setup": eff_setup,
        "side": "buy",
        "date": eval_day.isoformat(),
        "interval": "1d",
        "or_minutes": None,
        "now": local.strftime("%Y-%m-%d %H:%M:%S"),
        "now_ist": local.strftime("%Y-%m-%d %H:%M:%S"),
        "status": "waiting",
        "phase": "open" if (ok_today and OPEN <= local.time() < CLOSE) else "after-close",
        "reason": "",
        "opening_range": None,
        "swing_range": None,
        "level": None,
        "trigger": None,
        "last": None,
        "forming": None,
        "min_volume_x": min_volume_x,
        "prev_close": chart.get("prev_close"),
        "calendar_known": day.year in CALENDAR_YEARS,
        "data_time": data_time_str,
        "opens": hm(OPEN),
        "closes": hm(CLOSE),
        "_bars": [],
    }

    prior_bars = [daily_by_date[d] for d in sorted_dates if d < eval_day]
    if len(prior_bars) < 5:
        res.update(status="no-data", phase="closed", reason="fewer than 5 prior daily bars for %s" % chart["symbol"])
        return res

    lookback = prior_bars[-20:]
    high_20d = max(b["h"] for b in lookback)
    low_20d = min(b["l"] for b in lookback)
    low_10d = min(b["l"] for b in prior_bars[-10:])
    avg_vol = sum(b["v"] for b in lookback) / float(len(lookback))
    lvl = float(level) if level is not None else high_20d
    res["level"] = round_tick(lvl)
    res["swing_range"] = {
        "high_20d": round_tick(high_20d),
        "low_20d": round_tick(low_20d),
        "swing_low_10d": round_tick(low_10d),
        "days": len(lookback),
        "avg_volume": int(round(avg_vol)),
    }
    # Also populate opening_range with 20d high / 10d swing low so downstream tools have range bounds if needed
    res["opening_range"] = {
        "high": round_tick(high_20d),
        "low": round_tick(low_10d),
        "minutes": 0,
        "bars": len(lookback),
        "of": 20,
        "complete": True,
        "avg_volume": int(round(avg_vol)),
    }

    cur = daily_by_date.get(eval_day)
    if cur is None and prior_bars:
        cur = prior_bars[-1]
        eval_day = cur["date"]
        res["date"] = eval_day.isoformat()
        prior_bars = [daily_by_date[d] for d in sorted_dates if d < eval_day]
        lookback = prior_bars[-20:]
        if level is None and lookback:
            high_20d = max(b["h"] for b in lookback)
            lvl = high_20d
            res["level"] = round_tick(lvl)

    if cur is None:
        res.update(status="no-data", reason="Yahoo sent no daily bar for %s" % eval_day.isoformat())
        return res

    res["_bars"] = [dict(b, slot=i, end=datetime.datetime.combine(b["date"], CLOSE))
                    for i, b in enumerate(prior_bars[-20:] + [cur])]
    res["bars_closed"] = len(res["_bars"])
    res["last"] = {
        "bar_start": cur["date"].isoformat(),
        "close": round_tick(cur["c"]),
        "vs_level_pct": r2(100.0 * (cur["c"] / lvl - 1)),
    }

    vx = (cur["v"] / avg_vol) if avg_vol else None
    lvl_desc = "the 20-day high %s" % format_inr(lvl) if eff_setup == "20d" else "the level %s" % format_inr(lvl)
    if cur["c"] > lvl:
        if min_volume_x and (vx is None or vx < min_volume_x):
            res["status"] = "waiting"
            res["reason"] = ("daily close %s on %s is above %s, but volume %.2fx is under %.2fx"
                             % (format_inr(cur["c"]), eval_day.isoformat(), lvl_desc, vx or 0.0, min_volume_x))
            return res
        res["status"] = "triggered"
        res["reason"] = "the %s daily bar closed above %s" % (eval_day.isoformat(), lvl_desc)
        res["trigger"] = {
            "time": "3:30 PM",
            "close_time": "15:30",
            "bar_start": "09:15",
            "bar_index": len(lookback),
            "price": round_tick(cur["c"]),
            "close": cur["c"],
            "volume": cur["v"],
            "volume_x": r2(vx),
            "pct_over": r2(100.0 * (cur["c"] / lvl - 1)),
        }
        res["minutes_since"] = 0
        return res

    gap = lvl - cur["c"]
    res["status"] = "waiting"
    res["reason"] = ("daily close %s on %s is %s under %s (%+.2f%%)"
                     % (format_inr(cur["c"]), eval_day.isoformat(), format_inr(abs(gap)), lvl_desc,
                        100.0 * (cur["c"] / lvl - 1)))
    return res


def evaluate(chart, now_utc, setup="orb", or_minutes=15, interval="5m", level=None,
             min_volume_x=None, cfg=None, trade_type="intraday"):
    """Pure function: the setup's status at now_utc from one session's bars (or daily bars for swing)."""
    cfg = cfg or dict(DEFAULTS)
    if trade_type == "swing" or interval == "1d":
        return evaluate_swing(chart, now_utc, setup=setup, level=level, min_volume_x=min_volume_x, cfg=cfg)
    if interval not in ("1m", "5m"):
        raise EntryError("--interval is 5m or 1m for intraday (or use --swing for 1d)")
    step = INTERVALS[interval]
    if or_minutes <= 0 or (or_minutes * 60) % step:
        raise EntryError("--or-minutes must be a whole number of %s bars" % interval)
    if setup not in ("orb", "level"):
        raise EntryError("--setup is orb or level")
    if setup == "level" and level is None:
        raise EntryError("--setup level needs --level PRICE (for example a 20-day high)")
    n_or = or_minutes * 60 // step
    local = to_ist(now_utc)
    day = local.date()
    res = {
        "symbol": chart["symbol"],
        "yahoo": chart["yahoo"],
        "trade_type": "intraday",
        "product": cfg.get("product") or "MIS",
        "setup": setup,
        "side": "buy",
        "date": day.isoformat(),
        "interval": interval,
        "or_minutes": or_minutes,
        "now": local.strftime("%Y-%m-%d %H:%M:%S"),
        "now_ist": local.strftime("%Y-%m-%d %H:%M:%S"),
        "status": "waiting",
        "phase": "",
        "reason": "",
        "opening_range": None,
        "level": None,
        "trigger": None,
        "last": None,
        "forming": None,
        "min_volume_x": min_volume_x,
        "prev_close": chart.get("prev_close"),
        "calendar_known": day.year in CALENDAR_YEARS,
        "_bars": [],
    }
    dt = chart.get("data_time")
    res["data_time"] = to_ist(utc_of(dt)).strftime("%Y-%m-%d %H:%M:%S") if dt else None
    ok, why = trading_day(day)
    if not ok:
        res.update(status="no-data", phase="closed", reason="the NSE is closed today (%s)" % why,
                   next_open=next_open(day))
        return res
    o, c = session(day)
    open_dt = datetime.datetime.combine(day, o)
    close_dt = datetime.datetime.combine(day, c)
    before = datetime.datetime.combine(day, parse_hhmm(cfg["no_entries_before"], "no_entries_before"))
    cutoff = datetime.datetime.combine(day, parse_hhmm(cfg["no_entries_after"], "no_entries_after"))
    if cutoff > close_dt:
        cutoff = close_dt
    or_end = open_dt + datetime.timedelta(minutes=or_minutes)
    res.update(opens=hm(o), closes=hm(c), early_close=None,
               no_entries_before=hm(before), no_entries_after=hm(cutoff))

    by_slot = {}
    for b in chart["bars"]:
        if b["t"] % step:
            continue
        et = to_ist(utc_of(b["t"]))
        if et.date() != day or not (open_dt <= et < close_dt):
            continue
        slot = int((et - open_dt).total_seconds()) // step
        by_slot[slot] = dict(b, et=et, end=et + datetime.timedelta(seconds=step), slot=slot)
    bars, pending = [], None
    now_ts = ts_of(now_utc)
    top = max(by_slot) if by_slot else -1
    for slot in sorted(by_slot):
        b = by_slot[slot]
        end_ts = b["t"] + step
        if end_ts <= now_ts:
            if dt is None or dt >= end_ts or slot < top or now_ts >= end_ts + SETTLE:
                bars.append(b)
            else:
                pending = b
                res["forming"] = {"bar_start": hm(b["et"]), "closes": ampm(b["end"]), "price": round_tick(b["c"])}
                res["lag_note"] = ("Yahoo's last trade is %s, before the %s bar's close: it counts once Yahoo "
                                   "catches up (a minute at most)" % (to_ist(utc_of(dt)).strftime("%H:%M:%S"),
                                                                       short(b["et"])))
        elif b["et"] <= local:
            res["forming"] = {"bar_start": hm(b["et"]), "closes": ampm(b["end"]), "price": round_tick(b["c"])}
    res["_bars"] = bars
    res["bars_closed"] = len(bars)

    if local < open_dt:
        res.update(phase="pre-open", reason="the NSE opens at 9:15%s" % (
            " and the %d-minute range is set at %s" % (or_minutes, short(or_end)) if setup == "orb" else ""))
        return res

    or_bars = [b for b in bars if b["slot"] < n_or]
    avg_vol = (sum(b["v"] for b in or_bars) / float(len(or_bars))) if or_bars else None
    if or_bars:
        res["opening_range"] = {"high": round_tick(max(b["h"] for b in or_bars)),
                                "low": round_tick(min(b["l"] for b in or_bars)),
                                "minutes": or_minutes, "bars": len(or_bars), "of": n_or,
                                "complete": local >= or_end, "avg_volume": int(round(avg_vol))}
    if setup == "orb":
        if local < or_end:
            res.update(phase="forming", reason="%d of %d range bars closed; the range is set at %s" % (
                len(or_bars), n_or, short(or_end)))
            return res
        if not or_bars:
            res.update(status="no-data", phase="open", reason="Yahoo sent no bars for the opening range")
            return res
        lvl = max(b["h"] for b in or_bars)
    else:
        lvl = float(level)
    res["level"] = round_tick(lvl)

    first_slot = n_or if setup == "orb" else 0
    trig, thin = None, []
    for b in bars:
        if b["slot"] < first_slot or b["end"] < before or b["end"] > cutoff:
            continue
        if b["c"] > lvl:
            vx = (b["v"] / avg_vol) if avg_vol else None
            if min_volume_x and (vx is None or vx < min_volume_x):
                thin.append(b)
                continue
            trig = b
            break
    last = bars[-1] if bars else None
    if last:
        res["last"] = {"bar_start": hm(last["et"]), "close": round_tick(last["c"]),
                       "vs_level_pct": r2(100.0 * (last["c"] / lvl - 1))}

    if trig:
        vx = (trig["v"] / avg_vol) if avg_vol else None
        res.update(status="triggered", phase="open" if local < close_dt else "after-close",
                   reason="the %s bar closed above %s" % (short(trig["et"]), "the range high" if setup == "orb" else "the level"))
        res["trigger"] = {"time": ampm(trig["end"]), "close_time": hm(trig["end"]), "bar_start": hm(trig["et"]),
                          "bar_index": trig["slot"], "price": round_tick(trig["c"]), "close": trig["c"],
                          "volume": trig["v"], "volume_x": r2(vx), "pct_over": r2(100.0 * (trig["c"] / lvl - 1))}
        res["minutes_since"] = int((local - trig["end"]).total_seconds() // 60)
        if dt and utc_of(dt) < ist_to_utc(trig["end"]):
            res["data_note"] = ("Yahoo's last trade is %s, before this bar closed: its close can still move a tick or two"
                                % to_ist(utc_of(dt)).strftime("%H:%M:%S"))
        return res
    if not bars:
        res.update(status="no-data", phase="open", reason="Yahoo sent no bars for %s yet" % day.isoformat())
        return res
    what = "close above the range high %s" % format_inr(lvl) if setup == "orb" else "close above %s" % format_inr(lvl)
    if local >= cutoff and not (pending and pending["end"] <= cutoff):
        res.update(status="expired", phase="after-close" if local >= close_dt else "after-cutoff",
                   reason="no %s by %s, the last time for a new entry" % (what, short(cutoff)))
        return res
    res["phase"] = "open"
    if last["c"] > lvl and last in thin:
        res["reason"] = "the %s bar closed above at %s but on %.2fx the range's volume, under your %.2fx" % (
            short(last["et"]), format_inr(last["c"]), last["v"] / avg_vol if avg_vol else 0, min_volume_x)
    elif last["c"] > lvl and last["end"] < before:
        res["reason"] = "the %s bar closed above at %s, but no entries before %s (config)" % (
            short(last["et"]), format_inr(last["c"]), short(before))
    else:
        gap = lvl - last["c"]
        res["reason"] = "last close %s (%s bar), %s %s %s (%+.2f%%)" % (
            format_inr(last["c"]), short(last["et"]), format_inr(abs(gap)), "under" if gap >= 0 else "over",
            "the range high %s" % format_inr(lvl) if setup == "orb" else "the level %s" % format_inr(lvl),
            100.0 * (last["c"] / lvl - 1))
    return res


def public(res):
    """The result without the internal bar list, ready for JSON."""
    return {k: v for k, v in res.items() if not k.startswith("_")}


def entry_doc(res, now_utc):
    """trader/entry.json, the contract shape (plus trade_type, product, yahoo)."""
    t = res.get("trigger") or {}
    orr = res.get("opening_range")
    doc = {
        "created_by": "trader-entry",
        "symbol": res["symbol"],
        "yahoo": res.get("yahoo") or yahoo_symbol(res["symbol"]),
        "trade_type": res.get("trade_type") or "intraday",
        "product": res.get("product") or ("CNC" if res.get("trade_type") == "swing" else "MIS"),
        "setup": res["setup"],
        "side": "buy",
        "date": res["date"],
        "status": res["status"],
        "time": t.get("time"),
        "bar_start": t.get("bar_start"),
        "bar_index": t.get("bar_index"),
        "price": t.get("price"),
        "level": res.get("level"),
        "interval": res["interval"],
        "opening_range": {"high": orr["high"], "low": orr["low"], "minutes": orr["minutes"]} if orr else None,
        "source": "yahoo",
        "checked_at": iso(now_utc),
        "now_ist": res.get("now_ist") or res.get("now"),
        "reason": res.get("reason"),
    }
    if res.get("swing_range"):
        doc["swing_range"] = res["swing_range"]
    return doc


def write_json(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


# ---------------------------------------------------------------- words

LABEL = {"triggered": "TRIGGERED", "waiting": "WAITING", "expired": "EXPIRED", "no-data": "NO DATA"}
PHASE_LABEL = {"pre-open": "NOT OPEN YET", "forming": "RANGE FORMING", "closed": "MARKET CLOSED"}
SHORT = {"pre-open": "not open", "forming": "forming", "closed": "closed"}


def setup_name(res):
    if res.get("trade_type") == "swing":
        if res["setup"] == "20d":
            return "20-day high breakout (%s)" % format_inr(res["level"]) if res.get("level") else "20-day high breakout"
        return "daily close above %s" % format_inr(res["level"]) if res.get("level") is not None else "daily close above level"
    if res["setup"] == "orb":
        return "opening-range breakout (%d-minute range)" % res["or_minutes"]
    return "close above %s" % format_inr(res["level"]) if res["level"] is not None else "close above a level"


def describe(res):
    """Plain lines for a person."""
    when = datetime.datetime.strptime(res["now"], "%Y-%m-%d %H:%M:%S")
    if res.get("trade_type") == "swing":
        bars = "daily"
    else:
        bars = "5-minute" if res["interval"] == "5m" else "1-minute"
    mode_tag = "swing CNC" if res.get("trade_type") == "swing" else "intraday MIS"
    head = "%s (%s)  %s %s IST  %s on %s bars (Yahoo, free, can lag)" % (
        res["symbol"], mode_tag, when.strftime("%a %Y-%m-%d"), when.strftime("%H:%M"), setup_name(res), bars)
    if res.get("data_time") and res["data_time"] <= res["now"] and res["data_time"][:10] == res["date"]:
        head += ", data to %s" % res["data_time"][11:]
    out = [head]
    if res.get("trade_type") == "swing" and res.get("swing_range"):
        sr = res["swing_range"]
        out.append("Prior %d trading days: 20d high %s, 10d swing low %s, 20d low %s." % (
            sr["days"], format_inr(sr["high_20d"]), format_inr(sr["swing_low_10d"]), format_inr(sr["low_20d"])))
    else:
        orr = res.get("opening_range")
        if orr and res.get("opens"):
            start = datetime.datetime.strptime(res["opens"], "%H:%M")
            end = start + datetime.timedelta(minutes=orr["minutes"])
            out.append("Opening range %s-%s: high %s, low %s (%d of %d bars%s)." % (
                short(start), short(end), format_inr(orr["high"]), format_inr(orr["low"]),
                orr["bars"], orr["of"], "" if orr["complete"] else ", still forming"))
    label = PHASE_LABEL.get(res["phase"]) or LABEL.get(res["status"], res["status"].upper())
    t = res.get("trigger")
    if t:
        if res.get("trade_type") == "swing":
            what_lvl = "the 20-day high" if res["setup"] == "20d" else "the level"
            out.append("TRIGGERED on %s daily close at %s: closed above %s %s (%+.2f%%%s)." % (
                res["date"], format_inr(t["price"]), what_lvl, format_inr(res["level"]), t["pct_over"],
                (", %.2fx 20-day avg volume" % t["volume_x"]) if res.get("min_volume_x") and t.get("volume_x") else ""))
            out.append("This is the swing entry signal (CNC delivery). Nothing was placed.")
            out.append("Next: set the stop and target BEFORE you buy: /trader-exit "
                       "(exit.py plan --swing --from trader/entry.json).")
        else:
            out.append("TRIGGERED %s at %s: the %s bar (bar %d) closed above %s %s (%+.2f%%%s)." % (
                t["time"], format_inr(t["price"]), short(datetime.datetime.strptime(t["bar_start"], "%H:%M")), t["bar_index"],
                "the range high" if res["setup"] == "orb" else "the level", format_inr(res["level"]), t["pct_over"],
                (", %.2fx the range's volume" % t["volume_x"]) if res.get("min_volume_x") and t.get("volume_x") else ""))
            step = INTERVALS[res["interval"]] // 60
            if res.get("minutes_since", 0) > step and res.get("last"):
                out.append("That was %d minutes ago; the last close is %s (%s bar). A late entry is a different "
                           "trade: the price, the stop distance and the odds have moved." % (
                               res["minutes_since"], format_inr(res["last"]["close"]), res["last"]["bar_start"]))
            if res.get("data_note"):
                out.append(res["data_note"] + ".")
            if res.get("minutes_since", 0) > step:
                out.append("Nothing was placed.")
            else:
                out.append("This is the moment to get in, if you take the trade. Nothing was placed.")
            out.append("Next: set the stop and target BEFORE you buy: /trader-exit "
                       "(exit.py plan --from trader/entry.json).")
    else:
        out.append("%s  %s." % (label, res["reason"]))
        f = res.get("forming")
        if res.get("lag_note") and res["status"] == "waiting":
            out.append(res["lag_note"] + ".")
        elif f and res["status"] == "waiting" and res["phase"] in ("open", "forming"):
            out.append("The %s bar is still forming (%s so far, closes %s); it does not count until it closes." % (
                f["bar_start"].lstrip("0"), format_inr(f["price"]), f["closes"]))
        if res["status"] == "expired":
            out.append("No entry on this setup today. Nothing was placed.")
        if res.get("next_open"):
            out.append("Next open: %s IST." % res["next_open"])
    if not res.get("calendar_known"):
        out.append("2027 holidays unknown, check NSE (holiday list covers %s only)." % ", ".join(map(str, CALENDAR_YEARS)))
    return out


def bar_line(b, res):
    """One watch line for a bar that just closed."""
    start = short(b["et"])
    orr = res.get("opening_range") or {}
    lvl = res.get("level")
    t = res.get("trigger")
    n_or = orr.get("of") or 0
    if res["setup"] == "orb" and b["slot"] < n_or:
        seen = [x for x in res["_bars"] if x["slot"] <= b["slot"]]
        note = "range so far %s to %s" % (format_inr(min(x["l"] for x in seen)), format_inr(max(x["h"] for x in seen)))
        if b["slot"] == n_or - 1:
            note = "opening range set: high %s, low %s" % (format_inr(orr["high"]), format_inr(orr["low"]))
    elif t and t["bar_index"] == b["slot"]:
        note = "TRIGGERED: closed above %s (%+.2f%%). Get in now if you take it: %s, %s" % (
            format_inr(lvl), t["pct_over"], t["time"], format_inr(t["price"]))
    elif lvl is None:
        note = ""
    elif b["c"] > lvl:
        note = "above %s" % format_inr(lvl)
        if res["setup"] == "level" or b["slot"] >= n_or:
            avg = orr.get("avg_volume")
            if res.get("min_volume_x") and avg:
                note += ", but volume %.2fx is under %.2fx" % (b["v"] / float(avg), res["min_volume_x"])
            elif b["end"] < datetime.datetime.combine(b["et"].date(), parse_hhmm(res["no_entries_before"], "x")):
                note += ", but no entries before %s" % res["no_entries_before"]
    else:
        note = "%s under %s" % (format_inr(lvl - b["c"]), format_inr(lvl))
    return "%5s bar  closed %s  %s" % (start, format_inr(b["c"]), note)


# ---------------------------------------------------------------- watch

class SimClock(object):
    """A clock that moves only when it sleeps: replays a day without waiting."""

    def __init__(self, start):
        self.t = start

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += datetime.timedelta(seconds=max(1, int(round(seconds))))


def watch_loop(provider, clock, sleep, emit, opts, every=30, until=None, max_errors=5, on_trigger=None):
    """Poll bar by bar until a trigger (0), expiry, the close or --until (1)."""
    if opts.get("trade_type") == "swing":
        now = clock()
        chart = provider(now)
        res = evaluate(chart, now, **opts)
        for line in describe(res):
            emit(line)
        if res["status"] == "triggered":
            if on_trigger:
                on_trigger(res, now)
            return 0, res
        return 1, res

    step = INTERVALS[opts.get("interval", "5m")]
    printed, errors, said_preopen, first = -1, 0, False, True
    next_fetch = None
    while True:
        now = clock()
        if next_fetch is not None and now < next_fetch:
            sleep(max(1, min(every, (next_fetch - now).total_seconds())))
            continue
        local = to_ist(now)
        day = local.date()
        s = session(day)
        if not s:
            emit("%s: NSE closed today (%s). Nothing to watch." % (day.isoformat(), trading_day(day)[1]))
            return 1, None
        open_dt = datetime.datetime.combine(day, s[0])
        close_dt = datetime.datetime.combine(day, s[1])
        if local < open_dt + datetime.timedelta(seconds=step):
            if not said_preopen:
                emit("%s IST  %s: watching from the first bar's close at %s." % (
                    local.strftime("%H:%M"), "not open yet" if local < open_dt else "open, no bar closed yet",
                    short(open_dt + datetime.timedelta(seconds=step))))
                said_preopen = True
            next_fetch = ist_to_utc(open_dt) + datetime.timedelta(seconds=step + GRACE)
            continue
        try:
            chart = provider(now)
            errors = 0
        except EntryError as e:
            errors += 1
            emit("%s IST  could not read bars: %s" % (local.strftime("%H:%M:%S"), e))
            if errors >= max_errors:
                emit("Stopping after %d failed reads in a row." % errors)
                return 1, None
            next_fetch = now + datetime.timedelta(seconds=every)
            continue
        res = evaluate(chart, now, **opts)
        bars = res["_bars"]
        lines = describe(res)
        fresh = first
        if first:
            emit(lines[0])
            if len(lines) > 1 and lines[1].startswith("Opening range"):
                emit(lines[1])
            if bars and not res.get("trigger"):
                emit(bar_line(bars[-1], res))
            printed = bars[-1]["slot"] if bars else -1
            first = False
        else:
            for b in bars:
                if b["slot"] > printed:
                    emit(bar_line(b, res))
                    printed = b["slot"]
        if res["status"] == "triggered":
            for line in lines[1:]:
                if line.startswith("Opening range") or (line.startswith("TRIGGERED") and not fresh):
                    continue
                emit(line)
            if on_trigger:
                on_trigger(res, now)
            return 0, res
        if res["status"] == "expired" or local >= close_dt:
            emit("EXPIRED  %s." % (res["reason"] or "the market closed without a trigger"))
            return 1, res
        if until and local >= until:
            emit("Stopped at --until %s without a trigger. %s." % (short(until), res["reason"]))
            return 1, res
        cur_slot = int((local - open_dt).total_seconds()) // step
        expected_last = cur_slot - 1
        behind = (not bars) or bars[-1]["slot"] < expected_last
        if behind:
            wait = every
        else:
            nxt = open_dt + datetime.timedelta(seconds=(cur_slot + 1) * step + GRACE)
            wait = (nxt - local).total_seconds()
        cutoff = datetime.datetime.combine(day, parse_hhmm(res.get("no_entries_after") or "14:45", "cutoff"))
        for stop_at in (until, cutoff, close_dt):
            if stop_at and stop_at > local:
                wait = min(wait, (stop_at - local).total_seconds())
        next_fetch = now + datetime.timedelta(seconds=max(1, wait))


# ---------------------------------------------------------------- commands

def _resolve_trade_type(a, cfg):
    if getattr(a, "swing", False) and getattr(a, "intraday", False):
        raise EntryError("use --swing or --intraday, not both")
    if getattr(a, "swing", False):
        return "swing"
    if getattr(a, "intraday", False):
        return "intraday"
    if getattr(a, "interval", None) == "1d":
        return "swing"
    t = str(cfg.get("trade_type") or "intraday").lower()
    return "swing" if t == "swing" else "intraday"


def _opts(a, cfg):
    if a.side in ("sell", "short"):
        raise EntryError("v2 plans longs only: no short entries. Watch for a buy (--side buy) instead.")
    trade_type = _resolve_trade_type(a, cfg)
    if trade_type == "intraday" and a.setup == "orb" and a.level is not None:
        raise EntryError("--level is for --setup level or --swing (an orb's level is its range high)")
    if a.min_volume_x is not None and a.min_volume_x <= 0:
        raise EntryError("--min-volume-x must be above 0")
    if a.level is not None and a.level <= 0:
        raise EntryError("--level must be a price above 0")
    interval = "1d" if trade_type == "swing" else a.interval
    return {"setup": a.setup, "or_minutes": a.or_minutes, "interval": interval, "level": a.level,
            "min_volume_x": a.min_volume_x, "cfg": cfg, "trade_type": trade_type}


def _load(a, sym, now, interval=None):
    """(chart, now). An offline file without --now is read as of its own time."""
    iv = interval or a.interval
    real_today = to_ist(utc_now()).date()
    if a.offline and now is None:
        ch = get_chart(sym, iv, offline=a.offline)
        return ch, (utc_of(ch["data_time"]) if ch.get("data_time") else utc_now())
    now = now or utc_now()
    return get_chart(sym, iv, to_ist(now).date(), a.offline, real_today), now


def cmd_check(a):
    cfg = read_config(a.config)
    opts = _opts(a, cfg)
    sym = plain_symbol(a.symbol)
    chart, now = _load(a, a.symbol, parse_now(a.now) if a.now else None, interval=opts["interval"])
    res = evaluate(chart, now, **opts)
    if a.write:
        write_json(a.write, entry_doc(res, now))
    if a.json:
        print(json.dumps(public(res), indent=2, ensure_ascii=False))
    else:
        for line in describe(res):
            print(line)
        if a.write:
            print("Wrote %s (status %s)." % (a.write, res["status"]))
        print(DISCLAIMER)
    return 0 if res["status"] == "triggered" else 1


def cmd_watch(a):
    cfg = read_config(a.config)
    opts = _opts(a, cfg)
    if a.every <= 0:
        raise EntryError("--every must be above 0 seconds")
    start = parse_now(a.now) if a.now else None
    if a.offline or start is not None:
        if start is None:
            start = _load(a, a.symbol, None, interval=opts["interval"])[1]
        clock = SimClock(start)
        now_fn, sleep_fn = clock.now, clock.sleep
    else:
        now_fn, sleep_fn = utc_now, _sleep
    real_today = to_ist(utc_now()).date()

    def provider(now):
        return get_chart(a.symbol, opts["interval"], to_ist(now).date(), a.offline, real_today)

    until = None
    if a.until:
        t = parse_hhmm(a.until, "--until")
        until = datetime.datetime.combine(to_ist(now_fn()).date(), t)

    def on_trigger(res, now):
        write_json(a.out, entry_doc(res, now))
        print("Wrote %s." % a.out, flush=True)

    def emit(line):
        print(line, flush=True)

    code, _ = watch_loop(provider, now_fn, sleep_fn, emit, opts, every=a.every, until=until,
                         on_trigger=on_trigger)
    return code


def cmd_watchlist(a):
    cfg = read_config(a.config)
    opts = _opts(a, cfg)
    if not os.path.exists(a.watchlist):
        raise EntryError("no watchlist at %s: run /trader-scanner first (it writes the hits there)" % a.watchlist)
    try:
        with open(a.watchlist, encoding="utf-8") as fh:
            doc = json.load(fh)
    except ValueError as e:
        raise EntryError("%s is not valid JSON: %s" % (a.watchlist, e))
    hits = [h for h in (doc.get("hits") or []) if isinstance(h, dict) and h.get("symbol")]
    if not hits:
        raise EntryError("no hits in %s: the scanner found nothing to watch" % a.watchlist)
    if a.offline and not os.path.isdir(a.offline):
        raise EntryError("watchlist --offline wants a folder of <TICKER>.json files")
    now = parse_now(a.now) if a.now else None
    rows = []
    for i, h in enumerate(hits):
        sym = plain_symbol(h["symbol"])
        o = dict(opts)
        if (a.setup == "level" or o["trade_type"] == "swing") and o["level"] is None:
            o["level"] = h.get("level")
        try:
            if o["trade_type"] == "intraday" and a.setup == "level" and o["level"] is None:
                raise EntryError("needs a level: --level PRICE or a \"level\" in the watchlist")
            if i and not a.offline:
                _sleep(PAUSE)
            chart, when = _load(a, sym, now, interval=o["interval"])
            rows.append((sym, evaluate(chart, when, **o), None))
        except EntryError as e:
            rows.append((sym, None, str(e)))
    if a.json:
        print(json.dumps([{"symbol": s, "result": public(r) if r else None, "error": e} for s, r, e in rows],
                         indent=2, ensure_ascii=False))
    else:
        print("%-10s  %-9s  %-8s  %-11s  %-11s  %-11s  %s" % (
            "SYMBOL", "STATUS", "TIME", "PRICE", "LEVEL", "LAST", "VS LEVEL"))
        for s, r, e in rows:
            if e:
                print("%-10s  error: %s" % (s, e))
                continue
            t = r.get("trigger") or {}
            last = r.get("last") or {}
            status = SHORT.get(r["phase"], r["status"]) if r["status"] != "triggered" else "triggered"
            print("%-10s  %-9s  %-8s  %-11s  %-11s  %-11s  %s" % (
                s[:10], status[:9], t.get("time") or "-",
                format_inr(t["price"]) if t else "-",
                format_inr(r["level"]) if r.get("level") is not None else "-",
                format_inr(last["close"]) if last else "-",
                ("%+.2f%%" % last["vs_level_pct"]) if last.get("vs_level_pct") is not None else "-"))
        n = sum(1 for _, r, _ in rows if r and r["status"] == "triggered")
        print("%d of %d triggered. Pick one, then: entry.py check <SYMBOL> --write, and /trader-exit." % (n, len(rows)))
        print("Free Yahoo bars (.NS), unofficial and can lag. Nothing was placed.")
        print(DISCLAIMER)
    return 0 if any(r and r["status"] == "triggered" for _, r, _ in rows) else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="Entry Timer (trader-entry, NSE India): watches a setup bar by bar.")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--setup", choices=["orb", "level", "20d"], default="orb")
        p.add_argument("--or-minutes", type=int, default=15, help="opening range length in minutes (default 15: 09:15-09:30)")
        p.add_argument("--interval", choices=["5m", "1m", "1d"], default="5m")
        p.add_argument("--level", type=float, help="the price in ₹ to close above (--setup level or --swing)")
        p.add_argument("--min-volume-x", type=float, help="trigger bar volume vs the range/20d average")
        p.add_argument("--side", default="buy", choices=["buy", "long", "sell", "short"], help="v2: buy only")
        p.add_argument("--intraday", action="store_true", help="force intraday mode (MIS, 5m/1m bars)")
        p.add_argument("--swing", action="store_true", help="force swing mode (CNC delivery, 1d bars)")
        p.add_argument("--now", help='pretend it is this IST time, e.g. "2026-10-30 10:30"')
        p.add_argument("--offline", metavar="PATH", help="saved Yahoo chart JSON (.json/.json.gz) or a folder")
        p.add_argument("--config", default="trader/config.json")

    p = sub.add_parser("check", help="the setup's status right now")
    p.add_argument("symbol")
    common(p)
    p.add_argument("--write", nargs="?", const="trader/entry.json", metavar="PATH",
                   help="save the result (default trader/entry.json)")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("watch", help="poll bar by bar until it triggers or expires")
    p.add_argument("symbol")
    common(p)
    p.add_argument("--every", type=float, default=30, help="seconds between polls while Yahoo is behind")
    p.add_argument("--until", help="stop at this IST time (HH:MM)")
    p.add_argument("--out", default="trader/entry.json", help="where a trigger is written")
    p = sub.add_parser("watchlist", help="check every hit in trader/watchlist.json")
    common(p)
    p.add_argument("--watchlist", default="trader/watchlist.json")
    p.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"check": cmd_check, "watch": cmd_watch, "watchlist": cmd_watchlist}[a.cmd](a)
    except EntryError as e:
        print("entry.py: %s" % e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("entry.py: stopped.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
