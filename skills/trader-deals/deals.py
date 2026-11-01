#!/usr/bin/env python3
"""NSE Bulk/Block Deals + Insider (SEBI PIT) + SAST Reg 29 tracker for trader-deals.
Standard library only.

Tracks institutional and insider activity on the National Stock Exchange (NSE):
  1. Bulk Deals (>0.5% of equity shares traded in a single day)
  2. Block Deals (minimum ₹10 crore / 5 lakh shares negotiated in block window)
  3. Insider Trading (SEBI PIT Reg 7(2) — promoters, directors, KMPs, designated persons)
  4. SAST Regulation 29 (Substantial Acquisition of Shares & Takeovers — ≥5% or ±2% stake changes)

Usage:
    python deals.py                                 # all deals (last 5 days)
    python deals.py bulk --days 3                   # bulk deals only
    python deals.py block --min-value 25            # block deals >= ₹25 Cr
    python deals.py insider --promoter              # insider filings by promoters/promoter group
    python deals.py sast --days 7                   # SAST Reg 29 substantial stake changes
    python deals.py all --symbol SWIGGY             # all activity for a symbol
    python deals.py all --client "Mutual Fund"      # follow mutual funds / FIIs / superstar investors
    python deals.py new                             # only new deals since last check (updates state)

Educational tool, not SEBI-registered investment advice. Trading loses most people money.
"""

import argparse
import csv
import datetime as _dt
import hashlib
import http.cookiejar
import io
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

try:
    from zoneinfo import ZoneInfo
    try:
        IST = ZoneInfo("Asia/Kolkata")
    except Exception:
        IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30), name="IST")
except ImportError:
    IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30), name="IST")

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

BULK_CSV_URLS = [
    "https://archives.nseindia.com/content/equities/bulk.csv",
    "https://nsearchives.nseindia.com/content/equities/bulk.csv",
]
BLOCK_CSV_URLS = [
    "https://archives.nseindia.com/content/equities/block.csv",
    "https://nsearchives.nseindia.com/content/equities/block.csv",
]
BULK_HIST_API = "https://www.nseindia.com/api/historical/bulk-deals?from={from_d}&to={to_d}"
BLOCK_HIST_API = "https://www.nseindia.com/api/historical/block-deals?from={from_d}&to={to_d}"
BULK_BLOCK_OR_API = (
    "https://www.nseindia.com/api/historicalOR/bulk-block-short-deals"
    "?optionType={opt}&from={from_d}&to={to_d}"
)
PIT_API = "https://www.nseindia.com/api/corporates-pit?index=equities&from_date={from_d}&to_date={to_d}"
PIT_GG_API = "https://www.nseindia.com/api/corporates-pit-gg?index=equities&from_date={from_d}&to_date={to_d}"
SAST_API = "https://www.nseindia.com/api/corporate-sast-reg29?index=equities&from_date={from_d}&to_date={to_d}"

NSE_SEED_URLS = [
    "https://www.nseindia.com/companies-listing/corporate-filings-insider-trading",
    "https://www.nseindia.com/market-data/live-equity-market",
    "https://www.nseindia.com/",
]

DISCLAIMER = (
    "Educational tool, not SEBI-registered investment advice. "
    "Trading loses most people money."
)

# Famous Indian marquee investors / superstar HNI names & investment vehicles
SUPERSTAR_PATTERNS = [
    "JHUNJHUNWALA", "RARE ENTERPRISES", "RADHAKISHAN DAMANI", "DERIVE TRADING",
    "BRIGHT STAR", "ASHISH KACHOLIA", "KACHOLIA", "SURYAVANSHI COMMOTRADE",
    "VIJAY KEDIA", "KEDIA SECURITIES", "MUKUL AGRAWAL", "MUKUL MAHAVIR",
    "DOLLY KHANNA", "SUNIL SINGHANIA", "ABAKKUS", "MADHUSUDAN KELA", "MK VENTURES",
    "NEMISH SHAH", "PORINJU VELIYATH", "EQUITY INTELLIGENCE", "SANJAY DUGAR",
    "SHANKAR SHARMA", "AKASH BHANSALI", "VALLABH BHANSALI", "ENAM", "RAMESH DAMANI",
    "ANIL KUMAR GOEL", "SEEMA GOEL", "SATPAL KHATTAR", "SANJIV BHASIN",
]

MF_PATTERNS = [
    "MUTUAL FUND", "ASSET MANAGEMENT", " AMC", "NIPPON INDIA", "HDFC MUTUAL",
    "SBI MUTUAL", "ICICI PRUDENTIAL MUTUAL", "KOTAK MAHINDRA MUTUAL", "AXIS MUTUAL",
    "ADITYA BIRLA SUN LIFE", "QUANT MUTUAL", "MOTILAL OSWAL MUTUAL", "TATA MUTUAL",
    "DSP MUTUAL", "MIRAE ASSET", "FRANKLIN TEMPLETON", "SUNDARAM MUTUAL",
    "BANDHAN MUTUAL", "UTI MUTUAL", "PPFAS", "EDELWEISS MUTUAL", "INVESCO MUTUAL",
    "HSBC MUTUAL", "CANARA ROBECO", "LIC MUTUAL", "MAHINDRA MANULIFE", "WHITE OAK",
]

DII_PATTERNS = [
    "LIFE INSURANCE", "GENERAL INSURANCE", "LIC OF INDIA", "SBI LIFE", "HDFC LIFE",
    "ICICI PRUDENTIAL LIFE", "MAX LIFE", "BAJAJ ALLIANZ", "KOTAK MAHINDRA LIFE",
    "TATA AIA", "STAR HEALTH", "PENSION FUND", "NPS TRUST", "PROVIDENT FUND",
]

