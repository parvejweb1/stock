---
name: trader-analyst
description: >-
  The Earnings Analyst (India / NSE & BSE). Reads a company's reported numbers
  straight from Screener.in (consolidated primary with standalone fallback:
  quarterly results and annual P&L in ₹ crore) plus NSE's corporate event
  calendar (`/api/event-calendar`) and BSE/NSE exchange filings, and sums up
  what changed: Sales/Revenue (or Interest Earned & NII for banks), Operating
  Profit (or Financing Profit for banks), OPM %, Other Income, Net Profit, Net
  Margin % and EPS (₹), quarter by quarter, with YoY and QoQ growth, Trailing
  Twelve Months (TTM), and Gross/Net NPA % for banks. Checks whether the company
  reports this week on NSE and reads a concall transcript when you paste one.
  Use when the user says "how did Reliance's earnings look", "analyze TCS
  results", "what changed this quarter", "is revenue growing", "check the
  quarterly results", "compare OPM margins", "sum up the concall", "does SBIN
  report this week", or before trading an Indian stock around its results.
argument-hint: "[symbols]"
---

# trader-analyst

Quarterly results move stocks more than anything else on the calendar, and the
media headline is written to put the best number first. This skill reads the
reported quarterly and annual numbers (in **₹ crore**, EPS in **₹**) from
Screener.in (`consolidated` primary, `standalone` fallback) alongside the NSE
event calendar (`/api/event-calendar`), lines them up quarter by quarter using
the Indian fiscal calendar (`Q1` = Apr–Jun, `Q2` = Jul–Sep, `Q3` = Oct–Dec,
`Q4` = Jan–Mar), and says plainly what changed. For a trader it answers two
questions before a trade: what did the last quarterly report say, and does the
next board meeting for results fall inside this trade?

Tool in this folder (standard library Python, no API keys):

```bash
python "${CLAUDE_SKILL_DIR}/facts.py" --help
```

## What it reads and writes

- **Primary financials (`Screener.in`):**
  Reads `https://www.screener.in/company/<SYMBOL>/consolidated/` first and
  automatically falls back to `https://www.screener.in/company/<SYMBOL>/`
  (standalone) when consolidated quarterly rows are absent (or when
  `--standalone` is passed). Parses:
  - Quarterly results (`#quarters`): Sales/Revenue, Expenses, Operating Profit,
    OPM %, Other Income, Interest, Depreciation, Profit Before Tax, Tax %, Net
    Profit, EPS (₹), and links to the exchange result PDF (`Raw PDF`).
  - Banks & NBFCs: Revenue (Interest Earned), Interest Expended, Net Interest
    Income (`Revenue - Interest`), Financing Profit, Financing Margin %, Other
    Income, Net Profit, EPS (₹), and Gross / Net NPA % (available on standalone
    bank pages via `--standalone`).
  - Annual P&L (`#profit-loss`) and Trailing Twelve Months (`TTM`).
  - Documents (`#documents`): links to the latest Investor Presentation (`PPT`),
    Concall `Transcript`, `Annual Report`, and recent BSE/NSE Regulation 30
    exchange announcements.
- **Upcoming result dates (`NSE Event Calendar`):**
  Calls `https://www.nseindia.com/api/event-calendar?symbol=<SYMBOL>` (after an
  NSE homepage cookie handshake, handling 401/403 gracefully) to flag whether
  the company has a Board Meeting for Financial Results scheduled **this week**
  (within 7 days) or upcoming.
- **Metrics file (`trader/metrics.json`):**
  With `--metrics trader/metrics.json`, merges one row per symbol (TTM revenue,
  operating profit and net profit in ₹ crore, TTM EPS in ₹, YoY/QoQ growth,
  margins, and `reports_this_week`). `metrics.example.json` in this folder is a
  live run for `RELIANCE`, `TCS`, `HDFCBANK`, and `SBIN`.
- **Concall transcripts:**
  If the user pastes an earnings concall transcript in chat (or opens the linked
  BSE concall transcript PDF), read it and sum up what management said changed:
  demand/order book, margin drivers, capex, guidance, and any questions they
  sidestepped.

## The rules

- **Reported numbers first (in ₹ crore).** State clearly whether the numbers are
  `consolidated` or `standalone`.
