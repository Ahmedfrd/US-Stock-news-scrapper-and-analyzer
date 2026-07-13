"""
social.py — multi-source crowd sentiment via ADANOS (https://api.adanos.org).

Adanos aggregates finance sentiment across FOUR stock sources, each with its own
compare endpoint (X-API-Key auth, free tier 250 req/mo, 100/min):
    Reddit      /reddit/stocks/v1/compare      (50+ subreddits)
    X/Twitter   /x/stocks/v1/compare           (FinTwit, Grok-analysed)
    News        /news/stocks/v1/compare        (Reuters/Benzinga/Finviz/…)
    Polymarket  /polymarket/stocks/v1/compare  (prediction-market implied)

Each returns per ticker: buzz_score (0-100), sentiment_score (-1..1), bullish_pct,
bearish_pct, mentions, trend. We query all enabled platforms with ONE batched
compare call each (tickers grouped in 10s) to stay well inside the free quota,
then build a per-source breakdown + a blended consensus per ticker.

No key → returns empty (crowd simply omitted). ADANOS_API_KEY required.
"""

from __future__ import annotations

import os
import requests

_BASE = "https://api.adanos.org/{platform}/stocks/v1/compare"
_PLATFORMS = {"reddit": "Reddit", "x": "X", "news": "News", "polymarket": "Polymarket"}


def _chunk(lst, n=10):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def _num(x):
    try: return float(x)
    except Exception: return None


def _rows(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("results") or data.get("data") or data.get("stocks") or []
    return []


def _platform(platform, tickers, key):
    """{ticker: {bullish,bearish,neutral,buzz,score,mentions,trend}} for one platform."""
    out = {}
    first = True
    for group in _chunk(tickers, 10):
        try:
            r = requests.get(_BASE.format(platform=platform),
                             params={"tickers": ",".join(group), "days": 7},
                             headers={"X-API-Key": key}, timeout=30)
            if first:
                print(f"[adanos] {platform}: HTTP {r.status_code} for {len(group)} tickers", flush=True)
                if r.status_code != 200:
                    print(f"[adanos] {platform}: body: {r.text[:200]}", flush=True)
                first = False
            if r.status_code != 200:
                continue
            for row in _rows(r.json()):
                tk = (row.get("ticker") or row.get("symbol") or "").upper()
                if not tk:
                    continue
                bull = _num(row.get("bullish_pct"))
                bear = _num(row.get("bearish_pct"))
                neutral = None
                if bull is not None and bear is not None:
                    neutral = max(0.0, round(100 - bull - bear, 1))
                out[tk] = {"bullish": bull, "bearish": bear, "neutral": neutral,
                           "buzz": _num(row.get("buzz_score")),
                           "score": _num(row.get("sentiment_score")),
                           "mentions": row.get("mentions") or row.get("total_mentions") or row.get("trade_count"),
                           "trend": row.get("trend")}
        except Exception:
            continue
    return out


def _label(bull, bear, score):
    if bull is not None and bear is not None:
        if bull - bear >= 12: return "bullish"
        if bear - bull >= 12: return "bearish"
        return "neutral"
    if score is not None:
        if score >= 0.15: return "bullish"
        if score <= -0.15: return "bearish"
        return "neutral"
    return "n/a"


def crypto_sentiment(symbols):
    """Crowd sentiment for major coins via Adanos reddit-crypto compare (one call).
    Returns {symbol: {consensus:{bullish,bearish,neutral,buzz,score,label}, has_data}}."""
    symbols = [s.upper() for s in symbols if s]
    key = os.environ.get("ADANOS_API_KEY")
    out = {s: {"has_data": False, "consensus": {}} for s in symbols}
    if not key or not symbols:
        return out
    for group in _chunk(symbols, 10):
        try:
            r = requests.get("https://api.adanos.org/reddit/crypto/v1/compare",
                             params={"tickers": ",".join(group), "days": 7},
                             headers={"X-API-Key": key}, timeout=30)
            if r.status_code != 200:
                continue
            for row in _rows(r.json()):
                tk = (row.get("ticker") or row.get("symbol") or row.get("token") or "").upper()
                if tk not in out:
                    continue
                bull = _num(row.get("bullish_pct")); bear = _num(row.get("bearish_pct"))
                neu = max(0.0, round(100 - bull - bear, 1)) if (bull is not None and bear is not None) else None
                score = _num(row.get("sentiment_score"))
                out[tk] = {"has_data": True,
                           "consensus": {"bullish": bull, "bearish": bear, "neutral": neu,
                                         "buzz": _num(row.get("buzz_score")), "score": score,
                                         "label": _label(bull, bear, score)}}
        except Exception:
            continue
    return out


def crowd(tickers, platforms=None):
    """Return {ticker: {has_data, sources:{name:{...}}, consensus:{bullish,bearish,
    neutral,buzz,label}}}."""
    tickers = [t.upper() for t in tickers if t]
    key = os.environ.get("ADANOS_API_KEY")
    out = {tk: {"has_data": False, "sources": {}, "consensus": {}} for tk in tickers}
    if not key or not tickers:
        if not key:
            print("[adanos] ADANOS_API_KEY NOT in environment → crowd sentiment disabled", flush=True)
        return out
    use = [p for p in (platforms or _PLATFORMS) if p in _PLATFORMS]
    print(f"[adanos] key present ({len(key)} chars); querying {use} for {len(tickers)} tickers", flush=True)
    per = {p: _platform(p, tickers, key) for p in use}
    print("[adanos] rows received: " + ", ".join(f"{p}={len(per[p])}" for p in use), flush=True)
    for tk in tickers:
        sources = {}
        for p in use:
            row = per[p].get(tk)
            if row and (row["buzz"] or row["mentions"] or row["score"] is not None
                        or row["bullish"] is not None):
                sources[_PLATFORMS[p]] = row
        if not sources:
            continue
        bulls = [s["bullish"] for s in sources.values() if s["bullish"] is not None]
        bears = [s["bearish"] for s in sources.values() if s["bearish"] is not None]
        buzzes = [s["buzz"] for s in sources.values() if s["buzz"] is not None]
        scores = [s["score"] for s in sources.values() if s["score"] is not None]
        cons_bull = round(sum(bulls) / len(bulls), 1) if bulls else None
        cons_bear = round(sum(bears) / len(bears), 1) if bears else None
        cons_neu = max(0.0, round(100 - cons_bull - cons_bear, 1)) if (cons_bull is not None and cons_bear is not None) else None
        cons_score = round(sum(scores) / len(scores), 3) if scores else None
        out[tk] = {"has_data": True, "sources": sources,
                   "consensus": {"bullish": cons_bull, "bearish": cons_bear, "neutral": cons_neu,
                                 "buzz": round(sum(buzzes) / len(buzzes)) if buzzes else None,
                                 "score": cons_score, "label": _label(cons_bull, cons_bear, cons_score)}}
    return out
