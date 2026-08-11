"""
debate.py — three-layer "researcher debate" for a single name.

Inspired by the TradingAgents framework (Xiao et al., UCLA/MIT): rather than
asking one model to weigh both sides, two models are forced to advocate opposite
positions and a third synthesises a judgement. We run it single-round to stay
inside free tiers, and spread the three roles across providers for both quota
and genuine model diversity (uncorrelated reasoning):

    BULL  → Groq         (strongest evidence-based bull case)
    BEAR  → OpenRouter   (strongest evidence-based bear case)
    JUDGE → Gemini       (reads both + the data, issues the call)

Every role reads the SAME evidence: fundamentals, technicals, news, and crowd
sentiment. Providers are chosen from whatever keys exist; if only one provider
is available all three roles use it (still useful — different prompts). Any role
that errors degrades gracefully, and the whole thing is a no-op if debate is off
or no AI key is present.
"""

from __future__ import annotations

import providers
import analyzer as _analyzer

# Default role→provider assignment (overridable via analysis.debate_providers).
_DEFAULT_ROLES = {"bull": "groq", "bear": "openrouter", "judge": "gemini"}

_BULL_SYS = (
    "You are a BULL-side equity researcher. Build the STRONGEST evidence-based "
    "case FOR this security using only the data provided (fundamentals, technicals, "
    "news, crowd sentiment). Cite concrete figures and cross-check facts and figures. 4-6 tight bullet points. Be "
    "persuasive but honest — no invented numbers. This is advocacy, not a balanced view."
)
_BEAR_SYS = (
    "You are a BEAR-side equity researcher. Build the STRONGEST evidence-based case "
    "AGAINST this security (or for caution) using only the data provided. Surface "
    "risks, weak fundamentals, technical warnings, negative catalysts, valuation and "
    "sentiment concerns. Cite concrete figures. 4-6 tight bullet points. No invented numbers."
)
_JUDGE_SYS = (
    "You are the JUDGE — a senior portfolio manager. You have just read a BULL analyst "
    "and a BEAR analyst, plus the underlying evidence. Do NOT score the debate or pick a "
    "winner ('the bear cited better numbers, so bear wins'). Instead, READ both cases, "
    "then form YOUR OWN independent thesis on this security: take the strongest valid "
    "points from each side, add the fundamentals, earnings and news catalysts, and reason "
    "through anything neither analyst raised. You may reach a conclusion that neither the "
    "bull nor the bear argued — that is welcome. "
    "Base your call ONLY on CATALYSTS, FUNDAMENTALS, EARNINGS and NEWS — the real drivers "
    "of the business and the stock's story but focus more on Catalysts. IGNORE all technical indicators (RSI, MACD, "
    "moving averages, price-vs-SMA, support/resistance, chart 'signals'); if either "
    "analyst leaned on technicals, disregard that part of their case. A move's technicals "
    "are noise here — the WHY behind the fundamentals and news is what matters. "
    "Your verdict must read as your OWN reasoned view of the name (its catalysts and "
    "fundamentals), NOT as 'which analyst won'. Respond ONLY with JSON, no prose:\n"
    "{\"call\":\"buy|accumulate|hold|reduce|sell|avoid\","
    "\"conviction\":\"low|medium|high\","
    "\"verdict\":\"2-3 sentences in YOUR voice: your thesis on the name, grounded in the "
    "catalysts/news/fundamentals that drive it — not a summary of who won the debate\","
    "\"key_risks\":[\"the 1-3 risks that matter most — fundamental/news-driven, not technical\"],"
    "\"what_would_change_it\":\"one line: the catalyst/event/fundamental datapoint that would flip your call\","
    "\"start_here\":\"one line: what the reader should investigate first\"}"
)
_SINGLE_SYS = (
    "You are a senior fund analyst issuing an RESEARCH read on a fund/ETF "
    ", using only the data provided (holdings, "
    "flows, technicals, crowd sentiment, news). This is a direct assessment, not a "
    "debate — weigh the evidence yourself and give a balanced, evidence-based call. "
    "Cite concrete figures. Respond ONLY with JSON, no prose:\n"
    "{\"call\":\"buy|accumulate|hold|reduce|sell|avoid\","
    "\"conviction\":\"low|medium|high\","
    "\"verdict\":\"2-3 sentences: your read and why, citing figures\","
    "\"key_risks\":[\"the 1-3 risks that matter most\"],"
    "\"what_would_change_it\":\"one line: the datapoint/event that would flip the call\","
    "\"start_here\":\"one line: what the reader should investigate first\"}"
)


