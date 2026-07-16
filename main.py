#!/usr/bin/env python3
"""
main.py — build TWO reports and send them as two separate emails:
  * Portfolio Digest — your holdings, full depth
  * Market Digest    — general market, sector highlights, and stocks to watch
                       (which get the SAME full analysis as your holdings)

    python main.py                 # full run (sends both)
    python main.py --dry-run       # build & save & print both, send nothing
    python main.py --weekly        # force the weekly sector deep-dive
    python main.py --config x.yaml
"""

from __future__ import annotations

import os, sys, argparse, datetime as dt
import yaml

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import sources, fundamentals, etf as etf_mod, technicals as tech_mod
import sentiment, social, analyzer, digest, delivery, portfolio, shariah
try:
    import pk
except ImportError:
    pk = None
try:
    import pk_social
except ImportError:
    pk_social = None
try:
    import market_data
except ImportError:
    market_data = None
try:
    import filings
except ImportError:
    filings = None

_WEEKDAYS = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
_DEFAULT_FLAGS = [("Brent","BZ=F"),("WTI","CL=F"),("Gold","GC=F"),("US Dollar (DXY)","DX-Y.NYB"),
                  ("S&P 500","^GSPC"),("Nasdaq","^IXIC"),("10Y yield","^TNX"),
                  ("Bitcoin","BTC-USD"),("Ethereum","ETH-USD")]


def load_config(path):
    with open(path,"r",encoding="utf-8") as f: return yaml.safe_load(f)

def is_weekly(config, forced):
    if forced or os.environ.get("RUN_MODE","").lower()=="weekly": return True
    return _WEEKDAYS[dt.date.today().weekday()] == config.get("weekly_sector_day","Fri")

def is_market_day(config, args):
    """Market Digest cadence: 'daily' (the default) sends it every run; set
    market_digest_day to a UTC weekday name (e.g. Thu) to make it weekly.
    Manual runs (workflow_dispatch / --dry-run / --force-market) always
    produce it so testing is predictable."""
    day = str(config.get("market_digest_day", "daily") or "daily").strip()
    if day.lower() == "daily": return True
    if args.dry_run or args.force_market: return True
    if os.environ.get("GITHUB_EVENT_NAME","") == "workflow_dispatch": return True
    return _WEEKDAYS[dt.date.today().weekday()] == day

def _market_flags(instruments):
    if instruments is None: pairs=[{"name":n,"symbol":s} for n,s in _DEFAULT_FLAGS]
    elif not instruments: return []
    else: pairs=instruments
    out=[]
    try:
        import yfinance as yf
    except ImportError:
        return out
    for item in pairs:
        name=item.get("name") if isinstance(item,dict) else item[0]
        sym=item.get("symbol") if isinstance(item,dict) else item[1]
        try:
            h=yf.Ticker(sym).history(period="5d")["Close"].dropna()
            if len(h)>=2:
                last,prev=float(h.iloc[-1]),float(h.iloc[-2])
                out.append({"name":name,"price":round(last,2),"pct":round((last-prev)/prev*100,2) if prev else None})
        except Exception:
            continue
    return out


def _news_for(tk, name, lookback, max_items, fetch_full=False, full_limit=2):
    """ETF holdings and stocks-to-watch bypass sources.collect(), so they never
    got the full-article-body treatment collect() applies to portfolio holdings —
    they were stuck with a 1-2 sentence RSS/Finnhub blurb, which is why the AI
    could only paraphrase headlines instead of explaining the actual news. Fetch
    the real article body for the top few items here too."""
    arts = sources.finnhub_news(tk, max_items, lookback)
    if not arts:
        arts = sources.google_news(f'{name} stock OR "{tk}"', tk, "stock", max_items, lookback)
    if fetch_full:
        n = 0
        for a in arts:
            if n >= full_limit:
                break
            if not a.url or "news.google.com" in a.url:
                continue
            body, final_url = sources.fetch_article_text(a.url)
            if final_url and "finnhub.io" not in final_url:
                a.url = final_url
            if body:
                a.summary = (a.summary + " " + body).strip()[:1800]
                n += 1
    return arts


