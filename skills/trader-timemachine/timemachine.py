#!/usr/bin/env python3
"""Time Machine for trader-timemachine (India / NSE). Standard library only.

Replays a planned trade before you risk a rupee: 10,000 simulated runs of the
rest of the session (intraday MIS, 5-minute bars) or over up to
swing_max_hold_days daily bars (swing CNC, ~2y daily history), plus the same
rule on every past session in the data.

    python timemachine.py run                           # trader/plan.json, 10,000 replays, seed 19
    python timemachine.py run --write                   # also save trader/timemachine.json
    python timemachine.py run --until 15:10             # stop each intraday replay at your flat-by time
    python timemachine.py run --swing --write           # replay a swing plan over daily bars (~2y history)
    python timemachine.py run --seed 7 --replays 20000 --json
    python timemachine.py run --symbol SBIN --date 2026-10-29 --entry 961.65 \\
        --stop 958.25 --target 968.45 --entry-time "10:10 AM"
    python timemachine.py chart                         # ASCII fan chart of the replays
    python timemachine.py chart --from trader/timemachine.json

Educational tool, not SEBI-registered investment advice. Trading loses most people money.
"""

import argparse
import datetime
import gzip
import json
import math
import os
import random
import re
import statistics
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
    "stt_delivery_pct": 0.001,
    "stt_intraday_sell_pct": 0.00025,
    "exchange_txn_pct": 0.0000297,
    "sebi_per_crore_inr": 10.0,
    "stamp_delivery_buy_pct": 0.00015,
    "stamp_intraday_buy_pct": 0.00003,
    "gst_pct": 0.18,
    "dp_charge_inr": 15.93,
}

DISCLAIMER = "Educational tool, not SEBI-registered investment advice. Trading loses most people money."
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
PAUSE = 0.5          # seconds between requests: be polite to free endpoints
BACKOFF = 5.0        # seconds to wait once after a 429
HOSTS = ("query2", "query1")
INTERVAL = 300       # 5-minute bars, in seconds
SESSION_BARS = 75    # 09:15 to 15:30 IST in 5-minute bars (375 minutes = 75 bars)
MIN_FULL_BARS = 68   # Yahoo NSE 5m data often omits 1-3 closing bars (forward-filled to 75)
OPEN_MIN = 9 * 60 + 15
CLOSE_MIN = 15 * 60 + 30
MIN_SESSIONS = 20
MIN_SWING_BARS = 40
SAMPLE_PATHS = 160
BAND_PATHS = 2000
BAND_Q = (0.05, 0.25, 0.5, 0.75, 0.95)
HIST_BINS = 24
COIN_FLIP = 0.05
STRONG = 0.15
MANY_NEITHER = 0.40
EPOCH = datetime.datetime(1970, 1, 1)
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


class TMError(Exception):
    pass


# ---------------------------------------------------------------- time & formatting

def to_ist(utc):
    return utc.replace(tzinfo=None) + IST_OFFSET


def ist_to_utc(local):
    return local.replace(tzinfo=None) - IST_OFFSET


def parse_now(text):
    """ISO time -> naive UTC. A time without an offset is taken as IST."""
    if not text:
        return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    t = text.strip()
    for suffix in (" IST", " ET"):
        if t.upper().endswith(suffix):
            t = t[:-len(suffix)].strip()
    t = t.replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise TMError('--now wants a time like "2026-10-30 10:30" (IST) or ...Z (UTC)')
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def utc_of(ts):
    return EPOCH + datetime.timedelta(seconds=int(ts))


def parse_clock(text, what):
    """'10:15 AM', '10:15am', '15:10' or '3:10' (a bare 1 to 8 o'clock means p.m.) -> minutes after midnight."""
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*(?:([AaPp])\.?\s*[Mm]\.?)?\s*$", str(text or ""))
    if not m:
        raise TMError("%s wants an IST time like 10:15 AM or 15:10, not %r" % (what, text))
    h, mi, ap = int(m.group(1)), int(m.group(2)), (m.group(3) or "").lower()
    if mi > 59 or h > 23 or (ap and not 1 <= h <= 12):
        raise TMError("%s %r is not a clock time" % (what, text))
    if ap == "p" and h != 12:
        h += 12
    elif ap == "a" and h == 12:
        h = 0
    elif not ap and 1 <= h <= 8:
        h += 12
    return h * 60 + mi


def clock12(minutes):
    h, m = divmod(minutes, 60)
    return "%d:%02d %s" % (h % 12 or 12, m, "AM" if h < 12 else "PM")


def clock24(minutes):
    return "%02d:%02d" % divmod(minutes, 60)


def plain_symbol(sym):
    s = str(sym or "").strip().upper()
    if s.endswith(".NS") or s.endswith(".BO"):
        return s[:-3]
    return s


def yahoo_symbol(sym):
    s = str(sym or "").strip().upper()
    if not s:
        raise TMError("symbol cannot be empty")
    if s.startswith("^") or s.endswith(".NS") or s.endswith(".BO"):
        return s
    return s + ".NS"


def format_inr(x):
    """Format float as ₹1,23,456.75 (Indian grouping)."""
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
    return "%s₹%s.%s" % ("-" if neg else "", whole, dec)


def tick_away(x, long):
    """Round a stop to the ₹0.05 tick, away from the entry: down for a buy, up for a sell."""
    steps = round(x * 20.0, 4)
    return round((math.floor(steps) if long else math.ceil(steps)) / 20.0, 2)


def round_tick(x):
    """Round to nearest ₹0.05 tick."""
    return round(round(float(x) * 20.0) / 20.0, 2)


# ---------------------------------------------------------------- data

def chart_url(sym, host="query2", rng="60d", interval="5m"):
    ysym = urllib.parse.quote(yahoo_symbol(sym), safe="")
    return "https://%s.finance.yahoo.com/v8/finance/chart/%s?range=%s&interval=%s" % (host, ysym, rng, interval)


def _sleep(seconds):
    time.sleep(seconds)


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def fetch_chart(sym, rng="60d", interval="5m"):
    """One request for 60d of 5m bars (intraday) or 2y of 1d bars (swing)."""
    psym = plain_symbol(sym)
    last = None
    for n, host in enumerate(HOSTS):
        if n:
            _sleep(PAUSE)
        for attempt in (0, 1):
            url = chart_url(sym, host, rng=rng, interval=interval)
            try:
                return _get(url)
            except urllib.error.HTTPError as e:
                last = e.code
                try:
                    e.close()
                except Exception:
                    pass
                if e.code == 429 and attempt == 0:
                    _sleep(BACKOFF)
                    continue
                if e.code == 429 or e.code >= 500:
                    break
                if e.code == 404:
                    raise TMError("Yahoo has no chart for %s (HTTP 404): check the ticker" % psym)
                raise TMError("Yahoo answered HTTP %d for %s" % (e.code, psym))
            except urllib.error.URLError as e:
                raise TMError("could not reach Yahoo: %s" % e.reason)
            except OSError as e:
                raise TMError("could not reach Yahoo: %s" % e)
    if last == 429:
        raise TMError("Yahoo is rate limiting (HTTP 429). Wait a minute and run it again.")
    raise TMError("Yahoo answered HTTP %s for %s on both hosts. Try again later." % (last, psym))