def _pick_roles(config: dict) -> dict | None:
    explicit = (config.get("analysis") or {}).get("debate_providers")
    avail = [p for p in ["groq", "openrouter", "gemini"] if providers.available(p)]
    if explicit:  # user-specified, but keep only those with keys
        picked = {r: explicit.get(r) for r in ("bull", "bear", "judge")}
        picked = {r: p for r, p in picked.items() if p and providers.available(p)}
        if len(picked) == 3:
            return picked
    if not avail:
        return None
    # distinct providers where possible, else reuse what's available
    bull = "groq" if "groq" in avail else avail[0]
    bear = "openrouter" if "openrouter" in avail else (avail[1] if len(avail) > 1 else avail[0])
    judge = "gemini" if "gemini" in avail else avail[-1]
    return {"bull": bull, "bear": bear, "judge": judge}


def build_context(ticker, name, fund, tech, crowd_entry, news_items, prior=None) -> str:
    """Compact per-name evidence block shared by all three roles."""
    L = [f"SECURITY: {ticker} ({name})"]
    if fund and not getattr(fund, "error", None):
        # Reuse the EXACT same formatter as the main analysis context (analyzer._fund_block)
        # so bull/bear/judge see byte-identical figures to what the primary analysis used —
        # a separate hand-rolled formatter here previously mismatched units (e.g. net margin
        # shown as a raw fraction + "%" instead of ×100), causing bull/bear/judge to argue
        # over "misstated" numbers that were actually a formatting bug, not a real dispute.
        L.append("FUNDAMENTALS:\n" + _analyzer._fund_block(fund))
        if getattr(fund, "market_cap", None):
            L.append(f"MARKET CAP: {_analyzer._money(fund.market_cap)}")
    if tech and not getattr(tech, "error", None):
        L.append("TECHNICALS: " + ", ".join(filter(None, [
            f"RSI {tech.rsi}" if tech.rsi is not None else "",
            f"MACD hist {tech.macd_hist}" if tech.macd_hist is not None else "",
            f"trend {tech.trend}" if getattr(tech, "trend", None) else "",
            f"vs SMA50 {tech.sma50}" if getattr(tech, "sma50", None) else "",
            f"vs SMA200 {tech.sma200}" if getattr(tech, "sma200", None) else "",
            f"support {tech.support}" if getattr(tech, "support", None) else "",
            f"resistance {tech.resistance}" if getattr(tech, "resistance", None) else "",
            f"rule-signal {tech.signal}" if getattr(tech, "signal", None) else "",
        ])))
    if crowd_entry and crowd_entry.get("has_data"):
        con = crowd_entry.get("consensus", {})
        L.append(f"CROWD SENTIMENT (Adanos consensus): {con.get('label')} "
                 f"({con.get('bullish')}% bull / {con.get('bearish')}% bear, buzz {con.get('buzz')})")
    if news_items:
        L.append("RECENT NEWS (full article content where available — read it for the "
                 "actual reason behind any move, not just the headline):")
        for it in news_items[:6]:
            summ = (getattr(it, "summary", "") or "")[:1400]
            L.append(f"  - {it.title} [{it.source}]\n    {summ}")
    if prior:
        if prior.get("news_impact"):
            L.append(f"PRIOR ANALYST NOTE (impact): {prior['news_impact']}")
    return "\n".join(L)


