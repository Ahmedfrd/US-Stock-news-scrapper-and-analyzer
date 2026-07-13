"""
sources.py — collect news from free sources.

Sources used (all free, no paid API keys):
  * Google News RSS  — flexible search per stock and per topic
  * Yahoo Finance     — per-ticker headlines + a price snapshot (via yfinance)
  * Macro RSS feeds   — central-bank / official releases
  * Any extra RSS URLs listed in config

Everything is defensive: if one source fails, the rest still run.
"""

from __future__ import annotations

import time
import datetime as dt
from dataclasses import dataclass, field
from urllib.parse import quote_plus

import feedparser
import requests
from bs4 import BeautifulSoup

# Feeds that broadly cover macro / official releases.
MACRO_FEEDS = [
    ("Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("US Treasury",     "https://home.treasury.gov/rss/press.xml"),
    ("ECB",             "https://www.ecb.europa.eu/rss/press.html"),
]

_UA = {"User-Agent": "Mozilla/5.0 (compatible; MarketNewsDigest/1.0)"}


@dataclass
class NewsItem:
    title: str
    url: str
    source: str
    published: dt.datetime | None
    summary: str
    group: str            # e.g. "AAPL" or "semiconductor industry"
    group_type: str       # "stock" | "topic" | "macro"

    def key(self) -> str:
        return (self.title or "").strip().lower()[:120]


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #
def _clean(html_or_text: str) -> str:
    if not html_or_text:
        return ""
    text = BeautifulSoup(html_or_text, "html.parser").get_text(" ", strip=True)
    return " ".join(text.split())


def _parsed_to_dt(parsed) -> dt.datetime | None:
    if not parsed:
        return None
    try:
        return dt.datetime.fromtimestamp(time.mktime(parsed), tz=dt.timezone.utc)
    except Exception:
        return None


def _recent(published: dt.datetime | None, lookback_hours: int) -> bool:
    # Keep items with no timestamp (better to include than silently drop).
    if published is None:
        return True
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=lookback_hours)
    return published >= cutoff


def _parse_feed(url: str) -> feedparser.FeedParserDict:
    # Fetch with a UA header first (some feeds reject the default), fall back
    # to letting feedparser fetch it directly.
    try:
        resp = requests.get(url, headers=_UA, timeout=20)
        resp.raise_for_status()
        return feedparser.parse(resp.content)
    except Exception:
        return feedparser.parse(url)


# --------------------------------------------------------------------------- #
#  Google News RSS
# --------------------------------------------------------------------------- #
def google_news(query: str, group: str, group_type: str,
                max_items: int, lookback_hours: int) -> list[NewsItem]:
    q = quote_plus(f"{query} when:2d")
    url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
    feed = _parse_feed(url)
    items: list[NewsItem] = []
    for e in feed.entries[: max_items * 2]:
        published = _parsed_to_dt(getattr(e, "published_parsed", None))
        if not _recent(published, lookback_hours):
            continue
        source = ""
        if getattr(e, "source", None) is not None:
            source = getattr(e.source, "title", "") or ""
        items.append(NewsItem(
            title=_clean(getattr(e, "title", "")),
            url=getattr(e, "link", ""),
            source=source or "Google News",
            published=published,
            summary=_clean(getattr(e, "summary", "")),
            group=group,
            group_type=group_type,
        ))
        if len(items) >= max_items:
            break
    return items


# --------------------------------------------------------------------------- #
#  Yahoo Finance (yfinance)
# --------------------------------------------------------------------------- #
def yahoo_news(ticker: str, max_items: int, lookback_hours: int) -> list[NewsItem]:
    try:
        import yfinance as yf
    except ImportError:
        return []
    items: list[NewsItem] = []
    try:
        raw = yf.Ticker(ticker).news or []
    except Exception:
        return []

    for n in raw[: max_items * 2]:
        # yfinance has two schemas depending on version.
        content = n.get("content", n)
        title = content.get("title") or n.get("title") or ""
        url = ""
        for key in ("canonicalUrl", "clickThroughUrl"):
            val = content.get(key)
            if isinstance(val, dict) and val.get("url"):
                url = val["url"]
                break
        url = url or n.get("link", "")
        publisher = ""
        if isinstance(content.get("provider"), dict):
            publisher = content["provider"].get("displayName", "")
        publisher = publisher or n.get("publisher", "") or "Yahoo Finance"

        published = None
        if n.get("providerPublishTime"):
            published = dt.datetime.fromtimestamp(n["providerPublishTime"], tz=dt.timezone.utc)
        elif content.get("pubDate"):
            try:
                published = dt.datetime.fromisoformat(content["pubDate"].replace("Z", "+00:00"))
            except Exception:
                published = None

        if not title or not _recent(published, lookback_hours):
            continue
        items.append(NewsItem(
            title=_clean(title),
            url=url,
            source=publisher,
            published=published,
            summary=_clean(content.get("summary", "")),
            group=ticker,
            group_type="stock",
        ))
        if len(items) >= max_items:
            break
    return items


