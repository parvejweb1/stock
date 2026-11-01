---
name: trader-scanner
description: >-
  Chart Scanner (India / NSE & BSE). Scans NSE charts for setups: opening-range
  breakouts, bull flags, 20-day breakouts, oversold bounces. Builds the universe
  of NSE EQ-series stocks from Nifty 50, Nifty 500 or NSE EQUITY_L.csv, runs a
  daily closes scan for swing (CNC delivery) and intraday (MIS) setups (20-day
  closing-high breakout, bull flag, reclaimed the 20-day average, oversold,
  oversold bounce) and an intraday opening-range breakout (ORB) scan on 5-minute
  bars (09:15–09:30 IST range), then writes trader/watchlist.json for
  trader-news and trader-entry. Free Yahoo (.NS) data, bar by bar. Use when the
  user says "scan the market", "scan Nifty 50", "scan Nifty 500", "find me
  setups on NSE", "what is breaking out", "opening range breakouts", "scan for
  bull flags", "oversold stocks", "pre-market scan", "build my watchlist",
  "what should I look at today", or before /trader-news.
argument-hint: "[universe | daily | orb] [--universe nifty50|nifty500|all] [--at HH:MM]"
---

# trader-scanner

The Chart Scanner is the first step of an intraday (MIS) or swing (CNC delivery)
trade on NSE. It reads hundreds of free `.NS` charts and lists the ones that
match a rule. That is all it does. A scan is a list of charts that match a
rule, not a list of stocks to buy, and a setup on a chart is not a prediction.
Educational tool, not SEBI-registered investment advice. Trading loses most
people money; the scanner only saves you from flipping through 500 charts by
hand.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python "${CLAUDE_SKILL_DIR}/scan.py" universe                                # Nifty 500 (default) -> trader/universe.txt
python "${CLAUDE_SKILL_DIR}/scan.py" universe --universe nifty50             # or nifty500, or all (NSE EQUITY_L.csv)
python "${CLAUDE_SKILL_DIR}/scan.py" daily --universe nifty50                # daily setups -> trader/watchlist.json
python "${CLAUDE_SKILL_DIR}/scan.py" daily --symbols RELIANCE,TCS,SBIN,INFY  # or --watchlist FILE, --limit 100
python "${CLAUDE_SKILL_DIR}/scan.py" orb --universe nifty50                  # after 09:35 IST -> trader/watchlist.json
python "${CLAUDE_SKILL_DIR}/scan.py" orb --universe nifty50 --at 10:15       # cut the clock at 10:15 IST
python "${CLAUDE_SKILL_DIR}/scan.py" orb --watchlist trader/watchlist.json --json
```

Flags: `--universe nifty50|nifty500|all` (default `nifty500`, or a file path),
`--min-price 50` (₹50) and `--min-volume` (daily `500000` = 5,00,000 shares,
orb `0`), `--or-minutes 15` (09:15–09:30 IST), `--max-confirm 200` (orb stage
2), `--now "2026-10-30 10:15"` (IST) to pin the clock, `--offline PATH` to read
saved Yahoo files, `--no-write`, `--show N`, `--json`. Exit code 0 when the
scan found something, 1 when it found nothing or could not run (the reason is
one plain line).

## Swing (CNC) vs Intraday (MIS) Use

- **Swing trading (`trade_type: "swing"`, product `CNC` delivery)**: Run
  `scan.py daily` before the 09:15 IST open or after the 15:30 IST close. Daily
  setups (`breakout_20d`, `bull_flag`, `reclaim_ma20`, `oversold_bounce`) spot
  multi-day momentum and mean-reversion candidates that can be held up to
  `swing_max_hold_days` (default 10 days) without intraday auto square-off.
- **Intraday trading (`trade_type: "intraday"`, product `MIS`)**: Use
  `scan.py daily` before 09:15 IST to build a focused morning watchlist, then
  run `scan.py orb` after 09:35 IST (once the 09:15–09:30 IST opening range and
  the first post-range 5-minute bar at 09:30–09:35 IST have completed) to catch
  intraday opening-range breakouts. All MIS positions must be squared off by
  `flat_by` (default 15:10 IST, ahead of broker auto square-off).

## What it reads and writes

| file | what |
| --- | --- |
| `trader/universe.txt` | written by `universe`: one `SYMBOL\|Name` a line (plain NSE symbols like `RELIANCE`), read by `daily` and `orb` |
| `trader/watchlist.json` | written by `daily` and `orb`: `mode`, `as_of`, `now_ist`, `universe`, `scanned`, `failed`, `hits[]`. `trader-news` and `trader-entry` read `hits[].symbol` |
| `trader/config.json` | read by `orb`: `no_entries_after` (default `14:45` IST) |

## The universe

`universe` downloads official CSVs from `archives.nseindia.com` (using a
browser `User-Agent` so NSE does not block the request):

- `--universe nifty50`: `content/indices/ind_nifty50list.csv` (50 large-cap stocks)
- `--universe nifty500` (default): `content/indices/ind_nifty500list.csv` (~495 EQ-series stocks)
- `--universe all`: `content/equities/EQUITY_L.csv` (~2,357 active EQ-series stocks)

It keeps only rows where `Series == "EQ"` (regular rolling-settlement equities
eligible for both intraday MIS and delivery CNC) and drops `BE`/`BZ`
(trade-to-trade / surveillance segments where intraday is barred), `RR`/`IV`
(REITs and InvITs), SME series, and `DUMMY*` demerger placeholder tickers.
Symbols are stored as plain NSE tickers (`RELIANCE`, `TCS`, `M&M`) and mapped
to Yahoo `.NS` symbols (`RELIANCE.NS`, `M%26M.NS`) when querying Yahoo Finance.

## The daily scan (before 09:15 IST or after 15:30 IST)

Yahoo's spark endpoint returns closes for 20 symbols a request, so Nifty 50
takes 3 requests (~2 seconds) and Nifty 500 takes 25 requests (~12 seconds)
with the polite 0.3 s pause. Liquidity comes from the spark meta: last price at
least `--min-price` (₹50) and last session's volume at least `--min-volume`
(5,00,000 shares). Today's daily bar is left out until the 15:30 IST close.

| setup | the rule on daily closes |
| --- | --- |
| `breakout_20d` | the last close is above every close of the 20 sessions before it |
| `bull_flag` | up more than 10 % over the 10 sessions before the last 5, then the last 5 closes inside a 5 % range |
| `reclaim_ma20` | the close before was under its 20-day average, the last close is above it |
| `oversold` | RSI 14 (Wilder) under 30 |
| `oversold_bounce` | RSI 14 was under 30 yesterday and today closed up |

Hits are sorted by ₹ turnover (`price × volume`) so the most liquid NSE names
appear first, with prices rounded to the ₹0.05 NSE tick.

## The opening-range breakout scan (during the 09:15–15:30 IST session)

The opening range is the first `--or-minutes` (15) of the NSE normal session:
the `09:15`, `09:20` and `09:25` five-minute bars (`09:15 to 09:30 IST`). A hit
is the first completed 5-minute bar to CLOSE above the range high, at that
bar's close time (earliest possible hit is `09:35 IST`).

1. **Stage 1, every symbol**: spark closes of that day's 5-minute bars, 20 a
   request. A chart goes on only if a completed bar after `09:30 IST` closed at
   or above the highest close of the opening range.
2. **Stage 2, each candidate**: the v8 chart with real highs and lows, one
   request each. Range high = highest high of the opening-range bars, range low
   = lowest low. Stage 2 checks at most `--max-confirm` (200) candidates, the
   highest ₹ turnover first, and prints `PARTIAL` with the count skipped if
   more than 200 qualify.

A chart is usable only when every 5-minute bar from `09:15 IST` to the cutoff
is present; a missing bar means an illiquid counter or trading halt. Before
`09:35 IST`, on a weekend or NSE holiday, after the `15:30 IST` close, or after
`no_entries_after` (`14:45 IST` in `trader/config.json`) it prints one plain
line and writes nothing.

## Data and limits

- Yahoo's `.NS` chart and spark endpoints are free, unofficial and can lag.
  Bars are 5-minute bars, bar by bar, never tick by tick. `query2` first,
  `query1` on a 429 or 5xx, one retry after a 5-second wait on a 429.
- Partial results are stated plainly: `"Fetched 48 of 50 charts (2 failed or had no data)"`.
- NSE 2026 trading holidays are built in (with optional live cache at
  `trader/cache/nse_holidays.json`). Sunday 2026-11-08 (Diwali Muhurat trading)
  is flagged as a special session ("check NSE circular"). For 2027 dates, the
  scanner notes that 2027 NSE holidays are not yet published. India has no
  early-close days and no daylight saving time (IST = UTC+05:30 year-round).

## Steps

1. Refresh the symbol list when needed: `python "${CLAUDE_SKILL_DIR}/scan.py" universe --universe nifty500` (or `nifty50`).
2. Before the 09:15 IST open (or for swing setups after 15:30 IST):
   `python "${CLAUDE_SKILL_DIR}/scan.py" daily --universe nifty50`. Tell the
   user the counts (fetched, liquid, scanned, hits) and the top setups by ₹
   turnover.
3. After 09:35 IST (10:00–10:15 IST is the classic window):
   `python "${CLAUDE_SKILL_DIR}/scan.py" orb --universe nifty50`. Tell the user
   how many usable charts broke their 09:15–09:30 IST range and when.
4. The user picks one chart. Hand it on: `/trader-news SYMBOL` (corporate
   announcements + headlines), then `/trader-entry SYMBOL` (bar-by-bar entry).
   Never pick for them.

## Example

Friday Oct 30 2026 on Nifty 50 (`--universe nifty50`):

```
python "${CLAUDE_SKILL_DIR}/scan.py" orb --universe nifty50 --now "2026-10-30 10:15"
Opening-range breakout scan (intraday MIS), Fri 2026-10-30 10:15 IST: 15-minute range (09:15 to 09:30 IST), 5-minute bars, Yahoo (.NS).
Fetched 50 of 50 charts; 45 usable; 28 candidates, 28 confirmed with real highs and lows.
12 of 45 charts closed above their opening-range high by 10:15 IST:
  09:35 IST  BAJAJFINSV      ₹1,666.90  range ₹1,658.00 to ₹1,666.40  +0.03% over
  09:35 IST  BAJFINANCE        ₹940.50  range ₹932.05 to ₹937.70  +0.30% over
  09:35 IST  HCLTECH         ₹1,428.50  range ₹1,415.60 to ₹1,427.40  +0.08% over
  09:35 IST  ICICIBANK       ₹1,394.50  range ₹1,383.40 to ₹1,390.90  +0.26% over
  09:35 IST  INDIGO          ₹4,788.80  range ₹4,638.00 to ₹4,766.70  +0.46% over
  ...
```

## Rules

- Say what was scanned: which universe (`nifty50`, `nifty500`, `all`), how many
  symbols, how many charts came back, how many were usable, and the IST time.
- Say "matched a rule", never "a buy", "a winner" or "a good trade".
- Never print a P&L, a return or a profit figure.
- A hit is stale once the price has moved on. Re-run before acting on it.

## Safety

- Educational tool, not SEBI-registered investment advice. Trading loses most people money.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except `trader-broker`, and only after `trader-risk` passes that exact order and the user gives explicit confirmation.
