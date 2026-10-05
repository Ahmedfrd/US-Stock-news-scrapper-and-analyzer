"""
analyzer.py — the two AI analyses, one per email, and the debates that hang off
them. Each reports exactly which engine produced it.

  analyze_portfolio()  WEEKLY. Holdings only: this week's company news, how each
                       name travelled over the week, ETF component news. No
                       market/macro/sector commentary — that is the market email's.
  analyze_market()     DAILY. TL;DR, policy & rates, the macro prints released in
                       the window, global news, the main sectors, at most THREE
                       stocks to watch (each with a named catalyst), crypto, risks.
                       Never discusses portfolio holdings.

Engine selection is a fallback CHAIN (configured provider → fallbacks → free
local heuristic). Whatever happens is recorded in a status object so the email
says which AI ran — or that it failed and why.

Hard rule in both: no articles → no news text. The model is told so, and the
code blanks any news field for a name/section that had no articles.
"""

from __future__ import annotations

from collections import defaultdict

import providers


# --------------------------------------------------------------------------- #
#  Formatting helpers
# --------------------------------------------------------------------------- #
def _money(x):
    try:
        x = float(x)
        for u, d in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
            if abs(x) >= d:
                return f"${x/d:.2f}{u}"
        return f"${x:,.0f}"
    except Exception:  # noqa: BLE001
        return "n/a"


def _fmt(x, s="", pct=False, nd=2):
    if x is None:
        return "n/a"
    try:
        return f"{x*100:.1f}%" if pct else f"{x:.{nd}f}{s}"
    except Exception:  # noqa: BLE001
        return str(x)


def _fund_block(f) -> str:
    sc = f.scores or {}
    return "\n".join([
        f"  sector: {f.sector or 'n/a'} / {f.industry or 'n/a'}",
        f"  price {_fmt(f.price)}  1d {_fmt(f.change_1d,'%')}  1m {_fmt(f.ret_1m,'%')}  3m {_fmt(f.ret_3m,'%')}  6m {_fmt(f.ret_6m,'%')}",
        f"  valuation: P/E {_fmt(f.pe)} fwd {_fmt(f.forward_pe)} P/S {_fmt(f.ps)} PEG {_fmt(f.peg)}",
        f"  growth: rev {_fmt(f.rev_growth,pct=True)} earn {_fmt(f.earn_growth,pct=True)}; "
        f"margins: net {_fmt(f.net_margin,pct=True)} gross {_fmt(f.gross_margin,pct=True)}; ROE {_fmt(f.roe,pct=True)}",
        f"  analyst target {_fmt(f.target_mean)} ({_fmt(f.implied_upside,'%')} upside), rating {_fmt(f.rating)}/5",
        f"  factor scores: value {sc.get('value')} growth {sc.get('growth')} profit {sc.get('profitability')} "
        f"momentum {sc.get('momentum')} health {sc.get('health')} COMPOSITE {sc.get('composite')}",
    ])


def _arts(arts, cap, snip):
    out = []
    for it in arts[:cap]:
        body = (it.summary or "").strip()
        body = (body[:snip] + "…") if len(body) > snip else body
        when = it.published.strftime("%a %d %b %H:%M UTC") if it.published else "undated"
        out.append(f"    - [{when}] {it.title} [{it.source}]" + (f"\n      {body}" if body else ""))
    return out


def _flags_line(flags):
    def one(fl):
        if fl.get("bp") is not None:
            return f"{fl['name']} {fl.get('price')}% ({fl['bp']:+d} bp)"
        if fl.get("pct") is not None:
            return f"{fl['name']} {fl.get('price')} ({fl['pct']:+.2f}%)"
        return f"{fl['name']} {fl.get('price')}"
    return " | ".join(one(fl) for fl in (flags or []))


