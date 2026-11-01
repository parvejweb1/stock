#!/usr/bin/env python3
"""Earnings and financial results reader for trader-analyst (India / NSE & BSE).
Standard library only.

Pulls a company's reported quarterly and annual numbers from Screener.in
(consolidated first, standalone fallback) plus upcoming board-meeting /
financial-result dates from NSE's event calendar, and lays out what changed
quarter by quarter (in ₹ crore except EPS in ₹):
  - Sales / Revenue (or Interest Earned for banks)
  - Expenses, Operating Profit (or Financing Profit for banks), OPM %
  - Other Income, Interest, Depreciation, Profit Before Tax, Tax %
  - Net Profit, Net Margin %, Diluted EPS (₹), Gross/Net NPA % (for banks)
  - YoY growth (vs same quarter a year earlier) and QoQ growth (vs prior quarter)
  - Trailing Twelve Months (TTM) and recent Annual P&L summary
  - Links to latest result PDF, investor presentation (PPT), concall transcript,
    and BSE/NSE announcements
  - NSE Event Calendar check ("does it report this week?")

    python facts.py RELIANCE
    python facts.py RELIANCE TCS HDFCBANK SBIN --quarters 6
    python facts.py HDFCBANK --standalone
    python facts.py RELIANCE --json
    python facts.py RELIANCE TCS HDFCBANK SBIN --metrics trader/metrics.json
    python facts.py SBIN --now "2026-11-01"

Numbers only: this tool never says whether a stock is a buy or a sell.
Educational tool, not SEBI-registered investment advice. Trading loses most people money.
"""

import argparse
import calendar
import datetime as _dt
import gzip
import html as _htmllib
import http.cookiejar
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
    IST_TZ = _dt.timezone(_dt.timedelta(hours=5, minutes=30))

SCREENER_CONSOLIDATED_URL = "https://www.screener.in/company/%s/consolidated/"
SCREENER_STANDALONE_URL = "https://www.screener.in/company/%s/"
NSE_HOME_URL = "https://www.nseindia.com/market-data/live-equity-market"
NSE_EVENT_CALENDAR_URL = "https://www.nseindia.com/api/event-calendar?symbol=%s"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0"
MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


class FactsError(Exception):
    pass


def clean_symbol(sym):
    s = str(sym or "").strip().upper()
    if s.endswith(".NS") or s.endswith(".BO"):
        s = s[:-3]
    return s


def today_ist(now_str=None):
    if now_str:
        t = now_str.strip().replace("Z", "+00:00")
        try:
            if len(t) == 10:
                return _dt.date.fromisoformat(t)
            return _dt.datetime.fromisoformat(t).date()
        except ValueError:
            raise FactsError("--now wants YYYY-MM-DD or YYYY-MM-DD HH:MM")
    return _dt.datetime.now(IST_TZ).date()


class Fetcher(object):
    def __init__(self, offline=None, pause=0.4):
        self.offline = offline
        self.pause = pause
        self._last = 0.0
        self._nse_opener = None

    def _wait(self):
        gap = time.time() - self._last
        if gap < self.pause:
            time.sleep(self.pause - gap)
        self._last = time.time()

    def text(self, url, offline_name=None, allow_404=False):
        if self.offline and offline_name:
            path = os.path.join(self.offline, offline_name)
            if os.path.isfile(path):
                with open(path, encoding="utf-8-sig") as fh:
                    return fh.read()
            if allow_404:
                return None
            raise FactsError("offline: no file %s" % path)
        self._wait()
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip",
        })
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                data = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
                return data.decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and allow_404:
                return None
            if exc.code == 404:
                raise FactsError("Screener.in has no company page at %s (check the NSE/BSE symbol)" % url)
            raise FactsError("HTTP %s for %s" % (exc.code, url))
        except urllib.error.URLError as exc:
            raise FactsError("could not reach %s: %s" % (url, exc.reason))

    def nse_events(self, symbol):
        """Fetch NSE event calendar for symbol. Returns list of dicts; never crashes on 401/403."""
        if self.offline:
            path = os.path.join(self.offline, "events-%s.json" % symbol)
            if os.path.isfile(path):
                with open(path, encoding="utf-8-sig") as fh:
                    return json.load(fh)
            return []
        hdrs = {
            "User-Agent": UA,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.5",
            "Connection": "keep-alive",
        }
        try:
            if self._nse_opener is None:
                cj = http.cookiejar.CookieJar()
                self._nse_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
                self._wait()
                self._nse_opener.open(urllib.request.Request(NSE_HOME_URL, headers=hdrs), timeout=10).read()
            self._wait()
            enc = urllib.parse.quote(symbol, safe="")
            api_hdrs = dict(hdrs, Referer=NSE_HOME_URL)
            req = urllib.request.Request(NSE_EVENT_CALENDAR_URL % enc, headers=api_hdrs)
            with self._nse_opener.open(req, timeout=10) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                data = json.loads(raw.decode("utf-8", "replace"))
                return data if isinstance(data, list) else []
        except Exception:
            return []


