# 📈 Market & Macro Digest

A free, automatic, off-machine research brief for your portfolio. Every weekday
morning it scrapes finance + macro + crypto news, pulls the fundamentals,
technicals, and crowd-sentiment data behind it, has a free AI analyse everything,
and emails you **two reports** (all analysis written as scannable bullet points):

- **📊 Portfolio Digest** *(every weekday)* — your holdings in depth, plus a
  portfolio-wide look-through concentration view.
- **🇺🇸 Market Digest US** *(weekly, on `market_digest_day` — default Thu UTC =
  Friday morning HK; manual runs always include it)* — the general market:
  global flags, detailed sector highlights, Shariah-screened *stocks to watch*
  (US-listed only, each fully analysed like a holding), a major-coins crypto
  section, industries & themes, and macro.

Runs on GitHub's servers on a schedule. **Nothing runs on your own computer.**
Everything is free (one optional key adds multi-source crowd sentiment).

---

## What's in each report

### 📊 Portfolio Digest
- **Engine banner** — which AI produced the analysis (or that the free local
  heuristic ran, and why).
- **Portfolio overview** + **global market flags** (Brent, WTI, gold, DXY,
  S&P 500, Nasdaq, 10Y yield, Bitcoin, Ethereum — with % moves).
- **Portfolio look-through** — your *true* exposure to companies and sectors
  across direct holdings **and** what your ETFs hold underneath, with overlap
  and concentration warnings. Add a `weight:` to each holding for real
  allocation; otherwise equal weight is assumed (and stated).
- **What matters today** — holdings ranked by materiality of the day's news.
- **A full card per holding** (stock or ETF) — see below.
- **Macro backdrop** — detailed paragraph + figure-cited bullets + watch list.

### 🇺🇸 Market Digest US (weekly)
- Built + emailed once a week, on `market_digest_day` (UTC weekday, default
  Thu). Manual **Run workflow** runs always produce it for testing.
- **Market overview** + **global market flags**.
- **Sector highlights** — sectors affected by the day's news flow, each
  bullish/bearish/neutral with 3–5 detailed, figure-cited bullets.
- **Stocks to watch** — AI-surfaced names (usually outside your portfolio),
  **Shariah-screened** when `shariah_only: true`, each given the *same full
  analysis* as a holding plus a compliance badge with the debt/cash ratios.
- **Crypto — major coins** — market call with figure-cited drivers; every coin
  in your `crypto_watch` list gets a combined buy/hold/sell call, price + 1d/7d
  moves, Adanos crowd sentiment, collapsible technicals, and crypto news links.
- **Industries & themes** — your topics as detailed paragraphs, with key
  non-portfolio companies driving each.
- **Weekly sector deep-dive** (once a week) with read-across to your holdings.
- **Macro** — detailed paragraph, figure bullets, upcoming catalysts.

### The per-name card (holdings and stocks-to-watch get identical treatment)
Price + move · news tone · **multi-source crowd sentiment panel** (Adanos:
Reddit · X · News · Polymarket — per-source bullish/neutral/bearish stacked bars,
buzz, trend, and a blended consensus) · impact & sentiment badges · factor
scores (Value / Growth / Profit / Momentum / Health + composite) · valuation,
growth, margins, analyst target, next earnings · a highlighted
**news-impact-on-the-company** read · earnings & outlook with management
commentary from SEC filings · divergence flag · bull/bear cases · **technical
analysis** (RSI, MACD, ATR, SMA/EMA, volume, support/resistance) shown three
ways: the **rule-based signal** (deterministic reference), the **technical read**
(the AI's own call from the technicals, which may override the rules with
reasoning), and the **combined call** (the AI's final buy/accumulate/hold/
reduce/sell from technicals + fundamentals + news) · collapsible source links
(full articles are read, not just headlines).

**ETF cards** additionally show: multi-horizon returns (1d→1y, YTD), risk
(volatility, max drawdown, beta), NAV premium/discount, expense/yield/AUM,
**move attribution by holding** (which components drove today's move, with a
summed "explained move"), sector weights, vs-benchmark and vs-competitor-ETF
tables, and news on the major underlying holdings.