def _gather_stock(tk, name, lookback, max_items, benchmark, peers=None, market="US", fetch_full=False):
    """Fetch fundamentals/technicals/earnings/filing/etf + news for one ticker."""
    if market == "PK":
        return {"news": sources.google_news(f'"{tk}" PSX OR "{name}" Pakistan stock',
                                            tk, "stock", max_items, lookback),
                "fund": pk.fetch_fundamentals(tk, name),
                "tech": pk.technicals(tk),
                "earn": None, "fil": None, "etf": None, "holding_news": {}}
    data = {"news": _news_for(tk, name, lookback, max_items, fetch_full=fetch_full),
            "fund": fundamentals.fetch(tk, name),
            "tech": tech_mod.compute(tk),
            "earn": market_data.earnings_window(tk) if (market_data and market_data.enabled()) else None,
            "fil": filings.latest_filing(tk) if filings else None,
            "etf": None, "holding_news": {}}
    if data["fund"].is_etf:
        prof = etf_mod.enrich(tk, benchmark=benchmark, peer_tickers=peers)
        data["etf"] = prof
        for h in prof.holdings[:8]:
            arts = _news_for(h.symbol, h.name or h.symbol, lookback, 4, fetch_full=fetch_full, full_limit=1)
            if arts: data["holding_news"][h.symbol] = arts
    return data