def read_offline(path, sym):
    """A saved Yahoo chart file (.json or .json.gz), or a folder holding <SYM>-60d5m.json(.gz)."""
    psym = plain_symbol(sym)
    ysym = yahoo_symbol(sym)
    if os.path.isdir(path):
        cands = (
            "%s-60d5m.json" % psym, "%s-60d5m.json.gz" % psym,
            "%s-60d5m.json" % ysym, "%s-60d5m.json.gz" % ysym,
            "%s.json" % psym, "%s.json.gz" % psym,
            "%s.json" % ysym, "%s.json.gz" % ysym,
        )
        for name in cands:
            if os.path.exists(os.path.join(path, name)):
                path = os.path.join(path, name)
                break
        else:
            raise TMError("no offline chart file for %s in %s" % (psym, path))
    if not os.path.exists(path):
        raise TMError("no offline chart file %s" % path)
    opener = gzip.open if path.endswith(".gz") else open
    try:
        with opener(path, "rt", encoding="utf-8") as fh:
            return fh.read()
    except OSError as e:
        raise TMError("could not read %s: %s" % (path, e))


def chart_result(text, sym):
    psym = plain_symbol(sym)
    try:
        data = json.loads(text)
    except ValueError:
        raise TMError("Yahoo returned something that is not JSON for %s" % psym)
    chart = data.get("chart") if isinstance(data, dict) else None
    if not isinstance(chart, dict):
        raise TMError("Yahoo returned something unexpected for %s" % psym)
    res = chart.get("result")
    if not res:
        err = chart.get("error") or {}
        raise TMError("Yahoo has no data for %s: %s" % (psym, err.get("description") or "unknown ticker"))
    return res[0]


def parse_bars(result, now_utc):
    """Yahoo chart result -> (meta, bars, dropped). Regular-session 5-min NSE bars (09:15-15:30 IST)."""
    meta = result.get("meta") or {}
    stamps = result.get("timestamp") or []
    q = ((result.get("indicators") or {}).get("quote") or [{}])[0] or {}
    cols = [q.get(k) or [] for k in ("open", "high", "low", "close", "volume")]
    as_of = now_utc
    rmt = meta.get("regularMarketTime")
    if isinstance(rmt, (int, float)) and utc_of(rmt) < as_of:
        as_of = utc_of(rmt)
    dropped = {"no_price": 0, "off_grid": 0, "not_complete": 0, "outside_hours": 0}
    bars = {}
    for i, ts in enumerate(stamps):
        o, h, l, c, v = [col[i] if i < len(col) else None for col in cols]
        if ts is None or None in (o, h, l, c):
            dropped["no_price"] += 1
            continue
        ts = int(ts)
        if ts % INTERVAL:
            dropped["off_grid"] += 1
            continue
        start = utc_of(ts)
        local = to_ist(start)
        mins = local.hour * 60 + local.minute - OPEN_MIN
        if mins < 0 or mins >= SESSION_BARS * 5:
            dropped["outside_hours"] += 1
            continue
        if start + datetime.timedelta(seconds=INTERVAL) > as_of:
            dropped["not_complete"] += 1
            continue
        bars[ts] = {"date": local.date().isoformat(), "slot": mins // 5, "start": local.strftime("%H:%M"),
                    "o": float(o), "h": float(h), "l": float(l), "c": float(c), "v": int(v or 0)}
    return meta, [bars[t] for t in sorted(bars)], dropped


def parse_daily_bars(result, now_utc):
    """Yahoo 1d chart result -> (meta, bars, dropped). One daily bar per NSE session."""
    meta = result.get("meta") or {}
    stamps = result.get("timestamp") or []
    q = ((result.get("indicators") or {}).get("quote") or [{}])[0] or {}
    cols = [q.get(k) or [] for k in ("open", "high", "low", "close", "volume")]
    dropped = {"no_price": 0}
    by_date = {}
    for i, ts in enumerate(stamps):
        o, h, l, c, v = [col[i] if i < len(col) else None for col in cols]
        if ts is None or None in (o, h, l, c):
            dropped["no_price"] += 1
            continue
        local = to_ist(utc_of(int(ts)))
        ds = local.date().isoformat()
        by_date[ds] = {"date": ds, "o": float(o), "h": float(h), "l": float(l), "c": float(c), "v": int(v or 0)}
    return meta, [by_date[d] for d in sorted(by_date)], dropped


def split_sessions(bars):
    """{date: {slot: bar}} with forward-fill for isolated missing Yahoo NSE slots."""
    days = {}
    for b in bars:
        days.setdefault(b["date"], {})[b["slot"]] = b
    return days


def fill_session_slots(s, date_str, upto_slot=None):
    """Return a dict {slot: bar} with any missing internal slots forward-filled from the previous slot's close."""
    if not s or 0 not in s:
        return dict(s)
    out = dict(s)
    max_s = upto_slot if upto_slot is not None else (SESSION_BARS - 1)
    for i in range(1, max_s + 1):
        if i not in out and (i - 1) in out:
            pc = out[i - 1]["c"]
            out[i] = {"date": date_str, "slot": i, "start": clock24(OPEN_MIN + 5 * i),
                      "o": pc, "h": pc, "l": pc, "c": pc, "v": 0}
    return out


def full_sessions(days, before):
    """Full sessions (>= MIN_FULL_BARS of 75 bars, forward-filled to 75) strictly before the plan's date."""
    full, short = [], []
    for d in sorted(days):
        if d >= before:
            continue
        s = days[d]
        if len(s) >= MIN_FULL_BARS and 0 in s:
            filled = fill_session_slots(s, d, SESSION_BARS - 1)
            full.append((d, [filled[i] for i in range(SESSION_BARS)]))
        else:
            short.append({"date": d, "bars": len(s)})
    return full, short


# ---------------------------------------------------------------- the simulation (intraday & swing)

def build_pool(sessions):
    """pool[i] = every past session's bar i as (o, h, l, c) / bar i-1's close, in date order."""
    pool = [[] for _ in range(SESSION_BARS)]
    for _, D in sessions:
        for i in range(1, SESSION_BARS):
            pc = D[i - 1]["c"]
            pool[i].append((D[i]["o"] / pc, D[i]["h"] / pc, D[i]["l"] / pc, D[i]["c"] / pc))
    return pool


def pct(a, q):
    return a[min(len(a) - 1, int(q * len(a)))]


