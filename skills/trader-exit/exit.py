#!/usr/bin/env python3
"""Exit Planner for trader-exit (India / NSE): sets the stop and the target
BEFORE you enter, so you know when to get out. Standard library only. Long side
only. It never places an order.

    python exit.py plan                                     # from trader/entry.json: or-mid stop, 2R target
    python exit.py plan --stop or-mid --target 2R --write   # save trader/plan.json
    python exit.py plan --symbol SBIN --entry 961.65 --or-high 961.50 --or-low 955.00
    python exit.py plan --stop atr:1.5                      # ATR on session bars (intraday) or daily bars (swing)
    python exit.py plan --swing --write                     # swing plan (CNC): daily ATR(14)x1.5 or swing-low stop, 2R target
    python exit.py plan --swing --stop swing-low --write    # 10-day daily swing low stop
    python exit.py show                                     # the current trader/plan.json

Stops (a long's stop rounds DOWN to the ₹0.05 tick, away from the entry):
  or-mid        the opening-range midpoint (the default for an intraday orb entry)
  or-low        the opening-range low
  swing-low[:N] the N-day daily swing low (default 10 days; for swing trades)
  price:X       your price in ₹
  pct:X         X percent under the entry
  atr:K         K times the 14-bar ATR under the entry (intraday bars for MIS,
                daily bars for CNC swing; default stop in swing mode is atr:1.5)

Targets (round half up to the ₹0.05 tick):
  2R (default), 1R, 1.5R ...   entry + R x risk, where risk = entry - stop
  price:X                      your price in ₹

flat_by (default 15:10 IST for intraday MIS, before broker auto square-off;
None for swing CNC delivery, which holds up to swing_max_hold_days).

Educational tool, not SEBI-registered investment advice. Trading loses most people money.
"""

import argparse
import datetime
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_HALF_UP

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
BACKOFF = 5.0
HOSTS = ("query2", "query1")
INTERVALS = {"1m": 60, "5m": 300, "1d": 86400}
DEFAULTS = {
    "flat_by": "15:10",
    "min_reward_risk": 1.5,
    "trade_type": "intraday",
    "product": "MIS",
    "swing_max_hold_days": 10,
}
ATR_BARS = 14
MIN_RISK_PCT = Decimal("0.05")     # risk under 0.05% of the price is inside the spread's noise
TICK = Decimal("0.05")             # NSE equity tick size ₹0.05
CENT = Decimal("0.01")
EPOCH = datetime.datetime(1970, 1, 1)
NOTE = "A stop is a plan, not a guarantee: a gap, circuit limit or fast market can fill past it."

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
OPEN = datetime.time(9, 15)
CLOSE = datetime.time(15, 30)


class PlanError(Exception):
    pass


# ---------------------------------------------------------------- time & symbols

def to_ist(utc):
    return utc.replace(tzinfo=None) + IST_OFFSET


def ist_to_utc(local):
    return local.replace(tzinfo=None) - IST_OFFSET


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)


def utc_of(ts):
    return EPOCH + datetime.timedelta(seconds=int(ts))


def plain_symbol(sym):
    s = str(sym or "").strip().upper()
    if s.endswith(".NS") or s.endswith(".BO"):
        return s[:-3]
    return s


def yahoo_symbol(sym):
    s = str(sym or "").strip().upper()
    if not s:
        raise PlanError("symbol cannot be empty")
    if s.startswith("^") or s.endswith(".NS") or s.endswith(".BO"):
        return s
    return s + ".NS"


def parse_now(text):
    """--now -> naive UTC. A time without an offset is IST."""
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
        raise PlanError('--now wants an IST time like "2026-10-30 10:30" (or ...Z for UTC)')
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def parse_hhmm(text, what):
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", str(text))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise PlanError("%s wants an IST time like 15:10, not %r" % (what, text))
    return datetime.time(int(m.group(1)), int(m.group(2)))


def parse_clock(text, what):
    """10:15 AM, 10:15, 3:05 PM -> time."""
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*([AaPp][Mm])?\s*$", str(text))
    if not m:
        raise PlanError("%s wants a time like 10:15 AM, not %r" % (what, text))
    h, mi = int(m.group(1)), int(m.group(2))
    if m.group(3):
        if not 1 <= h <= 12:
            raise PlanError("%s wants a time like 10:15 AM, not %r" % (what, text))
        h = h % 12 + (12 if m.group(3).upper() == "PM" else 0)
    if h > 23 or mi > 59:
        raise PlanError("%s wants a time like 10:15 AM, not %r" % (what, text))
    return datetime.time(h, mi)


