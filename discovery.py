"""
discovery.py — turn whole-market news flow into concrete stock CANDIDATES.

Why this exists
---------------
"Stocks to watch" used to be picked out of news the digest had already scraped
for the portfolio — your tickers, your ETFs' holdings, your topics. That made
every recommendation portfolio-adjacent BY CONSTRUCTION: the AI could not name a
company whose news we had never fetched. This module closes that hole. It reads
the market-wide scan (sources.market_scan) and works out which companies the
day's stories are actually ABOUT, whether or not you have ever held them:

  1) explicit tickers in the text — "(NASDAQ: ABCD)", "(NYSE: ABC)", "$ABC"
  2) tickers the wire itself tagged (Finnhub's `related` field)
  3) company NAMES matched against the full US symbol directory

Each candidate is then priced (today's move, 5-day move, volume vs average) so
the AI is choosing between real, quantified movers rather than headlines alone.

Everything is free: Finnhub's symbol directory + yfinance for quotes. With no
FINNHUB_API_KEY the name-matching layer is unavailable, so we fall back to the
explicit-ticker patterns only, which still work.
"""

from __future__ import annotations

import re
from collections import defaultdict

# Symbols that look like tickers in prose but almost never are, plus common
# all-caps words that appear inside "$X"/"(NYSE: X)"-shaped false positives.
_TICKER_STOP = {
    "CEO", "CFO", "COO", "CTO", "USA", "USD", "GDP", "CPI", "PPI", "FED", "FOMC",
    "SEC", "FDA", "IPO", "ETF", "AI", "EPS", "YOY", "QOQ", "EBIT", "IRS", "DOJ",
    "FTC", "OPEC", "NATO", "EU", "UK", "US", "GMT", "EST", "PDT", "ADR", "NAV",
    "M&A", "ESG", "API", "EIA", "ISM", "PMI", "BLS", "NYSE", "AMEX", "OTC",
}

# Company "names" that collide with ordinary English once corporate suffixes are
# stripped. Matching these as company mentions produces pure noise.
_NAME_STOP = {
    "open", "gap", "key", "now", "real", "big", "one", "first", "new", "next",
    "core", "prime", "global", "national", "united", "american", "general",
    "capital", "growth", "value", "income", "trust", "partners", "holdings",
    "group", "industries", "technologies", "solutions", "systems", "services",
    "international", "resources", "energy", "financial", "health", "medical",
    "power", "water", "gold", "silver", "china", "india", "japan", "europe",
    "sun", "star", "summit", "eagle", "liberty", "heritage", "pioneer", "vision",
}

_SUFFIXES = re.compile(
    r"\b(inc|incorporated|corp|corporation|co|company|ltd|limited|plc|llc|lp|"
    r"nv|sa|ag|se|holdings?|group|the|class|cl|common|stock|shares?|adr|ads|"
    r"depositary|receipts?|sponsored|ordinary|units?|reit|trust|fund|etf)\b\.?",
    re.I)

_EXPLICIT = re.compile(
    r"\((?:NASDAQ|NYSE|NYSEAMERICAN|NYSE American|AMEX|BATS|CBOE|OTC)[:\s]+([A-Z][A-Z.\-]{0,5})\)"
    r"|\$([A-Z][A-Z.\-]{0,5})\b",
    re.I)


def _norm_name(desc: str) -> str:
    """'ADVANCED MICRO DEVICES INC' -> 'advanced micro devices'."""
    s = (desc or "").lower()
    s = re.sub(r"[^\w\s&.\-]", " ", s)
    s = _SUFFIXES.sub(" ", s)
    s = re.sub(r"[.\-]", " ", s)
    words = s.split()
    # Share-class markers leave a dangling letter behind ("PALANTIR TECHNOLOGIES
    # INC CLASS A" -> "palantir technologies a"), which then fails to match the
    # plain company name as it appears in a headline. Drop those trailing stubs.
    while words and len(words[-1]) == 1:
        words.pop()
    return " ".join(words)


def _symbol_index():
    """Build (valid_tickers, {normalised company name: ticker}) from Finnhub's
    free US symbol directory. Returns empty structures without a key."""
    try:
        import market_data
    except ImportError:
        return set(), {}
    rows = market_data.us_symbols() or []
    valid, by_name = set(), {}
    for r in rows:
        sym = (r.get("symbol") or "").strip().upper()
        typ = (r.get("type") or "").strip()
        if not sym or sym.count(".") > 1 or len(sym) > 6:
            continue
        # Common stock, ADRs and ETPs only — drop warrants, rights, units, bonds.
        if typ and typ not in ("Common Stock", "ADR", "ETP", "REIT", "Equity"):
            continue
        valid.add(sym)
        name = _norm_name(r.get("description", ""))
        if len(name) < 4 or name in _NAME_STOP:
            continue
        prev = by_name.get(name)
        # Share classes collide (GOOG/GOOGL). Prefer the shorter, dot-free symbol.
        if prev is None or (len(sym), "." in sym) < (len(prev), "." in prev):
            by_name[name] = sym
    return valid, by_name