# ---------------------------------------------------------------- HTML parsing

def _strip_tags(html_frag):
    txt = re.sub(r"<[^>]+>", " ", html_frag or "")
    txt = _htmllib.unescape(txt).replace("\xa0", " ")
    return re.sub(r"\s+", " ", txt).strip()


def _parse_num(val):
    if val is None:
        return None
    s = str(val).replace(",", "").replace("%", "").replace("₹", "").strip()
    if not s or s == "-":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _period_to_date_and_fiscal(label):
    """'Sep 2026' -> ('2026-09-30', 'Q2 FY27')."""
    m = re.match(r"^([A-Za-z]{3})\s+(\d{4})$", (label or "").strip())
    if not m:
        return label, label
    mon_str, yr_str = m.group(1).lower(), m.group(2)
    mon = MONTHS.get(mon_str)
    yr = int(yr_str)
    if not mon:
        return label, label
    last_day = calendar.monthrange(yr, mon)[1]
    iso_end = "%04d-%02d-%02d" % (yr, mon, last_day)
    # Indian FY starts April 1: Jun=Q1, Sep=Q2, Dec=Q3, Mar=Q4
    if mon in (4, 5, 6):
        fy = (yr + 1) % 100
        fp = "Q1"
    elif mon in (7, 8, 9):
        fy = (yr + 1) % 100
        fp = "Q2"
    elif mon in (10, 11, 12):
        fy = (yr + 1) % 100
        fp = "Q3"
    else:
        fy = yr % 100
        fp = "Q4"
    return iso_end, "%s FY%02d" % (fp, fy)


def _normalize_row_key(label):
    s = re.sub(r"\+$", "", (label or "").strip()).strip().lower()
    if s in ("sales", "revenue"):
        return "revenue", (s == "revenue")
    if s == "interest":
        return "interest", False
    if s == "expenses":
        return "expenses", False
    if s == "operating profit":
        return "operating_profit", False
    if s == "financing profit":
        return "financing_profit", True
    if s == "opm %":
        return "opm_pct", False
    if s == "financing margin %":
        return "financing_margin_pct", True
    if s == "other income":
        return "other_income", False
    if s == "depreciation":
        return "depreciation", False
    if s == "profit before tax":
        return "pbt", False
    if s == "tax %":
        return "tax_pct", False
    if s == "net profit":
        return "net_profit", False
    if s.startswith("eps"):
        return "eps", False
    if s == "gross npa %":
        return "gross_npa_pct", True
    if s == "net npa %":
        return "net_npa_pct", True
    if s == "raw pdf":
        return "raw_pdf", False
    return None, False