def split_bullets(text: str) -> list[str]:
    """Newline bullets, or — when a model crams them onto one line — split on the
    ' - ' separators ('- A did x - B did y')."""
    import re as _re
    lines = [l for l in str(text or "").splitlines() if l.strip()]
    if len(lines) == 1 and lines[0].lstrip().startswith("- ") and lines[0].count(" - ") >= 1:
        parts = _re.split(r"\s+-\s+(?=[A-Z0-9$(“\"'])", lines[0].strip()[2:])
        if len(parts) > 1:
            return ["- " + p.strip() for p in parts if p.strip()]
    return lines


_FMT_RULE = ("FORMAT: every multi-point text field is newline-separated bullet lines, each "
             "starting with '- ', one point per line, leading with the reason, then the "
             "figures. Short and specific — no filler.")
_WHY_RULE = (
    "WHY, NOT JUST WHAT: for every move you mention, give the SPECIFIC cause from the "
    "article text (the deal and its size, the guidance or EPS figure, the ruling, the data "
    "print, the analyst action and new target, the management quote). Never use filler like "
    "'broader market sentiment', 'profit-taking', 'sector rotation' or 'investors reacted' "
    "unless an article names it. If the articles do not explain a move, say the driver is "
    "unclear. Use ONLY the data provided; never invent numbers or news.")


def _run_chain(system, build_user, config, label):
    """build_user(compact: bool) -> prompt. Providers with a small request cap get
    the COMPACT prompt (shorter snippets spread across every name) instead of a
    full prompt cut off at the tail — a tail cut drops whole holdings."""
    acfg = config.get("analysis", {}) or {}
    status = {"engine": "heuristic", "ok": False, "reason": "", "attempts": []}
    if not acfg.get("enabled", True):
        status["reason"] = "analysis disabled in config"
        return None, status
    chain = [acfg.get("provider", "gemini")] + list(acfg.get("fallbacks", []))
    seen = set()
    for prov in chain:
        prov = (prov or "").lower()
        if not prov or prov in seen:
            continue
        seen.add(prov)
        if not providers.available(prov):
            status["attempts"].append(f"{prov}: no API key")
            continue
        model = acfg.get("model") if prov == acfg.get("provider", "gemini") else None
        user = build_user(False)
        cap = providers.user_cap(prov)
        if cap and len(user) > cap:
            user = build_user(True)
        try:
            raw = providers.complete(prov, system, user, model)
            data = providers.normalize_text_fields(providers.parse_json(raw))
            status.update(engine=f"{prov} ({model or providers.model_in_use(prov)})", ok=True,
                          attempts=status["attempts"] + [f"{prov}: ok"])
            return data, status
        except Exception as e:  # noqa: BLE001
            status["attempts"].append(f"{prov}: failed ({str(e)[:160]})")
            print(f"[analyzer:{label}] {prov} failed ({str(e)[:200]}); trying next.", flush=True)
    status["reason"] = "; ".join(status["attempts"]) or "no providers available"
    return None, status


# =========================================================================== #
#  WEEKLY PORTFOLIO
# =========================================================================== #
PORTFOLIO_SYSTEM = (
    "You are a buy-side analyst writing the reader's WEEKLY PORTFOLIO REVIEW. It covers only "
    "the names the reader owns: what happened to each this week (company news, results, "
    "filings), how each stock travelled over the week (the computed weekly progression is "
    "given — cite it), and for ETFs, the news at each major component company and how it fed "
    "through to the fund. Do NOT write general market, macro or sector commentary — the reader "
    "gets that in a separate daily market email; mention the market only as a one-clause "
    "benchmark comparison. NO ARTICLES → NO NEWS: if a name has no articles in the data, its "
    "summary and news_impact must be empty strings — never invent or pad news. "
    + _WHY_RULE + " " + _FMT_RULE + " Informational analysis, not investment advice.")


