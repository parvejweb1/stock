---
name: trader-alerts
description: >-
  Price Alerts (India / NSE). Rings when a price, a stop, pre-open (09:00 IST),
  the opening bell (09:15 IST), MIS square-off (15:10 IST), or market close
  (15:30 IST) hits. Turns the trade plan into three alerts for intraday/MIS
  (stop, target and "flat by close" at 15:10 IST) or two alerts for swing/CNC
  (stop and target), plus above / below / stop / target levels, percent moves
  from the previous close, and any IST time of day, each ringing once so you
  are not spammed. Knows NSE hours in Asia/Kolkata (IST) with the 2026 trading
  holidays and Muhurat session handling, fetches free delayed NSE quotes
  (Yahoo chart JSON `.NS`) one symbol at a time, and writes trader/prices.json
  for sizing and the risk check. Use when the user says "alert me when", "tell
  me if RELIANCE hits", "watch my stop", "watch my trade", "remind me to square
  off by 3:10", "is the market open", "when does NSE open", "get me a price",
  or right after /trader-exit writes a plan.
argument-hint: "[symbol] [price]"
---

# trader-alerts

Price Alerts watches the numbers so the user does not have to stare at a
chart. An alert is information, never an order: when a stop alert rings,
nothing is sold by this skill. With a stop-loss / target order at the broker,
the broker's order does the work; the alerts tell the user what happened and
when it is time to square off an intraday (MIS) position before the broker's
auto square-off window (~15:10–15:20 IST).

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python "${CLAUDE_SKILL_DIR}/alerts.py" clock                             # NSE open or closed, next open (IST)
python "${CLAUDE_SKILL_DIR}/alerts.py" add --from-plan                   # stop, target (+ flat-by 15:10 for intraday; stop+target for swing)
python "${CLAUDE_SKILL_DIR}/alerts.py" add --pre-open                    # 09:00 IST pre-open session
python "${CLAUDE_SKILL_DIR}/alerts.py" add --opening-bell                # 09:15 IST normal session open
python "${CLAUDE_SKILL_DIR}/alerts.py" add --closing-bell                # 15:30 IST normal session close
python "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol RELIANCE --stop 1152.50
python "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol RELIANCE --target 1185.00
python "${CLAUDE_SKILL_DIR}/alerts.py" add --at 15:10 --label "flat by close"
python "${CLAUDE_SKILL_DIR}/alerts.py" add --square-off --symbol RELIANCE
python "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol TCS --pct 3
python "${CLAUDE_SKILL_DIR}/alerts.py" check                             # exit 0 = something rang
python "${CLAUDE_SKILL_DIR}/alerts.py" quote RELIANCE TCS --write        # -> trader/prices.json
python "${CLAUDE_SKILL_DIR}/alerts.py" quote --holdings --write          # portfolio + plan symbols
python "${CLAUDE_SKILL_DIR}/alerts.py" list
python "${CLAUDE_SKILL_DIR}/alerts.py" reset reliance-plan-stop
```

`--now "2026-10-30 15:10"` pins the clock (IST, `Asia/Kolkata`); `--offline DIR`
reads saved quote files instead of the network.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/plan.json` | the trade from trader-exit, for `add --from-plan` (`trade_type`: `intraday` or `swing`, `product`: `MIS` or `CNC`) |
| `trader/config.json` | shared config (`flat_by`: `"15:10"`, `trade_type`: `"intraday"`) |
| `trader/alerts.json` | the rules (`alerts.example.json` in this folder) |
| `trader/state/alerts.json` | which rules rang and when, so each rings once |
| `trader/prices.json` | the latest quotes (keyed by plain NSE symbol like `"RELIANCE"`), merged in, for trader-budget and trader-risk |
| `trader/portfolio.json` | the symbols held, for `quote --holdings` |
| `trader/cache/nse_holidays.json` | cached NSE holiday list when fetched from NSE's holiday-master API |

## Rule types

| type | rings when | rings again |
| --- | --- | --- |
| `above` / `target` | price at or above `price` (₹) | after `reset`, or on its own if `"rearm": true` and the price fell back |
| `below` / `stop` | price at or below `price` (₹) | same |
| `pct_move` | moved `pct` percent from the previous close (or `ref`), optional `direction` up or down | once per day at most |
| `time` | the IST clock passed `at` (e.g. `"15:10"`) on a trading day | once per trading day |
| `pre_open` | NSE pre-open started at `09:00` IST on a trading day | once per trading day |
| `opening_bell` | the NSE regular session is open (`09:15` IST) | once per trading day |
| `closing_bell` | the NSE regular session closed (`15:30` IST) on a trading day | once per trading day |

