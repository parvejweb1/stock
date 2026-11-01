#!/usr/bin/env python3
"""Order tickets, paper simulation, and live broker SDK execution for trader-broker (India / NSE & BSE).

Supports the 4 Indian brokers the user has demat accounts with:
  - groww     (growwapi: GrowwAPI.place_order / GrowwAPI.create_smart_order)
  - zerodha   (kiteconnect: KiteConnect.place_order / KiteConnect.place_gtt)
  - upstox    (upstox_client v2/v3: OrderApi.place_order / OrderApiV3.place_gtt_order)
  - angelone  (SmartApi + pyotp: SmartConnect.placeOrder / SmartConnect.gttCreateRule)

Broker SDKs are imported LAZILY only when placing a LIVE order ("paper": false),
so paper mode works with the Python standard library alone.

None of these Indian brokers provide a paper trading account. Therefore:
  - PAPER mode (default, "paper": true): local simulation. After trader-risk
    passes and you record an explicit "yes" via `approve`, `execute` fills the
    order locally using the latest Yahoo Finance NSE/BSE price (or trader/prices.json
    / limit / plan entry when offline or market closed), deducts estimated Indian
    charges, updates trader/portfolio.json, and logs the trade in trader/state/broker.json.
  - LIVE mode ("paper": false): after trader-risk passes and you record your
    explicit "yes" via `approve`, `execute` reads credentials ONLY from environment
    variables and calls the selected broker's official Python SDK.

Commands:
    python ticket.py show [--broker groww]           # every order, READY or not
    python ticket.py show --index 1 --json           # one ticket as JSON
    python ticket.py approve --index 1 --reply "yes" # record explicit yes for order #1
    python ticket.py execute --index 1               # paper: simulate fill; live: call broker SDK
    python ticket.py result --index 1 --status placed --broker-id ID --filled-qty 1 --filled-price 820.0
    python ticket.py fill --symbol SBIN --side buy --qty 1 --price 820.0
    python ticket.py flatten --symbol SBIN --qty 1   # build close/flatten SELL ticket by flat_by (15:10 IST)
    python ticket.py log                             # today's decisions and fills

Educational tool, not SEBI-registered investment advice. Trading loses most people money.
"""

import argparse
import datetime
import hashlib
import json
import math
import os
import re
import sys
import urllib.parse
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

# Identical fingerprint field list as trader-risk/check.py
FINGERPRINT_FIELDS = (
    "symbol", "exchange", "segment", "product", "transaction_type",
    "order_type", "quantity", "price", "trigger_price", "validity",
    "stop_loss", "target", "trade_type",
    "side", "qty", "notional", "type", "time_in_force", "limit_price",
    "stop_price", "order_class", "take_profit_limit_price", "stop_loss_stop_price",
)

BROKERS = ("groww", "zerodha", "upstox", "angelone")
BROKER_LABELS = {
    "groww": "Groww (growwapi)",
    "zerodha": "Zerodha Kite (kiteconnect)",
    "upstox": "Upstox (upstox_client v2)",
    "angelone": "Angel One SmartAPI (SmartApi + pyotp)",
}
BROKER_ENV_VARS = {
    "groww": ["GROWW_ACCESS_TOKEN (or GROWW_API_KEY + GROWW_API_SECRET / GROWW_TOTP_SECRET)"],
    "zerodha": ["KITE_API_KEY", "KITE_ACCESS_TOKEN (daily login token)"],
    "upstox": ["UPSTOX_ACCESS_TOKEN (daily login OAuth token)"],
    "angelone": ["ANGEL_API_KEY", "ANGEL_CLIENT_ID", "ANGEL_PIN", "ANGEL_TOTP_SECRET"],
}

VALIDITY_WORDS = {
    "DAY": "good for today (DAY)",
    "IOC": "immediate or cancel (IOC)",
    "GTC": "good till triggered/cancelled",
}
YES_WORDS = ("yes", "y")
BATCH_WORDS = {"all", "every", "everything", "rest", "remaining", "them", "both", "batch", "each"}
DOUBT_WORDS = {
    "no", "not", "dont", "don't", "wait", "cancel", "stop", "but", "maybe", "later",
    "hold", "unless", "if",
}
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
DISCLAIMER = "Educational tool, not SEBI-registered investment advice. Trading loses most people money."