def simulate(pool, entry, stop, target, k, last, replays=10000, seed=19, window=2, side="buy"):
    """Monte Carlo replays of bars k+1..last."""
    long = side == "buy"
    risk = (entry - stop) if long else (stop - entry)
    r_target = round(((target - entry) if long else (entry - target)) / risk, 4)

    def slot(rng, i):
        return i if window == 0 else min(SESSION_BARS - 1, max(1, i + rng.randint(-window, window)))

    rng = random.Random(seed)
    choice = rng.choice
    outcomes, closes, paths = [], [], []
    for n in range(replays):
        p = entry
        outcome = None
        keep = n < SAMPLE_PATHS
        path = [entry]
        for i in range(k + 1, last + 1):
            o, h, l, c = choice(pool[slot(rng, i)])
            hi, lo, cl = p * h, p * l, p * c
            if outcome is None:
                if long:
                    if lo <= stop:
                        outcome = "stop"
                    elif hi >= target:
                        outcome = "target"
                else:
                    if hi >= stop:
                        outcome = "stop"
                    elif lo <= target:
                        outcome = "target"
            p = cl
            if keep:
                path.append(round(p, 2))
        outcomes.append(outcome or "close")
        closes.append(p)
        if keep:
            paths.append(path)
    cnt = {x: outcomes.count(x) for x in ("target", "stop", "close")}
    green = sum(1 for o, p in zip(outcomes, closes)
                if o == "target" or (o == "close" and (p > entry if long else p < entry)))
    Rs = [r_target if o == "target" else -1.0 if o == "stop" else ((p - entry) if long else (entry - p)) / risk
          for o, p in zip(outcomes, closes)]
    srt = sorted(closes)
    rng = random.Random(seed)
    choice = rng.choice
    allp = []
    for n in range(BAND_PATHS):
        p = entry
        path = [entry]
        for i in range(k + 1, last + 1):
            o, h, l, c = choice(pool[slot(rng, i)])
            p = p * c
            path.append(p)
        allp.append(path)
    bands = []
    for j in range(last - k + 1):
        col = sorted(pp[j] for pp in allp)
        bands.append([round(pct(col, q), 2) for q in BAND_Q])
    lo_, hi_ = pct(srt, 0.01), pct(srt, 0.99)
    W = (hi_ - lo_) / HIST_BINS
    bins = [0] * HIST_BINS
    for c in srt:
        b = int((c - lo_) / W) if W > 0 else 0
        if 0 <= b < HIST_BINS:
            bins[b] += 1
    return {
        "replays": replays, "seed": seed, "window": window, "bars_left": last - k,
        "p_target": round(cnt["target"] / replays, 4), "p_stop": round(cnt["stop"] / replays, 4),
        "p_neither": round(cnt["close"] / replays, 4), "p_green": round(green / replays, 4),
        "median_close": round(pct(srt, 0.5), 2), "p5_close": round(pct(srt, 0.05), 2),
        "p95_close": round(pct(srt, 0.95), 2), "avg_R": round(statistics.mean(Rs), 3),
        "r_at_target": r_target, "band_paths": BAND_PATHS, "bands": bands, "sample_paths": paths,
        "hist": {"lo": round(lo_, 2), "hi": round(hi_, 2), "bins": bins},
    }


def simulate_swing(daily_ratios, entry, stop, target, hold_days=10, replays=10000, seed=19, side="buy"):
    """Monte Carlo replays over up to `hold_days` daily bars."""
    long = side == "buy"
    risk = (entry - stop) if long else (stop - entry)
    r_target = round(((target - entry) if long else (entry - target)) / risk, 4)
    rng = random.Random(seed)
    choice = rng.choice
    outcomes, closes, paths = [], [], []
    for n in range(replays):
        p = entry
        outcome = None
        keep = n < SAMPLE_PATHS
        path = [entry]
        for _ in range(hold_days):
            o, h, l, c = choice(daily_ratios)
            hi, lo, cl = p * h, p * l, p * c
            if outcome is None:
                if long:
                    if lo <= stop:
                        outcome = "stop"
                    elif hi >= target:
                        outcome = "target"
                else:
                    if hi >= stop:
                        outcome = "stop"
                    elif lo <= target:
                        outcome = "target"
            p = cl
            if keep:
                path.append(round(p, 2))
        outcomes.append(outcome or "close")
        closes.append(p)
        if keep:
            paths.append(path)
    cnt = {x: outcomes.count(x) for x in ("target", "stop", "close")}
    green = sum(1 for o, p in zip(outcomes, closes)
                if o == "target" or (o == "close" and (p > entry if long else p < entry)))
    Rs = [r_target if o == "target" else -1.0 if o == "stop" else ((p - entry) if long else (entry - p)) / risk
          for o, p in zip(outcomes, closes)]
    srt = sorted(closes)
    rng = random.Random(seed)
    choice = rng.choice
    allp = []
    for n in range(BAND_PATHS):
        p = entry
        path = [entry]
        for _ in range(hold_days):
            o, h, l, c = choice(daily_ratios)
            p = p * c
            path.append(p)
        allp.append(path)
    bands = []
    for j in range(hold_days + 1):
        col = sorted(pp[j] for pp in allp)
        bands.append([round(pct(col, q), 2) for q in BAND_Q])
    lo_, hi_ = pct(srt, 0.01), pct(srt, 0.99)
    W = (hi_ - lo_) / HIST_BINS
    bins = [0] * HIST_BINS
    for c in srt:
        b = int((c - lo_) / W) if W > 0 else 0
        if 0 <= b < HIST_BINS:
            bins[b] += 1
    return {
        "replays": replays, "seed": seed, "window": 0, "bars_left": hold_days, "hold_days": hold_days,
        "p_target": round(cnt["target"] / replays, 4), "p_stop": round(cnt["stop"] / replays, 4),
        "p_neither": round(cnt["close"] / replays, 4), "p_green": round(green / replays, 4),
        "median_close": round(pct(srt, 0.5), 2), "p5_close": round(pct(srt, 0.05), 2),
        "p95_close": round(pct(srt, 0.95), 2), "avg_R": round(statistics.mean(Rs), 3),
        "r_at_target": r_target, "band_paths": BAND_PATHS, "bands": bands, "sample_paths": paths,
        "hist": {"lo": round(lo_, 2), "hi": round(hi_, 2), "bins": bins},
    }


# ---------------------------------------------------------------- stop rules and history replay

def opening_range(D, n):
    """(high, low) of the first n bars of a session given as {slot: bar} or a list."""
    rows = [D[i] for i in range(n)]
    return max(b["h"] for b in rows), min(b["l"] for b in rows)


def rule_stop(rule, oh, ol, entry, long):
    r = rule["stop_rule"]
    if r == "or_mid":
        return tick_away((oh + ol) / 2, long)
    if r in ("or_low", "or_high"):
        return tick_away(ol if long else oh, long)
    frac = rule["stop_pct"] / 100.0
    return tick_away(entry * (1 - frac) if long else entry * (1 + frac), long)


def history_replay(sessions, rule, last, side="buy"):
    """The plan's rule on every past full session (intraday ORB)."""
    long = side == "buy"
    n = rule["or_minutes"] // 5
    trades = []
    for d, D in sessions:
        oh, ol = opening_range(D, n)
        kk = next((i for i in range(n, last + 1) if (D[i]["c"] > oh if long else D[i]["c"] < ol)), None)
        if kk is None:
            continue
        e = round_tick(D[kk]["c"])
        s = rule_stop(rule, oh, ol, e, long)
        risk = round(e - s, 2) if long else round(s - e, 2)
        if risk <= 0:
            continue
        t = round_tick(e + rule["target_r"] * risk) if long else round_tick(e - rule["target_r"] * risk)
        oc = None
        for b in D[kk + 1:last + 1]:
            if (b["l"] <= s) if long else (b["h"] >= s):
                oc = "stop"
                break
            if (b["h"] >= t) if long else (b["l"] <= t):
                oc = "target"
                break
        cl = D[last]["c"]
        R = ((t - e) if long else (e - t)) / risk if oc == "target" else -1.0 if oc == "stop" else \
            ((cl - e) if long else (e - cl)) / risk
        trades.append({"date": d, "time": clock12(OPEN_MIN + 5 * (kk + 1)), "entry": e, "stop": s, "target": t,
                       "outcome": oc or "close", "R": round(R, 2)})
    return {
        "sessions": len(sessions), "triggered": len(trades),
        "target": sum(1 for x in trades if x["outcome"] == "target"),
        "stop": sum(1 for x in trades if x["outcome"] == "stop"),
        "close": sum(1 for x in trades if x["outcome"] == "close"),
        "green": sum(1 for x in trades if x["R"] > 0),
        "avg_R": round(statistics.mean(x["R"] for x in trades), 3) if trades else None,
        "rule": rule, "trades": trades,
    }


