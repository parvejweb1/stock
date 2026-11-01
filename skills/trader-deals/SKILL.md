---
name: trader-deals
description: >-
  The Smart Money & Insider Tracker (India). Tracks NSE Bulk Deals, Block Deals,
  SEBI PIT (Prohibition of Insider Trading Reg 7(2)) disclosures, and SEBI SAST
  Regulation 29 substantial stake changes (≥5% or ±2%). Alerts once per new
  filing via trader/state/deals.json, turns exchange CSVs/JSON/XBRL into clean
  rows (date, symbol, client/person, category, buy/sell, qty, price, value in
  ₹ crore, mode), highlights ★ PROMOTER BUY activity, follows big investors
  (FIIs, Mutual Funds, superstar HNIs), and flags stocks in trader/portfolio.json
  and trader/watchlist.json. Use when the user says "bulk deal", "block deal",
  "promoter buying", "insider trading", "SAST filings", "FII kya kharid rahe",
  "mutual fund buying", "big investor trades", "superstar investor portfolio",
  "kiske bulk deals aaye", "promoter ne kya kharida", or "run trader-deals".
argument-hint: "[all|bulk|block|insider|sast|new] [--symbol SYM] [--client NAME] [--min-value CR] [--promoter]"
---

# trader-deals

Tracks institutional, insider, and substantial-shareholder disclosures on the
National Stock Exchange of India (NSE):

1. **Bulk Deals** — single-day market trades exceeding **0.5%** of a company's
   listed equity shares, disclosed to the exchange after market hours.
2. **Block Deals** — negotiated institutional trades executed in NSE's Block
   Deal windows (08:45–09:00 AM and 02:05–02:20 PM IST; minimum order size
   **₹10 crore**).
3. **Insider Trading (SEBI PIT Reg 7(2))** — mandatory disclosures by
   **Promoters, Promoter Group, Directors, KMPs, and Designated Persons** when
   trades exceed ₹10 lakh in a calendar quarter (handles both NSE's legacy PIT
   API and the May 2026+ PIT V2.0 XBRL disclosures).
4. **SAST Regulation 29** — SEBI Substantial Acquisition of Shares and Takeovers
   filings when an acquirer crosses **5%** shareholding (`Reg 29(1)`) or changes
   an existing ≥5% stake by **±2%** (`Reg 29(2)`).

Public filings are fast, not early: bulk and block deals are posted after the
session closes, and SEBI PIT / SAST disclosures are filed within **2 working
days** of the transaction. This is public post-trade data, never inside
information. Say that every time.

Tool in this folder (standard library Python only, IST timezone, UTF-8 `₹`):

```bash
python "${CLAUDE_SKILL_DIR}/deals.py" --help
```

## What it reads and writes

- **Bulk & Block Deals**:
  - `https://archives.nseindia.com/content/equities/bulk.csv` and `.../block.csv`
    (with fallback to `nsearchives.nseindia.com`).
  - Range queries via `https://www.nseindia.com/api/historical/bulk-deals?from=dd-mm-yyyy&to=dd-mm-yyyy`
    (and `block-deals`), with automatic fallback to NSE's
    `https://www.nseindia.com/api/historicalOR/bulk-block-short-deals`.
- **Insider Trading (SEBI PIT)**:
  - `https://www.nseindia.com/api/corporates-pit?index=equities&from_date=dd-mm-yyyy&to_date=dd-mm-yyyy`
    (optional `&symbol=`), plus NSE's PIT V2.0 endpoint
    `https://www.nseindia.com/api/corporates-pit-gg?index=equities&from_date=...`
    and its XBRL XML filings on `nsearchives.nseindia.com/corporate/xbrl/`.
- **SAST Regulation 29**:
  - `https://www.nseindia.com/api/corporate-sast-reg29?index=equities&from_date=dd-mm-yyyy&to_date=dd-mm-yyyy`.
- **Portfolio & Watchlist**:
  - Reads `trader/portfolio.json` (`[HELD]`) and `trader/watchlist.json`
    (`[WATCHLIST]`) to highlight activity in stocks the user holds or tracks.
  - Reads `trader/prices.json` (if present) to estimate rupee values on SAST
    share-count disclosures.
- **State**:
  - Writes `trader/state/deals.json` when run with `new` or `--new` so each
    filing alerts only once.

## Step 1: scan bulk, block, insider & SAST deals

