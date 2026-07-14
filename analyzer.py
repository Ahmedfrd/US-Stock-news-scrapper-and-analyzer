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
            # News on major underlying holdings
            hn = (extras.get("etf_holding_news") or {}).get(tk, {})
            for sym, arts in hn.items():
                if arts:
                    out.append(f"  news on holding {sym}:")
                    for a in arts[:4]:
                        out.append(f"    - {a.title} [{a.source}]")
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
            snip = (it.summary[:180] + "…") if len(it.summary) > 180 else it.summary
            out.append(f"    - {it.title} [{it.source}] {snip}")

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
    "with a rules-based signal), and today's headlines. Do these well: (1) judge "
    "the IMPACT of news on the company using its financials; (2) give a TECHNICAL "
    "read (buy/hold/sell) that is YOUR interpretation of the indicators — you may "
    "override the rule-based signal with reasoning; (3) give a COMBINED call that "
    "reconciles your technical read + fundamentals + news catalysts, explaining "
    "agreement or conflict; (4) from ALL the day's headlines "
    "across the watchlist, macro and topics, identify the SECTORS affected, the effect, "
    "and a bullish/bearish call with reasons — plus specific STOCKS TO WATCH (may be "
    "outside the portfolio) with a one-line why. For ETFs analyse at the fund level. "
    "Use ONLY the data provided; never invent numbers. Be SPECIFIC and QUANTITATIVE — "
    "cite concrete figures (price %, index/commodity levels from the market flags, deal "
    "sizes, targets) in the macro, sector and industry sections. FORMAT: write every "
    "multi-sentence free-text field as newline-separated bullet lines, each starting "
    "with '- ' and stating ONE point with its figures — never a dense paragraph. "
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
  "market_overview": "3-5 bullet lines (each starting '- ', one concrete fact with figures) tying today's news to the macro backdrop",
  "priority": [{{"ticker": "AAPL", "why": "why this matters most today"}}],
  "stocks": [
    {{"ticker": "AAPL",
      "impact": "high|medium|low",
      "sentiment": "bullish|bearish|neutral|mixed",
      "summary": "2-3 bullet lines (each starting '- '): what happened AND the implication given fundamentals/valuation",
      "news_impact": "the concrete effect of today's news on THIS company's financials/outlook/competitive position — cite the specific headline and the specific financial consequence; empty string if no material news",
      "fundamental_read": "1 sentence on financial standing (for an ETF: what the fund holds / its tilt)",
      "divergence": "mismatch between news, price move, fundamentals — or empty string",
      "crowd_note": "1 sentence on what retail/Reddit sentiment says vs the fundamentals — or empty string",
      "bull": "one-line bull case",
      "bear": "one-line bear case",
      "earnings": {{"result": "beat/miss/inline with the numbers, or empty",
                    "outlook": "guidance/outlook from filing, or empty",
                    "management_review": "management's commentary from the filing, or empty"}},
      "etf": {{"move_explainer": "for a FUND only: what drove today's move — attribute to specific holdings and sectors using the contribution figures, plus any macro driver; else empty",
               "nav_read": "premium/discount to NAV and what it implies; else empty",
               "vs_market": "how it's performing vs the benchmark across horizons; else empty",
               "vs_peers": "how it compares to the peer ETFs (returns/expense/yield/size); else empty",
               "risks": "key risks: concentration, volatility, sector/rate sensitivity, beta; else empty",
               "holdings_news_impact": "news on major underlying holdings and its effect on those companies AND on the fund; else empty"}},
      "technical_read": {{"call": "buy|accumulate|hold|reduce|sell",
               "rationale": "YOUR interpretation of the technicals ALONE (RSI/MACD/moving averages/ATR/volume/support-resistance). You may agree or disagree with the rule-based signal provided — if you override it, say why (e.g. overbought RSI inside a strong uptrend is momentum, not a sell)"}},
      "combined_call": {{"call": "buy|accumulate|hold|reduce|sell",
               "rationale": "reconcile the TECHNICAL read, the fundamentals, and the news catalysts — say where they agree or conflict and why the call lands where it does"}},
      "key_drivers": ["short phrase"]}}
  ],
  "sector_highlights": [
    {{"sector": "Oil & Gas Exploration", "call": "bullish|bearish|neutral",
      "points": ["3-5 DETAILED bullets, each a full sentence with CONCRETE FIGURES where available (price moves %, index/commodity levels from the market flags, deal sizes, analyst targets, guidance numbers) — not vague one-liners"]}}
  ],
  "stocks_to_watch": [
    {{"ticker": "NVDA", "call": "bullish|bearish|neutral",
      "reason": "one line: the catalyst and why it matters (may be outside the portfolio)"}}
  ],
  // stocks_to_watch MUST be US-listed tickers only (NYSE/NASDAQ symbols). NEVER use
  // foreign-exchange suffixes (.KS, .T, .AS, .DE, .PA, .L, .TO, .HK). If the catalyst
  // concerns a foreign company, include it ONLY via its US-listed ADR (e.g. TSM, ASML),
  // otherwise leave it out.{shariah_line}
  "crypto_highlight": {{"call": "bullish|bearish|neutral",
      "points": ["2-4 DETAILED bullets on the crypto market with figures (BTC/ETH levels & % moves from the data, ETF flows, dominance, catalysts)"],
      "coins": [{{"symbol": "BTC", "call": "buy|accumulate|hold|reduce|sell",
                  "rationale": "COMBINED view for THIS coin: reconcile its price action + technicals with crypto news and market context, 1-2 sentences with figures. Cover EVERY coin present in the CRYPTO data, not just one or two."}}]}},
  "topics": [{{"topic": "semiconductor industry", "sentiment": "...",
      "summary": "3-5 DETAILED bullet lines (each starting '- ') on the industry/market implication, citing specific figures (company moves %, revenue/deal numbers, analyst targets) from the data",
      "key_companies": [{{"name": "TSMC", "note": "1-2 sentences: why this (non-portfolio) company's news matters, with figures if available"}}]}}],
  "macro": {{"summary": "4-6 DETAILED bullet lines (each starting '- ') on the macro backdrop, explicitly citing the MARKET FLAGS figures (Brent/WTI oil, gold, dollar/DXY, S&P 500 & Nasdaq levels and % moves, 10Y yield) and any rates/inflation/jobs news",
      "points": ["3-5 bullets, each with a concrete figure or level"],
      "watch": ["upcoming catalyst or data release with date if known"]}}{weekly_block}
}}

