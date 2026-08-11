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

# Broad, whole-market feeds. These are NOT tied to the watchlist — they exist so
# the digest sees the day's news for companies you don't own, which is where
# genuinely new "stocks to watch" come from.
BROAD_FEEDS = [
    ("CNBC Top News",   "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("CNBC Markets",    "https://www.cnbc.com/id/20910258/device/rss/rss.html"),
    ("CNBC Earnings",   "https://www.cnbc.com/id/15839135/device/rss/rss.html"),
    ("Yahoo Finance",   "https://finance.yahoo.com/news/rssindex"),
    ("MarketWatch",     "https://feeds.marketwatch.com/marketwatch/topstories/"),
    ("Investing.com",   "https://www.investing.com/rss/news_25.rss"),
    ("Seeking Alpha",   "https://seekingalpha.com/market_currents.xml"),
]

# Catalyst-shaped searches across the WHOLE market. Each targets the kind of
# story that makes a stock actionable in the short term, regardless of whether
# the company has anything to do with the portfolio.
CATALYST_QUERIES = [
    "stock surges OR soars OR jumps after",
    "stock plunges OR sinks OR tumbles after",
    "analyst upgrade price target raised stock",
    "analyst downgrade price target cut stock",
    "earnings beat raises guidance shares",
    "earnings miss cuts guidance shares",
    "merger OR acquisition OR takeover deal shares",
    "FDA approval OR clinical trial results shares",
    "SEC investigation OR lawsuit OR recall shares",
    "IPO debut shares first day trading",
    "biggest stock movers today",
    "52-week high OR breakout stock",
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
    group_type: str       # "stock" | "topic" | "macro" | "market"
    related: str = ""     # comma-separated tickers the source itself tagged (Finnhub)

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


def _recent(published: dt.datetime | None, lookback_hours: int,
            keep_undated: bool = False) -> bool:
    # For a freshness digest, an item we can't date is a liability, not a bonus:
    # week-old stories slip through when their timestamp fails to parse. Default
    # is now to DROP undated items. Macro/official feeds pass keep_undated=True
    # because their releases are inherently recent even when the feed omits a date.
    if published is None:
        return keep_undated
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=lookback_hours)
    return published >= cutoff


def _entry_dt(entry) -> dt.datetime | None:
    # Try published, then updated — some feeds populate only one of them.
    for attr in ("published_parsed", "updated_parsed"):
        got = _parsed_to_dt(getattr(entry, attr, None))
        if got:
            return got
    return None


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
    # Window tracks the configured lookback instead of a hardcoded 2 days.
    when = f"{lookback_hours}h" if lookback_hours < 48 else f"{lookback_hours // 24}d"
    q = quote_plus(f"{query} when:{when}")
    url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
    feed = _parse_feed(url)
    items: list[NewsItem] = []
    for e in feed.entries[: max_items * 2]:
        published = _entry_dt(e)
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
    # Official macro releases are inherently current even when the feed omits a
    # timestamp, so those are allowed through undated; other feeds are not.
    keep_undated = (group_type == "macro")
    for e in feed.entries[: max_items * 2]:
        published = _entry_dt(e)
        if not _recent(published, lookback_hours, keep_undated=keep_undated):
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
def fetch_article_text(url: str, limit: int = 3500):
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


def marketaux_news(ticker: str, max_items: int, lookback_hours: int) -> list[NewsItem]:
    """Company news from Marketaux (free tier: 100 req/day). Ticker-tagged with a
    server-side published_after filter, so freshness is enforced at the source and
    every item arrives with a real timestamp. No-op without MARKETAUX_API_KEY."""
    import os
    key = os.environ.get("MARKETAUX_API_KEY")
    if not key:
        return []
    after = (dt.datetime.now(dt.timezone.utc)
             - dt.timedelta(hours=lookback_hours)).strftime("%Y-%m-%dT%H:%M")
    params = {
        "symbols": ticker,
        "filter_entities": "true",
        "language": "en",
        "published_after": after,
        "limit": max_items,
        "api_token": key,
    }
    try:
        r = requests.get("https://api.marketaux.com/v1/news/all",
                         params=params, headers=_UA, timeout=20)
        r.raise_for_status()
        data = r.json().get("data", [])
    except Exception:
        return []
    items: list[NewsItem] = []
    for n in data:
        published = None
        if n.get("published_at"):
            try:
                published = dt.datetime.fromisoformat(
                    n["published_at"].replace("Z", "+00:00"))
            except Exception:
                published = None
        if not _recent(published, lookback_hours):
            continue
        items.append(NewsItem(
            title=_clean(n.get("title", "")),
            url=n.get("url", ""),
            source=n.get("source", "Marketaux"),
            published=published,
            summary=_clean(n.get("description") or n.get("snippet") or ""),
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


def market_scan(config: dict) -> list[NewsItem]:
    """Whole-market news flow, independent of the watchlist.

    Everything else in this module scrapes news FOR names you already care about
    (your holdings, your ETFs' holdings, your topics). That made "stocks to
    watch" structurally portfolio-bound: the AI could only recommend companies
    whose news we had already gone looking for. This function scrapes the market
    at large — general market wires, broad business RSS, and catalyst-shaped
    searches — so a company you have never held can surface purely on its news.

    Items come back grouped as "Market scan" / group_type "market".
    """
    src = config.get("sources", {}) or {}
    ms = config.get("market_scan", {}) or {}
    if not ms.get("enabled", True):
        return []
    lookback = int(src.get("lookback_hours", 24))
    per_query = int(ms.get("max_items_per_query", 10))
    items: list[NewsItem] = []

    # 1) Finnhub general market news — real publisher links, dated, ticker-tagged.
    if ms.get("finnhub_general", True):
        try:
            import market_data
            if market_data.enabled():
                for n in market_data.general_news(max_items=int(ms.get("finnhub_max", 40))):
                    if not _recent(n.get("published"), lookback):
                        continue
                    it = NewsItem(title=n["title"], url=n.get("url", ""),
                                  source=n.get("source", "Finnhub"),
                                  published=n.get("published"),
                                  summary=n.get("summary", ""),
                                  group="Market scan", group_type="market")
                    # Finnhub tags its own tickers on general news — free, exact
                    # candidates that need no name matching at all.
                    it.related = n.get("related", "")
                    items.append(it)
        except ImportError:
            pass

    # 2) Broad business/market RSS.
    for name, url in (BROAD_FEEDS if ms.get("broad_feeds", True) else []):
        items += rss_feed(url, name, "Market scan", "market", per_query, lookback)
    for url in (ms.get("extra_rss") or []):
        items += rss_feed(url, "", "Market scan", "market", per_query, lookback)

    # 3) Catalyst searches across the whole market.
    queries = ms.get("catalyst_queries")
    if queries is None:
        queries = CATALYST_QUERIES if ms.get("catalyst_search", True) else []
    for q in queries:
        items += google_news(q, "Market scan", "market", per_query, lookback)

    # Dedupe by headline; keep the dated/newest copy.
    best: dict[str, NewsItem] = {}
    for it in items:
        if not it.title:
            continue
        k = it.key()
        cur = best.get(k)
        if cur is None or (it.published and (cur.published is None or it.published > cur.published)):
            best[k] = it
    out = list(best.values())
    _floor = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    out.sort(key=lambda it: it.published or _floor, reverse=True)
    return out[: int(ms.get("max_items", 220))]


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

    # Make the degraded path visible: if Finnhub is switched on in config but has
    # no working key, the digest silently falls back to Google News only. Say so.
    if src.get("finnhub", True):
        try:
            import market_data
            if not market_data.enabled():
                print("[sources] WARNING: finnhub enabled in config but "
                      "FINNHUB_API_KEY is missing — dated, direct-link company "
                      "news is OFF; running on Google News / RSS only.")
        except ImportError:
            pass

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
        if src.get("marketaux", True):
            items += marketaux_news(ticker, max_items, lookback)
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
            if per_group[it.group] >= 4:      # read the top few articles per name in FULL
                continue
            if not it.url or "news.google.com" in it.url:
                continue
            body, final_url = fetch_article_text(it.url)
            if final_url and "finnhub.io" not in final_url:
                it.url = final_url
            if body:
                # Keep a large slice of the real article body so the AI can explain
                # WHY a name moved (figures, guidance, deal terms), not just the headline.
                it.summary = (it.summary + " " + body).strip()[:3500]
                per_group[it.group] += 1

    # Deduplicate by (group, title). When the same story appears more than once,
    # keep the copy with the most recent timestamp rather than whichever arrived
    # first, so a dated version always wins over an undated duplicate.
    best: dict[tuple[str, str], NewsItem] = {}
    for it in items:
        if not it.title:
            continue
        k = (it.group, it.key())
        cur = best.get(k)
        if cur is None:
            best[k] = it
            continue
        # Prefer the one with a timestamp; if both dated, prefer the newer.
        if it.published and (cur.published is None or it.published > cur.published):
            best[k] = it
    deduped = list(best.values())

    # Newest first. Undated items sink to the bottom instead of floating to the top.
    _floor = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    deduped.sort(key=lambda it: it.published or _floor, reverse=True)

    return deduped