---

## Data sources (all free)

| Data | Source | Key |
|------|--------|-----|
| News (direct publisher links, full articles) | Finnhub company-news + Google News RSS | `FINNHUB_API_KEY` (free) |
| Fundamentals, prices, targets, ETF holdings | Yahoo Finance (`yfinance`) | none |
| Technicals (stocks **and** crypto) | computed from Yahoo prices | none |
| Earnings calendar + surprises | Finnhub | `FINNHUB_API_KEY` |
| Earnings results / outlook / management commentary | SEC EDGAR filings | none (set `SEC_USER_AGENT`) |
| News tone | VADER (offline) | none |
| **Crowd sentiment** (Reddit · X · News · Polymarket, stocks + crypto) | **Adanos** | `ADANOS_API_KEY` (free, 250 req/mo) |
| Crypto prices | Yahoo Finance | none |
| Macro | Fed / Treasury / ECB RSS | none |
| Shariah screen | computed (sector + balance sheet) | none |
| AI analysis | Gemini (Groq / OpenRouter fallback) | one free key |
| Hosting / schedule | GitHub Actions | free |

No AI key at all → a local heuristic still produces both reports (clearly labelled).

---

## Setup (off-machine, ~15 min)

### 1. Free keys (no credit card)
- **Gemini**: ai.google.dev → Get API key.
- **Finnhub**: finnhub.io → sign up → copy key.
- **Adanos** (crowd sentiment): adanos.org → register → key emailed.

### 2. Repo
Create a **private** GitHub repo and upload the project files. The drag-and-drop
uploader skips the hidden `.github` folder, so create the workflow via
**Actions → set up a workflow yourself** and paste in
`.github/workflows/main.yml`.

### 3. Secrets — AND the workflow env block
Repo → **Settings → Secrets and variables → Actions → Secrets tab → New
repository secret** (must be *Secrets*, not *Variables*):

| Secret | Value |
|--------|-------|
| `GEMINI_API_KEY` | Gemini key |
| `FINNHUB_API_KEY` | Finnhub key |
| `ADANOS_API_KEY` | Adanos key (enables the crowd panels) |
| `SEC_USER_AGENT` | `Your Name your@email.com` |
| `EMAIL_PASSWORD` | Gmail **App Password** (16 chars) |
| `GROQ_API_KEY` / `OPENROUTER_API_KEY` | optional AI fallbacks |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | only for Telegram delivery |

⚠️ **A secret only reaches the program if the workflow's `env:` block maps it**
(`ADANOS_API_KEY: ${{ secrets.ADANOS_API_KEY }}` etc.). The included
`daily-digest.yml` maps all of them — if you hand-edited the workflow, verify.
The run log prints an `[env] ...=set|MISSING` line so you can confirm at a glance.

**Gmail App Password:** enable 2-Step Verification, then
myaccount.google.com/apppasswords → create → paste the 16-character code into
`EMAIL_PASSWORD`. Regular Gmail passwords are rejected for SMTP.

### 4. Configure
Edit `config.yaml` in GitHub: your holdings (ETFs fully supported; optional
`weight`, `peers`), topics, `shariah_only`, `crypto_watch`, delivery email.
YAML is indentation-sensitive — use spaces only, keep keys inside one holding
aligned, and validate before committing if unsure.

### 5. Run
Schedule is set in the workflow `cron` (UTC). Default `0 23 * * 0-4` =
**07:00 Hong Kong, Mon–Fri**. Test now: **Actions → Run workflow**, then read
the emails or download the **digest** artifact (contains both HTML reports).

> **Local dev (optional):** `pip install -r requirements.txt`, copy
> `.env.example` → `.env` with your keys, `python main.py --dry-run`.

---

## Configuration reference (`config.yaml`)
- `watchlist.stocks` — tickers (+ optional `name`, `weight` %, `peers`).
- `watchlist.topics` — free-text themes for the Market report.
- `etf_benchmark` — benchmark for ETF relative performance/beta (default SPY).
- `shariah_only` — filter stocks-to-watch to Shariah-compliant names.
- `crypto_watch` — major coins for the crypto section (`[]` disables).
- `market_flags` — omit for defaults, `[]` to hide, or customise.
- `weekly_sector_day` — UTC weekday for the weekly deep-dive (`Thu` = Friday-morning HK).
- `market_digest_day` — UTC weekday the (weekly) Market Digest goes out
  (defaults to `weekly_sector_day`); the Portfolio Digest goes out every run.
