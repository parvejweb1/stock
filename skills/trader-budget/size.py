#!/usr/bin/env python3
"""Position sizing for trader-budget (India / NSE & BSE). Standard library only.

Default mode: sizes ONE trade (intraday MIS or swing CNC) from trader/plan.json
(written by trader-exit) to your budget and your max loss per trade (including
estimated Indian brokerage, STT, exchange txn, SEBI, stamp duty, GST, and DP
charges when include_charges is true), then writes one broker-neutral Indian
order to trader/orders.json. Nothing is placed: trader-risk checks the order
and trader-broker asks you yes or no.

    python size.py                                  # trader/plan.json + trader/config.json
    python size.py --write                          # write trader/orders.json
    python size.py --limit                          # a LIMIT order at the plan's entry
    python size.py --limit 820.00 --write           # a LIMIT order at ₹820.00
    python size.py --now "2026-10-30 10:15" --json  # pin the clock (IST)

How many shares. Whole shares only (or lot-size multiples for F&O when enabled):

    quantity = floor of the smallest of
      max loss       qty where (qty x risk_per_share + est_charges_at_stop) <= max_loss_per_trade_inr
      position cap   budget x max_position_pct / entry
      max order      max_order_inr / entry
      cash           (budget minus the cash buffer) / entry
                     (and no more than the cash in trader/portfolio.json, if you have one)

Weights mode (split a budget across a list of NSE stocks by weight):

    python size.py --weights HDFCBANK=28.5,RELIANCE=22.1,ICICIBANK=19.4,INFY=15.8,TCS=14.2
    python size.py --targets targets.example.json --budget 10000 --prices trader/prices.json

Educational tool, not SEBI-registered investment advice. Trading loses most people money.
"""

import argparse
import csv
import datetime
import json
import math
import os
import sys
import urllib.request

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
# Used when "include_charges": true in trader/config.json.
CHARGES_TABLE = {
    "intraday_brokerage_pct": 0.0005,      # 0.05% per executed order
    "intraday_brokerage_max_inr": 20.0,    # capped at ₹20 per order
    "delivery_brokerage_pct": 0.001,       # 0.10% per executed order (Groww equity delivery)
    "delivery_brokerage_min_inr": 5.0,     # minimum ₹5 per order on Groww delivery
    "delivery_brokerage_max_inr": 20.0,    # capped at ₹20 per order
    "stt_intraday_sell_pct": 0.00025,      # 0.025% on sell side (MIS / F&O)
    "stt_delivery_both_pct": 0.001,        # 0.10% on both buy and sell (CNC)
    "exchange_txn_nse_pct": 0.0000297,     # ~0.00297% NSE cash/F&O turnover
    "sebi_turnover_pct": 0.000001,         # ₹10 per crore = 0.0001%
    "stamp_intraday_buy_pct": 0.00003,     # 0.003% on buy side (MIS / F&O)
    "stamp_delivery_buy_pct": 0.00015,     # 0.015% on buy side (CNC)
    "gst_pct": 0.18,                       # 18% on (brokerage + exchange_txn + sebi)
    "dp_charge_delivery_sell_inr": 15.93,  # ₹13.5 + 18% GST per scrip on CNC sell
}

# Fallback NSE F&O lot sizes when fo_mktlots.csv is unavailable
FALLBACK_LOT_SIZES = {
    "NIFTY": 75,
    "BANKNIFTY": 30,
    "FINNIFTY": 65,
    "MIDCPNIFTY": 120,
    "RELIANCE": 500,
    "TCS": 175,
    "INFY": 400,
    "HDFCBANK": 550,
    "ICICIBANK": 700,
    "SBIN": 750,
    "TATAMOTORS": 550,
    "ITC": 1600,
    "LT": 150,
    "AXISBANK": 625,
    "KOTAKBANK": 400,
    "BHARTIARTL": 475,
    "BAJFINANCE": 125,
    "MARUTI": 50,
    "M&M": 350,
    "SUNPHARMA": 350,
}

# Spec defaults from _INDIA_SPEC.md
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
    "whole_shares": True,
}

EPS = 1e-9
TICK = 0.05
FRESH_MINUTES = 15
SETUP_WORDS = {
    "orb": "opening-range breakout",
    "flag": "bull flag",
    "bounce": "oversold bounce",
    "breakout": "20-day breakout",
    "reclaim": "20-DMA reclaim",
}
ORDER_CORE_FIELDS = (
    "symbol", "exchange", "segment", "product", "transaction_type",
    "order_type", "quantity", "price", "trigger_price", "validity",
    "stop_loss", "target", "trade_type",
)
DISCLAIMER = "Educational tool, not SEBI-registered investment advice. Trading loses most people money."


class SizeError(Exception):
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
        raise SizeError('--now wants a time like "2026-10-30 10:15" (IST) or ...Z (UTC)')
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def parse_utc(text):
    """"2026-10-30T04:45:00Z" or "+05:30" -> naive UTC, or None."""
    if not text:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = (dt - dt.utcoffset()).replace(tzinfo=None)
    return dt