def hm(t):
    return "%02d:%02d" % (t.hour, t.minute)


def ampm(t):
    h = t.hour % 12 or 12
    return "%d:%02d %s" % (h, t.minute, "AM" if t.hour < 12 else "PM")


def session(d):
    """(open_time, close_time) in IST, or None if closed all day."""
    if d.weekday() >= 5 or d.isoformat() in NSE_HOLIDAYS_2026 or d.isoformat() in MUHURAT_2026:
        return None
    return OPEN, CLOSE


# ---------------------------------------------------------------- money math (Decimal, ₹0.05 tick)

def D(x, what="a number"):
    try:
        if isinstance(x, float):
            return Decimal(repr(x))
        s = str(x).strip().lstrip("₹").lstrip("$").replace(",", "")
        return Decimal(s)
    except (InvalidOperation, ValueError):
        raise PlanError("%s is not a number: %r" % (what, x))


def round_tick(x):
    """Round half up to the nearest ₹0.05 tick."""
    steps = (D(x) / TICK).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return (steps * TICK).quantize(CENT, rounding=ROUND_HALF_UP)


def floor_tick(x):
    """A long's stop: round down to the nearest ₹0.05 tick, away from the entry."""
    steps = (D(x) / TICK).quantize(Decimal("1"), rounding=ROUND_FLOOR)
    return (steps * TICK).quantize(CENT, rounding=ROUND_FLOOR)


def money(x):
    """Format Decimal/float as ₹1,23,456.75 (Indian grouping)."""
    val = D(x).quantize(CENT, rounding=ROUND_HALF_UP)
    neg = val < 0
    val = abs(val)
    s = format(val, "f")
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


def num(x):
    """1, 1.5, 2.25: a Decimal without trailing zeros."""
    return format(D(x).normalize(), "f")


def ratio(x):
    """reward:risk for people: 1.0, 1.5, 1.37."""
    s = "%.2f" % float(x)
    return s[:-1] if s.endswith("0") else s


WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}


def parse_stop(text):
    t = str(text).strip().lower()
    if t in ("or-mid", "or_mid", "mid", "or-midpoint"):
        return "or_mid", None
    if t in ("or-low", "or_low"):
        return "or_low", None
    if t in ("swing-low", "swing_low", "low"):
        return "swing_low", Decimal("10")
    m = re.match(r"^(?:swing-low|swing_low):\s*(\d+)$", t)
    if m:
        days = int(m.group(1))
        if days < 2:
            raise PlanError("--stop swing-low:N needs N >= 2")
        return "swing_low", Decimal(days)
    m = re.match(r"^(price|pct|atr):\s*[₹$]?([0-9][0-9,]*\.?[0-9]*)$", t)
    if not m:
        raise PlanError("--stop is or-mid, or-low, swing-low[:N], price:X, pct:X or atr:K (got %r)" % text)
    kind, val = m.group(1), D(m.group(2), "--stop")
    if val <= 0:
        raise PlanError("--stop %s needs a number above 0" % kind)
    if kind == "pct" and val >= 100:
        raise PlanError("--stop pct:X needs X under 100")
    return kind, val


def parse_target(text):
    t = str(text).strip()
    m = re.match(r"^([0-9]*\.?[0-9]+)\s*[Rr]$", t)
    if m:
        r = D(m.group(1), "--target")
        if r <= 0:
            raise PlanError("--target needs an R multiple above 0, like 1R or 2R")
        return "r", r
    m = re.match(r"^price:\s*[₹$]?([0-9][0-9,]*\.?[0-9]*)$", t.lower())
    if m:
        return "price", D(m.group(1), "--target")
    raise PlanError("--target is 2R, 1R, 1.5R ... or price:X (got %r)" % text)


def r_label(r):
    n = int(r) if r == r.to_integral_value() else None
    return "%sR (%s times the risk)" % (num(r), WORDS.get(n, num(r)))