def price_snapshot(ticker: str) -> dict | None:
    """Return {'last':.., 'prev':.., 'pct':..} or None."""
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period="5d")
        closes = hist["Close"].dropna()
        if len(closes) < 2:
            return None
        last, prev = float(closes.iloc[-1]), float(closes.iloc[-2])
        pct = (last - prev) / prev * 100 if prev else 0.0
        return {"last": last, "prev": prev, "pct": pct}
    except Exception:
        return None


# --------------------------------------------------------------------------- #
#  Generic RSS (macro feeds + user extras)
# --------------------------------------------------------------------------- #
def rss_feed(url: str, source_name: str, group: str, group_type: str,
             max_items: int, lookback_hours: int) -> list[NewsItem]:
    feed = _parse_feed(url)
    items: list[NewsItem] = []
    for e in feed.entries[: max_items * 2]:
        published = _parsed_to_dt(getattr(e, "published_parsed", None))
        if not _recent(published, lookback_hours):
            continue
        items.append(NewsItem(
            title=_clean(getattr(e, "title", "")),
            url=getattr(e, "link", ""),
            source=source_name or getattr(feed.feed, "title", "") or "RSS",
            published=published,
            summary=_clean(getattr(e, "summary", "")),
            group=group,
            group_type=group_type,
        ))
        if len(items) >= max_items:
            break
    return items


# --------------------------------------------------------------------------- #
#  Optional full-article text (off by default)
# --------------------------------------------------------------------------- #
def fetch_article_text(url: str, limit: int = 1500):
    """Fetch article body. Follows redirects (e.g. Finnhub's finnhub.io links
    resolve to the real publisher). Returns (text, final_url)."""
    try:
        resp = requests.get(url, headers=_UA, timeout=20, allow_redirects=True)
        resp.raise_for_status()
        final_url = str(resp.url) or url
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "aside"]):
            tag.decompose()
        paras = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
        text = " ".join(p for p in paras if len(p) > 40)
        return " ".join(text.split())[:limit], final_url
    except Exception:
        return "", url


def _relevant(item, name_by_ticker) -> bool:
    """Keep a stock-tagged item only if it actually references that company —
    drops generic market-roundup noise Finnhub tags to big tickers."""
    if item.group_type != "stock":
        return True
    tk = item.group.upper()
    hay = f"{item.title} {item.summary}".lower()
    if tk.lower() in hay:
        return True
    name = (name_by_ticker.get(tk) or "").lower()
    # match on a distinctive word of the company name (len>3, not "inc/corp/etf")
    stop = {"inc", "corp", "co", "the", "ltd", "plc", "etf", "fund", "trust", "group", "holdings"}
    for w in name.replace(",", " ").replace(".", " ").split():
        if len(w) > 3 and w not in stop and w in hay:
            return True
    return False


