"""
etf.py — comprehensive ETF/fund analysis (free data via yfinance).

For a fund we compute, as far as free data allows:
  * Multi-horizon returns (1d, 1w, 1m, 3m, 6m, YTD, 1y)
  * Move ATTRIBUTION — which top holdings drove today's move (weight x holding move)
  * NAV premium/discount (is it trading above/below the value of what it holds?)
  * Risk — annualised volatility, max drawdown (1y), beta vs a benchmark, and
    concentration (top-10 weight, largest sector)
  * Relative performance vs a benchmark (default SPY) across horizons
  * Peer comparison (returns / expense / yield / AUM) for peer ETFs you list
  * Top holdings and sector weights

Everything is defensive: any piece that Yahoo can't supply comes back as None and
is shown as "n/a" rather than guessed.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field


@dataclass
class Holding:
    symbol: str
    name: str
    weight: float | None            # fraction (0..1)
    ret_1d: float | None = None     # %
    contribution: float | None = None  # weight * ret_1d, in % points


@dataclass
class Peer:
    ticker: str
    ret_1y: float | None = None
    expense: float | None = None
    etf_yield: float | None = None
    aum: float | None = None


@dataclass
class EtfProfile:
    ticker: str
    returns: dict = field(default_factory=dict)        # {'1d','1w','1m','3m','6m','ytd','1y'}
    vol_1y: float | None = None                        # annualised %
    max_drawdown_1y: float | None = None               # %
    beta: float | None = None
    nav: float | None = None
    premium_discount: float | None = None              # %
    expense_ratio: float | None = None
    etf_yield: float | None = None
    aum: float | None = None
    category: str = ""
    holdings: list = field(default_factory=list)       # [Holding]
    top10_weight: float | None = None                  # %
    sector_weights: dict = field(default_factory=dict)
    rel_market: dict = field(default_factory=dict)     # {horizon: etf-bmk in % pts}
    benchmark: str = "SPY"
    tracks_benchmark: bool = False                     # ~identical to benchmark (e.g. S&P ETF vs SPY)
    peers: list = field(default_factory=list)          # [Peer]
    explained_move: float | None = None                # sum of contributions, %
    error: str | None = None


def _norm_expense(x):
    """Return expense ratio as a PERCENT number (e.g. 0.03 for a 0.03% fund),
    whether the source gave a fraction (0.0003) or a percent (0.03)."""
    v = _f(x)
    if v is None:
        return None
    return round(v * 100, 3) if v < 0.02 else round(v, 3)


def _ret(closes, periods):
    try:
        if len(closes) > periods:
            a, b = float(closes.iloc[-1]), float(closes.iloc[-1 - periods])
            return (a - b) / b * 100 if b else None
    except Exception:
        pass
    return None


def _returns_block(closes) -> dict:
    out = {"1d": _ret(closes, 1), "1w": _ret(closes, 5), "1m": _ret(closes, 21),
           "3m": _ret(closes, 63), "6m": _ret(closes, 126), "1y": _ret(closes, 252)}
    # YTD
    try:
        import pandas as pd  # noqa
        this_year = closes[closes.index.year == dt.date.today().year]
        if len(this_year) >= 2:
            a, b = float(this_year.iloc[-1]), float(this_year.iloc[0])
            out["ytd"] = (a - b) / b * 100 if b else None
    except Exception:
        out["ytd"] = None
    return out


def _vol_and_drawdown(closes):
    try:
        rets = closes.pct_change().dropna()
        vol = float(rets.std()) * (252 ** 0.5) * 100 if len(rets) > 5 else None
        cummax = closes.cummax()
        dd = ((closes - cummax) / cummax).min()
        mdd = float(dd) * 100 if dd == dd else None  # NaN check
        return vol, mdd
    except Exception:
        return None, None


def _beta(etf_closes, bmk_closes):
    try:
        import pandas as pd
        a = etf_closes.pct_change().dropna()
        b = bmk_closes.pct_change().dropna()
        df = pd.concat([a, b], axis=1, join="inner").dropna()
        if len(df) < 30:
            return None
        cov = df.cov().iloc[0, 1]
        var = df.iloc[:, 1].var()
        return round(float(cov / var), 2) if var else None
    except Exception:
        return None


def enrich(ticker: str, benchmark: str = "SPY", peer_tickers: list | None = None) -> EtfProfile:
    p = EtfProfile(ticker=ticker, benchmark=benchmark)
    try:
        import yfinance as yf
    except ImportError:
        p.error = "yfinance not installed"
        return p

    try:
        t = yf.Ticker(ticker)
        info = {}
        try:
            info = t.info or {}
        except Exception:
            info = {}
        p.category = info.get("category", "") or ""
        p.aum = info.get("totalAssets")
        p.etf_yield = info.get("yield")
        p.nav = info.get("navPrice")
        p.expense_ratio = _norm_expense(info.get("annualReportExpenseRatio")
                           or info.get("netExpenseRatio") or info.get("expenseRatio"))
        price = info.get("regularMarketPrice") or info.get("currentPrice")

        etf_hist = t.history(period="2y")
        closes = etf_hist["Close"].dropna()
        if len(closes):
            price = price or float(closes.iloc[-1])
            p.returns = _returns_block(closes)
            p.vol_1y, p.max_drawdown_1y = _vol_and_drawdown(closes)
        if p.nav and price:
            p.premium_discount = (price - p.nav) / p.nav * 100

        # Benchmark for beta + relative performance
        try:
            bmk_hist = yf.Ticker(benchmark).history(period="2y")
            bmk_closes = bmk_hist["Close"].dropna()
            if len(bmk_closes):
                p.beta = _beta(closes, bmk_closes)
                bmk_ret = _returns_block(bmk_closes)
                p.rel_market = {h: round(p.returns.get(h) - bmk_ret.get(h), 2)
                                for h in p.returns
                                if p.returns.get(h) is not None and bmk_ret.get(h) is not None}
                if p.rel_market and all(abs(v) < 0.2 for v in p.rel_market.values()):
                    p.tracks_benchmark = True
        except Exception:
            pass

        # Holdings + sector weights
        try:
            fd = t.funds_data
            th = getattr(fd, "top_holdings", None)
            if th is not None and hasattr(th, "iterrows"):
                for sym, row in th.head(10).iterrows():
                    p.holdings.append(Holding(symbol=str(sym),
                                              name=str(row.get("Name", sym)),
                                              weight=_f(row.get("Holding Percent"))))
            sw = getattr(fd, "sector_weightings", None)
            if isinstance(sw, dict):
                p.sector_weights = {k: round(_f(v) * 100, 1) for k, v in sw.items() if _f(v) is not None}
        except Exception:
            pass

        if p.holdings:
            p.top10_weight = round(sum((h.weight or 0) for h in p.holdings) * 100, 1)

        # Per-holding 1d move + contribution to the ETF's move
        explained = 0.0
        for h in p.holdings[:8]:
            try:
                hh = yf.Ticker(h.symbol).history(period="5d")["Close"].dropna()
                h.ret_1d = _ret(hh, 1)
                if h.ret_1d is not None and h.weight is not None:
                    h.contribution = round(h.weight * h.ret_1d, 3)
                    explained += h.contribution
            except Exception:
                continue
        p.explained_move = round(explained, 3) if p.holdings else None

        # Peers
        for peer in (peer_tickers or [])[:3]:
            peer = (peer or "").strip()
            if not peer:
                continue
            pr = Peer(ticker=peer)
            try:
                pi = yf.Ticker(peer)
                pinfo = pi.info or {}
                pr.expense = _norm_expense(pinfo.get("annualReportExpenseRatio")
                              or pinfo.get("netExpenseRatio"))
                pr.etf_yield = pinfo.get("yield")
                pr.aum = pinfo.get("totalAssets")
                pc = pi.history(period="2y")["Close"].dropna()
                pr.ret_1y = _ret(pc, 252)
            except Exception:
                pass
            p.peers.append(pr)

    except Exception as e:  # noqa: BLE001
        p.error = str(e)
    return p


def _f(x):
    try:
        return float(x)
    except Exception:
        return None