def _fallback_chain(primary):
    """Try the assigned provider first, then EVERY other available provider as a
    backup, so a rate-limited or failed call still gets answered instead of
    dropping to the canned 'unavailable' text.

    Ordering is deliberate: after the primary we try groq → openrouter → gemini,
    which keeps Gemini LAST for the bull/bear advocacy roles. Groq and OpenRouter
    are the priority engines for the debate; Gemini's tight free-tier quota is
    reserved for its own role (the judge) and used only as a last resort for the
    others. The judge (primary=gemini) still gets groq/openrouter behind it."""
    order = [primary, "groq", "openrouter", "gemini"]
    chain = []
    for p in order:
        if p and p not in chain and providers.available(p):
            chain.append(p)
    return chain or [primary]


def _call(role, provider, sysmsg, ctx, task, ticker=""):
    """Run one advocacy role. Tries the assigned provider, then any other
    available provider; logs the real error to the run log and never leaks a
    raw error string into the report."""
    for prov in _fallback_chain(provider):
        try:
            # Prose output — json_mode must stay off: Groq returns HTTP 400 on
            # JSON mode when the prompt doesn't ask for JSON, which silently
            # killed every bull/bear call.
            return providers.complete(prov, sysmsg, f"{ctx}\n\nTASK: {task}",
                                      json_mode=False).strip()
        except Exception as e:  # noqa: BLE001
            print(f"[debate] {role} for {ticker} via {prov} failed: {e}", flush=True)
    return (f"({role} case unavailable this run — all AI providers failed or were "
            f"rate-limited; details in the run log)")


def run(ticker, name, ctx, config) -> dict | None:
    roles = _pick_roles(config)
    if not roles:
        return None
    bull = _call("bull", roles["bull"], _BULL_SYS, ctx,
                 f"Make the bull case for {ticker}.", ticker)
    bear = _call("bear", roles["bear"], _BEAR_SYS, ctx,
                 f"Make the bear case for {ticker}.", ticker)
    judge_ctx = (f"{ctx}\n\n=== BULL ANALYST ===\n{bull}\n\n=== BEAR ANALYST ===\n{bear}")
    verdict = {}
    for prov in _fallback_chain(roles["judge"]):
        try:
            raw = providers.complete(prov, _JUDGE_SYS, judge_ctx +
                                     f"\n\nTASK: Judge {ticker} and return the JSON.")
            verdict = providers.normalize_text_fields(providers.parse_json(raw))
            break
        except Exception as e:  # noqa: BLE001
            print(f"[debate] judge for {ticker} via {prov} failed: {e}", flush=True)
    if not verdict.get("call"):
        verdict = {"call": "hold", "conviction": "low",
                   "verdict": "Judge unavailable this run (provider errors — see the run "
                              "log); read the bull/bear cases below.",
                   "key_risks": [], "what_would_change_it": "", "start_here": ""}
    return {"bull": bull, "bear": bear, "verdict": verdict, "roles": roles, "mode": "debate"}


def run_single(ticker, name, ctx, config) -> dict | None:
    """ETFs/funds get one direct verdict call instead of the 3-role bull/bear/judge
    debate — a fund's news rarely supports a real adversarial case, and skipping
    two of the three calls saves meaningful free-tier budget for the stocks that
    do warrant a debate. Still falls back through every available provider."""
    roles = _pick_roles(config)
    if not roles:
        return None
    primary = roles["judge"]
    verdict, used = {}, primary
    for prov in _fallback_chain(primary):
        try:
            raw = providers.complete(prov, _SINGLE_SYS,
                                     f"{ctx}\n\nTASK: Assess {ticker} and return the JSON.")
            verdict = providers.normalize_text_fields(providers.parse_json(raw))
            used = prov
            break
        except Exception as e:  # noqa: BLE001
            print(f"[debate] single-call verdict for {ticker} via {prov} failed: {e}", flush=True)
    if not verdict.get("call"):
        verdict = {"call": "hold", "conviction": "low",
                   "verdict": "Verdict unavailable this run (all AI providers failed or "
                              "were rate-limited; details in the run log).",
                   "key_risks": [], "what_would_change_it": "", "start_here": ""}
    return {"bull": "", "bear": "", "verdict": verdict,
            "roles": {"judge": used}, "mode": "single"}
