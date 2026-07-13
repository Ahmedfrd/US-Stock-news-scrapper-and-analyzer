# How this tool compares — and what I improved

You asked me to benchmark this project against the best comparable products and
then improve it. Here's the honest read.

## The landscape (2026)

The best retail-facing tools split into three jobs:

| Tool | Job it does | Strength | Cost | What I borrowed |
|------|------------|----------|------|-----------------|
| **FinChat / Fiscal.ai** | Fundamental research terminal | Deep financials, 10–20yr history, segment KPIs, earnings-transcript Q&A, every figure cited to filings; peer charts (e.g. NVDA vs AMD margins) | Free tier; Pro ~$39/mo | Fundamentals + valuation context; **peer comparison** |
| **Seeking Alpha (Quant + Virtual Analyst)** | Factor grades + AI briefings | A–F grades on Value/Growth/Profitability/Momentum/EPS-revisions; huge contributor base for bull/bear cases | ~$239/yr | **Transparent factor scores**; **bull vs bear** framing |
| **Danelfin** | Explainable AI score | 1–10 score from 600+ indicators, and it *shows which signals drove it* | Paid | **Explainability** — show the sub-scores, not a black box |
| **Prospero.ai** | Free AI signal layer | Institutional-flow-style signals, daily picks, genuinely free | Free (mobile) | Free-first philosophy; daily prioritisation |
| **NowNews** | Push news intelligence | AI-scored news with **impact/materiality**, affected-asset mapping, **narrative-vs-data contradiction** ("honesty") signals, news-to-price correlation | ~€15–60/mo | **News impact scoring**; **divergence flags** |
| **Koyfin / Stock Rover** | Data terminal | Broad fundamentals, estimates, macro dashboards, AI summaries | Free tier + paid | Fundamentals breadth (within free-source limits) |
| **TrendSpider / Tickeron** | Technical/charting | Pattern recognition, backtesting, alerts | Paid | Light momentum signals only |

Where your original version sat: a **news-only summariser**. It aggregated
headlines and gave keyword/LLM sentiment, but with no financial context — so it
couldn't tell a genuinely thesis-changing story from noise. That's the single
biggest gap the paid tools close.

## What I changed (and which competitor it's modelled on)

1. **Completely free stack.** Analysis now defaults to Google's Gemini free tier
   (Flash, no credit card, ~1,500 requests/day, 1M context) with Groq and
   OpenRouter free tiers as drop-in fallbacks, plus a $0 local heuristic if no key
   is set. All data is free (Yahoo Finance, Google News RSS, central-bank feeds).

2. **Fundamentals + valuation context** *(FinChat, Koyfin)* — every stock now
   carries P/E, forward P/E, P/S, P/B, PEG, revenue/earnings growth, margins, ROE,
   debt/equity, current ratio, FCF, and trailing returns.

3. **Transparent factor scores** *(Seeking Alpha Quant, Danelfin, Zen)* — Value,
   Growth, Profitability, Momentum, and Health each scored 0–100 by simple,
   documented rules (you can read every threshold in `fundamentals.py`), plus a
   composite. Explainable by design — no black box.

4. **News impact / materiality rating** *(NowNews)* — each day's news is rated
   high/medium/low for how much it actually matters to the thesis, so a genuine
   catalyst outranks a fluff headline.

5. **Divergence / contradiction flags** *(NowNews honesty signals)* — the model
   is asked to flag mismatches between the news, the price move, and the
   fundamentals (e.g. "stock up 5% but guidance was cut and it's already at 40× —
   move looks stretched").

6. **Bull vs bear case** *(Seeking Alpha contributors)* — a one-line steelman for
   each side, per stock, instead of a single sentiment label.

7. **Analyst consensus & catalysts** — mean price target with implied upside, and
   the next earnings date flagged when it's within a week.

8. **Peer comparison** *(FinChat)* — optional `peers:` per stock puts competitors'
   valuation/growth/margin/score side by side.

9. **Watchlist prioritisation** *("what matters today")* — the whole watchlist is
   ranked by materiality so the most important item leads, rather than a flat list.

10. **The read is now in-context.** The model receives the fundamentals *and* the
    headlines together and is prompted to judge the news against where the company
    actually stands — which is what "gauge the impact on the stock" requires.

## Honest limitations vs the paid tools

- **No earnings-call transcript ingestion** (FinChat's edge). Free transcript
  sources are unreliable; could be added later via a free provider if one exists.
- **No options flow / institutional 13F data** (Prospero's edge) — not freely
  available at quality.
- **Free financial data (Yahoo) can lag or have gaps**; paid tools use vetted
  feeds. The tool degrades gracefully (shows "n/a") rather than guessing.
- **Factor scores are rule-based, not a trained model** — deliberately, for
  transparency. They're context, not predictions, and no backtest is claimed.
- **A daily digest, not real-time push** like NowNews/Dataminr.

## Sensible next steps if you want to go further

- Add a **SEC EDGAR** watcher (free, official) for 8-K/10-Q filings as catalysts.
- Add a **weekly deep-dive** mode that spends more of the free token budget on one
  stock (full peer table + multi-quarter trend).
- Add **charts** (price + factor radar) to the email via a free plotting lib.
- Provider **fallback chain** (try Gemini → Groq → OpenRouter → heuristic) so a
  single rate-limit never blanks the digest.

## Update — closing more of the gap

A later pass added several things that move it closer to the paid tools:

- **Finnhub free tier** for company news with real article links, an earnings
  calendar, and earnings surprises (actual vs estimate EPS) — the data behind the
  "about to report / just reported" alerts.
- **SEC EDGAR** filings (free, official): when a company reports, it pulls the
  latest 8-K/10-Q/10-K and summarises results, outlook, and **management's
  discussion** from the filing text — a free stand-in for FinChat's transcript
  edge, sourced and linked.
- **VADER sentiment** — a free, offline numeric sentiment score per stock,
  independent of the LLM (Finnhub's own sentiment endpoint is paid).
- **Weekly sector deep-dive** — sector-wide developments scraped over 7 days with
  a read-across to your holdings (NowNews-style affected-asset framing).
- **Provider fallback chain + engine banner** — tries Gemini → Groq → OpenRouter →
  heuristic, and every report says which actually ran.
- **Off-machine, zero-install** — runs on GitHub Actions on a schedule.

Still not matched for free: full earnings-call *transcripts* with tone analysis,
options/institutional flow, audited backtests, and multi-decade fundamental history.

