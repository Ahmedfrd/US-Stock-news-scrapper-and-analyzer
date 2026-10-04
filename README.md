# 📈 Market & Macro Digest — US

A free, automatic, off-machine research brief. It runs on GitHub Actions and
emails you **two reports on two cadences** — nothing runs on your computer.

| Email | Cadence | What it covers |
|---|---|---|
| **🇺🇸 Market Digest US** | **Daily** — every scheduled run, Mon–Sat 07:00 HKT | What happened in the market *since the previous email* |
| **📊 Weekly Portfolio Digest US** | **Weekly** — Saturday 07:00 HKT (after Friday's close) | The week's news on **your holdings** and how each one travelled |

The two emails never share a section: market, macro, sector and policy news
lives only in the daily email; your holdings live only in the weekly one.

---

## 🇺🇸 Market Digest (daily)

1. **TL;DR** — 3–5 lines: what moved, by how much, and why.
2. **Market levels** — S&P 500, Nasdaq, Dow, Russell 2000, VIX, 10Y yield, DXY,
   gold, Brent, WTI, BTC, ETH.
3. **Policy & rates** — computed facts (Fed funds rate, the exact date/time of
   the next FOMC decision, 10Y yield) plus the AI's read of Fed/central-bank news.
4. **Macro data** — prints released in the news window as *actual vs consensus
   vs previous* (CPI, payrolls, retail sales, ISM, …) plus what they mean.
5. **Global news & impact** — China, Europe, Japan, geopolitics, trade, oil
   supply — and their effect on US markets.
6. **Sectors** — a sector-ETF scoreboard (1-day / 5-day), then a box for each
   sector that **had news** (technology, consumer, energy & oil, financials,
   health care, industrials, real estate).
7. **Stocks to watch — at most three.** Drawn from a whole-market scan (never
   your holdings), Shariah-screened, each with a **named catalyst**. Any buy or
   sell call carries a **Catalyst Thesis**: catalyst, how it reaches the price,
   what is already priced in, horizon, what would prove it wrong, what to verify
   first — with every price level *computed in Python* (support/resistance,
   ATR, distance to levels), never invented by the AI. A buy/sell the judge
   cannot back with a complete thesis is withheld and shown as WATCH.
8. **Crypto** — major coins, compact.
9. **Coming up — exact dates** — next 14 days of scheduled releases and
   decisions with date, New York time and Hong Kong time, consensus/previous.
10. **Risks to watch**, and a **recent-picks scorecard** (price move since each
    pick was flagged).

## 📊 Weekly Portfolio Digest

1. **Your portfolio this week** — overview bullets + a scoreboard (price, week,
   1 month, verdict last week → this week).
2. **One card per holding**: this week's news (only if there was any), ETF
   component-company news (only components with news), **how it travelled**
   (day-by-day closes, week vs benchmark, last 4 weeks, RSI and volume over the
   week), earnings, compact technicals and fundamentals, and the **verdict**.
3. **Verdict = bull · bear · judge as short pointers** (3 + 3 + 2–3). Every
   role reads *this week's* news and price progression, and the judge sees
   **last week's verdict** (stored in `state/state.json`) and says what changed.
4. **Look-through** exposure across your ETFs, **coming up for your holdings**
   (earnings dates), **risks to your holdings**.

**No news → no placard.** A name or sector with no articles gets no news block
(and no "no news" filler). The AI is told so, and the code blanks any news text
for a name that had no articles.

---

## Schedule & manual runs

`.github/workflows/main.yml` fires at `0 23 * * 0-5` (UTC) = **07:00 HKT Mon–Sat**.
- Every scheduled run sends the **Market Digest**.
- On `schedule.portfolio_day` (default `Sat`, HK-local weekday) it also sends
  the **Portfolio Digest**.
- The market news window starts exactly where the previous scheduled email's
  window ended (remembered in `state/state.json`), so GitHub's start delays
  never cause gaps or repeats; Monday's edition covers the weekend.

**Actions → Market Digest → Run workflow** offers:
- **report**: `both` (default) / `market` / `portfolio` / `auto` (scheduled behaviour)
- **dry_run**: build only — the HTML is uploaded as the `digest` artifact, no
  email is sent and no state is saved. Use this to check changes without
  burning LLM quota on real sends.

## Exact-date calendar — where the dates come from

Nothing in the calendar is written by the AI.
- **Federal Reserve FOMC page** — meeting dates (decision on the 2nd day, 14:00 ET).
- **ForexFactory weekly JSON** — this week's events with full timestamps,
  forecast and previous.
- **Nasdaq economic calendar** — the following weeks + actuals for released
  prints. Its date parameter is **off by one day** (verified 5 Oct 2026), so the
  offset is calibrated on every run against TreasuryDirect auction dates and
  the FOMC calendar; if calibration is inconclusive, Nasdaq is not used.
- **TreasuryDirect** — 10Y/30Y auction dates.

## Data sources (all free)

| Data | Source | Key |
|------|--------|-----|
| Company news (direct links, full articles) | Finnhub + Marketaux + Google News RSS | `FINNHUB_API_KEY`, `MARKETAUX_API_KEY` (optional) |
| Market news | CNBC, MarketWatch, Yahoo, Investing.com, Seeking Alpha RSS + catalyst searches + per-theme Google News | none |
| Official releases | Federal Reserve, ECB, BLS, BEA RSS (the US Treasury RSS is dead — 404) | none |
| Calendar | Fed, ForexFactory, Nasdaq, TreasuryDirect | none |
| Fundamentals, prices, ETF holdings | Yahoo Finance (`yfinance`) | none |
| Earnings calendar / surprises | Finnhub | `FINNHUB_API_KEY` |
| Filings | SEC EDGAR | `SEC_USER_AGENT` |
| Crowd sentiment | Adanos (Reddit · X · News · Polymarket) | `ADANOS_API_KEY` |
| AI | Gemini → Groq → OpenRouter (free tiers) → rule-based fallback | one free key |

**Free AI models rot.** Groq and OpenRouter retire free models often (both
defaults died in September). `providers.py` reads each provider's live model
list at run time and walks to the next working model when one is gone or
restricted. OpenRouter's own `openrouter/free` router is tried first.

---

## Setup (off-machine, ~15 min)

### 1. Free keys (no credit card)
- **Gemini**: ai.google.dev → Get API key (add up to 3 keys from separate Google
  accounts as `GEMINI_API_KEY`, `_2`, `_3` — each has its own quota).
- **Finnhub**: finnhub.io → sign up → copy key.
- **Adanos** (crowd sentiment): adanos.org → register → key emailed.

### 2. Secrets
Repo → **Settings → Secrets and variables → Actions → Secrets → New repository secret**:

| Secret | Value |
|--------|-------|
| `GEMINI_API_KEY` (+ `_2`, `_3`) | Gemini keys |
| `GROQ_API_KEY` / `OPENROUTER_API_KEY` | AI fallbacks + debate roles |
| `FINNHUB_API_KEY` | Finnhub key |
| `MARKETAUX_API_KEY` | optional second news source |
| `ADANOS_API_KEY` | crowd sentiment |
| `SEC_USER_AGENT` | `Your Name your@email.com` |
| `EMAIL_PASSWORD` | Gmail **App Password** (16 chars) |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | only for Telegram delivery |

A secret only reaches the program if the workflow's `env:` block maps it; the
run log prints `[env] KEY=set|MISSING` so you can confirm at a glance.
**Gmail App Password:** enable 2-Step Verification → myaccount.google.com/apppasswords.

### 3. Configure (`config.yaml`)
- `schedule.portfolio_day` — weekday of the weekly email (HK time, default `Sat`).
- `watchlist.stocks` — holdings (+ optional `name`, `weight` %, `peers`). ETFs supported.
- `market_themes` — the daily email's sections and sectors: each has a news
  `query`, wire-story `keywords`, `kind` (`section` / `sector`) and, for
  sectors, the `etf` whose move is printed next to it.
- `sector_etfs` — the sector scoreboard.
- `market_scan.watch_max` — stocks to watch shown (max 3); `watch_pool` — how
  many the AI ranks before screening.
- `calendar.horizon_days` — calendar window (default 14).
- `shariah_only`, `crypto_watch`, `market_flags`, `etf_holdings_news`,
  `analysis.*` (AI chain, debate roles), `delivery.*`.

> **Local dev:** `pip install -r requirements.txt`, copy `.env.example` → `.env`,
> then `python main.py --report both --dry-run` (or `--no-ai` with no keys).

---

## Module map
```
main.py           decides which email(s) to build; daily market + weekly portfolio pipelines
config.yaml       holdings, themes, schedule (no secrets)
sources.py        news collection, exact news window, junk/relevance filters, market themes
discovery.py      whole-market scan: stories -> companies -> priced candidates
econ_calendar.py  exact-date calendar (Fed, ForexFactory, Nasdaq-calibrated, Treasury)
analyzer.py       the two AI analyses (portfolio weekly, market daily) + debates
debate.py         bull / bear / judge pointers; catalyst thesis for picks
thesis.py         Catalyst Thesis figures (computed) + completeness check
prices.py         cached daily bars + weekly progression maths
state.py          run-to-run memory (state/state.json, committed by the workflow)
fundamentals.py   yfinance fundamentals + factor scores
etf.py            ETF analysis: returns, weekly attribution by component, peers
technicals.py     RSI/MACD/ATR/SMA/volume/S-R + rule-based signal
shariah.py        automated Shariah screen
market_data.py    Finnhub: news, earnings calendar, symbol directory
filings.py        SEC EDGAR filings
social.py         Adanos crowd sentiment
portfolio.py      look-through exposure
providers.py      free LLM layer with live model discovery + fallbacks
digest.py         renders both emails (inline-styled HTML + plain text)
delivery.py       email / Telegram
```

## Honest limitations
- **Not investment advice.** Calls describe the current setup, not forecasts.
- **The Shariah screen is automated**, not a certified ruling — verify with
  Zoya / Musaffa before acting.
- **Free data has gaps** (niche ETFs, thinly covered tickers). Missing data is
  left out rather than padded.
- **Free LLM tiers rate-limit.** Each manual non-dry run emails you and burns
  quota; prefer a dry run and read the artifact.
- GitHub pauses schedules after 60 days without repo activity — the daily state
  commit keeps the repo active.