def _portfolio_context(items, funds, extras, watchlist, compact=False) -> str:
    a_cap, a_snip, c_cap, c_snip, f_snip = (4, 260, 1, 160, 300) if compact else (10, 1500, 3, 1000, 1800)
    by_group = defaultdict(list)
    for it in items:
        by_group[it.group].append(it)
    crowd, earn, fils = extras.get("crowd", {}), extras.get("earnings", {}), extras.get("filings", {})
    prog_txt = extras.get("progression_text", {})
    out = [f"WEEK COVERED: {extras.get('period_label', 'the past week')}"]
    for s in watchlist.get("stocks", []):
        tk = s.get("ticker", "").strip()
        if not tk:
            continue
        f = funds.get(tk)
        is_etf = bool(f and f.is_etf)
        out.append(f"\n=== {'ETF/FUND' if is_etf else 'STOCK'} {tk} ({f.name if f else tk}) ===")
        if is_etf:
            prof = (extras.get("etf") or {}).get(tk)
            if prof:
                r = prof.returns or {}
                out.append(f"  returns: 1w {r.get('1w')}  1m {r.get('1m')}  3m {r.get('3m')}  YTD {r.get('ytd')}  1y {r.get('1y')}; "
                           f"expense {prof.expense_ratio}; top-10 = {prof.top10_weight}% of fund")
                if prof.holdings:
                    out.append("  COMPONENTS (weight, 1-week move, contribution to the fund's week in % pts):")
                    for h in prof.holdings[:8]:
                        out.append(f"    {h.symbol} ({h.name}): wt {round((h.weight or 0)*100,1)}%, "
                                   f"1w {h.ret_1w}%, contrib {h.contribution_1w} pts")
            hn = (extras.get("etf_holding_news") or {}).get(tk, {})
            if hn:
                out.append(f"  --- THIS WEEK'S NEWS AT {tk}'s COMPONENT COMPANIES (one etf.holdings_news "
                           "entry per company listed here, and ONLY these) ---")
                for sym, arts in hn.items():
                    out.append(f"  COMPONENT {sym}:")
                    out += _arts(arts, c_cap, c_snip)
        elif f:
            out.append(_fund_block(f))
        if prog_txt.get(tk):
            out.append(prog_txt[tk])
        e = earn.get(tk) or {}
        if e.get("recent"):
            rc = e["recent"]
            out.append(f"  REPORTED ({rc.get('date')}): EPS {rc.get('eps_actual')} vs est {rc.get('eps_estimate')} "
                       f"(surprise {rc.get('eps_surprise_pct')}%), revenue {_money(rc.get('rev_actual'))}")
        if e.get("upcoming"):
            u = e["upcoming"]
            out.append(f"  NEXT EARNINGS {u.get('date')} ({u.get('days_away')}d)")
        fil = fils.get(tk)
        if fil and fil.get("excerpt"):
            out.append(f"  SEC FILING {fil['form']} filed {fil['filed']} — excerpt: \"{fil['excerpt'][:f_snip]}\"")
        cw = crowd.get(tk)
        if cw and cw.get("has_data"):
            con = cw.get("consensus", {})
            out.append(f"  crowd (Adanos): {con.get('label')} ({con.get('bullish')}% bull / {con.get('bearish')}% bear)")
        tech = (extras.get("technicals") or {}).get(tk)
        if tech and not tech.error:
            out.append(f"  technicals: RSI {tech.rsi}; trend {tech.trend}; price {tech.price} vs SMA50 {tech.sma50} / "
                       f"SMA200 {tech.sma200}; support {tech.support} / resistance {tech.resistance}; signal {tech.signal}")
        arts = by_group.get(tk, [])
        if arts:
            out.append(f"  THIS WEEK'S ARTICLES ({len(arts)}):")
            out += _arts(arts, a_cap, a_snip)
        else:
            out.append("  THIS WEEK'S ARTICLES: none — leave summary and news_impact empty.")
    return "\n".join(out)