- `sources.*` — news toggles, `fetch_full_articles`, `relevance_filter`,
  `adanos_platforms` (`[reddit, x, news, polymarket]`), `lookback_hours`.
- `analysis` — AI chain: gemini → groq → openrouter → heuristic.
- `delivery` — email / telegram / both / none.

## Pipeline
1. **Collect** news (Finnhub first for direct links; Google News; macro feeds) →
   relevance-filter noise → read full articles.
2. **Portfolio data** — fundamentals, technicals, deep ETF analysis, earnings, filings.
3. **Sentiment + crowd + flags + crypto** — VADER tone, Adanos multi-source crowd,
   market snapshot, coin prices/technicals/news.
4. **Analyse portfolio** (AI) → cards, sector highlights, watch tickers, crypto calls.
5. **Deep-analyse stocks to watch** — Shariah-screen, fetch their full data, re-analyse.
6. **Build two reports** → save → email both.

## Module map
```
config.yaml       watchlist + settings (no secrets)
sources.py        news collection + relevance filter + full-article reading
fundamentals.py   yfinance fundamentals + factor scores (+ ETF detection, debt/cash)
etf.py            deep ETF analysis: returns, attribution, NAV, risk, peers
technicals.py     RSI/MACD/ATR/SMA-EMA/volume/S-R + rule-based signal (stocks & crypto)
shariah.py        automated Shariah screen (business activity + 33% debt/cash ratios)
market_data.py    Finnhub: company news, earnings calendar/surprises, peers
filings.py        SEC EDGAR: latest filings + MD&A/outlook excerpt
sentiment.py      VADER news tone (offline)
social.py         Adanos multi-source crowd sentiment (stocks + crypto) + diagnostics
portfolio.py      look-through company + sector concentration
providers.py      free LLM layer (Gemini/Groq/OpenRouter) + fallback chain
analyzer.py       fuses everything → structured analysis (AI owns the calls)
digest.py         renders the two phone-friendly reports (HTML + text)
delivery.py       email + Telegram
main.py           orchestrates the two-report run (+ [env]/[adanos] diagnostics)
.github/workflows/main.yml   scheduler + secrets mapping + artifact upload
```

## Free-tier budget (typical: 5 holdings, weekday runs)
- **Adanos**: ~9 requests/run ≈ 190/month vs 250 free — fine, but trim
  `adanos_platforms` if you add many tickers.
- **Gemini**: ~3 calls/run vs ~1,500/day free — trivial.
- **Finnhub**: per-minute limit (60), never approached.
- **GitHub Actions**: a few minutes/run vs 2,000 free minutes/month.
- **Yahoo**: no formal tier; heavy ETF watchlists can see occasional throttling
  (fields degrade to "n/a", the run continues).

## Honest limitations
- **Not investment advice.** All signals, reads, and calls describe the current
  setup; they are not recommendations or forecasts. The AI only sees the data
  it's given and can be wrong. Do your own research.
- **The Shariah screen is an automated heuristic**, not a certified ruling — it
  can't check receivables or the <5% impermissible-income screen. Verify with
  Zoya / Musaffa / IdealRatings before acting.
- **Free data has gaps**, especially for niche/new ETFs (holdings, expense, NAV
  may be n/a) and thinly-covered tickers (little news → tone n/a).
- **Crowd sentiment covers what people actually discuss** — rich for popular
  stocks and major coins, sparse for niche Shariah ETFs.
- **Company-level ETF look-through** uses disclosed top ~10 holdings (largest
  overlaps); sector-level look-through is complete.
- **SEC filings** are US-listed companies only.
- Scheduled runs can be delayed minutes at peak; GitHub pauses schedules after
  60 days of repo inactivity (visit the repo occasionally).

See `COMPARISON.md` for how this stacks up against paid tools.