def _parse_section_table(html, section_id):
    """Parse <section id=section_id> table into (headers, rows_dict, raw_pdfs, is_bank)."""
    sm = re.search(r'<section[^>]*id="%s"[^>]*>(.*?)</section>' % re.escape(section_id), html, re.S)
    if not sm:
        return [], {}, {}, False
    sec = sm.group(1)
    tm = re.search(r"<table[^>]*>(.*?)</table>", sec, re.S)
    if not tm:
        return [], {}, {}, False
    tbl = tm.group(1)
    thead = re.search(r"<thead[^>]*>(.*?)</thead>", tbl, re.S)
    raw_ths = re.findall(r"<th[^>]*>(.*?)</th>", thead.group(1) if thead else tbl, re.S)
    headers = [_strip_tags(t) for t in raw_ths]
    if headers and headers[0] == "":
        headers = headers[1:]
    rows = {}
    pdfs = {}
    is_bank = False
    tbody = re.search(r"<tbody[^>]*>(.*?)</tbody>", tbl, re.S)
    trs = re.findall(r"<tr[^>]*>(.*?)</tr>", tbody.group(1) if tbody else tbl, re.S)
    for tr in trs:
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        if not tds:
            continue
        label = _strip_tags(tds[0])
        key, bank_flag = _normalize_row_key(label)
        if bank_flag:
            is_bank = True
        if not key:
            continue
        vals = tds[1:1 + len(headers)]
        if key == "raw_pdf":
            for h, cell in zip(headers, vals):
                hm = re.search(r'href="([^"]+)"', cell)
                if hm:
                    href = hm.group(1)
                    if href.startswith("/"):
                        href = "https://www.screener.in" + href
                    pdfs[h] = href
        else:
            rows[key] = [_parse_num(_strip_tags(c)) for c in vals]
    return headers, rows, pdfs, is_bank


def parse_top_ratios(html):
    ratios = {}
    top = re.search(r'<div[^>]*class="company-ratios"[^>]*>(.*?)</div>\s*</div>', html, re.S)
    if not top:
        return ratios
    for li in re.findall(r"<li[^>]*>(.*?)</li>", top.group(1), re.S):
        nm = re.search(r'<span[^>]*class="name"[^>]*>(.*?)</span>', li, re.S)
        vm = re.search(r'<span[^>]*class="nowrap value"[^>]*>(.*?)</span>', li, re.S) or \
             re.search(r'<span[^>]*class="value"[^>]*>(.*?)</span>', li, re.S)
        if nm and vm:
            k = _strip_tags(nm.group(1))
            v = _strip_tags(vm.group(1))
            ratios[k] = v
    return ratios