A rule with `"date"` is watched on that IST date only. For **intraday (`MIS`)**
plans, `add --from-plan` creates `stop`, `target`, and `flat-by` (default
`15:10` IST) scoped to the plan's date, so yesterday's intraday stop never rings
today. For **swing (`CNC`)** plans (`"trade_type": "swing"` or `"product": "CNC"`),
`add --from-plan` creates only `stop` and `target` without a single-day lock or
a `15:10` flat-by alert, so the position can be watched across holding days.

## Quotes

- `yahoo` (default): `query2.finance.yahoo.com/v8/finance/chart/<SYMBOL>.NS`, an
  unofficial free JSON endpoint, one request per symbol, half a second apart.
  Plain NSE symbols (`RELIANCE`, `TCS`, `HDFCBANK`, `SBIN`, `M&M`) automatically
  query `.NS` (indices like `^NSEI`, `^NSEBANK`, `^INDIAVIX` and explicit `.BO`
  symbols are passed through; `&` is URL-encoded). In `trader/prices.json`, keys
  are always stored as the plain NSE symbol (`"RELIANCE"`).
- Free quotes are unofficial, delayed and can lag. They are fine for alerts
  and for checking a size, not for timing an order. The broker's price is the
  one that fills. Each price in `trader/prices.json` keeps its quote time, and
  trader-risk only uses one under 15 minutes old.

## The NSE clock (IST)

- **Timezone:** `Asia/Kolkata` (IST, UTC+05:30, no DST).
- **Sessions:** Pre-open `09:00–09:08` (order entry) and `09:08–09:15`
  (matching); **Regular session `09:15–15:30` IST**; broker MIS auto square-off
  window starts around **`15:10` IST** (`flat_by`); closing session `15:40–16:00`
  (ignored for trading). Closed on weekends.
- **2026 NSE trading holidays:** Jan 15, Jan 26, Mar 3, Mar 26, Mar 31, Apr 3,
  Apr 14, May 1, May 28, Jun 26, Sep 14, Oct 2, Oct 20, Nov 10, Nov 24, Dec 25.
  (Also tries live `https://www.nseindia.com/api/holiday-master?type=trading` and
  caches to `trader/cache/nse_holidays.json`.)
- **Muhurat trading:** Sunday `2026-11-08` is flagged as `"special session, check NSE"`
  and does not auto-open.
- **2027:** Not yet published in code; prints `"2027 holidays unknown, check NSE"`.
  India has no early-close days.

## Steps

1. Right after trader-exit writes the plan, run `add --from-plan`. Confirm
   the alerts back to the user: stop, target, and (for intraday MIS) the `15:10`
   flat-by time.
2. Run `check` while the trade is open: every few minutes from a scheduled task
   or `/schedule`. Each run fetches only the symbols in today's active rules and
   rings each alert once.
3. Tell the user what rang in one line each. "Flat by close" at `15:10` IST means
   square off any open intraday (MIS) position before the broker's auto
   square-off charges kick in: `trader-broker flatten --symbol <SYM>`, then
   `trader-risk`, then the user's explicit yes. Never act for them.

## Example

RELIANCE on Friday 2026-10-30 (intraday MIS): entry ₹1,168.00 at 09:45 IST,
stop ₹1,152.50, target ₹1,185.00, flat by 15:10. `add --from-plan` writes
`reliance-plan-stop`, `reliance-plan-target` and `reliance-flat-by` for that day.
A `check` at 10:30 IST reads ₹1,164.90, between the stop and the target:

```
NSE OPEN (2026-10-30 10:30 IST, regular session).
Nothing hit. 3 rule(s) watched today, RELIANCE ₹1,164.90.
```

At 15:10 IST:

```
ALERT Flat by close: it is 15:10 IST (set for 15:10), the market closes at 15:30. If RELIANCE (MIS intraday) is still open, square it off before broker auto square-off: trader-broker flatten --symbol RELIANCE builds the sell ticket, then trader-risk and your yes.
```

## Rules

- An alert is a fact about a price or the clock. Say it as a fact.
- Never present a quote as live. Free quotes are delayed and unofficial.
- Confirm every new rule back to the user: symbol, type, level (₹), day.

## What it never does

It never places, cancels or changes an order, never claims a quote is
live, and never predicts where a price is going.

## Safety

- Educational tool, not SEBI-registered investment advice. Trading loses most people money.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when trader-risk passed that exact order today and the user typed an explicit yes for that one order in the chat.
- No F&O, margin, short selling or leverage unless the config allows it and the user confirms.