def history_replay_swing(pool_bars, plan, hold_days=10):
    """Re-run a 20-day high daily breakout with the plan's stop rule and target R across ~2y daily history."""
    long = plan["side"] == "buy"
    stop_rule = plan.get("stop_rule") or "atr"
    stop_pct = round(100.0 * plan["risk_per_share"] / plan["entry"], 4)
    target_r = plan["target_r"]
    rule = {
        "setup": "20d",
        "lookback_days": 20,
        "hold_days": hold_days,
        "stop_rule": stop_rule if stop_rule in ("atr", "swing_low") else "pct",
        "target_r": target_r,
        "stop_pct": stop_pct,
    }
    trades = []
    for idx in range(20, len(pool_bars) - hold_days):
        prior20 = pool_bars[idx - 20:idx]
        h20 = max(b["h"] for b in prior20)
        l20 = min(b["l"] for b in prior20)
        cur = pool_bars[idx]
        cond = (cur["c"] > h20) if long else (cur["c"] < l20)
        if not cond:
            continue
        e = round_tick(cur["c"])
        if stop_rule == "swing_low" and long:
            low10 = min(b["l"] for b in pool_bars[max(0, idx - 9):idx + 1])
            if low10 >= e:
                low10 = min(b["l"] for b in pool_bars[max(0, idx - 10):idx])
            s = tick_away(low10, True)
        elif stop_rule == "atr":
            trs = []
            for j in range(max(1, idx - 13), idx + 1):
                b = pool_bars[j]
                pc = pool_bars[j - 1]["c"]
                trs.append(max(b["h"] - b["l"], abs(b["h"] - pc), abs(b["l"] - pc)))
            atr_val = sum(trs) / len(trs) if trs else (e * stop_pct / 100.0)
            s = tick_away(e - 1.5 * atr_val if long else e + 1.5 * atr_val, long)
        else:
            frac = stop_pct / 100.0
            s = tick_away(e * (1 - frac) if long else e * (1 + frac), long)
        risk = round(e - s, 2) if long else round(s - e, 2)
        if risk <= 0:
            continue
        t = round_tick(e + target_r * risk) if long else round_tick(e - target_r * risk)
        oc = None
        for b in pool_bars[idx + 1: idx + 1 + hold_days]:
            if (b["l"] <= s) if long else (b["h"] >= s):
                oc = "stop"
                break
            if (b["h"] >= t) if long else (b["l"] <= t):
                oc = "target"
                break
        cl = pool_bars[idx + hold_days]["c"]
        R = ((t - e) if long else (e - t)) / risk if oc == "target" else -1.0 if oc == "stop" else \
            ((cl - e) if long else (e - cl)) / risk
        trades.append({"date": cur["date"], "time": "15:30 IST", "entry": e, "stop": s, "target": t,
                       "outcome": oc or "close", "R": round(R, 2)})
    return {
        "sessions": len(pool_bars), "triggered": len(trades),
        "target": sum(1 for x in trades if x["outcome"] == "target"),
        "stop": sum(1 for x in trades if x["outcome"] == "stop"),
        "close": sum(1 for x in trades if x["outcome"] == "close"),
        "green": sum(1 for x in trades if x["R"] > 0),
        "avg_R": round(statistics.mean(x["R"] for x in trades), 3) if trades else None,
        "rule": rule, "trades": trades,
    }


# ---------------------------------------------------------------- the plan

def _num(v, what):
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise TMError("the plan needs a number for %s, got %r" % (what, v))
    if not x > 0 or math.isinf(x):
        raise TMError("the plan's %s must be a positive price, got %r" % (what, v))
    return x


def read_config(path="trader/config.json"):
    cfg = {"trade_type": "intraday", "product": "MIS", "swing_max_hold_days": 10}
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
            for k in cfg:
                if doc.get(k) is not None:
                    cfg[k] = doc[k]
        except Exception:
            pass
    return cfg


def load_plan(a, now_utc):
    """The plan from --plan (trader/plan.json) or from the flags, checked and normalized."""
    cfg = read_config(getattr(a, "config", "trader/config.json"))
    if getattr(a, "swing", False) and getattr(a, "intraday", False):
        raise TMError("use --swing or --intraday, not both")
    if a.symbol:
        trade_type = "swing" if getattr(a, "swing", False) else (
            "intraday" if getattr(a, "intraday", False) else str(cfg.get("trade_type") or "intraday"))
        req_fields = ("entry", "stop", "target") if trade_type == "swing" else ("entry", "stop", "target", "entry_time")
        missing = [f for f in req_fields if getattr(a, f) is None]
        if missing:
            raise TMError("with --symbol also give %s" % ", ".join("--" + m.replace("_", "-") for m in missing))
        raw = {
            "symbol": plain_symbol(a.symbol),
            "yahoo": yahoo_symbol(a.symbol),
            "trade_type": trade_type,
            "product": "CNC" if trade_type == "swing" else "MIS",
            "side": a.side,
            "date": a.date or to_ist(now_utc).date().isoformat(),
            "entry": a.entry,
            "stop": a.stop,
            "target": a.target,
            "entry_time": a.entry_time or ("3:30 PM" if trade_type == "swing" else None),
            "setup": a.setup if a.setup != "orb" or trade_type == "intraday" else "20d",
            "opening_range": {"minutes": a.or_minutes} if trade_type == "intraday" else None,
            "stop_rule": a.stop_rule,
            "interval": "1d" if trade_type == "swing" else "5m",
            "max_hold_days": a.hold_days or int(cfg.get("swing_max_hold_days") or 10),
        }
        src = "flags"
    else:
        if not os.path.exists(a.plan):
            raise TMError("no plan at %s: run /trader-exit first, or pass --symbol --date --entry --stop "
                          "--target --entry-time" % a.plan)
        try:
            with open(a.plan, encoding="utf-8") as fh:
                raw = json.load(fh)
        except ValueError as e:
            raise TMError("%s is not valid JSON: %s" % (a.plan, e))
        if not isinstance(raw, dict):
            raise TMError("%s is not a plan" % a.plan)
        if getattr(a, "swing", False):
            raw = dict(raw, trade_type="swing", product="CNC", interval="1d")
        elif getattr(a, "intraday", False):
            raw = dict(raw, trade_type="intraday", product="MIS", interval="5m")
        if getattr(a, "hold_days", None):
            raw = dict(raw, max_hold_days=a.hold_days)
        elif not raw.get("max_hold_days"):
            raw = dict(raw, max_hold_days=int(cfg.get("swing_max_hold_days") or 10))
        src = a.plan
    return normalize_plan(raw, src)


