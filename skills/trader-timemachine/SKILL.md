---
name: trader-timemachine
description: >-
  Time Machine (India / NSE). Replays every possible outcome of a trade before
  you risk a rupee: 10,000 simulated sessions plus every past time the setup
  fired. Takes the plan from trader-exit (entry, stop, target), supports
  intraday (MIS, rebuilds the rest of the NSE session 10,000 times from 60 days
  of 5-minute .NS bars) and swing (CNC delivery, simulates over up to
  swing_max_hold_days daily bars using ~2 years of daily .NS history), and
  re-runs the same rule on past sessions. Prints the odds of target first,
  stop first, or neither, the closing price distribution in ₹, average R,
  history, and one honest verdict, then hands off to /trader-budget. Use when
  the user says "run the time machine", "replay this trade", "what are the
  odds", "simulate this trade", "monte carlo", "backtest this setup", "how
  often does this hit the target", "is this a coin flip", or after
  /trader-exit writes a plan, before /trader-budget.
argument-hint: "[path to plan.json, or SYMBOL] [--intraday|--swing]"
---

# trader-timemachine

The Time Machine sits between the plan and the money. `trader-exit` has set
the entry, the stop and the target in `₹`. Before anything is sized or sent,
this skill asks one question: if this trade plays out like the stock's own
recent NSE bars (last 60 days of 5-minute bars for intraday `MIS`, or last ~2
years of daily bars over up to `swing_max_hold_days` for swing `CNC`), how
often does this plan hit the target first, the stop first, or neither? It
answers with 10,000 replays and with the real record of the same rule on past
sessions. It is a simulation built from the stock's own recent bars, not a
prediction.

Educational tool, not SEBI-registered investment advice. Trading loses most people money.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python "${CLAUDE_SKILL_DIR}/timemachine.py" run                         # trader/plan.json, 10,000 replays, seed 19
python "${CLAUDE_SKILL_DIR}/timemachine.py" run --write                 # also save trader/timemachine.json
python "${CLAUDE_SKILL_DIR}/timemachine.py" run --until 15:10 --write   # end each intraday replay at your flat-by time
python "${CLAUDE_SKILL_DIR}/timemachine.py" run --swing --write         # replay a swing CNC plan over daily bars (~2y history)
python "${CLAUDE_SKILL_DIR}/timemachine.py" run --json
python "${CLAUDE_SKILL_DIR}/timemachine.py" run --symbol SBIN --date 2026-10-29 \
    --entry 961.65 --stop 958.25 --target 968.45 --entry-time "10:10 AM"
python "${CLAUDE_SKILL_DIR}/timemachine.py" chart --from                # ASCII fan chart of the saved run
```

Other flags: `--intraday`, `--swing`, `--hold-days N` (swing horizon, default
`swing_max_hold_days` = 10), `--replays N`, `--seed N` (default 19),
`--window N` (default 2 bars for intraday), `--offline FILE`,
`--now "2026-10-30 10:30"` (IST).

## What it reads and writes

| file | what |
| --- | --- |
| `trader/plan.json` | the plan from `trader-exit`: symbol, trade_type (`intraday`/`swing`), product (`MIS`/`CNC`), date, entry, stop, target, target_r |
| `trader/config.json` | `trade_type`, `swing_max_hold_days` (default 10) |
| Yahoo chart API | `.NS` bars: `range=60d&interval=5m` for intraday, or `range=2y&interval=1d` for swing |
| `trader/timemachine.json` | with `--write`: `{plan, mc, history}` plus the verdict, percentiles, fan-chart bands, 160 sample paths and a 24-bin histogram |

## How it works

1. **Intraday pool (`MIS`, 5-minute bars).** Keeps full NSE sessions (`09:15`
   to `15:30 IST`, 75 five-minute bars) strictly before the plan's date over
   the last 60 days. Each bar is stored as ratios `(open, high, low, close)` to
   the previous bar's close. For each remaining 5-minute slot until `--until`
   (default `15:30 IST`, or `15:10 IST` with `--until 15:10`), a replay draws a
   past bar from the same time of day (`+-2` bars) and scales it to the running
   price.
2. **Swing pool (`CNC`, daily bars).** Fetches ~2 years of daily NSE bars
   strictly before the plan's date. Each replay steps forward up to
   `swing_max_hold_days` (default 10 trading days), drawing whole daily bar
   ratios from the stock's 2-year history.
3. **Stop-first rule.** In every bar (5-minute or daily), the stop is checked
   before the target. A bar that touches both counts as the stop (conservative).
4. **History.** Re-runs the plan's rule across the past sessions in the pool
   (15-minute opening-range breakout for intraday, or 20-day high daily
   breakout for swing) with `₹0.05` tick rounding.
5. **The verdict.** Compares `target / (target + stop)` against the break-even
   share `1 / (1 + R)` (`50%` at `1R`, `33%` at `2R`).

## Steps

1. Make sure `trader/plan.json` is the current plan from `/trader-exit`.
2. Run `timemachine.py run --until 15:10 --write` for intraday (or
   `timemachine.py run --swing --write` for swing).
3. Report the four odds, the median and 5th–95th percentile price in `₹`,
   average R, history line, and the exact verdict line. State the seed.
4. Remind the user once that this is built from historical NSE bars and is not
   a prediction: earnings, news and circuit limits can do what recent history
   never did.
5. Hand off to `/trader-budget` to size the position within the `₹1,000` budget
   and `₹20` max loss per trade.

## Rules

- Every number comes from the run. Never round a coin flip up into an edge.
- Repeat the verdict line and its notes verbatim.
- Always state the seed so any run can be repeated.

## Safety

- Educational tool, not SEBI-registered investment advice. Trading loses most people money.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except `trader-broker`, and only after `trader-risk` passes that exact order today and the user gives an explicit yes.
