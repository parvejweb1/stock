#!/usr/bin/env python3
"""Pre-trade risk check for trader-risk (India / NSE & BSE). Standard library only.

Reads proposed orders (trader/orders.json) and checks every one against your
rules (trader/config.json), your account (trader/portfolio.json), prices
(trader/prices.json) and what was placed today (trader/state/broker.json).
Each order gets PASS or BLOCK with the reasons. Orders are checked in file
order as if each PASS had filled, so buys that are fine one by one but break
a limit together are caught.

    python check.py                                 # trader/ files, writes trader/state/risk.json
    python check.py --now "2026-10-30 10:15"        # pin the clock (IST)
    python check.py --orders trader/orders.json --json
    python check.py --no-write                      # check without recording the verdicts

Entries and exits. An ENTRY opens or adds to a position. An EXIT only reduces
what you hold. Getting out must always be possible: an exit is checked for
format, asset/F&O and short sales only; every other rule turns into a warning.

Exit code 0 when every order passes, 1 when any order is blocked.
The verdicts go to trader/state/risk.json with a fingerprint of each order,
so trader-broker only shows an order as ready if THIS exact order passed today.

Educational tool, not SEBI-registered investment advice. Trading loses most people money.
"""

import argparse
import csv
import datetime
import hashlib
import json
import math
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    from zoneinfo import ZoneInfo
    IST_TZ = ZoneInfo("Asia/Kolkata")
except Exception:
    IST_TZ = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

IST_OFFSET = datetime.timedelta(hours=5, minutes=30)

# Approximate Indian statutory & broker charges (Groww / Zerodha-like).
CHARGES_TABLE = {
    "intraday_brokerage_pct": 0.0005,
    "intraday_brokerage_max_inr": 20.0,
    "delivery_brokerage_pct": 0.001,
    "delivery_brokerage_min_inr": 5.0,
    "delivery_brokerage_max_inr": 20.0,
    "stt_intraday_sell_pct": 0.00025,
    "stt_delivery_both_pct": 0.001,
    "exchange_txn_nse_pct": 0.0000297,
    "sebi_turnover_pct": 0.000001,
    "stamp_intraday_buy_pct": 0.00003,
    "stamp_delivery_buy_pct": 0.00015,
    "gst_pct": 0.18,
    "dp_charge_delivery_sell_inr": 15.93,
}

FALLBACK_LOT_SIZES = {
    "NIFTY": 75, "BANKNIFTY": 30, "FINNIFTY": 65, "MIDCPNIFTY": 120,
    "RELIANCE": 500, "TCS": 175, "INFY": 400, "HDFCBANK": 550,
    "ICICIBANK": 700, "SBIN": 750, "TATAMOTORS": 550, "ITC": 1600,
    "LT": 150, "AXISBANK": 625, "KOTAKBANK": 400, "BHARTIARTL": 475,
    "BAJFINANCE": 125, "MARUTI": 50, "M&M": 350, "SUNPHARMA": 350,
}

DEFAULTS = {
    "budget": 1000,
    "cash_buffer_pct": 5,
    "max_position_pct": 100,
    "max_order_inr": 1000,
    "min_order_inr": 50,
    "max_loss_per_trade_inr": 20,
    "max_daily_loss_inr": 50,
    "max_trades_per_day": 2,
    "max_orders_per_day": 10,
    "min_reward_risk": 1.5,
    "require_stop": True,
    "no_entries_before": "09:30",
    "no_entries_after": "14:45",
    "flat_by": "15:10",
    "trade_type": "intraday",
    "product": "MIS",
    "exchange": "NSE",
    "broker": "groww",
    "brokers_available": ["groww", "zerodha", "upstox", "angelone"],
    "paper": True,
    "include_charges": True,
    "blocked": [],
    "allow_fno": False,
    "allow_margin": False,
    "allow_short": False,
    "swing_max_hold_days": 10,
}

# Canonical fields used to compute order fingerprint across check.py and ticket.py
FINGERPRINT_FIELDS = (
    "symbol", "exchange", "segment", "product", "transaction_type",
    "order_type", "quantity", "price", "trigger_price", "validity",
    "stop_loss", "target", "trade_type",
    # legacy fallback fields if present
    "side", "qty", "notional", "type", "time_in_force", "limit_price",
    "stop_price", "order_class", "take_profit_limit_price", "stop_loss_stop_price",
)

EXCHANGES = {"NSE", "BSE", "NFO", "BFO"}
SEGMENTS = {"CASH", "FNO"}
PRODUCTS = {"MIS", "CNC", "NRML"}
ORDER_TYPES = {"MARKET", "LIMIT", "SL", "SL-M", "SL_M"}
VALIDITIES = {"DAY", "IOC", "GTC"}
NSE_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9&\-\.]{0,24}$")
FNO_SYMBOL_RE = re.compile(r"\d{2}(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d+(CE|PE)|FUT)$")
OCC_RE = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")
HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
PAISA = 0.005
FRESH_MINUTES = 15
REF_WORDS = {
    "plan": "the plan's entry",
    "price": "the latest price",
    "limit": "the limit price",
    "trigger": "the trigger price",
    "stale price": "an old price",
}
DISCLAIMER = "Educational tool, not SEBI-registered investment advice. Trading loses most people money."

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
    "2026-11-24": "Guru Nanak Jayanti",
    "2026-12-25": "Christmas",
}
MUHURAT_2026 = "2026-11-08"
OPEN = datetime.time(9, 15)
CLOSE = datetime.time(15, 30)