def normalize_plan(raw, src="flags"):
    """A plan dict (trader/plan.json shape) -> the checked plan the time machine runs."""
    sym = plain_symbol(raw.get("symbol") or "")
    if not re.match(r"^[A-Z^][A-Z0-9.&_\-]{0,19}$", sym):
        raise TMError("the plan needs a ticker symbol, got %r" % raw.get("symbol"))
    side = str(raw.get("side") or "buy").lower()
    if side not in ("buy", "sell"):
        raise TMError("side must be buy or sell, got %r" % raw.get("side"))
    trade_type = str(raw.get("trade_type") or ("swing" if raw.get("interval") == "1d" else "intraday")).lower()
    if trade_type not in ("intraday", "swing"):
        trade_type = "intraday"
    product = str(raw.get("product") or ("CNC" if trade_type == "swing" else "MIS")).upper()
    interval = "1d" if trade_type == "swing" else str(raw.get("interval") or "5m")
    if trade_type == "intraday" and interval != "5m":
        raise TMError("5-minute bars only for intraday (the plan says %s): Yahoo keeps 60 days of 5-min bars "
                      "but only about 7 days of 1-min bars, too few sessions for a fair replay pool" % interval)
    try:
        date = datetime.date.fromisoformat(str(raw.get("date") or "")).isoformat()
    except ValueError:
        raise TMError("the plan needs a date like 2026-10-30, got %r" % raw.get("date"))
    entry, stop, target = (_num(raw.get(k), k) for k in ("entry", "stop", "target"))
    long = side == "buy"
    if long and not stop < entry < target:
        raise TMError("a buy needs stop < entry < target (got stop %s, entry %s, target %s)" % (stop, entry, target))
    if not long and not target < entry < stop:
        raise TMError("a sell needs target < entry < stop (got target %s, entry %s, stop %s)" % (target, entry, stop))

    risk = (entry - stop) if long else (stop - entry)
    if trade_type == "swing":
        hold_days = int(raw.get("max_hold_days") or 10)
        if not 1 <= hold_days <= 120:
            raise TMError("max_hold_days must be between 1 and 120 (got %s)" % hold_days)
        return {
            "symbol": sym,
            "yahoo": raw.get("yahoo") or yahoo_symbol(sym),
            "trade_type": "swing",
            "product": product,
            "side": side,
            "date": date,
            "setup": str(raw.get("setup") or "20d"),
            "entry": entry,
            "entry_time": str(raw.get("entry_time") or "3:30 PM"),
            "entry_bar": raw.get("entry_bar"),
            "stop": stop,
            "target": target,
            "risk_per_share": round(risk, 2),
            "target_r": round(((target - entry) if long else (entry - target)) / risk, 2),
            "opening_range": None,
            "stop_rule": str(raw.get("stop_rule") or "atr"),
            "interval": "1d",
            "flat_by": None,
            "max_hold_days": hold_days,
            "from": src,
        }

    k = None
    if raw.get("entry_time"):
        close_min = parse_clock(raw["entry_time"], "entry_time")
        if (close_min - OPEN_MIN) % 5:
            raise TMError("entry_time %s is not on the 5-minute NSE grid (09:15 open; use the bar's close, e.g. 9:35 AM)"
                          % raw["entry_time"])
        k = (close_min - OPEN_MIN) // 5 - 1
    if raw.get("entry_bar") is not None:
        try:
            kb = int(raw["entry_bar"])
        except (TypeError, ValueError):
            raise TMError("entry_bar must be a bar number, got %r" % raw["entry_bar"])
        if k is not None and kb != k:
            raise TMError("the plan disagrees with itself: entry_bar %d but entry_time %s is bar %d"
                          % (kb, raw["entry_time"], k))
        k = kb
    if k is None:
        raise TMError("the plan needs entry_time (the entry bar's close, e.g. 10:15 AM) or entry_bar")
    if not 0 <= k <= SESSION_BARS - 2:
        raise TMError("the entry must close between 9:20 AM and 3:25 PM IST to leave bars to replay "
                      "(got %s)" % clock12(OPEN_MIN + 5 * (k + 1)))
    orng = raw.get("opening_range") or {}
    or_min = orng.get("minutes", 15) if isinstance(orng, dict) else 15
    try:
        or_min = int(or_min or 15)
    except (TypeError, ValueError):
        raise TMError("opening_range.minutes must be a number")
    if or_min < 5 or or_min % 5 or or_min >= 375:
        raise TMError("opening_range.minutes must be a multiple of 5 (got %s)" % or_min)
    return {
        "symbol": sym,
        "yahoo": raw.get("yahoo") or yahoo_symbol(sym),
        "trade_type": "intraday",
        "product": product,
        "side": side,
        "date": date,
        "setup": str(raw.get("setup") or "orb"),
        "entry": entry,
        "entry_time": clock12(OPEN_MIN + 5 * (k + 1)),
        "entry_bar": k,
        "stop": stop,
        "target": target,
        "risk_per_share": round(risk, 2),
        "target_r": round(((target - entry) if long else (entry - target)) / risk, 2),
        "opening_range": dict(orng, minutes=or_min) if isinstance(orng, dict) else {"minutes": or_min},
        "stop_rule": str(raw.get("stop_rule") or "auto"),
        "interval": "5m",
        "flat_by": raw.get("flat_by"),
        "from": src,
    }


def resolve_rule(plan, entry_day):
    """The rule the intraday history replay uses."""
    if plan["setup"].lower() != "orb":
        return None
    long = plan["side"] == "buy"
    n = plan["opening_range"]["minutes"] // 5
    rule = {"setup": "orb", "or_minutes": plan["opening_range"]["minutes"], "stop_rule": plan["stop_rule"],
            "target_r": plan["target_r"],
            "stop_pct": round(100.0 * plan["risk_per_share"] / plan["entry"], 4)}
    far = "or_low" if long else "or_high"
    if rule["stop_rule"] == "auto":
        rule["stop_rule"] = "pct"
        if all(i in entry_day for i in range(n)):
            oh, ol = opening_range(entry_day, n)
            if abs(tick_away((oh + ol) / 2, long) - plan["stop"]) < 0.03:
                rule["stop_rule"] = "or_mid"
            elif abs(tick_away(ol if long else oh, long) - plan["stop"]) < 0.03:
                rule["stop_rule"] = far
    elif rule["stop_rule"] in ("or_low", "or_high") and rule["stop_rule"] != far:
        rule["stop_rule"] = "pct"
    elif rule["stop_rule"] not in ("or_mid", far):
        rule["stop_rule"] = "pct"
    if rule["stop_rule"] != "pct":
        del rule["stop_pct"]
    return rule


# ---------------------------------------------------------------- the run

def _until_label(mc):
    if mc.get("hold_days"):
        return "day %d" % mc["hold_days"]
    t = mc.get("until") or "15:30"
    return clock12(int(t[:2]) * 60 + int(t[3:]))