def _portfolio_instructions(tickers):
    return f"""Return ONLY a JSON object (no prose, no code fences):

{{
  "week_overview": "3-5 bullet lines: how THE PORTFOLIO did this week and why — which holdings moved, by how much (cite the weekly figures), and the company news behind it. Holdings only; at most one clause comparing to the benchmark.",
  "priority": [{{"ticker": "LLY", "why": "one line: why this holding mattered most this week"}}],
  "stocks": [
    {{"ticker": "LLY",
      "impact": "high|medium|low",
      "sentiment": "bullish|bearish|neutral|mixed",
      "summary": "2-4 bullet lines: THIS WEEK's company news — the substance from the article text (figures, terms, guidance) and why it matters. EMPTY STRING if the name had no articles.",
      "news_impact": "1-2 bullet lines: concrete effect on earnings/outlook/competitive position; empty string if no material news",
      "progression": "1-2 bullet lines: how the stock travelled over the week (cite the computed daily/weekly figures) and what drove it; if nothing company-specific explains it, say so",
      "fundamental_read": "one sentence on financial standing (for a fund: what it holds / its tilt)",
      "earnings": {{"result": "beat/miss with the numbers, or empty", "outlook": "guidance, or empty", "management_review": "management commentary from the filing, or empty"}},
      "etf": {{"move_explainer": "FUND ONLY: what drove the fund's week — name the component companies and the NEWS behind their moves (contribution figures quantify it); else empty",
               "holdings_news": [{{"symbol": "NVDA", "company": "NVIDIA", "news": "1-3 bullet lines on THIS component's news this week", "impact_on_fund": "one line, using its weight and contribution", "call": "bullish|bearish|neutral"}}]}},
      "technical_read": {{"call": "buy|accumulate|hold|reduce|sell", "rationale": "1-2 lines: your read of the technicals (you may override the rule-based signal, say why)"}}}}
  ],
  "risks": ["2-4 holding-specific risks for the coming weeks, each naming the holding and the concrete trigger"]
}}

Holdings: {', '.join(tickers)}
"stocks" MUST contain one entry for EVERY holding listed (ETFs included).
etf.holdings_news: one entry per component whose articles contain CONCRETE company news (a result, deal,
product, ruling, analyst action). Skip components whose articles are only passing mentions — no filler like
"X is a key player in AI".
Return STRICTLY valid JSON."""


def analyze_portfolio(items, funds, extras, config):
    watchlist = config.get("watchlist", {})
    tickers = [s.get("ticker") for s in watchlist.get("stocks", []) if s.get("ticker")]
    head = _portfolio_instructions(tickers) + "\n\nDATA:\n"
    data, status = _run_chain(PORTFOLIO_SYSTEM,
                              lambda c: head + _portfolio_context(items, funds, extras, watchlist, compact=c),
                              config, "portfolio")
    if data is None:
        data = _heur_portfolio(items, funds, extras, watchlist)
    else:
        have = {(s.get("ticker") or "").upper() for s in (data.get("stocks") or [])}
        missing = [s for s in watchlist.get("stocks", []) if s.get("ticker", "").upper() not in have]
        if missing:
            print(f"[analyzer] model omitted {', '.join(s['ticker'] for s in missing)} — heuristic card(s) added")
            data.setdefault("stocks", []).extend(
                _heur_portfolio(items, funds, extras, {"stocks": missing})["stocks"])
    _enforce_no_news(data, items, extras)
    data["_status"] = status
    return data, status


def _enforce_no_news(data, items, extras):
    """No articles → no news text, whatever the model wrote."""
    have = defaultdict(int)
    for it in items:
        have[it.group.upper()] += 1
    hn_all = extras.get("etf_holding_news") or {}
    for s in data.get("stocks") or []:
        tk = (s.get("ticker") or "").upper()
        comp_news = {k.upper() for k, v in (hn_all.get(s.get("ticker"), {}) or {}).items() if v}
        if not have.get(tk) and not comp_news:
            s["summary"] = ""
            s["news_impact"] = ""
        etf = s.get("etf") or {}
        if isinstance(etf, dict):
            etf["holdings_news"] = [e for e in (etf.get("holdings_news") or [])
                                    if isinstance(e, dict) and str(e.get("symbol", "")).upper() in comp_news]
            if not comp_news:
                etf["move_explainer"] = etf.get("move_explainer", "") if have.get(tk) else ""


