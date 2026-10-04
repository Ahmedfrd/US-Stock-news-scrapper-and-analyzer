"""
thesis.py — the Catalyst Thesis behind every buy/sell call on a stock-to-watch.

THE RULE (borrowed from the market-digest monorepo, spec §16): every number is
computed here, in Python. The model is handed these figures and returns only
REASONING — the catalyst, how it reaches earnings/price, what is priced in,
what would prove it wrong. It never originates a price level, so a
hallucinated "target 214.80" cannot reach the email.

  compute()   -> the figures (price, support/resistance, volatility unit,
                 distance from moving averages, liquidity, days to the next
                 scheduled catalyst, today's move vs typical).
  validate()  -> whether the model's thesis is complete enough to print.
"""

from __future__ import annotations

import datetime as dt

DIRECTIONAL = {"buy", "accumulate", "sell", "reduce", "avoid"}
HORIZONS = ("days", "weeks", "quarter")
REQUIRED = ("catalyst", "mechanism", "invalidation")


def is_directional(call: str | None) -> bool:
    return (call or "").strip().lower() in DIRECTIONAL


def compute(ticker: str, fund=None, tech=None, hist=None, next_event=None) -> dict:
    """Every figure in a Catalyst Thesis — no model involved.

    next_event: (date, title) of the next scheduled company catalyst, if known.
    """
    out = {"ticker": ticker.upper()}
    price = getattr(fund, "price", None) or getattr(tech, "price", None)
    out["price"] = round(float(price), 2) if price else None
    out["currency"] = (getattr(fund, "currency", "") or "") if fund else ""
    out["support"] = getattr(tech, "support", None)
    out["resistance"] = getattr(tech, "resistance", None)
    out["atr_pct"] = getattr(tech, "atr_pct", None)
    for span in (50, 200):
        sma = getattr(tech, f"sma{span}", None)
        out[f"dist_sma{span}"] = (round((price - sma) / sma * 100, 2) if (price and sma) else None)
    move = getattr(fund, "change_1d", None)
    out["move_1d"] = round(move, 2) if move is not None else None
    unit = out["atr_pct"]
    out["move_vs_typical"] = round(move / unit, 1) if (move is not None and unit) else None
    out["liquidity_20d"] = _liquidity(hist)
    if next_event and next_event[0]:
        out["next_catalyst"] = {"date": next_event[0].isoformat()
                                if hasattr(next_event[0], "isoformat") else str(next_event[0]),
                                "title": next_event[1],
                                "days": (next_event[0] - dt.date.today()).days
                                if hasattr(next_event[0], "year") else None}
    sup, res = out["support"], out["resistance"]
    if price and sup and res and res > price > sup:
        out["upside_to_res_pct"] = round((res - price) / price * 100, 2)
        out["downside_to_sup_pct"] = round((sup - price) / price * 100, 2)
    return out


def _liquidity(hist):
    """20-day average traded value (close × volume)."""
    try:
        if hist is None or len(hist) < 10:
            return None
        tail = hist.tail(20)
        vals = (tail["Close"] * tail["Volume"]).dropna()
        vals = vals[vals > 0]
        return round(float(vals.mean()), 0) if len(vals) else None
    except Exception:  # noqa: BLE001
        return None


def describe(c: dict) -> str:
    """The computed block as fixed context for the model."""
    if not c:
        return ""
    cur = c.get("currency") or ""
    parts = [f"price {c.get('price')} {cur}".strip(),
             f"support {c.get('support')}", f"resistance {c.get('resistance')}",
             f"ATR {c.get('atr_pct')}% of price (one typical daily move)"]
    if c.get("move_1d") is not None:
        parts.append(f"last move {c['move_1d']:+.2f}% (= {c.get('move_vs_typical')}x typical)")
    for k, lab in (("dist_sma50", "vs 50-day avg"), ("dist_sma200", "vs 200-day avg")):
        if c.get(k) is not None:
            parts.append(f"{lab} {c[k]:+.2f}%")
    if c.get("upside_to_res_pct") is not None:
        parts.append(f"room to resistance {c['upside_to_res_pct']:+.2f}%, to support {c['downside_to_sup_pct']:+.2f}%")
    if c.get("liquidity_20d"):
        parts.append(f"20-day avg traded value {c['liquidity_20d']:,.0f} {cur}".strip())
    nc = c.get("next_catalyst")
    if nc:
        parts.append(f"next scheduled company event: {nc['title']} on {nc['date']}"
                     + (f" ({nc['days']}d away)" if nc.get("days") is not None else ""))
    return "COMPUTED FIGURES (fixed — reason about them, never restate different numbers): " + "; ".join(parts)


def validate(thesis: dict | None) -> tuple[bool, list[str]]:
    """A directional call prints only with a complete thesis."""
    if not isinstance(thesis, dict):
        return False, ["no thesis returned"]
    problems = [f"{k} empty" for k in REQUIRED if not str(thesis.get(k) or "").strip()]
    hz = str(thesis.get("horizon") or "").strip().lower()
    if hz not in HORIZONS:
        # normalise common variants rather than reject a good thesis over wording
        if "day" in hz:
            thesis["horizon"] = "days"
        elif "week" in hz or "month" in hz:
            thesis["horizon"] = "weeks"
        elif "quarter" in hz:
            thesis["horizon"] = "quarter"
        else:
            problems.append(f"horizon {hz!r} not one of {HORIZONS}")
    return (not problems), problems
