"""
debate.py — bull / bear / judge, kept SHORT: pointers, not essays.

Inspired by the TradingAgents framework (Xiao et al.): two models argue opposite
sides and a third forms its own view. Single round, roles spread across free
providers (bull → Groq, bear → OpenRouter, judge → Gemini), each with a fallback
through every other available provider.

Two flavours:
  * HOLDINGS (weekly Portfolio Digest): every role reads the WEEK — this week's
    news, the day-by-day price progression, results — plus last week's verdict
    from state, so the cases move with the stock instead of repeating the same
    long-term boilerplate every week.
  * STOCKS TO WATCH (daily Market Digest): the judge must also write a Catalyst
    Thesis whenever it recommends buying or selling; the figures in it are
    computed by thesis.py, never by the model.
"""

from __future__ import annotations

import re

import providers
import analyzer as _analyzer
import thesis as thesis_mod

_DEFAULT_ROLES = {"bull": "groq", "bear": "openrouter", "judge": "gemini"}

_POINTER_RULES = (
    "Write EXACTLY 3 bullet lines, each starting with '- ', each at most 22 words, each "
    "making ONE point anchored in a concrete fact or figure from the data (this period's "
    "news, the price progression, results, a valuation number). No preamble, no headings, "
    "no conclusion line. Never write a point that would be equally true of any stock "
    "('strong brand', 'market volatility', 'macro uncertainty'). Use only the data given — "
    "never invent numbers.")

_BULL_SYS = ("You are the BULL analyst. Make the strongest evidence-based case FOR this "
             "security. " + _POINTER_RULES)
_BEAR_SYS = ("You are the BEAR analyst. Make the strongest evidence-based case AGAINST this "
             "security (or for caution). " + _POINTER_RULES)

_JUDGE_CORE = (
    "You are the JUDGE, a senior portfolio manager. Read the bull and bear pointers and the "
    "evidence, then form YOUR OWN view — do not score the debate. Weigh catalysts, news, "
    "results and fundamentals first; use the price progression as evidence of how the "
    "market is receiving the story, but never base the call on indicator signals "
    "(RSI/MACD/moving-average crossovers). Keep it short: pointers, not paragraphs. "
    "Respond ONLY with JSON, no prose.")

_JUDGE_HOLDING = _JUDGE_CORE + (
    " This is the WEEKLY review of a name the reader OWNS. Your pointers must be about what "
    "happened THIS WEEK and where the stock stands now — if last week's verdict is given, "
    "say plainly what changed (or that nothing material did).\n"
    '{"call":"buy|accumulate|hold|reduce|sell",'
    '"conviction":"low|medium|high",'
    '"pointers":["2-3 bullets, max 22 words each: why, anchored in this week"],'
    '"changed_since_last_week":"one line, max 25 words (\'first weekly review\' if no prior verdict)",'
    '"key_risk":"one line, max 20 words",'
    '"what_would_change_it":"one line, max 20 words: the specific event/datapoint"}')

_JUDGE_PICK = _JUDGE_CORE + (
    " This is a STOCK TO WATCH the reader does not own, flagged because of a specific "
    "catalyst. If your call is buy, accumulate, sell, reduce or avoid you MUST include a "
    "complete catalyst thesis; if the evidence cannot support one, the call must be 'hold'. "
    "Conviction may not be 'high' (one session of news on a new name). Use the COMPUTED "
    "FIGURES as given; never introduce other price levels or targets.\n"
    '{"call":"buy|accumulate|hold|reduce|sell|avoid",'
    '"conviction":"low|medium",'
    '"pointers":["2-3 bullets, max 22 words each"],'
    '"key_risk":"one line, max 20 words",'
    '"what_would_change_it":"one line, max 20 words",'
    '"thesis":{"catalyst":"the specific event (and its date if scheduled), max 30 words",'
    '"mechanism":"how it reaches revenue/earnings or the share price, max 35 words",'
    '"priced_in":"what the price already reflects, reasoned from the computed figures, max 30 words",'
    '"horizon":"days|weeks|quarter",'
    '"invalidation":"the concrete observable that proves it wrong (may cite the computed support/resistance), max 30 words",'
    '"before_acting":"what to verify first, max 25 words"}}')

