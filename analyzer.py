"""
analyzer.py — fuse news + fundamentals + sentiment + earnings + filings into a
holistic read, and report exactly which engine produced it.

Engine selection is a fallback CHAIN: try the configured provider, then any
configured fallbacks that have keys, then the free local heuristic. Whatever
happens is recorded in a status object so the digest can tell you at the top
which AI ran — or that it failed and the heuristic was used, and why.
"""

from __future__ import annotations

from collections import defaultdict

import providers


# --------------------------------------------------------------------------- #
#  Context formatting
# --------------------------------------------------------------------------- #
def _money(x):
    try:
        x=float(x)
        for u,d in (("T",1e12),("B",1e9),("M",1e6)):
            if abs(x)>=d: return f"${x/d:.2f}{u}"
        return f"${x:,.0f}"
    except Exception:
        return "n/a"


def _fmt(x, s="", pct=False, nd=2):
    if x is None:
        return "n/a"
    try:
        return f"{x*100:.1f}%" if pct else f"{x:.{nd}f}{s}"
    except Exception:
        return str(x)


def _fund_block(f) -> str:
    sc = f.scores or {}
    out = [
        f"  sector: {f.sector or 'n/a'} / {f.industry or 'n/a'}",
        f"  price {_fmt(f.price)}  1d {_fmt(f.change_1d,'%')}  3m {_fmt(f.ret_3m,'%')}  6m {_fmt(f.ret_6m,'%')}",
        f"  valuation: P/E {_fmt(f.pe)} fwd {_fmt(f.forward_pe)} P/S {_fmt(f.ps)} PEG {_fmt(f.peg)}",
        f"  growth: rev {_fmt(f.rev_growth,pct=True)} earn {_fmt(f.earn_growth,pct=True)}; "
        f"margins: net {_fmt(f.net_margin,pct=True)} gross {_fmt(f.gross_margin,pct=True)}; ROE {_fmt(f.roe,pct=True)}",
        f"  analyst target {_fmt(f.target_mean)} ({_fmt(f.implied_upside,'%')} upside), rating {_fmt(f.rating)}/5",
        f"  factor scores: value {sc.get('value')} growth {sc.get('growth')} profit {sc.get('profitability')} "
        f"momentum {sc.get('momentum')} health {sc.get('health')} COMPOSITE {sc.get('composite')}",
    ]
    return "\n".join(out)


