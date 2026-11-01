---
name: trader-entry
description: >-
  Entry Timer (India / NSE). Watches a setup bar by bar and tells you the
  moment to get in. Supports intraday (MIS, 1-minute or 5-minute Yahoo .NS
  bars, 09:15-09:30 IST opening range by default) and swing (CNC delivery,
  daily-bar close above the prior 20-day high or a level you give), with an
  optional volume check. Says waiting and how far, triggered with the time and
  the price in ₹, expired, or not open yet, and writes trader/entry.json for
  /trader-exit. Long side only. It never places an order. Use when the user
  says "tell me when to get in", "watch SBIN", "watch RELIANCE", "has it
  broken out", "did it break the opening range", "entry signal", "time my
  entry", "check my watchlist", or after /trader-scanner and /trader-news,
  before /trader-exit.
argument-hint: "[SYMBOL] [--setup orb|level|20d] [--interval 5m|1m|1d] [--intraday|--swing]"
---

# trader-entry

The Entry Timer turns a setup into one moment: the bar that confirms it. For
intraday (`MIS`), it reads 5-minute or 1-minute NSE bars (`09:15–15:30 IST`)
and waits for each bar to finish. For swing (`CNC` delivery), it reads daily
NSE bars and checks if the completed session closed above the prior 20-day
high or a price level you give. A price that pokes above the high in the
middle of a bar is not an entry. A bar that CLOSES above it is. Then it says
the time and the price in `₹`, writes them down for the Exit Planner, and
stops. It never buys anything.

Educational tool, not SEBI-registered investment advice. Trading loses most people money.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python "${CLAUDE_SKILL_DIR}/entry.py" check SBIN                        # 15-minute opening range (09:15-09:30 IST), 5m bars
python "${CLAUDE_SKILL_DIR}/entry.py" check SBIN --write                # also save trader/entry.json
python "${CLAUDE_SKILL_DIR}/entry.py" check RELIANCE --setup level --level 1170.00
python "${CLAUDE_SKILL_DIR}/entry.py" check SBIN --interval 1m --min-volume-x 1.5
python "${CLAUDE_SKILL_DIR}/entry.py" check SBIN --swing --write        # daily-bar close above 20-day high (CNC)
python "${CLAUDE_SKILL_DIR}/entry.py" check RELIANCE --swing --level 1160.00 --write
python "${CLAUDE_SKILL_DIR}/entry.py" watch SBIN --until 14:45          # one line per closed bar, stops on trigger
python "${CLAUDE_SKILL_DIR}/entry.py" watchlist                         # every hit in trader/watchlist.json
```

Other flags: `--intraday`, `--swing`, `--or-minutes N` (default 15), `--json`,
`--offline FILE` (a saved Yahoo chart JSON, `.json` or `.json.gz`, or a folder
of `<SYMBOL>.json`), `--now "2026-10-30 10:30"` (IST) to replay a session.
`watch` with `--now` or `--offline` runs on a simulated clock and replays the
day bar by bar without waiting.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/watchlist.json` | the scanner's hits (`hits[].symbol`), for `watchlist` |
| `trader/config.json` | `trade_type` (`"intraday"` or `"swing"`), `product` (`"MIS"` or `"CNC"`), `no_entries_before` (`09:30`) and `no_entries_after` (`14:45`), IST. Optional |
| Yahoo chart API | `.NS` bars (`range=1d&interval=5m` or `1m` for intraday, `range=6mo&interval=1d` for swing): free, unofficial, can lag |
| `trader/entry.json` | with `check --write`, or when `watch` triggers: symbol, yahoo, trade_type, product, setup, status, time, bar, price, level, opening range |

## The rule

- **Intraday opening range (`orb`, product `MIS`).** The high and low of the
  first 15 minutes of the NSE session (`09:15–09:30 IST`): the `09:15`, `09:20`
  and `09:25` five-minute bars, or fifteen 1-minute bars. The level to beat is
  the range high.
- **Intraday level (`level`, product `MIS`).** A price in `₹` you give, like a
  key level from `/trader-scanner`.
- **Swing (`--swing`, product `CNC`).** Uses daily NSE bars (`1d`). By default
  (`20d`), the level to beat is the highest high of the prior 20 trading days,
  or `--level PRICE` if you provide one. Triggers when the completed daily bar
  closes strictly above that level.
- **The trigger** is the first completed bar that CLOSES above the level. A
  wick above is not a trigger. A close exactly at the level is not above it.
- **The time & price.** For intraday, the trigger time is that bar's close
  time (e.g. the `10:05` bar closes at `10:10 AM IST`), and the price is its
  close rounded to the `₹0.05` tick. For swing, the trigger is the `3:30 PM IST`
  daily close.
- **Time limits (intraday).** No entries before `09:30` or after `14:45` IST
  (config). Weekends, NSE holidays and special Muhurat sessions (`2026-11-08`)
  report the market closed.

## Steps

1. Get the NSE symbol from the user (e.g. `SBIN`, `RELIANCE`, `TCS`, `INFY`),
   or run `entry.py watchlist` after `/trader-scanner`. Check `/trader-news`
   for it first.
2. Run `entry.py check SYMBOL` (add `--swing` for a swing trade). Tell the
   user the status in one line: waiting and how far, triggered at what time
   and price, or expired.
3. To wait for an intraday breakout, run `entry.py watch SYMBOL --until 14:45`
   in the background. It exits 0 on the trigger (writing `trader/entry.json`)
   or 1 when the setup expires.
4. When it triggers, say the time and the price in `₹`, and that nothing was
   placed.
5. Hand off to `/trader-exit` to set the stop and the target BEFORE anything
   is bought.

## Example

`SBIN`, Thursday `2026-10-29`, 5-minute NSE bars (`--now "2026-10-29 10:30"`):

```
SBIN (intraday MIS)  Thu 2026-10-29 10:30 IST  opening-range breakout (15-minute range) on 5-minute bars (Yahoo, free, can lag)
Opening range 9:15-9:30: high ₹961.50, low ₹955.00 (3 of 3 bars).
TRIGGERED 10:10 AM at ₹961.65: the 10:05 bar (bar 10) closed above the range high ₹961.50 (+0.02%).
That was 20 minutes ago; the last close is ₹959.65 (10:25 bar). A late entry is a different trade: the price, the stop distance and the odds have moved.
Nothing was placed.
Next: set the stop and target BEFORE you buy: /trader-exit (exit.py plan --from trader/entry.json).
Educational tool, not SEBI-registered investment advice. Trading loses most people money.
```

## Rules

- Bar by bar, on free Yahoo `.NS` bars (`1m`, `5m`, or `1d`). They are
  unofficial and can lag. Say so when a minute matters.
- A signal is information, not an order, and not a promise.
- Long side only. v2 plans longs only.
- Never call an entry early because a bar looks like it will close above.

## Safety

- Educational tool, not SEBI-registered investment advice. Trading loses most people money.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except `trader-broker`, and only after `trader-risk` passes that exact order today and the user gives an explicit yes.
- F&O is disabled by default (`"allow_fno": false`). No options, margin, or short selling unless config allows and user confirms.