def utc_iso(now_utc=None):
    now_utc = now_utc or datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    return now_utc.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def ist_iso(now_utc=None):
    now_utc = now_utc or datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    return to_ist(now_utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S+05:30")


# ---------------------------------------------------------------- symbol & money helpers

def clean_symbol(raw):
    """Store/display plain NSE symbol ('RELIANCE', 'SBIN', 'M&M'). Strip .NS / .BO suffix."""
    s = str(raw or "").strip().upper()
    for suf in (".NS", ".BO"):
        if s.endswith(suf):
            s = s[:-len(suf)]
            break
    return s


def yahoo_symbol(sym, exchange="NSE"):
    s = str(sym or "").strip().upper()
    if s.startswith("^") or s.endswith(".NS") or s.endswith(".BO"):
        return s
    return s + (".BO" if str(exchange).upper() == "BSE" else ".NS")


def round_tick(x):
    """Round price to nearest ₹0.05 tick."""
    ticks = math.floor(float(x) * 20.0 + 0.5 + EPS)
    return round(ticks / 20.0, 2)


def floor_tick(x):
    """Round stop price DOWN to ₹0.05 tick."""
    ticks = math.floor(float(x) * 20.0 + EPS)
    return round(ticks / 20.0, 2)


def round_paise(x):
    return math.floor(float(x) * 100.0 + 0.5 + EPS) / 100.0


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
    """Format INR with ₹ and Indian grouping (e.g. ₹1,00,000 or ₹1,234.50)."""
    x = float(x)
    if abs(x - round(x)) < 0.005:
        return "₹" + _indian_grouping(str(int(round(x))))
    whole, frac = ("%.2f" % abs(x)).split(".")
    sign = "-" if x < 0 else ""
    return sign + "₹" + _indian_grouping(whole) + "." + frac


def _price(x):
    return "%.2f" % round_paise(x)


def _num(x):
    x = round(float(x), 2)
    if abs(x - round(x)) < 0.005:
        return str(int(round(x)))
    return "%.2f" % x


def shares_for(inr_amount, per_share):
    """Whole shares that fit in inr_amount at per_share, in exact integer paise."""
    u = int(round(float(inr_amount) * 100))
    p = int(round(float(per_share) * 100))
    if p <= 0 or u <= 0:
        return 0
    return u // p


# ---------------------------------------------------------------- charges calculation

def estimate_charges(qty, buy_price, sell_price, product="MIS"):
    """Approximate Indian round-trip charges (buy + sell) for qty shares."""
    qty = float(qty)
    if qty <= 0 or buy_price <= 0 or sell_price <= 0:
        return {
            "brokerage": 0.0, "stt": 0.0, "exchange_txn": 0.0,
            "sebi": 0.0, "stamp": 0.0, "gst": 0.0, "dp": 0.0, "total": 0.0,
        }
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
        # MIS (intraday) or NRML (F&O)
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
        "brokerage": round_paise(brokerage),
        "stt": round_paise(stt),
        "exchange_txn": round_paise(exch),
        "sebi": round_paise(sebi),
        "stamp": round_paise(stamp),
        "gst": round_paise(gst),
        "dp": round_paise(dp),
        "total": total,
    }


