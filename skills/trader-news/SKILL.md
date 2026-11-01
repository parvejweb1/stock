---
name: trader-news
description: >-
  News Monitor (India / NSE & BSE). Tracks breaking news and exchange filings
  on every NSE stock you watch and flags what moves it: Yahoo Finance (.NS)
  headlines per ticker, each company's NSE corporate announcements (quarterly
  results, board meetings, dividends, bonus/splits, promoter pledges, SAST
  disclosures, order wins, credit ratings, SEBI/regulatory updates) with an
  automatic NSE RSS fallback, and how far each stock is from its previous close
  in ₹. Shows only what is new since the last check, newest first, in Indian
  Standard Time (IST), tags each item (results, board meeting, dividend,
  bonus/split, pledge, SAST, order win, rating, regulatory/SEBI, macro/RBI...)
  and never repeats one. Use when the user says "any news on RELIANCE", "why is
  TCS moving", "what's the news on my watchlist", "morning briefing",
  "pre-market news", "monitor the news", "did anything break", "check NSE
  announcements", "any exchange filings", or after /trader-scanner finds setups.
argument-hint: "[tickers]"
---

# trader-news

A chart on NSE can look perfect and still be about to gap on quarterly Q1–Q4
results, a board meeting outcome, a promoter pledge, an order win, or a SEBI /
USFDA action. Before a setup becomes an intraday (MIS) or swing (CNC) trade,
this skill reads the two feeds that carry those first, for every stock on the
watchlist, and tells you only what is new in IST. It also prints how far each
stock has moved from its previous close in `₹`, so a corporate filing and a
price move sit side by side.

Tool in this folder (standard library Python, no API keys required):

```bash
python "${CLAUDE_SKILL_DIR}/news.py"                                    # watchlist + plan + portfolio
python "${CLAUDE_SKILL_DIR}/news.py" --tickers RELIANCE TCS SBIN --hours 48
python "${CLAUDE_SKILL_DIR}/news.py" --tickers RELIANCE --all --hours 72
python "${CLAUDE_SKILL_DIR}/news.py" --no-nse --json
```

## What it reads and writes

| file / feed | what |
| --- | --- |
| `--tickers`, else `trader/watchlist.json` | the scanner's hits (`trader-scanner`) |
| `trader/plan.json` | the trade you are planning (`trader-exit`) |
| `trader/portfolio.json` | what you hold (`trader-broker` records fills there) |
| Yahoo Finance headline RSS (`.NS`) | `https://feeds.finance.yahoo.com/rss/2.0/headline?s=RELIANCE.NS&region=IN&lang=en-IN` |
| NSE Corporate Announcements API | `https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=RELIANCE` (uses the NSE cookie handshake; falls back automatically to `https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml` if NSE blocks the API) |
| Yahoo Finance chart endpoint (`.NS`) | the last price in `₹` against previous close (unofficial, can lag) |
| `trader/state/news.json` | what has been shown (kept 30 days) so items are not repeated |

## The rules

- **Headlines are leads, not facts.** Before telling the user something
  happened, open the story or the NSE PDF attachment (`nsearchives.nseindia.com/corporate/...`)
  and read it. Quote the source and link it. Never summarise a paywalled story
  you could not read: say so.
- **Exchange filings beat headlines.** If an NSE corporate announcement (Reg. 30
  LODR disclosure) and a media headline disagree, the exchange filing is what
  the company officially submitted to NSE/BSE.
- **Say what it touches.** Every item lists the NSE tickers it touches and its
  tags: `results`, `board meeting`, `dividend`, `bonus/split`, `pledge`, `SAST`,
  `order win`, `rating`, `deal`, `people`, `regulatory/SEBI`, `macro/RBI`.
  Filings marked `!` carry high-impact corporate disclosures (financial results,
  outcome of board meeting, dividend/bonus/split/buyback, promoter pledge or
  SAST disclosure, order win, credit rating change, auditor/KMP resignation, or
  SEBI/regulatory orders), while routine newspaper publications or duplicate
  share certificate notices are left unmarked.
- **MOVING** marks a stock 3 % or more from its previous close (`--move-pct`).
  A move with no headline or filing is worth saying out loud too.
- **No predictions.** Report what was published and how far the price in `₹` is
  from the previous close. Never say what the stock will do next.

## Steps

1. Run the check: `python "${CLAUDE_SKILL_DIR}/news.py"`. Exit code 1 means
   nothing new. `--all` shows items already seen, `--json` gives everything
   machine readable, `--dry-run` leaves `trader/state/news.json` untouched.
2. Read the items that matter: every `!` NSE filing, anything tagged `results`,
   `board meeting`, `pledge`, `order win`, `regulatory/SEBI`, and every `MOVING`
   ticker first.
3. Write a short brief, per ticker: what happened, the source and link, the
   move in `₹` and `%`, and one line on why it matters to the setup (for
   example "Q2 results board meeting today: intraday volatility or overnight
   gap can jump past a tight stop").
4. If a filing or story changes the picture, say so before `/trader-entry` and
   `/trader-exit`: an upcoming board meeting for results, a promoter pledge
   invocation, an ASM/GSM surveillance action, or a trading halt. Never act on
   a headline alone.

During the 09:15–15:30 IST session: `/loop 15m /trader-news`, or run each
morning before the 09:15 IST open.

## Example

Checking `RELIANCE`, `TCS`, and `SBIN` over a 48-hour window:

```
python "${CLAUDE_SKILL_DIR}/news.py" --tickers RELIANCE TCS SBIN --hours 48 --dry-run
RELIANCE        ₹1,164.90  -0.21% vs previous close ₹1,167.40 at 15:30 IST
SBIN              ₹961.85  +0.45% vs previous close ₹957.55 at 15:30 IST
TCS             ₹2,502.60  -0.38% vs previous close ₹2,512.10 at 15:30 IST

! 2026-10-30 19:43 IST  RELIANCE      NSE Filing      RELIANCE — Press Release: In continuation of our letter of today's date on the Unaudited Financial Results (Consolidated and Standalone) of the Company for the quarter and half year ende...
      tags: results  https://nsearchives.nseindia.com/corporate/Reliance_30102026194256_PR_30102026.pdf
! 2026-10-30 19:40 IST  RELIANCE      NSE Filing      RELIANCE — Outcome of Board Meeting: In continuation of our letter dated October 27, 2026, we wish to inform you that the Board of Directors of the Company, at its meeting held today, i.e., October...
      tags: results, board meeting  https://nsearchives.nseindia.com/corporate/Reliance_30102026193802_SE_FinancialResults_30092026.pdf
```

When a ticker has no new headlines or NSE filings in the window, say so plainly
rather than padding the brief.

## What it never does

- Never invents or embellishes a story, a quote or a number.
- Never trades on news, and never tells the user to buy or sell because of a
  headline or corporate announcement.
- Never hammers a server: one request per feed per ticker, with a polite pause
  between calls.

## Safety

Educational tool, not SEBI-registered investment advice. Trading loses most
people money. This skill only reads and reports. Nothing places an order except
`/trader-broker`, after `/trader-risk` passes and the user gives explicit
confirmation for that order.