def _build_context(items, funds, extras, watchlist) -> str:
    by_group = defaultdict(list)
    for it in items:
        by_group[it.group].append(it)
    sent = extras.get("sentiment", {})
    crowd = extras.get("crowd", {})
    earn = extras.get("earnings", {})
    fils = extras.get("filings", {})

    out = []
    flags = extras.get("flags") or []
    if flags:
        out.append("=== MARKET FLAGS (today's levels — CITE these figures in macro & sector analysis) ===")
        out.append("  " + " | ".join(
            (f"{fl['name']} {fl.get('price')} ({fl.get('pct'):+.2f}%)" if fl.get('pct') is not None
             else f"{fl['name']} {fl.get('price')}") for fl in flags))
    for s in watchlist.get("stocks", []):
        tk = s.get("ticker", "").strip()
        if not tk:
            continue
        f = funds.get(tk)
        kind = "ETF/FUND" if (f and f.is_etf) else "STOCK"
        out.append(f"\n=== {kind} {tk} ({f.name if f else tk}) ===")
        if f and f.is_etf:
            prof = (extras.get("etf") or {}).get(tk)
            out.append("  THIS IS AN ETF/FUND — analyse at the fund level (holdings, "
                       "sector tilt, flows, NAV, risk), not as a company.")
            if prof:
                r = prof.returns or {}
                out.append(f"  returns: 1d {r.get('1d')}  1w {r.get('1w')}  1m {r.get('1m')}  "
                           f"3m {r.get('3m')}  6m {r.get('6m')}  YTD {r.get('ytd')}  1y {r.get('1y')}")
                out.append(f"  risk: annualised vol {prof.vol_1y}  max drawdown(1y) {prof.max_drawdown_1y}  "
                           f"beta vs {prof.benchmark} {prof.beta}")
                out.append(f"  NAV {prof.nav}  premium/discount {prof.premium_discount}%  "
                           f"expense {prof.expense_ratio}  yield {prof.etf_yield}  AUM {prof.aum}  "
                           f"category {prof.category}")
                if prof.rel_market:
                    rm = "  ".join(f"{k} {round(v,2)}" for k, v in prof.rel_market.items())
                    out.append(f"  vs benchmark ({prof.benchmark}) [% pts]: {rm}")
                if prof.top10_weight is not None:
                    out.append(f"  concentration: top-10 = {prof.top10_weight}% of fund")
                if prof.sector_weights:
                    sw = ", ".join(f"{k} {v}%" for k, v in list(prof.sector_weights.items())[:6])
                    out.append(f"  sector weights: {sw}")
                if prof.holdings:
                    out.append(f"  TODAY'S MOVE ATTRIBUTION (holding: weight, 1d move, contribution to ETF move):")
                    for h in prof.holdings[:8]:
                        out.append(f"    {h.symbol} ({h.name}): wt {round((h.weight or 0)*100,1)}%, "
                                   f"1d {h.ret_1d}%, contrib {h.contribution} pts")
                    out.append(f"  sum of contributions ≈ {prof.explained_move} pts of the ETF's move")
                if prof.peers:
                    out.append("  peer ETFs:")
                    for pe in prof.peers:
                        out.append(f"    {pe.ticker}: 1y {pe.ret_1y}%, expense {pe.expense}, "
                                   f"yield {pe.etf_yield}, AUM {pe.aum}")
            # News on major underlying holdings — reported PER COMPANY. The reader
            # wants the ETF broken down into its big component companies, each
            # with its own news, not one blended paragraph about "the holdings".
            hn = (extras.get("etf_holding_news") or {}).get(tk, {})
            wt_by_sym = {h.symbol: h for h in (prof.holdings if prof else [])}
            if hn:
                out.append(f"  --- NEWS ON {tk}'s COMPONENT COMPANIES (write ONE separate "
                           f"entry per company in etf.holdings_news) ---")
            for sym, arts in hn.items():
                if not arts:
                    continue
                h = wt_by_sym.get(sym)
                head = f"  COMPONENT {sym}"
                if h is not None:
                    head += (f" ({h.name}) — weight {round((h.weight or 0)*100,1)}%, "
                             f"1d {h.ret_1d}%, contributes {h.contribution} pts to the fund")
                out.append(head + " — its news (read the CONTENT for the real reason behind "
                                  "its move, not just the headline):")
                for a in arts[:4]:
                    snip = (a.summary[:1400] + "…") if len(a.summary or "") > 1400 else (a.summary or "")
                    out.append(f"    - {a.title} [{a.source}]" + (f"\n      {snip}" if snip else ""))
        elif f:
            out.append(_fund_block(f))
        sg = sent.get(tk)
        if sg:
            out.append(f"  news tone (VADER on headlines): {sg['score']} ({sg['label']})")
        cw = crowd.get(tk)
        if cw and cw.get("has_data"):
            con = cw.get("consensus", {})
            src_bits = []
            for name, sv in cw.get("sources", {}).items():
                if sv.get("bullish") is not None:
                    src_bits.append(f"{name} {sv['bullish']}%bull/{sv['bearish']}%bear (buzz {sv.get('buzz')})")
                elif sv.get("score") is not None:
                    src_bits.append(f"{name} score {sv['score']} (buzz {sv.get('buzz')})")
            out.append(f"  CROWD sentiment (Adanos, multi-source): consensus {con.get('label')} "
                       f"({con.get('bullish')}% bull / {con.get('bearish')}% bear / {con.get('neutral')}% neutral, "
                       f"buzz {con.get('buzz')}). By source: " + "; ".join(src_bits))
        tech = (extras.get("technicals") or {}).get(tk)
        if tech and not tech.error:
            out.append(f"  TECHNICALS: signal {tech.signal.upper()} (bias {tech.bias}, score {tech.score}); "
                       f"RSI {tech.rsi}; MACD hist {tech.macd_hist} {('('+tech.macd_cross+' cross)') if tech.macd_cross else ''}; "
                       f"price {tech.price} vs SMA20 {tech.sma20}/SMA50 {tech.sma50}/SMA200 {tech.sma200}; "
                       f"ATR {tech.atr} ({tech.atr_pct}%); volume {tech.vol_ratio}x avg; "
                       f"support {tech.support} / resistance {tech.resistance}")
            if tech.reasons:
                out.append("    technical reasons: " + "; ".join(tech.reasons))
        e = earn.get(tk) or {}
        if e.get("upcoming"):
            u = e["upcoming"]
            out.append(f"  UPCOMING EARNINGS in {u.get('days_away')} days ({u.get('date')}); "
                       f"EPS est {u.get('eps_estimate')}, rev est {_money(u.get('rev_estimate'))}")
        if e.get("recent"):
            rc = e["recent"]
            out.append(f"  JUST REPORTED ({rc.get('date')}, {rc.get('days_ago')}d ago): "
                       f"EPS actual {rc.get('eps_actual')} vs est {rc.get('eps_estimate')} "
                       f"(surprise {rc.get('eps_surprise_pct')}%), rev actual {_money(rc.get('rev_actual'))}")
        fil = fils.get(tk)
        if fil and fil.get("excerpt"):
            out.append(f"  LATEST SEC FILING {fil['form']} filed {fil['filed']} — excerpt:")
            out.append(f"    \"{fil['excerpt'][:2500]}\"")
        for it in by_group.get(tk, [])[:10]:
            snip = (it.summary[:1800] + "…") if len(it.summary) > 1800 else it.summary
            out.append(f"    - {it.title} [{it.source}]" + (f"\n      {snip}" if snip else ""))

    for t in watchlist.get("topics", []):
        out.append(f"\n=== TOPIC: {t} ===")
        for it in by_group.get(t, [])[:8]:
            out.append(f"    - {it.title} [{it.source}]")

    macro = by_group.get("Macro", [])
    if macro:
        out.append("\n=== MACRO HEADLINES ===")
        for it in macro[:10]:
            out.append(f"    - {it.title} [{it.source}]")

    if extras.get("weekly"):
        sn = extras.get("sector_news", {})
        for sector, arts in sn.items():
            out.append(f"\n=== SECTOR (weekly): {sector} ===")
            for it in arts[:12]:
                out.append(f"    - {it.title} [{it.source}]")

    # ---- WHOLE-MARKET DISCOVERY POOL -------------------------------------- #
    # Candidates found by scanning the entire market's news flow, NOT the
    # portfolio. This is the pool stocks_to_watch should be drawn from so the
    # recommendations aren't confined to names the reader already owns.
    cands = extras.get("candidates") or []
    if cands:
        out.append("\n=== MARKET-WIDE CANDIDATES (companies in TODAY'S news across the WHOLE "
                   "US market — NONE of these are portfolio holdings; this is the pool to "
                   "pick stocks_to_watch from) ===")
        for c in cands:
            bits = [f"  {c['ticker']}"]
            if c.get("price") is not None:
                bits.append(f"price {c['price']}")
            if c.get("pct_1d") is not None:
                bits.append(f"1d {c['pct_1d']:+.2f}%")
            if c.get("pct_5d") is not None:
                bits.append(f"5d {c['pct_5d']:+.2f}%")
            if c.get("vol_ratio") is not None:
                bits.append(f"vol {c['vol_ratio']}x avg")
            bits.append(f"{c.get('mentions', 1)} stor{'y' if c.get('mentions',1)==1 else 'ies'}")
            out.append(", ".join(bits) + ":")
            for a in (c.get("articles") or [])[:3]:
                snip = (a.summary or "")[:900]
                out.append(f"    - {a.title} [{a.source}]" + (f"\n      {snip}" if snip else ""))

    scan_items = extras.get("market_scan") or []
    if scan_items:
        out.append("\n=== MARKET-WIDE NEWS FLOW (whole-market wires & catalyst searches — "
                   "use for sector calls and for spotting names not listed above) ===")
        for it in scan_items[:40]:
            snip = (it.summary or "")[:500]
            out.append(f"    - {it.title} [{it.source}]" + (f"\n      {snip}" if snip else ""))

    crypto = extras.get("crypto") or {}
    if crypto.get("snapshot") or crypto.get("news"):
        out.append("\n=== CRYPTO (major coins) ===")
        for c in crypto.get("snapshot", []):
            line = f"  {c['symbol']}: {c.get('price')} ({c.get('pct')}% 1d"
            if c.get("pct7d") is not None:
                line += f", {c.get('pct7d')}% 7d"
            line += ")"
            sv = (crypto.get("sentiment") or {}).get(c["symbol"])
            if sv and sv.get("consensus", {}).get("label") not in (None, "n/a"):
                line += f" — crowd {sv['consensus'].get('label')} ({sv['consensus'].get('bullish')}% bull)"
            out.append(line)
        for it in crypto.get("news", [])[:8]:
            out.append(f"  news: {it.title} [{it.source}]")
    return "\n".join(out)