FII_PATTERNS = [
    "SOCIETE GENERALE", "MORGAN STANLEY", "GOLDMAN SACHS", "CITIGROUP",
    "BOFA SECURITIES", "BANK OF AMERICA", "MERRILL LYNCH", "BNP PARIBAS", "NOMURA",
    "UBS ", "CLSA", "COPTHALL", "MAURITIUS", "SINGAPORE PTE", "NORGES",
    "GOVERNMENT PENSION FUND GLOBAL", "VANGUARD", "BLACKROCK", "FIDELITY",
    "EASTSPRING", "ABU DHABI", "KUWAIT INVESTMENT", "QATAR INVESTMENT",
    "GIC PRIVATE", "TEMASEK", "FOREIGN PORTFOLIO", "FPI", "FII", "MASTER FUND",
    "GLOBAL FUND", "OPPORTUNITIES FUND", "WORLD FUND", "EUROPE SE", "EUROPE SA",
    "EUROPE B.V", "CAYMAN", "LUXEMBOURG", "IRELAND", "GHISALLO", "MARSHALL WACE",
]

HFT_PATTERNS = [
    "GRAVITON RESEARCH", "NK SECURITIES", "QE SECURITIES", "JUMP TRADING",
    "TOWER RESEARCH", "ALPHAGREP", "HRTI PRIVATE", "HUDSON RIVER", "CITADEL",
    "OPTIVER", "IMC ", "JANE STREET", "MILLENNIUM", "QUADEYE", "MANSI SHARE",
    "PROGNOSIS SECURITIES", "MULTIPLIER SHARE", "SETU SECURITIES", "SSPL SECURITIES",
]


class DealsError(Exception):
    pass


# ---------------------------------------------------------------- dates & formatting

def now_ist(override=None):
    """Return current or overridden IST datetime."""
    if override:
        s = override.strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d-%m-%Y"):
            try:
                dt = _dt.datetime.strptime(s, fmt)
                return dt.replace(tzinfo=IST)
            except ValueError:
                continue
        try:
            dt = _dt.datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=IST)
            return dt.astimezone(IST)
        except ValueError:
            raise DealsError(f"Invalid --now/--today format: {override!r} (use YYYY-MM-DD or 'YYYY-MM-DD HH:MM')")
    return _dt.datetime.now(IST)


def parse_any_date(text):
    """Parse NSE date strings like '30-OCT-2026', '30-Oct-2026 19:22:30',
    '05-OCT-2026 to 05-OCT-2026', '2026-10-30', '30/10/2026' -> 'YYYY-MM-DD'."""
    if not text:
        return None
    s = str(text).strip()
    if " to " in s.lower():
        parts = re.split(r"\s+to\s+", s, flags=re.I)
        s = parts[-1].strip()
    # Take date portion before space/comma if timestamp present
    m_iso = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m_iso:
        return f"{m_iso.group(1)}-{m_iso.group(2)}-{m_iso.group(3)}"
    s_tok = re.split(r"[\s,]+", s)[0].strip()
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return _dt.datetime.strptime(s_tok, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def fmt_ddmmyyyy(d):
    if isinstance(d, str):
        d = _dt.date.fromisoformat(d)
    return d.strftime("%d-%m-%Y")


def clean_symbol(sym):
    if not sym:
        return ""
    s = str(sym).strip().upper()
    for suffix in (".NS", ".BO"):
        if s.endswith(suffix):
            s = s[:-len(suffix)]
    return s


def to_float(val):
    if val is None or val == "" or val == "-":
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).replace(",", "").replace("₹", "").replace("Rs.", "").replace("Rs", "").strip()
    if not s or s == "-":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def to_int(val):
    f = to_float(val)
    return int(round(f)) if f is not None else 0


def format_inr(amount):
    """Format a rupee number with Indian comma grouping (e.g. ₹1,23,456.50)."""
    if amount is None:
        return "-"
    neg = amount < 0
    amount = abs(float(amount))
    whole, frac = divmod(round(amount * 100), 100)
    s = str(int(whole))
    if len(s) > 3:
        last3 = s[-3:]
        rest = s[:-3]
        groups = []
        while rest:
            groups.append(rest[-2:])
            rest = rest[:-2]
        s = ",".join(reversed(groups)) + "," + last3
    sign = "-" if neg else ""
    if frac:
        return f"{sign}₹{s}.{frac:02d}"
    return f"{sign}₹{s}"


def format_cr(value_cr):
    """Format ₹ crore cleanly."""
    if value_cr is None:
        return "-"
    v = float(value_cr)
    if v >= 100:
        return f"₹{v:,.1f} Cr"
    if v >= 1:
        return f"₹{v:,.2f} Cr"
    if v >= 0.01:
        return f"₹{v:.3f} Cr"
    if v > 0:
        return f"₹{v * 100:.1f} L"
    return "₹0.00 Cr"


def infer_category(client_name, explicit_cat=None, remarks=None):
    """Classify client/person into Promoter, Director, FII/FPI, MF, DII/Insurance,
    HNI/Superstar, HFT/Prop, Institution/Corp, or Individual/HNI."""
    if explicit_cat:
        c = explicit_cat.strip()
        cl = c.lower()
        if "promoter" in cl and "group" in cl:
            return "Promoter Group"
        if "promoter" in cl:
            return "Promoter"
        if "director" in cl:
            return "Director"
        if "key managerial" in cl or "kmp" in cl:
            return "KMP"
        if "employee" in cl or "designated" in cl:
            return "Employee/DP"
        if "relative" in cl:
            return "Relative"
        if c and cl not in ("other", "others", "-", "na", "n"):
            return c

    up = f"{client_name or ''} {remarks or ''}".upper()
    if "PROMOTER" in up:
        return "Promoter"
    for pat in SUPERSTAR_PATTERNS:
        if pat in up:
            return "HNI/Superstar"
    for pat in MF_PATTERNS:
        if pat in up:
            return "MF"
    for pat in DII_PATTERNS:
        if pat in up:
            return "DII/Insurance"
    for pat in FII_PATTERNS:
        if pat in up:
            return "FII/FPI"
    for pat in HFT_PATTERNS:
        if pat in up:
            return "HFT/Prop"
    corp_tokens = (
        "LIMITED", " LTD", "LLP", "PVT", "PRIVATE", "TRUST", "VENTURES",
        "CAPITAL", "INVESTMENT", "HOLDINGS", "ENTERPRISES", "FINANCE",
        "SECURITIES", "BROKING", "ADVISORS", "FUND", "CORP", "INC", "B.V.",
    )
    if any(t in up for t in corp_tokens):
        return "Institution/Corp"
    return "Individual/HNI"


