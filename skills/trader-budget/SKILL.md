---
name: trader-budget
description: >-
  Budget Manager (India / NSE & BSE). Sizes every position to your INR budget
  and your max loss per trade, including estimated Indian brokerage, STT,
  exchange txn, SEBI, stamp duty, GST, and DP charges. Reads the trade plan
  from trader-exit (entry, stop, target, trade_type, product), finds the whole
  shares (or F&O lot multiples) that fit inside four limits (max loss per trade
  including charges, position cap, max order, cash after your buffer), warns
  clearly when charges eat a large % of your reward (especially on ₹1,000
  capital), and writes ONE broker-neutral Indian order to trader/orders.json.
  Nothing is placed. Use when the user says "how many shares", "size this
  trade", "how much should I buy", "I have ₹1,000", "I only want to risk ₹20
  a trade", "set my rules", or after /trader-exit writes a plan.
argument-hint: "[budget in INR]"
---

# trader-budget

The Budget Manager turns an Indian trade plan ("buy SBIN at ₹820.00, stop
₹816.00, target ₹828.00") into an exact order ("buy 1 share MIS"), inside rules
the user sets once. The stop and the statutory/brokerage charges decide the
size: the further away the stop, the fewer shares. It writes a proposal only.
`trader-risk` checks it and `trader-broker` asks the user yes or no.

Educational tool, not SEBI-registered investment advice. Trading loses most people money.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python "${CLAUDE_SKILL_DIR}/size.py"                        # trader/plan.json + trader/config.json
python "${CLAUDE_SKILL_DIR}/size.py" --write                # write trader/orders.json
python "${CLAUDE_SKILL_DIR}/size.py" --limit --write        # a LIMIT order at the plan's entry
python "${CLAUDE_SKILL_DIR}/size.py" --limit 820.00 --json  # a LIMIT order at ₹820.00
python "${CLAUDE_SKILL_DIR}/size.py" --trade-type swing --product CNC --write
python "${CLAUDE_SKILL_DIR}/size.py" --weights HDFCBANK=40,RELIANCE=60 --prices trader/prices.json
```

`--now "2026-10-30 10:15"` pins the clock (IST, `Asia/Kolkata`). An intraday
plan for another day is refused: make a new one with `trader-exit`.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/config.json` | the rules (`_inr` keys; start from `config.example.json` in this folder) |
| `trader/plan.json` | the trade: entry, stop, target, setup, flat_by, trade_type, product (`trader-exit` writes it) |
| `trader/prices.json` | optional: a fresh price above the plan's entry means a bigger risk (`trader-alerts` writes it) |
| `trader/portfolio.json` | optional: its cash caps the size (`trader-broker` keeps it) |
| `trader/orders.json` | the one proposed broker-neutral Indian order it writes with `--write` |

## Step 1: set the rules once

If `trader/config.json` does not exist, copy `config.example.json` into
`trader/config.json` and ask the user for each value in plain words:

- **budget**: how much money (in INR) this is for (default `1000`). Only money they can afford to lose.
- **cash_buffer_pct**: how much always stays in cash (default `5`).
- **max_loss_per_trade_inr**: the most one trade may lose if the stop fills at the stop, **including round-trip charges** when `include_charges` is true (default `20`).
- **max_position_pct**, **max_order_inr**, **min_order_inr**: the biggest position (`100`% of budget), biggest order (`₹1,000`), and smallest order (`₹50`).
- **trade_type** (`"intraday"` or `"swing"`) and **product** (`"MIS"` for intraday, `"CNC"` for swing delivery, `"NRML"` for F&O when allowed).
- The day limits `trader-risk` enforces: **max_daily_loss_inr** (`50`), **max_trades_per_day** (`2`), **min_reward_risk** (`1.5`), **require_stop** (`true`), **no_entries_before** / **no_entries_after** (`"09:30"` / `"14:45"` IST) and **flat_by** (`"15:10"` IST, before broker MIS auto square-off).
- **include_charges**: `true` (counts brokerage, STT, NSE txn, SEBI, stamp duty, GST, and DP charges in max loss and shows net reward).
- **allow_fno**: `false` by default (F&O lots require large margin and are blocked on ₹1,000 capital).
- **broker**: `"groww"` (or `"zerodha"`, `"upstox"`, `"angelone"`). Keep **paper** `true` until tested thoroughly in local simulation.

## Step 2: size the trade

```bash
python "${CLAUDE_SKILL_DIR}/size.py" --write
```

Whole shares only (and lot-size multiples for F&O when `allow_fno` is enabled).
Prices round to the **₹0.05** NSE tick (stops round DOWN). The size is the
smallest of four limits:

| limit | shares |
| --- | --- |
| max loss | largest whole `qty` where `qty x (entry - stop) + est_charges_at_stop <= max_loss_per_trade_inr` |
| position cap | `budget x max_position_pct / entry` |
| max order | `max_order_inr / entry` |
| cash | `(budget - cash_buffer) / entry`, never more than the cash in `trader/portfolio.json` |

### Estimated Indian charges (`include_charges: true`)

- **Brokerage**: Intraday (`MIS`) / F&O (`NRML`) `min(₹20, 0.05% of turnover)` per leg; Delivery (`CNC`) `min(₹20, 0.1% of turnover)` with ₹5 minimum per leg (Groww).
- **STT**: Delivery `0.1%` buy + sell; Intraday `0.025%` on sell side.
- **Exchange txn**: `~0.00297%` (NSE); **SEBI**: `₹10/crore` (`0.0001%`).
- **Stamp duty**: `0.015%` buy side (delivery) / `0.003%` buy side (intraday).
- **GST**: `18%` on `(brokerage + exchange_txn + SEBI)`.
- **DP charge**: `₹15.93` per scrip on delivery (`CNC`) sell.

With `₹1,000` capital, `size.py` explicitly shows how much of your gross target
reward is eaten by charges (especially on `CNC` delivery where ₹15.93 DP + ₹10
min brokerage = ~₹26+ fixed drag).

## Step 3: show the user

Show the four limits, which one decided the size, the shares, the INR value,
the estimated charges breakdown, the max loss at the stop, and what % of the
gross reward charges eat. Say plainly that a stop is an order, not a promise:
in a fast market or circuit gap it can fill below the stop. Then hand over to
`/trader-risk`.

## Example: SBIN on NSE (`₹1,000` budget)

SBIN on Friday 2026-10-30. Opening-range breakout: entry `₹820.00` at 10:15 IST,
stop `₹812.00`, target `₹836.00` (2R). Price risk `₹8.00` a share. Config:
`₹1,000` budget, `5%` cash buffer (`₹50`), `₹20` max loss per trade, `MIS` intraday.

| limit | the math | shares |
| --- | --- | --- |
| max loss | `₹20` max loss / (`₹8.00`/sh + `₹1.30` est. charges) | **1 (decides it)** |
| position cap | `100%` of `₹1,000` = `₹1,000` / `₹820.00` | 1 |
| max order | `₹1,000` / `₹820.00` | 1 |
| cash | `₹1,000` minus `₹50` = `₹950` / `₹820.00` | 1 |

1 share = `₹820.00`. Price loss at stop = `₹8.00` + `₹1.30` charges = `₹9.30` max loss.
It writes to `trader/orders.json`:

```json
{
  "symbol": "SBIN",
  "exchange": "NSE",
  "segment": "CASH",
  "product": "MIS",
  "transaction_type": "BUY",
  "order_type": "MARKET",
  "quantity": 1,
  "price": 0.0,
  "trigger_price": null,
  "validity": "DAY",
  "stop_loss": 812.0,
  "target": 836.0,
  "trade_type": "intraday",
  "lot_size": 1,
  "est_charges_inr": 1.3,
  "price_loss_inr": 8.0,
  "max_loss_inr": 9.3,
  "gross_reward_inr": 16.0,
  "net_reward_inr": 14.7,
  "charges_pct_of_reward": 8.1,
  "why": "opening-range breakout on SBIN (NSE MIS): 1 share, bound by your ₹20 max loss per trade (1 x ₹8 + ₹1.30 est. charges = ₹9.30 at the stop)"
}
```

## Weights mode

`--weights HDFCBANK=28.5,RELIANCE=22.1` or `--targets targets.example.json`
splits a budget across a list of NSE stocks by weight in whole shares (needs
`--prices trader/prices.json`).

## Safety

- Educational tool, not SEBI-registered investment advice. Trading loses most people money.
- Paper trading by default (`"paper": true`).
- Nothing in this pack places an order except `trader-broker` after `trader-risk` passes and the user types an explicit `yes`.