def compute(entry, stop_rule, stop_param, target_rule, target_param, or_high=None, or_low=None,
            atr=None, atr_bars=None, swing_low=None, swing_low_days=None, trade_type="intraday"):
    """The stop, the target and the risk, rounded to ₹0.05 ticks. Raises PlanError with the reason."""
    e = round_tick(entry)
    if e <= 0:
        raise PlanError("the entry price must be above ₹0")
    detail = ""
    if stop_rule in ("or_mid", "or_low"):
        if or_high is None or or_low is None:
            raise PlanError("an %s stop needs the opening range (--or-high and --or-low)" % stop_rule.replace("_", "-"))
        oh, ol = round_tick(or_high), round_tick(or_low)
        if oh <= ol:
            raise PlanError("the opening-range high %s must be above its low %s" % (money(oh), money(ol)))
        if stop_rule == "or_mid":
            raw = (oh + ol) / 2
            label = "opening-range midpoint"
            detail = "(%s + %s) / 2 = ₹%s" % (money(oh), money(ol), num(raw))
        else:
            raw, label = ol, "opening-range low"
    elif stop_rule == "swing_low":
        if swing_low is None:
            raise PlanError("a swing-low stop needs daily bars or swing_range in entry.json")
        raw = D(swing_low)
        n_days = int(swing_low_days or stop_param or 10)
        label = "%d-day daily swing low" % n_days
        detail = "lowest daily low of last %d sessions = %s" % (n_days, money(raw))
    elif stop_rule == "price":
        raw, label = stop_param, "your price"
    elif stop_rule == "pct":
        raw = e * (1 - stop_param / 100)
        label = "%s%% under the entry" % num(stop_param)
        detail = "%s x (1 - %s%%) = ₹%s" % (money(e), num(stop_param), num(raw.quantize(Decimal("0.0001"))))
    elif stop_rule == "atr":
        if atr is None:
            raise PlanError("an atr stop needs the bars")
        a = D(round(atr, 6))
        raw = e - stop_param * a
        unit = "daily" if trade_type == "swing" else "session"
        label = "%s x %s ATR(%d) under the entry" % (num(stop_param), unit, atr_bars)
        detail = "ATR ₹%s over the %d %s bars to the entry%s" % (
            num(a.quantize(Decimal("0.01"))), atr_bars, unit,
            " (fewer than 14 so far)" if atr_bars < ATR_BARS else "")
    else:
        raise PlanError("unknown stop rule %r" % stop_rule)

    stop = floor_tick(raw)
    if stop != D(raw).quantize(CENT, rounding=ROUND_HALF_UP):
        detail = (detail + ", rounded down to ₹0.05 tick" if detail else "₹%s rounded down to ₹0.05 tick" % num(raw))
    if stop >= e:
        raise PlanError("the stop %s (%s) is at or above the entry %s: a long's stop goes under the entry"
                        % (money(stop), label, money(e)))
    if stop <= 0:
        raise PlanError("the stop works out to %s: pick a stop above ₹0" % money(stop))
    risk = (e - stop).quantize(CENT)
    floor_risk = e * MIN_RISK_PCT / 100
    if risk < floor_risk:
        raise PlanError("the stop is %s under the entry, less than 0.05%% of the price (%s on %s): too tight to "
                        "survive the spread, normal wiggle would stop you out. Widen it."
                        % (money(risk), money(floor_risk.quantize(CENT, rounding=ROUND_HALF_UP)), money(e)))
    if target_rule == "r":
        target = round_tick(e + target_param * risk)
        t_label = r_label(target_param)
    else:
        target = round_tick(target_param)
        t_label = None
    if target <= e:
        raise PlanError("the target %s is at or below the entry %s: a long's target goes above it"
                        % (money(target), money(e)))
    rr = ((target - e) / risk).quantize(CENT, rounding=ROUND_HALF_UP)
    if t_label is None:
        t_label = "your price (%sR)" % ratio(rr)
    return {"entry": e, "stop": stop, "stop_label": label, "stop_detail": detail, "target": target,
            "target_r": target_param if target_rule == "r" else rr, "target_label": t_label,
            "risk": risk, "reward_risk": rr}


# ---------------------------------------------------------------- bars for ATR & swing-low