- **Compare like with like.** Primary growth comparison is **YoY** (against the
  same quarter a year earlier, e.g. `Sep 2026` / `Q2 FY27` vs `Sep 2025` /
  `Q2 FY26`), alongside **QoQ** (against the immediately preceding quarter).
  Many Indian sectors are seasonal (festival demand in Q2/Q3, IT furloughs in
  Q3, March year-end push in Q4).
- **Handle banks properly.** Banks report `Revenue` (Interest Earned),
  `Interest` (Interest Expended), and `Financing Profit` rather than gross
  margin. Note that on Screener's `consolidated` view for banks (`HDFCBANK`,
  `SBIN`), subsidiary/insurance revenues sit in `Other Income` and standalone
  NPA % is omitted — run `--standalone` when evaluating core bank Financing
  Margin % and Gross/Net NPA %.
- **Flag Other Income spikes.** When `Other Income` is a large share (≥20%) of
  Profit Before Tax, point it out so treasury gains or one-offs are not misread
  as operating growth.
- **Flag upcoming earnings.** If NSE's event calendar shows a Financial Results
  board meeting within the next 7 days (`EARNINGS ALERT: Reports THIS WEEK`),
  warn the user before `/trader-exit` or `/trader-budget`: a stop-loss order
  does not protect against an overnight gap, and even intraday results released
  during market hours can jump past a stop.

## Steps

1. Run `facts.py` for the symbols in question (or the symbols on
   `trader/watchlist.json`):

   ```bash
   python "${CLAUDE_SKILL_DIR}/facts.py" RELIANCE
   python "${CLAUDE_SKILL_DIR}/facts.py" HDFCBANK --standalone
   python "${CLAUDE_SKILL_DIR}/facts.py" RELIANCE TCS HDFCBANK SBIN --metrics trader/metrics.json
   ```

   `--quarters 8` shows two years, `--standalone` forces standalone numbers,
   `--json` prints machine-readable JSON, `--now YYYY-MM-DD` pins the reference
   date for the "reports this week" check.
2. Check the linked Quarter Result PDF, Investor Presentation (PPT), or Concall
   Transcript when the numbers need segment or guidance context. If the user
   pasted a concall transcript, summarize management's commentary alongside the
   reported numbers.
3. Write the summary: one line on the latest quarter (Revenue in ₹ Cr, Net
   Profit in ₹ Cr, EPS in ₹, YoY and QoQ growth), one on margins (OPM % and Net
   Margin %, or NII / Financing Margin / NPAs for banks), one on any unusual
   item (high Other Income, consolidated vs standalone bank differences), and
   whether it reports this week.

## Example (run 2026-11-01)

- `facts.py RELIANCE`: Q2 FY27 (`Sep 2026`) consolidated revenue ₹3,30,601 Cr
  (+29.8% YoY, +6.8% QoQ), operating profit ₹56,616 Cr (OPM 17.1%, +1.7 pts
  QoQ), net profit ₹29,550 Cr (+33.8% YoY, +27.4% QoQ), EPS ₹18.88 (+40.7%
  YoY). TTM revenue ₹11,99,033 Cr, PAT ₹95,625 Cr, EPS ₹60.68. Last board
  meeting for results was 2026-10-30.
- `facts.py SBIN`: Q1 FY27 (`Jun 2026`) consolidated revenue ₹1,36,240 Cr (+8.4%
  YoY), NII ₹53,992 Cr, net profit ₹25,121 Cr (+13.6% YoY), EPS ₹26.12 (+9.9%
  YoY). NSE Event Calendar flags: **`EARNINGS ALERT: Reports THIS WEEK on
  2026-11-03`** (Q2 FY27 Financial Results). Any swing (CNC) hold across Nov 3
  or intraday trade during the board meeting announcement carries earnings gap
  risk.

## What it never does

- Never says a stock is cheap, expensive, a buy or a sell. It reports what
  the company filed.
- Never invents a number the filings do not have.

## Safety

Educational tool, not SEBI-registered investment advice. Trading loses most
people money. This skill only reads financial results and event calendars.
Nothing places an order except `/trader-broker`, after `/trader-risk` passes
and the user types an explicit yes for that order.