SYSTEM = (
    "You are a buy-side analyst writing a concise daily briefing. For each name you "
    "get fundamentals (or fund holdings for ETFs), factor scores, a news-tone reading, "
    "CROWD sentiment (Reddit), any earnings event, a SEC filing excerpt when available, "
    "TECHNICAL indicators (RSI, MACD, moving averages, ATR, volume, support/resistance "
    "with a rules-based signal), and today's headlines — where available, the FULL "
    "ARTICLE TEXT follows the headline, not just the title. The reader's TOP priority is "
    "STAYING ON TOP OF THE NEWS: what happened to each name (and to an ETF's major "
    "holdings) and what it concretely means — make summaries and news_impact the most "
    "detailed, specific parts of your output. READ THE ARTICLE CONTENT, not just the "
    "headline, and extract the actual reason behind any move: the specific deal size, "
    "lawsuit amount, guidance number, regulatory action, analyst target change, or "
    "management quote that explains WHY — never write things like 'reported in headlines "
    "like X' or 'boosted by positive news' as a substitute for the real reason; if the "
    "article content doesn't explain the move, say what IS known and that the driver is "
    "unclear, rather than restating the headline as if it were the explanation. Do these "
    "well: (1) judge the IMPACT of news on the company using its financials; (2) give "
    "a TECHNICAL read (buy/hold/sell) that is YOUR interpretation of the indicators — "
    "you may override the rule-based signal with reasoning; (3) from ALL the day's "
    "headlines across the watchlist, macro, topics AND the whole-market scan, identify "
    "the SECTORS affected, the effect, and a bullish/bearish call with reasons — plus "
    "specific STOCKS TO WATCH with a one-line why. "
    "STOCKS TO WATCH ARE A WHOLE-MARKET JOB, NOT A PORTFOLIO JOB: you are given a "
    "MARKET-WIDE CANDIDATES block listing companies from across the entire US market "
    "that are in today's news, with their price moves — none of them are holdings. "
    "Draw your picks primarily from there. Do NOT confine yourself to the reader's "
    "holdings, to the companies inside their ETFs, or to their listed topics; a name "
    "the reader has never owned is exactly what this section is for. "
    "For an ETF, analyse at the fund level AND break it down into its COMPONENT "
    "COMPANIES: give each major holding with news its own separate entry explaining "
    "what happened to THAT company and how it feeds through to the fund. "
    "Use ONLY the data provided; never invent numbers. Be SPECIFIC and QUANTITATIVE — "
    "cite concrete figures (price %, index/commodity levels from the market flags, deal "
    "sizes, targets) in the macro, sector and industry sections. "
    "CRITICAL — WHY, NOT JUST WHAT: a number with no cause is useless to this reader. "
    "For EVERY move you mention (a stock, an index, a sector, an ETF, or one of an ETF's "
    "holdings) your PRIMARY job is to explain the SPECIFIC CATALYST behind it, taken from "
    "the article text — the deal and its size, the guidance/EPS/revenue figure, the "
    "lawsuit or ruling, the economic report, the analyst upgrade/downgrade and new target, "
    "the product launch, the management quote. NEVER explain a move with empty filler like "
    "'broader market sentiment', 'profit-taking', 'sector rotation', 'sector weakness', "
    "'market dynamics', or 'investors reacted' UNLESS an article explicitly names that as "
    "the cause — those phrases are NON-ANSWERS and are worse than saying nothing. If the "
    "provided articles genuinely do not explain a move, say so plainly (e.g. 'no clear "
    "catalyst in today's coverage — the move looks technical/flow-driven') instead of "
    "inventing a vague reason. Lead each bullet with the WHY. "
    "FORMAT: write every "
    "multi-sentence free-text field as newline-separated bullet lines, each starting "
    "with '- ' and stating ONE point — lead with the reason/catalyst, then the figures "
    "that quantify it — never a dense paragraph. "
    "This is informational analysis, NOT investment advice."
)