def max_shares_for_loss(max_loss_inr, entry, stop, product="MIS", include_charges=True, lot_size=1):
    """Largest whole-share quantity (multiple of lot_size) whose loss at stop (+ charges) <= max_loss_inr."""
    risk_ps = round_paise(float(entry) - float(stop))
    if risk_ps <= 0 or max_loss_inr <= 0 or lot_size <= 0:
        return 0
    upper = shares_for(max_loss_inr, risk_ps)
    upper = (upper // lot_size) * lot_size
    if not include_charges:
        return max(0, upper)
    while upper >= lot_size:
        ch = estimate_charges(upper, entry, stop, product)["total"]
        total_loss = round_paise(upper * risk_ps + ch)
        if total_loss <= max_loss_inr + 0.005:
            return upper
        upper -= lot_size
    return 0


# ---------------------------------------------------------------- F&O lot size helper

def get_lot_size(symbol, plan=None, cache_dir="trader/cache"):
    """Return F&O lot size for symbol from plan, cached fo_mktlots.csv, or fallback table."""
    if isinstance(plan, dict) and plan.get("lot_size"):
        try:
            ls = int(plan["lot_size"])
            if ls > 0:
                return ls
        except (TypeError, ValueError):
            pass
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


# ---------------------------------------------------------------- inputs

def read_json(path, what):
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise SizeError("no %s at %s" % (what, path))
    except json.JSONDecodeError as e:
        raise SizeError("%s is not valid JSON: %s" % (path, e))


def _migrate_cfg(cfg):
    """Map any legacy _usd keys to _inr keys so nothing breaks."""
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


def load_config(path):
    cfg = dict(DEFAULTS)
    if path and os.path.exists(path):
        raw = read_json(path, "config")
        if isinstance(raw, dict):
            cfg.update(_migrate_cfg(raw))
    return cfg


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def parse_weights(data):
    """Accept a weights file, a {ticker: weight} dict or a list of rows."""
    if isinstance(data, dict) and "weights" in data:
        data = data["weights"]
    rows = []
    if isinstance(data, dict):
        for t, w in data.items():
            rows.append({"ticker": t, "name": "", "weight": w})
    elif isinstance(data, list):
        for i, r in enumerate(data):
            if not isinstance(r, dict):
                raise SizeError("weights[%d] is not an object" % i)
            t = r.get("ticker") or r.get("symbol")
            w = r.get("weight", r.get("weight_pct", r.get("pct")))
            if not t or w is None:
                raise SizeError("weights[%d] needs a ticker and a weight" % i)
            rows.append({"ticker": t, "name": r.get("name", ""), "weight": w})
    else:
        raise SizeError("weights must be a list or an object")
    out = []
    for r in rows:
        try:
            w = float(r["weight"])
        except (TypeError, ValueError):
            raise SizeError("weight for %s is not a number: %r" % (r["ticker"], r["weight"]))
        out.append({"ticker": clean_symbol(r["ticker"]), "name": r["name"] or "", "weight": w})
    return out


def weights_from_arg(text):
    out = {}
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise SizeError("--weights wants TICKER=WEIGHT pairs, got %r" % part)
        t, w = part.split("=", 1)
        out[clean_symbol(t)] = w.strip()
    return out


def load_prices(path):
    if not path:
        return {}
    data = read_json(path, "prices")
    prices = data.get("prices", data) if isinstance(data, dict) else {}
    out = {}
    for t, p in prices.items():
        try:
            out[clean_symbol(t)] = float(p)
        except (TypeError, ValueError):
            continue
    return out


def price_time(doc, sym):
    det_map = (doc or {}).get("detail") or {}
    det = det_map.get(sym) or det_map.get(sym + ".NS") or {}
    for key in ("time", "fetched"):
        t = parse_utc(det.get(key))
        if t:
            return t
    return parse_utc((doc or {}).get("as_of"))


def fresh_price(doc, sym, now_utc, minutes=FRESH_MINUTES):
    if not isinstance(doc, dict):
        return None, None, False
    pmap = doc.get("prices") or {}
    p = _f(pmap.get(sym))
    if p is None:
        p = _f(pmap.get(sym + ".NS"))
    if p is None or p <= 0:
        return None, None, False
    t = price_time(doc, sym)
    if t is None:
        return p, None, False
    age = (now_utc - t).total_seconds()
    return p, t, -60 <= age <= minutes * 60


# ---------------------------------------------------------------- plan mode (default)

def _is_fno(sym, plan, product, segment):
    if str(segment or "").upper() == "FNO" or str(product or "").upper() == "NRML":
        return True
    if isinstance(plan, dict):
        if str(plan.get("segment") or "").upper() == "FNO":
            return True
        if str(plan.get("instrument_type") or "").upper() in ("FUT", "CE", "PE", "OPTIDX", "OPTSTK", "FUTIDX", "FUTSTK"):
            return True
        if int(_f(plan.get("lot_size")) or 1) > 1:
            return True
    # Heuristic for NSE F&O contract symbols like NIFTY26NOV24500CE or SBIN26NOVFUT
    import re
    if re.search(r"\d{2}(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d+(CE|PE)|FUT)$", sym):
        return True
    return False


def size_trade(plan, cfg, budget=None, limit=None, price=None, cash=None, today=None,
               trade_type_override=None, product_override=None):
    """Pure function: one plan + your rules -> sizing dict. qty may be 0."""
    c = dict(DEFAULTS)
    c.update(_migrate_cfg(dict(cfg or {})))
    if not isinstance(plan, dict):
        raise SizeError("the plan is not a JSON object")
    raw_sym = str(plan.get("symbol") or "").strip().upper()
    if not raw_sym:
        raise SizeError("the plan has no symbol (run trader-exit)")
    sym = clean_symbol(raw_sym)
    exchange = str(plan.get("exchange") or ("BSE" if raw_sym.endswith(".BO") else c.get("exchange", "NSE"))).upper()

    side = str(plan.get("side") or plan.get("transaction_type") or "buy").strip().lower()
    if side != "buy":
        raise SizeError("trader-budget sizes long (BUY) trades only; the plan says %r" % side)

    trade_type = str(trade_type_override or plan.get("trade_type") or c.get("trade_type") or "intraday").strip().lower()
    if trade_type not in ("intraday", "swing"):
        raise SizeError("trade_type must be 'intraday' or 'swing', got %r" % trade_type)

    if product_override:
        product = str(product_override).strip().upper()
    elif plan.get("product"):
        product = str(plan.get("product")).strip().upper()
    elif trade_type == "swing":
        product = "CNC"
    else:
        product = str(c.get("product") or "MIS").strip().upper()
        if product == "CNC" and trade_type == "intraday":
            product = "MIS"

    if product not in ("MIS", "CNC", "NRML"):
        raise SizeError("product must be MIS, CNC, or NRML, got %r" % product)

    segment = str(plan.get("segment") or ("FNO" if product == "NRML" else "CASH")).strip().upper()
    is_fno = _is_fno(sym, plan, product, segment)
    if is_fno:
        segment = "FNO"

    if today and plan.get("date") and str(plan["date"]) != today and trade_type == "intraday":
        raise SizeError("the intraday plan is for %s and today is %s in IST: make a new plan with trader-exit"
                        % (plan["date"], today))

    entry_plan, stop_raw, target_raw = _f(plan.get("entry")), _f(plan.get("stop")), _f(plan.get("target"))
    for name, v in (("entry", entry_plan), ("stop", stop_raw), ("target", target_raw)):
        if v is None or v <= 0:
            raise SizeError("the plan needs a positive %s (run trader-exit); an order needs entry, "
                            "stop and target" % name)

    blocked = {clean_symbol(b) for b in (c.get("blocked") or [])}
    if sym in blocked:
        raise SizeError("%s is on your blocked list" % sym)

    budget = float(budget if budget is not None else (c.get("budget") or 0))
    if budget <= 0:
        raise SizeError('no budget: pass --budget or set "budget" in trader/config.json')
    buf_pct = float(c.get("cash_buffer_pct") if c.get("cash_buffer_pct") is not None else 5)
    if not 0 <= buf_pct < 100:
        raise SizeError("cash_buffer_pct must be between 0 and 100")

    if limit is not None:
        ref, ref_from = round_tick(limit), "limit"
    elif price is not None and round_tick(price) > round_tick(entry_plan):
        ref, ref_from = round_tick(price), "price"
    else:
        ref, ref_from = round_tick(entry_plan), "plan"

    stop = floor_tick(stop_raw)
    target = round_tick(target_raw)
    if stop >= ref:
        raise SizeError("the stop %s is not below the entry %s; a long trade needs its stop underneath"
                        % (money(stop), money(ref)))
    if target <= ref:
        raise SizeError("the target %s is not above the entry %s: the move has already happened. "
                        "Skip it or make a new plan with trader-exit" % (money(target), money(ref)))

    risk_p = int(round((ref - stop) * 100))
    reward_p = int(round((target - ref) * 100))
    risk = risk_p / 100.0
    reward_ps = reward_p / 100.0

    cash_buffer = round_paise(budget * buf_pct / 100.0)
    available = budget - cash_buffer
    cash_note = "budget %s minus %s (%s%%) cash buffer" % (money(budget), money(cash_buffer), _num(buf_pct))
    if cash is not None and cash - cash_buffer < available - 0.005:
        available = cash - cash_buffer
        cash_note = "cash %s in trader/portfolio.json minus %s buffer" % (money(cash), money(cash_buffer))
    available = max(round_paise(available), 0.0)

    include_charges = bool(c.get("include_charges", True))
    lot_size = get_lot_size(sym, plan) if is_fno else int(_f(plan.get("lot_size")) or 1)
    lot_size = max(1, lot_size)

    def _lot_floor(n):
        return (max(0, int(n)) // lot_size) * lot_size

    limits = []
    max_loss_cap = _f(c.get("max_loss_per_trade_inr"))
    if max_loss_cap:
        sh_loss = max_shares_for_loss(max_loss_cap, ref, stop, product, include_charges, lot_size)
        ch_1 = estimate_charges(lot_size, ref, stop, product)["total"] if include_charges else 0.0
        how_loss = "%s max loss / (%s/sh risk%s)" % (
            money(max_loss_cap), money(risk),
            (" + est. charges") if include_charges else "",
        )
        limits.append({
            "name": "max loss", "inr": max_loss_cap, "per": risk,
            "shares": sh_loss, "how": how_loss, "one_lot_charges": ch_1,
        })
    cap_pct = _f(c.get("max_position_pct"))
    if cap_pct:
        cap = round_paise(budget * cap_pct / 100.0)
        limits.append({
            "name": "position cap", "inr": cap, "per": ref,
            "shares": _lot_floor(shares_for(cap, ref)),
            "how": "%s%% of %s = %s / %s" % (_num(cap_pct), money(budget), money(cap), money(ref)),
        })
    max_order = _f(c.get("max_order_inr"))
    if max_order:
        limits.append({
            "name": "max order", "inr": max_order, "per": ref,
            "shares": _lot_floor(shares_for(max_order, ref)),
            "how": "%s / %s" % (money(max_order), money(ref)),
        })
    limits.append({
        "name": "cash", "inr": available, "per": ref,
        "shares": _lot_floor(shares_for(available, ref)),
        "how": "%s = %s / %s" % (cash_note, money(available), money(ref)),
    })

    qty = max(0, min(l["shares"] for l in limits))
    bound = [l["name"] for l in limits if l["shares"] == qty]
    for l in limits:
        l["binds"] = l["name"] == bound[0]

    cost = round_paise(qty * ref)
    price_loss = round_paise(qty * risk)
    charges_stop = estimate_charges(qty, ref, stop, product) if qty > 0 else estimate_charges(0, ref, stop, product)
    charges_target = estimate_charges(qty, ref, target, product) if qty > 0 else estimate_charges(0, ref, target, product)
    est_charges_inr = charges_stop["total"] if include_charges else 0.0
    total_max_loss = round_paise(price_loss + est_charges_inr)
    gross_reward = round_paise(qty * reward_ps)
    net_reward = round_paise(gross_reward - (charges_target["total"] if include_charges else 0.0))
    charges_pct_reward = round(100.0 * charges_target["total"] / gross_reward, 1) if gross_reward > 0 else 0.0

    refusal = None
    if is_fno and not c.get("allow_fno", False):
        one_lot_cost = round_paise(lot_size * ref)
        refusal = (
            "F&O is disabled (\"allow_fno\": false in trader/config.json). %s (%s) requires a minimum of "
            "1 lot (%d shares = %s turnover), which is blocked for a %s capital account."
            % (sym, product, lot_size, money(one_lot_cost), money(budget))
        )
        qty = 0
    elif qty == 0:
        if bound[0] == "max loss":
            one_ch = estimate_charges(lot_size, ref, stop, product)["total"] if include_charges else 0.0
            one_loss = round_paise(lot_size * risk + one_ch)
            unit_lbl = "1 lot (%d shares)" % lot_size if lot_size > 1 else "1 share"
            refusal = (
                "%s of %s risks %s at the stop (%s price risk%s), more than your %s max loss per trade. "
                "No order. Skip this trade, or plan a closer stop with trader-exit."
                % (
                    unit_lbl, sym, money(one_loss), money(lot_size * risk),
                    (" + %s est. charges" % money(one_ch)) if include_charges else "",
                    money(max_loss_cap),
                )
            )
        else:
            unit_lbl = "1 lot (%d shares)" % lot_size if lot_size > 1 else "1 share"
            refusal = (
                "%s of %s costs %s, more than your %s limit (%s). No order."
                % (unit_lbl, sym, money(lot_size * ref), bound[0],
                   limits[[l["name"] for l in limits].index(bound[0])]["how"])
            )
    elif _f(c.get("min_reward_risk")) and reward_p < _f(c["min_reward_risk"]) * risk_p - EPS:
        refusal = (
            "reward:risk %.2f (target %s, entry %s, stop %s) is under your minimum %s, so trader-risk "
            "would block it. No order. Skip it, or make a new plan with trader-exit."
            % (reward_p / float(risk_p), money(target), money(ref), money(stop), _num(c["min_reward_risk"]))
        )
    elif cost < float(c.get("min_order_inr") or 0) - 0.005:
        refusal = (
            "%d share%s of %s is %s, under your %s minimum order. No order."
            % (qty, "" if qty == 1 else "s", sym, money(cost), money(c["min_order_inr"]))
        )

    flat_by = (plan.get("flat_by") or c.get("flat_by") or "15:10") if trade_type == "intraday" else None
    return {
        "symbol": sym,
        "yahoo": yahoo_symbol(sym, exchange),
        "exchange": exchange,
        "segment": segment,
        "product": product,
        "trade_type": trade_type,
        "lot_size": lot_size,
        "side": "buy",
        "setup": plan.get("setup", ""),
        "date": plan.get("date"),
        "entry_time": plan.get("entry_time", ""),
        "entry_plan": round_tick(entry_plan),
        "entry": ref,
        "entry_from": ref_from,
        "stop": stop,
        "target": target,
        "risk_per_share": risk,
        "reward_per_share": reward_ps,
        "reward_risk": round(reward_p / float(risk_p), 2),
        "stop_label": plan.get("stop_label", ""),
        "target_label": plan.get("target_label", ""),
        "budget": budget,
        "cash_buffer_pct": buf_pct,
        "cash_buffer": cash_buffer,
        "cash_available": available,
        "max_loss_per_trade_inr": max_loss_cap,
        "include_charges": include_charges,
        "limits": limits,
        "qty": qty,
        "bound_by": bound[0],
        "cost": cost,
        "price_loss_inr": price_loss,
        "est_charges_inr": est_charges_inr,
        "charges_breakdown": charges_stop,
        "charges_at_target": charges_target,
        "max_loss": total_max_loss,
        "max_loss_inr": total_max_loss,
        "gross_reward_inr": gross_reward,
        "net_reward_inr": net_reward,
        "charges_pct_of_reward": charges_pct_reward,
        "flat_by": flat_by,
        "swing_max_hold_days": c.get("swing_max_hold_days", 10) if trade_type == "swing" else None,
        "order_type": "LIMIT" if limit is not None else "MARKET",
        "refusal": refusal,
    }


def indian_order(s):
    """Build the broker-neutral Indian order dict for trader/orders.json."""
    if s["refusal"] or s["qty"] <= 0:
        return None
    setup = SETUP_WORDS.get(s["setup"], s["setup"] or ("%s trade" % s["trade_type"]))
    bound_map = {
        "max loss": "%s max loss per trade" % money(s["max_loss_per_trade_inr"] or 0),
        "position cap": "position cap",
        "max order": "max order",
        "cash": "cash after the buffer",
    }
    ch_str = (" + %s est. charges" % money(s["est_charges_inr"])) if s["include_charges"] else ""
    why = (
        "%s on %s (%s %s): %d share%s, bound by your %s (%d x %s%s = %s at the stop)"
        % (
            setup, s["symbol"], s["exchange"], s["product"],
            s["qty"], "" if s["qty"] == 1 else "s",
            bound_map.get(s["bound_by"], s["bound_by"]),
            s["qty"], money(s["risk_per_share"]), ch_str, money(s["max_loss_inr"]),
        )
    )
    o = {
        "symbol": s["symbol"],
        "exchange": s["exchange"],
        "segment": s["segment"],
        "product": s["product"],
        "transaction_type": "BUY",
        "order_type": s["order_type"],
        "quantity": int(s["qty"]),
        "price": round_tick(s["entry"]) if s["order_type"] == "LIMIT" else 0.0,
        "trigger_price": None,
        "validity": "DAY",
        "stop_loss": s["stop"],
        "target": s["target"],
        "trade_type": s["trade_type"],
        "lot_size": s["lot_size"],
        "est_charges_inr": s["est_charges_inr"],
        "price_loss_inr": s["price_loss_inr"],
        "max_loss_inr": s["max_loss_inr"],
        "gross_reward_inr": s["gross_reward_inr"],
        "net_reward_inr": s["net_reward_inr"],
        "charges_pct_of_reward": s["charges_pct_of_reward"],
        "why": why,
        "plan": {
            "entry": s["entry"],
            "stop": s["stop"],
            "target": s["target"],
            "risk_per_share": s["risk_per_share"],
            "price_loss_inr": s["price_loss_inr"],
            "est_charges_inr": s["est_charges_inr"],
            "max_loss": s["max_loss_inr"],
            "max_loss_inr": s["max_loss_inr"],
            "setup": s["setup"],
            "entry_time": s["entry_time"],
            "flat_by": s["flat_by"],
            "trade_type": s["trade_type"],
            "product": s["product"],
            "exchange": s["exchange"],
            "segment": s["segment"],
            "lot_size": s["lot_size"],
        },
    }
    return o


def plan_report(s, order, notes=()):
    lines = []
    setup = SETUP_WORDS.get(s["setup"], s["setup"] or "setup")
    lines.append(
        "%s BUY (%s %s, %s), %s, entry %s%s"
        % (
            s["symbol"], s["exchange"], s["product"], s["trade_type"],
            setup, money(s["entry_plan"]),
            (" at " + s["entry_time"] + " IST") if s["entry_time"] else "",
        )
    )
    lines.append(
        "Stop %s%s. Target %s%s."
        % (
            money(s["stop"]), (": " + s["stop_label"]) if s["stop_label"] else "",
            money(s["target"]), (": " + s["target_label"]) if s["target_label"] else "",
        )
    )
    for n in notes:
        lines.append(n)
    src = {
        "plan": "the plan's entry",
        "price": "the latest price, above the plan's entry",
        "limit": "your limit price",
    }[s["entry_from"]]
    lines.append(
        "Sized at %s (%s). Price risk %s/share (entry %s minus stop %s). Gross reward:risk %.2f."
        % (money(s["entry"]), src, money(s["risk_per_share"]), money(s["entry"]), money(s["stop"]), s["reward_risk"])
    )
    lines.append("")
    lines.append("Limits (whole shares%s; the smallest one wins):" % (
        ", lot size %d" % s["lot_size"] if s["lot_size"] > 1 else ""
    ))
    for l in s["limits"]:
        lines.append("  %-13s %-58s %4d shares%s" % (
            l["name"], l["how"], l["shares"], "  <- binds" if l["binds"] else ""
        ))
    lines.append("")
    if order is None:
        lines.append("No order: " + (s["refusal"] or "size is 0 shares."))
        lines.append(DISCLAIMER)
        return "\n".join(lines)

    ch = s["charges_breakdown"]
    lines.append(
        "Size: %d share%s of %s = %s at %s (%s, %s)."
        % (
            s["qty"], "" if s["qty"] == 1 else "s", s["symbol"],
            money(s["cost"]), money(s["entry"]), s["product"], s["trade_type"],
        )
    )
    lines.append(
        "Estimated charges (round-trip): %s (brokerage %s, STT %s, exch+SEBI %s, stamp %s, GST %s%s)."
        % (
            money(ch["total"]), money(ch["brokerage"]), money(ch["stt"]),
            money(ch["exchange_txn"] + ch["sebi"]), money(ch["stamp"]), money(ch["gst"]),
            (", DP %s" % money(ch["dp"])) if ch["dp"] > 0 else "",
        )
    )
    lines.append(
        "Max loss at stop: %d x %s = %s price loss + %s charges = %s (limit %s)."
        % (
            s["qty"], money(s["risk_per_share"]), money(s["price_loss_inr"]),
            money(s["est_charges_inr"]), money(s["max_loss_inr"]),
            money(s["max_loss_per_trade_inr"] or 0),
        )
    )
    lines.append(
        "Target reward: %s gross, %s net after %s charges (charges eat %.1f%% of gross reward)."
        % (
            money(s["gross_reward_inr"]), money(s["net_reward_inr"]),
            money(s["charges_at_target"]["total"]), s["charges_pct_of_reward"],
        )
    )
    if s["charges_pct_of_reward"] >= 20.0 or s["net_reward_inr"] <= 0:
        lines.append(
            "WARNING: With %s capital and a %s order, statutory & broker charges eat %.1f%% of your "
            "gross target reward (%s of %s)%s!"
            % (
                money(s["budget"]), s["product"], s["charges_pct_of_reward"],
                money(s["charges_at_target"]["total"]), money(s["gross_reward_inr"]),
                " (CNC delivery includes ₹15.93 DP sell charge + ₹5 min brokerage per leg)"
                if s["product"] == "CNC" else "",
            )
        )
    lines.append("A stop is an order, not a promise: in a fast market or circuit gap it can fill below %s." % money(s["stop"]))
    lines.append("")
    lines.append("Proposed Indian order (broker-neutral, nothing has been placed):")
    how = "LIMIT %s" % money(order["price"]) if order["order_type"] == "LIMIT" else "MARKET"
    lines.append(
        "  %s %d %s (%s %s, %s), %s, %s, stop_loss %s, target %s"
        % (
            order["transaction_type"], order["quantity"], order["symbol"],
            order["exchange"], order["segment"], order["product"],
            how, order["validity"], money(order["stop_loss"]), money(order["target"]),
        )
    )
    if s["flat_by"]:
        lines.append(
            "Flat by %s IST: intraday MIS positions auto square-off around 15:15-15:20; close by %s IST "
            "(trader-alerts rings, trader-broker flatten)." % (s["flat_by"], s["flat_by"])
        )
    elif s["trade_type"] == "swing":
        lines.append(
            "Swing delivery (CNC): no intraday flat_by; max planned hold %d trading days."
            % (s["swing_max_hold_days"] or 10)
        )
    lines.append("")
    lines.append("Next: trader-risk checks it, then trader-broker asks you yes or no.")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


# ---------------------------------------------------------------- weights mode

def normalise(rows, blocked=(), top=None):
    blocked = {clean_symbol(b) for b in (blocked or [])}
    dropped, keep, seen = [], [], set()
    for r in rows:
        if r["ticker"] in seen:
            raise SizeError("%s is listed twice in the weights" % r["ticker"])
        seen.add(r["ticker"])
        if r["ticker"] in blocked:
            dropped.append({"ticker": r["ticker"], "reason": "on your blocked list"})
        elif r["weight"] <= 0:
            dropped.append({"ticker": r["ticker"], "reason": "weight is zero or negative"})
        else:
            keep.append(dict(r))
    keep.sort(key=lambda r: -r["weight"])
    if top:
        for r in keep[top:]:
            dropped.append({"ticker": r["ticker"], "reason": "outside the top %d" % top})
        keep = keep[:top]
    total = sum(r["weight"] for r in keep)
    for r in keep:
        r["source_weight"] = r["weight"]
        r["weight"] = 100.0 * r["weight"] / total if total else 0.0
    return keep, dropped


def allocate(rows, investable, cap):
    alloc, capped = {}, set()
    active = [r for r in rows]
    money_left = investable
    while active:
        wsum = sum(r["weight"] for r in active)
        trial = {r["ticker"]: money_left * r["weight"] / wsum for r in active}
        over = [r for r in active if trial[r["ticker"]] > cap + EPS]
        if not over:
            alloc.update(trial)
            break
        for r in over:
            alloc[r["ticker"]] = cap
            capped.add(r["ticker"])
            money_left -= cap
        active = [r for r in active if r["ticker"] not in capped]
        if money_left <= EPS:
            for r in active:
                alloc[r["ticker"]] = 0.0
            break
    return alloc, capped


def round_to_unit(exact, capped, order, cap, unit):
    def units(x):
        return int(math.floor(x / unit + EPS))

    cap_u = units(cap)
    total_u = units(sum(exact.values()))
    out = {}
    for t, v in exact.items():
        out[t] = cap_u if t in capped else int(math.floor(v / unit + 0.5 + EPS))
    diff = total_u - sum(out.values())
    takers = [t for t in order if t not in capped]
    guard = 0
    while diff != 0 and takers and guard < 100000:
        moved = False
        for t in takers:
            if diff == 0:
                break
            if diff > 0 and out[t] + 1 <= cap_u:
                out[t] += 1
                diff -= 1
                moved = True
            elif diff < 0 and out[t] > 0:
                out[t] -= 1
                diff += 1
                moved = True
        if not moved:
            break
        guard += 1
    return {t: round(u * unit, 2) for t, u in out.items()}


def whole_shares(inr_slices, prices, order, cap, cash):
    shares = {}
    for t in order:
        shares[t] = int(math.floor(inr_slices[t] / prices[t] + EPS)) if inr_slices[t] > 0 else 0
    spent = sum(shares[t] * prices[t] for t in order)
    left = cash - spent
    while True:
        best, gap = None, None
        for t in order:
            p = prices[t]
            if p > left + EPS or (shares[t] + 1) * p > cap + EPS:
                continue
            g = inr_slices[t] - shares[t] * p
            if best is None or g > gap + EPS:
                best, gap = t, g
        if best is None:
            break
        shares[best] += 1
        left -= prices[best]
    return shares


def size(rows, cfg, budget=None, prices=None, whole=None, paise=False, top=None):
    """Weights mode. Pure function: weights + rules -> full sizing result (a dict).
    Indian equities cannot be bought in fractional shares, so whole_shares defaults to True."""
    c = dict(DEFAULTS)
    c.update(_migrate_cfg(dict(cfg or {})))
    budget = float(budget if budget is not None else (c.get("budget") or 0))
    if budget <= 0:
        raise SizeError('no budget: pass --budget or set "budget" in trader/config.json')
    buf_pct = float(c.get("cash_buffer_pct", 5))
    cap_pct = float(c.get("max_position_pct", 100))
    min_order = float(c.get("min_order_inr", 50) or 0)
    if not 0 <= buf_pct < 100:
        raise SizeError("cash_buffer_pct must be between 0 and 100")
    if not 0 < cap_pct <= 100:
        raise SizeError("max_position_pct must be above 0 and at most 100")
    prices = prices or {}
    if whole is None:
        whole = bool(prices)

    keep, dropped = normalise(rows, c.get("blocked"), top)
    if not keep:
        raise SizeError("nothing to size: no weights left after the blocked list")

    cash_buffer = round_paise(budget * buf_pct / 100.0)
    investable = round_paise(budget - cash_buffer)
    cap = round_paise(budget * cap_pct / 100.0)
    unit = 0.01 if (paise or whole) else 1.0

    if whole:
        missing = [r["ticker"] for r in keep if not prices.get(r["ticker"])]
        if missing:
            raise SizeError(
                "whole shares need a price for: %s (add them to trader/prices.json, "
                "e.g. with trader-alerts: alerts.py quote %s --write)"
                % (", ".join(missing), " ".join(missing))
            )

    while True:
        live = [dict(r) for r in keep]
        tot = sum(r["source_weight"] for r in live)
        for r in live:
            r["weight"] = 100.0 * r["source_weight"] / tot
        exact, capped = allocate(live, investable, cap)
        order = [r["ticker"] for r in sorted(live, key=lambda r: -exact[r["ticker"]])]
        inr_map = round_to_unit(exact, capped, order, cap, unit)
        shares = None
        if whole:
            shares = whole_shares(inr_map, prices, order, cap, sum(inr_map.values()))
            inr_map = {t: round_paise(shares[t] * prices[t]) for t in order}
        small = [r for r in live if inr_map[r["ticker"]] < min_order - EPS]
        if not small:
            break
        worst = min(small, key=lambda r: (r["source_weight"], r["ticker"]))
        if whole and shares is not None and shares[worst["ticker"]] == 0 and prices.get(worst["ticker"], 0) > 0:
            reason = (
                "one share costs %s, more than its slice; it cannot be bought in whole shares"
                % money(prices[worst["ticker"]])
            )
        else:
            reason = "its slice (%s) is under your %s minimum order" % (
                money(inr_map[worst["ticker"]]), money(min_order)
            )
        dropped.append({"ticker": worst["ticker"], "reason": reason})
        keep = [r for r in keep if r["ticker"] != worst["ticker"]]
        if not keep:
            raise SizeError("every slice is under your minimum order; raise the budget or hold fewer stocks")

    positions = []
    for r in sorted(live, key=lambda r: -r["weight"]):
        t = r["ticker"]
        p = {
            "ticker": t, "name": r["name"], "weight": round(r["weight"], 2),
            "source_weight": r["source_weight"], "amount_inr": inr_map[t],
            "capped": t in capped,
        }
        if prices.get(t):
            p["price"] = prices[t]
            p["shares"] = int(shares[t]) if whole else int(math.floor(inr_map[t] / prices[t]))
        positions.append(p)
    invested = round_paise(sum(p["amount_inr"] for p in positions))
    return {
        "budget": budget, "cash_buffer_pct": buf_pct, "cash_buffer": cash_buffer,
        "investable": investable, "max_position_pct": cap_pct, "cap": cap,
        "min_order_inr": min_order, "whole_shares": bool(whole),
        "positions": positions, "invested": invested,
        "idle": round_paise(investable - invested), "dropped": dropped,
    }


def orders_from(result, cfg, source=""):
    """Weights mode: proposed CNC delivery buy orders in Indian order shape."""
    out = []
    exchange = str(cfg.get("exchange", "NSE")).upper()
    for p in result["positions"]:
        if p["amount_inr"] <= 0:
            continue
        qty = int(p.get("shares") or 0)
        if result["whole_shares"] and qty <= 0:
            continue
        why = "%.2f%% of the split" % p["weight"]
        if source:
            why = "%s, weights from %s" % (why, source)
        if p["capped"]:
            why += "; capped at %s%% of the %s budget" % (_num(result["max_position_pct"]), money(result["budget"]))
        o = {
            "symbol": p["ticker"],
            "exchange": exchange,
            "segment": "CASH",
            "product": "CNC",
            "transaction_type": "BUY",
            "order_type": "MARKET",
            "quantity": qty if qty > 0 else 1,
            "price": round_tick(p["price"]) if p.get("price") else 0.0,
            "trigger_price": None,
            "validity": "DAY",
            "stop_loss": None,
            "target": None,
            "trade_type": "swing",
            "lot_size": 1,
            "amount_inr": p["amount_inr"],
            "why": why,
        }
        out.append(o)
    return out


def report(result, orders, source=""):
    r = result
    lines = []
    if source:
        lines.append("Weights from: %s" % source)
    lines.append(
        "Budget %s, cash buffer %s%% = %s, investing %s, cap %s%% = %s per stock"
        % (
            money(r["budget"]), _num(r["cash_buffer_pct"]), money(r["cash_buffer"]),
            money(r["investable"]), _num(r["max_position_pct"]), money(r["cap"]),
        )
    )
    lines.append("")
    has_price = any("price" in p for p in r["positions"])
    head = "%-10s %-24s %8s %12s" % ("symbol", "name", "weight", "amount")
    if has_price:
        head += " %11s %8s" % ("price", "shares")
    lines.append(head + "  note")
    for p in r["positions"]:
        row = "%-10s %-24s %7.2f%% %12s" % (
            p["ticker"], (p["name"] or "")[:24], p["weight"], money(p["amount_inr"])
        )
        if has_price:
            row += " %11s %8s" % (money(p["price"]) if "price" in p else "", p.get("shares", ""))
        if p["capped"]:
            row += "  capped at %s%%" % _num(r["max_position_pct"])
        lines.append(row)
    lines.append("%-10s %-24s %8s %12s" % ("", "invested", "", money(r["invested"])))
    lines.append("%-10s %-24s %8s %12s" % ("", "cash buffer", "", money(r["cash_buffer"])))
    lines.append("%-10s %-24s %8s %12s" % ("", "left idle", "", money(r["idle"])))
    if r["idle"] >= 1:
        lines.append("")
        lines.append(
            "Left idle %s: %s"
            % (
                money(r["idle"]),
                "whole shares do not divide evenly" if r["whole_shares"]
                else "every stock is at its cap; add stocks or raise max_position_pct",
            )
        )
    for d in r["dropped"]:
        lines.append("Dropped %s: %s" % (d["ticker"], d["reason"]))
    lines.append("")
    lines.append("Proposed orders (Indian equity CNC delivery, nothing has been placed):")
    for o in orders:
        lines.append(
            "  %s %d %s (%s %s), %s, %s (~%s)"
            % (
                o["transaction_type"], o["quantity"], o["symbol"],
                o["exchange"], o["product"], o["order_type"], o["validity"],
                money(o.get("amount_inr", 0)),
            )
        )
    lines.append("")
    lines.append('These buys have no stop. trader-risk blocks them while "require_stop" is true.')
    lines.append("Next: trader-risk checks every order, then trader-broker asks you yes or no for each one.")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


# ---------------------------------------------------------------- orders file

def write_orders(path, orders, cfg, source="", now_utc=None):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    doc = {
        "created_by": "trader-budget",
        "created_at": utc_iso(now_utc),
        "now_ist": ist_iso(now_utc),
        "status": "proposed",
        "paper": bool(cfg.get("paper", True)),
        "broker": cfg.get("broker", "groww"),
        "exchange": cfg.get("exchange", "NSE"),
        "note": (
            "Proposals only. Nothing is placed until trader-risk passes each order "
            "and you say yes to it in the chat."
        ),
        "source": source,
        "orders": orders,
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


# ---------------------------------------------------------------- main

def run_plan(a, cfg):
    now = parse_now(a.now)
    today = to_ist(now).date().isoformat()
    plan = read_json(a.plan, "plan (run trader-exit first, or use --weights for a split)")
    limit = None
    if a.limit is not None:
        if a.limit == "plan":
            limit = _f((plan or {}).get("entry"))
        else:
            limit = _f(a.limit)
            if limit is None or limit <= 0:
                raise SizeError("--limit wants a price, like --limit 820.00")
    sym = clean_symbol((plan or {}).get("symbol") or "")
    notes, price = [], None
    ppath = a.prices or ("trader/prices.json" if os.path.exists("trader/prices.json") else None)
    if ppath and limit is None and sym:
        pdoc = read_json(ppath, "prices")
        p, t, fresh = fresh_price(pdoc, sym, now)
        if p is not None and fresh:
            price = p
            if round_tick(p) > round_tick(_f(plan.get("entry")) or 0):
                notes.append(
                    "%s last %s (as of %s IST) is above the plan's entry %s, so the risk "
                    "is bigger: sized at %s."
                    % (sym, money(p), to_ist(t).strftime("%H:%M"), money(plan.get("entry")), money(p))
                )
        elif p is not None:
            notes.append(
                "%s in %s is %s: not used (older than %d minutes). Sized at the plan's entry."
                % (
                    sym, ppath,
                    ("from " + to_ist(t).strftime("%Y-%m-%d %H:%M") + " IST") if t else "undated",
                    FRESH_MINUTES,
                )
            )
    cash = None
    if a.portfolio and os.path.exists(a.portfolio):
        pf = read_json(a.portfolio, "portfolio")
        cash = _f(pf.get("cash")) if isinstance(pf, dict) else None
    s = size_trade(
        plan, cfg, a.budget, limit, price, cash, today,
        trade_type_override=a.trade_type, product_override=a.product,
    )
    order = indian_order(s)
    if order and a.write:
        src = "trader/plan.json (%s %s %s)" % (s["symbol"], s["setup"] or "plan", s["date"] or today)
        write_orders(a.write, [order], cfg, src, now)
    if a.json:
        print(json.dumps({"sizing": s, "orders": [order] if order else [], "notes": notes}, indent=2, ensure_ascii=False))
    else:
        print(plan_report(s, order, notes))
        if order and a.write:
            print("Wrote 1 proposed order to %s" % a.write)
    return 0 if order else 1


def run_weights(a, cfg):
    source = ""
    if a.weights:
        rows = parse_weights(weights_from_arg(a.weights))
    else:
        data = read_json(a.targets, "weights file")
        rows = parse_weights(data)
        if isinstance(data, dict):
            source = data.get("source", "")
    prices = load_prices(a.prices)
    if not prices and os.path.exists("trader/prices.json") and not a.prices:
        prices = load_prices("trader/prices.json")
    whole = True if a.whole_shares else (True if prices else False)
    result = size(rows, cfg, a.budget, prices, whole, a.paise, a.top)
    orders = orders_from(result, cfg, source)
    if a.write:
        write_orders(a.write, orders, cfg, source, parse_now(a.now))
    if a.json:
        print(json.dumps({"result": result, "orders": orders}, indent=2, ensure_ascii=False))
    else:
        print(report(result, orders, source))
        if a.write:
            print("Wrote %d proposed orders to %s" % (len(orders), a.write))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Size an Indian equity trade to your budget and max loss (trader-budget).")
    ap.add_argument("--plan", default="trader/plan.json", help="the trade plan (default trader/plan.json)")
    ap.add_argument("--config", default="trader/config.json", help="rules (default trader/config.json)")
    ap.add_argument("--budget", type=float, help="override the budget (in INR) in the config")
    ap.add_argument("--limit", nargs="?", const="plan", metavar="PRICE",
                    help="a LIMIT order instead of MARKET, at the plan's entry or at PRICE")
    ap.add_argument("--trade-type", choices=["intraday", "swing"], help="override trade_type (intraday or swing)")
    ap.add_argument("--product", choices=["MIS", "CNC", "NRML"], help="override product (MIS, CNC, NRML)")
    ap.add_argument("--prices", help="prices JSON (default trader/prices.json if it exists)")
    ap.add_argument("--portfolio", default="trader/portfolio.json",
                    help="your account; its cash caps the size if it exists")
    ap.add_argument("--now", help='pretend it is this IST time, e.g. "2026-10-30 10:15"')
    ap.add_argument("--write", nargs="?", const="trader/orders.json", metavar="PATH",
                    help="write the proposed order (default trader/orders.json)")
    ap.add_argument("--json", action="store_true", help="print JSON instead of the report")
    g = ap.add_argument_group("weights mode (split a budget across a list)")
    g.add_argument("--weights", help="inline weights: HDFCBANK=28.5,RELIANCE=22.1")
    g.add_argument("--targets", help='a weights file: {"weights": [{"ticker", "weight"}]}')
    g.add_argument("--top", type=int, help="keep only the N biggest weights")
    g.add_argument("--whole-shares", action="store_true", help="whole shares only (needs --prices)")
    g.add_argument("--paise", "--cents", dest="paise", action="store_true",
                   help="size to the paisa instead of whole rupees")
    a = ap.parse_args(argv)
    try:
        cfg = load_config(a.config)
        if a.weights or a.targets:
            return run_weights(a, cfg)
        if a.top or a.whole_shares or a.paise:
            raise SizeError("--top, --whole-shares and --paise work with --weights or --targets")
        return run_plan(a, cfg)
    except SizeError as e:
        print("size.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
