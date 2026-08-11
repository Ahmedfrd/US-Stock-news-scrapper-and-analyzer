"""
market_data.py — Finnhub free-tier data (key: FINNHUB_API_KEY).

Free endpoints used (60 calls/min, no credit card):
  * /company-news        real article headlines + URLs + summaries
  * /calendar/earnings   upcoming + just-reported earnings (with EPS/rev estimates)
  * /stock/peers         auto peer tickers
  * /stock/metric        extra fundamental ratios

Finnhub's own news-sentiment endpoint is premium, so we don't use it (sentiment.py
covers that for free). Everything degrades gracefully if the key is missing.
"""

from __future__ import annotations

import os
import datetime as dt
import requests

BASE = "https://finnhub.io/api/v1"


def enabled() -> bool:
    return bool(os.environ.get("FINNHUB_API_KEY"))


def _get(path: str, params: dict):
    key = os.environ.get("FINNHUB_API_KEY")
    if not key:
        return None
    params = dict(params, token=key)
    try:
        r = requests.get(f"{BASE}{path}", params=params, timeout=30)
        if r.status_code == 429:
            return None
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def company_news(symbol: str, lookback_days: int = 3, max_items: int = 8):
    """Return list of dicts: {title, url, source, summary, published(datetime)}."""
    today = dt.date.today()
    frm = (today - dt.timedelta(days=lookback_days)).isoformat()
    data = _get("/company-news", {"symbol": symbol, "from": frm, "to": today.isoformat()})
    if not isinstance(data, list):
        return []
    out = []
    for n in data[: max_items * 2]:
        ts = n.get("datetime")
        published = dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc) if ts else None
        title = (n.get("headline") or "").strip()
        if not title:
            continue
        out.append({
            "title": title,
            "url": n.get("url", ""),
            "source": n.get("source", "Finnhub"),
            "summary": (n.get("summary") or "").strip(),
            "published": published,
        })
        if len(out) >= max_items:
            break
    return out


def general_news(category: str = "general", max_items: int = 40):
    """Market-WIDE news (not tied to any ticker) from Finnhub's free /news feed.
    This is the discovery feed: it carries stories about companies you have never
    held, which is exactly what 'stocks to watch' needs. Returns the same dict
    shape as company_news()."""
    data = _get("/news", {"category": category})
    if not isinstance(data, list):
        return []
    out = []
    for n in data[: max_items * 2]:
        ts = n.get("datetime")
        published = dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc) if ts else None
        title = (n.get("headline") or "").strip()
        if not title:
            continue
        out.append({
            "title": title,
            "url": n.get("url", ""),
            "source": n.get("source", "Finnhub"),
            "summary": (n.get("summary") or "").strip(),
            "published": published,
            "related": (n.get("related") or "").strip(),  # Finnhub's own ticker tags
        })
        if len(out) >= max_items:
            break
    return out


_SYMBOLS_CACHE = None


def us_symbols():
    """The full US-listed symbol directory (free): [{symbol, description, type}].
    Used to turn company names mentioned in market-wide headlines back into
    tickers. Cached for the life of the process — it's a ~25k-row payload."""
    global _SYMBOLS_CACHE
    if _SYMBOLS_CACHE is not None:
        return _SYMBOLS_CACHE
    data = _get("/stock/symbol", {"exchange": "US"})
    _SYMBOLS_CACHE = data if isinstance(data, list) else []
    return _SYMBOLS_CACHE


def peers(symbol: str, limit: int = 3):
    data = _get("/stock/peers", {"symbol": symbol})
    if not isinstance(data, list):
        return []
    # First entry is usually the symbol itself.
    return [p for p in data if p and p != symbol][:limit]


def earnings_window(symbol: str, back_days: int = 10, fwd_days: int = 40) -> dict:
    """Return {'upcoming': {...}|None, 'recent': {...}|None} from the calendar."""
    today = dt.date.today()
    frm = (today - dt.timedelta(days=back_days)).isoformat()
    to = (today + dt.timedelta(days=fwd_days)).isoformat()
    data = _get("/calendar/earnings", {"symbol": symbol, "from": frm, "to": to})
    rows = (data or {}).get("earningsCalendar", []) if isinstance(data, dict) else []
    upcoming, recent = None, None
    for row in rows:
        try:
            d = dt.date.fromisoformat(row.get("date"))
        except Exception:
            continue
        entry = {
            "date": row.get("date"),
            "eps_estimate": row.get("epsEstimate"),
            "eps_actual": row.get("epsActual"),
            "rev_estimate": row.get("revenueEstimate"),
            "rev_actual": row.get("revenueActual"),
            "hour": row.get("hour"),
            "quarter": row.get("quarter"),
            "year": row.get("year"),
        }
        reported = entry["eps_actual"] is not None
        if d >= today and not reported:
            if upcoming is None or d < dt.date.fromisoformat(upcoming["date"]):
                upcoming = entry
                upcoming["days_away"] = (d - today).days
        elif d <= today and reported:
            if recent is None or d > dt.date.fromisoformat(recent["date"]):
                recent = entry
                recent["days_ago"] = (today - d).days
                if entry["eps_estimate"]:
                    try:
                        recent["eps_surprise_pct"] = round(
                            (entry["eps_actual"] - entry["eps_estimate"])
                            / abs(entry["eps_estimate"]) * 100, 1)
                    except Exception:
                        recent["eps_surprise_pct"] = None
    return {"upcoming": upcoming, "recent": recent}


def metrics(symbol: str) -> dict:
    data = _get("/stock/metric", {"symbol": symbol, "metric": "all"})
    if not isinstance(data, dict):
        return {}
    m = data.get("metric", {}) or {}
    return {
        "pe_ttm": m.get("peTTM"),
        "ps_ttm": m.get("psTTM"),
        "pb": m.get("pbQuarterly") or m.get("pbAnnual"),
        "roe_ttm": m.get("roeTTM"),
        "net_margin_ttm": m.get("netProfitMarginTTM"),
        "rev_growth_ttm": m.get("revenueGrowthTTMYoy"),
        "52w_high": m.get("52WeekHigh"),
        "52w_low": m.get("52WeekLow"),
        "beta": m.get("beta"),
    }
