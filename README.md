# 📈 Market & Macro Digest

A free, automatic, off-machine research brief for your portfolio. Every weekday
morning it scrapes finance + macro + crypto news, pulls the fundamentals,
technicals, and crowd-sentiment data behind it, has a free AI analyse everything,
and emails you **two reports** (all analysis written as scannable bullet points):

- **📊 Portfolio Digest** — your holdings in depth, plus a portfolio-wide
  look-through concentration view.
- **🇺🇸 Market Digest US** — the general market: global flags, detailed sector
  highlights, Shariah-screened *stocks to watch* (US-listed, large or mid cap,
  picked for actionable near-term news catalysts, each fully analysed like a
  holding), a major-coins crypto section, industries & themes, and macro.
  Daily by default; set `market_digest_day` to a UTC weekday to make it weekly.

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
- **Macro backdrop** — detailed paragraph + figure-cited bullets, then **Risks**
  and **Key Upcoming Events** as bulleted closing sections.

### 🇺🇸 Market Digest US
- Daily by default; set `market_digest_day` to a UTC weekday for weekly.
- **Market overview** + **global market flags**.
- **Sector highlights** — sectors affected by the day's news flow, each
  bullish/bearish/neutral with 3–5 detailed, figure-cited bullets.
- **Whole-market scan** — the discovery pool. Every run scans the *entire* US
  market's news flow (general wires, broad business RSS, catalyst searches),
  works out which companies the day's stories are actually about, and prices
  them. The table lists those candidates with their move and lead story, ticking
  the ones taken forward. See [Whole-market scan](#whole-market-scan) below.
- **Stocks to watch** — names drawn from that market-wide pool, **not** from
  your portfolio or your ETFs' holdings, **Shariah-screened** when
  `shariah_only: true`, each given the *same full analysis* as a holding plus a
  compliance badge with the debt/cash ratios.
- **Crypto — major coins** — market call with figure-cited drivers; every coin
  in your `crypto_watch` list gets a combined buy/hold/sell call, price + 1d/7d
  moves, Adanos crowd sentiment, collapsible technicals, and crypto news links.
- **Industries & themes** — your topics as detailed paragraphs, with key
  non-portfolio companies driving each.
- **Weekly sector deep-dive** (once a week) with read-across to your holdings.
- **Macro** — detailed paragraph, figure bullets, then **Risks** and **Key
  Upcoming Events** as bulleted closing sections (each event/risk one line,
  e.g. "15-Jul, 8:30am ET: US CPI print").

### The per-name card (holdings and stocks-to-watch get identical treatment)
News first, noise last — in this order:
1. **Header** — price + move · news tone · crowd consensus one-liner (with
   total mention count) · impact & sentiment badges.
2. **The news** — a detailed bulleted read of the day's headlines on the name,
   plus a highlighted **news-impact-on-the-company** box and a divergence flag.
   For an ETF, the news summary of what its underlying holdings did today and
   what it means for the fund ("what moved it" + "holdings news & impact")
   appears here too — as prose, not a raw article dump.
3. **Crowd sentiment panel** (Adanos: Reddit · X · News · Polymarket) —
   per-source bullish/neutral/bearish stacked bars, buzz, **mentions** (how many
   people are actually talking, so you can judge whether the %s represent a big
   population), trend, and a blended consensus.
4. **Technical analysis** — RSI, MACD, ATR, SMA/EMA, volume,
   support/resistance, the **rule-based signal** (deterministic reference) and
   the **technical read** (the AI's own call, which may override the rules with
   reasoning) in one box.
5. **Fundamentals** — factor scores (Value / Growth / Profit / Momentum /
   Health + composite), valuation, growth, margins, analyst target, next
   earnings; earnings & outlook with management commentary from SEC filings.
6. **Research verdict** — the 3-model bull/bear/judge debate.
7. **Source articles** — a plain, always-visible list at the end of the card
   (full articles are read, not just headlines).

**ETF cards** additionally show: multi-horizon returns (1d→1y, YTD), risk
(volatility, max drawdown, beta), NAV premium/discount, expense/yield/AUM,
**move attribution by holding** (which components drove today's move, with a
summed "explained move"), sector weights, vs-benchmark and vs-competitor-ETF
tables — plus the component breakdown below.

#### Component companies — news by company

A fund is a basket of businesses, so ETF cards report it that way. Each of the
top `etf_holdings_news` holdings (default 8) gets **its own block**:

- the company's name, its **weight in the fund**, its **1-day move**, and how
  many points of the fund's move it explains;
- what actually happened at *that company* today — the deal size, guidance
  figure, lawsuit, upgrade with the new target — read from the article body, not
  the headline, with a bullish/bearish/neutral call;
- one line on how it feeds through to the fund;
- **that company's own article links**, under its own heading.

News is queried by company name (so foreign constituents like Samsung/SK Hynix
are covered properly). The fund's own news stays at the end of the card. When
no AI is available, the same per-company blocks are still built from each
holding's headlines and its measured contribution.

### Whole-market scan

Stocks-to-watch used to be picked out of news the digest had already fetched for
*your* names — your tickers, your ETFs' holdings, your topics — so every
suggestion was portfolio-adjacent by construction. It can't be any more. Each
run now also:

1. **Scans the market at large** — Finnhub's general news wire, broad business
   RSS (CNBC, Yahoo Finance, MarketWatch, Investing.com, Seeking Alpha), and a
   dozen catalyst-shaped searches ("stock surges after…", "price target raised",
   "FDA approval", "merger agreement", "IPO debut", …).
2. **Resolves companies from the stories** — explicit tickers (`(NASDAQ: ABCD)`,
   `$ABCD`), the wire's own ticker tags, and company **names** matched against
   Finnhub's full US symbol directory.
3. **Prices every candidate** — today's move, 5-day move, and volume vs average,
   in one batched request — then ranks by coverage and movement.
4. **Reads the lead story in full** for the top candidates, so the AI is judging
   substance rather than a headline.

That pool goes to the AI as the explicit source for stocks-to-watch, and tops up
the list directly if the model leans on familiar names. Portfolio holdings are
excluded (they already have their own cards). Everything is tunable under
`market_scan:` in `config.yaml`; set `enabled: false` to switch it off.

---

## Data sources (all free)

| Data | Source | Key |
|------|--------|-----|
| News (direct publisher links, full articles) | Finnhub company-news + Marketaux + Google News RSS | `FINNHUB_API_KEY` (free) |
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
- `market_digest_day` — `daily` (default) sends the Market Digest every run;
  a UTC weekday name (e.g. `Thu`) makes it weekly instead.
- `sources.*` — news toggles, `fetch_full_articles`, `relevance_filter`,
  `adanos_platforms` (`[reddit, x, news, polymarket]`), `lookback_hours`.
- `market_scan.*` — the whole-market discovery pass behind stocks-to-watch:
  `enabled`, `finnhub_general`, `broad_feeds`, `catalyst_search`,
  `catalyst_queries` (override the built-in list), `max_candidates`,
  `min_move_pct` (e.g. `2` to only consider names that moved ≥2%),
  `full_articles_for_top`, `watch_max`.
- `etf_holdings_news` — how many of a fund's top holdings get their own news
  section (default 8).
- `analysis` — AI chain: gemini → groq → openrouter → heuristic.
- `delivery` — email / telegram / both / none.

## Pipeline
1. **Collect** news (Finnhub first for direct links; Google News; macro feeds) →
   relevance-filter noise → read full articles.
2. **Scan the whole market** — general wires + broad RSS + catalyst searches →
   resolve companies from the stories → price and rank them as candidates.
3. **Portfolio data** — fundamentals, technicals, deep ETF analysis (including a
   news pull per component company), earnings, filings.
4. **Sentiment + crowd + flags + crypto** — VADER tone, Adanos multi-source crowd,
   market snapshot, coin prices/technicals/news.
5. **Analyse portfolio** (AI) → cards, sector highlights, watch tickers, crypto calls.
6. **Deep-analyse stocks to watch** — drawn from the market-wide pool,
   Shariah-screened, full data fetched, re-analysed.
7. **Build two reports** → save → email both.

## Module map
```
config.yaml       watchlist + settings (no secrets)
sources.py        news collection + whole-market scan + relevance filter + full articles
discovery.py      market-wide candidate discovery: stories -> companies -> priced movers
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