def is_promoter_category(cat):
    c = (cat or "").lower()
    return "promoter" in c


def normalize_side(raw_side, mode=None):
    s = (raw_side or "").strip().upper()
    m = (mode or "").strip().upper()
    if "PLEDGE" in s or "PLEDGE" in m or "ENCUMBRANCE" in m or "INVOCATION" in m:
        if "REVOKE" in s or "REVOCATION" in m or "RELEASE" in m:
            return "PLEDGE-REV"
        return "PLEDGE"
    if s in ("BUY", "PURCHASE", "ACQUISITION", "ACQ", "B", "P"):
        return "BUY"
    if s in ("SELL", "SALE", "DISPOSAL", "DISP", "S"):
        return "SELL"
    if "BUY" in s or "ACQ" in s or "PURCHASE" in s:
        return "BUY"
    if "SELL" in s or "SALE" in s or "DISP" in s:
        return "SELL"
    return s or "OTHER"


def make_deal_id(row):
    """Deterministic ID for deduplication and state tracking."""
    raw = "|".join(
        str(row.get(k) or "")
        for k in ("source", "date", "symbol", "client", "side", "qty", "price", "mode", "raw_id")
    )
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:16]


# ---------------------------------------------------------------- HTTP / NSE session

class NSESession:
    """Cookie-aware HTTP client for NSE archives and JSON APIs."""

    def __init__(self, timeout=18):
        self.timeout = timeout
        self.cj = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cj)
        )
        self.seeded = False
        self.warnings = []

    def _base_headers(self, referer="https://www.nseindia.com/"):
        return {
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": referer,
            "Connection": "keep-alive",
        }

    def ensure_cookies(self):
        if self.seeded and len(self.cj) > 0:
            return
        for url in NSE_SEED_URLS:
            try:
                req = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": UA,
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.9",
                    },
                )
                with self.opener.open(req, timeout=self.timeout) as resp:
                    resp.read(4096)
                if len(self.cj) > 0:
                    self.seeded = True
                    return
            except Exception:
                continue
        self.seeded = True

    def get_text(self, url, need_cookies=False, referer="https://www.nseindia.com/"):
        if need_cookies:
            self.ensure_cookies()
        headers = self._base_headers(referer=referer)
        for attempt in range(2):
            try:
                req = urllib.request.Request(url, headers=headers)
                with self.opener.open(req, timeout=self.timeout) as resp:
                    data = resp.read()
                return data.decode("utf-8", "replace")
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403) and attempt == 0:
                    self.seeded = False
                    self.ensure_cookies()
                    continue
                self.warnings.append(f"HTTP {exc.code} for {url}")
                return None
            except Exception as exc:
                self.warnings.append(f"Network error for {url}: {exc}")
                return None
        return None

    def get_json(self, url, referer="https://www.nseindia.com/companies-listing/corporate-filings-insider-trading"):
        text = self.get_text(url, need_cookies=True, referer=referer)
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            self.warnings.append(f"Invalid JSON from {url}")
            return None


# ---------------------------------------------------------------- Fetchers

def _parse_bulk_block_csv(csv_text, source_kind):
    rows = []
    reader = csv.DictReader(io.StringIO(csv_text.strip()))
    for r in reader:
        norm = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
        dt = parse_any_date(norm.get("date"))
        sym = clean_symbol(norm.get("symbol"))
        if not sym or not dt:
            continue
        client = norm.get("client name") or norm.get("client") or "?"
        company = norm.get("security name") or norm.get("company name") or sym
        raw_side = norm.get("buy/sell") or norm.get("buy / sell") or ""
        qty = to_int(norm.get("quantity traded") or norm.get("quantity"))
        price = to_float(
            norm.get("trade price / wght. avg. price")
            or norm.get("trade price")
            or norm.get("price")
        )
        remarks = norm.get("remarks")
        if remarks == "-":
            remarks = None
        val_inr = (qty * price) if (qty and price is not None) else 0.0
        val_cr = round(val_inr / 1e7, 4)
        cat = infer_category(client, remarks=remarks)
        side = normalize_side(raw_side, mode="Market")
        mode = "Block Window" if source_kind == "block" else "Bulk (Market)"
        promo_buy = (side == "BUY") and is_promoter_category(cat)
        item = {
            "source": source_kind,
            "date": dt,
            "symbol": sym,
            "company": company,
            "client": client,
            "category": cat,
            "side": side,
            "qty": qty,
            "price": round(price, 2) if price is not None else None,
            "value_inr": round(val_inr, 2),
            "value_cr": val_cr,
            "mode": mode,
            "remarks": remarks,
            "promoter_buy": promo_buy,
            "raw_id": f"{source_kind}:{dt}:{sym}:{client}:{side}:{qty}:{price}",
            "url": BULK_CSV_URLS[0] if source_kind == "bulk" else BLOCK_CSV_URLS[0],
        }
        item["id"] = make_deal_id(item)
        rows.append(item)
    return rows


