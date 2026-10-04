"""
prices.py — one cached daily-price fetch per symbol per run, plus the weekly
PROGRESSION figures the portfolio review and the debate read.

Technicals, the weekly progression and the catalyst-thesis maths all need the
same daily bars; fetching them once keeps the run fast and the numbers
consistent across sections.
"""

from __future__ import annotations

import datetime as dt

_CACHE: dict[tuple[str, str], object] = {}


def history(symbol: str, period: str = "1y"):
    """Daily OHLCV DataFrame from yfinance (None if unavailable). Cached."""
    key = (symbol.upper(), period)
    if key in _CACHE:
        return _CACHE[key]
    hist = None
    try:
        import yfinance as yf
        h = yf.Ticker(symbol).history(period=period, auto_adjust=False)
        if h is not None and not h.empty:
            hist = h
    except Exception:  # noqa: BLE001
        hist = None
    _CACHE[key] = hist
    return hist


def _pct(a, b):
    try:
        return round((float(a) - float(b)) / float(b) * 100, 2) if b else None
    except Exception:  # noqa: BLE001
        return None


def _rsi_series(close):
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    return 100 - 100 / (1 + gain / loss)


def progression(symbol: str, hist=None, bench_hist=None, sessions: int = 5) -> dict:
    """How the stock travelled over the last week (and the weeks before it).

    Returns {} when there is no usable history. Every figure is arithmetic on
    daily closes — nothing here is model-generated.
      days:        [{date, close, pct}] for the last `sessions` sessions
      week_pct:    close vs the close `sessions` sessions earlier
      weeks:       the last 4 weekly % changes, oldest first
      m1_pct / m3_pct / ytd_pct
      bench_week_pct, vs_bench_pts
      rsi_now / rsi_week_ago
      off_high_pct: distance below the 52-week high
      vol_week_vs_avg: this week's average volume vs the prior 20 sessions
    """
    hist = hist if hist is not None else history(symbol)
    if hist is None or len(hist) < sessions + 2:
        return {}
    try:
        close = hist["Close"].dropna()
        out: dict = {}
        last = float(close.iloc[-1])
        days = []
        for i in range(sessions, 0, -1):
            idx = len(close) - i
            if idx < 1:
                continue
            d = close.index[idx]
            days.append({"date": d.strftime("%a %d %b"), "close": round(float(close.iloc[idx]), 2),
                         "pct": _pct(close.iloc[idx], close.iloc[idx - 1])})
        out["days"] = days
        out["week_pct"] = _pct(last, close.iloc[-(sessions + 1)])
        weeks = []
        for w in range(4, 0, -1):
            a, b = -(w - 1) * sessions - 1, -w * sessions - 1
            if len(close) >= -b:
                weeks.append(_pct(close.iloc[a], close.iloc[b]))
        out["weeks"] = weeks
        out["m1_pct"] = _pct(last, close.iloc[-22]) if len(close) >= 22 else None
        out["m3_pct"] = _pct(last, close.iloc[-64]) if len(close) >= 64 else None
        this_year = close[close.index.year == close.index[-1].year]
        if len(this_year) >= 2 and len(close) > len(this_year):
            prev_year_close = close.iloc[len(close) - len(this_year) - 1]
            out["ytd_pct"] = _pct(last, prev_year_close)
        hi = float(close.tail(252).max())
        out["off_high_pct"] = _pct(last, hi)
        rsi = _rsi_series(close)
        out["rsi_now"] = round(float(rsi.iloc[-1]), 1)
        out["rsi_week_ago"] = round(float(rsi.iloc[-(sessions + 1)]), 1)
        if "Volume" in hist:
            vol = hist["Volume"].dropna()
            if len(vol) >= sessions + 20:
                wk = float(vol.tail(sessions).mean())
                base = float(vol.iloc[-(sessions + 20):-sessions].mean())
                out["vol_week_vs_avg"] = round(wk / base, 2) if base else None
        if bench_hist is not None and len(bench_hist) > sessions + 1:
            bc = bench_hist["Close"].dropna()
            out["bench_week_pct"] = _pct(bc.iloc[-1], bc.iloc[-(sessions + 1)])
            if out.get("week_pct") is not None and out.get("bench_week_pct") is not None:
                out["vs_bench_pts"] = round(out["week_pct"] - out["bench_week_pct"], 2)
        out["as_of"] = close.index[-1].strftime("%a %d %b %Y")
        return out
    except Exception as e:  # noqa: BLE001
        print(f"[prices] progression failed for {symbol}: {e}")
        return {}


def describe(prog: dict, currency: str = "") -> str:
    """One compact text block for the AI context."""
    if not prog:
        return ""
    cur = f"{currency} " if currency else ""
    days = ", ".join(f"{d['date']} {cur}{d['close']} ({d['pct']:+.2f}%)" for d in prog.get("days", [])
                     if d.get("pct") is not None)
    bits = [f"  WEEKLY PROGRESSION (as of {prog.get('as_of')}): week {_fmt(prog.get('week_pct'))}"]
    if prog.get("vs_bench_pts") is not None:
        bits.append(f"benchmark week {_fmt(prog.get('bench_week_pct'))} "
                    f"(relative {prog['vs_bench_pts']:+.2f} pts)")
    if prog.get("weeks"):
        bits.append("last 4 weeks " + " → ".join(_fmt(w) for w in prog["weeks"]))
    for k, lab in (("m1_pct", "1m"), ("m3_pct", "3m"), ("ytd_pct", "YTD"), ("off_high_pct", "vs 52w high")):
        if prog.get(k) is not None:
            bits.append(f"{lab} {_fmt(prog[k])}")
    if prog.get("rsi_now") is not None:
        bits.append(f"RSI {prog.get('rsi_week_ago')} → {prog['rsi_now']} over the week")
    if prog.get("vol_week_vs_avg") is not None:
        bits.append(f"week's volume {prog['vol_week_vs_avg']}x the prior 20-day average")
    return "; ".join(bits) + (f"\n    daily closes: {days}" if days else "")


def _fmt(x):
    return "n/a" if x is None else f"{x:+.2f}%"


def batch_moves(symbols: list[str], period: str = "1mo") -> dict:
    """{sym: {price, pct_1d, pct_5d}} in one batched yfinance call."""
    out = {}
    syms = [s for s in symbols if s]
    if not syms:
        return out
    try:
        import yfinance as yf
        data = yf.download(" ".join(syms), period=period, interval="1d", group_by="ticker",
                           auto_adjust=False, progress=False, threads=True)
    except Exception:  # noqa: BLE001
        return out
    import pandas as pd
    multi = isinstance(data.columns, pd.MultiIndex)
    for s in syms:
        try:
            if multi:
                lvl0 = set(data.columns.get_level_values(0))
                df = data[s] if s in lvl0 else data.xs(s, axis=1, level=1)
            else:
                df = data
            c = df["Close"].dropna()
            if len(c) < 2:
                continue
            out[s] = {"price": round(float(c.iloc[-1]), 2),
                      "pct_1d": _pct(c.iloc[-1], c.iloc[-2]),
                      "pct_5d": _pct(c.iloc[-1], c.iloc[-6]) if len(c) >= 6 else None,
                      "as_of": c.index[-1].date().isoformat()}
        except Exception:  # noqa: BLE001
            continue
    return out


def today_hk() -> dt.date:
    from zoneinfo import ZoneInfo
    return dt.datetime.now(ZoneInfo("Asia/Hong_Kong")).date()