_SINGLE_SYS = (
    "You are a senior fund analyst giving the WEEKLY read on a fund/ETF the reader owns, from "
    "the data provided (holdings and their news, flows, returns, the week's price "
    "progression). Short pointers, not paragraphs; anchor them in THIS WEEK. Respond ONLY "
    "with JSON:\n"
    '{"call":"buy|accumulate|hold|reduce|sell",'
    '"conviction":"low|medium|high",'
    '"pointers":["2-3 bullets, max 22 words each"],'
    '"changed_since_last_week":"one line, max 25 words (\'first weekly review\' if no prior verdict)",'
    '"key_risk":"one line, max 20 words",'
    '"what_would_change_it":"one line, max 20 words"}')


def _pick_roles(config: dict) -> dict | None:
    avail = [p for p in ("groq", "openrouter", "gemini") if providers.available(p)]
    if not avail:
        return None
    explicit = (config.get("analysis") or {}).get("debate_providers") or {}
    roles = {}
    for r in ("bull", "bear", "judge"):
        pref = (explicit.get(r) or _DEFAULT_ROLES[r]).lower()
        roles[r] = pref if providers.available(pref) else avail[0]
    return roles


def build_context(ticker, name, fund, tech, crowd_entry, news_items, prog_text="",
                  prior=None, crowd_label="Adanos") -> str:
    """Evidence block shared by all three roles."""
    L = [f"SECURITY: {ticker} ({name})"]
    if fund and not getattr(fund, "error", None):
        L.append("FUNDAMENTALS:\n" + _analyzer._fund_block(fund))
    if prog_text:
        L.append(prog_text)
    if tech and not getattr(tech, "error", None):
        L.append("TECHNICALS (context only): " + ", ".join(filter(None, [
            f"RSI {tech.rsi}" if tech.rsi is not None else "",
            f"trend {tech.trend}" if tech.trend else "",
            f"SMA50 {tech.sma50}" if tech.sma50 else "",
            f"SMA200 {tech.sma200}" if tech.sma200 else "",
            f"support {tech.support}" if tech.support else "",
            f"resistance {tech.resistance}" if tech.resistance else ""])))
    if crowd_entry and crowd_entry.get("has_data"):
        con = crowd_entry.get("consensus", {})
        L.append(f"CROWD ({crowd_label}): {con.get('label')} ({con.get('bullish')}% bull / "
                 f"{con.get('bearish')}% bear)")
    if news_items:
        L.append("NEWS (article text where available — use the substance, not the headline):")
        for it in news_items[:6]:
            summ = (getattr(it, "summary", "") or "")[:1200]
            when = it.published.strftime("%a %d %b") if getattr(it, "published", None) else ""
            L.append(f"  - [{when}] {it.title} [{it.source}]\n    {summ}")
    else:
        L.append("NEWS: no company-specific articles in this period.")
    if prior:
        L.append(f"LAST WEEK'S VERDICT ({prior.get('date')}): {str(prior.get('call', '')).upper()} "
                 f"({prior.get('conviction', '')} conviction) at {prior.get('price')}; pointers then: "
                 + " | ".join(prior.get("pointers") or []))
    return "\n".join(L)


def _pointers(text, n=3) -> list[str]:
    """Model prose → at most n clean one-line pointers."""
    if isinstance(text, list):
        lines = [str(x) for x in text]
    else:
        lines = _analyzer.split_bullets(text)
    out = []
    for ln in lines:
        ln = re.sub(r"^\s*(?:[-*•–·]|\d+[.)])\s*", "", ln).strip().strip('"')
        ln = re.sub(r"\*\*(.+?)\*\*", r"\1", ln)
        if len(ln) < 8 or ln.endswith(":") or ln.lower().startswith(("bull case", "bear case", "here are")):
            continue
        out.append(ln[:240])
        if len(out) >= n:
            break
    return out