def _instructions(tickers, topics, weekly, shariah=False):
    weekly_block = ""
    shariah_line = ""
    if shariah:
        shariah_line = ("\n  // For stocks_to_watch, include ONLY names that are plausibly "
                        "Shariah-compliant: avoid conventional banks/insurers & interest-based "
                        "finance, alcohol, tobacco, gambling, weapons/defense, adult, and pork.")
    if weekly:
        weekly_block = """,
  "sectors": [
    {"sector": "Semiconductors",
     "summary": "2-3 sentences on the week's sector-wide developments",
     "developments": ["short point", "short point"],
     "read_across": "how these developments affect the watchlist names in this sector"}
  ]"""
    return f"""Return ONLY a JSON object (no prose, no code fences):

{{
  "market_overview": "4-6 bullet lines (each starting '- '). This is a NEWS DIGEST, not a stat dump: lead with the day's market-moving STORY and the REASON behind it (e.g. '- Tech sold off ~3% as investors dumped AI names after Moonlit AI's cheaper model raised margin fears — Nvidia -5%, chip suppliers hit'), then tie it to the macro backdrop. Every bullet must name WHAT happened, to WHOM, by HOW MUCH (figures), and WHY. Never just report levels with no narrative.",
  "priority": [{{"ticker": "AAPL", "why": "why this matters most today"}}],
  "stocks": [
    {{"ticker": "AAPL",
      "impact": "high|medium|low",
      "sentiment": "bullish|bearish|neutral|mixed",
      "summary": "3-5 bullet lines (each starting '- '): the day's NEWS on this name — the SUBSTANCE from the article content (the deal size, guidance number, lawsuit amount, regulatory action, analyst call, management quote — whatever concretely explains what happened), not the headline restated, AND the implication given fundamentals/valuation. Be detailed and specific; this is the heart of the report",
      "news_impact": "the concrete effect of today's news on THIS company's financials/outlook/competitive position, grounded in specific facts from the article body (figures, terms, dates) — not a paraphrase of the headline; empty string if no material news",
      "fundamental_read": "1 sentence on financial standing (for an ETF: what the fund holds / its tilt)",
      "divergence": "mismatch between news, price move, fundamentals — or empty string",
      "crowd_note": "1 sentence on what retail/Reddit sentiment says vs the fundamentals — or empty string",
      "earnings": {{"result": "beat/miss/inline with the numbers, or empty",
                    "outlook": "guidance/outlook from filing, or empty",
                    "management_review": "management's commentary from the filing, or empty"}},
      "etf": {{"move_explainer": "for a FUND only: what drove today's move. Name the specific holdings AND — this is the point — the actual NEWS/CATALYST behind each big contributor's move (the deal, earnings figure, downgrade, ruling from that holding's article text), not just 'holding X contributed -0.4 pts'. The contribution figures quantify it; the news explains it. Else empty",
               "nav_read": "premium/discount to NAV and what it implies; else empty",
               "vs_market": "how it's performing vs the benchmark across horizons; else empty",
               "vs_peers": "how it compares to the peer ETFs (returns/expense/yield/size); else empty",
               "risks": "key risks: concentration, volatility, sector/rate sensitivity, beta; else empty",
               "holdings_news": [{{"symbol": "NVDA", "company": "NVIDIA Corp",
                    "news": "2-4 bullet lines (each starting '- ') covering THIS ONE COMPONENT COMPANY on its own: the actual SUBSTANCE from its article content — what specifically happened (a product launch, an earnings beat/miss with the numbers, a deal and its size, a lawsuit, an upgrade/downgrade with the new target, a management statement) and WHY. NEVER write just 'it was up/down Y%, possibly due to broader rotation/profit-taking' — that is a non-answer; if the articles don't explain the move, say the driver is unclear",
                    "impact_on_fund": "1 line: how this company's news feeds through to the fund given its weight and contribution",
                    "call": "bullish|bearish|neutral"}}],
               "holdings_news_impact": "OPTIONAL one-paragraph roll-up across the components — only if it adds something the per-company entries above don't; else empty"}},
      "technical_read": {{"call": "buy|accumulate|hold|reduce|sell",
               "rationale": "YOUR interpretation of the technicals ALONE (RSI/MACD/moving averages/ATR/volume/support-resistance). You may agree or disagree with the rule-based signal provided — if you override it, say why (e.g. overbought RSI inside a strong uptrend is momentum, not a sell)"}},
      "key_drivers": ["short phrase"]}}
  ],
  "sector_highlights": [
    {{"sector": "Oil & Gas Exploration", "call": "bullish|bearish|neutral",
      "points": ["3-5 DETAILED bullets, each a full sentence with CONCRETE FIGURES where available (price moves %, index/commodity levels from the market flags, deal sizes, analyst targets, guidance numbers) — not vague one-liners"]}}
  ],
  "stocks_to_watch": [
    {{"ticker": "NVDA", "call": "bullish|bearish|neutral",
      "reason": "one line: the specific catalyst from today's news and why it matters",
      "source": "market-scan|portfolio-adjacent — where the idea came from"}}
  ],{shariah_line}
  "crypto_highlight": {{"call": "bullish|bearish|neutral",
      "points": ["2-4 DETAILED bullets on the crypto market with figures (BTC/ETH levels & % moves from the data, ETF flows, dominance, catalysts)"],
      "coins": [{{"symbol": "BTC", "call": "buy|accumulate|hold|reduce|sell",
                  "rationale": "COMBINED view for THIS coin: reconcile its price action + technicals with crypto news and market context, 1-2 sentences with figures. Cover EVERY coin present in the CRYPTO data, not just one or two."}}]}},
  "topics": [{{"topic": "semiconductor industry", "sentiment": "...",
      "summary": "3-5 DETAILED bullet lines (each starting '- ') on the industry/market implication, citing specific figures (company moves %, revenue/deal numbers, analyst targets) from the data",
      "key_companies": [{{"name": "TSMC", "note": "1-2 sentences: why this (non-portfolio) company's news matters, with figures if available"}}]}}],
  "macro": {{"summary": "4-6 DETAILED bullet lines (each starting '- ') on the macro backdrop. Explain the DRIVERS behind the moves (the specific report, policy, or event and what it did), not just the levels — explicitly cite the MARKET FLAGS figures (Brent/WTI oil, gold, dollar/DXY, S&P 500 & Nasdaq levels and % moves, 10Y yield) and any rates/inflation/jobs news",
      "points": ["3-5 bullets, each with a concrete figure or level"],
      "risks": ["3-5 forward-looking risks: the concrete trigger (an escalation, a data print, a policy decision) and what it would do to markets — one risk per line, specific not generic"],
      "watch": ["upcoming catalysts/data releases the reader should watch for, one per line, each phrased as 'date/time (if known): what happens', e.g. '15-Jul, 8:30am ET: US CPI print' — from the news/macro data provided, not invented"]}}{weekly_block}
}}

Stocks: {', '.join(tickers)}
Topics: {', '.join(topics)}
Fill "earnings" only when earnings/filing data is present; otherwise use empty strings.
The "stocks" array MUST contain one entry for EVERY name listed under Stocks above —
ETFs/funds included; never omit a name.
For every ETF/fund, etf.holdings_news MUST contain a SEPARATE entry for EACH
component company that has news in the data — one per company, never merged into
a single blended paragraph. Cover every component listed under "NEWS ON ...
COMPONENT COMPANIES"; that per-company breakdown is the main reason the reader
holds the fund in this report.

STOCKS TO WATCH — SCAN THE WHOLE MARKET, NOT THE PORTFOLIO:
- Give 5-8 names. Source them PRIMARILY from the MARKET-WIDE CANDIDATES block and
  the MARKET-WIDE NEWS FLOW — i.e. companies from anywhere in the US market that
  are in today's news. At least 4 picks MUST have no connection to the reader's
  holdings, their ETFs' holdings, or their listed topics.
- Never fill this section with the reader's own holdings or their ETFs'
  constituents just because those names are familiar or well-covered in the data.
- Mid-caps and small-caps are welcome, not just mega-caps.
- Prioritise a concrete near-term catalyst (earnings, guidance, upgrades/
  downgrades, deals, regulatory/FDA, product news) where the price change is
  actionable SHORT-TERM; long-term merit is a bonus, not a requirement.
- MUST be US-listed tickers only (NYSE/NASDAQ symbols) — never use foreign-exchange
  suffixes like .KS/.T/.AS/.DE/.PA/.L/.TO/.HK; if the catalyst concerns a foreign
  company, use its US-listed ADR (e.g. TSM, ASML) or leave it out.
Only include topics/sectors that have data. Return STRICTLY valid JSON — no comments,
no trailing commas. Keep it tight."""