def _parse_bulk_block_json(data, source_kind):
    rows = []
    items = data.get("data", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
    for r in items:
        if not isinstance(r, dict):
            continue
        dt = parse_any_date(r.get("BD_DT_DATE") or r.get("date") or r.get("mTIMESTAMP"))
        sym = clean_symbol(r.get("BD_SYMBOL") or r.get("symbol"))
        if not sym or not dt:
            continue
        company = (r.get("BD_SCRIP_NAME") or r.get("name") or sym).strip()
        client = (r.get("BD_CLIENT_NAME") or r.get("clientName") or "?").strip()
        raw_side = (r.get("BD_BUY_SELL") or r.get("buySell") or "").strip()
        qty = to_int(r.get("BD_QTY_TRD") or r.get("qty"))
        price = to_float(r.get("BD_TP_WATP") or r.get("watp") or r.get("price"))
        remarks = r.get("BD_REMARKS") or r.get("remarks")
        if remarks in ("-", "", None):
            remarks = None
        val_inr = (qty * price) if (qty and price is not None) else 0.0
        val_cr = round(val_inr / 1e7, 4)
        cat = infer_category(client, remarks=remarks)
        side = normalize_side(raw_side, mode="Market")
        mode = "Block Window" if source_kind == "block" else "Bulk (Market)"
        promo_buy = (side == "BUY") and is_promoter_category(cat)
        item = {
            "source": source_kind,
            "date": dt,
            "symbol": sym,
            "company": company,
            "client": client,
            "category": cat,
            "side": side,
            "qty": qty,
            "price": round(price, 2) if price is not None else None,
            "value_inr": round(val_inr, 2),
            "value_cr": val_cr,
            "mode": mode,
            "remarks": remarks,
            "promoter_buy": promo_buy,
            "raw_id": f"{source_kind}:{dt}:{sym}:{client}:{side}:{qty}:{price}",
            "url": "https://www.nseindia.com/report-detail/display-bulk-and-block-deals",
        }
        item["id"] = make_deal_id(item)
        rows.append(item)
    return rows


def fetch_bulk_or_block(session, source_kind, from_date, to_date):
    """Fetch bulk or block deals from NSE historical API + archive CSV fallback."""
    rows_by_id = {}
    from_str = fmt_ddmmyyyy(from_date)
    to_str = fmt_ddmmyyyy(to_date)

    # 1. Try archive CSV first (fast, no cookies required, covers latest session)
    csv_urls = BULK_CSV_URLS if source_kind == "bulk" else BLOCK_CSV_URLS
    for u in csv_urls:
        text = session.get_text(u, need_cookies=False)
        if text and "," in text:
            for item in _parse_bulk_block_csv(text, source_kind):
                if from_date.isoformat() <= item["date"] <= to_date.isoformat():
                    rows_by_id[item["id"]] = item
            break

    # 2. If date range spans multiple days or CSV didn't cover the range, query NSE JSON APIs
    if (to_date - from_date).days >= 1 or not rows_by_id:
        primary_api = (BULK_HIST_API if source_kind == "bulk" else BLOCK_HIST_API).format(
            from_d=from_str, to_d=to_str
        )
        opt = "bulk_deals" if source_kind == "bulk" else "block_deals"
        fallback_api = BULK_BLOCK_OR_API.format(opt=opt, from_d=from_str, to_d=to_str)
        ref = "https://www.nseindia.com/report-detail/display-bulk-and-block-deals"

        data = session.get_json(primary_api, referer=ref)
        if not data or not data.get("data"):
            data = session.get_json(fallback_api, referer=ref)
        if data:
            for item in _parse_bulk_block_json(data, source_kind):
                if from_date.isoformat() <= item["date"] <= to_date.isoformat():
                    rows_by_id[item["id"]] = item

    return list(rows_by_id.values())


def _parse_pit_v1_row(r):
    """Parse a row from /api/corporates-pit."""
    sym = clean_symbol(r.get("symbol"))
    dt = (
        parse_any_date(r.get("date"))
        or parse_any_date(r.get("intimDt"))
        or parse_any_date(r.get("acqtoDt"))
        or parse_any_date(r.get("acqfromDt"))
    )
    if not sym or not dt:
        return None
    company = (r.get("company") or sym).strip()
    client = (r.get("acqName") or "?").strip()
    cat = infer_category(client, explicit_cat=r.get("personCategory"))
    mode = (r.get("acqMode") or "Market").strip()
    raw_side = (r.get("tdpTransactionType") or "").strip()
    side = normalize_side(raw_side, mode=mode)
    qty = to_int(r.get("secAcq") or r.get("buyQuantity") or r.get("sellquantity"))
    val_inr = to_float(r.get("secVal") or r.get("buyValue") or r.get("sellValue")) or 0.0
    price = (val_inr / qty) if (qty > 0 and val_inr > 0) else None
    val_cr = round(val_inr / 1e7, 4)
    sec_type = (r.get("secType") or "Equity Shares").strip()
    is_market_or_acq = " PLEDGE" not in f" {mode.upper()}" and side == "BUY"
    promo_buy = is_market_or_acq and is_promoter_category(cat)
    pid = r.get("pid") or r.get("did") or ""
    item = {
        "source": "insider",
        "date": dt,
        "symbol": sym,
        "company": company,
        "client": client,
        "category": cat,
        "side": side,
        "qty": qty,
        "price": round(price, 2) if price is not None else None,
        "value_inr": round(val_inr, 2),
        "value_cr": val_cr,
        "mode": mode,
        "security_type": sec_type,
        "before_pct": to_float(r.get("befAcqSharesPer")),
        "after_pct": to_float(r.get("afterAcqSharesPer")),
        "promoter_buy": promo_buy,
        "raw_id": f"pit1:{pid}:{dt}:{sym}:{client}:{side}:{qty}",
        "url": r.get("xbrl") or "https://www.nseindia.com/companies-listing/corporate-filings-insider-trading",
    }
    item["id"] = make_deal_id(item)
    return item


def _parse_pit_v2_xbrl(xml_text, meta):
    """Parse NSE PIT V2.0 XBRL XML file into one or more insider transaction rows."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    by_ctx = {}
    for elem in root.iter():
        tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        ctx = elem.attrib.get("contextRef", "")
        val = (elem.text or "").strip()
        if ctx:
            by_ctx.setdefault(ctx, {})[tag] = val

    main = by_ctx.get("MainI", {})
    sym = clean_symbol(main.get("Symbol") or meta.get("symbol"))
    company = (main.get("NameOfTheCompany") or meta.get("companyName") or sym).strip()
    filing_dt = (
        parse_any_date(meta.get("broadcastDateTime"))
        or parse_any_date(main.get("DateOfFiling"))
    )
    app_id = meta.get("appId") or ""
    xml_url = meta.get("xmlFileName") or meta.get("ixbrl") or ""

    out = []
    disc_keys = sorted(k for k in by_ctx if k != "MainI")
    for dk in disc_keys:
        d = by_ctx[dk]
        client = (d.get("NameOfThePerson") or "?").strip()
        raw_cat = d.get("CategoryOfPerson") or ""
        cat = infer_category(client, explicit_cat=raw_cat)
        mode = (d.get("ModeOfAcquisitionOrDisposal") or "Market").strip()
        raw_side = (d.get("SecuritiesAcquiredOrDisposedTransactionType") or "").strip()
        side = normalize_side(raw_side, mode=mode)
        qty = to_int(d.get("SecuritiesAcquiredOrDisposedNumberOfSecurity"))
        val_inr = to_float(d.get("SecuritiesAcquiredOrDisposedValueOfSecurity")) or 0.0
        price = (val_inr / qty) if (qty > 0 and val_inr > 0) else None
        val_cr = round(val_inr / 1e7, 4)
        trade_dt = (
            parse_any_date(d.get("DateOfAllotmentAdviceOrAcquisitionOfSharesOrSaleOfSharesSpecifyToDate"))
            or parse_any_date(d.get("DateOfAllotmentAdviceOrAcquisitionOfSharesOrSaleOfSharesSpecifyFromDate"))
            or filing_dt
        )
        bef_pct = to_float(d.get("SecuritiesHeldPriorToAcquisitionOrDisposalPercentageOfShareholding"))
        aft_pct = to_float(d.get("SecuritiesHeldPostAcquistionOrDisposalPercentageOfShareholding"))
        # In PIT V2 XBRL pure unit is sometimes 0.3812 for 38.12% or 38.12 directly
        if bef_pct is not None and 0 < bef_pct <= 1.0 and (aft_pct is None or aft_pct <= 1.0):
            bef_pct = round(bef_pct * 100, 2)
        if aft_pct is not None and 0 < aft_pct <= 1.0:
            aft_pct = round(aft_pct * 100, 2)
        is_market_or_acq = "PLEDGE" not in mode.upper() and side == "BUY"
        promo_buy = is_market_or_acq and is_promoter_category(cat)
        item = {
            "source": "insider",
            "date": filing_dt or trade_dt,
            "trade_date": trade_dt,
            "symbol": sym,
            "company": company,
            "client": client,
            "category": cat,
            "side": side,
            "qty": qty,
            "price": round(price, 2) if price is not None else None,
            "value_inr": round(val_inr, 2),
            "value_cr": val_cr,
            "mode": mode,
            "security_type": d.get("TypeOfInstrument") or "Equity",
            "before_pct": bef_pct,
            "after_pct": aft_pct,
            "promoter_buy": promo_buy,
            "raw_id": f"pit2:{app_id}:{dk}:{sym}:{client}:{side}:{qty}:{val_inr}",
            "url": xml_url,
        }
        item["id"] = make_deal_id(item)
        out.append(item)
    return out


def fetch_insider(session, from_date, to_date, symbols=None, max_xbrl=25):
    """Fetch SEBI PIT insider trading disclosures from NSE (V1 + V2 XBRL)."""
    rows_by_id = {}
    from_str = fmt_ddmmyyyy(from_date)
    to_str = fmt_ddmmyyyy(to_date)
    ref = "https://www.nseindia.com/companies-listing/corporate-filings-insider-trading"

    sym_list = list(symbols) if symbols else [None]

    # 1. Try V1 /api/corporates-pit
    for sym in sym_list:
        url = PIT_API.format(from_d=from_str, to_d=to_str)
        if sym:
            url += "&symbol=" + urllib.parse.quote(sym)
        data = session.get_json(url, referer=ref)
        if data and isinstance(data.get("data"), list):
            for r in data["data"]:
                item = _parse_pit_v1_row(r)
                if item and from_date.isoformat() <= item["date"] <= to_date.isoformat():
                    rows_by_id[item["id"]] = item

    # 2. Also query V2 /api/corporates-pit-gg (used by NSE for May 2026+ PIT V2.0 filings)
    for sym in sym_list:
        url = PIT_GG_API.format(from_d=from_str, to_d=to_str)
        if sym:
            url += "&symbol=" + urllib.parse.quote(sym)
        data = session.get_json(url, referer=ref)
        if not data or not isinstance(data.get("data"), list):
            continue
        entries = data["data"]
        # Filter entries by broadcastDateTime date before downloading XBRL
        filtered_entries = []
        for meta in entries:
            dt = parse_any_date(meta.get("broadcastDateTime") or meta.get("exchdisstime"))
            if dt and not (from_date.isoformat() <= dt <= to_date.isoformat()):
                continue
            msym = clean_symbol(meta.get("symbol"))
            if symbols and msym not in symbols:
                continue
            filtered_entries.append(meta)

        for meta in filtered_entries[:max_xbrl]:
            xml_url = meta.get("xmlFileName")
            parsed_any = False
            if xml_url and xml_url.endswith(".xml"):
                xml_text = session.get_text(xml_url, need_cookies=False)
                if xml_text and "<" in xml_text:
                    for item in _parse_pit_v2_xbrl(xml_text, meta):
                        rows_by_id[item["id"]] = item
                        parsed_any = True
            if not parsed_any:
                dt = parse_any_date(meta.get("broadcastDateTime")) or to_date.isoformat()
                msym = clean_symbol(meta.get("symbol"))
                item = {
                    "source": "insider",
                    "date": dt,
                    "symbol": msym,
                    "company": (meta.get("companyName") or msym).strip(),
                    "client": "See XBRL filing",
                    "category": meta.get("regulation") or "PIT",
                    "side": "FILING",
                    "qty": 0,
                    "price": None,
                    "value_inr": 0.0,
                    "value_cr": 0.0,
                    "mode": meta.get("typeOfSubmission") or "PIT Filing",
                    "promoter_buy": False,
                    "raw_id": f"pit2meta:{meta.get('appId')}:{dt}:{msym}",
                    "url": meta.get("ixbrl") or xml_url or ref,
                }
                item["id"] = make_deal_id(item)
                rows_by_id[item["id"]] = item

    return list(rows_by_id.values())


def fetch_sast(session, from_date, to_date, symbols=None, prices_map=None):
    """Fetch SEBI SAST Reg 29 substantial acquisition / disposal filings."""
    rows_by_id = {}
    from_str = fmt_ddmmyyyy(from_date)
    to_str = fmt_ddmmyyyy(to_date)
    ref = "https://www.nseindia.com/companies-listing/corporate-filings-insider-trading"
    prices_map = prices_map or {}

    url = SAST_API.format(from_d=from_str, to_d=to_str)
    data = session.get_json(url, referer=ref)
    if not data or not isinstance(data.get("data"), list):
        return []

    for r in data["data"]:
        sym = clean_symbol(r.get("symbol"))
        if not sym:
            continue
        if symbols and sym not in symbols:
            continue
        dt = (
            parse_any_date(r.get("timestamp"))
            or parse_any_date(r.get("sysTime"))
            or parse_any_date(r.get("time"))
            or parse_any_date(r.get("acquirerDate"))
        )
        if not dt or not (from_date.isoformat() <= dt <= to_date.isoformat()):
            continue

        company = (r.get("company") or sym).strip()
        client = (r.get("acquirerName") or "?").strip()
        promo_flag = str(r.get("promoterType") or "").strip().upper()
        explicit_cat = "Promoter" if promo_flag in ("Y", "YES", "PROMOTER", "TRUE") else None
        cat = infer_category(client, explicit_cat=explicit_cat)

        raw_side = (r.get("acqSaleType") or "").strip()
        acq_mode = (r.get("acquisitionMode") or "SAST").strip()
        acq_type = (r.get("acqType") or "Shares").strip()
        reg_type = (r.get("regType") or "Reg29").strip()
        side = normalize_side(raw_side, mode=acq_mode)

        qty_acq = to_int(r.get("noOfShareAcq"))
        qty_sale = to_int(r.get("noOfShareSale"))
        qty = qty_acq if qty_acq > 0 else qty_sale
        pct_acq = to_float(r.get("totAcqShare"))
        pct_sale = to_float(r.get("totSaleShare"))
        pct_aft = to_float(r.get("totAftShare"))
        stake_chg = pct_acq if (pct_acq is not None and pct_acq > 0) else pct_sale

        # SAST Reg 29 JSON does not include transaction price; estimate only if local prices.json has symbol
        price = prices_map.get(sym)
        val_inr = (qty * price) if (qty and price) else None
        val_cr = round(val_inr / 1e7, 4) if val_inr is not None else None

        mode_bits = [acq_mode]
        if stake_chg is not None:
            mode_bits.append(f"{stake_chg:g}% stake")
        if pct_aft is not None:
            mode_bits.append(f"post {pct_aft:g}%")
        mode_str = " | ".join(mode_bits)

        promo_buy = (side == "BUY") and is_promoter_category(cat) and ("PLEDGE" not in acq_mode.upper())
        app_no = r.get("application_no") or ""
        item = {
            "source": "sast",
            "date": dt,
            "symbol": sym,
            "company": company,
            "client": client,
            "category": cat,
            "side": side,
            "qty": qty,
            "price": round(price, 2) if price is not None else None,
            "value_inr": round(val_inr, 2) if val_inr is not None else None,
            "value_cr": val_cr,
            "mode": mode_str,
            "reg_type": reg_type,
            "instrument": acq_type,
            "stake_change_pct": stake_chg,
            "after_pct": pct_aft,
            "promoter_buy": promo_buy,
            "raw_id": f"sast:{app_no}:{dt}:{sym}:{client}:{side}:{qty}",
            "url": r.get("attachement") or ref,
        }
        item["id"] = make_deal_id(item)
        rows_by_id[item["id"]] = item

    return list(rows_by_id.values())


# ---------------------------------------------------------------- Portfolio / Watchlist / State

def _extract_symbols_from_json(data):
    out = set()
    if isinstance(data, list):
        for x in data:
            if isinstance(x, str):
                out.add(clean_symbol(x))
            elif isinstance(x, dict):
                s = x.get("symbol") or x.get("ticker")
                if s:
                    out.add(clean_symbol(s))
    elif isinstance(data, dict):
        for key in ("positions", "holdings", "watchlist", "candidates", "symbols", "tickers", "stocks", "items"):
            val = data.get(key)
            if isinstance(val, list):
                out.update(_extract_symbols_from_json(val))
            elif isinstance(val, dict):
                out.update(clean_symbol(k) for k in val.keys())
    return {s for s in out if s}


def load_held_and_watched(portfolio_path="trader/portfolio.json", watchlist_path="trader/watchlist.json", extra=None):
    held = set()
    watched = {clean_symbol(x) for x in (extra or []) if clean_symbol(x)}
    if portfolio_path and os.path.isfile(portfolio_path):
        try:
            with open(portfolio_path, encoding="utf-8") as fh:
                held.update(_extract_symbols_from_json(json.load(fh)))
        except Exception:
            pass
    if watchlist_path and os.path.isfile(watchlist_path):
        try:
            with open(watchlist_path, encoding="utf-8") as fh:
                watched.update(_extract_symbols_from_json(json.load(fh)))
        except Exception:
            pass
    return held, watched


def load_local_prices(prices_path="trader/prices.json"):
    out = {}
    if prices_path and os.path.isfile(prices_path):
        try:
            with open(prices_path, encoding="utf-8") as fh:
                data = json.load(fh)
            quotes = data.get("quotes") or data.get("prices") or data
            if isinstance(quotes, dict):
                for k, v in quotes.items():
                    if isinstance(v, (int, float)):
                        out[clean_symbol(k)] = float(v)
                    elif isinstance(v, dict):
                        p = v.get("price") or v.get("ltp") or v.get("last_price") or v.get("close")
                        if p:
                            out[clean_symbol(k)] = float(p)
        except Exception:
            pass
    return out


def load_state(path):
    if path and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            pass
    return {"seen": [], "last_check_ist": None}


def save_state(path, state):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    # Keep state compact (max 2000 recent IDs) to respect C: / workspace disk limits
    if len(state.get("seen", [])) > 2000:
        state["seen"] = state["seen"][-2000:]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


# ---------------------------------------------------------------- Filtering & Sorting

def filter_rows(
    rows,
    symbols=None,
    clients=None,
    min_value_cr=None,
    side_filter=None,
    promoter_only=False,
    held=None,
    watched=None,
):
    held = held or set()
    watched = watched or set()
    sym_set = {clean_symbol(s) for s in (symbols or []) if clean_symbol(s)}
    client_terms = [c.strip().lower() for c in (clients or []) if c and c.strip()]
    side_norm = (side_filter or "").strip().upper()
    if side_norm in ("ALL", ""):
        side_norm = None

    out = []
    for r in rows:
        sym = r["symbol"]
        r["held"] = sym in held
        r["watched"] = sym in watched
        r["flagged"] = r["held"] or r["watched"]

        if sym_set and sym not in sym_set:
            continue
        if client_terms:
            hay = f"{r.get('client', '')} {r.get('category', '')} {r.get('company', '')}".lower()
            if not any(term in hay for term in client_terms):
                continue
        if min_value_cr is not None and min_value_cr > 0:
            v = r.get("value_cr")
            if v is None or v < min_value_cr:
                continue
        if side_norm:
            if side_norm == "BUY" and r.get("side") != "BUY":
                continue
            elif side_norm == "SELL" and r.get("side") != "SELL":
                continue
            elif side_norm == "PLEDGE" and not str(r.get("side", "")).startswith("PLEDGE"):
                continue
        if promoter_only and not is_promoter_category(r.get("category")):
            continue
        out.append(r)

    # Sort: newest date first, then flagged (held/watched), then promoter_buy, then value_cr desc
    out.sort(
        key=lambda x: (
            x.get("date") or "",
            1 if x.get("flagged") else 0,
            1 if x.get("promoter_buy") else 0,
            x.get("value_cr") if x.get("value_cr") is not None else -1.0,
            x.get("qty") or 0,
        ),
        reverse=True,
    )
    return out


# ---------------------------------------------------------------- Rendering

def render_table(rows, meta):
    lines = []
    kinds_str = ", ".join(k.upper() for k in meta["sources"])
    hdr = (
        f"NSE Deals & Disclosures ({kinds_str}) | "
        f"{meta['from_date']} to {meta['to_date']} | "
        f"IST: {meta['now_ist']}"
    )
    lines.append(hdr)
    lines.append("=" * len(hdr))

    if not rows:
        lines.append("No matching filings or deals found for the selected filters.")
        lines.append("")
        lines.append(f"Disclaimer: {DISCLAIMER}")
        return "\n".join(lines) + "\n"

    promo_buys = [r for r in rows if r.get("promoter_buy")]
    flagged_rows = [r for r in rows if r.get("flagged")]
    if promo_buys or flagged_rows:
        highlights = []
        if promo_buys:
            highlights.append(f"★ {len(promo_buys)} Promoter Buy(s)")
        if flagged_rows:
            syms = sorted({r["symbol"] for r in flagged_rows})
            highlights.append(f"⚑ {len(flagged_rows)} in Portfolio/Watchlist ({', '.join(syms)})")
        lines.append("Highlights: " + "  |  ".join(highlights))
        lines.append("-" * len(hdr))

    col_hdr = (
        f"{'Date':<10} {'Type':<7} {'Symbol':<11} {'Client / Person':<28} "
        f"{'Category':<14} {'Side':<6} {'Qty':>11} {'Price':>10} {'Value':>11}  {'Mode / Flags'}"
    )
    lines.append(col_hdr)
    lines.append("-" * len(col_hdr))

    for r in rows:
        dt = r.get("date") or "-"
        src = (r.get("source") or "").upper()[:7]
        sym = (r.get("symbol") or "")[:11]
        client = (r.get("client") or "")[:28]
        cat = (r.get("category") or "")[:14]
        side = (r.get("side") or "")[:6]
        qty_str = f"{r['qty']:,}" if r.get("qty") else "-"
        price_str = f"₹{r['price']:,.2f}" if r.get("price") is not None else "-"
        val_str = format_cr(r.get("value_cr"))
        mode = r.get("mode") or "-"

        flags = []
        if r.get("promoter_buy"):
            flags.append("★ PROMOTER BUY")
        if r.get("held"):
            flags.append("[HELD]")
        elif r.get("watched"):
            flags.append("[WATCHLIST]")
        if r.get("before_pct") is not None and r.get("after_pct") is not None:
            flags.append(f"({r['before_pct']:g}%→{r['after_pct']:g}%)")

        tail = mode + ("  " + " ".join(flags) if flags else "")
        lines.append(
            f"{dt:<10} {src:<7} {sym:<11} {client:<28} "
            f"{cat:<14} {side:<6} {qty_str:>11} {price_str:>10} {val_str:>11}  {tail}"
        )

    lines.append("-" * len(col_hdr))
    lines.append(
        f"Showing {len(rows)} row(s). Public exchange disclosures (NSE Bulk/Block, SEBI PIT Reg 7(2), SAST Reg 29) "
        "are reported after execution — not inside information."
    )
    lines.append(f"Disclaimer: {DISCLAIMER}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- CLI

def _build_parser():
    ap = argparse.ArgumentParser(
        description="Track NSE Bulk Deals, Block Deals, Insider Trading (SEBI PIT), and SAST Reg 29 filings."
    )
    ap.add_argument(
        "mode",
        nargs="?",
        default="all",
        choices=["all", "bulk", "block", "insider", "pit", "sast", "new"],
        help="Deal category to query: all (default), bulk, block, insider (pit), sast, or new",
    )
    ap.add_argument(
        "--kind", "--type",
        dest="kind",
        choices=["all", "bulk", "block", "insider", "pit", "sast"],
        help="Filter by filing kind (useful with 'new' mode)",
    )
    ap.add_argument(
        "--symbol", "-s",
        action="append",
        dest="symbols",
        help="NSE symbol (e.g. RELIANCE, SWIGGY, TCS). Repeat or comma-separate for multiple.",
    )
    ap.add_argument(
        "--days", "-d",
        type=int,
        default=5,
        help="Lookback window in calendar days ending on --now/--to (default: 5)",
    )
    ap.add_argument("--from", "--since", dest="from_date", help="Start date (YYYY-MM-DD or DD-MM-YYYY)")
    ap.add_argument("--to", dest="to_date", help="End date (YYYY-MM-DD or DD-MM-YYYY)")
    ap.add_argument("--now", "--today", dest="now", help="Override current IST date/time (e.g. '2026-10-30 15:30')")
    ap.add_argument(
        "--client", "--investor", "--member",
        action="append",
        dest="clients",
        help="Substring match on investor / client / promoter / fund name (repeatable)",
    )
    ap.add_argument(
        "--min-value",
        type=float,
        default=None,
        help="Minimum deal value in ₹ crore (e.g. --min-value 10 for ≥ ₹10 Cr)",
    )
    ap.add_argument(
        "--side",
        choices=["all", "buy", "sell", "pledge", "BUY", "SELL", "PLEDGE"],
        default="all",
        help="Filter by transaction side: buy, sell, pledge, or all (default)",
    )
    ap.add_argument("--buy", action="store_true", help="Shortcut for --side buy")
    ap.add_argument("--sell", action="store_true", help="Shortcut for --side sell")
    ap.add_argument(
        "--promoter",
        action="store_true",
        help="Only show Promoter / Promoter Group filings",
    )
    ap.add_argument(
        "--new",
        action="store_true",
        dest="only_new",
        help="Only show filings not seen in previous checks (updates trader/state/deals.json)",
    )
    ap.add_argument("--state", default="trader/state/deals.json", help="State file path for --new")
    ap.add_argument("--dry-run", action="store_true", help="Do not update the state file on --new")
    ap.add_argument("--portfolio", default="trader/portfolio.json", help="Portfolio JSON to flag held stocks")
    ap.add_argument("--watchlist", default="trader/watchlist.json", help="Watchlist JSON to flag watched stocks")
    ap.add_argument("--prices", default="trader/prices.json", help="Local prices JSON for optional SAST valuation")
    ap.add_argument("--tickers", nargs="*", help="Additional NSE symbols to flag as watched")
    ap.add_argument("--limit", type=int, default=100, help="Maximum rows to display (default: 100)")
    ap.add_argument("--offline", metavar="FILE", help="Read deals from a local JSON or CSV file instead of network")
    ap.add_argument("--json", action="store_true", help="Print structured JSON output")
    return ap


def _resolve_sources(mode, kind):
    k = (kind or mode or "all").lower()
    if k in ("all", "new"):
        return ["bulk", "block", "insider", "sast"]
    if k == "pit":
        return ["insider"]
    return [k]


def main(argv=None):
    ap = _build_parser()
    args = ap.parse_args(argv)

    try:
        current_ist = now_ist(args.now)
    except DealsError as exc:
        print(f"deals: {exc}", file=sys.stderr)
        return 2

    to_dt = _dt.date.fromisoformat(parse_any_date(args.to_date)) if args.to_date else current_ist.date()
    if args.from_date:
        from_dt = _dt.date.fromisoformat(parse_any_date(args.from_date))
    else:
        days = max(1, int(args.days or 5))
        from_dt = to_dt - _dt.timedelta(days=days)

    if from_dt > to_dt:
        from_dt, to_dt = to_dt, from_dt

    only_new = args.only_new or (args.mode == "new")
    sources = _resolve_sources(args.mode, args.kind)

    symbols = set()
    for s in args.symbols or []:
        for part in s.split(","):
            cs = clean_symbol(part)
            if cs:
                symbols.add(cs)

    side_filter = "BUY" if args.buy else ("SELL" if args.sell else args.side)
    held, watched = load_held_and_watched(args.portfolio, args.watchlist, args.tickers)
    prices_map = load_local_prices(args.prices)

    session = NSESession()
    raw_rows = []

    if args.offline:
        if not os.path.isfile(args.offline):
            print(f"deals: offline file not found: {args.offline}", file=sys.stderr)
            return 2
        with open(args.offline, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        if args.offline.lower().endswith(".json"):
            data = json.loads(text)
            if isinstance(data, dict) and "rows" in data:
                raw_rows = data["rows"]
            elif isinstance(data, list):
                raw_rows = data
        else:
            raw_rows = _parse_bulk_block_csv(text, sources[0] if sources else "bulk")
    else:
        if "bulk" in sources:
            raw_rows.extend(fetch_bulk_or_block(session, "bulk", from_dt, to_dt))
        if "block" in sources:
            raw_rows.extend(fetch_bulk_or_block(session, "block", from_dt, to_dt))
        if "insider" in sources:
            raw_rows.extend(fetch_insider(session, from_dt, to_dt, symbols=symbols or None))
        if "sast" in sources:
            raw_rows.extend(fetch_sast(session, from_dt, to_dt, symbols=symbols or None, prices_map=prices_map))

    filtered = filter_rows(
        raw_rows,
        symbols=symbols,
        clients=args.clients,
        min_value_cr=args.min_value,
        side_filter=side_filter,
        promoter_only=args.promoter,
        held=held,
        watched=watched,
    )

    state = load_state(args.state) if only_new else None
    if only_new:
        seen = set(state.get("seen", []))
        fresh = [r for r in filtered if r["id"] not in seen]
        if not args.dry_run:
            seen.update(r["id"] for r in filtered)
            state["seen"] = sorted(seen)
            state["last_check_ist"] = current_ist.strftime("%Y-%m-%d %H:%M:%S IST")
            save_state(args.state, state)
        filtered = fresh

    shown = filtered[: max(1, args.limit)]
    meta = {
        "now_ist": current_ist.strftime("%Y-%m-%d %H:%M:%S IST"),
        "from_date": from_dt.isoformat(),
        "to_date": to_dt.isoformat(),
        "sources": sources,
        "only_new": only_new,
        "total_matched": len(filtered),
        "shown": len(shown),
        "promoter_buys": sum(1 for r in filtered if r.get("promoter_buy")),
        "flagged_count": sum(1 for r in filtered if r.get("flagged")),
        "held_symbols": sorted(held),
        "watched_symbols": sorted(watched),
        "warnings": session.warnings,
        "disclaimer": DISCLAIMER,
    }

    if args.json:
        print(json.dumps({"meta": meta, "rows": shown}, indent=2, ensure_ascii=False))
    else:
        if only_new and not shown:
            print(f"Nothing new since {state.get('last_check_ist') or 'the last check'}.")
            print(f"Disclaimer: {DISCLAIMER}")
            return 1
        sys.stdout.write(render_table(shown, meta))

    return 0 if shown else 1


if __name__ == "__main__":
    sys.exit(main())