```bash
python "${CLAUDE_SKILL_DIR}/deals.py" all --days 5
python "${CLAUDE_SKILL_DIR}/deals.py" bulk --days 3
python "${CLAUDE_SKILL_DIR}/deals.py" block --days 5
python "${CLAUDE_SKILL_DIR}/deals.py" insider --days 5
python "${CLAUDE_SKILL_DIR}/deals.py" sast --days 7
```

## Step 2: filter by symbol, promoter buying, investor, or deal size

```bash
# All deals & filings for a specific NSE stock
python "${CLAUDE_SKILL_DIR}/deals.py" all --symbol SWIGGY --days 5
python "${CLAUDE_SKILL_DIR}/deals.py" insider --symbol JINDALSTEL --days 5
python "${CLAUDE_SKILL_DIR}/deals.py" insider --symbol RELIANCE --days 180

# Highlight & filter only Promoter / Promoter Group buying
python "${CLAUDE_SKILL_DIR}/deals.py" all --promoter --buy --days 7

# Follow FIIs, Mutual Funds, or superstar HNI investors ("FII kya kharid rahe")
python "${CLAUDE_SKILL_DIR}/deals.py" all --client "Mutual Fund" --days 5
python "${CLAUDE_SKILL_DIR}/deals.py" all --client "Societe Generale" --days 5
python "${CLAUDE_SKILL_DIR}/deals.py" all --client "Morgan Stanley" --buy --days 5

# High-conviction large deals (>= ₹25 crore)
python "${CLAUDE_SKILL_DIR}/deals.py" all --min-value 25 --days 5
```

## Step 3: watch for new filings only (alert once)

```bash
python "${CLAUDE_SKILL_DIR}/deals.py" new --days 5
python "${CLAUDE_SKILL_DIR}/deals.py" all --new --min-value 10
```

`new` (or `--new`) compares against `trader/state/deals.json`, prints only
unseen rows, and updates the state file (pass `--dry-run` to preview without
saving). Exit code `1` means nothing new since the last check.

## Understanding the columns & categories

Every row is normalized into:
- **Date**: Trade or exchange broadcast date in IST (`YYYY-MM-DD`).
- **Type**: `BULK`, `BLOCK`, `INSIDER`, or `SAST`.
- **Symbol**: Plain NSE symbol (`SWIGGY`, `AURUM`, `EQUITASBNK`).
- **Client / Person**: Acquirer, seller, fund, or insider name.
- **Category**:
  - `Promoter` / `Promoter Group` / `Director` / `KMP` / `Employee/DP`
  - `MF` (Domestic Mutual Funds / AMCs)
  - `FII/FPI` (Foreign Institutional / Portfolio Investors)
  - `DII/Insurance` (LIC, insurers, pension funds)
  - `HNI/Superstar` (marquee Indian investors & family offices)
  - `HFT/Prop` (intraday high-frequency / prop desks like Graviton, QE, Alphagrep, Jump)
- **Side**: `BUY`, `SELL`, or `PLEDGE`.
- **Qty, Price, Value**: Share quantity, trade/implied price in `₹`, and total
  deal value in **₹ Crore** (`1 Cr = ₹1,00,00,000`).
- **Mode / Flags**:
  - Distinguishes `Market Purchase` vs `Off Market`, `ESOP`, `Preferential Allotment`,
    `Block Window`, and `Pledge Creation`.
  - Marks open-market promoter acquisitions with `★ PROMOTER BUY`.
  - Marks held/watched stocks with `[HELD]` or `[WATCHLIST]`.

## Key Indian market nuances to tell the user

- **Separate HFT jobbing from delivery conviction**: In NSE Bulk Deals, prop
  firms (`QE Securities`, `Graviton`, `Alphagrep`, `Mansi Share`, `HRTI`) often
  appear on both BUY and SELL sides on the same day. True institutional
  accumulation occurs when a client (`MF`, `FII/FPI`, `HNI/Superstar`) has a
  net one-sided BUY or executes in the **Block Window**.
- **Open-market Promoter Buying vs ESOP / Off-Market / Pledge**: A promoter
  buying shares via `Market Purchase` (`★ PROMOTER BUY`) is a much stronger
  signal than an inter-se `Off Market` promoter transfer, `ESOP` exercise, or
  `Pledge Creation`.
- **Actionable workflow**: A bulk deal or insider filing is a reason to inspect
  the chart, not an automatic buy trigger. Hand off candidates to
  `/trader-scanner` and `/trader-entry`, plan stops with `/trader-exit`, size
  within the ₹ budget via `/trader-budget`, verify with `/trader-risk`, and
  execute through `/trader-broker` only after explicit user confirmation.

## Disclaimer

Educational tool, not SEBI-registered investment advice. Trading loses most people money.