class RiskError(Exception):
    pass


# ---------------------------------------------------------------- time (IST, Asia/Kolkata)

def to_ist(utc_naive):
    return utc_naive.replace(tzinfo=None) + IST_OFFSET


def ist_to_utc(local_naive):
    return local_naive.replace(tzinfo=None) - IST_OFFSET


def parse_now(text):
    """ISO time -> naive UTC. A time without an offset is taken as IST (Asia/Kolkata)."""
    if not text:
        return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    t = text.strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise RiskError('--now wants a time like "2026-10-30 10:15" (IST) or ...Z (UTC)')
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def parse_utc(text):
    if not text:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = (dt - dt.utcoffset()).replace(tzinfo=None)
    return dt


def load_holidays(cache_dir="trader/cache"):
    holidays = dict(NSE_HOLIDAYS_2026)
    cache_file = os.path.join(cache_dir, "nse_holidays.json")
    if os.path.exists(cache_file):
        try:
            with open(cache_file, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                for k, v in data.items():
                    if re.match(r"^\d{4}-\d{2}-\d{2}$", str(k)):
                        holidays[str(k)] = str(v)
        except Exception:
            pass
    return holidays


def trading_day(d, holidays=None):
    """(is_trading_day, reason). Weekends, Muhurat session, and NSE holidays."""
    iso = d.isoformat()
    if iso == MUHURAT_2026:
        return False, "Muhurat trading special session (Sun 2026-11-08; special ~1 hr evening session, check NSE circular)"
    if d.weekday() >= 5:
        return False, "weekend"
    hmap = holidays if holidays is not None else NSE_HOLIDAYS_2026
    name = hmap.get(iso)
    if name:
        return False, "NSE holiday: " + name
    return True, ""


def hhmm(value, key):
    if value in (None, "", False):
        return None
    m = HHMM.match(str(value).strip())
    if not m:
        raise RiskError('%s must be an IST time like "09:30", got %r' % (key, value))
    return datetime.time(int(m.group(1)), int(m.group(2)))


def window_check(now_ist, c, trade_type="intraday", flat_by_override=None):
    """Reasons an entry cannot be placed now (IST). Empty when window bounds are null."""
    before = hhmm(c.get("no_entries_before"), "no_entries_before")
    after = hhmm(c.get("no_entries_after"), "no_entries_after")
    flat_by = hhmm(flat_by_override or c.get("flat_by"), "flat_by") if trade_type == "intraday" else None
    if before is None and after is None and flat_by is None:
        return [], []
    warns = []
    d, t = now_ist.date(), now_ist.time().replace(second=0, microsecond=0)
    holidays = load_holidays()
    if d.year == 2027:
        warns.append("2027 holidays unknown, check NSE (weekends still closed)")
    elif d.year != 2026:
        warns.append("holiday list covers 2026 only: check nseindia.com for %s" % d.isoformat())
    ok, why = trading_day(d, holidays)
    stamp = now_ist.strftime("%a %Y-%m-%d %H:%M")
    if not ok:
        return ["window: the market is closed today (%s, %s IST)" % (why, stamp)], warns
    if t < OPEN or now_ist.time() >= CLOSE:
        return [
            "window: the market is closed (%s IST; NSE normal session is %s to %s IST)"
            % (stamp, OPEN.strftime("%H:%M"), CLOSE.strftime("%H:%M"))
        ], warns
    if trade_type == "intraday" and flat_by is not None and t >= flat_by:
        return [
            "window: past intraday flat_by %s IST (it is %s IST); MIS positions must be squared off, no new intraday entries"
            % (flat_by.strftime("%H:%M"), t.strftime("%H:%M"))
        ], warns
    if before is not None and t < before:
        return ["window: no new entries before %s IST (it is %s IST)" % (before.strftime("%H:%M"), t.strftime("%H:%M"))], warns
    if after is not None and t > after:
        return ["window: no new entries after %s IST (it is %s IST)" % (after.strftime("%H:%M"), t.strftime("%H:%M"))], warns
    return [], warns


# ---------------------------------------------------------------- helpers & charges

def clean_symbol(raw):
    s = str(raw or "").strip().upper()
    for suf in (".NS", ".BO"):
        if s.endswith(suf):
            s = s[:-len(suf)]
            break
    return s


def round_paise(x):
    return math.floor(float(x) * 100.0 + 0.5 + 1e-9) / 100.0


def is_tick_multiple(x):
    if x is None:
        return True
    v = round(float(x) * 20.0, 4)
    return abs(v - round(v)) < 1e-3


def _indian_grouping(int_str):
    neg = ""
    if int_str.startswith("-"):
        neg, int_str = "-", int_str[1:]
    if len(int_str) <= 3:
        return neg + int_str
    last3 = int_str[-3:]
    rest = int_str[:-3]
    groups = []
    while len(rest) > 2:
        groups.append(rest[-2:])
        rest = rest[:-2]
    if rest:
        groups.append(rest)
    groups.reverse()
    return neg + ",".join(groups) + "," + last3


def money(x):
    x = float(x)
    if abs(x - round(x)) < 0.005:
        return "₹" + _indian_grouping(str(int(round(x))))
    whole, frac = ("%.2f" % abs(x)).split(".")
    sign = "-" if x < 0 else ""
    return sign + "₹" + _indian_grouping(whole) + "." + frac


def _p(x):
    return "%.2f" % float(x)


def _pct(x):
    x = float(x)
    return str(int(x)) if x == int(x) else ("%.2f" % x)


def _num(x):
    x = float(x)
    return str(int(x)) if x == int(x) else ("%.4f" % x).rstrip("0").rstrip(".")


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def estimate_charges(qty, buy_price, sell_price, product="MIS"):
    qty = float(qty or 0)
    if qty <= 0 or not buy_price or not sell_price or buy_price <= 0 or sell_price <= 0:
        return {"brokerage": 0.0, "stt": 0.0, "exchange_txn": 0.0, "sebi": 0.0, "stamp": 0.0, "gst": 0.0, "dp": 0.0, "total": 0.0}
    prod = str(product or "MIS").upper()
    buy_turn = qty * float(buy_price)
    sell_turn = qty * float(sell_price)
    total_turn = buy_turn + sell_turn
    t = CHARGES_TABLE
    if prod == "CNC":
        b_buy = max(t["delivery_brokerage_min_inr"], min(t["delivery_brokerage_max_inr"], buy_turn * t["delivery_brokerage_pct"]))
        b_sell = max(t["delivery_brokerage_min_inr"], min(t["delivery_brokerage_max_inr"], sell_turn * t["delivery_brokerage_pct"]))
        brokerage = b_buy + b_sell
        stt = total_turn * t["stt_delivery_both_pct"]
        stamp = buy_turn * t["stamp_delivery_buy_pct"]
        dp = t["dp_charge_delivery_sell_inr"]
    else:
        b_buy = min(t["intraday_brokerage_max_inr"], buy_turn * t["intraday_brokerage_pct"])
        b_sell = min(t["intraday_brokerage_max_inr"], sell_turn * t["intraday_brokerage_pct"])
        brokerage = b_buy + b_sell
        stt = sell_turn * t["stt_intraday_sell_pct"]
        stamp = buy_turn * t["stamp_intraday_buy_pct"]
        dp = 0.0
    exch = total_turn * t["exchange_txn_nse_pct"]
    sebi = total_turn * t["sebi_turnover_pct"]
    gst = (brokerage + exch + sebi) * t["gst_pct"]
    total = round_paise(brokerage + stt + exch + sebi + stamp + gst + dp)
    return {
        "brokerage": round_paise(brokerage), "stt": round_paise(stt),
        "exchange_txn": round_paise(exch), "sebi": round_paise(sebi),
        "stamp": round_paise(stamp), "gst": round_paise(gst),
        "dp": round_paise(dp), "total": total,
    }


def get_lot_size(symbol, order=None, cache_dir="trader/cache"):
    if isinstance(order, dict):
        ls = _f(order.get("lot_size")) or _f((order.get("plan") or {}).get("lot_size"))
        if ls and int(ls) > 0:
            return int(ls)
    sym = clean_symbol(symbol)
    base = sym
    for idx_name in ("BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTY"):
        if sym.startswith(idx_name):
            base = idx_name
            break
    csv_path = os.path.join(cache_dir, "fo_mktlots.csv")
    if os.path.exists(csv_path):
        try:
            with open(csv_path, encoding="utf-8", errors="ignore") as fh:
                for row in csv.reader(fh):
                    if len(row) >= 3 and row[1].strip().upper() == base:
                        for cell in row[2:]:
                            cell = cell.strip()
                            if cell.isdigit() and int(cell) > 0:
                                return int(cell)
        except Exception:
            pass
    return FALLBACK_LOT_SIZES.get(base, 1)


def _migrate_cfg(cfg):
    pairs = (
        ("max_order_usd", "max_order_inr"),
        ("min_order_usd", "min_order_inr"),
        ("max_loss_per_trade_usd", "max_loss_per_trade_inr"),
        ("max_daily_loss_usd", "max_daily_loss_inr"),
    )
    for old_k, new_k in pairs:
        if old_k in cfg and new_k not in cfg:
            cfg[new_k] = cfg[old_k]
    return cfg


def read_json(path, what, required=True):
    if not path or not os.path.exists(path):
        if required:
            raise RiskError("no %s at %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise RiskError("%s is not valid JSON: %s" % (path, e))


def fingerprint(order):
    """Identical fingerprint function in trader-risk/check.py and trader-broker/ticket.py."""
    core = {k: order[k] for k in FINGERPRINT_FIELDS if k in order and order[k] is not None}
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- account & state

def account(cfg, portfolio, prices):
    warnings = []
    if portfolio is None:
        budget = _f(cfg.get("budget"))
        if not budget:
            raise RiskError("no trader/portfolio.json and no budget in the config: "
                            "nothing to check the orders against")
        return {"cash": budget, "shares": {}, "values": {}, "products": {}}, warnings
    cash = _f(portfolio.get("cash"))
    if cash is None:
        raise RiskError('portfolio.json needs a "cash" number')
    shares, values, products = {}, {}, {}
    for p in portfolio.get("positions", []):
        t = clean_symbol(p.get("ticker") or p.get("symbol") or "")
        if not t:
            continue
        n = _f(p.get("shares", p.get("qty", p.get("quantity"))))
        if n is None:
            n = sum(_f(l.get("shares")) or 0 for l in p.get("lots", []))
        shares[t] = shares.get(t, 0.0) + n
        if p.get("product"):
            products[t] = str(p.get("product")).upper()
        if prices.get(t):
            values[t] = shares[t] * prices[t]
        else:
            basis = sum(_f(l.get("cost")) or 0 for l in p.get("lots", []))
            if not basis and _f(p.get("avg_price")):
                basis = shares[t] * float(p["avg_price"])
            values[t] = basis
            warnings.append("no live price for %s: valued at its cost basis %s" % (t, money(basis)))
    return {"cash": cash, "shares": shares, "values": values, "products": products}, warnings


def rec_day(rec):
    return str(rec.get("date") or str(rec.get("at", ""))[:10])


def rec_is_entry(rec):
    k = rec.get("kind")
    if k:
        return k == "entry"
    side = str(rec.get("side") or rec.get("transaction_type") or "").lower()
    return side == "buy"


def placed_today(state, today):
    out = []
    for rec in (state or {}).get("orders", []):
        if rec.get("status") == "placed" and rec_day(rec) == today:
            out.append(rec)
    return out


# ---------------------------------------------------------------- the check

def _is_fno_order(sym, o, plan, segment, product, exchange):
    if segment == "FNO" or product == "NRML" or exchange in ("NFO", "BFO"):
        return True
    if str(plan.get("segment") or "").upper() == "FNO":
        return True
    if str(plan.get("instrument_type") or "").upper() in ("FUT", "CE", "PE", "OPTIDX", "OPTSTK", "FUTIDX", "FUTSTK"):
        return True
    if int(_f(o.get("lot_size")) or _f(plan.get("lot_size")) or 1) > 1:
        return True
    if FNO_SYMBOL_RE.search(sym) or OCC_RE.match(sym.replace(" ", "")):
        return True
    return False


def check(orders, cfg, portfolio=None, prices=None, state=None, today=None, now=None,
          price_times=None, price_details=None):
    """Pure function. Returns {"results": [...], "pass": n, "block": n, "warnings": [...]}."""
    c = dict(DEFAULTS)
    c.update(_migrate_cfg(dict(cfg or {})))
    prices = {clean_symbol(k): float(v) for k, v in (prices or {}).items() if _f(v)}
    price_details = {clean_symbol(k): v for k, v in (price_details or {}).items() if isinstance(v, dict)}
    now_utc = now or parse_now(None)
    now_ist = to_ist(now_utc)
    today = today or now_ist.date().isoformat()
    acct, top_warn = account(c, portfolio, prices)
    blocked = {clean_symbol(b) for b in c.get("blocked") or []}
    done_today = placed_today(state, today)
    count_today = len(done_today)
    entries_today = [r for r in done_today if rec_is_entry(r)]
    loss_today = 0.0
    for r in entries_today:
        ml = _f(r.get("max_loss_inr"))
        if ml is None:
            ml = _f(r.get("max_loss"))
        if ml is None:
            top_warn.append(
                "an entry placed today (%s %s) has no max loss in the log; not counted in the daily loss"
                % (r.get("transaction_type") or r.get("side"), r.get("symbol"))
            )
        else:
            loss_today += ml
    placed_keys = {
        (clean_symbol(r.get("symbol", "")), str(r.get("transaction_type") or r.get("side") or "").lower())
        for r in done_today
    }
    seen = set()
    results = []
    batch_entries, batch_loss = 0, 0.0
    if not c.get("paper", True):
        top_warn.append('LIVE account ("paper": false): these orders use real money on %s' % c.get("broker", "groww"))

    def fresh(sym):
        if sym not in prices:
            return None
        if price_times is None:
            return prices[sym]
        t = price_times.get(sym)
        if t is None:
            return None
        age = (now_utc - t).total_seconds()
        return prices[sym] if -60 <= age <= FRESH_MINUTES * 60 else None

    for i, o in enumerate(orders, 1):
        reasons, warns, info = [], [], []
        later = []
        raw_sym = str(o.get("symbol", "")).strip().upper()
        sym = clean_symbol(raw_sym)
        plan = o.get("plan") if isinstance(o.get("plan"), dict) else {}

        # Support both new Indian order fields and legacy fields
        tx_raw = o.get("transaction_type") or o.get("side") or ""
        side = str(tx_raw).strip().lower()
        exchange = str(o.get("exchange") or plan.get("exchange") or c.get("exchange", "NSE")).strip().upper()
        trade_type = str(o.get("trade_type") or plan.get("trade_type") or c.get("trade_type", "intraday")).strip().lower()
        product = str(o.get("product") or plan.get("product") or ("CNC" if trade_type == "swing" else c.get("product", "MIS"))).strip().upper()
        segment = str(o.get("segment") or plan.get("segment") or ("FNO" if product == "NRML" else "CASH")).strip().upper()
        order_type = str(o.get("order_type") or o.get("type") or "MARKET").strip().upper().replace("STOP_LIMIT", "SL").replace("STOP", "SL-M")
        validity = str(o.get("validity") or o.get("time_in_force") or "DAY").strip().upper()
        qty_raw = o.get("quantity") if o.get("quantity") is not None else o.get("qty")
        notional = o.get("notional") or o.get("amount_inr")
        limit_price = _f(o.get("price")) if order_type in ("LIMIT", "SL") and _f(o.get("price")) else _f(o.get("limit_price"))
        trigger_price = _f(o.get("trigger_price")) or _f(o.get("stop_price"))

        # format checks
        if not sym:
            reasons.append("format: no symbol")
        if side not in ("buy", "sell"):
            reasons.append("format: transaction_type/side must be BUY or SELL, got %r" % tx_raw)
        if qty_raw is None and notional is None:
            reasons.append("format: order needs a quantity")
        q = _f(qty_raw)
        if qty_raw is not None:
            if q is None or q <= 0 or abs(q - round(q)) > 1e-9:
                reasons.append("format: quantity must be a positive whole number of shares, got %r" % qty_raw)
            else:
                q = int(round(q))
        if exchange not in EXCHANGES:
            reasons.append("format: unknown exchange %r (expected NSE or BSE)" % exchange)
        if segment not in SEGMENTS:
            reasons.append("format: unknown segment %r (expected CASH or FNO)" % segment)
        if product not in PRODUCTS:
            reasons.append("format: unknown product %r (expected MIS, CNC, or NRML)" % product)
        if order_type not in ORDER_TYPES:
            reasons.append("format: unknown order_type %r (expected MARKET, LIMIT, SL, or SL-M)" % order_type)
        if validity not in VALIDITIES:
            reasons.append("format: unknown validity %r (expected DAY or IOC)" % validity)
        if order_type in ("LIMIT", "SL") and (limit_price is None or limit_price <= 0):
            reasons.append("format: a %s order needs a positive price" % order_type)
        if order_type in ("SL", "SL-M", "SL_M") and (trigger_price is None or trigger_price <= 0):
            reasons.append("format: a %s order needs a positive trigger_price" % order_type)

        # Tick size validation (₹0.05)
        for fname, fval in (("price", limit_price), ("trigger_price", trigger_price),
                            ("stop_loss", _f(o.get("stop_loss"))), ("target", _f(o.get("target")))):
            if fval is not None and fval > 0 and not is_tick_multiple(fval):
                warns.append("tick size: %s %s is not a multiple of ₹0.05" % (fname, money(fval)))

        # F&O and asset checks
        is_fno = _is_fno_order(sym, o, plan, segment, product, exchange)
        lot_size = get_lot_size(sym, o) if is_fno else int(_f(o.get("lot_size")) or 1)
        if is_fno:
            if not c.get("allow_fno", False):
                reasons.append(
                    'fno: %s is an F&O / %s order (lot size %d); F&O is disabled ("allow_fno": false)'
                    % (sym, product, lot_size)
                )
            else:
                warns.append("fno: F&O trading carries high leverage risk")
                if q and lot_size > 1 and (q % lot_size != 0):
                    reasons.append(
                        "lot size: quantity %d for %s is not a multiple of its NSE lot size %d"
                        % (q, sym, lot_size)
                    )
        elif sym and ("/" in sym or not NSE_SYMBOL_RE.match(sym)):
            reasons.append("asset: %s does not look like a valid NSE/BSE equity symbol" % sym)

        # Reference price
        plan_entry = _f(plan.get("entry"))
        live = fresh(sym)
        ref, ref_from = None, None
        if order_type in ("LIMIT", "SL") and limit_price:
            ref, ref_from = limit_price, "limit"
        elif order_type in ("SL-M", "SL_M") and trigger_price:
            ref, ref_from = trigger_price, "trigger"
        if ref is None:
            cands = [(p, w) for p, w in ((plan_entry, "plan"), (live, "price")) if p]
            if cands:
                ref, ref_from = max(cands, key=lambda x: x[0]) if side == "buy" else min(cands, key=lambda x: x[0])
                ref = round_paise(ref)
                if live and ref_from == "price" and plan_entry and side == "buy" and live > plan_entry + PAISA:
                    info.append(
                        "%s last %s is above the plan's entry %s: risk is measured from %s"
                        % (sym, money(ref), money(plan_entry), money(ref))
                    )
            elif sym in prices:
                ref, ref_from = prices[sym], "stale price"
            elif _f(o.get("price")) and _f(o.get("price")) > 0:
                ref, ref_from = _f(o.get("price")), "limit"

        if plan_entry and live is None and sym in prices and price_times is not None and order_type == "MARKET":
            warns.append("the %s price in trader/prices.json is older than %d minutes: not used" % (sym, FRESH_MINUTES))

        value = None
        if q and ref:
            value = round_paise(q * ref)
        elif notional and _f(notional):
            value = round_paise(_f(notional))

        # Entry or exit
        held = acct["shares"].get(sym, 0.0)
        held_value = acct["values"].get(sym, 0.0)
        exiting = False
        if side == "sell" and held > 1e-9:
            if q is not None:
                exiting = q <= held + 1e-9
            elif value is not None:
                exiting = value <= held_value + PAISA
        elif side == "buy" and held < -1e-9 and q is not None:
            exiting = q <= -held + 1e-9
        kind = "exit" if exiting else "entry"

        if kind == "entry" and value is None and q:
            later.append(
                "price: no price for %s to value a %s-share order; add it to trader/prices.json"
                % (sym, q)
            )

        # Upper / lower circuit check
        det = price_details.get(sym) or {}
        upper_cp = _f(det.get("upper_circuit") or det.get("upperCP") or o.get("upper_circuit"))
        lower_cp = _f(det.get("lower_circuit") or det.get("lowerCP") or o.get("lower_circuit"))
        if kind == "entry" and side == "buy":
            if upper_cp and ref and ref >= upper_cp - PAISA:
                later.append("circuit: %s price %s is at or above its upper circuit %s (cannot buy at upper circuit)"
                             % (sym, money(ref), money(upper_cp)))
            elif not upper_cp:
                warns.append("circuit: Yahoo quote does not include NSE circuit band for %s; verify it is not locked at upper circuit" % sym)
        elif side == "sell" and lower_cp and ref and ref <= lower_cp + PAISA:
            warns.append("circuit: %s is at or near lower circuit %s; sell liquidity may be locked" % (sym, money(lower_cp)))

        # Short selling check (India: MIS intraday only, never CNC delivery)
        if side == "sell" and sym and kind == "entry":
            over = (q > held + 1e-9) if q is not None else (held <= 0)
            if over:
                if not c.get("allow_short"):
                    reasons.append(
                        'short: selling more %s than you hold (%s shares); short selling is off ("allow_short": false). '
                        "If you bought it, record the fill first (ticket.py fill)"
                        % (sym, _num(held))
                    )
                elif product != "MIS" or trade_type != "intraday":
                    reasons.append(
                        "short: Indian equity short selling is allowed only in intraday MIS product (got product=%s, trade_type=%s); "
                        "delivery (CNC) shorting is prohibited on NSE/BSE"
                        % (product, trade_type)
                    )

        # Intraday vs Swing product consistency
        if kind == "entry" and not is_fno:
            if trade_type == "intraday" and product == "CNC":
                warns.append("product: trade_type is intraday but product is CNC (delivery charges & DP fee apply)")
            elif trade_type == "swing" and product == "MIS":
                later.append("product: swing trade cannot use MIS (intraday product is auto squared-off at 15:10-15:20 IST); use CNC")

        # Blocked list and order size limits
        if sym in blocked:
            later.append("blocked: %s is on your blocked list" % sym)
        if value is not None:
            if value < float(c.get("min_order_inr") or 0) - PAISA:
                later.append("size: %s is under your %s minimum order" % (money(value), money(c["min_order_inr"])))
            if c.get("max_order_inr") and value > float(c["max_order_inr"]) + PAISA:
                later.append("size: %s is over your %s max order" % (money(value), money(c["max_order_inr"])))

        # Account cash & position cap
        cash = acct["cash"]
        total = cash + sum(acct["values"].values())
        buffer_inr = total * float(c.get("cash_buffer_pct") or 0) / 100.0
        if value is not None and side == "buy" and kind == "entry":
            after_cash = cash - value
            if after_cash < -PAISA and not c.get("allow_margin"):
                later.append(
                    "cash: order costs %s but account has only %s cash; margin/leverage is off (\"allow_margin\": false)"
                    % (money(value), money(cash))
                )
            elif after_cash < buffer_inr - PAISA:
                later.append(
                    "cash: leaves %s cash, under your %s%% cash buffer of %s"
                    % (money(max(after_cash, 0)), _pct(c["cash_buffer_pct"]), money(buffer_inr))
                )
            pos_after = held_value + value
            limit_pos = total * float(c.get("max_position_pct") or 100) / 100.0
            if total > 0 and pos_after > limit_pos + PAISA:
                later.append(
                    "position: %s would be %s (%.1f%% of the %s account), over your %s%% cap"
                    % (sym, money(pos_after), 100.0 * pos_after / total, money(total), _pct(c["max_position_pct"]))
                )

        # Stop, max loss (including charges), reward:risk, daily loss, trades/day, window
        max_loss = price_loss = est_charges = risk_ps = rr = stop = target = None
        include_charges = bool(c.get("include_charges", True))
        if kind == "entry" and side in ("buy", "sell"):
            long = side == "buy"
            leg_stop = _f(o.get("stop_loss")) or _f(o.get("stop_loss_stop_price"))
            plan_stop = _f(plan.get("stop"))
            stop = leg_stop if leg_stop else plan_stop
            target = _f(o.get("target")) or _f(o.get("take_profit_limit_price")) or _f(plan.get("target"))
            if leg_stop and plan_stop and abs(leg_stop - plan_stop) > PAISA:
                warns.append(
                    "the order stop_loss %s differs from the plan's stop %s: %s is used"
                    % (money(leg_stop), money(plan_stop), money(leg_stop))
                )
            if stop and not leg_stop:
                warns.append(
                    "the stop %s is in the plan only, not on the order: set a stop alert "
                    "(trader-alerts: alerts.py add --from-plan)" % money(stop)
                )
            if not stop:
                if c.get("require_stop", True):
                    later.append(
                        'stop: no stop_loss. Every entry needs one ("require_stop": true), from trader-exit and trader-budget'
                    )
                else:
                    warns.append("no stop: max loss is uncapped and not counted in daily loss")
            elif ref:
                if (long and stop >= ref - PAISA) or (not long and stop <= ref + PAISA):
                    later.append(
                        "stop: the stop %s must be %s the entry %s for a %s"
                        % (money(stop), "below" if long else "above", money(ref), side.upper())
                    )
                else:
                    risk_p = int(round(abs(ref - stop) * 100))
                    risk_ps = risk_p / 100.0
                    shares = q if q is not None else ((value or 0.0) / ref)
                    price_loss = round_paise(shares * risk_ps)
                    ch_stop = estimate_charges(shares, ref if long else stop, stop if long else ref, product)
                    est_charges = ch_stop["total"] if include_charges else 0.0
                    max_loss = round_paise(price_loss + est_charges)
                    cap_loss = _f(c.get("max_loss_per_trade_inr"))
                    if cap_loss and max_loss > cap_loss + PAISA:
                        later.append(
                            "loss: %s share%s x %s price risk = %s%s = %s at the stop, over your %s max loss per trade"
                            % (
                                _num(shares), "" if shares == 1 else "s", money(risk_ps), money(price_loss),
                                (" + %s est. charges" % money(est_charges)) if include_charges else "",
                                money(max_loss), money(cap_loss),
                            )
                        )
                    min_rr = _f(c.get("min_reward_risk"))
                    if target:
                        if (long and target <= ref + PAISA) or (not long and target >= ref - PAISA):
                            later.append(
                                "target: the target %s must be %s the entry %s"
                                % (money(target), "above" if long else "below", money(ref))
                            )
                        else:
                            reward_p = int(round(abs(target - ref) * 100))
                            rr = round(reward_p / float(risk_p), 2)
                            if min_rr and rr < min_rr - 1e-9:
                                later.append(
                                    "reward:risk: %.2f (target %s, entry %s, stop %s) is under your minimum %s"
                                    % (rr, money(target), money(ref), money(stop), _num(min_rr))
                                )
                            gross_rew = round_paise(shares * (reward_p / 100.0))
                            ch_tgt = estimate_charges(shares, ref if long else target, target if long else ref, product)["total"]
                            if include_charges and gross_rew > 0 and (ch_tgt / gross_rew) >= 0.20:
                                warns.append(
                                    "charges: estimated round-trip charges %s eat %.1f%% of your %s gross target reward (%s)"
                                    % (money(ch_tgt), 100.0 * ch_tgt / gross_rew, money(gross_rew), product)
                                )
                    elif min_rr:
                        later.append("reward:risk: no target to measure against (min_reward_risk %s)" % _num(min_rr))

            daily = _f(c.get("max_daily_loss_inr"))
            if daily and max_loss is not None:
                used = loss_today + batch_loss
                if used + max_loss > daily + PAISA:
                    later.append(
                        "daily loss: %s already at risk today plus %s for this trade = %s, over your %s daily loss limit"
                        % (money(used), money(max_loss), money(used + max_loss), money(daily))
                    )
            cap_t = int(c.get("max_trades_per_day") or 0)
            if cap_t and len(entries_today) + batch_entries >= cap_t:
                later.append(
                    "trades: %d entries placed today plus %d passed earlier in this batch; your limit is %d trades/day"
                    % (len(entries_today), batch_entries, cap_t)
                )

            flat_by_ord = o.get("flat_by") or plan.get("flat_by") or c.get("flat_by")
            win_reasons, win_warns = window_check(now_ist, c, trade_type=trade_type, flat_by_override=flat_by_ord)
            later.extend(win_reasons)
            for ww in win_warns:
                if ww not in top_warn:
                    top_warn.append(ww)

            if stop and ref and risk_ps:
                info.append(
                    "entry %s (%s), stop %s, target %s: price risk %s/share (%s, %s)"
                    % (
                        money(ref), REF_WORDS.get(ref_from, ref_from), money(stop),
                        money(target) if target else "none", money(risk_ps), product, trade_type,
                    )
                )
                info.append(
                    "max loss %s (%s price loss%s)%s, reward:risk %s%s"
                    % (
                        money(max_loss), money(price_loss),
                        (" + %s charges" % money(est_charges)) if include_charges else "",
                        (" (limit %s)" % money(c["max_loss_per_trade_inr"])) if _f(c.get("max_loss_per_trade_inr")) else "",
                        ("%.2f" % rr) if rr is not None else "n/a",
                        (" (min %s)" % _num(c["min_reward_risk"])) if _f(c.get("min_reward_risk")) else "",
                    )
                )
            if daily or cap_t:
                info.append(
                    "today before this: %d of %s trades, %s of %s daily loss"
                    % (
                        len(entries_today) + batch_entries, cap_t or "unlimited",
                        money(loss_today + batch_loss), money(daily) if daily else "unlimited",
                    )
                )

        # Duplicates and daily order limit
        key = (sym, side)
        if key in seen:
            later.append("duplicate: a second %s %s in this batch" % (side.upper(), sym))
        if key in placed_keys:
            later.append("duplicate: a %s %s was already placed today" % (side.upper(), sym))
        passed_so_far = sum(1 for r in results if r["verdict"] == "PASS")
        cap_n = int(c.get("max_orders_per_day") or 0)
        if cap_n and count_today + passed_so_far >= cap_n:
            later.append(
                "daily limit: %d orders placed today plus %d passed earlier in this batch; limit is %d/day"
                % (count_today, passed_so_far, cap_n)
            )
        seen.add(key)

        if kind == "entry":
            reasons.extend(later)
        else:
            warns.extend("exit, not blocked: " + x for x in later)

        verdict = "BLOCK" if reasons else "PASS"
        if verdict == "PASS":
            if kind == "entry" and side in ("buy", "sell"):
                batch_entries += 1
                batch_loss += max_loss or 0.0
            if side == "buy":
                if value is not None:
                    acct["cash"] -= value
                    acct["values"][sym] = acct["values"].get(sym, 0.0) + value
                if q is not None:
                    acct["shares"][sym] = held + q
            elif side == "sell":
                if value is not None:
                    acct["cash"] += value
                    acct["values"][sym] = max(acct["values"].get(sym, 0.0) - value, 0.0)
                if q is not None:
                    acct["shares"][sym] = held - q

        results.append({
            "index": i,
            "symbol": sym,
            "exchange": exchange,
            "segment": segment,
            "product": product,
            "trade_type": trade_type,
            "side": side,
            "transaction_type": side.upper(),
            "kind": kind,
            "quantity": q,
            "amount": ("%s share%s (%s)" % (q, "" if q == 1 else "s", product)) if q is not None else money(_f(notional) or 0),
            "value_inr": round_paise(value) if value is not None else None,
            "entry_ref": round_paise(ref) if ref else None,
            "entry_ref_from": ref_from,
            "stop": stop,
            "target": target,
            "risk_per_share": risk_ps,
            "price_loss_inr": price_loss,
            "est_charges_inr": est_charges,
            "max_loss": max_loss,
            "max_loss_inr": max_loss,
            "reward_risk": rr,
            "fingerprint": fingerprint(o),
            "verdict": verdict,
            "reasons": reasons,
            "warnings": warns,
            "info": info,
        })

    return {
        "date": today,
        "now_ist": now_ist.strftime("%Y-%m-%d %H:%M"),
        "paper": bool(c.get("paper", True)),
        "broker": c.get("broker", "groww"),
        "results": results,
        "pass": sum(1 for r in results if r["verdict"] == "PASS"),
        "block": sum(1 for r in results if r["verdict"] == "BLOCK"),
        "warnings": top_warn,
        "cash_after_inr": round_paise(acct["cash"]),
        "cash_after": round_paise(acct["cash"]),
        "today": {
            "orders_placed": count_today,
            "entries_placed": len(entries_today),
            "loss_at_risk_inr": round_paise(loss_today),
        },
    }


def report(res):
    lines = []
    for w in res["warnings"]:
        lines.append("WARNING: " + w)
    if res["warnings"]:
        lines.append("")
    for r in res["results"]:
        lines.append("%-5s #%d %s %s %s, %s" % (
            r["verdict"], r["index"], r["transaction_type"], r["symbol"], r["amount"], r["kind"]
        ))
        for x in r.get("info", []):
            lines.append("          " + x)
        for x in r["reasons"]:
            lines.append("        - " + x)
        for x in r["warnings"]:
            lines.append("        ~ " + x)
    lines.append("")
    lines.append(
        "%d passed, %d blocked at %s IST. Cash after passed orders: %s (%s account, %s)."
        % (
            res["pass"], res["block"], res["now_ist"], money(res["cash_after_inr"]),
            "paper" if res["paper"] else "LIVE", res.get("broker", "groww"),
        )
    )
    if res["block"]:
        lines.append("Blocked orders do not go to the broker. Fix the order or your rules, then check again.")
    else:
        lines.append("Every order passed. trader-broker still asks you yes or no for each one.")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def load_prices(pdoc):
    """prices.json -> ({SYM: price}, {SYM: naive UTC time}, {SYM: detail dict})."""
    if not isinstance(pdoc, dict):
        return {}, {}, {}
    prices = pdoc.get("prices", pdoc)
    if not isinstance(prices, dict):
        return {}, {}, {}
    out, times, details = {}, {}, {}
    as_of = parse_utc(pdoc.get("as_of"))
    detail = pdoc.get("detail") or {}
    for s, p in prices.items():
        if _f(p) is None:
            continue
        clean = clean_symbol(s)
        out[clean] = float(p)
        d = detail.get(s) or detail.get(clean) or detail.get(clean + ".NS") or {}
        if isinstance(d, dict):
            details[clean] = d
            times[clean] = parse_utc(d.get("time")) or parse_utc(d.get("fetched")) or as_of
        else:
            times[clean] = as_of
    return out, times, details


def main(argv=None):
    ap = argparse.ArgumentParser(description="Check proposed Indian equity orders against your limits (trader-risk).")
    ap.add_argument("--orders", default="trader/orders.json")
    ap.add_argument("--config", default="trader/config.json")
    ap.add_argument("--portfolio", default="trader/portfolio.json",
                    help="your account; if missing, a fresh account funded with the budget")
    ap.add_argument("--prices", default="trader/prices.json")
    ap.add_argument("--state", default="trader/state/broker.json", help="what trader-broker placed today")
    ap.add_argument("--now", help='pretend it is this IST time, e.g. "2026-10-30 10:15"')
    ap.add_argument("--today", help="YYYY-MM-DD (default: today in IST)")
    ap.add_argument("--out", default="trader/state/risk.json", help="where the verdicts are recorded")
    ap.add_argument("--no-write", action="store_true", help="do not record the verdicts")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        now = parse_now(a.now)
        if a.today and not re.match(r"^\d{4}-\d{2}-\d{2}$", a.today):
            raise RiskError("--today wants a date like 2026-10-30")
        doc = read_json(a.orders, "orders file (run trader-budget first)")
        orders = doc.get("orders", doc) if isinstance(doc, dict) else doc
        if not isinstance(orders, list) or not orders:
            raise RiskError("no orders in %s" % a.orders)
        cfg = read_json(a.config, "config", required=False) or {}
        portfolio = read_json(a.portfolio, "portfolio", required=False)
        pdoc = read_json(a.prices, "prices", required=False) or {}
        prices, times, details = load_prices(pdoc)
        state = read_json(a.state, "broker state", required=False)
        res = check(orders, cfg, portfolio, prices, state, a.today, now, times, details)
        if pdoc.get("as_of"):
            res["prices_as_of"] = pdoc["as_of"]
        if not a.no_write:
            d = os.path.dirname(a.out)
            if d:
                os.makedirs(d, exist_ok=True)
            rec = dict(res)
            rec["checked_at"] = datetime.datetime.now(datetime.timezone.utc).replace(
                microsecond=0, tzinfo=None
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
            rec["orders_file"] = a.orders
            tmp = a.out + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(rec, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
            os.replace(tmp, a.out)
    except RiskError as e:
        print("check.py: %s" % e, file=sys.stderr)
        return 1
    print(json.dumps(res, indent=2, ensure_ascii=False) if a.json else report(res))
    return 1 if res["block"] else 0


if __name__ == "__main__":
    sys.exit(main())