# --------------------------------------------------------------------------- #
#  Heuristic fallback
# --------------------------------------------------------------------------- #
def _sent_to_sentiment(label):
    return {"positive": "bullish", "negative": "bearish",
            "neutral": "neutral", "mixed": "mixed"}.get(label, "neutral")


def _heur_crypto(crypto):
    snap = crypto.get("snapshot") or []
    if not snap:
        return {"call": "neutral", "points": [], "coins": []}
    moves = [c.get("pct") for c in snap if c.get("pct") is not None]
    avg = sum(moves) / len(moves) if moves else 0
    call = "bullish" if avg > 1 else "bearish" if avg < -1 else "neutral"
    coins = []
    for c in snap:
        p = c.get("pct")
        cc = "accumulate" if (p or 0) > 1 else "reduce" if (p or 0) < -1 else "hold"
        coins.append({"symbol": c["symbol"], "call": cc,
                      "rationale": f"{p:+.1f}% today" if p is not None else "n/a"})
    return {"call": call, "points": [f"Major coins averaged {avg:+.1f}% today."], "coins": coins}


def _heur_technical(tech):
    if not tech or tech.error:
        return {"call": "hold", "rationale": "No technical data."}
    m = {"strong buy": "buy", "buy": "accumulate", "hold": "hold",
         "sell": "reduce", "strong sell": "sell"}
    return {"call": m.get(tech.signal, "hold"),
            "rationale": (f"Rule-based technicals: {tech.signal} (bias {tech.bias}). "
                          + ("; ".join(tech.reasons[:3]) if tech.reasons else ""))}


