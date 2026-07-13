"""
sentiment.py — free, offline news sentiment via VADER.

VADER is a lexicon+rules sentiment model (no API, no key, no network). It returns
a compound score in [-1, +1] per text. We average it across a stock's headlines
to get a numeric daily sentiment, separate from (and a cross-check on) the LLM's
qualitative read. Finnhub's own news-sentiment endpoint is premium, so this keeps
the feature genuinely free.

VADER is tuned for general/social text; finance jargon isn't its specialty, so
treat the number as a rough tilt, not gospel. It's most useful as a quantitative
signal alongside the LLM and the factor scores.
"""

from __future__ import annotations

from collections import defaultdict

try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    _ANALYZER = SentimentIntensityAnalyzer()
except Exception:  # pragma: no cover
    _ANALYZER = None

# A few finance-specific nudges VADER doesn't know well.
_EXTRA = {
    "beat": 2.5, "beats": 2.5, "tops": 2.0, "surge": 2.5, "soar": 2.5, "upgrade": 2.0,
    "outperform": 2.0, "raises": 1.5, "guidance": 0.0, "record": 1.5, "rally": 2.0,
    "miss": -2.5, "misses": -2.5, "plunge": -2.8, "slump": -2.2, "downgrade": -2.2,
    "cut": -1.5, "cuts": -1.5, "probe": -1.8, "lawsuit": -1.8, "recall": -2.0,
    "bankruptcy": -3.2, "layoffs": -2.0, "warning": -1.8, "halt": -1.5, "default": -2.5,
}
if _ANALYZER is not None:
    _ANALYZER.lexicon.update(_EXTRA)


def score_text(text: str) -> float:
    if not _ANALYZER or not text:
        return 0.0
    return _ANALYZER.polarity_scores(text)["compound"]


def label(score: float) -> str:
    if score >= 0.35:
        return "positive"
    if score <= -0.35:
        return "negative"
    if abs(score) < 0.1:
        return "neutral"
    return "mixed"


def aggregate(items) -> dict:
    """items: list of NewsItem -> {group: {'score','label','n'}} plus '_overall'."""
    by_group = defaultdict(list)
    for it in items:
        by_group[it.group].append(it)

    out = {}
    all_scores = []
    for group, grp in by_group.items():
        scores = [score_text(f"{i.title}. {i.summary}") for i in grp]
        scores = [s for s in scores if s is not None]
        avg = round(sum(scores) / len(scores), 3) if scores else 0.0
        out[group] = {"score": avg, "label": label(avg), "n": len(grp)}
        all_scores += scores
    overall = round(sum(all_scores) / len(all_scores), 3) if all_scores else 0.0
    out["_overall"] = {"score": overall, "label": label(overall), "n": len(all_scores)}
    return out
