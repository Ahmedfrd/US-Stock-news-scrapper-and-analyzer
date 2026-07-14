"""
fundamentals.py — pull free fundamental + technical context for a ticker and
compute transparent factor scores.

All data comes from Yahoo Finance via `yfinance` (free, no key). Every score is
a simple, documented rule — NOT a black-box model — so you can see exactly why a
stock scores the way it does (explainability, à la Danelfin / Seeking Alpha Quant).

The scores are context for judgement, not buy/sell signals.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field


@dataclass
class Fundamentals:
    ticker: str
    name: str = ""
    sector: str = ""
    industry: str = ""
    price: float | None = None
    currency: str = ""                  # quote currency (e.g. USD, KRW) — non-USD is labelled in the report
    change_1d: float | None = None      # %
    ret_1m: float | None = None
    ret_3m: float | None = None
    ret_6m: float | None = None
    range_pos: float | None = None      # 0..1 within 52-week range
    market_cap: float | None = None
    # valuation
    pe: float | None = None
    forward_pe: float | None = None
    ps: float | None = None
    pb: float | None = None
    peg: float | None = None
    # growth
    rev_growth: float | None = None     # fraction
    earn_growth: float | None = None
    # profitability / health
    net_margin: float | None = None
    gross_margin: float | None = None
    roe: float | None = None
    debt_to_equity: float | None = None
    total_debt: float | None = None
    total_cash: float | None = None
    current_ratio: float | None = None
    fcf: float | None = None
    # analyst
    target_mean: float | None = None
    implied_upside: float | None = None  # %
    rating: float | None = None          # 1(strong buy)..5(sell)
    n_analysts: int | None = None
    next_earnings: str | None = None
    days_to_earnings: int | None = None
    # computed factor scores (0..100)
    scores: dict = field(default_factory=dict)
    error: str | None = None
    # ETF-specific
    is_etf: bool = False
    category: str = ""
    aum: float | None = None
    etf_yield: float | None = None
    ytd_return: float | None = None
    top_holdings: list = field(default_factory=list)   # [(name/symbol, weight_pct)]
    sector_weights: dict = field(default_factory=dict)  # {sector: weight_pct}


def _get(info: dict, *keys):
    for k in keys:
        v = info.get(k)
        if v is not None:
            return v
    return None


def _pct(a, b):
    try:
        return (a - b) / b * 100 if b else None
    except Exception:
        return None


# ----- scoring helpers: map a raw metric to 0..100 (higher = more attractive) -----
def _score_lower_better(x, good, bad):
    if x is None or x <= 0:
        return None
    if x <= good:
        return 100.0
    if x >= bad:
        return 0.0
    return round(100 * (bad - x) / (bad - good), 1)


def _score_higher_better(x, bad, good):
    if x is None:
        return None
    if x >= good:
        return 100.0
    if x <= bad:
        return 0.0
    return round(100 * (x - bad) / (good - bad), 1)


def _avg(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def _compute_scores(f: Fundamentals) -> dict:
    # Value: cheaper valuation multiples score higher.
    value = _avg([
        _score_lower_better(f.pe, 12, 45),
        _score_lower_better(f.forward_pe, 12, 40),
        _score_lower_better(f.ps, 1.5, 15),
        _score_lower_better(f.peg, 1.0, 3.0),
    ])
    # Growth: revenue + earnings growth (fractions).
    growth = _avg([
        _score_higher_better(f.rev_growth, 0.0, 0.30),
        _score_higher_better(f.earn_growth, 0.0, 0.30),
    ])
    # Profitability: margins + ROE.
    profit = _avg([
        _score_higher_better(f.net_margin, 0.0, 0.25),
        _score_higher_better(f.gross_margin, 0.10, 0.60),
        _score_higher_better(f.roe, 0.0, 0.30),
    ])
    # Momentum: trailing returns + position in 52-week range.
    momentum = _avg([
        _score_higher_better(f.ret_3m, -15, 25),
        _score_higher_better(f.ret_6m, -20, 40),
        _score_higher_better((f.range_pos * 100) if f.range_pos is not None else None, 20, 90),
    ])
    # Health: balance sheet.
    health = _avg([
        _score_lower_better(f.debt_to_equity, 40, 200),
        _score_higher_better(f.current_ratio, 0.8, 2.5),
    ])
    scores = {"value": value, "growth": growth, "profitability": profit,
              "momentum": momentum, "health": health}
    composite = _avg(list(scores.values()))
    scores["composite"] = composite
    return scores


def _price_and_momentum(t, f) -> None:
    """Fill price, 1d/1m/3m/6m returns and 52-week range position from history."""
    try:
        hist = t.history(period="6mo")
        closes = hist["Close"].dropna()
        if len(closes) >= 2:
            last = float(closes.iloc[-1])
            f.price = f.price or last
            f.change_1d = _pct(last, float(closes.iloc[-2]))
            if len(closes) >= 21:
                f.ret_1m = _pct(last, float(closes.iloc[-21]))
            if len(closes) >= 63:
                f.ret_3m = _pct(last, float(closes.iloc[-63]))
            f.ret_6m = _pct(last, float(closes.iloc[0]))
            hi, lo = float(closes.max()), float(closes.min())
            f.range_pos = (last - lo) / (hi - lo) if hi > lo else None
    except Exception:
        pass


def fetch(ticker: str, name: str = "") -> Fundamentals:
    f = Fundamentals(ticker=ticker, name=name or ticker)
    try:
        import yfinance as yf
    except ImportError:
        f.error = "yfinance not installed"
        return f

    try:
        t = yf.Ticker(ticker)
        info = {}
        try:
            info = t.info or {}
        except Exception:
            info = {}

        f.name = _get(info, "shortName", "longName") or f.name
        f.sector = info.get("sector", "") or ""
        f.industry = info.get("industry", "") or ""
        f.currency = (info.get("currency") or "").upper()

        quote_type = (info.get("quoteType") or "").upper()
        f.is_etf = quote_type in ("ETF", "MUTUALFUND")

        if f.is_etf:
            f.category = info.get("category", "") or ""
            f.aum = _get(info, "totalAssets")
            f.etf_yield = _get(info, "yield")
            f.ytd_return = _get(info, "ytdReturn")
            # price + momentum still apply to a fund
            _price_and_momentum(t, f)
            # holdings + sector weights (newer yfinance exposes .funds_data)
            try:
                fd = t.funds_data
                th = getattr(fd, "top_holdings", None)
                if th is not None and hasattr(th, "iterrows"):
                    for idx, rowv in th.head(10).iterrows():
                        name = rowv.get("Name", idx)
                        pct = rowv.get("Holding Percent")
                        f.top_holdings.append((str(name), float(pct) if pct is not None else None))
                sw = getattr(fd, "sector_weightings", None)
                if isinstance(sw, dict):
                    f.sector_weights = {k: round(float(v) * 100, 1) for k, v in sw.items()}
            except Exception:
                pass
            # For a fund, only momentum is meaningful; others are n/a by design.
            f.scores = {"value": None, "growth": None, "profitability": None,
                        "momentum": _compute_scores(f).get("momentum"),
                        "health": None}
            f.scores["composite"] = f.scores["momentum"]
            if f.target_mean and f.price:
                f.implied_upside = _pct(f.target_mean, f.price)
            return f

        f.market_cap = _get(info, "marketCap")
        f.price = _get(info, "currentPrice", "regularMarketPrice")
        f.pe = _get(info, "trailingPE")
        f.forward_pe = _get(info, "forwardPE")
        f.ps = _get(info, "priceToSalesTrailing12Months")
        f.pb = _get(info, "priceToBook")
        f.peg = _get(info, "trailingPegRatio", "pegRatio")
        f.rev_growth = _get(info, "revenueGrowth")
        f.earn_growth = _get(info, "earningsGrowth", "earningsQuarterlyGrowth")
        f.net_margin = _get(info, "profitMargins")
        f.gross_margin = _get(info, "grossMargins")
        f.roe = _get(info, "returnOnEquity")
        f.debt_to_equity = _get(info, "debtToEquity")
        f.total_debt = _get(info, "totalDebt")
        f.total_cash = _get(info, "totalCash")
        f.current_ratio = _get(info, "currentRatio")
        f.fcf = _get(info, "freeCashflow")
        f.target_mean = _get(info, "targetMeanPrice")
        f.rating = _get(info, "recommendationMean")
        f.n_analysts = _get(info, "numberOfAnalystOpinions")

        # Price history for momentum + 52w range.
        _price_and_momentum(t, f)

        if f.target_mean and f.price:
            f.implied_upside = _pct(f.target_mean, f.price)

        # Next earnings date (a key catalyst).
        try:
            cal = t.calendar
            ed = None
            if isinstance(cal, dict):
                ed = cal.get("Earnings Date")
                if isinstance(ed, (list, tuple)) and ed:
                    ed = ed[0]
            if ed is not None:
                ed = ed if isinstance(ed, dt.date) else getattr(ed, "date", lambda: None)()
                # Yahoo sometimes returns a stale, already-past date (common for
                # foreign listings) — showing "-82d" as upcoming is misleading.
                if ed and (ed - dt.date.today()).days >= -1:
                    f.next_earnings = ed.strftime("%Y-%m-%d")
                    f.days_to_earnings = (ed - dt.date.today()).days
        except Exception:
            pass

        f.scores = _compute_scores(f)
    except Exception as e:  # noqa: BLE001
        f.error = str(e)
        f.scores = _compute_scores(f)
    return f


def fetch_many(stocks: list[dict]) -> dict:
    """stocks: [{'ticker','name','peers':[...]}]  ->  {ticker: Fundamentals}.
    If a stock has no peers listed and Finnhub is available, peers are auto-filled."""
    try:
        import market_data
        finnhub_on = market_data.enabled()
    except ImportError:
        finnhub_on = False

    out = {}
    for s in stocks:
        tk = s.get("ticker", "").strip()
        if not tk:
            continue
        out[tk] = fetch(tk, s.get("name", ""))
        peers = s.get("peers") or []
        if not peers and finnhub_on:
            peers = market_data.peers(tk, limit=3)
            s["peers"] = peers  # so downstream (context/digest) sees them
        for peer in peers:
            peer = (peer or "").strip()
            if peer and peer not in out:
                out[peer] = fetch(peer)
    return out