def lean(mc):
    pt, ps = mc["p_target"], mc["p_stop"]
    if pt + ps <= 0:
        return None
    r = mc.get("r_at_target") or 1.0
    share, even = pt / (pt + ps), 1.0 / (1.0 + r)
    return share - even, share, even


def verdict(mc):
    pt, ps = mc["p_target"], mc["p_stop"]
    lv = lean(mc)
    ulab = _until_label(mc)
    if lv is None:
        return "No replay reached the target or the stop by %s: the price at %s decides every one." % (ulab, ulab)
    d, share, even = lv
    r = mc.get("r_at_target") or 1.0
    one_r = abs(r - 1.0) < 0.005
    if abs(d) < COIN_FLIP:
        head = "Close to a coin flip" if one_r else "Close to break-even"
    elif abs(d) < STRONG:
        head = "Leans toward the target" if d > 0 else "Leans toward the stop"
    else:
        head = "Strongly toward the target" if d > 0 else "Strongly toward the stop"
    odds = "target first %.1f %% vs stop first %.1f %%" % (100 * pt, 100 * ps)
    if one_r:
        return "%s: %s." % (head, odds)
    return ("%s: at %gR the target has to come first in %.0f %% of the replays that hit one side; "
            "it did in %.0f %% (%s)." % (head, round(r, 2), 100 * even, 100 * share, odds))


def verdict_notes(mc):
    notes = []
    pn = mc.get("p_neither", 0)
    ulab = _until_label(mc)
    if pn > MANY_NEITHER and lean(mc) is not None:
        notes.append("%.1f %% of the replays hit neither by %s; the price at %s decides those."
                     % (100 * pn, ulab, ulab))
    lv = lean(mc)
    avg = mc.get("avg_R")
    if lv and abs(lv[0]) >= COIN_FLIP and avg is not None and avg != 0 and (avg > 0) != (lv[0] > 0):
        notes.append("Average R is %+.3f all the same: the replays that hit neither pull it the other way." % avg)
    return notes


def time_machine_swing(text, plan, now_utc, replays=10000, seed=19, source="yahoo"):
    """Daily-bar Time Machine over up to max_hold_days using ~2y daily history."""
    if not 1 <= replays <= 1000000:
        raise TMError("--replays must be between 1 and 1,000,000")
    sym = plan["symbol"]
    result = chart_result(text, sym)
    meta, bars, dropped = parse_daily_bars(result, now_utc)
    msym = str(meta.get("symbol") or "").upper()
    if msym and plain_symbol(msym) != sym:
        raise TMError("the data is for %s but the plan is for %s" % (msym, sym))
    gran = meta.get("dataGranularity")
    if gran and gran != "1d":
        raise TMError("swing time machine needs 1d daily bars (got %s)" % gran)
    if not bars:
        raise TMError("no daily bars for %s in the data" % sym)

    pool_bars = [b for b in bars if b["date"] < plan["date"]]
    if len(pool_bars) < MIN_SWING_BARS:
        raise TMError("only %d daily bar(s) of %s before %s; swing time machine needs at least %d"
                      % (len(pool_bars), sym, plan["date"], MIN_SWING_BARS))
    daily_ratios = []
    for i in range(1, len(pool_bars)):
        pc = pool_bars[i - 1]["c"]
        if pc > 0:
            daily_ratios.append((pool_bars[i]["o"] / pc, pool_bars[i]["h"] / pc,
                                 pool_bars[i]["l"] / pc, pool_bars[i]["c"] / pc))

    hold_days = int(plan.get("max_hold_days") or 10)
    mc = simulate_swing(daily_ratios, plan["entry"], plan["stop"], plan["target"],
                        hold_days=hold_days, replays=replays, seed=seed, side=plan["side"])
    mc = dict(mc, until="day %d" % hold_days, pool_sessions=len(pool_bars),
              pool_from=pool_bars[0]["date"], pool_to=pool_bars[-1]["date"],
              band_times=["entry"] + ["d+%d" % d for d in range(1, hold_days + 1)])
    history = history_replay_swing(pool_bars, plan, hold_days=hold_days)
    plan_out = {kk: v for kk, v in plan.items() if v is not None}
    last_bar = bars[-1]
    now_ist_str = to_ist(now_utc).strftime("%Y-%m-%d %H:%M:%S")
    return {
        "created_by": "trader-timemachine",
        "created_at": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "now_ist": now_ist_str,
        "source": source,
        "data": {"symbol": msym or plan["yahoo"], "interval": "1d", "range": "2y",
                 "last_bar": "%s IST" % last_bar["date"],
                 "dropped": dropped, "short_sessions_left_out": []},
        "plan": plan_out,
        "mc": mc,
        "history": history,
        "verdict": verdict(mc),
        "verdict_notes": verdict_notes(mc),
        "warnings": [],
        "note": ("A swing simulation built from %s's own last %d daily bars (~2y NSE history). "
                 "It is not a prediction: earnings, news and circuit limits can do things those days never did. "
                 "Same seed, same numbers." % (sym, len(pool_bars))),
    }


