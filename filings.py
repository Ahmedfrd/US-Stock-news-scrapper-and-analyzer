"""
filings.py — pull recent SEC filings straight from EDGAR (free, official, no key).

When a company reports, the real substance — results, guidance, and management's
discussion (MD&A) — lands in an 8-K (earnings press release) or 10-Q/10-K. This
module finds the latest such filing, grabs its text, and hands an excerpt to the
analyzer so the LLM can summarise the results, the business outlook, and the
management commentary, with a link to the primary source.

SEC asks that automated requests send a descriptive User-Agent with contact info
and stay under 10 req/sec. Set SEC_USER_AGENT="Your Name your@email.com" — it's
strongly recommended (some requests are rejected without a real UA).
"""

from __future__ import annotations

import os
import datetime as dt
import requests
from bs4 import BeautifulSoup

_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
_ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"

_INTERESTING = {"8-K", "10-Q", "10-K", "10-K/A", "10-Q/A"}
_cik_cache: dict | None = None


def _ua() -> dict:
    return {"User-Agent": os.environ.get("SEC_USER_AGENT",
            "market-news-digest (contact: set SEC_USER_AGENT env)")}


def _load_cik_map():
    global _cik_cache
    if _cik_cache is not None:
        return _cik_cache
    try:
        r = requests.get(_TICKER_MAP_URL, headers=_ua(), timeout=30)
        r.raise_for_status()
        data = r.json()
        _cik_cache = {row["ticker"].upper(): str(row["cik_str"]).zfill(10)
                      for row in data.values()}
    except Exception:
        _cik_cache = {}
    return _cik_cache


def latest_filing(ticker: str, within_days: int = 8, max_chars: int = 6000) -> dict | None:
    """Return {form, filed, url, excerpt} for the most recent interesting filing
    within `within_days`, or None."""
    cik = _load_cik_map().get(ticker.upper())
    if not cik:
        return None
    try:
        r = requests.get(_SUBMISSIONS.format(cik=cik), headers=_ua(), timeout=30)
        r.raise_for_status()
        recent = r.json().get("filings", {}).get("recent", {})
    except Exception:
        return None

    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accs = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])
    cutoff = dt.date.today() - dt.timedelta(days=within_days)

    for i, form in enumerate(forms):
        if form not in _INTERESTING:
            continue
        try:
            filed = dt.date.fromisoformat(dates[i])
        except Exception:
            continue
        if filed < cutoff:
            continue  # list is newest-first, so we can stop soon after, but keep simple
        acc = accs[i].replace("-", "")
        url = _ARCHIVE.format(cik=int(cik), acc=acc, doc=docs[i])
        excerpt = _extract(url, max_chars)
        return {"form": form, "filed": dates[i], "url": url, "excerpt": excerpt}
    return None


def _extract(url: str, max_chars: int) -> str:
    try:
        r = requests.get(url, headers=_ua(), timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "table"]):
            tag.decompose()
        text = soup.get_text(" ", strip=True)
        text = " ".join(text.split())
        # Prefer the section around MD&A / outlook if present.
        for marker in ["Management's Discussion", "Management’s Discussion",
                       "Outlook", "Business Outlook", "Guidance"]:
            idx = text.find(marker)
            if idx != -1:
                return text[idx: idx + max_chars]
        return text[:max_chars]
    except Exception:
        return ""