Stocks: {', '.join(tickers)}
Topics: {', '.join(topics)}
Fill "earnings" only when earnings/filing data is present; otherwise use empty strings.
Only include stocks/topics/sectors that have data. Keep it tight."""


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
                    "risks": "", "holdings_news_impact": ""},
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
              "macro": {"summary": "See macro headlines below.", "watch": []},
              "sector_highlights": [],
              "crypto_highlight": _heur_crypto(extras.get("crypto") or {}),
              "stocks_to_watch": [{"ticker": s["ticker"], "call": s["sentiment"],
                                   "reason": f"technical {s['combined_call']['call']}, {s['impact']} news impact"}
                                  for s in stocks
                                  if s["combined_call"]["call"] in ("buy", "accumulate", "reduce", "sell")][:6]}
    if extras.get("weekly"):
        result["sectors"] = [{"sector": sec, "summary": f"{len(arts)} developments this week.",
                              "developments": [a.title for a in arts[:4]], "read_across": ""}
                             for sec, arts in extras.get("sector_news", {}).items()]
    return result


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
    print(f"[debate] running bull/bear/judge on {len(targets)} name(s) (scope={scope})…")
    import time
    for i, (tk, name, sdict) in enumerate(targets):
        if i:
            time.sleep(3)  # space the 3-call bursts out — free tiers are per-minute
        ctx = _debate.build_context(tk, name, funds.get(tk), techs.get(tk),
                                    crowd.get(tk), by_tk.get(tk, []), prior=sdict)
        res = _debate.run(tk, name, ctx, config)
        if res:
            sdict["debate"] = res