def time_machine(text, plan, now_utc, replays=10000, seed=19, window=2, until=None, source="yahoo"):
    """Chart JSON text + a normalized plan -> the timemachine.json document."""
    if plan.get("trade_type") == "swing":
        return time_machine_swing(text, plan, now_utc, replays=replays, seed=seed, source=source)
    if not 1 <= replays <= 1000000:
        raise TMError("--replays must be between 1 and 1,000,000")
    if not 0 <= window <= 10:
        raise TMError("--window must be between 0 and 10 bars")
    sym = plan["symbol"]
    result = chart_result(text, sym)
    meta, bars, dropped = parse_bars(result, now_utc)
    msym = str(meta.get("symbol") or "").upper()
    if msym and plain_symbol(msym) != sym:
        raise TMError("the data is for %s but the plan is for %s" % (msym, sym))
    gran = meta.get("dataGranularity")
    if gran and gran != "5m":
        raise TMError("the data has %s bars; the intraday time machine needs 5-minute bars" % gran)
    if not bars:
        raise TMError("no complete 5-minute bars for %s in the data" % sym)
    k = plan["entry_bar"]
    end_min = CLOSE_MIN
    until_min = end_min if until is None else parse_clock(until, "--until")
    if (until_min - OPEN_MIN) % 5 or until_min > end_min:
        raise TMError("--until must be on the 5-minute grid and no later than %s IST" % clock12(end_min))
    last = (until_min - OPEN_MIN) // 5 - 1
    if last <= k:
        raise TMError("--until %s leaves nothing to replay after the %s entry" % (clock12(until_min),
                                                                                  plan["entry_time"]))
    days = split_sessions(bars)
    raw_entry_day = days.get(plan["date"], {})
    entry_day = fill_session_slots(raw_entry_day, plan["date"], max(raw_entry_day) if raw_entry_day else 0)
    if k not in entry_day:
        reason = ("no bars for that day" if not entry_day else
                  "the data stops at the %s bar" % entry_day[max(entry_day)]["start"])
        raise TMError("the entry is not in the data: no complete %s bar (closing %s) on %s for %s (%s)"
                      % (clock24(OPEN_MIN + 5 * k), plan["entry_time"], plan["date"], sym, reason))
    eb = entry_day[k]
    warn = []
    if not eb["l"] - 0.05 <= plan["entry"] <= eb["h"] + 0.05:
        warn.append("the plan's entry %.2f is outside that bar's range %.2f to %.2f"
                    % (plan["entry"], eb["l"], eb["h"]))
    sessions, short = full_sessions(days, plan["date"])
    if len(sessions) < MIN_SESSIONS:
        raise TMError("only %d full session(s) of %s before %s in the data; the time machine needs at least %d"
                      % (len(sessions), sym, plan["date"], MIN_SESSIONS))
    pool = build_pool(sessions)
    mc = simulate(pool, plan["entry"], plan["stop"], plan["target"], k, last, replays, seed, window, plan["side"])
    mc = dict(mc, until=clock24(until_min), pool_sessions=len(sessions), pool_from=sessions[0][0],
              pool_to=sessions[-1][0],
              band_times=[clock24(OPEN_MIN + 5 * (j + 1)) for j in range(k, last + 1)])
    rule = resolve_rule(plan, entry_day)
    plan_out = {kk: v for kk, v in plan.items() if v is not None}
    if rule:
        plan_out["stop_rule"] = rule["stop_rule"]
        history = history_replay(sessions, rule, last, plan["side"])
    else:
        history = {"sessions": len(sessions), "triggered": 0, "rule": None, "trades": [],
                   "note": "skipped: the intraday history replay knows the opening-range breakout (orb) only, "
                           "this plan's setup is %r" % plan["setup"]}
    last_bar = bars[-1]
    now_ist_str = to_ist(now_utc).strftime("%Y-%m-%d %H:%M:%S")
    return {
        "created_by": "trader-timemachine",
        "created_at": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "now_ist": now_ist_str,
        "source": source,
        "data": {"symbol": msym or plan["yahoo"], "interval": "5m", "range": "60d",
                 "last_bar": "%s %s IST" % (last_bar["date"], last_bar["start"]),
                 "dropped": dropped, "short_sessions_left_out": short},
        "plan": plan_out,
        "mc": mc,
        "history": history,
        "verdict": verdict(mc),
        "verdict_notes": verdict_notes(mc),
        "warnings": warn,
        "note": ("A simulation built from %s's own last %d full sessions on NSE. It is not a prediction: earnings, "
                 "news and circuit limits can do things those sessions never did. Same seed, same numbers."
                 % (sym, len(sessions))),
    }


# ---------------------------------------------------------------- output

def pc(x):
    return "%.1f %%" % (100.0 * x)


def report(doc):
    p, m, h = doc["plan"], doc["mc"], doc["history"]
    long = p["side"] == "buy"
    trade_type = p.get("trade_type") or "intraday"
    product = p.get("product") or ("CNC" if trade_type == "swing" else "MIS")
    wd = WEEKDAYS[datetime.date.fromisoformat(p["date"]).weekday()]
    r = p["target_r"]

    if trade_type == "swing":
        hold_days = m.get("hold_days") or p.get("max_hold_days") or 10
        until = "day %d" % hold_days
        lines = [
            "%s %s (swing %s), %s %s: entry %s, stop %s, target %s (%sR, risk %s a share)."
            % (p["symbol"], p["side"], product, wd, p["date"], format_inr(p["entry"]),
               format_inr(p["stop"]), format_inr(p["target"]), ("%g" % r), format_inr(p["risk_per_share"])),
            "%s replays over up to %d trading days, seed %d."
            % ("{:,}".format(m["replays"]), hold_days, m["seed"]),
            "Each day is drawn whole from %s's own daily bars over its last %d trading days (%s to %s)."
            % (p["symbol"], m["pool_sessions"], m["pool_from"], m["pool_to"]),
        ]
    else:
        until = clock12(int(m["until"][:2]) * 60 + int(m["until"][3:])) + " IST"
        lines = [
            "%s %s (intraday %s), %s %s: entry %s at %s IST (bar %d), stop %s, target %s (%sR, risk %s a share)."
            % (p["symbol"], p["side"], product, wd, p["date"], format_inr(p["entry"]), p["entry_time"],
               p["entry_bar"], format_inr(p["stop"]), format_inr(p["target"]), ("%g" % r), format_inr(p["risk_per_share"])),
            "%s replays of the rest of the session, %s to %s (%d five-minute bars), seed %d."
            % ("{:,}".format(m["replays"]), p["entry_time"], until, m["bars_left"], m["seed"]),
            "Each bar is drawn whole from %s's own 5-min bars at the same time of day (+-%d bars) over its last "
            "%d full sessions (%s to %s)." % (p["symbol"], m["window"], m["pool_sessions"], m["pool_from"], m["pool_to"]),
        ]

    lines += [
        "",
        "  target first   %s" % pc(m["p_target"]),
        "  stop first     %s" % pc(m["p_stop"]),
        "  neither        %-8s still open at %s" % (pc(m["p_neither"]), until),
        "  green          %-8s target first, or %s the entry at %s" % (pc(m["p_green"]),
                                                                       "above" if long else "below", until),
        "",
        "  Price at %s: median %s, 90 %% of replays between %s and %s."
        % (until, format_inr(m["median_close"]), format_inr(m["p5_close"]), format_inr(m["p95_close"])),
        "  Average R: %+.3f (R = the %s risk: %sR at the target, -1R at the stop)."
        % (m["avg_R"], format_inr(p["risk_per_share"]), ("+%g" % m["r_at_target"])),
        "",
    ]
    if h.get("rule"):
        ru = h["rule"]
        if trade_type == "swing":
            stop_words = {
                "atr": "1.5x daily ATR(14)",
                "swing_low": "10-day swing low",
            }.get(ru["stop_rule"], "%.2f %% from the entry" % ru.get("stop_pct", 0))
            if h["triggered"]:
                lines.append("History: the same rule (20-day high daily breakout, stop at %s, %gR target, max %d days) "
                             "over those %d trading days fired %d times: %d target, %d stop, %d neither. Average R %+.3f."
                             % (stop_words, ru["target_r"], ru["hold_days"], h["sessions"], h["triggered"],
                                h["target"], h["stop"], h["close"], h["avg_R"]))
            else:
                lines.append("History: the same 20-day breakout rule never fired in those %d trading days." % h["sessions"])
        else:
            stop_words = {"or_mid": "opening-range midpoint", "or_low": "opening-range low",
                          "or_high": "opening-range high"}.get(ru["stop_rule"],
                                                               "%.2f %% from the entry" % ru.get("stop_pct", 0))
            if h["triggered"]:
                lines.append("History: the same rule (%d-min opening range, stop at the %s, %gR target) on each of "
                             "those %d real sessions fired %d times: %d target, %d stop, %d neither. Average R %+.3f."
                             % (ru["or_minutes"], stop_words, ru["target_r"], h["sessions"], h["triggered"],
                                h["target"], h["stop"], h["close"], h["avg_R"]))
            else:
                lines.append("History: the same rule never fired in those %d sessions." % h["sessions"])
    else:
        lines.append("History: " + h.get("note", "skipped."))
    lines += ["", doc["verdict"]] + list(doc.get("verdict_notes") or []) + [""]
    for w in doc.get("warnings") or []:
        lines.append("Note: " + w + ".")
    if doc["data"]["short_sessions_left_out"]:
        lines.append("Left out %d short session(s) (half days or gaps): %s."
                     % (len(doc["data"]["short_sessions_left_out"]),
                        ", ".join(x["date"] for x in doc["data"]["short_sessions_left_out"])))
    lines.append(doc["note"] + " Repeat this run with --seed %d." % m["seed"])
    lines.append(DISCLAIMER)
    return lines