def _heur_technical(tech):
    if not tech or tech.error:
        return {"call": "hold", "rationale": ""}
    m = {"strong buy": "buy", "buy": "accumulate", "hold": "hold", "sell": "reduce", "strong sell": "sell"}
    return {"call": m.get(tech.signal, "hold"),
            "rationale": "Rule-based: " + "; ".join((tech.reasons or [])[:2])}


def _heur_portfolio(items, funds, extras, watchlist):
    by_group = defaultdict(list)
    for it in items:
        by_group[it.group].append(it)
    progs = extras.get("progression", {})
    stocks = []
    for s in watchlist.get("stocks", []):
        tk = s.get("ticker", "").strip()
        if not tk:
            continue
        arts = by_group.get(tk, [])
        p = progs.get(tk) or {}
        prog = ""
        if p.get("week_pct") is not None:
            prog = f"- Week {p['week_pct']:+.2f}%"
            if p.get("vs_bench_pts") is not None:
                prog += f" ({p['vs_bench_pts']:+.2f} pts vs the benchmark)"
        hn = (extras.get("etf_holding_news") or {}).get(tk, {}) or {}
        stocks.append({
            "ticker": tk, "impact": "medium" if arts else "low", "sentiment": "neutral",
            # no AI → no written news; the card lists the week's article links instead
            "summary": "",
            "news_impact": "", "progression": prog, "fundamental_read": "",
            "earnings": {}, "technical_read": _heur_technical((extras.get("technicals") or {}).get(tk)),
            "etf": {"move_explainer": "",
                    "holdings_news": []},
        })
    moved = sorted([s for s in stocks if (progs.get(s["ticker"]) or {}).get("week_pct") is not None],
                   key=lambda s: -abs(progs[s["ticker"]]["week_pct"]))
    overview = [f"- {s['ticker']} {progs[s['ticker']]['week_pct']:+.2f}% this week" for s in moved[:5]]
    return {"week_overview": "\n".join(overview), "priority": [], "stocks": stocks, "risks": []}


# =========================================================================== #
#  DAILY MARKET
# =========================================================================== #
MARKET_SYSTEM = (
    "You are a markets strategist writing the reader's DAILY US MARKET DIGEST: what happened "
    "in the market since the last email and why. Cover policy & rates, the macro data "
    "released (actual vs consensus is given — interpret it), global news and its impact on US "
    "markets, and the main sectors. Pick AT MOST THREE stocks to watch, each with a specific, "
    "named catalyst. Never discuss the reader's portfolio holdings (listed as EXCLUDED) — they "
    "get a separate weekly email. NO NEWS → NO SECTION: return [] for any section the data "
    "does not support and only include sectors whose theme has articles. "
    + _WHY_RULE + " " + _FMT_RULE + " Informational analysis, not investment advice.")