def _open_url(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _sleep(seconds):
    time.sleep(seconds)


def yahoo_get(path):
    """query2 first, query1 on 429 or 5xx; one retry after a pause on 429."""
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
                    raise PlanError("Yahoo has no chart at %s (HTTP 404)" % url.split("?")[0])
                last = "HTTP %d" % code
                if code == 429 and attempt == 0:
                    _sleep(BACKOFF)
                    continue
                if code == 429 or code >= 500:
                    break
                raise PlanError("Yahoo answered HTTP %d for %s" % (code, url.split("?")[0]))
            except urllib.error.URLError as e:
                last = "could not connect: %s" % (e.reason,)
                break
            except OSError as e:
                last = "network error: %s" % (e,)
                break
    raise PlanError("Yahoo did not answer (%s on query2 and query1): wait a minute and try again" % last)


def read_text(path):
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return fh.read()
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def load_chart(sym, interval, day, offline=None):
    psym = plain_symbol(sym)
    ysym = yahoo_symbol(sym)
    if offline:
        path = offline
        if os.path.isdir(offline):
            for cand in (psym + ".json", psym + ".json.gz", ysym + ".json", ysym + ".json.gz"):
                p = os.path.join(offline, cand)
                if os.path.exists(p):
                    path = p
                    break
        if not os.path.exists(path):
            raise PlanError("no offline file %s" % path)
        try:
            text = read_text(path)
        except (OSError, ValueError) as e:
            raise PlanError("could not read %s: %s" % (path, e))
    else:
        back = (to_ist(utc_now()).date() - day).days
        if interval == "1d":
            rng = "6mo" if back <= 90 else "1y" if back <= 300 else "2y"
        else:
            rng = "1d" if back <= 0 else "5d" if back <= 6 else "1mo" if back <= 28 else "60d"
            if interval == "1m" and back > 6:
                raise PlanError("Yahoo keeps 1-minute bars for about a week")
        qsym = urllib.parse.quote(ysym, safe="")
        text = yahoo_get("/v8/finance/chart/%s?range=%s&interval=%s" % (qsym, rng, interval))
    try:
        data = json.loads(text)
        res = data["chart"]["result"]
    except (ValueError, KeyError, TypeError):
        raise PlanError("Yahoo returned something unexpected for %s" % psym)
    if not res:
        err = (data.get("chart") or {}).get("error") or {}
        raise PlanError("Yahoo has no data for %s: %s" % (psym, err.get("description") or "unknown ticker"))
    r = res[0] or {}
    meta = r.get("meta") or {}
    meta_sym = str(meta.get("symbol") or ysym).upper()
    if plain_symbol(meta_sym) != psym and meta_sym != ysym:
        raise PlanError("the chart file holds %s, not %s" % (meta_sym, ysym))
    q = ((r.get("indicators") or {}).get("quote") or [{}])[0] or {}

    def at(key, i):
        arr = q.get(key) or []
        return arr[i] if i < len(arr) else None

    bars = []
    for i, t in enumerate(r.get("timestamp") or []):
        c = at("close", i)
        if t is None or c is None:
            continue
        h, lo = at("high", i), at("low", i)
        bars.append({"t": int(t), "h": float(c if h is None else h), "l": float(c if lo is None else lo),
                     "c": float(c)})
    return {"bars": bars, "data_time": meta.get("regularMarketTime")}


def session_bars(chart, day, interval, upto_slot=None, now_utc=None):
    """Closed, on-grid bars of one IST session, oldest first."""
    step = INTERVALS[interval]
    s = session(day)
    if not s:
        return []
    open_dt = datetime.datetime.combine(day, s[0])
    close_dt = datetime.datetime.combine(day, s[1])
    if now_utc is None and chart.get("data_time"):
        now_utc = utc_of(chart["data_time"])
    local = to_ist(now_utc) if now_utc else None
    out = {}
    for b in chart["bars"]:
        if b["t"] % step:
            continue
        et = to_ist(utc_of(b["t"]))
        if et.date() != day or not (open_dt <= et < close_dt):
            continue
        if local and et + datetime.timedelta(seconds=step) > local:
            continue
        slot = int((et - open_dt).total_seconds()) // step
        if upto_slot is not None and slot > upto_slot:
            continue
        out[slot] = b
    return [out[k] for k in sorted(out)]


def daily_bars_upto(chart, day):
    """Daily bars up to and including `day` in IST, oldest first."""
    by_date = {}
    for b in chart["bars"]:
        d = to_ist(utc_of(b["t"])).date()
        if d <= day:
            by_date[d] = b
    return [by_date[d] for d in sorted(by_date)]


def atr_of(bars, n=ATR_BARS):
    """Average true range of the last n bars (all of them if fewer)."""
    trs, prev = [], None
    for b in bars:
        tr = b["h"] - b["l"]
        if prev is not None:
            tr = max(tr, abs(b["h"] - prev), abs(b["l"] - prev))
        trs.append(tr)
        prev = b["c"]
    use = trs[-n:]
    return sum(use) / len(use), len(use)


# ---------------------------------------------------------------- plan

def read_json(path, what):
    if not path or not os.path.exists(path):
        raise PlanError("no %s at %s" % (what, path))
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as e:
        raise PlanError("%s is not valid JSON: %s" % (path, e))


def read_config(path):
    cfg = dict(DEFAULTS)
    if path and os.path.exists(path):
        doc = read_json(path, "config")
        for k in DEFAULTS:
            if doc.get(k) is not None:
                cfg[k] = doc[k]
    return cfg


def resolve_flat_by(day, explicit, cfg, entry_time, trade_type="intraday"):
    """(HH:MM or None, note). Swing CNC trades have no intraday flat-by."""
    if trade_type == "swing":
        return None, ""
    t = parse_hhmm(explicit or cfg.get("flat_by") or DEFAULTS["flat_by"], "--flat-by" if explicit else "flat_by")
    note = ""
    s = session(day) if day else None
    if s:
        close_dt = datetime.datetime.combine(day, s[1])
        at = datetime.datetime.combine(day, t)
        if at >= close_dt:
            raise PlanError("flat-by %s is at or after the %s NSE close on %s" % (hm(t), ampm(s[1]), day))
    if entry_time and entry_time >= t:
        raise PlanError("the entry at %s is at or after your flat-by %s: there is no time left to hold it"
                        % (ampm(entry_time), hm(t)))
    return hm(t), note


def _resolve_trade_type(a, doc_trade_type, cfg):
    if getattr(a, "swing", False) and getattr(a, "intraday", False):
        raise PlanError("use --swing or --intraday, not both")
    if getattr(a, "swing", False):
        return "swing"
    if getattr(a, "intraday", False):
        return "intraday"
    if getattr(a, "interval", None) == "1d":
        return "swing"
    if doc_trade_type in ("swing", "intraday"):
        return doc_trade_type
    t = str(cfg.get("trade_type") or "intraday").lower()
    return "swing" if t == "swing" else "intraday"


def gather(a, cfg):
    """The entry facts, from trader/entry.json or the flags."""
    if a.symbol and a.from_path:
        raise PlanError("use --from or --symbol, not both")
    if a.side in ("sell", "short"):
        raise PlanError("v2 plans longs only: no stops above a short entry. Plan a buy instead.")
    if a.symbol:
        if a.entry is None:
            raise PlanError("--symbol needs --entry PRICE")
        if (a.or_high is None) != (a.or_low is None):
            raise PlanError("give both --or-high and --or-low, or neither")
        trade_type = _resolve_trade_type(a, None, cfg)
        default_setup = "20d" if trade_type == "swing" else ("orb" if a.or_high is not None else "level")
        default_iv = "1d" if trade_type == "swing" else "5m"
        info = {
            "symbol": plain_symbol(a.symbol),
            "yahoo": yahoo_symbol(a.symbol),
            "trade_type": trade_type,
            "product": "CNC" if trade_type == "swing" else (cfg.get("product") or "MIS"),
            "entry": a.entry,
            "setup": a.setup or default_setup,
            "date": a.date,
            "entry_time": a.time,
            "entry_bar": a.entry_bar,
            "interval": "1d" if trade_type == "swing" else (a.interval or default_iv),
            "or_high": a.or_high,
            "or_low": a.or_low,
            "or_minutes": a.or_minutes,
            "swing_range": None,
            "source": "flags",
        }
    else:
        path = a.from_path or "trader/entry.json"
        doc = read_json(path, "entry (run /trader-entry first, or give --symbol and --entry)")
        if str(doc.get("side") or "buy").lower() not in ("buy", "long"):
            raise PlanError("%s is a %s: v2 plans longs only" % (path, doc.get("side")))
        if doc.get("status") != "triggered" and a.entry is None:
            raise PlanError("%s says %s is %s, not triggered: plan once it triggers, or give --entry PRICE to plan ahead"
                            % (path, doc.get("symbol"), doc.get("status") or "unknown"))
        trade_type = _resolve_trade_type(a, doc.get("trade_type"), cfg)
        orr = doc.get("opening_range") or {}
        sym = plain_symbol(doc.get("symbol") or "")
        info = {
            "symbol": sym,
            "yahoo": doc.get("yahoo") or yahoo_symbol(sym),
            "trade_type": trade_type,
            "product": "CNC" if trade_type == "swing" else (doc.get("product") or cfg.get("product") or "MIS"),
            "entry": a.entry if a.entry is not None else (doc.get("price") or doc.get("level")),
            "setup": a.setup or doc.get("setup") or ("20d" if trade_type == "swing" else "orb"),
            "date": a.date or doc.get("date"),
            "entry_time": a.time or (doc.get("time") if a.entry is None else None),
            "entry_bar": a.entry_bar if a.entry_bar is not None else (doc.get("bar_index") if a.entry is None else None),
            "interval": "1d" if trade_type == "swing" else (a.interval or doc.get("interval") or "5m"),
            "or_high": a.or_high if a.or_high is not None else orr.get("high"),
            "or_low": a.or_low if a.or_low is not None else orr.get("low"),
            "or_minutes": orr.get("minutes") if orr.get("minutes") is not None else a.or_minutes,
            "swing_range": doc.get("swing_range"),
            "source": path,
        }
        if not info["symbol"] or info["entry"] is None:
            raise PlanError("%s has no symbol or price" % path)
    if info["interval"] not in INTERVALS:
        raise PlanError("interval is 5m, 1m, or 1d")
    return info


def build_plan(a, now_utc):
    cfg = read_config(a.config)
    info = gather(a, cfg)
    trade_type = info["trade_type"]
    stop_text = a.stop
    if not stop_text:
        if trade_type == "swing":
            stop_text = "atr:1.5"
        elif info["setup"] != "orb":
            raise PlanError("a %s setup has no opening-range stop to default to: give --stop price:X, pct:X or atr:K"
                            % info["setup"])
        else:
            stop_text = "or-mid"
    stop_rule, stop_param = parse_stop(stop_text)
    target_rule, target_param = parse_target(a.target or "2R")
    day = None
    if info["date"]:
        try:
            day = datetime.date.fromisoformat(str(info["date"]))
        except ValueError:
            raise PlanError("date wants YYYY-MM-DD, not %r" % info["date"])
    else:
        day = to_ist(now_utc).date()
    entry_time = parse_clock(info["entry_time"], "the entry time") if info["entry_time"] else None

    atr = atr_n = None
    swing_low = swing_low_days = None

    if trade_type == "swing" and stop_rule in ("atr", "swing_low"):
        chart = load_chart(info["symbol"], "1d", day, a.offline)
        dbars = daily_bars_upto(chart, day)
        if len(dbars) < 3:
            raise PlanError("a %s stop in swing mode needs at least 3 daily bars of %s up to %s; found %d"
                            % (stop_rule, info["symbol"], day, len(dbars)))
        if stop_rule == "atr":
            atr, atr_n = atr_of(dbars)
        else:
            n_days = int(stop_param or 10)
            # Use prior bars if the latest bar's low is >= entry
            use_bars = dbars[-n_days:]
            cand = min(b["l"] for b in use_bars)
            if cand >= float(info["entry"]) and len(dbars) > 1:
                use_bars = dbars[:-1][-n_days:]
                cand = min(b["l"] for b in use_bars)
            swing_low = cand
            swing_low_days = len(use_bars)
    elif stop_rule == "atr":
        chart = load_chart(info["symbol"], info["interval"], day, a.offline)
        bars = session_bars(chart, day, info["interval"], info["entry_bar"], parse_now(a.now) if a.now else None)
        if len(bars) < 3:
            raise PlanError("an atr stop needs at least 3 closed %s bars of %s on %s by the entry; found %d"
                            % (info["interval"], info["symbol"], day, len(bars)))
        atr, atr_n = atr_of(bars)
    elif stop_rule == "swing_low":
        chart = load_chart(info["symbol"], "1d", day, a.offline)
        dbars = daily_bars_upto(chart, day)
        if len(dbars) < 2:
            raise PlanError("a swing-low stop needs daily bars of %s up to %s" % (info["symbol"], day))
        n_days = int(stop_param or 10)
        use_bars = dbars[-n_days:]
        swing_low = min(b["l"] for b in use_bars)
        swing_low_days = len(use_bars)

    c = compute(info["entry"], stop_rule, stop_param, target_rule, target_param, info["or_high"], info["or_low"],
                atr, atr_n, swing_low, swing_low_days, trade_type=trade_type)
    flat_by, flat_note = resolve_flat_by(day, a.flat_by, cfg, entry_time, trade_type=trade_type)
    orr = None
    if trade_type == "intraday" and info["or_high"] is not None and info["or_low"] is not None:
        orr = {"high": float(round_tick(info["or_high"])), "low": float(round_tick(info["or_low"])),
               "minutes": int(info["or_minutes"] or 15)}
    max_hold_days = int(cfg.get("swing_max_hold_days") or 10) if trade_type == "swing" else None
    now_ist_str = to_ist(now_utc).strftime("%Y-%m-%d %H:%M:%S")
    plan = {
        "created_by": "trader-exit",
        "symbol": info["symbol"],
        "yahoo": info["yahoo"],
        "trade_type": trade_type,
        "product": info["product"],
        "side": "buy",
        "date": day.isoformat(),
        "setup": info["setup"],
        "entry": float(c["entry"]),
        "entry_time": ampm(entry_time) if entry_time else ("3:30 PM" if trade_type == "swing" else None),
        "entry_bar": info["entry_bar"],
        "interval": info["interval"],
        "opening_range": orr,
        "stop": float(c["stop"]),
        "stop_rule": stop_rule,
        "stop_label": c["stop_label"],
        "target": float(c["target"]),
        "target_r": float(c["target_r"]),
        "target_label": c["target_label"],
        "risk_per_share": float(c["risk"]),
        "reward_risk": float(c["reward_risk"]),
        "flat_by": flat_by,
        "max_hold_days": max_hold_days,
        "stop_detail": c["stop_detail"],
        "created_at": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "now_ist": now_ist_str,
        "note": NOTE,
    }
    if atr is not None:
        plan["atr"] = round(atr, 4)
        plan["atr_bars"] = atr_n
    warnings = []
    if flat_note:
        warnings.append("Flat-by %s: %s." % (flat_by, flat_note))
    mrr = cfg.get("min_reward_risk")
    try:
        if mrr is not None and c["reward_risk"] < D(mrr):
            warnings.append("Reward:risk %s is under your min_reward_risk %s in the config: trader-risk checks that."
                            % (ratio(c["reward_risk"]), num(D(mrr))))
    except PlanError:
        pass
    today = to_ist(now_utc).date()
    if day != today:
        warnings.append("This plan is for %s, not today (%s)." % (day.isoformat(), today.isoformat()))
    return plan, warnings


def describe(plan):
    day = datetime.date.fromisoformat(plan["date"])
    trade_type = plan.get("trade_type") or "intraday"
    product = plan.get("product") or ("CNC" if trade_type == "swing" else "MIS")
    setup = {
        "orb": "opening-range breakout",
        "level": "close above a level",
        "20d": "20-day high breakout",
    }.get(plan["setup"], plan["setup"])
    e = D(plan["entry"])
    risk = D(plan["risk_per_share"])
    pct = (risk / e * 100).quantize(CENT, rounding=ROUND_HALF_UP)
    when = ""
    if plan.get("entry_time"):
        when = " at %s IST" % plan["entry_time"]
        if plan.get("entry_bar") is not None and trade_type == "intraday":
            when += " (bar %d)" % plan["entry_bar"]
    bars_desc = "daily" if plan.get("interval") == "1d" else ("5-minute" if plan.get("interval") == "5m" else "1-minute")
    out = ["Exit plan: %s long (%s %s), %s, %s on %s bars" % (
        plan["symbol"], trade_type, product, day.strftime("%a %Y-%m-%d"), setup, bars_desc)]
    out.append("  Entry         %s%s" % (money(plan["entry"]), when))
    out.append("  Stop          %s  %s%s" % (money(plan["stop"]), plan["stop_label"],
                                             (": " + plan["stop_detail"]) if plan.get("stop_detail") else ""))
    out.append("  Target        %s  %s" % (money(plan["target"]), plan["target_label"]))
    out.append("  Risk a share  %s (%s%% of the entry)" % (money(plan["risk_per_share"]), pct))
    out.append("  Reward:risk   %s" % ratio(plan["reward_risk"]))
    if trade_type == "swing":
        hold = plan.get("max_hold_days") or 10
        out.append("  Hold horizon  up to %d trading days (CNC delivery, no intraday flat-by)" % hold)
    else:
        out.append("  Flat by       %s IST, win or lose: MIS auto square-off window (no overnight hold)" % plan["flat_by"])
    out.append("Set before the entry. If price hits the stop, you are out; never move it further away.")
    out.append(NOTE)
    out.append(DISCLAIMER)
    return out


def cmd_plan(a):
    now = parse_now(a.now)
    plan, warnings = build_plan(a, now)
    if a.write:
        d = os.path.dirname(a.write)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(a.write, "w", encoding="utf-8") as fh:
            json.dump(plan, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
    if a.json:
        print(json.dumps(dict(plan, warnings=warnings), indent=2, ensure_ascii=False))
        return 0
    for line in describe(plan):
        print(line)
    for w in warnings:
        print("Note: " + w)
    if a.write:
        print("Wrote %s." % a.write)
    print("Next: /trader-timemachine replays it, then /trader-budget sizes it to your max loss. Nothing was placed.")
    return 0


def cmd_show(a):
    if not os.path.exists(a.plan):
        print("No plan at %s yet. Make one: exit.py plan --write" % a.plan)
        return 1
    plan = read_json(a.plan, "plan")
    req_keys = ["symbol", "date", "entry", "stop", "target", "risk_per_share", "reward_risk"]
    if plan.get("trade_type", "intraday") == "intraday":
        req_keys.append("flat_by")
    for k in req_keys:
        if plan.get(k) is None:
            raise PlanError("%s is missing %r: make a new plan with exit.py plan --write" % (a.plan, k))
    if a.json:
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        return 0
    for line in describe(dict({"setup": "orb", "interval": "5m", "stop_label": "", "target_label": ""}, **plan)):
        print(line)
    today = to_ist(parse_now(a.now)).date().isoformat()
    if plan["date"] != today:
        print("Note: this plan is for %s, not today (%s)." % (plan["date"], today))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Exit Planner (trader-exit, NSE India): stop and target before the entry.")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("plan", help="set the stop and target")
    p.add_argument("--from", dest="from_path", metavar="PATH", help="entry.json to plan from (default trader/entry.json)")
    p.add_argument("--symbol")
    p.add_argument("--entry", type=float, help="entry price in ₹ (or a plan-ahead price with --from)")
    p.add_argument("--or-high", type=float)
    p.add_argument("--or-low", type=float)
    p.add_argument("--or-minutes", type=int, default=15)
    p.add_argument("--setup", choices=["orb", "level", "20d"])
    p.add_argument("--date", help="session date YYYY-MM-DD (default: the entry's, or today)")
    p.add_argument("--time", help="entry time in IST, e.g. 10:15 AM")
    p.add_argument("--entry-bar", type=int)
    p.add_argument("--interval", choices=["5m", "1m", "1d"])
    p.add_argument("--intraday", action="store_true", help="force intraday mode (MIS, flat-by 15:10)")
    p.add_argument("--swing", action="store_true", help="force swing mode (CNC delivery, daily bars, no flat-by)")
    p.add_argument("--side", default="buy", choices=["buy", "long", "sell", "short"], help="v2: buy only")
    p.add_argument("--stop", help="or-mid (orb default), or-low, swing-low[:N], price:X, pct:X, atr:K (swing default atr:1.5)")
    p.add_argument("--target", help="2R (default), 1R, 1.5R, price:X")
    p.add_argument("--flat-by", help="HH:MM IST (default: config flat_by, 15:10)")
    p.add_argument("--config", default="trader/config.json")
    p.add_argument("--offline", metavar="PATH", help="saved Yahoo chart JSON for an atr or swing-low stop")
    p.add_argument("--now", help='pretend it is this IST time, e.g. "2026-10-30 10:30"')
    p.add_argument("--write", nargs="?", const="trader/plan.json", metavar="PATH",
                   help="save the plan (default trader/plan.json)")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("show", help="print the current plan")
    p.add_argument("--plan", default="trader/plan.json")
    p.add_argument("--now")
    p.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"plan": cmd_plan, "show": cmd_show}[a.cmd](a)
    except PlanError as e:
        print("exit.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