def _crypto_snapshot(symbols):
    """[{symbol, price, pct(1d), pct7d}] for major coins via yfinance (SYM-USD)."""
    out = []
    try:
        import yfinance as yf
    except ImportError:
        return out
    for sym in symbols:
        try:
            h = yf.Ticker(f"{sym}-USD").history(period="8d")["Close"].dropna()
            if len(h) >= 2:
                last, prev = float(h.iloc[-1]), float(h.iloc[-2])
                row = {"symbol": sym, "price": round(last, 2),
                       "pct": round((last - prev) / prev * 100, 2) if prev else None,
                       "pct7d": None}
                if len(h) >= 8:
                    w = float(h.iloc[0])
                    row["pct7d"] = round((last - w) / w * 100, 2) if w else None
                out.append(row)
        except Exception:
            continue
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--weekly", action="store_true")
    ap.add_argument("--force-market", action="store_true",
                    help="build the Market Digest even off its weekly day")
    args = ap.parse_args()
    config = load_config(args.config)
    print("[env] " + " | ".join(
        f"{k}={'set' if os.environ.get(k) else 'MISSING'}"
        for k in ["GEMINI_API_KEY", "FINNHUB_API_KEY", "ADANOS_API_KEY",
                  "SEC_USER_AGENT", "EMAIL_PASSWORD"]))
    stocks = config.get("watchlist", {}).get("stocks", [])
    weekly = is_weekly(config, args.weekly)
    make_market = is_market_day(config, args)
    if not make_market:
        print(f"[market] Market Digest is set to weekly (market_digest_day: "
              f"{config.get('market_digest_day')} UTC) — today is Portfolio Digest only.")
    benchmark = config.get("etf_benchmark", "SPY")
    lookback = int(config.get("sources", {}).get("lookback_hours", 24))
    max_items = int(config.get("sources", {}).get("max_items_per_query", 8))
    fetch_full = bool(config.get("sources", {}).get("fetch_full_articles", False))

    print("[1/6] Collecting news…")
    items = sources.collect(config)
    print(f"      {len(items)} headlines.")

    market = (config.get("market") or "US").upper()
    print(f"[2/6] Portfolio data ({market}: fundamentals/technicals"
          + ("/etf/earnings/filings" if market == "US" else " via PSX") + ")…")
    technicals, earnings, fils, etf_profiles, etf_hnews = {}, {}, {}, {}, {}
    if market == "PK":
        # PSX: free official data portal. No Finnhub/EDGAR/ETF-internals coverage.
        funds = {}
        for s in stocks:
            tk = s.get("ticker", "").strip()
            if not tk: continue
            funds[tk] = pk.fetch_fundamentals(tk, s.get("name", ""))
            technicals[tk] = pk.technicals(tk)
    else:
        funds = fundamentals.fetch_many(stocks)
        for s in stocks:
            tk = s.get("ticker","").strip()
            if not tk: continue
            technicals[tk] = tech_mod.compute(tk)
            if market_data and market_data.enabled():
                earnings[tk] = market_data.earnings_window(tk)
            if filings:
                fl = filings.latest_filing(tk)
                if fl: fils[tk] = fl
            if funds.get(tk) and funds[tk].is_etf:
                prof = etf_mod.enrich(tk, benchmark=benchmark, peer_tickers=s.get("peers"))
                etf_profiles[tk] = prof
                for h in prof.holdings[:8]:
                    arts = _news_for(h.symbol, h.name or h.symbol, lookback, 4, fetch_full=fetch_full, full_limit=1)
                    if arts: etf_hnews.setdefault(tk, {})[h.symbol] = arts

    print(f"[3/6] Sentiment + crowd ({'Reddit' if market=='PK' else 'Adanos'}) + market flags…")
    sent = sentiment.aggregate(items)
    port_tickers = [s.get("ticker","").strip() for s in stocks if s.get("ticker")]
    name_map = {s.get("ticker","").strip().upper(): s.get("name","") for s in stocks if s.get("ticker")}
    crowd = {}
    if config.get("sources", {}).get("social", True):
        try:
            if market == "PK" and pk_social:
                crowd = pk_social.crowd(port_tickers, names=name_map,
                                        subs=config.get("sources", {}).get("reddit_subs"))
            elif social:
                crowd = social.crowd(port_tickers, config.get("sources", {}).get("adanos_platforms"))
        except Exception as ex: print(f"      crowd unavailable ({ex})")
    flags = _market_flags(config.get("market_flags"))

    sector_news = {}
    if weekly:
        print("[weekly] Sector developments…")
        secs = sorted({(funds[s['ticker']].sector or '').strip() for s in stocks
                       if s.get('ticker') in funds and funds[s['ticker']].sector}
                      | set(config.get("watchlist", {}).get("topics", [])))
        sector_news = sources.sector_news([x for x in secs if x])

    # ---- Crypto (major coins only) — gathered before analysis ----
    crypto_watch = config.get("crypto_watch", ["BTC", "ETH", "SOL", "XRP", "BNB"])
    crypto = {}
    if crypto_watch:
        print(f"[crypto] Snapshot + news for {len(crypto_watch)} major coin(s)…")
        cnews = sources.google_news("cryptocurrency market bitcoin ethereum",
                                    "Crypto", "topic", max_items, lookback) \
            if config.get("sources", {}).get("google_news", True) else []
        csent = {}
        if config.get("sources", {}).get("social", True):
            try: csent = social.crypto_sentiment(crypto_watch)
            except Exception: csent = {}
        crypto = {"snapshot": _crypto_snapshot(crypto_watch), "news": cnews, "sentiment": csent}
        # technical analysis per coin (shown collapsed in the report)
        ctech = {}
        for sym in crypto_watch:
            try: ctech[sym] = tech_mod.compute(f"{sym}-USD")
            except Exception: pass
        crypto["technicals"] = ctech
        items = items + cnews

    extras = {"sentiment": sentiment.aggregate(items), "crowd": crowd, "earnings": earnings,
              "filings": fils, "weekly": weekly, "sector_news": sector_news, "etf": etf_profiles,
              "etf_holding_news": etf_hnews, "technicals": technicals, "flags": flags,
              "crypto": crypto, "region": market,
              "look_through": portfolio.look_through(stocks, funds, etf_profiles)}

    print("[4/6] Analyzing portfolio…")
    port_analysis, status = analyzer.analyze(items, funds, extras, config)
    print(f"      engine: {status['engine']} (ok={status['ok']})")

    # ---- Stage 2: fully analyse AI-derived stocks to watch ----
    # (Market-Digest-only content — skipped on non-market days to save quota.)
    shariah_only = config.get("shariah_only", False)
    watch = [w for w in (port_analysis.get("stocks_to_watch") or [])
             if w.get("ticker") and w["ticker"].upper() not in {t.upper() for t in port_tickers}]
    # Defense in depth alongside the prompt: the US Market Digest must not carry
    # foreign-exchange listings (KRW/JPY prices, no Adanos/Finnhub coverage).
    # Class shares like BRK.B stay; Yahoo exchange suffixes and numeric roots go.
    _FOREIGN_SFX = {"KS","KQ","T","AS","DE","F","PA","L","TO","V","HK","SS","SZ","TW","TWO",
                    "SI","AX","NZ","SW","MI","MC","ST","OL","CO","HE","VI","BR","LS","SA",
                    "MX","NS","BO","IR","IS","JK","BK","KL"}
    def _is_foreign(tk):
        tk = tk.strip().upper()
        root, _, sfx = tk.rpartition(".")
        return (root and sfx in _FOREIGN_SFX) or tk.split(".")[0].isdigit()
    dropped = [w["ticker"] for w in watch if _is_foreign(w["ticker"])]
    if dropped:
        print(f"      dropped non-US listing(s) from stocks-to-watch: {', '.join(dropped)}")
    watch = [w for w in watch if not _is_foreign(w["ticker"])]
    if not make_market:
        watch = []
    watch = watch[:6]
    watch_analysis = {"stocks": []}
    watch_reasons, shariah_res = {}, {}
    if watch:
        print(f"[5/6] Deep-analysing stocks to watch"
              + (" (Shariah-screened)" if shariah_only else "") + "…")
        witems, kept = [], []
        for w in watch:
            tk = w["ticker"].strip()
            d = _gather_stock(tk, tk, lookback, max_items, benchmark, market=market, fetch_full=fetch_full)
            sc = shariah.screen(d["fund"])
            if shariah_only and sc["status"] == "fail":
                print(f"      dropped {tk} — {sc['reasons'][0]}")
                continue
            kept.append(w); shariah_res[tk] = sc
            witems += d["news"]
            funds[tk] = d["fund"]; technicals[tk] = d["tech"]
            if d["earn"]: earnings[tk] = d["earn"]
            if d["fil"]: fils[tk] = d["fil"]
            if d["etf"]: etf_profiles[tk] = d["etf"]; etf_hnews[tk] = d["holding_news"]
            if len(kept) >= 5:
                break
        watch = kept
        watch_reasons = {w["ticker"]: w.get("reason", "") for w in watch}
        wcrowd = {}
        if watch and config.get("sources", {}).get("social", True):
            try:
                if market == "PK" and pk_social:
                    wcrowd = pk_social.crowd([w["ticker"] for w in watch],
                                             names={w["ticker"].upper(): w["ticker"] for w in watch},
                                             subs=config.get("sources", {}).get("reddit_subs"))
                elif social:
                    wcrowd = social.crowd([w["ticker"] for w in watch], config.get("sources", {}).get("adanos_platforms"))
            except Exception: pass
        crowd.update(wcrowd)
        items = items + witems
        extras.update({"sentiment": sentiment.aggregate(items), "crowd": crowd,
                       "earnings": earnings, "filings": fils, "etf": etf_profiles,
                       "etf_holding_news": etf_hnews, "technicals": technicals,
                       "shariah": shariah_res})
        if watch:
            wconfig = {"watchlist": {"stocks": [{"ticker": w["ticker"], "name": w["ticker"]} for w in watch],
                                     "topics": []}, "analysis": config.get("analysis", {})}
            watch_analysis, _ = analyzer.analyze(items, funds, extras, wconfig)
    else:
        print("[5/6] No external stocks to watch this run.")

    # ETFs rarely get fund-level headlines (Finnhub company-news doesn't cover
    # them), so their news tone was always n/a — fall back to the tone of their
    # underlying holdings' headlines, labelled as such in the report.
    senti = extras.get("sentiment") or {}
    for tk, hn in (extras.get("etf_holding_news") or {}).items():
        if senti.get(tk, {}).get("n"):
            continue  # the fund had direct news after all
        scores = [sentiment.score_text(f"{a.title}. {a.summary}")
                  for arts in hn.values() for a in arts]
        scores = [s for s in scores if s is not None]
        if scores:
            avg = round(sum(scores) / len(scores), 3)
            senti[tk] = {"score": avg, "label": sentiment.label(avg),
                         "n": len(scores), "basis": "holdings"}

    print(f"[6/6] Building {'two reports' if make_market else 'the portfolio report'}…")
    # Optional three-layer bull/bear/judge debate on selected names.
    try:
        analyzer.run_debates(port_analysis, watch_analysis, funds, extras, items, config)
    except Exception as e:
        print(f"[debate] skipped ({e})")
    # Diagnose crowd availability so the report can explain a blank panel.
    if market == "PK":
        if any(c.get("has_data") for c in crowd.values()):
            extras["crowd_status"] = "ok"
        elif not config.get("sources", {}).get("social", True):
            extras["crowd_status"] = "ok"
        else:
            extras["crowd_status"] = "pk_thin"
    elif not os.environ.get("ADANOS_API_KEY"):
        extras["crowd_status"] = "no_key"
    elif any(c.get("has_data") for c in crowd.values()):
        extras["crowd_status"] = "ok"
    else:
        extras["crowd_status"] = "empty"
    out_dir = config.get("output_dir", "./digests")
    os.makedirs(out_dir, exist_ok=True)
    tag = "weekly" if weekly else "daily"
    reports = [
        ("portfolio", *digest.build_portfolio(port_analysis, items, funds, extras)),
    ]
    if make_market:
        reports.append(("market", *digest.build_market(port_analysis, watch_analysis,
                                                       items, funds, extras, watch_reasons)))
    for kind, subject, html_body, text_body in reports:
        path = os.path.join(out_dir, f"digest-{dt.date.today():%Y-%m-%d}-{kind}.html")
        with open(path, "w", encoding="utf-8") as f: f.write(html_body)
        print(f"      saved {path}")

    if args.dry_run:
        for kind, subject, html_body, text_body in reports:
            print("\n" + "="*64 + f"\n{kind.upper()}\n" + "="*64 + "\n" + text_body[:1500])
        print("\n[dry-run] Not sending.")
        return 0

    for kind, subject, html_body, text_body in reports:
        print(f"      delivering {kind}…")
        delivery.deliver(config, subject, html_body, text_body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