def _call_role(role, provider, sysmsg, ctx, task, ticker):
    try:
        text, used = providers.complete_chain([provider], sysmsg, f"{ctx}\n\nTASK: {task}",
                                              json_mode=False)
        return _pointers(text), used
    except Exception as e:  # noqa: BLE001
        print(f"[debate] {role} for {ticker} failed on every provider: {str(e)[:200]}", flush=True)
        return [], None


def _judge(sysmsg, ctx, task, primary, ticker):
    try:
        raw, used = providers.complete_chain([primary], sysmsg, f"{ctx}\n\nTASK: {task}")
        v = providers.normalize_text_fields(providers.parse_json(raw))
        v["pointers"] = _pointers(v.get("pointers"), 3)
        return v, used
    except Exception as e:  # noqa: BLE001
        print(f"[debate] judge for {ticker} failed on every provider: {str(e)[:200]}", flush=True)
        return {}, None


def run_holding(ticker, name, ctx, config, is_fund=False) -> dict | None:
    roles = _pick_roles(config)
    if not roles:
        return None
    if is_fund:
        v, used = _judge(_SINGLE_SYS, ctx, f"Give the weekly read on {ticker} as JSON.",
                         roles["judge"], ticker)
        return {"bull": [], "bear": [], "verdict": v, "roles": {"judge": used}, "mode": "single"} \
            if v.get("call") else None
    bull, bu = _call_role("bull", roles["bull"], _BULL_SYS, ctx,
                          f"Bull pointers for {ticker}, based on this week.", ticker)
    bear, be = _call_role("bear", roles["bear"], _BEAR_SYS, ctx,
                          f"Bear pointers for {ticker}, based on this week.", ticker)
    jctx = (f"{ctx}\n\n=== BULL ===\n" + "\n".join(f"- {b}" for b in bull)
            + "\n\n=== BEAR ===\n" + "\n".join(f"- {b}" for b in bear))
    v, ju = _judge(_JUDGE_HOLDING, jctx, f"Weekly verdict on {ticker} as JSON.", roles["judge"], ticker)
    if not (v.get("call") or bull or bear):
        return None
    return {"bull": bull, "bear": bear, "verdict": v, "mode": "debate",
            "roles": {"bull": bu, "bear": be, "judge": ju}}


def run_pick(ticker, name, ctx, computed: dict, config) -> dict | None:
    roles = _pick_roles(config)
    if not roles:
        return None
    ctx = ctx + "\n" + thesis_mod.describe(computed)
    bull, bu = _call_role("bull", roles["bull"], _BULL_SYS, ctx, f"Bull pointers for {ticker}.", ticker)
    bear, be = _call_role("bear", roles["bear"], _BEAR_SYS, ctx, f"Bear pointers for {ticker}.", ticker)
    jctx = (f"{ctx}\n\n=== BULL ===\n" + "\n".join(f"- {b}" for b in bull)
            + "\n\n=== BEAR ===\n" + "\n".join(f"- {b}" for b in bear))
    v, ju = _judge(_JUDGE_PICK, jctx, f"Verdict and (if directional) catalyst thesis for {ticker} as JSON.",
                   roles["judge"], ticker)
    if not v.get("call"):
        return {"bull": bull, "bear": bear, "verdict": {}, "mode": "debate",
                "roles": {"bull": bu, "bear": be, "judge": ju}} if (bull or bear) else None
    # Guard-rails the prompt cannot enforce on its own.
    if (v.get("conviction") or "").lower() == "high":
        v["conviction"] = "medium"
    th = v.get("thesis") if isinstance(v.get("thesis"), dict) else None
    if thesis_mod.is_directional(v.get("call")):
        ok, problems = thesis_mod.validate(th)
        if not ok:
            print(f"[debate] {ticker}: {v.get('call')} withheld — thesis incomplete ({', '.join(problems)})")
            v["withheld_call"] = v.get("call")
            v["call"] = "hold"
            v["thesis"] = None
    else:
        v["thesis"] = None
    return {"bull": bull, "bear": bear, "verdict": v, "mode": "debate",
            "roles": {"bull": bu, "bear": be, "judge": ju}}
