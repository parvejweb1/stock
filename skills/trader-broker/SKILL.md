---
name: trader-broker
description: >-
  Order Executor (India / NSE & BSE). Supports the 4 Indian brokers you have
  demat accounts with: Groww (growwapi), Zerodha (kiteconnect), Upstox
  (upstox_client v2), and Angel One (SmartApi + pyotp), selectable via config
  or --broker. Paper mode (default) runs local simulation in trader/portfolio.json
  and trader/state/broker.json using the latest Yahoo Finance NSE/BSE price
  (since Indian brokers do not offer paper trading accounts). Live mode builds
  the exact SDK call for your chosen broker (entry + OCO/GTT or SL-M + target
  legs), shows the ticket, requires your explicit typed yes recorded verbatim,
  and places via SDK using credentials strictly from environment variables.
  Builds the flatten ticket by flat_by (15:10 IST). Use when the user says
  "place the order", "buy it", "execute", "send it to my broker", "close my
  position", "flatten", "get me out", "connect Groww/Zerodha/Upstox/Angel One",
  or after /trader-risk passes.
argument-hint: "[order number]"
---

# trader-broker

The Order Executor is the only skill in this pack that can execute a paper fill
or place a live order on an Indian broker, so it is the strictest. An order
executes only after three gates, every time, for every order:

1. **`trader-risk` passed this exact order today** (matched by fingerprint).
2. **The user typed an explicit `yes` for this one order** in the chat, recorded verbatim by `ticket.py approve`.
3. **Explicit execution** (`ticket.py execute --index N`): in **Paper mode** (`"paper": true`, default), simulates the fill locally using the latest Yahoo NSE/BSE price; in **Live mode** (`"paper": false`), asks for final confirmation and calls the selected broker's official Python SDK.

Educational tool, not SEBI-registered investment advice. Trading loses most people money.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python "${CLAUDE_SKILL_DIR}/ticket.py" show                                # every ticket, READY or not
python "${CLAUDE_SKILL_DIR}/ticket.py" show --broker zerodha               # preview SDK calls for Zerodha
python "${CLAUDE_SKILL_DIR}/ticket.py" approve --index 1 --reply "yes"     # record explicit yes
python "${CLAUDE_SKILL_DIR}/ticket.py" execute --index 1                   # paper: local fill; live: broker SDK
python "${CLAUDE_SKILL_DIR}/ticket.py" result --index 1 --status placed --broker-id ID --filled-qty 1 --filled-price 820.00
python "${CLAUDE_SKILL_DIR}/ticket.py" fill --symbol SBIN --side buy --qty 1 --price 820.00 --product MIS
python "${CLAUDE_SKILL_DIR}/ticket.py" flatten --symbol SBIN --qty 1       # build close SELL ticket by flat_by (15:10 IST)
python "${CLAUDE_SKILL_DIR}/ticket.py" log
```

`--now "2026-10-30 10:15"` pins the clock (IST, `Asia/Kolkata`). `--broker groww|zerodha|upstox|angelone` overrides `"broker"` in `trader/config.json`.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/orders.json` | the proposed Indian orders (`trader-budget` writes it; `flatten` replaces it) |
| `trader/state/risk.json` | `trader-risk`'s verdicts; only a PASS from today (IST) is READY |
| `trader/state/broker.json` | every yes, no, placed, failed and filled, with each entry's `max_loss_inr` |
| `trader/portfolio.json` | cash and holdings in INR, updated by `execute` (paper), `fill`, and `result` |
| `trader/plan.json` | the `flat_by` time (`15:10` IST default) for `flatten` |

## Paper mode vs Live mode

None of the 4 Indian brokers (Groww, Zerodha, Upstox, Angel One) offer paper
trading accounts.

- **Paper mode (`"paper": true`, default)**: `ticket.py execute --index N` runs a
  local simulation. It fetches the latest price from Yahoo Finance (`SYMBOL.NS` /
  `SYMBOL.BO`), falls back to `trader/prices.json` or the plan's entry when
  offline/closed, deducts estimated Indian charges, updates `trader/portfolio.json`,
  and logs `"placed"` + `"filled"` in `trader/state/broker.json`. No broker SDK
  or credentials are needed in paper mode.
- **Live mode (`"paper": false`)**: `ticket.py` lazily imports the selected
  broker's SDK from `D:\trading-env\venv` and reads credentials **strictly from
  environment variables** (see `brokers.env.example` and
  `claude_desktop_config.example.json`).

## Broker Setup & Environment Variables (Credentials NEVER in chat or files)

Never ask for, read, store, or echo API keys, secrets, MPINs, TOTP seeds, or
access tokens in chat. Set them only in your shell environment before running
live mode.

### 1. Groww (`--broker groww`, package `growwapi`)
- **Env vars**:
  - `GROWW_ACCESS_TOKEN` (direct JWT token from Groww developer portal), **OR**
  - `GROWW_API_KEY` + `GROWW_API_SECRET` (or `GROWW_TOTP_SECRET` base32 seed; `ticket.py` calls `GrowwAPI.get_access_token(api_key, totp=..., secret=...)`).