def parse_documents(html):
    """Extract Announcements, Concalls (Transcript/PPT), and Annual Reports from #documents."""
    out = {"announcements": [], "concall_transcript": None, "concall_ppt": None, "annual_report": None}
    dm = re.search(r'<section[^>]*id="documents"[^>]*>(.*?)</section>', html, re.S)
    if not dm:
        return out
    sec = dm.group(1)
    # Announcements
    ann_m = re.search(r"<h3[^>]*>\s*Announcements\s*</h3>(.*?)(?=<h3|$)", sec, re.S | re.I)
    if ann_m:
        for href, inner in re.findall(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', ann_m.group(1), re.S):
            title = _strip_tags(inner)
            if title and title.lower() not in ("all", "recent", "important", "search") and not href.startswith("#"):
                out["announcements"].append({"title": title, "url": href})
            if len(out["announcements"]) >= 3:
                break
    # Concalls
    cc_m = re.search(r"<h3[^>]*>\s*Concalls\s*</h3>(.*?)(?=<h3|$)", sec, re.S | re.I)
    if cc_m:
        for href, inner in re.findall(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', cc_m.group(1), re.S):
            t = _strip_tags(inner).lower()
            if "transcript" in t and not out["concall_transcript"]:
                out["concall_transcript"] = href
            elif "ppt" in t and not out["concall_ppt"]:
                out["concall_ppt"] = href
    # Annual reports
    ar_m = re.search(r"<h3[^>]*>\s*Annual reports\s*</h3>(.*?)(?=<h3|$)", sec, re.S | re.I)
    if ar_m:
        for href, inner in re.findall(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', ar_m.group(1), re.S):
            t = _strip_tags(inner)
            if "annual report" in t.lower():
                out["annual_report"] = {"title": t, "url": href}
                break
    return out


def pct(a, b):
    if a is None or b in (None, 0):
        return None
    return round(100.0 * (a - b) / abs(b), 1)


def margin(part, whole):
    if part is None or not whole:
        return None
    return round(100.0 * part / whole, 1)


def parse_screener_company(symbol, fetcher, standalone=False, quarters_limit=6):
    enc = urllib.parse.quote(symbol, safe="")
    basis = "standalone" if standalone else "consolidated"
    url = (SCREENER_STANDALONE_URL if standalone else SCREENER_CONSOLIDATED_URL) % enc
    html = fetcher.text(url, offline_name="screener-%s-%s.html" % (symbol, basis), allow_404=not standalone)
    q_headers, q_rows, q_pdfs, is_bank = ([], {}, {}, False)
    if html:
        q_headers, q_rows, q_pdfs, is_bank = _parse_section_table(html, "quarters")
    # Fallback to standalone if consolidated has no quarterly columns
    if not standalone and not q_headers:
        basis = "standalone"
        url = SCREENER_STANDALONE_URL % enc
        html = fetcher.text(url, offline_name="screener-%s-standalone.html" % symbol, allow_404=False)
        q_headers, q_rows, q_pdfs, is_bank = _parse_section_table(html, "quarters")
    if not q_headers:
        raise FactsError("no quarterly results table found on Screener.in for %s" % symbol)

    h1_m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
    entity = _strip_tags(h1_m.group(1)) if h1_m else symbol
    a_headers, a_rows, _, a_bank = _parse_section_table(html, "profit-loss")
    is_bank = is_bank or a_bank
    ratios = parse_top_ratios(html)
    docs = parse_documents(html)

    # Build chronological list of quarters
    n_cols = len(q_headers)
    chron = []
    for i, h in enumerate(q_headers):
        end_iso, fiscal = _period_to_date_and_fiscal(h)

        def val(k):
            arr = q_rows.get(k)
            return arr[i] if arr and i < len(arr) else None

        rev = val("revenue")
        exp = val("expenses")
        op = val("financing_profit") if "financing_profit" in q_rows else val("operating_profit")
        opm_reported = val("financing_margin_pct") if "financing_margin_pct" in q_rows else val("opm_pct")
        oi = val("other_income")
        intr = val("interest")
        dep = val("depreciation")
        pbt = val("pbt")
        tax_pct = val("tax_pct")
        np = val("net_profit")
        eps = val("eps")
        gnpa = val("gross_npa_pct")
        nnpa = val("net_npa_pct")
        nii = (round(rev - intr, 2) if (is_bank and rev is not None and intr is not None) else None)

        op_margin = margin(op, rev) if (op is not None and rev) else opm_reported
        net_margin = margin(np, rev)

        chron.append({
            "period": h,
            "end": end_iso,
            "fiscal": fiscal,
            "revenue": rev,
            "interest": intr,
            "nii": nii,
            "expenses": exp,
            "operating_profit": op,
            "operating_margin": op_margin,
            "opm_reported_pct": opm_reported,
            "other_income": oi,
            "depreciation": dep,
            "pbt": pbt,
            "tax_pct": tax_pct,
            "net_income": np,
            "net_margin": net_margin,
            "eps_diluted": eps,
            "gross_npa_pct": gnpa,
            "net_npa_pct": nnpa,
            "raw_pdf": q_pdfs.get(h),
        })

    # Compute QoQ and YoY for each quarter
    by_period = {c["period"]: c for c in chron}
    for i, c in enumerate(chron):
        prev_q = chron[i - 1] if i > 0 else None
        # YoY match: same month name, year - 1
        m = re.match(r"^([A-Za-z]{3})\s+(\d{4})$", c["period"])
        yoy_q = by_period.get("%s %d" % (m.group(1), int(m.group(2)) - 1)) if m else (chron[i - 4] if i >= 4 else None)

        c["revenue_qoq"] = pct(c["revenue"], prev_q["revenue"] if prev_q else None)
        c["revenue_yoy"] = pct(c["revenue"], yoy_q["revenue"] if yoy_q else None)
        c["operating_profit_qoq"] = pct(c["operating_profit"], prev_q["operating_profit"] if prev_q else None)
        c["operating_profit_yoy"] = pct(c["operating_profit"], yoy_q["operating_profit"] if yoy_q else None)
        c["net_income_qoq"] = pct(c["net_income"], prev_q["net_income"] if prev_q else None)
        c["net_income_yoy"] = pct(c["net_income"], yoy_q["net_income"] if yoy_q else None)
        c["eps_qoq"] = pct(c["eps_diluted"], prev_q["eps_diluted"] if prev_q else None)
        c["eps_yoy"] = pct(c["eps_diluted"], yoy_q["eps_diluted"] if yoy_q else None)

    # TTM from last 4 quarters or annual TTM column
    ttm = {}
    last4 = chron[-4:] if len(chron) >= 4 else []
    if len(last4) == 4:
        for k in ("revenue", "operating_profit", "net_income", "eps_diluted"):
            vals = [q[k] for q in last4 if q[k] is not None]
            if len(vals) == 4:
                ttm[k] = round(sum(vals), 2)
    # Also parse annual P&L rows (last 3 annual periods + TTM if present)
    annual = []
    for i, ah in enumerate(a_headers):
        def aval(k):
            arr = a_rows.get(k)
            return arr[i] if arr and i < len(arr) else None
        rev = aval("revenue")
        op = aval("financing_profit") if "financing_profit" in a_rows else aval("operating_profit")
        np = aval("net_profit")
        eps = aval("eps")
        if rev is None and np is None:
            continue
        row_a = {
            "period": ah,
            "revenue": rev,
            "operating_profit": op,
            "operating_margin": margin(op, rev),
            "net_income": np,
            "net_margin": margin(np, rev),
            "eps_diluted": eps,
        }
        if ah.upper() == "TTM":
            for k in ("revenue", "operating_profit", "net_income", "eps_diluted"):
                if row_a[k] is not None:
                    ttm[k] = row_a[k]
        else:
            annual.append(row_a)
    if ttm.get("revenue"):
        ttm["operating_margin"] = margin(ttm.get("operating_profit"), ttm.get("revenue"))
        ttm["net_margin"] = margin(ttm.get("net_income"), ttm.get("revenue"))

    rows_desc = list(reversed(chron))[:quarters_limit]
    return {
        "entity": entity,
        "symbol": symbol,
        "basis": basis,
        "is_bank": is_bank,
        "unit": "INR_crore",
        "screener_url": url,
        "ratios": ratios,
        "rows": rows_desc,
        "ttm": ttm,
        "annual": annual[-4:],
        "documents": docs,
    }


def analyse_events(raw_events, ref_date):
    """Parse NSE event calendar entries relative to ref_date."""
    parsed = []
    for ev in raw_events or []:
        d_str = (ev.get("date") or "").strip()
        try:
            dt = _dt.datetime.strptime(d_str, "%d-%b-%Y").date()
        except ValueError:
            continue
        purpose = (ev.get("purpose") or "").strip()
        desc = (ev.get("bm_desc") or "").strip()
        is_results = ("result" in purpose.lower() or "result" in desc.lower() or "quarterly" in desc.lower())
        parsed.append({
            "date": dt.isoformat(),
            "purpose": purpose,
            "description": desc,
            "is_results": is_results,
            "days_from_now": (dt - ref_date).days,
        })
    parsed.sort(key=lambda x: x["date"])
    upcoming = [e for e in parsed if e["days_from_now"] >= 0]
    this_week = [e for e in upcoming if 0 <= e["days_from_now"] <= 7]
    results_this_week = [e for e in this_week if e["is_results"]]
    past_results = [e for e in parsed if e["days_from_now"] < 0 and e["is_results"]]
    return {
        "as_of": ref_date.isoformat(),
        "reports_this_week": bool(results_this_week),
        "this_week_events": this_week,
        "upcoming_events": upcoming[:5],
        "latest_past_results_event": past_results[-1] if past_results else None,
    }


# ---------------------------------------------------------------- summary & rendering

def _cr(v):
    if v is None:
        return "-"
    return "₹{:,.0f} Cr".format(v) if abs(v) >= 10 else "₹{:,.2f} Cr".format(v)


def _num_col(v):
    if v is None:
        return "-"
    return "{:,.0f}".format(v) if abs(v) >= 10 else "{:,.2f}".format(v)


def _p(v):
    return "-" if v is None else ("%+.1f%%" % v if v != 0 else "0.0%")


def _pm(v):
    return "-" if v is None else "%.1f%%" % v


def changes(result, events_info=None):
    rows = result["rows"]
    if not rows:
        return []
    cur = rows[0]
    prev = rows[1] if len(rows) > 1 else None
    is_bank = result.get("is_bank", False)
    out = []

    rev_label = "Revenue (Interest Earned)" if is_bank else "Sales/Revenue"
    op_label = "Financing profit" if is_bank else "Operating profit"
    opm_label = "Financing margin" if is_bank else "Operating margin (OPM)"

    line = "Quarter %s (%s, ending %s): %s %s" % (
        cur["period"], cur["fiscal"], cur["end"], rev_label, _cr(cur["revenue"])
    )
    if cur["revenue_yoy"] is not None:
        line += " (%+.1f%% YoY" % cur["revenue_yoy"]
        if cur["revenue_qoq"] is not None:
            line += ", %+.1f%% QoQ)" % cur["revenue_qoq"]
        else:
            line += ")"
    if is_bank and cur.get("nii") is not None:
        line += ", Net Interest Income (Rev - Int) %s" % _cr(cur["nii"])
    line += ", Net Profit %s" % _cr(cur["net_income"])
    if cur["net_income_yoy"] is not None:
        line += " (%+.1f%% YoY" % cur["net_income_yoy"]
        if cur["net_income_qoq"] is not None:
            line += ", %+.1f%% QoQ)" % cur["net_income_qoq"]
        else:
            line += ")"
    if cur["eps_diluted"] is not None:
        line += ", EPS ₹%.2f" % cur["eps_diluted"]
        if cur["eps_yoy"] is not None:
            line += " (%+.1f%% YoY)" % cur["eps_yoy"]
    out.append(line + ".")

    if cur["operating_margin"] is not None:
        s = "%s %.1f%% (%s %s)" % (opm_label, cur["operating_margin"], op_label, _cr(cur["operating_profit"]))
        if prev and prev["operating_margin"] is not None:
            s += " (%+.1f pts QoQ)" % (cur["operating_margin"] - prev["operating_margin"])
        out.append(s + ".")

    if cur["net_margin"] is not None:
        s = "Net profit margin %.1f%%" % cur["net_margin"]
        if prev and prev["net_margin"] is not None:
            s += " (%+.1f pts QoQ)" % (cur["net_margin"] - prev["net_margin"])
        out.append(s + ".")

    if is_bank and (cur.get("gross_npa_pct") is not None or cur.get("net_npa_pct") is not None):
        out.append("Asset quality: Gross NPA %s, Net NPA %s." % (
            _pm(cur.get("gross_npa_pct")), _pm(cur.get("net_npa_pct"))
        ))
    elif is_bank and result.get("basis") == "consolidated":
        out.append("Bank consolidated view includes insurance/subsidiary income in Other Income (%s) and "
                   "omits standalone NPA %% — run with --standalone for standalone bank Financing Margin & NPAs."
                   % _cr(cur.get("other_income")))

    if not is_bank and cur.get("other_income") and cur.get("pbt") and cur["pbt"] > 0:
        oi_share = 100.0 * cur["other_income"] / cur["pbt"]
        if oi_share >= 20.0:
            out.append("Other income is %s (%.1f%% of Profit Before Tax): check whether treasury gains or "
                       "one-offs boosted net profit." % (_cr(cur["other_income"]), oi_share))

    if events_info:
        if events_info.get("reports_this_week"):
            for ev in events_info.get("this_week_events", []):
                if ev.get("is_results"):
                    out.append("EARNINGS ALERT: Reports THIS WEEK on %s (%s — %s)." % (
                        ev["date"], ev["purpose"], ev["description"]
                    ))
        elif events_info.get("upcoming_events"):
            ev = events_info["upcoming_events"][0]
            out.append("Next NSE scheduled board meeting: %s (%s — %s)." % (
                ev["date"], ev["purpose"], ev["description"]
            ))
        elif events_info.get("latest_past_results_event"):
            ev = events_info["latest_past_results_event"]
            out.append("Last NSE board meeting for results: %s (%s)." % (ev["date"], ev["purpose"]))

    return out


def render(result, events_info=None):
    sym = result["symbol"]
    is_bank = result.get("is_bank", False)
    out = [
        "%s (%s) [%s]. Values in ₹ crore except EPS (₹). Source: Screener.in." % (
            result["entity"], sym, result["basis"]
        ),
        "",
    ]
    op_hdr = "fin prof" if is_bank else "oper prof"
    opm_hdr = "fin mgn" if is_bank else "OPM %"
    out.append("%-9s %-8s %10s %8s %8s %10s %7s %10s %8s %7s %8s %8s" % (
        "quarter", "fiscal", "revenue", "rev yoy", "rev qoq", op_hdr, opm_hdr,
        "net prof", "net yoy", "net mgn", "EPS (₹)", "EPS yoy"
    ))
    for r in result["rows"]:
        out.append("%-9s %-8s %10s %8s %8s %10s %7s %10s %8s %7s %8s %8s" % (
            r["period"], r["fiscal"],
            _num_col(r["revenue"]), _p(r["revenue_yoy"]), _p(r["revenue_qoq"]),
            _num_col(r["operating_profit"]), _pm(r["operating_margin"]),
            _num_col(r["net_income"]), _p(r["net_income_yoy"]), _pm(r["net_margin"]),
            "-" if r["eps_diluted"] is None else "%.2f" % r["eps_diluted"],
            _p(r["eps_yoy"]),
        ))

    t = result.get("ttm") or {}
    if t:
        out.append("")
        out.append("TTM (last 4 quarters): revenue %s, %s %s (%s), net profit %s (%s), EPS %s." % (
            _cr(t.get("revenue")),
            "financing profit" if is_bank else "operating profit",
            _cr(t.get("operating_profit")),
            _pm(t.get("operating_margin")),
            _cr(t.get("net_income")),
            _pm(t.get("net_margin")),
            ("₹%.2f" % t["eps_diluted"]) if t.get("eps_diluted") is not None else "-",
        ))

    if result.get("annual"):
        ann_str = ", ".join(
            "%s: rev %s / PAT %s / EPS %s" % (
                a["period"], _cr(a["revenue"]), _cr(a["net_income"]),
                ("₹%.2f" % a["eps_diluted"]) if a["eps_diluted"] is not None else "-"
            )
            for a in result["annual"][-3:]
        )
        out.append("Annual P&L: " + ann_str)

    out.append("")
    out.append("What changed:")
    for line in changes(result, events_info):
        out.append("  - " + line)

    docs = result.get("documents") or {}
    latest_pdf = result["rows"][0].get("raw_pdf") if result["rows"] else None
    out.append("")
    out.append("Links & Filings:")
    out.append("  - Screener page: %s" % result["screener_url"])
    if latest_pdf:
        out.append("  - Latest Quarter Result PDF: %s" % latest_pdf)
    if docs.get("concall_ppt"):
        out.append("  - Latest Investor Presentation (PPT): %s" % docs["concall_ppt"])
    if docs.get("concall_transcript"):
        out.append("  - Latest Concall Transcript: %s" % docs["concall_transcript"])
    if docs.get("annual_report"):
        ar = docs["annual_report"]
        out.append("  - %s: %s" % (ar["title"], ar["url"]))
    for ann in (docs.get("announcements") or [])[:2]:
        out.append("  - Exchange Announcement: %s (%s)" % (ann["title"], ann["url"]))

    return "\n".join(out) + "\n"


def metrics_row(result, events_info=None):
    rows = result["rows"]
    cur = rows[0] if rows else {}
    t = result.get("ttm") or {}
    ev = events_info or {}
    next_ev = (ev.get("upcoming_events") or [None])[0]
    return {
        "ticker": result["symbol"],
        "symbol": result["symbol"],
        "name": result["entity"],
        "basis": result["basis"],
        "unit": "INR_crore",
        "period_end": cur.get("end"),
        "quarter": cur.get("period"),
        "fiscal": cur.get("fiscal"),
        "revenue_ttm_cr": t.get("revenue"),
        "operating_profit_ttm_cr": t.get("operating_profit"),
        "net_income_ttm_cr": t.get("net_income"),
        "eps_ttm": t.get("eps_diluted"),
        "revenue_growth_yoy": cur.get("revenue_yoy"),
        "revenue_growth_qoq": cur.get("revenue_qoq"),
        "operating_profit_growth_yoy": cur.get("operating_profit_yoy"),
        "net_income_growth_yoy": cur.get("net_income_yoy"),
        "net_income_growth_qoq": cur.get("net_income_qoq"),
        "eps_growth_yoy": cur.get("eps_yoy"),
        "operating_margin": cur.get("operating_margin"),
        "net_margin": cur.get("net_margin"),
        "reports_this_week": bool(ev.get("reports_this_week")),
        "next_event_date": next_ev["date"] if next_ev else None,
        "next_event_purpose": next_ev["purpose"] if next_ev else None,
        "source": "Screener.in (%s)" % result["basis"],
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("tickers", nargs="*", help="NSE/BSE symbols, e.g. RELIANCE TCS HDFCBANK SBIN")
    ap.add_argument("--quarters", type=int, default=6, help="number of recent quarters to display (default 6)")
    ap.add_argument("--standalone", action="store_true",
                    help="use standalone Screener page instead of consolidated primary")
    ap.add_argument("--no-filings", action="store_true", help="skip NSE event calendar lookup")
    ap.add_argument("--metrics", metavar="FILE", help="merge a row per symbol into this JSON file")
    ap.add_argument("--now", help="reference date in IST (YYYY-MM-DD) for 'reports this week' check")
    ap.add_argument("--config", default="trader/config.json")
    ap.add_argument("--offline", metavar="DIR")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if not args.tickers:
        ap.print_help()
        return 2

    ref_date = today_ist(args.now)
    fetcher = Fetcher(offline=args.offline)
    results, rows, failed = [], [], 0

    for raw_t in args.tickers:
        sym = clean_symbol(raw_t)
        try:
            result = parse_screener_company(
                sym, fetcher, standalone=args.standalone, quarters_limit=args.quarters
            )
            raw_events = [] if args.no_filings else fetcher.nse_events(sym)
            events_info = analyse_events(raw_events, ref_date) if not args.no_filings else None
        except FactsError as exc:
            print("facts: %s: %s" % (sym, exc), file=sys.stderr)
            failed += 1
            continue

        row_m = metrics_row(result, events_info)
        results.append({
            "symbol": sym,
            "ticker": sym,
            "result": result,
            "events": events_info,
            "changes": changes(result, events_info),
        })
        rows.append(row_m)
        if not args.json:
            sys.stdout.write(render(result, events_info))
            sys.stdout.write("\n")

    if args.json:
        print(json.dumps(results, indent=2))

    if args.metrics and rows:
        existing = {}
        if os.path.isfile(args.metrics):
            try:
                with open(args.metrics, encoding="utf-8-sig") as fh:
                    existing = {(r.get("symbol") or r.get("ticker")): r for r in json.load(fh).get("companies", [])}
            except Exception:
                existing = {}
        for r in rows:
            existing[r["symbol"]] = r
        folder = os.path.dirname(args.metrics)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(args.metrics, "w", encoding="utf-8") as fh:
            json.dump({
                "as_of": ref_date.isoformat(),
                "unit": "INR_crore",
                "companies": sorted(existing.values(), key=lambda r: r.get("symbol") or r.get("ticker", "")),
            }, fh, indent=2)
            fh.write("\n")
        if not args.json:
            print("wrote %s (%d companies)" % (args.metrics, len(existing)))
    return 0 if results and not failed else (1 if results else 2)


if __name__ == "__main__":
    sys.exit(main())