def _phrases(text: str, max_words: int = 4):
    """Yield lowercase 1..max_words-word n-grams from a headline/summary."""
    words = re.sub(r"[^\w\s&\-]", " ", (text or "").lower()).split()
    for i in range(len(words)):
        for n in range(1, max_words + 1):
            if i + n <= len(words):
                yield " ".join(words[i:i + n])


def extract(items, exclude=(), limit: int = 30) -> list[dict]:
    """Companies the market-wide news is actually about.

    Returns [{ticker, name, mentions, headline, url, source}] ranked by how many
    distinct stories name them. `exclude` drops names already covered elsewhere
    in the report (your holdings) — everything else is fair game.
    """
    valid, by_name = _symbol_index()
    excl = {str(t).strip().upper() for t in exclude if t}
    hits = defaultdict(lambda: {"mentions": 0, "stories": []})

    for it in items:
        text = f"{it.title} {(it.summary or '')[:600]}"
        found = set()

        # 1) tickers the wire tagged itself
        for tk in (getattr(it, "related", "") or "").split(","):
            tk = tk.strip().upper()
            if tk and (not valid or tk in valid):
                found.add(tk)

        # 2) explicit "(NASDAQ: ABCD)" / "$ABCD"
        for m in _EXPLICIT.finditer(text):
            tk = (m.group(1) or m.group(2) or "").upper()
            if tk and tk not in _TICKER_STOP and (not valid or tk in valid):
                found.add(tk)

        # 3) company names — headline only, where the subject of the story lives
        if by_name:
            for ph in _phrases(it.title):
                tk = by_name.get(ph)
                if tk:
                    found.add(tk)

        for tk in found:
            if tk in excl or tk in _TICKER_STOP:
                continue
            h = hits[tk]
            h["mentions"] += 1
            if len(h["stories"]) < 3:
                h["stories"].append(it)

    out = []
    for tk, h in hits.items():
        top = h["stories"][0]
        out.append({"ticker": tk, "mentions": h["mentions"],
                    "headline": top.title, "url": top.url, "source": top.source,
                    "articles": h["stories"]})
    out.sort(key=lambda c: -c["mentions"])
    return out[:limit]


def price_moves(tickers: list[str]) -> dict:
    """{ticker: {price, pct_1d, pct_5d, vol_ratio}} in ONE batched yfinance call."""
    out = {}
    tickers = [t for t in tickers if t]
    if not tickers:
        return out
    try:
        import yfinance as yf
    except ImportError:
        return out
    try:
        data = yf.download(" ".join(tickers), period="1mo", interval="1d",
                           group_by="ticker", auto_adjust=False, progress=False,
                           threads=True)
    except Exception:
        return out
    for tk in tickers:
        try:
            df = data[tk] if len(tickers) > 1 else data
            closes = df["Close"].dropna()
            if len(closes) < 2:
                continue
            last, prev = float(closes.iloc[-1]), float(closes.iloc[-2])
            row = {"price": round(last, 2),
                   "pct_1d": round((last - prev) / prev * 100, 2) if prev else None,
                   "pct_5d": None, "vol_ratio": None}
            if len(closes) >= 6:
                w = float(closes.iloc[-6])
                row["pct_5d"] = round((last - w) / w * 100, 2) if w else None
            try:
                vols = df["Volume"].dropna()
                if len(vols) >= 10:
                    avg = float(vols.iloc[-21:-1].mean())
                    row["vol_ratio"] = round(float(vols.iloc[-1]) / avg, 2) if avg else None
            except Exception:
                pass
            out[tk] = row
        except Exception:
            continue
    return out


def scan(items, exclude=(), limit: int = 25, min_move: float = 0.0) -> list[dict]:
    """Full pipeline: market-wide news -> named companies -> priced candidates.

    Ranked by how newsworthy AND how mobile the name is, so what reaches the AI
    is a shortlist of real movers with a story attached.
    """
    cands = extract(items, exclude=exclude, limit=limit * 2)
    if not cands:
        return []
    moves = price_moves([c["ticker"] for c in cands])
    for c in cands:
        c.update(moves.get(c["ticker"], {}))
    # Keep names we could actually price — an unpriceable symbol is usually a
    # bad match (a fund, a delisted shell, or a false-positive name hit).
    priced = [c for c in cands if c.get("price") is not None]
    if min_move:
        priced = [c for c in priced if abs(c.get("pct_1d") or 0) >= min_move]
    priced.sort(key=lambda c: (-(c["mentions"]), -abs(c.get("pct_1d") or 0)))
    return priced[:limit]