def _heur_combined(tech):
    """Map the rule-based technical signal into a combined_call for the no-LLM path."""
    if not tech or tech.error:
        return {"call": "hold", "rationale": "No technical data; fundamentals/news only."}
    m = {"strong buy": "buy", "buy": "accumulate", "hold": "hold",
         "sell": "reduce", "strong sell": "sell"}
    return {"call": m.get(tech.signal, "hold"),
            "rationale": f"Technicals: {tech.signal} (bias {tech.bias}). "
                         + ("; ".join(tech.reasons[:3]) if tech.reasons else "")
                         + " — combine with fundamentals & news above. (Rule-based; enable AI for a fuller call.)"}


def _heur_watch(extras, stocks):
    """No-LLM path: pick stocks to watch from the WHOLE-MARKET candidate scan
    (companies in today's news anywhere in the market), ranked by how much news
    they drew and how far they moved. Portfolio names are the fallback only."""
    out = []
    for c in (extras.get("candidates") or [])[:6]:
        pct = c.get("pct_1d")
        call = "bullish" if (pct or 0) > 1.5 else "bearish" if (pct or 0) < -1.5 else "neutral"
        why = f"{c.get('mentions', 1)} stor{'y' if c.get('mentions',1)==1 else 'ies'} today"
        if pct is not None:
            why += f", {pct:+.2f}%"
        if c.get("vol_ratio"):
            why += f" on {c['vol_ratio']}x average volume"
        out.append({"ticker": c["ticker"], "call": call,
                    "reason": f"{why} — {c.get('headline','')}",
                    "source": "market-scan"})
    if out:
        return out
    return [{"ticker": s["ticker"], "call": s["sentiment"], "source": "portfolio-adjacent",
             "reason": f"technical {s['combined_call']['call']}, {s['impact']} news impact"}
            for s in stocks
            if s["combined_call"]["call"] in ("buy", "accumulate", "reduce", "sell")][:6]