class TicketError(Exception):
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
        raise TicketError('--now wants a time like "2026-10-30 10:15" (IST) or ...Z (UTC)')
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def ist_iso(now_utc):
    """naive UTC -> '2026-10-30T10:15:00+05:30' (IST wall clock)."""
    return "%s+05:30" % to_ist(now_utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------- files & formatting

def read_json(path, what, required=True):
    if not path or not os.path.exists(path):
        if required:
            raise TicketError("no %s at %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise TicketError("%s is not valid JSON: %s" % (path, e))


def write_json(path, data):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def rec_day(rec):
    return str(rec.get("date") or str(rec.get("at", ""))[:10])


def clean_symbol(raw):
    s = str(raw or "").strip().upper()
    for suf in (".NS", ".BO"):
        if s.endswith(suf):
            s = s[:-len(suf)]
            break
    return s


def yahoo_symbol(sym, exchange="NSE"):
    s = clean_symbol(sym)
    if s.startswith("^"):
        return s
    return s + (".BO" if str(exchange).upper() == "BSE" else ".NS")


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


def _money(v):
    f = float(v)
    if abs(f - round(f)) < 0.005:
        return "₹" + _indian_grouping(str(int(round(f))))
    whole, frac = ("%.2f" % abs(f)).split(".")
    sign = "-" if f < 0 else ""
    return sign + "₹" + _indian_grouping(whole) + "." + frac


def _p(v):
    return "%.2f" % float(v)


def _str_num(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        f = float(v)
        return str(int(f)) if f == int(f) else repr(round(f, 2))
    return str(v)


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- order helpers

def fingerprint(order):
    """Identical fingerprint function as trader-risk/check.py."""
    core = {k: order[k] for k in FINGERPRINT_FIELDS if k in order and order[k] is not None}
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def norm_order(order, cfg=None):
    """Return normalized Indian order dict from an order entry."""
    cfg = cfg or {}
    plan = order.get("plan") if isinstance(order.get("plan"), dict) else {}
    sym = clean_symbol(order.get("symbol", "?"))
    exchange = str(order.get("exchange") or plan.get("exchange") or cfg.get("exchange", "NSE")).upper()
    trade_type = str(order.get("trade_type") or plan.get("trade_type") or cfg.get("trade_type", "intraday")).lower()
    product = str(order.get("product") or plan.get("product") or ("CNC" if trade_type == "swing" else cfg.get("product", "MIS"))).upper()
    segment = str(order.get("segment") or plan.get("segment") or ("FNO" if product == "NRML" else "CASH")).upper()
    tx = str(order.get("transaction_type") or order.get("side") or "BUY").upper()
    otype = str(order.get("order_type") or order.get("type") or "MARKET").upper()
    validity = str(order.get("validity") or order.get("time_in_force") or "DAY").upper()
    qty = int(round(_f(order.get("quantity") if order.get("quantity") is not None else order.get("qty")) or 0))
    price = _f(order.get("price")) if _f(order.get("price")) else (_f(order.get("limit_price")) or 0.0)
    trigger_price = _f(order.get("trigger_price")) or _f(order.get("stop_price"))
    stop_loss = _f(order.get("stop_loss")) or _f(order.get("stop_loss_stop_price")) or _f(plan.get("stop"))
    target = _f(order.get("target")) or _f(order.get("take_profit_limit_price")) or _f(plan.get("target"))
    est_charges = _f(order.get("est_charges_inr"))
    if est_charges is None:
        est_charges = _f(plan.get("est_charges_inr")) or 0.0
    return {
        "symbol": sym,
        "exchange": exchange,
        "segment": segment,
        "product": product,
        "transaction_type": tx,
        "order_type": otype,
        "quantity": qty,
        "price": price,
        "trigger_price": trigger_price,
        "validity": validity,
        "stop_loss": stop_loss,
        "target": target,
        "trade_type": trade_type,
        "est_charges_inr": est_charges,
        "plan": plan,
    }


def max_loss(order):
    """(inr_loss, stop_price) lost if the stop fills at stop (including est_charges_inr)."""
    no = norm_order(order)
    stop = no["stop_loss"]
    qty = no["quantity"]
    if not stop or not qty:
        return None, stop
    if _f(order.get("max_loss_inr")) is not None:
        return round(_f(order["max_loss_inr"]), 2), stop
    plan = no["plan"]
    if _f(plan.get("max_loss_inr")) is not None:
        return round(_f(plan["max_loss_inr"]), 2), stop
    if _f(plan.get("max_loss")) is not None:
        return round(_f(plan["max_loss"]), 2), stop
    entry = no["price"] if no["order_type"] in ("LIMIT", "SL") and no["price"] > 0 else _f(plan.get("entry"))
    if not entry:
        return None, stop
    risk = abs(entry - stop)
    return round(qty * risk + (no["est_charges_inr"] or 0.0), 2), stop


def plain_parts(order):
    """Ticket in plain words, one line per leg."""
    no = norm_order(order)
    val_word = VALIDITY_WORDS.get(no["validity"], no["validity"])
    if no["order_type"] == "LIMIT" and no["price"] > 0:
        how = "LIMIT at %s" % _money(no["price"])
    elif no["order_type"] in ("SL", "SL-M", "SL_M") and no["trigger_price"]:
        how = "%s trigger %s" % (no["order_type"], _money(no["trigger_price"]))
        if no["price"] > 0:
            how += " limit %s" % _money(no["price"])
    else:
        how = "MARKET"
    parts = [
        "%s %d %s (%s %s, %s, %s), %s, %s"
        % (
            no["transaction_type"], no["quantity"], no["symbol"],
            no["exchange"], no["segment"], no["product"], no["trade_type"],
            how, val_word,
        )
    ]
    if no["stop_loss"] is not None:
        parts.append("stop-loss %s (SL-M / OCO trigger)" % _money(no["stop_loss"]))
    if no["target"] is not None:
        parts.append("target %s (LIMIT / OCO target)" % _money(no["target"]))
    loss, stop = max_loss(order)
    if loss is not None and stop is not None:
        ch_note = (" (includes %s est. charges)" % _money(no["est_charges_inr"])) if no["est_charges_inr"] else ""
        parts.append(
            "max loss about %s%s if the stop fills at %s (can fill worse in a fast market or circuit gap)"
            % (_money(loss), ch_note, _money(stop))
        )
    return parts


def plain(order):
    return " / ".join(plain_parts(order))


# ---------------------------------------------------------------- broker SDK payload builders

def build_broker_calls(order, broker):
    """Build exact Python SDK call specification for the selected Indian broker."""
    no = norm_order(order)
    b = str(broker or "groww").lower()
    sym = no["symbol"]
    exch = no["exchange"]
    seg = no["segment"]
    prod = no["product"]
    tx = no["transaction_type"]
    exit_tx = "SELL" if tx == "BUY" else "BUY"
    otype = no["order_type"]
    qty = no["quantity"]
    price = round(float(no["price"] or 0.0), 2)
    trig = round(float(no["trigger_price"]), 2) if no["trigger_price"] else None
    stop = round(float(no["stop_loss"]), 2) if no["stop_loss"] else None
    target = round(float(no["target"]), 2) if no["target"] else None

    if b == "groww":
        groww_otype = {"MARKET": "MARKET", "LIMIT": "LIMIT", "SL": "SL", "SL-M": "SL_M", "SL_M": "SL_M"}.get(otype, "MARKET")
        entry_call = {
            "sdk": "growwapi.GrowwAPI",
            "method": "place_order",
            "kwargs": {
                "validity": no["validity"],
                "exchange": exch,
                "order_type": groww_otype,
                "product": prod,
                "quantity": qty,
                "segment": seg,
                "trading_symbol": sym,
                "transaction_type": tx,
                "price": price,
                "trigger_price": trig,
            },
        }
        legs = []
        if stop and target:
            legs.append({
                "sdk": "growwapi.GrowwAPI",
                "method": "create_smart_order (OCO)",
                "kwargs": {
                    "smart_order_type": "OCO",
                    "segment": seg,
                    "trading_symbol": sym,
                    "quantity": qty,
                    "product_type": prod,
                    "exchange": exch,
                    "duration": "DAY" if prod == "MIS" else "GTC",
                    "net_position_quantity": qty,
                    "transaction_type": exit_tx,
                    "stop_loss": {"trigger_price": _p(stop), "order_type": "SL_M"},
                    "target": {"trigger_price": _p(target), "order_type": "LIMIT", "price": _p(target)},
                },
            })
        else:
            if stop:
                legs.append({
                    "sdk": "growwapi.GrowwAPI",
                    "method": "place_order (stop_loss)",
                    "kwargs": {
                        "validity": "DAY", "exchange": exch, "order_type": "SL_M",
                        "product": prod, "quantity": qty, "segment": seg,
                        "trading_symbol": sym, "transaction_type": exit_tx,
                        "price": 0.0, "trigger_price": stop,
                    },
                })
            if target:
                legs.append({
                    "sdk": "growwapi.GrowwAPI",
                    "method": "place_order (target)",
                    "kwargs": {
                        "validity": "DAY", "exchange": exch, "order_type": "LIMIT",
                        "product": prod, "quantity": qty, "segment": seg,
                        "trading_symbol": sym, "transaction_type": exit_tx,
                        "price": target, "trigger_price": None,
                    },
                })
        return {"broker": "groww", "entry": entry_call, "exit_legs": legs}

    if b == "zerodha":
        kite_otype = {"MARKET": "MARKET", "LIMIT": "LIMIT", "SL": "SL", "SL-M": "SL-M", "SL_M": "SL-M"}.get(otype, "MARKET")
        entry_call = {
            "sdk": "kiteconnect.KiteConnect",
            "method": "place_order",
            "kwargs": {
                "variety": "regular",
                "exchange": "NFO" if seg == "FNO" and exch == "NSE" else exch,
                "tradingsymbol": sym,
                "transaction_type": tx,
                "quantity": qty,
                "product": prod,
                "order_type": kite_otype,
                "price": price if kite_otype in ("LIMIT", "SL") else None,
                "trigger_price": trig if kite_otype in ("SL", "SL-M") else None,
                "validity": no["validity"],
            },
        }
        legs = []
        if stop and target and prod == "CNC":
            ref_ltp = price or _f(no["plan"].get("entry")) or stop
            legs.append({
                "sdk": "kiteconnect.KiteConnect",
                "method": "place_gtt (two-leg OCO)",
                "kwargs": {
                    "trigger_type": "two-leg",
                    "tradingsymbol": sym,
                    "exchange": exch,
                    "trigger_values": [stop, target],
                    "last_price": ref_ltp,
                    "orders": [
                        {"exchange": exch, "tradingsymbol": sym, "transaction_type": exit_tx,
                         "quantity": qty, "order_type": "LIMIT", "product": prod, "price": stop},
                        {"exchange": exch, "tradingsymbol": sym, "transaction_type": exit_tx,
                         "quantity": qty, "order_type": "LIMIT", "product": prod, "price": target},
                    ],
                },
            })
        else:
            if stop:
                legs.append({
                    "sdk": "kiteconnect.KiteConnect",
                    "method": "place_order (stop_loss)",
                    "kwargs": {
                        "variety": "regular", "exchange": exch, "tradingsymbol": sym,
                        "transaction_type": exit_tx, "quantity": qty, "product": prod,
                        "order_type": "SL-M", "trigger_price": stop, "validity": "DAY",
                    },
                })
            if target:
                legs.append({
                    "sdk": "kiteconnect.KiteConnect",
                    "method": "place_order (target)",
                    "kwargs": {
                        "variety": "regular", "exchange": exch, "tradingsymbol": sym,
                        "transaction_type": exit_tx, "quantity": qty, "product": prod,
                        "order_type": "LIMIT", "price": target, "validity": "DAY",
                    },
                })
        return {"broker": "zerodha", "entry": entry_call, "exit_legs": legs}

    if b == "upstox":
        up_prod = {"MIS": "I", "CNC": "D", "NRML": "D"}.get(prod, "I")
        up_otype = {"MARKET": "MARKET", "LIMIT": "LIMIT", "SL": "SL", "SL-M": "SL-M", "SL_M": "SL-M"}.get(otype, "MARKET")
        inst_token = order.get("instrument_token") or ("%s_%s|%s" % (exch, "FO" if seg == "FNO" else "EQ", sym))
        entry_call = {
            "sdk": "upstox_client.OrderApi",
            "method": "place_order (PlaceOrderRequest, api_version='2.0')",
            "kwargs": {
                "quantity": qty,
                "product": up_prod,
                "validity": no["validity"],
                "price": price,
                "instrument_token": inst_token,
                "order_type": up_otype,
                "transaction_type": tx,
                "disclosed_quantity": 0,
                "trigger_price": trig or 0.0,
                "is_amo": False,
            },
        }
        legs = []
        if stop:
            legs.append({
                "sdk": "upstox_client.OrderApi",
                "method": "place_order (stop_loss SL-M)",
                "kwargs": {
                    "quantity": qty, "product": up_prod, "validity": "DAY",
                    "price": 0.0, "instrument_token": inst_token,
                    "order_type": "SL-M", "transaction_type": exit_tx,
                    "disclosed_quantity": 0, "trigger_price": stop, "is_amo": False,
                },
            })
        if target:
            legs.append({
                "sdk": "upstox_client.OrderApi",
                "method": "place_order (target LIMIT)",
                "kwargs": {
                    "quantity": qty, "product": up_prod, "validity": "DAY",
                    "price": target, "instrument_token": inst_token,
                    "order_type": "LIMIT", "transaction_type": exit_tx,
                    "disclosed_quantity": 0, "trigger_price": 0.0, "is_amo": False,
                },
            })
        return {"broker": "upstox", "entry": entry_call, "exit_legs": legs}

    if b == "angelone":
        ang_prod = {"MIS": "INTRADAY", "CNC": "DELIVERY", "NRML": "CARRYFORWARD"}.get(prod, "INTRADAY")
        ang_otype = {
            "MARKET": "MARKET", "LIMIT": "LIMIT",
            "SL": "STOPLOSS_LIMIT", "SL-M": "STOPLOSS_MARKET", "SL_M": "STOPLOSS_MARKET",
        }.get(otype, "MARKET")
        tsym = "%s-EQ" % sym if seg == "CASH" and exch == "NSE" and not sym.endswith("-EQ") else sym
        sym_token = str(order.get("symboltoken") or os.environ.get("ANGEL_SYMBOLTOKEN_" + sym, "3045" if sym == "SBIN" else "RESOLVED_AT_RUNTIME"))
        entry_call = {
            "sdk": "SmartApi.SmartConnect",
            "method": "placeOrder",
            "kwargs": {
                "variety": "STOPLOSS" if "STOPLOSS" in ang_otype else "NORMAL",
                "tradingsymbol": tsym,
                "symboltoken": sym_token,
                "transactiontype": tx,
                "exchange": "NFO" if seg == "FNO" and exch == "NSE" else exch,
                "ordertype": ang_otype,
                "producttype": ang_prod,
                "duration": no["validity"],
                "price": _p(price),
                "squareoff": "0",
                "stoploss": "0",
                "quantity": str(qty),
            },
        }
        if trig:
            entry_call["kwargs"]["triggerprice"] = _p(trig)
        legs = []
        if stop:
            legs.append({
                "sdk": "SmartApi.SmartConnect",
                "method": "placeOrder (stop_loss)",
                "kwargs": {
                    "variety": "STOPLOSS", "tradingsymbol": tsym, "symboltoken": sym_token,
                    "transactiontype": exit_tx, "exchange": exch,
                    "ordertype": "STOPLOSS_MARKET", "producttype": ang_prod,
                    "duration": "DAY", "price": "0.00", "triggerprice": _p(stop),
                    "squareoff": "0", "stoploss": "0", "quantity": str(qty),
                },
            })
        if target:
            legs.append({
                "sdk": "SmartApi.SmartConnect",
                "method": "placeOrder (target)",
                "kwargs": {
                    "variety": "NORMAL", "tradingsymbol": tsym, "symboltoken": sym_token,
                    "transactiontype": exit_tx, "exchange": exch,
                    "ordertype": "LIMIT", "producttype": ang_prod,
                    "duration": "DAY", "price": _p(target),
                    "squareoff": "0", "stoploss": "0", "quantity": str(qty),
                },
            })
        return {"broker": "angelone", "entry": entry_call, "exit_legs": legs}

    raise TicketError("unknown broker %r; choose from: %s" % (broker, ", ".join(BROKERS)))


# ---------------------------------------------------------------- yes parser

def amount_numbers(order):
    no = norm_order(order)
    nums = [float(no["quantity"])]
    for v in (no["price"], no["trigger_price"], no["stop_loss"], no["target"], order.get("amount_inr")):
        if _f(v) is not None and _f(v) > 0:
            nums.append(float(v))
    plan = no["plan"]
    if _f(plan.get("entry")):
        nums.append(float(plan["entry"]))
    loss, _ = max_loss(order)
    if loss is not None:
        nums.append(loss)
    return nums


def parse_yes(reply, order, batch_symbols=()):
    """Is this reply an explicit yes for THIS one order? Returns (ok, why)."""
    raw = (reply or "").strip()
    if not raw:
        return False, "the reply is empty"
    if "?" in raw:
        return False, "the reply is a question, not a yes"
    words = re.findall(r"[a-z']+", raw.lower())
    if not words or words[0] not in YES_WORDS:
        return False, 'only a reply that starts with "yes" counts (not ok, sure, go, do it)'
    if BATCH_WORDS & set(words):
        return False, "a yes covers one order at a time, not a batch"
    if DOUBT_WORDS & set(words[1:]):
        return False, "the reply has a condition or a doubt in it; ask again"
    sym = clean_symbol(order.get("symbol", ""))
    tokens = set(re.findall(r"[A-Z][A-Z0-9&\.\-]{0,14}", raw.upper()))
    others = {clean_symbol(s) for s in batch_symbols if s} - {sym}
    named = tokens & others
    if named:
        return False, "the reply names %s, but this order is for %s" % (", ".join(sorted(named)), sym)
    nums = [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", raw)]
    if nums:
        want = amount_numbers(order)
        if not any(abs(n - w) < 0.05 for n in nums for w in want):
            return False, "the reply says %s, but this order is %s" % (
                ", ".join(_str_num(n) for n in nums), plain(order)
            )
    return True, "explicit yes for %s" % plain(order)


# ---------------------------------------------------------------- state & status

def load_state(path):
    return read_json(path, "broker state", required=False) or {"orders": []}


def latest(state, fp, today):
    found = None
    for rec in state.get("orders", []):
        if rec.get("fingerprint") == fp and rec_day(rec) == today:
            found = rec
    return found


def risk_verdict(risk, fp, today):
    if not risk:
        return None, "trader-risk has not checked these orders (run check.py)"
    if risk.get("date") != today:
        return None, "the risk check is from %s, not today (%s); run check.py again" % (risk.get("date"), today)
    for r in risk.get("results", []):
        if r.get("fingerprint") == fp:
            return r, None
    return None, "this exact order was not in the last risk check (it changed or is new); run check.py again"


def status_of(order, risk, state, today):
    fp = fingerprint(order)
    rv, why = risk_verdict(risk, fp, today)
    if rv is None:
        return False, "NOT READY: " + why, None
    if rv.get("verdict") != "PASS":
        return False, "BLOCKED by trader-risk: " + "; ".join(rv.get("reasons", [])), rv
    rec = latest(state, fp, today)
    if rec:
        st = rec.get("status")
        if st == "approved":
            return False, "APPROVED by you at %s, ready for `ticket.py execute --index N`" % rec.get("at"), rv
        return False, "DONE: %s at %s" % (st, rec.get("at")), rv
    return True, "READY: trader-risk PASS, waiting for your yes", rv


def kind_of(order, rv):
    if rv and rv.get("kind"):
        return rv["kind"]
    tx = str(order.get("transaction_type") or order.get("side") or "").lower()
    return "entry" if tx == "buy" else "exit"


def resolve_broker(a, cfg):
    b = getattr(a, "broker", None) or (cfg or {}).get("broker") or "groww"
    b = str(b).strip().lower()
    if b not in BROKERS:
        raise TicketError("unknown broker %r; supported: %s" % (b, ", ".join(BROKERS)))
    return b


# ---------------------------------------------------------------- rendering

def render(order, idx, total, cfg, risk, state, today, broker_override=None):
    ready, label, rv = status_of(order, risk, state, today)
    broker = broker_override or str(cfg.get("broker", "groww")).lower()
    paper = bool(cfg.get("paper", True))
    acct = "PAPER (local simulation)" if paper else "LIVE ACCOUNT, REAL MONEY"
    b_label = BROKER_LABELS.get(broker, broker)
    lines = ["ORDER %d of %d, %s, %s" % (idx, total, acct, b_label)]
    for part in plain_parts(order):
        lines.append("  " + part)
    if order.get("why"):
        lines.append("  why: " + order["why"])
    lines.append("  " + label)
    for w in (rv or {}).get("warnings", []):
        lines.append("  note: " + w)
    spec = build_broker_calls(order, broker)
    if paper:
        lines.append("  Mode: PAPER (local simulation in trader/portfolio.json & trader/state/broker.json;")
        lines.append("        Indian brokers do not offer paper accounts). Equivalent %s SDK call:" % broker)
    else:
        lines.append("  Mode: LIVE (%s). After your yes, `ticket.py execute --index %d` will call:" % (b_label, idx))
        lines.append("  Env vars required: %s" % ", ".join(BROKER_ENV_VARS.get(broker, [])))
    lines.append("    # Entry (%s.%s):" % (spec["entry"]["sdk"], spec["entry"]["method"]))
    for l in json.dumps(spec["entry"]["kwargs"], indent=2, ensure_ascii=False).splitlines():
        lines.append("    " + l)
    for leg in spec["exit_legs"]:
        lines.append("    # Exit leg (%s.%s):" % (leg["sdk"], leg["method"]))
        for l in json.dumps(leg["kwargs"], indent=2, ensure_ascii=False).splitlines():
            lines.append("    " + l)
    if ready:
        lines.append('  Reply "yes" to approve this one order, or "no" to skip it.')
    return "\n".join(lines), ready


def ticket_json(order, idx, cfg, risk, state, today, broker_override=None):
    ready, label, rv = status_of(order, risk, state, today)
    broker = broker_override or str(cfg.get("broker", "groww")).lower()
    loss, stop = max_loss(order)
    return {
        "index": idx,
        "ready": ready,
        "status": label,
        "plain": plain(order),
        "fingerprint": fingerprint(order),
        "paper": bool(cfg.get("paper", True)),
        "broker": broker,
        "broker_sdk_calls": build_broker_calls(order, broker),
        "why": order.get("why", ""),
        "max_loss_inr": loss,
        "stop_loss": stop,
    }


# ---------------------------------------------------------------- price fetch for paper fills

def fetch_yahoo_ltp(symbol, exchange="NSE", prices_path="trader/prices.json"):
    """Fetch latest price for paper fill: try Yahoo 1m chart JSON first, fallback to trader/prices.json."""
    ysym = yahoo_symbol(symbol, exchange)
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%s?interval=1m&range=1d" % urllib.parse.quote(ysym)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
        res = ((data.get("chart") or {}).get("result") or [None])[0] or {}
        meta = res.get("meta") or {}
        p = _f(meta.get("regularMarketPrice"))
        if p and p > 0:
            return round(p, 2), "Yahoo live LTP (%s)" % ysym
    except Exception:
        pass
    if prices_path and os.path.exists(prices_path):
        try:
            pdoc = read_json(prices_path, "prices", required=False) or {}
            pmap = pdoc.get("prices", pdoc) if isinstance(pdoc, dict) else {}
            clean = clean_symbol(symbol)
            p = _f(pmap.get(clean)) or _f(pmap.get(ysym))
            if p and p > 0:
                return round(p, 2), "trader/prices.json"
        except Exception:
            pass
    return None, None


# ---------------------------------------------------------------- fills -> portfolio

def apply_fill(portfolio_path, cfg, symbol, side, qty, price, date, product="MIS", charges_inr=0.0):
    """Record a filled order in trader/portfolio.json: cash and shares (with cost-basis lots)."""
    pf = read_json(portfolio_path, "portfolio", required=False)
    if pf is None:
        budget = (cfg or {}).get("budget", 1000)
        if not budget:
            raise TicketError("no %s yet and no budget in config to start it from" % portfolio_path)
        pf = {"cash": float(budget), "positions": []}
    symbol = clean_symbol(symbol)
    side = str(side).lower()
    qty, price = int(round(float(qty))), float(price)
    charges_inr = float(charges_inr or 0.0)
    if qty <= 0 or price <= 0:
        raise TicketError("a fill needs a positive qty and price")
    gross = round(qty * price, 2)
    pos = None
    for p in pf.setdefault("positions", []):
        if clean_symbol(p.get("ticker") or p.get("symbol") or "") == symbol:
            pos = p
            break
    if side == "buy":
        if pos is None:
            pos = {"ticker": symbol, "symbol": symbol, "product": product, "shares": 0, "lots": []}
            pf["positions"].append(pos)
        lots = pos.setdefault("lots", [])
        lots.append({
            "id": "%s-%d" % (symbol, len(lots) + 1),
            "date": date,
            "product": product,
            "shares": qty,
            "price": price,
            "cost": gross,
        })
        pos["shares"] = int(round(float(pos.get("shares", 0)) + qty))
        pos["product"] = product
        pf["cash"] = round(float(pf.get("cash", 0)) - gross - charges_inr, 2)
    else:
        if pos is None or float(pos.get("shares", 0)) + 1e-9 < qty:
            raise TicketError(
                "cannot record selling %s %s: the portfolio holds %s"
                % (qty, symbol, pos.get("shares", 0) if pos else 0)
            )
        left = qty
        for lot in sorted(pos.get("lots", []), key=lambda l: l.get("date", "")):
            if left <= 1e-9:
                break
            take = min(left, float(lot["shares"]))
            per = float(lot["cost"]) / float(lot["shares"]) if float(lot["shares"]) else 0
            lot["cost"] = round(float(lot["cost"]) - per * take, 2)
            lot["shares"] = int(round(float(lot["shares"]) - take))
            left -= take
        pos["lots"] = [l for l in pos.get("lots", []) if float(l["shares"]) > 1e-9]
        pos["shares"] = int(round(float(pos["shares"]) - qty))
        if pos["shares"] <= 0:
            pf["positions"].remove(pos)
        pf["cash"] = round(float(pf.get("cash", 0)) + gross - charges_inr, 2)
    pf["updated_at_ist"] = date
    write_json(portfolio_path, pf)
    return pf


def log_fill(state_path, now, today, symbol, side, qty, price, product="MIS", mode="paper", broker="groww", charges_inr=0.0):
    state = load_state(state_path)
    sym = clean_symbol(symbol)
    state.setdefault("orders", []).append({
        "at": ist_iso(now),
        "date": today,
        "status": "filled",
        "mode": mode,
        "broker": broker,
        "symbol": sym,
        "product": product,
        "side": side.lower(),
        "transaction_type": side.upper(),
        "qty": str(int(round(float(qty)))),
        "quantity": int(round(float(qty))),
        "price": float(price),
        "charges_inr": round(float(charges_inr or 0.0), 2),
        "plain": "%s %d %s (%s) at %s" % (side.upper(), int(round(float(qty))), sym, product, _money(price)),
    })
    write_json(state_path, state)


# ---------------------------------------------------------------- live SDK execution (lazy imports)

def execute_live_sdk(order, broker):
    """Place a live order on the chosen Indian broker using env var credentials ONLY.
    SDKs are imported lazily here so paper mode never requires them."""
    b = str(broker).lower()
    spec = build_broker_calls(order, b)
    ek = spec["entry"]["kwargs"]

    if b == "groww":
        try:
            from growwapi import GrowwAPI
        except ImportError as e:
            raise TicketError("growwapi is not installed in this Python environment: %s" % e)
        token = os.environ.get("GROWW_ACCESS_TOKEN")
        if not token:
            api_key = os.environ.get("GROWW_API_KEY")
            api_secret = os.environ.get("GROWW_API_SECRET")
            totp_secret = os.environ.get("GROWW_TOTP_SECRET")
            if api_key and (api_secret or totp_secret):
                totp_code = None
                if totp_secret:
                    import pyotp
                    totp_code = pyotp.TOTP(totp_secret).now()
                resp_tok = GrowwAPI.get_access_token(api_key=api_key, totp=totp_code, secret=api_secret)
                token = resp_tok.get("token") if isinstance(resp_tok, dict) else str(resp_tok)
        if not token:
            raise TicketError(
                "Missing Groww credentials in environment variables. Set GROWW_ACCESS_TOKEN "
                "(or GROWW_API_KEY and GROWW_API_SECRET) in your shell environment, never in chat."
            )
        client = GrowwAPI(token)
        resp = client.place_order(**ek)
        order_id = str((resp or {}).get("groww_order_id") or (resp or {}).get("order_id") or resp)
        leg_ids = []
        for leg in spec["exit_legs"]:
            if "create_smart_order" in leg["method"]:
                lresp = client.create_smart_order(**leg["kwargs"])
            else:
                lresp = client.place_order(**leg["kwargs"])
            leg_ids.append(str((lresp or {}).get("smart_order_id") or (lresp or {}).get("groww_order_id") or lresp))
        return order_id, leg_ids

    if b == "zerodha":
        try:
            from kiteconnect import KiteConnect
        except ImportError as e:
            raise TicketError("kiteconnect is not installed in this Python environment: %s" % e)
        api_key = os.environ.get("KITE_API_KEY")
        access_token = os.environ.get("KITE_ACCESS_TOKEN")
        if not api_key or not access_token:
            raise TicketError(
                "Missing Zerodha credentials in environment variables. Set KITE_API_KEY and "
                "KITE_ACCESS_TOKEN (daily login access token) in your shell environment."
            )
        kite = KiteConnect(api_key=api_key)
        kite.set_access_token(access_token)
        clean_kw = {k: v for k, v in ek.items() if v is not None}
        order_id = str(kite.place_order(**clean_kw))
        leg_ids = []
        for leg in spec["exit_legs"]:
            lkw = {k: v for k, v in leg["kwargs"].items() if v is not None}
            if "place_gtt" in leg["method"]:
                lid = kite.place_gtt(**lkw)
            else:
                lid = kite.place_order(**lkw)
            leg_ids.append(str(lid))
        return order_id, leg_ids

    if b == "upstox":
        try:
            import upstox_client
        except ImportError as e:
            raise TicketError("upstox_client is not installed in this Python environment: %s" % e)
        access_token = os.environ.get("UPSTOX_ACCESS_TOKEN")
        if not access_token:
            raise TicketError(
                "Missing Upstox credentials in environment variables. Set UPSTOX_ACCESS_TOKEN "
                "(daily OAuth access token) in your shell environment."
            )
        conf = upstox_client.Configuration()
        conf.access_token = access_token
        api = upstox_client.OrderApi(upstox_client.ApiClient(conf))
        req = upstox_client.PlaceOrderRequest(**ek)
        resp = api.place_order(req, api_version="2.0")
        order_id = str(getattr(getattr(resp, "data", None), "order_id", None) or resp)
        leg_ids = []
        for leg in spec["exit_legs"]:
            lreq = upstox_client.PlaceOrderRequest(**leg["kwargs"])
            lresp = api.place_order(lreq, api_version="2.0")
            leg_ids.append(str(getattr(getattr(lresp, "data", None), "order_id", None) or lresp))
        return order_id, leg_ids

    if b == "angelone":
        try:
            from SmartApi import SmartConnect
            import pyotp
        except ImportError as e:
            raise TicketError("SmartApi / pyotp is not installed in this Python environment: %s" % e)
        api_key = os.environ.get("ANGEL_API_KEY")
        client_id = os.environ.get("ANGEL_CLIENT_ID")
        pin = os.environ.get("ANGEL_PIN")
        totp_secret = os.environ.get("ANGEL_TOTP_SECRET")
        if not (api_key and client_id and pin and totp_secret):
            raise TicketError(
                "Missing Angel One credentials in environment variables. Set ANGEL_API_KEY, "
                "ANGEL_CLIENT_ID, ANGEL_PIN, and ANGEL_TOTP_SECRET in your shell environment."
            )
        smart = SmartConnect(api_key=api_key)
        totp_now = pyotp.TOTP(totp_secret).now()
        sess = smart.generateSession(client_id, pin, totp_now)
        if not sess or not sess.get("status"):
            raise TicketError("Angel One generateSession failed: %s" % sess)
        order_id = str(smart.placeOrder(dict(ek)))
        leg_ids = []
        for leg in spec["exit_legs"]:
            lid = smart.placeOrder(dict(leg["kwargs"]))
            leg_ids.append(str(lid))
        return order_id, leg_ids

    raise TicketError("Unsupported broker: %s" % broker)


# ---------------------------------------------------------------- commands

def _clock(a):
    now = parse_now(getattr(a, "now", None))
    today = getattr(a, "today", None) or to_ist(now).date().isoformat()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", today):
        raise TicketError("--today wants a date like 2026-10-30")
    return now, today


def _load(a):
    doc = read_json(a.orders, "orders file (run trader-budget, or ticket.py flatten, first)")
    orders = doc.get("orders", doc) if isinstance(doc, dict) else doc
    if not isinstance(orders, list) or not orders:
        raise TicketError("no orders in %s" % a.orders)
    cfg = read_json(a.config, "config", required=False) or {}
    if getattr(a, "broker", None):
        cfg = dict(cfg)
        cfg["broker"] = resolve_broker(a, cfg)
    risk = read_json(a.risk, "risk check", required=False)
    state = load_state(a.state)
    now, today = _clock(a)
    return orders, cfg, risk, state, today, now


def _pick(orders, index):
    if index is None:
        raise TicketError("say which order: --index N")
    if not 1 <= index <= len(orders):
        raise TicketError("there is no order %d (there are %d)" % (index, len(orders)))
    return orders[index - 1]


def cmd_show(a):
    orders, cfg, risk, state, today, now = _load(a)
    broker = resolve_broker(a, cfg)
    picks = [(a.index, _pick(orders, a.index))] if a.index else list(enumerate(orders, 1))
    if a.json:
        out = [ticket_json(o, i, cfg, risk, state, today, broker_override=broker) for i, o in picks]
        print(json.dumps(out[0] if a.index else out, indent=2, ensure_ascii=False))
        return 0 if any(t["ready"] for t in out) else 1
    blocks, any_ready = [], False
    for i, o in picks:
        text, ready = render(o, i, len(orders), cfg, risk, state, today, broker_override=broker)
        blocks.append(text)
        any_ready = any_ready or ready
    print("\n\n".join(blocks))
    print("\nOne order at a time: a yes covers one ticket, never the batch.")
    print(DISCLAIMER)
    return 0 if any_ready else 1


def cmd_approve(a):
    orders, cfg, risk, state, today, now = _load(a)
    broker = resolve_broker(a, cfg)
    order = _pick(orders, a.index)
    ready, label, rv = status_of(order, risk, state, today)
    if not ready:
        raise TicketError("order %d is not ready: %s" % (a.index, label))
    ok, why = parse_yes(a.reply, order, [o.get("symbol", "") for o in orders])
    if not ok:
        print("Not recorded: %s. Ask the user again with the ticket for order %d." % (why, a.index))
        return 1
    no = norm_order(order, cfg)
    loss, stop = max_loss(order)
    paper = bool(cfg.get("paper", True))
    rec = {
        "at": ist_iso(now),
        "date": today,
        "index": a.index,
        "fingerprint": fingerprint(order),
        "symbol": no["symbol"],
        "exchange": no["exchange"],
        "product": no["product"],
        "trade_type": no["trade_type"],
        "side": no["transaction_type"].lower(),
        "transaction_type": no["transaction_type"],
        "qty": str(no["quantity"]),
        "quantity": no["quantity"],
        "kind": kind_of(order, rv),
        "max_loss": loss,
        "max_loss_inr": loss,
        "stop": stop,
        "target": no["target"],
        "plain": plain(order),
        "status": "approved",
        "reply": a.reply,
        "paper": paper,
        "broker": broker,
    }
    state.setdefault("orders", []).append(rec)
    write_json(a.state, state)
    print("Recorded your yes for order %d: %s." % (a.index, plain(order)))
    if paper:
        print("Next (PAPER mode): run `ticket.py execute --index %d` to simulate the fill locally using latest Yahoo price." % a.index)
    else:
        print("Next (LIVE mode on %s): after final confirmation, run `ticket.py execute --index %d --broker %s`." % (BROKER_LABELS.get(broker, broker), a.index, broker))
    return 0


def cmd_execute(a):
    """Execute an approved order: local simulation in paper mode, or live broker SDK in live mode."""
    orders, cfg, risk, state, today, now = _load(a)
    broker = resolve_broker(a, cfg)
    order = _pick(orders, a.index)
    fp = fingerprint(order)
    rec = latest(state, fp, today)
    if not rec or rec.get("status") != "approved":
        raise TicketError("order %d has no yes recorded today; run `ticket.py approve --index %d --reply \"yes\"` first" % (a.index, a.index))

    no = norm_order(order, cfg)
    paper = bool(cfg.get("paper", True))

    if paper:
        fill_price = _f(a.price)
        price_src = "--price flag"
        if not fill_price:
            if no["order_type"] == "LIMIT" and no["price"] > 0:
                fill_price, price_src = no["price"], "order LIMIT price"
            else:
                ltp, src = fetch_yahoo_ltp(no["symbol"], no["exchange"], a.prices)
                if ltp:
                    fill_price, price_src = ltp, src
                elif _f(no["plan"].get("entry")):
                    fill_price, price_src = float(no["plan"]["entry"]), "plan entry price"
                elif no["price"] > 0:
                    fill_price, price_src = no["price"], "order price"
        if not fill_price or fill_price <= 0:
            raise TicketError("could not determine a fill price for paper execution of %s; pass --price" % no["symbol"])

        # Leg charges on entry/exit (half of round-trip estimate per fill, or full on entry if closing locally)
        leg_charges = round((no["est_charges_inr"] or 0.0) / 2.0, 2)
        sim_id = "PAPER-%s-%s-%d" % (broker.upper(), today.replace("-", ""), len(state.get("orders", [])) + 1)

        placed_rec = {
            "at": ist_iso(now),
            "date": today,
            "index": a.index,
            "fingerprint": fp,
            "symbol": no["symbol"],
            "exchange": no["exchange"],
            "product": no["product"],
            "trade_type": no["trade_type"],
            "side": no["transaction_type"].lower(),
            "transaction_type": no["transaction_type"],
            "qty": str(no["quantity"]),
            "quantity": no["quantity"],
            "kind": rec.get("kind", "entry"),
            "max_loss": rec.get("max_loss"),
            "max_loss_inr": rec.get("max_loss_inr"),
            "stop": rec.get("stop"),
            "target": rec.get("target"),
            "plain": rec["plain"],
            "status": "placed",
            "mode": "paper",
            "broker": broker,
            "broker_order_id": sim_id,
            "filled_price": fill_price,
            "price_source": price_src,
        }
        state["orders"].append(placed_rec)
        write_json(a.state, state)

        pf = apply_fill(
            a.portfolio, cfg, no["symbol"], no["transaction_type"].lower(),
            no["quantity"], fill_price, today, product=no["product"], charges_inr=leg_charges,
        )
        log_fill(
            a.state, now, today, no["symbol"], no["transaction_type"].lower(),
            no["quantity"], fill_price, product=no["product"], mode="paper",
            broker=broker, charges_inr=leg_charges,
        )
        print(
            "PAPER FILL (%s simulation): %s %d %s (%s) filled at %s (from %s), order ID %s."
            % (broker, no["transaction_type"], no["quantity"], no["symbol"], no["product"], _money(fill_price), price_src, sim_id)
        )
        if no["stop_loss"] or no["target"]:
            print(
                "Paper exit levels active: stop_loss %s, target %s%s."
                % (
                    _money(no["stop_loss"]) if no["stop_loss"] else "none",
                    _money(no["target"]) if no["target"] else "none",
                    (", flat_by %s IST" % (no["plan"].get("flat_by") or cfg.get("flat_by", "15:10")))
                    if no["trade_type"] == "intraday" else "",
                )
            )
        print("Updated %s (cash now %s) and %s." % (a.portfolio, _money(pf["cash"]), a.state))
        return 0

    # LIVE mode execution
    order_id, leg_ids = execute_live_sdk(order, broker)
    placed_rec = {
        "at": ist_iso(now),
        "date": today,
        "index": a.index,
        "fingerprint": fp,
        "symbol": no["symbol"],
        "exchange": no["exchange"],
        "product": no["product"],
        "trade_type": no["trade_type"],
        "side": no["transaction_type"].lower(),
        "transaction_type": no["transaction_type"],
        "qty": str(no["quantity"]),
        "quantity": no["quantity"],
        "kind": rec.get("kind", "entry"),
        "max_loss": rec.get("max_loss"),
        "max_loss_inr": rec.get("max_loss_inr"),
        "stop": rec.get("stop"),
        "target": rec.get("target"),
        "plain": rec["plain"],
        "status": "placed",
        "mode": "live",
        "broker": broker,
        "broker_order_id": order_id,
        "exit_leg_ids": leg_ids,
    }
    state["orders"].append(placed_rec)
    write_json(a.state, state)
    print("LIVE ORDER PLACED on %s: broker_order_id=%s, exit_leg_ids=%s" % (BROKER_LABELS.get(broker, broker), order_id, leg_ids))
    print("When filled on the exchange, record the fill: `ticket.py fill --symbol %s --side %s --qty %d --price <FILL_PRICE>`"
          % (no["symbol"], no["transaction_type"].lower(), no["quantity"]))
    return 0


def cmd_decline(a):
    orders, cfg, risk, state, today, now = _load(a)
    order = _pick(orders, a.index)
    no = norm_order(order, cfg)
    rec = {
        "at": ist_iso(now),
        "date": today,
        "index": a.index,
        "fingerprint": fingerprint(order),
        "symbol": no["symbol"],
        "side": no["transaction_type"].lower(),
        "transaction_type": no["transaction_type"],
        "plain": plain(order),
        "status": "declined",
        "reply": a.reply or "no",
    }
    state.setdefault("orders", []).append(rec)
    write_json(a.state, state)
    print("Recorded: you said no to order %d (%s). Nothing was sent." % (a.index, plain(order)))
    return 0


def cmd_result(a):
    orders, cfg, risk, state, today, now = _load(a)
    broker = resolve_broker(a, cfg)
    order = _pick(orders, a.index)
    fp = fingerprint(order)
    rec = latest(state, fp, today)
    if not rec or rec.get("status") != "approved":
        raise TicketError("order %d has no yes recorded today; a result can only follow an approval" % a.index)
    new = {
        "at": ist_iso(now),
        "date": today,
        "index": a.index,
        "fingerprint": fp,
        "symbol": rec["symbol"],
        "side": rec["side"],
        "transaction_type": rec.get("transaction_type", rec["side"].upper()),
        "product": rec.get("product", "MIS"),
        "broker": broker,
        "plain": rec["plain"],
        "status": a.status,
    }
    for k in ("qty", "quantity", "kind", "max_loss", "max_loss_inr", "stop", "target"):
        if rec.get(k) is not None:
            new[k] = rec[k]
    if a.broker_id:
        new["broker_order_id"] = a.broker_id
    if a.note:
        new["note"] = a.note
    state["orders"].append(new)
    write_json(a.state, state)
    print("Recorded order %d as %s." % (a.index, a.status))
    if a.status == "placed" and a.filled_qty and a.filled_price:
        apply_fill(
            a.portfolio, cfg, rec["symbol"], rec["side"],
            a.filled_qty, a.filled_price, today, product=rec.get("product", "MIS"),
        )
        log_fill(
            a.state, now, today, rec["symbol"], rec["side"],
            a.filled_qty, a.filled_price, product=rec.get("product", "MIS"),
            mode="paper" if cfg.get("paper", True) else "live", broker=broker,
        )
        print("Added the fill to %s." % a.portfolio)
    elif a.status == "placed":
        print("When it fills, record it: ticket.py fill --symbol %s --side %s --qty Q --price P"
              % (rec["symbol"], rec["side"]))
    return 0


def cmd_fill(a):
    cfg = read_json(a.config, "config", required=False) or {}
    broker = resolve_broker(a, cfg)
    now = parse_now(a.now)
    date = a.date or to_ist(now).date().isoformat()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        raise TicketError("--date wants a date like 2026-10-30")
    product = (a.product or cfg.get("product") or "MIS").upper()
    pf = apply_fill(a.portfolio, cfg, a.symbol, a.side, a.qty, a.price, date, product=product)
    log_fill(
        a.state, now, date, a.symbol, a.side, a.qty, a.price,
        product=product, mode="paper" if cfg.get("paper", True) else "live", broker=broker,
    )
    print(
        "Recorded %s %s %s (%s) at %s on %s in %s. Cash now %s."
        % (a.side.upper(), a.qty, clean_symbol(a.symbol), product, _money(a.price), date, a.portfolio, _money(pf["cash"]))
    )
    return 0


def cmd_flatten(a):
    cfg = read_json(a.config, "config", required=False) or {}
    sym = clean_symbol(a.symbol)
    now, today = _clock(a)
    source = "--qty flag"
    held_product = None
    if a.qty is not None:
        qty = _f(a.qty)
        if qty is None or qty <= 0 or abs(qty - round(qty)) > 1e-9:
            raise TicketError("--qty wants the whole number of shares you hold, like --qty 4")
        qty = int(round(qty))
    else:
        pf = read_json(a.portfolio, "portfolio", required=False) or {}
        qty_sum = 0.0
        for p in pf.get("positions", []):
            if clean_symbol(p.get("ticker") or p.get("symbol") or "") == sym:
                qty_sum += _f(p.get("shares", p.get("qty"))) or 0.0
                if p.get("product"):
                    held_product = str(p["product"]).upper()
        qty = int(round(qty_sum))
        source = a.portfolio
        if qty <= 0:
            raise TicketError(
                "nothing to flatten: no %s shares in %s. Pass --qty N if you hold shares at the broker"
                % (sym, a.portfolio)
            )
    plan = read_json(a.plan, "plan", required=False) or {}
    product = (a.product or held_product or (plan.get("product") if clean_symbol(plan.get("symbol", "")) == sym else None) or cfg.get("product") or "MIS").upper()
    trade_type = "swing" if product == "CNC" else "intraday"
    exchange = str(cfg.get("exchange", "NSE")).upper()
    flat_by = (plan.get("flat_by") if clean_symbol(plan.get("symbol", "")) == sym else None) or cfg.get("flat_by") or "15:10"
    order = {
        "symbol": sym,
        "exchange": exchange,
        "segment": "FNO" if product == "NRML" else "CASH",
        "product": product,
        "transaction_type": "SELL",
        "order_type": "MARKET",
        "quantity": qty,
        "price": 0.0,
        "trigger_price": None,
        "validity": "DAY",
        "stop_loss": None,
        "target": None,
        "trade_type": trade_type,
        "why": (
            "flat by %s IST: closes the %s (%s) position before broker auto square-off (qty from %s)"
            % (flat_by, sym, product, source)
        ),
    }
    old = read_json(a.orders, "orders", required=False)
    replaced = len(old.get("orders", [])) if isinstance(old, dict) else 0
    doc = {
        "created_by": "trader-broker",
        "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "now_ist": ist_iso(now),
        "status": "proposed",
        "paper": bool(cfg.get("paper", True)),
        "broker": resolve_broker(a, cfg),
        "exchange": exchange,
        "note": "Flatten ticket. Nothing is placed until trader-risk passes it and you say yes in the chat.",
        "source": "ticket.py flatten %s on %s" % (sym, today),
        "orders": [order],
    }
    write_json(a.orders, doc)
    print("Flatten %s: %s (flat by %s IST)." % (sym, plain(order), flat_by))
    print("Wrote it to %s%s." % (
        a.orders,
        (" (replaced %d earlier proposal%s)" % (replaced, "" if replaced == 1 else "s")) if replaced else "",
    ))
    print("Next:")
    print("  1. trader-risk: python check.py (an exit that only closes what you hold is never blocked by daily limits or window).")
    print("  2. ticket.py show, then the user's explicit yes (`ticket.py approve --index 1 --reply \"yes\"`).")
    print("  3. In LIVE mode, cancel any open SL/target/GTT leg for %s first so shares are not locked, then `ticket.py execute --index 1`." % sym)
    return 0


def cmd_log(a):
    state = load_state(a.state)
    now, today = _clock(a)
    rows = [r for r in state.get("orders", []) if a.all or rec_day(r) == today]
    if not rows:
        print("No decisions recorded%s." % ("" if a.all else " today"))
        return 1
    for r in rows:
        extra = (" id " + str(r["broker_order_id"])) if r.get("broker_order_id") else ""
        reply = (' reply "%s"' % r["reply"]) if r.get("reply") and r["status"] in ("approved", "declined") else ""
        ml = r.get("max_loss_inr") if r.get("max_loss_inr") is not None else r.get("max_loss")
        loss = (" max loss %s" % _money(ml)) if ml is not None and r["status"] == "placed" else ""
        idx = ("#%s" % r["index"]) if r.get("index") is not None else "  "
        print("%s  %s %-9s %s%s%s%s" % (r.get("at"), idx, r.get("status"), r.get("plain"), reply, loss, extra))
    placed = sum(1 for r in rows if r.get("status") == "placed")
    filled = sum(1 for r in rows if r.get("status") == "filled")
    print("%d placed, %d filled%s." % (placed, filled, "" if a.all else " today"))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Order tickets, paper simulation, and live Indian broker execution (trader-broker).")
    sub = ap.add_subparsers(dest="cmd")

    def clock(p):
        p.add_argument("--now", help='pretend it is this IST time, e.g. "2026-10-30 10:15"')
        p.add_argument("--today", help="YYYY-MM-DD (default: today in IST)")

    def common(p):
        p.add_argument("--orders", default="trader/orders.json")
        p.add_argument("--config", default="trader/config.json")
        p.add_argument("--risk", default="trader/state/risk.json")
        p.add_argument("--state", default="trader/state/broker.json")
        p.add_argument("--broker", choices=list(BROKERS), help="override broker (groww, zerodha, upstox, angelone)")
        clock(p)

    p = sub.add_parser("show", help="render the tickets and broker SDK calls")
    common(p)
    p.add_argument("--index", type=int)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("approve", help="record the user's explicit yes for one order")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--reply", required=True, help="the user's reply, word for word")

    p = sub.add_parser("execute", help="execute an approved order (paper: local simulation; live: broker SDK)")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--price", type=float, help="override fill price in paper mode")
    p.add_argument("--prices", default="trader/prices.json", help="prices.json fallback for paper mode")
    p.add_argument("--portfolio", default="trader/portfolio.json")

    p = sub.add_parser("decline", help="record a no")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--reply")

    p = sub.add_parser("result", help="record what happened after manual/SDK placement")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--status", required=True, choices=["placed", "denied", "failed"])
    p.add_argument("--broker-id")
    p.add_argument("--note")
    p.add_argument("--filled-qty")
    p.add_argument("--filled-price")
    p.add_argument("--portfolio", default="trader/portfolio.json")

    p = sub.add_parser("fill", help="record a fill in trader/portfolio.json")
    p.add_argument("--symbol", required=True)
    p.add_argument("--side", required=True, choices=["buy", "sell", "BUY", "SELL"])
    p.add_argument("--qty", required=True)
    p.add_argument("--price", required=True)
    p.add_argument("--product", choices=["MIS", "CNC", "NRML"])
    p.add_argument("--broker", choices=list(BROKERS))
    p.add_argument("--date", help="YYYY-MM-DD (default: today in IST)")
    p.add_argument("--now")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p.add_argument("--config", default="trader/config.json")
    p.add_argument("--state", default="trader/state/broker.json")

    p = sub.add_parser("flatten", help="build a SELL ticket that closes a symbol's position by flat_by (15:10 IST)")
    p.add_argument("--symbol", required=True)
    p.add_argument("--qty", help="shares you hold (default: from trader/portfolio.json)")
    p.add_argument("--product", choices=["MIS", "CNC", "NRML"], help="product type (default: from portfolio/config)")
    p.add_argument("--broker", choices=list(BROKERS))
    p.add_argument("--orders", default="trader/orders.json", help="where the ticket is written")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p.add_argument("--plan", default="trader/plan.json")
    p.add_argument("--config", default="trader/config.json")
    clock(p)

    p = sub.add_parser("log", help="today's decisions and fills")
    p.add_argument("--state", default="trader/state/broker.json")
    clock(p)
    p.add_argument("--all", action="store_true")

    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {
            "show": cmd_show,
            "approve": cmd_approve,
            "execute": cmd_execute,
            "decline": cmd_decline,
            "result": cmd_result,
            "fill": cmd_fill,
            "flatten": cmd_flatten,
            "log": cmd_log,
        }[a.cmd](a)
    except TicketError as e:
        print("ticket.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
