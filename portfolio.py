"""
portfolio.py — look-through concentration.

Aggregates your TRUE exposure to individual companies and sectors across both:
  * direct stock holdings, and
  * the underlying holdings of the ETFs you own.

So if you hold NVDA directly and also via VOO and QQQ, this surfaces your real
combined NVDA weight — the thing that's invisible when you look at positions
one by one.

Weights: add an optional `weight` (percent of portfolio) to each holding in
config. If every holding has one, they're normalised to 100%. If none do, an
equal weight is assumed (and the report says so).

Coverage caveats (stated honestly in the report):
  * Company look-through uses each ETF's DISCLOSED TOP HOLDINGS (Yahoo exposes
    ~top 10), so it captures the largest overlaps, not the entire basket.
  * Sector look-through uses the ETF's full sector weightings, so it's complete.
"""

from __future__ import annotations

from collections import defaultdict


def _norm_sector(s: str) -> str:
    if not s:
        return ""
    return s.replace("_", " ").strip().title()


def look_through(stocks_cfg, funds, etf_profiles) -> dict:
    tickers = [s.get("ticker", "").strip() for s in stocks_cfg if s.get("ticker")]
    if not tickers:
        return {}

    # ---- weights ----
    have_all = all(("weight" in s and s.get("weight") is not None)
                   for s in stocks_cfg if s.get("ticker"))
    weights, equal = {}, True
    if have_all:
        total = sum(float(s["weight"]) for s in stocks_cfg if s.get("ticker"))
        if total > 0:
            for s in stocks_cfg:
                tk = s.get("ticker", "").strip()
                if tk:
                    weights[tk] = float(s["weight"]) / total
            equal = False
    if equal:
        n = len(tickers)
        for tk in tickers:
            weights[tk] = 1.0 / n if n else 0.0

    company = defaultdict(float)                       # ticker -> total weight (fraction)
    name_of = {}
    src = defaultdict(lambda: {"direct": 0.0, "via": defaultdict(float)})
    sector = defaultdict(float)                        # display sector -> weight
    etf_count = 0

    for s in stocks_cfg:
        tk = s.get("ticker", "").strip()
        if not tk:
            continue
        w = weights.get(tk, 0.0)
        f = funds.get(tk)
        prof = etf_profiles.get(tk)
        if f and f.is_etf and prof:
            etf_count += 1
            for h in prof.holdings:
                if h.weight:
                    company[h.symbol] += w * h.weight
                    name_of[h.symbol] = h.name or h.symbol
                    src[h.symbol]["via"][tk] += w * h.weight
            for sec, pct in (prof.sector_weights or {}).items():
                sector[_norm_sector(sec)] += w * (pct / 100.0)
        else:
            company[tk] += w
            if f:
                name_of[tk] = f.name or tk
            src[tk]["direct"] += w
            if f and f.sector:
                sector[_norm_sector(f.sector)] += w

    # ---- build sorted company list ----
    companies = []
    for tk, tot in sorted(company.items(), key=lambda x: x[1], reverse=True):
        via = [{"etf": e, "pct": round(p * 100, 2)}
               for e, p in sorted(src[tk]["via"].items(), key=lambda x: x[1], reverse=True)]
        companies.append({
            "ticker": tk, "name": name_of.get(tk, tk),
            "total_pct": round(tot * 100, 2),
            "direct_pct": round(src[tk]["direct"] * 100, 2),
            "via": via,
            "overlap": bool(src[tk]["direct"] > 0 and via),  # held directly AND via an ETF
        })

    sectors = [{"sector": s, "pct": round(p * 100, 1)}
               for s, p in sorted(sector.items(), key=lambda x: x[1], reverse=True) if p > 0]

    # ---- concentration flags ----
    flags = []
    for c in companies[:15]:
        if c["total_pct"] >= 10:
            extra = " (held directly and inside your ETFs)" if c["overlap"] else ""
            flags.append(f"{c['ticker']} is {c['total_pct']}% of your look-through exposure{extra}.")
    if sectors and sectors[0]["pct"] >= 30:
        flags.append(f"{sectors[0]['sector']} is {sectors[0]['pct']}% of your portfolio — heavy sector tilt.")
    top10 = round(sum(c["total_pct"] for c in companies[:10]), 1)
    overlaps = [c for c in companies if c["overlap"]]
    if overlaps:
        names = [c["ticker"] for c in overlaps[:6]]
        verb = "is held" if len(names) == 1 else "are held"
        flags.append(f"Overlap: {', '.join(names)} {verb} both directly and inside your funds.")

    return {"equal_weight": equal, "companies": companies, "sectors": sectors,
            "flags": flags, "top10_pct": top10, "etf_count": etf_count,
            "n_holdings": len(tickers)}
