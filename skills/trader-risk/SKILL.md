---
name: trader-risk
description: >-
  Risk Manager (India / NSE & BSE). Checks every trade against your INR limits
  and stops anything over them before it can reach the broker. Enforces Indian
  market rules: IST entry window (09:30–14:45), NSE trading hours (09:15–15:30)
  and 2026 holidays, intraday MIS flat_by (15:10 IST before broker auto
  square-off), MIS-only short selling (no delivery CNC shorting), F&O blocked
  when allow_fno is false plus NSE lot-size multiple checks, upper-circuit
  warnings, round-trip charges (brokerage, STT, NSE txn, SEBI, stamp, GST, DP)
  included in max loss per trade, reward:risk minimum, daily loss limit, and
  trades per day. Getting out (an exit) is never blocked by a day limit. Prints
  PASS or BLOCK with the reason and records verdicts by fingerprint for
  trader-broker. Use when the user says "check my order", "is this safe",
  "risk check", "does this break my rules", "can I take this trade", "how much
  have I risked today", or before /trader-broker, always.
argument-hint: "[path to orders.json]"
---

# trader-risk

The Risk Manager is the gate between a proposal and the broker. Every order
written by `trader-budget` or `trader-broker`'s `flatten` passes through it. A
BLOCK is final for that order: it does not go to the user for a yes, and it
does not go to the broker.

Educational tool, not SEBI-registered investment advice. Trading loses most people money.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python "${CLAUDE_SKILL_DIR}/check.py"                           # trader/ files; writes trader/state/risk.json
python "${CLAUDE_SKILL_DIR}/check.py" --json
python "${CLAUDE_SKILL_DIR}/check.py" --no-write                # look without recording
python "${CLAUDE_SKILL_DIR}/check.py" --now "2026-10-30 10:15"  # pin the clock (IST)
```

## What it reads and writes

| file | what |
| --- | --- |
| `trader/orders.json` | the proposed orders (`trader-budget`, or `trader-broker`'s `flatten`) |
| `trader/config.json` | the limits (`trader-budget` ships `config.example.json`) |
| `trader/portfolio.json` | cash and holdings in INR; if missing, a fresh account funded with `budget` |
| `trader/prices.json` | a fresh price raises the entry it measures risk from; also exposes circuit bands |
| `trader/state/broker.json` | what was placed today: duplicates, trades per day, daily loss |
| `trader/state/risk.json` | the verdicts it writes, with a fingerprint of each exact order |

## Entries and exits

An **entry** opens or adds to a position: a buy, or a sell that would go short.
An **exit** only reduces what you already hold. Getting out must always be
possible, so an exit is checked for format, asset/F&O and short sales only.
Every other rule (day limits, IST entry window, loss, reward:risk, blocked
list, size, duplicates) turns into a warning for an exit, never a BLOCK.

## The checks (India / NSE & BSE)

Orders are checked in file order, as if each passed order had filled, so a
batch that is fine order by order but breaks a limit together is caught.

| check | blocks when |
| --- | --- |
| format | missing `symbol`, `transaction_type` not `BUY`/`SELL`, `quantity` not a positive whole integer, unknown `exchange` (`NSE`/`BSE`), `segment` (`CASH`/`FNO`), `product` (`MIS`/`CNC`/`NRML`), `order_type` (`MARKET`/`LIMIT`/`SL`/`SL-M`), or missing `price`/`trigger_price` |
| fno & lot size | `segment == "FNO"` or `product == "NRML"` or F&O contract while `allow_fno` is `false`; or `quantity` is not a multiple of the NSE lot size |
| short | selling more than you hold while `allow_short` is `false`; or attempting a delivery (`CNC`) short sale (short selling in Indian cash equities is strictly `MIS` intraday only) |
| product | (entries) a `swing` trade using `MIS` (which brokers auto square-off at 15:10–15:20 IST) |
| circuit | (entries) buying a stock at or above its upper circuit band (`upperCP` / `upper_circuit` in `prices.json`), or warns to verify circuit band when using Yahoo data |
| blocked | (entries) the symbol is on the `blocked` list |
| size | (entries) under `min_order_inr` (`₹50`) or over `max_order_inr` (`₹1,000`) |
| position | (entries) the stock would be over `max_position_pct` of the account |
| cash | (entries) the buy would leave less than `cash_buffer_pct` (`5%`), or need margin while `allow_margin` is `false` |
| stop | (entries) no `stop_loss` while `require_stop` is `true`; or a stop on the wrong side of the entry |
| loss (with charges) | (entries) `quantity x (entry - stop) + est_charges_inr` over `max_loss_per_trade_inr` (`₹20`). Includes brokerage, STT, NSE txn, SEBI, stamp duty, GST, and DP charge when `include_charges` is `true` |
| reward:risk | (entries) `(target - entry) / (entry - stop)` under `min_reward_risk` (`1.5`) |
| daily loss | (entries) the max losses (including charges) of today's entries plus this one over `max_daily_loss_inr` (`₹50`) |
| trades | (entries) `max_trades_per_day` (`2`) entries already placed today |
| window | (entries) outside `no_entries_before` (`09:30` IST) to `no_entries_after` (`14:45` IST), at/after `flat_by` (`15:10` IST) for intraday trades, outside NSE normal hours (`09:15–15:30` IST), on weekends, on 2026 NSE trading holidays, or on special Muhurat trading (`2026-11-08`) |
| duplicate | (entries) the same symbol and side twice in one batch, or already placed today |
| daily limit | (entries) `max_orders_per_day` (`10`) orders already placed today |

## Steps

1. If the price may have moved since the plan, refresh it first
   (`trader-alerts`: `alerts.py quote SBIN --write`).
2. Run `check.py`. Exit code 0 means every order passed, 1 means at least one
   was blocked.
3. Tell the user the result in plain words, order by order: the price loss +
   estimated charges at the stop, reward:risk, and how much of today's limits
   it uses. For a BLOCK, give the reason and the fix. Never change a limit
   yourself.
4. If the order changes in any way, run `check.py` again. `trader-broker`
   matches each order to its verdict by fingerprint.

## Example: SBIN on NSE (`₹1,000` account)

```
WARNING: circuit: Yahoo quote does not include NSE circuit band for SBIN; verify it is not locked at upper circuit

PASS  #1 BUY SBIN 1 share (MIS), entry
          entry ₹820 (the plan's entry), stop ₹812, target ₹836: price risk ₹8/share (MIS, intraday)
          max loss ₹9.30 (₹8 price loss + ₹1.30 charges) (limit ₹20), reward:risk 2.00 (min 1.5)
          today before this: 0 of 2 trades, ₹0 of ₹50 daily loss

1 passed, 0 blocked at 2026-10-30 10:15 IST. Cash after passed orders: ₹180 (paper account, groww).
Every order passed. trader-broker still asks you yes or no for each one.
Educational tool, not SEBI-registered investment advice. Trading loses most people money.
```

## Safety

- Educational tool, not SEBI-registered investment advice. Trading loses most people money.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except `trader-broker`, and only after `trader-risk` passes that exact order today and the user types an explicit `yes` for that one order.
- Broker credentials live in environment variables only, never in chat or files.
