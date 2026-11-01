---
name: trader-exit
description: >-
  Exit Planner (India / NSE). Sets your stop and your target before you enter,
  so you know when to get out. Reads the entry from trader-entry (or a price
  you give), supports intraday (MIS, default opening-range midpoint stop,
  flat_by 15:10 IST before broker auto square-off) and swing (CNC delivery,
  daily ATR or 10-day swing-low stop, no flat-by, holds up to
  swing_max_hold_days), rounds stops down to the ₹0.05 tick and sets the
  target at 2R (or 1R, or your price). Writes trader/plan.json (with
  trade_type and product MIS/CNC) for the Time Machine, the Budget Manager and
  Price Alerts. Long side only. Use when the user says "where's my stop", "set
  my stop and target", "plan the exit", "when do I get out", "stop loss",
  "what's my R", "risk reward", or right after /trader-entry triggers, before
  /trader-timemachine.
argument-hint: "[--stop or-mid|or-low|swing-low|price:X|pct:X|atr:K] [--target 2R|1R|price:X] [--intraday|--swing]"
---

# trader-exit

The Exit Planner decides how a trade ends before it starts. The stop is where
the setup has failed. The target is where you take what you came for. Both
are set BEFORE the entry, rounded to NSE's `₹0.05` tick size, written down and
handed on, so nobody has to decide anything in the middle of a fast move. A
stop is a plan, not a guarantee: a gap, circuit limit or fast market can fill
past it.

Educational tool, not SEBI-registered investment advice. Trading loses most people money.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python "${CLAUDE_SKILL_DIR}/exit.py" plan                                      # trader/entry.json: or-mid stop, 2R target (MIS)
python "${CLAUDE_SKILL_DIR}/exit.py" plan --stop or-mid --target 2R --write    # save trader/plan.json
python "${CLAUDE_SKILL_DIR}/exit.py" plan --stop atr:1.5 --write               # 1.5x ATR(14) stop
python "${CLAUDE_SKILL_DIR}/exit.py" plan --symbol SBIN --entry 961.65 --or-high 961.50 --or-low 955.00
python "${CLAUDE_SKILL_DIR}/exit.py" plan --swing --write                      # swing CNC: daily ATR(14)x1.5 stop, 2R target
python "${CLAUDE_SKILL_DIR}/exit.py" plan --swing --stop swing-low --write     # swing CNC: 10-day daily swing low stop
python "${CLAUDE_SKILL_DIR}/exit.py" show                                      # print the current trader/plan.json
```

Other flags: `--intraday`, `--swing`, `--flat-by 15:10`, `--json`,
`--now "2026-10-30 10:30"` (IST), `--entry PRICE` with a waiting `entry.json`
to plan ahead, `--setup level|orb|20d`, `--offline FILE` (saved Yahoo chart
JSON for an `atr` or `swing-low` stop).

## What it reads and writes

| file | what |
| --- | --- |
| `trader/entry.json` | the trigger from `trader-entry`: symbol, trade_type, product, entry price, time, bar, opening range |
| `trader/config.json` | `flat_by` (`15:10`), `min_reward_risk` (`1.5`), `trade_type` (`"intraday"` or `"swing"`), `product` (`"MIS"` or `"CNC"`), `swing_max_hold_days` (`10`) |
| Yahoo chart API | `.NS` session bars (intraday `atr:K`) or daily bars (swing `atr:K` / `swing-low`), free and unofficial |
| `trader/plan.json` | with `--write`: symbol, yahoo, `trade_type`, `product` (`MIS`/`CNC`), entry, stop and its rule, target and its R, risk a share, reward:risk, `flat_by` (`15:10` for MIS, `null` for CNC) |

## Stops and targets

| stop | where it goes |
| --- | --- |
| `or-mid` | the opening-range midpoint (`09:15–09:30` default for an intraday opening-range breakout) |
| `or-low` | the opening-range low: wider, so fewer shares for the same max loss |
| `swing-low` / `swing-low:N` | the N-day daily swing low (default 10 days; for swing trades on daily bars) |
| `price:X` | your price in `₹` |
| `pct:X` | X percent under the entry |
| `atr:K` | K times the 14-bar ATR under the entry (session bars for intraday, daily bars for swing; `atr:1.5` is the default in `--swing` mode) |

- **Tick rounding (`₹0.05`).** The stop rounds DOWN to the nearest `₹0.05`
  tick, away from the entry. Risk = entry minus stop. Target = entry + R x
  risk, rounded half up to the nearest `₹0.05` tick. `2R` by default (`1.5R`
  minimum in `trader/config.json`).
- **Refused, with the reason.** A stop at or above the entry. A target at or
  below it. A risk under 0.05% of the price (too tight to survive the spread).
  An `entry.json` that has not triggered (unless you give `--entry`). A short:
  v2 plans longs only.
- **Flat by (intraday `MIS`) vs Hold horizon (swing `CNC`).** Intraday `MIS`
  plans default to `flat_by` `15:10 IST` (before broker auto square-off around
  `15:10–15:20`). Swing `CNC` delivery plans have `flat_by: null` and hold up
  to `swing_max_hold_days` (`10` trading days by default).

## Example

`SBIN`, Thursday `2026-10-29`, intraday `MIS`:

```
Exit plan: SBIN long (intraday MIS), Thu 2026-10-29, opening-range breakout on 5-minute bars
  Entry         ₹961.65 at 10:10 AM IST (bar 10)
  Stop          ₹958.25  opening-range midpoint: (₹961.50 + ₹955.00) / 2 = ₹958.25
  Target        ₹968.45  2R (two times the risk)
  Risk a share  ₹3.40 (0.35% of the entry)
  Reward:risk   2.0
  Flat by       15:10 IST, win or lose: MIS auto square-off window (no overnight hold)
Set before the entry. If price hits the stop, you are out; never move it further away.
A stop is a plan, not a guarantee: a gap, circuit limit or fast market can fill past it.
Educational tool, not SEBI-registered investment advice. Trading loses most people money.
```

## Rules

- The stop and the target are set before the entry, never after.
- Never move a stop further from the entry once the trade is on.
- Plain numbers only: entry, stop, target, risk a share, reward:risk.
- Long side only.

## Safety

- Educational tool, not SEBI-registered investment advice. Trading loses most people money.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except `trader-broker`, and only after `trader-risk` passes that exact order today and the user gives an explicit yes.