def _market_context(items, extras, excluded, compact=False) -> str:
    t_cap, t_snip, o_cap, o_snip, k_n, k_snip, sc_n, sc_snip = ((3, 200, 3, 120, 15, 220, 10, 100) if compact
                                                           else (8, 900, 8, 500, 25, 700, 30, 220))
    by_group = defaultdict(list)
    for it in items:
        by_group[it.group].append(it)
    out = [f"NEWS WINDOW: {extras.get('period_label', 'the last day')}",
           f"EXCLUDED (reader's holdings — do not discuss or pick): {', '.join(excluded)}"]
    if extras.get("flags"):
        out.append("\n=== MARKET LEVELS (1-day move) — cite these ===\n  " + _flags_line(extras["flags"]))
    sec = extras.get("sector_moves") or []
    if sec:
        out.append("=== SECTOR ETF MOVES (1d / 5d) ===\n  " + " | ".join(
            f"{r['label']} ({r['symbol']}) {r['pct_1d']:+.2f}% / "
            + (f"{r['pct_5d']:+.2f}%" if r.get("pct_5d") is not None else "n/a") for r in sec
            if r.get("pct_1d") is not None))
    cal = extras.get("calendar") or {}
    if cal.get("fed_rate") or cal.get("fomc_next"):
        out.append(f"=== POLICY === Fed funds rate (upper bound) {cal.get('fed_rate') or 'n/a'}; next FOMC decision "
                   f"{cal['fomc_next'].strftime('%a %d %b %Y') if cal.get('fomc_next') else 'n/a'}")
    if cal.get("released"):
        out.append("=== MACRO DATA RELEASED IN THE WINDOW (actual vs consensus vs previous) ===")
        for e in cal["released"]:
            out.append(f"  {e['when'].strftime('%a %d %b %H:%M ET')} {e['label']}: actual {e['actual']} | "
                       f"consensus {e['consensus'] or 'n/a'} | previous {e['previous'] or 'n/a'}")
    if cal.get("upcoming"):
        out.append("=== SCHEDULED NEXT (for context; dates are printed separately) ===\n  " + "; ".join(
            f"{e['when'].strftime('%d %b')} {e['label']}" for e in cal["upcoming"][:12]))
    for th in extras.get("theme_labels", []):
        arts = by_group.get(th, [])
        if arts:
            out.append(f"\n=== THEME: {th} ({len(arts)} articles) ===")
            out += _arts(arts, t_cap, t_snip)
    off = by_group.get("Official releases", [])
    if off:
        out.append("\n=== OFFICIAL RELEASES (Fed / ECB / BLS / BEA) ===")
        out += _arts(off, o_cap, o_snip)
    cands = extras.get("candidates") or []
    if cands:
        out.append("\n=== MARKET-WIDE CANDIDATES (companies in the window's news; the ONLY pool for stocks_to_watch) ===")
        for c in cands[:k_n]:
            bits = [f"  {c['ticker']}"]
            for k, lab in (("price", "price"), ("pct_1d", "1d"), ("pct_5d", "5d"), ("vol_ratio", "vol x avg")):
                if c.get(k) is not None:
                    bits.append(f"{lab} {c[k]:+.2f}%" if k.startswith("pct") else f"{lab} {c[k]}")
            out.append(", ".join(bits) + f", {c.get('mentions', 1)} stories:")
            out += _arts(c.get("articles") or [], 1 if compact else 2, k_snip)
    scan = extras.get("market_scan") or []
    if scan:
        out.append("\n=== OTHER MARKET-WIDE HEADLINES ===")
        out += _arts(scan, sc_n, sc_snip)
    crypto = extras.get("crypto") or {}
    if crypto.get("snapshot"):
        out.append("\n=== CRYPTO ===")
        for c in crypto["snapshot"]:
            out.append(f"  {c['symbol']}: {c.get('price')} ({c.get('pct')}% 1d, {c.get('pct7d')}% 7d)")
        out += _arts(crypto.get("news") or [], 6, 300)
    return "\n".join(out)