# --------------------------------------------------------------------------- #
#  Orchestrator
# --------------------------------------------------------------------------- #
def finnhub_news(ticker: str, max_items: int, lookback_hours: int) -> list[NewsItem]:
    """Company news from Finnhub (real article URLs). No-op without a key."""
    try:
        import market_data
    except ImportError:
        return []
    if not market_data.enabled():
        return []
    days = max(1, lookback_hours // 24 + 1)
    items = []
    for n in market_data.company_news(ticker, lookback_days=days, max_items=max_items):
        if not _recent(n.get("published"), lookback_hours):
            continue
        items.append(NewsItem(
            title=n["title"], url=n.get("url", ""), source=n.get("source", "Finnhub"),
            published=n.get("published"), summary=n.get("summary", ""),
            group=ticker, group_type="stock"))
    return items


def sector_news(sectors: list[str], lookback_days: int = 7,
                max_items: int = 10) -> dict:
    """Weekly: gather ~7 days of news per sector. Returns {sector: [NewsItem]}."""
    out = {}
    for sec in sectors:
        if not sec:
            continue
        q = quote_plus(f"{sec} sector when:{lookback_days}d")
        url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
        feed = _parse_feed(url)
        arts = []
        for e in feed.entries[: max_items * 2]:
            src_title = ""
            if getattr(e, "source", None) is not None:
                src_title = getattr(e.source, "title", "") or ""
            arts.append(NewsItem(
                title=_clean(getattr(e, "title", "")), url=getattr(e, "link", ""),
                source=src_title or "Google News",
                published=_parsed_to_dt(getattr(e, "published_parsed", None)),
                summary=_clean(getattr(e, "summary", "")), group=sec, group_type="sector"))
            if len(arts) >= max_items:
                break
        out[sec] = arts
    return out


def collect(config: dict) -> list[NewsItem]:
    """Returns a deduplicated list of news items. (Prices/financials are handled
    separately by fundamentals.py.)"""
    src = config.get("sources", {})
    wl = config.get("watchlist", {})
    max_items = int(src.get("max_items_per_query", 8))
    lookback = int(src.get("lookback_hours", 24))

    items: list[NewsItem] = []

    stocks = wl.get("stocks", []) or []
    topics = wl.get("topics", []) or []

    for s in stocks:
        ticker = s.get("ticker", "").strip()
        name = s.get("name", ticker).strip()
        if not ticker:
            continue
        # Finnhub first: it returns DIRECT publisher links (best for reading the
        # full article and for reliable links). Google News links are redirect
        # wrappers, so they're used only to fill gaps.
        if src.get("finnhub", True):
            items += finnhub_news(ticker, max_items, lookback)
        if src.get("google_news", True):
            items += google_news(f'{name} stock OR "{ticker}"', ticker, "stock",
                                 max_items, lookback)
        if src.get("yahoo_finance", True):
            items += yahoo_news(ticker, max_items, lookback)

    for t in topics:
        if src.get("google_news", True):
            items += google_news(t, t, "topic", max_items, lookback)

    if src.get("macro_feeds", True):
        for name, url in MACRO_FEEDS:
            items += rss_feed(url, name, "Macro", "macro", max_items, lookback)

    for url in (src.get("extra_rss") or []):
        items += rss_feed(url, "", "Markets", "topic", max_items, lookback)

    # Relevance filter: drop generic market-roundup items Finnhub tags onto big
    # tickers (e.g. "3 meme stocks", "Trump made 327 trades") that don't actually
    # discuss the company. On by default; set sources.relevance_filter: false to keep all.
    if src.get("relevance_filter", True):
        name_by_ticker = {s.get("ticker", "").strip().upper(): s.get("name", "")
                          for s in stocks if s.get("ticker")}
        items = [it for it in items if _relevant(it, name_by_ticker)]

    # Optional article bodies. Reads the FULL article (not just the headline) for
    # up to 3 items per group. Google News links are redirect wrappers to a consent
    # page (skipped); Finnhub finnhub.io links are followed to the real publisher,
    # and we adopt that resolved URL so the link points straight to the article.
    if src.get("fetch_full_articles", False):
        from collections import defaultdict as _dd
        per_group = _dd(int)
        for it in items:
            if per_group[it.group] >= 3:
                continue
            if not it.url or "news.google.com" in it.url:
                continue
            body, final_url = fetch_article_text(it.url)
            if final_url and "finnhub.io" not in final_url:
                it.url = final_url
            if body:
                it.summary = (it.summary + " " + body).strip()[:1800]
                per_group[it.group] += 1

    # Deduplicate by (group, title)
    seen: set[tuple[str, str]] = set()
    deduped: list[NewsItem] = []
    for it in items:
        k = (it.group, it.key())
        if it.title and k not in seen:
            seen.add(k)
            deduped.append(it)

    return deduped