def render_chart(doc, height=17):
    """ASCII fan chart of the bands: one column per 5-min bar (or per day in swing mode)."""
    p, m = doc["plan"], doc["mc"]
    bands, times = m["bands"], m["band_times"]
    lo = min([b[0] for b in bands] + [p["stop"], p["target"]])
    hi = max([b[4] for b in bands] + [p["stop"], p["target"]])
    pad = (hi - lo) * 0.04 or 0.05
    lo, hi = lo - pad, hi + pad
    step = (hi - lo) / (height - 1)

    def row(price):
        return max(0, min(height - 1, int(round((hi - price) / step))))
    grid = [[" "] * len(bands) for _ in range(height)]
    for j, b in enumerate(bands):
        r5, r25, r50, r75, r95 = [row(x) for x in b]
        for r in range(r95, r5 + 1):
            grid[r][j] = "."
        for r in range(r75, r25 + 1):
            grid[r][j] = ":"
        grid[r50][j] = "*"
    marks = {row(p["target"]): "target", row(p["stop"]): "stop"}
    if row(p["entry"]) not in marks:
        marks[row(p["entry"])] = "entry"
    for r in (row(p["target"]), row(p["stop"])):
        grid[r] = ["-" if ch == " " else ch for ch in grid[r]]
    out = ["%s %s, %s replay paths from the %s entry (seed %d)" % (
        p["symbol"], p["date"], "{:,}".format(m.get("band_paths", BAND_PATHS)), p["entry_time"], m["seed"])]
    for r in range(height):
        label = marks.get(r)
        price = {"target": p["target"], "stop": p["stop"], "entry": p["entry"]}.get(label, hi - r * step)
        left = ("%9.2f |" % price) if (label or r in (0, height - 1)) else "          |"
        out.append(left + "".join(grid[r]) + ("  " + label if label else ""))
    axis = [" "] * len(bands)
    for j in sorted({0, len(bands) // 2, len(bands) - 1}):
        t = str(times[j])
        if ":" in t:
            hh, mm = int(t[:2]), t[3:]
            lab = "%d:%s" % (hh % 12 or 12, mm)
        else:
            lab = t
        s = max(0, min(j, len(bands) - len(lab)))
        axis[s:s + len(lab)] = list(lab)
    out.append("          +" + "-" * len(bands))
    out.append("           " + "".join(axis))
    out.append("* median   : middle half of the paths   . 90 % of the paths   - stop and target")
    return out


# ---------------------------------------------------------------- commands

def _write(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, ensure_ascii=False)
        fh.write("\n")


def compute(a):
    now = parse_now(a.now)
    plan = load_plan(a, now)
    if a.offline:
        text, source = read_offline(a.offline, plan["symbol"]), "offline"
    elif plan.get("trade_type") == "swing":
        text, source = fetch_chart(plan["symbol"], rng="2y", interval="1d"), "yahoo"
    else:
        text, source = fetch_chart(plan["symbol"], rng="60d", interval="5m"), "yahoo"
    return time_machine(text, plan, now, a.replays, a.seed, a.window, a.until, source)


def cmd_run(a):
    doc = compute(a)
    if a.write:
        _write(a.write, doc)
    if a.json:
        slim = dict(doc, mc={k: v for k, v in doc["mc"].items() if k != "sample_paths"})
        print(json.dumps(slim, indent=2, ensure_ascii=False))
    else:
        print("\n".join(report(doc)))
        if a.write:
            print("Wrote %s." % a.write)
        print("Next: /trader-budget sizes it to your budget and your max loss per trade.")
    return 0


def cmd_chart(a):
    if a.from_file:
        if not os.path.exists(a.from_file):
            raise TMError("no %s: run timemachine.py run --write first" % a.from_file)
        try:
            with open(a.from_file, encoding="utf-8") as fh:
                doc = json.load(fh)
            doc["mc"]["bands"]
        except (ValueError, KeyError, TypeError):
            raise TMError("%s is not a time machine result" % a.from_file)
    else:
        doc = compute(a)
    print("\n".join(render_chart(doc)))
    print("\n".join([doc.get("verdict", "")] + list(doc.get("verdict_notes") or [])))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Time Machine (trader-timemachine, NSE India): replay a planned trade.")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--plan", default="trader/plan.json", help="the plan from trader-exit")
        p.add_argument("--config", default="trader/config.json")
        p.add_argument("--symbol", help="or give the plan as flags: NSE ticker (e.g. SBIN, RELIANCE)")
        p.add_argument("--side", default="buy", choices=["buy", "sell"])
        p.add_argument("--date", help="the plan's day, YYYY-MM-DD (default today, IST)")
        p.add_argument("--entry", type=float)
        p.add_argument("--stop", type=float)
        p.add_argument("--target", type=float)
        p.add_argument("--entry-time", help='the entry bar\'s close in IST, e.g. "10:15 AM"')
        p.add_argument("--setup", default="orb")
        p.add_argument("--or-minutes", type=int, default=15)
        p.add_argument("--stop-rule", default="auto", choices=["auto", "or_mid", "or_low", "or_high", "pct", "atr", "swing_low"])
        p.add_argument("--intraday", action="store_true", help="force intraday mode (MIS, 60d of 5m bars)")
        p.add_argument("--swing", action="store_true", help="force swing mode (CNC, ~2y of 1d bars)")
        p.add_argument("--hold-days", type=int, help="swing hold horizon in trading days (default config swing_max_hold_days=10)")
        p.add_argument("--replays", type=int, default=10000)
        p.add_argument("--seed", type=int, default=19)
        p.add_argument("--window", type=int, default=2, help="draw from the same time of day +- this many bars (intraday)")
        p.add_argument("--until", help="end each intraday replay at this IST time (default 15:30, the close)")
        p.add_argument("--offline", metavar="FILE", help="a saved Yahoo chart JSON (.json or .json.gz), no network")
        p.add_argument("--now", help='pretend it is this time ("2026-10-30 10:30" IST, or ISO with Z)')

    p = sub.add_parser("run", help="replay the plan: 10,000 simulated runs plus the history")
    common(p)
    p.add_argument("--json", action="store_true")
    p.add_argument("--write", nargs="?", const="trader/timemachine.json", metavar="PATH",
                   help="save the result (default trader/timemachine.json)")
    p = sub.add_parser("chart", help="ASCII fan chart of the replays")
    common(p)
    p.add_argument("--from", dest="from_file", nargs="?", const="trader/timemachine.json", metavar="PATH",
                   help="draw a saved result instead of running again")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"run": cmd_run, "chart": cmd_chart}[a.cmd](a)
    except TMError as e:
        print("timemachine.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