def _heur_holdings_news(tk, extras):
    """No-LLM path: still break an ETF down per component company, using each
    holding's own headlines + its measured contribution to the fund's move."""
    hn = (extras.get("etf_holding_news") or {}).get(tk) or {}
    if not hn:
        return []
    prof = (extras.get("etf") or {}).get(tk)
    by_sym = {h.symbol: h for h in (prof.holdings if prof else [])}
    out = []
    for sym, arts in hn.items():
        if not arts:
            continue
        h = by_sym.get(sym)
        move = f" moved {h.ret_1d:+.2f}% today" if (h and h.ret_1d is not None) else ""
        out.append({
            "symbol": sym,
            "company": (h.name if h and h.name else sym),
            "news": "\n".join(f"- {a.title} [{a.source}]" for a in arts[:4]),
            "impact_on_fund": (f"{sym}{move}"
                               + (f" at a {round((h.weight or 0)*100,1)}% weight, contributing "
                                  f"{h.contribution:+.3f} pts to the fund's move."
                                  if (h and h.contribution is not None) else ".")),
            "call": "neutral",
        })
    return out


def _heuristic(items, funds, extras, watchlist):
    by_group = defaultdict(list)
    for it in items:
        by_group[it.group].append(it)
    sent = extras.get("sentiment", {})
    crowd = extras.get("crowd", {})
    earn = extras.get("earnings", {})

    stocks = []
    for s in watchlist.get("stocks", []):
        tk = s.get("ticker", "").strip()
        if not tk:
            continue
        grp = by_group.get(tk, [])
        f = funds.get(tk)
        sg = sent.get(tk, {})
        cw = crowd.get(tk, {})
        e = earn.get(tk) or {}
        move = abs(f.change_1d) if (f and f.change_1d is not None) else 0
        impact_score = len(grp) + move + (5 if e.get("recent") else 0) + (3 if e.get("upcoming") else 0)
        impact = "high" if impact_score >= 6 else "medium" if impact_score >= 2 else "low"
        comp = f.scores.get("composite") if f else None
        ed = {"result": "", "outlook": "", "management_review": ""}
        if e.get("recent"):
            rc = e["recent"]
            beat = "beat" if (rc.get("eps_surprise_pct") or 0) > 0 else "miss/inline"
            ed["result"] = (f"EPS {rc.get('eps_actual')} vs est {rc.get('eps_estimate')} "
                            f"({rc.get('eps_surprise_pct')}% {beat})")
        crowd_note = ""
        con = (cw or {}).get("consensus", {})
        if cw and cw.get("has_data") and con.get("label") not in (None, "n/a"):
            crowd_note = (f"Crowd (Adanos) looks {con['label']} across "
                          f"{len(cw.get('sources', {}))} source(s): {con.get('bullish')}% bull / "
                          f"{con.get('bearish')}% bear.")
        stocks.append({
            "ticker": tk, "impact": impact,
            "sentiment": _sent_to_sentiment(sg.get("label", "neutral")),
            "summary": f"{len(grp)} headline(s); news tone {sg.get('score','n/a')} "
                       f"({sg.get('label','n/a')}).",
            "news_impact": "",
            "fundamental_read": (("ETF/fund — " + (f.category or "basket")) if (f and f.is_etf)
                                 else (f"Composite {comp}/100." if comp is not None else "Fundamentals n/a.")),
            "divergence": "", "crowd_note": crowd_note, "bull": "", "bear": "", "earnings": ed,
            "etf": {"move_explainer": "", "nav_read": "", "vs_market": "", "vs_peers": "",
                    "risks": "", "holdings_news_impact": "",
                    "holdings_news": _heur_holdings_news(tk, extras)},
            "combined_call": _heur_combined((extras.get("technicals") or {}).get(tk)),
            "technical_read": _heur_technical((extras.get("technicals") or {}).get(tk)),
            "key_drivers": [], "_impact_score": impact_score,
        })
    stocks.sort(key=lambda x: x.pop("_impact_score"), reverse=True)
    prio = [{"ticker": s["ticker"],
             "why": f"{s['impact']} impact, {s['sentiment']}"
                    + (" · just reported" if s["earnings"]["result"] else "")}
            for s in stocks[:6]]

    topics = []
    for t in watchlist.get("topics", []):
        sg = sent.get(t, {})
        topics.append({"topic": t, "sentiment": _sent_to_sentiment(sg.get("label", "neutral")),
                       "summary": f"{sg.get('n',0)} headline(s), sentiment {sg.get('label','n/a')}.",
                       "key_companies": []})

    result = {"market_overview": "Automated read (no LLM): VADER news tone + crowd buzz + "
              "computed factor scores + rule-based technical signals + earnings flags.",
              "priority": prio, "stocks": stocks, "topics": topics,
              "macro": {"summary": "See macro headlines below.", "risks": [], "watch": []},
              "sector_highlights": [],
              "crypto_highlight": _heur_crypto(extras.get("crypto") or {}),
              # Whole-market first: candidates discovered by scanning the entire
              # market's news flow. Only if that scan produced nothing do we fall
              # back to portfolio names — otherwise the no-LLM path would repeat
              # the very portfolio-bound behaviour this run is meant to avoid.
              "stocks_to_watch": _heur_watch(extras, stocks)}
    if extras.get("weekly"):
        result["sectors"] = [{"sector": sec, "summary": f"{len(arts)} developments this week.",
                              "developments": [a.title for a in arts[:4]], "read_across": ""}
                             for sec, arts in extras.get("sector_news", {}).items()]
    return result