- **How to get token**: Log in to your Groww account -> Settings -> Trading APIs -> generate API key/secret or copy the daily access token.
- **SDK calls used**:
  - Entry: `GrowwAPI(token).place_order(validity, exchange, order_type, product, quantity, segment, trading_symbol, transaction_type, price, trigger_price)`
  - Stop-loss + Target: `GrowwAPI(token).create_smart_order(smart_order_type="OCO", ...)` (or separate `SL_M` and `LIMIT` orders).

### 2. Zerodha Kite (`--broker zerodha`, package `kiteconnect`)
- **Env vars**:
  - `KITE_API_KEY`
  - `KITE_ACCESS_TOKEN` (**daily login access token** — SEBI requires daily login; expires every morning).
- **How to get daily token**:
  1. Open `https://kite.zerodha.com/connect/login?v=3&api_key=<KITE_API_KEY>` in your browser and log in with 2FA/TOTP.
  2. Copy the `request_token` from the redirect URL.
  3. Exchange it once per day in Python (`KiteConnect(api_key).generate_session(request_token, api_secret)["access_token"]`) and set `$env:KITE_ACCESS_TOKEN`.
- **SDK calls used**:
  - Entry: `kite.place_order(variety="regular", exchange="NSE", tradingsymbol=..., transaction_type=..., quantity=..., product="MIS"|"CNC"|"NRML", order_type="MARKET"|"LIMIT", ...)`
  - Stop-loss + Target: `kite.place_gtt(trigger_type="two-leg", ...)` for `CNC` swing, or separate `SL-M` and `LIMIT` orders for `MIS` intraday.

### 3. Upstox (`--broker upstox`, package `upstox_client` v2)
- **Env vars**:
  - `UPSTOX_ACCESS_TOKEN` (**daily login OAuth access token** — expires daily at 03:30 AM IST).
- **How to get daily token**:
  1. Authorize via `https://api.upstox.com/v2/login/authorization/dialog?response_type=code&client_id=<API_KEY>&redirect_uri=<REDIRECT_URI>`.
  2. Exchange the `code` via `LoginApi().token(...)` and set `$env:UPSTOX_ACCESS_TOKEN`.
- **SDK calls used**:
  - `OrderApi(ApiClient(cfg)).place_order(PlaceOrderRequest(quantity=..., product="I"|"D", validity="DAY", price=..., instrument_token="NSE_EQ|...", order_type=..., transaction_type=..., trigger_price=...), api_version="2.0")`
  - Plus `SL-M` stop-loss and `LIMIT` target orders.

### 4. Angel One SmartAPI (`--broker angelone`, packages `SmartApi` + `pyotp`)
- **Env vars**:
  - `ANGEL_API_KEY` (SmartAPI trading key)
  - `ANGEL_CLIENT_ID` (your Angel One client code)
  - `ANGEL_PIN` (your 4-digit MPIN)
  - `ANGEL_TOTP_SECRET` (base32 TOTP secret from SmartAPI Enable TOTP page)
- **How to get session**: `ticket.py` generates the 6-digit TOTP via `pyotp.TOTP(ANGEL_TOTP_SECRET).now()` and calls `SmartConnect(api_key=ANGEL_API_KEY).generateSession(ANGEL_CLIENT_ID, ANGEL_PIN, totp)`.
- **SDK calls used**:
  - `smart.placeOrder({"variety": "NORMAL", "tradingsymbol": "SBIN-EQ", "symboltoken": "3045", "transactiontype": "BUY", "exchange": "NSE", "ordertype": "MARKET"|"LIMIT", "producttype": "INTRADAY"|"DELIVERY", "duration": "DAY", "price": ..., "quantity": ...})`
  - Plus `"variety": "STOPLOSS", "ordertype": "STOPLOSS_MARKET"` stop leg and `"ordertype": "LIMIT"` target leg.

## Steps: the entry

1. `ticket.py show [--broker <broker>]`. Only READY tickets go to the user. A ticket that is NOT READY or BLOCKED goes back to `/trader-risk`.
2. Show ONE ticket and ask: `"Place this order? Reply yes or no."` One order per question. Never offer `"yes to all"`.
3. Run `ticket.py approve --index N --reply "<the user's reply, word for word>"`. If it prints `"Not recorded"`, ask again.
4. Only after `"Recorded your yes"`, run `ticket.py execute --index N [--broker <broker>]` to execute the paper simulation (or live SDK order when `"paper": false`).

## Steps: out by `flat_by` (`15:10` IST) for intraday

Indian brokers auto square-off open `MIS` intraday positions between `15:15` and `15:20` IST (often charging ₹20–₹50 + GST auto square-off penalty). Default `flat_by` is **`15:10` IST**:

1. `ticket.py flatten --symbol SBIN` (reads shares and product from `trader/portfolio.json`, or pass `--qty N`).
2. Run `/trader-risk` (`check.py`). An exit that only closes what you hold is never blocked by daily limits or the entry window.
3. `ticket.py show`, get the user's explicit `yes`, `ticket.py approve --index 1 --reply "yes"`, and `ticket.py execute --index 1` (in live mode, cancel open SL/target legs first so shares are not locked).

## Safety

- Educational tool, not SEBI-registered investment advice. Trading loses most people money.
- Paper trading by default (`"paper": true`).
- Credentials live strictly in environment variables, never in chat or files.