def _market_instructions(themes, shariah, watch_pool):
    sh = (" Only names plausibly Shariah-compliant: no conventional banks/insurers or "
          "interest-based finance, alcohol, tobacco, gambling, weapons/defence, adult content, pork.") if shariah else ""
    return f"""Return ONLY a JSON object (no prose, no code fences):

{{
  "tldr": ["3-5 one-line bullets: the market's story since the last email — what moved, by how much (cite the levels), and WHY"],
  "policy_rates": ["0-3 bullets: Fed / other central banks / Treasury yields — what was said, decided or priced, and the market effect. [] if nothing in the data"],
  "macro_data": ["0-3 bullets interpreting the RELEASED prints (beat/miss vs consensus) and what they mean for rates and stocks. [] if nothing was released"],
  "global": ["0-3 bullets: global news (China, Europe, Japan, geopolitics, trade/tariffs, oil supply) and its impact on US markets. [] if none"],
  "sectors": [{{"theme": "exact THEME label from the data", "call": "bullish|bearish|neutral",
               "points": ["2-3 bullets: what happened in the sector and why, with figures (cite the sector ETF move)"]}}],
  "stocks_to_watch": [{{"ticker": "XYZ", "direction": "bullish|bearish",
                       "catalyst": "the specific event behind the pick, from its articles (with the date if it is scheduled)",
                       "why_now": "one line: why it is actionable now"}}],
  "crypto": {{"call": "bullish|bearish|neutral", "points": ["0-3 bullets with figures"],
             "coins": [{{"symbol": "BTC", "call": "buy|accumulate|hold|reduce|sell", "rationale": "one line"}}]}},
  "risks": ["2-4 forward-looking risks: the concrete trigger and what it would do to markets"]
}}

Themes available: {', '.join(themes)}.
"sectors": only themes that have articles AND something worth saying; keep the theme label exact.
"stocks_to_watch": up to {watch_pool}, BEST FIRST (the reader sees at most 3 after screening). Each MUST be a
ticker from MARKET-WIDE CANDIDATES whose own articles contain a concrete catalyst (earnings/guidance, deal,
regulatory/FDA, product, contract, analyst action). The catalyst must be an EVENT that happened inside the news
window (article dates are shown) or a scheduled event with its date — opinion or valuation pieces ("looks cheap",
"could soar", "stock to buy") and old results resurfacing in a recap are NOT catalysts.
US listings only (no .KS/.T/.L/... suffixes). Never an EXCLUDED ticker. Return [] if none qualify.{sh}
Return STRICTLY valid JSON."""


def analyze_market(items, extras, config):
    themes = extras.get("sector_labels") or extras.get("theme_labels", [])
    excluded = [s.get("ticker") for s in (config.get("watchlist", {}) or {}).get("stocks", []) if s.get("ticker")]
    watch_max = int((config.get("market_scan") or {}).get("watch_pool", 5))
    head = (_market_instructions(themes, config.get("shariah_only", False), watch_max) + "\n\nDATA:\n")
    data, status = _run_chain(MARKET_SYSTEM, lambda c: head + _market_context(items, extras, excluded, compact=c),
                              config, "market")
    if data is None:
        data = _heur_market(items, extras, watch_max)
    # Code-level guards: themes need articles; picks need a catalyst, ≤ watch_max,
    # never a holding.
    counts = defaultdict(int)
    for it in items:
        counts[it.group] += 1
    data["sectors"] = [s for s in (data.get("sectors") or [])
                       if isinstance(s, dict) and s.get("theme") in themes and counts.get(s.get("theme"), 0)
                       and (_pts(s.get("points")) or s.get("headlines_only"))]
    ex = {t.upper() for t in excluded}
    picks, seen = [], set()
    for p in data.get("stocks_to_watch") or []:
        tk = str((p or {}).get("ticker", "")).strip().upper()
        if not tk or tk in ex or tk in seen or not str(p.get("catalyst") or "").strip():
            continue
        seen.add(tk)
        p["ticker"] = tk
        picks.append(p)
    data["stocks_to_watch"] = picks[:watch_max]
    data["_status"] = status
    return data, status


def _pts(x):
    if isinstance(x, str):
        x = split_bullets(x)
    return [str(p).strip().lstrip("-•–·* ").strip() for p in (x or []) if str(p).strip()]