def _fill_missing_stocks(data, items, funds, extras, watchlist, prov):
    """Models sometimes omit names (especially ETFs) from the stocks array,
    which silently dropped whole cards from the report. Backfill any missing
    watchlist name with a heuristic card so every holding always renders."""
    have = {(s.get("ticker") or "").strip().upper() for s in (data.get("stocks") or [])}
    missing = [s for s in watchlist.get("stocks", [])
               if s.get("ticker", "").strip() and s["ticker"].strip().upper() not in have]
    if not missing:
        return
    print(f"[analyzer] {prov} omitted {', '.join(s['ticker'] for s in missing)} — "
          f"filling with heuristic card(s)")
    heur = _heuristic(items, funds, extras, {"stocks": missing, "topics": []})
    data.setdefault("stocks", []).extend(heur["stocks"])


# --------------------------------------------------------------------------- #
#  Entry point with fallback chain + status
# --------------------------------------------------------------------------- #
def analyze(items, funds, extras, config):
    watchlist = config.get("watchlist", {})
    acfg = config.get("analysis", {})
    tickers = [s.get("ticker") for s in watchlist.get("stocks", [])]
    topics = watchlist.get("topics", [])
    weekly = bool(extras.get("weekly"))

    status = {"engine": "heuristic", "ok": False, "reason": "", "attempts": []}

    if acfg.get("enabled", True):
        chain = [acfg.get("provider", "gemini")] + list(acfg.get("fallbacks", []))
        seen = set()
        ctx = _build_context(items, funds, extras, watchlist)
        user = _instructions(tickers, topics, weekly, config.get("shariah_only", False)) + "\n\nWATCHLIST DATA:\n" + ctx
        for prov in chain:
            prov = (prov or "").lower()
            if not prov or prov in seen:
                continue
            seen.add(prov)
            model = acfg.get("model") if prov == acfg.get("provider", "gemini") else None
            if not providers.available(prov):
                status["attempts"].append(f"{prov}: no API key")
                continue
            try:
                raw = providers.complete(prov, SYSTEM, user, model)
                data = providers.normalize_text_fields(providers.parse_json(raw))
                _fill_missing_stocks(data, items, funds, extras, watchlist, prov)
                mdl = model or providers.DEFAULT_MODELS.get(prov)
                status.update(engine=f"{prov} ({mdl})", ok=True,
                              reason="", attempts=status["attempts"] + [f"{prov}: ok"])
                data["_status"] = status
                return data, status
            except Exception as e:  # noqa: BLE001
                status["attempts"].append(f"{prov}: failed ({e})")
                print(f"[analyzer] {prov} failed ({e}); trying next.")
        status["reason"] = "; ".join(status["attempts"]) or "no providers available"
    else:
        status["reason"] = "analysis disabled in config"

    data = _heuristic(items, funds, extras, watchlist)
    data["_status"] = status
    return data, status


def run_debates(analysis, watch_analysis, funds, extras, items, config):
    """Attach a bull/bear/judge debate to selected names (opening-research verdicts).
    Scope from analysis.debate_scope: 'watch' (default) | 'holdings' | 'all'.
    Bounded by analysis.debate_max (default 8) to stay inside free tiers."""
    acfg = config.get("analysis", {}) or {}
    if not acfg.get("debate"):
        return
    import debate as _debate
    if _debate._pick_roles(config) is None:
        print("[debate] no AI provider available — skipping")
        return
    scope = acfg.get("debate_scope", "watch")
    cap = int(acfg.get("debate_max", 8))
    crowd = extras.get("crowd", {})
    techs = extras.get("technicals", {})
    by_tk = defaultdict(list)
    for it in items:
        by_tk[it.group].append(it)

    targets = []
    if scope in ("holdings", "all"):
        for s in analysis.get("stocks", []):
            targets.append((s.get("ticker"), s.get("ticker"), s))
    if scope in ("watch", "all"):
        for s in (watch_analysis or {}).get("stocks", []):
            targets.append((s.get("ticker"), s.get("ticker"), s))
    targets = [t for t in targets if t[0]][:cap]
    if not targets:
        return
    print(f"[debate] running debate/verdict on {len(targets)} name(s) (scope={scope})…")
    import time
    for i, (tk, name, sdict) in enumerate(targets):
        if i:
            time.sleep(3)  # space the call bursts out — free tiers are per-minute
        ctx = _debate.build_context(tk, name, funds.get(tk), techs.get(tk),
                                    crowd.get(tk), by_tk.get(tk, []), prior=sdict)
        fund = funds.get(tk)
        is_etf = bool(fund and getattr(fund, "is_etf", False))
        # ETFs/funds skip the 3-role bull/bear/judge debate — a single direct
        # verdict call is a better fit (a fund's news rarely supports a real
        # adversarial case) and it saves 2 of every 3 AI calls for these names.
        res = _debate.run_single(tk, name, ctx, config) if is_etf else _debate.run(tk, name, ctx, config)
        if res:
            sdict["debate"] = res
