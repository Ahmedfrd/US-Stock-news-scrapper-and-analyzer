"""
technicals.py — classic technical indicators + a transparent, rule-based signal.

Computed from free daily price history (yfinance). No TA-Lib dependency — all
indicators are standard pandas math so you can read exactly how each is derived.

Indicators: RSI(14), MACD(12/26/9), ATR(14), SMA(20/50/200), EMA(12/26/50),
volume vs 20-day average, and support/resistance (recent range + pivot levels).

The `signal` (bullish/neutral/bearish + buy/hold/sell lean) is a plain rules
engine, shown with the exact reasons that produced it. It is a technical read of
the CURRENT setup, not a prediction or a recommendation.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Technicals:
    ticker: str
    price: float | None = None
    rsi: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_hist: float | None = None
    macd_cross: str = ""            # "bullish" / "bearish" / ""
    sma20: float | None = None
    sma50: float | None = None
    sma200: float | None = None
    ema12: float | None = None
    ema26: float | None = None
    ema50: float | None = None
    atr: float | None = None
    atr_pct: float | None = None    # ATR as % of price (volatility)
    vol: float | None = None
    vol_avg20: float | None = None
    vol_ratio: float | None = None  # today's volume / 20-day avg
    support: float | None = None
    resistance: float | None = None
    pivot: float | None = None
    trend: str = ""                 # "up" / "down" / "sideways"
    bias: str = "neutral"           # bullish / neutral / bearish
    signal: str = "hold"            # strong buy / buy / hold / sell / strong sell
    score: int = 0                  # -6..+6 raw rule score
    reasons: list = field(default_factory=list)
    error: str | None = None


def compute_from_closes(ticker: str, close, volumes=None) -> Technicals:
    """Technicals from a close-price Series (+ optional volume Series).
    Used for markets without free OHLC (e.g. PSX): ATR is n/a, support/resistance
    come from closing prices, everything else is identical."""
    t = Technicals(ticker=ticker)
    try:
        import pandas as pd  # noqa
    except ImportError:
        t.error = "pandas not installed"
        return t
    try:
        if close is None or len(close) < 30:
            t.error = "insufficient history"
            return t
        close = close.dropna()
        t.price = float(close.iloc[-1])

        delta = close.diff()
        gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
        rs = gain / loss
        t.rsi = round(float((100 - 100 / (1 + rs)).iloc[-1]), 1)

        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        hist_line = macd - signal
        t.macd = round(float(macd.iloc[-1]), 3)
        t.macd_signal = round(float(signal.iloc[-1]), 3)
        t.macd_hist = round(float(hist_line.iloc[-1]), 3)
        if len(hist_line) >= 2:
            prev, now = float(hist_line.iloc[-2]), float(hist_line.iloc[-1])
            if prev <= 0 < now:
                t.macd_cross = "bullish"
            elif prev >= 0 > now:
                t.macd_cross = "bearish"

        t.ema12 = round(float(ema12.iloc[-1]), 2)
        t.ema26 = round(float(ema26.iloc[-1]), 2)
        t.ema50 = round(float(close.ewm(span=50, adjust=False).mean().iloc[-1]), 2)
        if len(close) >= 20: t.sma20 = round(float(close.rolling(20).mean().iloc[-1]), 2)
        if len(close) >= 50: t.sma50 = round(float(close.rolling(50).mean().iloc[-1]), 2)
        if len(close) >= 200: t.sma200 = round(float(close.rolling(200).mean().iloc[-1]), 2)

        # ATR needs high/low — unavailable from PSX EOD; stays None (shown n/a).
        if volumes is not None:
            vols = volumes.dropna()
            if len(vols):
                t.vol = float(vols.iloc[-1])
                if len(vols) >= 20:
                    t.vol_avg20 = float(vols.rolling(20).mean().iloc[-1])
                    if t.vol_avg20:
                        t.vol_ratio = round(t.vol / t.vol_avg20, 2)

        t.support = round(float(close.tail(20).min()), 2)
        t.resistance = round(float(close.tail(20).max()), 2)
        t.pivot = round(t.price, 2)

        _signal(t)
    except Exception as e:  # noqa: BLE001
        t.error = str(e)
    return t


def compute(ticker: str) -> Technicals:
    t = Technicals(ticker=ticker)
    try:
        import yfinance as yf
        import pandas as pd  # noqa
    except ImportError:
        t.error = "yfinance/pandas not installed"
        return t
    try:
        hist = yf.Ticker(ticker).history(period="1y")
        if hist is None or hist.empty:
            t.error = "no price history"
            return t
        close = hist["Close"].dropna()
        high, low, vol = hist["High"], hist["Low"], hist["Volume"]
        if len(close) < 30:
            t.error = "insufficient history"
            return t

        t.price = float(close.iloc[-1])

        # RSI(14), Wilder smoothing
        delta = close.diff()
        gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
        rs = gain / loss
        t.rsi = round(float((100 - 100 / (1 + rs)).iloc[-1]), 1)

        # MACD(12,26,9)
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        hist_line = macd - signal
        t.macd = round(float(macd.iloc[-1]), 3)
        t.macd_signal = round(float(signal.iloc[-1]), 3)
        t.macd_hist = round(float(hist_line.iloc[-1]), 3)
        if len(hist_line) >= 2:
            prev, now = float(hist_line.iloc[-2]), float(hist_line.iloc[-1])
            if prev <= 0 < now:
                t.macd_cross = "bullish"
            elif prev >= 0 > now:
                t.macd_cross = "bearish"

        # Moving averages
        t.ema12 = round(float(ema12.iloc[-1]), 2)
        t.ema26 = round(float(ema26.iloc[-1]), 2)
        t.ema50 = round(float(close.ewm(span=50, adjust=False).mean().iloc[-1]), 2)
        if len(close) >= 20: t.sma20 = round(float(close.rolling(20).mean().iloc[-1]), 2)
        if len(close) >= 50: t.sma50 = round(float(close.rolling(50).mean().iloc[-1]), 2)
        if len(close) >= 200: t.sma200 = round(float(close.rolling(200).mean().iloc[-1]), 2)

        # ATR(14)
        prev_close = close.shift(1)
        tr = pd.concat([(high - low), (high - prev_close).abs(),
                        (low - prev_close).abs()], axis=1).max(axis=1)
        t.atr = round(float(tr.ewm(alpha=1/14, adjust=False).mean().iloc[-1]), 2)
        if t.price:
            t.atr_pct = round(t.atr / t.price * 100, 2)

        # Volume
        t.vol = float(vol.iloc[-1])
        t.vol_avg20 = float(vol.rolling(20).mean().iloc[-1]) if len(vol) >= 20 else None
        if t.vol_avg20:
            t.vol_ratio = round(t.vol / t.vol_avg20, 2)

        # Support / resistance: recent 20-day range + classic pivot from last bar
        t.support = round(float(low.tail(20).min()), 2)
        t.resistance = round(float(high.tail(20).max()), 2)
        h, l, c = float(high.iloc[-1]), float(low.iloc[-1]), float(close.iloc[-1])
        t.pivot = round((h + l + c) / 3, 2)

        _signal(t)
    except Exception as e:  # noqa: BLE001
        t.error = str(e)
    return t


def _signal(t: Technicals) -> None:
    score = 0
    R = t.reasons
    p = t.price

    # Trend vs moving averages
    if p and t.sma50 and t.sma200:
        if p > t.sma50 and p > t.sma200:
            score += 2; t.trend = "up"
            R.append("Price above both 50- & 200-day SMA — uptrend intact")
        elif p < t.sma50 and p < t.sma200:
            score -= 2; t.trend = "down"
            R.append("Price below both 50- & 200-day SMA — downtrend")
        else:
            t.trend = "sideways"
            R.append("Price between 50- & 200-day SMA — mixed / sideways")
        if t.sma50 > t.sma200:
            score += 1; R.append("50-day above 200-day SMA (golden-cross regime)")
        else:
            score -= 1; R.append("50-day below 200-day SMA (death-cross regime)")

    # MACD momentum
    if t.macd_hist is not None:
        if t.macd_hist > 0:
            score += 1; R.append(f"MACD histogram positive ({t.macd_hist}) — bullish momentum")
        elif t.macd_hist < 0:
            score -= 1; R.append(f"MACD histogram negative ({t.macd_hist}) — bearish momentum")
    if t.macd_cross == "bullish":
        score += 1; R.append("MACD just crossed above signal — bullish trigger")
    elif t.macd_cross == "bearish":
        score -= 1; R.append("MACD just crossed below signal — bearish trigger")

    # RSI
    if t.rsi is not None:
        if t.rsi >= 70:
            score -= 1; R.append(f"RSI {t.rsi} — overbought, pullback risk")
        elif t.rsi <= 30:
            score += 1; R.append(f"RSI {t.rsi} — oversold, bounce potential")
        elif t.rsi >= 55:
            R.append(f"RSI {t.rsi} — firm momentum")
        elif t.rsi <= 45:
            R.append(f"RSI {t.rsi} — soft momentum")

    # Volume confirmation
    if t.vol_ratio is not None and t.vol_ratio >= 1.2:
        R.append(f"Volume {t.vol_ratio}× the 20-day average — move is confirmed")

    t.score = score
    if score >= 4: t.bias, t.signal = "bullish", "strong buy"
    elif score >= 2: t.bias, t.signal = "bullish", "buy"
    elif score <= -4: t.bias, t.signal = "bearish", "strong sell"
    elif score <= -2: t.bias, t.signal = "bearish", "sell"
    else: t.bias, t.signal = "neutral", "hold"