def _heur_market(items, extras, watch_max):
    by_group = defaultdict(list)
    for it in items:
        by_group[it.group].append(it)
    flags = {f["name"]: f for f in (extras.get("flags") or [])}
    tldr = []
    idx = [n for n in ("S&P 500", "Nasdaq", "Dow Jones", "Russell 2000") if flags.get(n, {}).get("pct") is not None]
    if idx:
        tldr.append("- " + ", ".join(f"{n} {flags[n]['pct']:+.2f}%" for n in idx))
    for n in ("10Y yield", "Brent", "Gold", "US Dollar (DXY)"):
        if flags.get(n, {}).get("pct") is not None:
            tldr.append(f"- {n} {flags[n]['price']} ({flags[n]['pct']:+.2f}%)")
    sec_moves = {r["symbol"]: r for r in (extras.get("sector_moves") or [])}
    sectors = []
    for th in extras.get("sector_labels", []):
        arts = by_group.get(th, [])
        r = sec_moves.get((extras.get("theme_etf") or {}).get(th))
        if not arts or not r:
            continue
        call = "bullish" if (r.get("pct_1d") or 0) > 0.75 else "bearish" if (r.get("pct_1d") or 0) < -0.75 else "neutral"
        sectors.append({"theme": th, "call": call, "points": [], "headlines_only": True})
    picks = []
    for c in (extras.get("candidates") or [])[:watch_max]:
        pct = c.get("pct_1d") or 0
        picks.append({"ticker": c["ticker"], "direction": "bullish" if pct >= 0 else "bearish",
                      "catalyst": c.get("headline", ""), "why_now": f"{c.get('mentions', 1)} stories in the window"})
    return {"tldr": tldr, "policy_rates": [], "macro_data": [], "global": [], "sectors": sectors,
            "stocks_to_watch": picks, "crypto": {}, "risks": []}


# =========================================================================== #
#  Debates
# =========================================================================== #
def run_holding_debates(analysis, funds, extras, items, config, state_data=None):
    """Weekly bull/bear/judge on every holding (ETFs get a single fund read)."""
    acfg = config.get("analysis", {}) or {}
    if not acfg.get("debate", True):
        return
    import debate
    import state as state_mod
    if debate._pick_roles(config) is None:
        print("[debate] no AI provider available — skipping")
        return
    by_tk = defaultdict(list)
    for it in items:
        by_tk[it.group].append(it)
    cap = int(acfg.get("debate_max", 8))
    targets = [s for s in analysis.get("stocks", []) if s.get("ticker")][:cap]
    print(f"[debate] weekly verdicts on {len(targets)} holding(s)…")
    for s in targets:
        tk = s["ticker"]
        f = funds.get(tk)
        prior = state_mod.last_verdict(state_data or {}, tk) if state_data is not None else None
        news = list(by_tk.get(tk, []))
        for sym, arts in ((extras.get("etf_holding_news") or {}).get(tk, {}) or {}).items():
            news += arts[:1]
        ctx = debate.build_context(tk, (f.name if f else tk), f, (extras.get("technicals") or {}).get(tk),
                                   (extras.get("crowd") or {}).get(tk), news,
                                   prog_text=(extras.get("progression_text") or {}).get(tk, ""), prior=prior)
        if prior:
            s["prior_verdict"] = prior
        res = debate.run_holding(tk, (f.name if f else tk), ctx, config, is_fund=bool(f and f.is_etf))
        if res:
            s["debate"] = res


def run_pick_debates(picks, funds, extras, items, config):
    """Bull/bear/judge + Catalyst Thesis on each stock to watch."""
    import debate
    import thesis as thesis_mod
    import prices
    if not (config.get("analysis", {}) or {}).get("debate", True) or debate._pick_roles(config) is None:
        return
    by_tk = defaultdict(list)
    for it in items:
        by_tk[it.group].append(it)
    for p in picks:
        tk = p["ticker"]
        f = funds.get(tk)
        tech = (extras.get("technicals") or {}).get(tk)
        e = ((extras.get("earnings") or {}).get(tk) or {}).get("upcoming") or {}
        nxt = None
        if e.get("date"):
            try:
                import datetime as _dt
                nxt = (_dt.date.fromisoformat(e["date"]), "earnings")
            except ValueError:
                nxt = None
        hist_fn = extras.get("history_fn") or prices.history
        p["computed"] = thesis_mod.compute(tk, f, tech, hist_fn(tk), nxt)
        news = by_tk.get(tk, [])
        ctx = debate.build_context(tk, (f.name if f else tk), f, tech, (extras.get("crowd") or {}).get(tk), news)
        ctx += f"\nWHY IT WAS FLAGGED: catalyst — {p.get('catalyst')}; {p.get('why_now', '')}"
        res = debate.run_pick(tk, (f.name if f else tk), ctx, p["computed"], config)
        if res:
            p["debate"] = res
