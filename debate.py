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

# Default role→provider assignment (overridable via analysis.debate_providers).
_DEFAULT_ROLES = {"bull": "groq", "bear": "openrouter", "judge": "gemini"}

_BULL_SYS = (
    "You are a BULL-side equity researcher. Build the STRONGEST evidence-based "
    "case FOR this security using only the data provided (fundamentals, technicals, "
    "news, crowd sentiment). Cite concrete figures. 4-6 tight bullet points. Be "
    "persuasive but honest — no invented numbers. This is advocacy, not a balanced view."
)
_BEAR_SYS = (
    "You are a BEAR-side equity researcher. Build the STRONGEST evidence-based case "
    "AGAINST this security (or for caution) using only the data provided. Surface "
    "risks, weak fundamentals, technical warnings, negative catalysts, valuation and "
    "sentiment concerns. Cite concrete figures. 4-6 tight bullet points. No invented numbers."
)
_JUDGE_SYS = (
    "You are the JUDGE — a senior PM reading a bull analyst and a bear analyst plus "
    "the underlying data. Weigh both sides and issue a decision for an OPENING-RESEARCH "
    "brief (a starting direction, not advice). Reward the better-evidenced argument; "
    "penalise hand-waving. Respond ONLY with JSON, no prose:\n"
    "{\"call\":\"buy|accumulate|hold|reduce|sell|avoid\","
    "\"conviction\":\"low|medium|high\","
    "\"verdict\":\"2-3 sentences: which side won and why, citing figures\","
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
        sc = getattr(fund, "scores", {}) or {}
        L.append("FUNDAMENTALS: " + ", ".join(filter(None, [
            f"price {fund.price}" if fund.price is not None else "",
            f"1d {fund.change_1d:+.2f}%" if getattr(fund, "change_1d", None) is not None else "",
            f"P/E {fund.pe}" if getattr(fund, "pe", None) else "",
            f"P/S {getattr(fund,'ps',None)}" if getattr(fund, "ps", None) else "",
            f"rev growth {getattr(fund,'revenue_growth',None)}%" if getattr(fund, "revenue_growth", None) is not None else "",
            f"net margin {getattr(fund,'net_margin',None)}%" if getattr(fund, "net_margin", None) is not None else "",
            f"D/E {getattr(fund,'debt_to_equity',None)}" if getattr(fund, "debt_to_equity", None) is not None else "",
            f"mktcap {getattr(fund,'market_cap',None)}" if getattr(fund, "market_cap", None) else "",
        ])))
        if sc:
            L.append("FACTOR SCORES (0-100): " + ", ".join(
                f"{k} {v}" for k, v in sc.items() if v is not None))
        if getattr(fund, "target_mean", None):
            L.append(f"ANALYST TARGET: {fund.target_mean} (vs price {fund.price})")
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
        L.append("RECENT NEWS:")
        for it in news_items[:6]:
            summ = (getattr(it, "summary", "") or "")[:240]
            L.append(f"  - {it.title} [{it.source}] {summ}")
    if prior:
        if prior.get("news_impact"):
            L.append(f"PRIOR ANALYST NOTE (impact): {prior['news_impact']}")
    return "\n".join(L)


def _fallback_chain(primary):
    """On failure, back up to Gemini only. Its free tier (~1,500 req/day) is far
    more generous than Groq/OpenRouter's, and OpenRouter's unfunded free tier
    caps at just 50 req/day — cascading through every other provider on every
    failure burns retries and time on options just as likely to be rate-limited."""
    if primary == "gemini" or not providers.available("gemini"):
        return [primary]
    return [primary, "gemini"]


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
    return {"bull": bull, "bear": bear, "verdict": verdict, "roles": roles}
