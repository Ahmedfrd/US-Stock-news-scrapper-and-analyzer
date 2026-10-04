#!/usr/bin/env python3
"""
main.py — two emails on two cadences:

  * Market Digest US     DAILY (every scheduled run, Mon–Sat 07:00 HKT): what
                         happened in the market since the previous email — policy
                         & rates, macro prints, global, sectors, at most three
                         stocks to watch, crypto, and a dated calendar.
  * Portfolio Digest US  WEEKLY (on schedule.portfolio_day, default Sat HKT): the
                         week's news on your holdings, how each travelled over
                         the week, and a verdict that says what changed since
                         last week.

    python main.py                          # scheduled behaviour (market daily, portfolio weekly)
    python main.py --report both --dry-run  # build both, print, send nothing, save no state
    python main.py --report portfolio
    python main.py --no-ai --dry-run        # data + rule-based fallback only (no API keys needed)
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from zoneinfo import ZoneInfo

import yaml

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # noqa: BLE001
    pass

import sources, fundamentals, etf as etf_mod, technicals as tech_mod
import sentiment, social, analyzer, digest, delivery, portfolio, shariah
import prices, state as state_mod, econ_calendar
try:
    import discovery
except ImportError:
    discovery = None
try:
    import market_data
except ImportError:
    market_data = None
try:
    import filings
except ImportError:
    filings = None

LOCAL_TZ = ZoneInfo("Asia/Hong_Kong")
UTC = dt.timezone.utc
_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_DEFAULT_FLAGS = [("S&P 500", "^GSPC"), ("Nasdaq", "^IXIC"), ("Dow Jones", "^DJI"), ("Russell 2000", "^RUT"),
                  ("VIX", "^VIX"), ("10Y yield", "^TNX"), ("US Dollar (DXY)", "DX-Y.NYB"), ("Gold", "GC=F"),
                  ("Brent", "BZ=F"), ("WTI", "CL=F"), ("Bitcoin", "BTC-USD"), ("Ethereum", "ETH-USD")]
_FOREIGN_SFX = {"KS", "KQ", "T", "AS", "DE", "F", "PA", "L", "TO", "V", "HK", "SS", "SZ", "TW", "TWO", "SI",
                "AX", "NZ", "SW", "MI", "MC", "ST", "OL", "CO", "HE", "VI", "BR", "LS", "SA", "MX", "NS", "BO",
                "IR", "IS", "JK", "BK", "KL", "KA"}


def _now_local():
    return dt.datetime.now(LOCAL_TZ)


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _scheduled() -> bool:
    return os.environ.get("GITHUB_EVENT_NAME", "") == "schedule"


def decide_reports(config, requested: str) -> list[str]:
    """auto: scheduled runs send the market email every time and the portfolio
    email on schedule.portfolio_day (HK local weekday); any manual run sends both."""
    requested = (requested or "auto").lower()
    if requested in ("market", "portfolio"):
        return [requested]
    if requested == "both":
        return ["market", "portfolio"]
    if not _scheduled():
        return ["market", "portfolio"]
    day = str((config.get("schedule") or {}).get("portfolio_day", "Sat"))[:3].title()
    out = ["market"]
    if _WEEKDAYS[_now_local().weekday()] == day:
        out.append("portfolio")
    return out


def _fmt_span(start, end):
    a, b = start.astimezone(LOCAL_TZ), end.astimezone(LOCAL_TZ)
    return f"News from {a:%a %d %b %H:%M} to {b:%a %d %b %H:%M} HKT"


def market_window(state_data, now=None):
    """[start, end] for the daily email. Scheduled runs start exactly where the
    previous scheduled email's window ended (no gap, no repeats); Monday's email
    therefore covers the whole weekend. Without memory (or on manual runs) it
    falls back to 24h (48h on a Monday)."""
    now = now or dt.datetime.now(UTC)
    default = dt.timedelta(hours=48 if _now_local().weekday() == 0 else 24)
    start = now - default
    if _scheduled():
        last = state_mod.parse_iso((state_data.get("market") or {}).get("last_window_end"))
        if last and dt.timedelta(hours=12) <= (now - last) <= dt.timedelta(hours=96):
            start = last
    return start, now


def _market_flags(instruments):
    pairs = ([{"name": n, "symbol": s} for n, s in _DEFAULT_FLAGS] if instruments is None else instruments)
    moves = prices.batch_moves([p["symbol"] for p in pairs], period="1mo")
    out = []
    for p in pairs:
        m = moves.get(p["symbol"])
        if m:
            out.append({"name": p["name"], "symbol": p["symbol"], "price": m["price"], "pct": m["pct_1d"]})
    return out


def _news_for(tk, name, lookback, max_items, fetch_full=False, full_limit=3):
    """Ticker news for names outside collect(): Finnhub first, Google News fallback,
    with the real article body for the top few items."""
    arts = sources.finnhub_news(tk, max_items, lookback)
    if not arts:
        arts = sources.google_news(f'"{name}" stock OR "{tk}" shares', tk, "stock", max_items, lookback)
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
                a.summary = (a.summary + " " + body).strip()[:3500]
                n += 1
    return arts


def _crypto_snapshot(symbols):
    moves = prices.batch_moves([f"{s}-USD" for s in symbols], period="1mo")
    out = []
    for s in symbols:
        m = moves.get(f"{s}-USD")
        if m:
            h = prices.history(f"{s}-USD", "1mo")
            pct7 = None
            try:
                c = h["Close"].dropna()
                pct7 = round((float(c.iloc[-1]) - float(c.iloc[-8])) / float(c.iloc[-8]) * 100, 2) if len(c) >= 8 else None
            except Exception:  # noqa: BLE001
                pass
            out.append({"symbol": s, "price": m["price"], "pct": m["pct_1d"], "pct7d": pct7})
    return out


def _is_foreign(tk):
    tk = tk.strip().upper()
    root, _, sfx = tk.rpartition(".")
    return bool(root and sfx in _FOREIGN_SFX) or tk.split(".")[0].isdigit()


# =========================================================================== #
#  DAILY MARKET DIGEST
# =========================================================================== #
def run_market(config, state_data, args):
    start, end = market_window(state_data)
    sources.set_window(start, end)
    lookback = max(1, int((end - start).total_seconds() // 3600) + 1)
    config.setdefault("sources", {})["lookback_hours"] = lookback
    period = _fmt_span(start, end)
    print(f"\n===== MARKET DIGEST — {period} ({lookback}h) =====")
    src = config.get("sources", {}) or {}
    mscfg = config.get("market_scan", {}) or {}
    fetch_full = bool(src.get("fetch_full_articles", False))
    stocks = (config.get("watchlist") or {}).get("stocks", []) or []
    holdings = [s.get("ticker", "").strip().upper() for s in stocks if s.get("ticker")]

    print("[1/7] Whole-market scan…")
    scan_items, candidates = [], []
    try:
        scan_items = sources.market_scan(config)
    except Exception as ex:  # noqa: BLE001
        print(f"      market scan failed ({ex})")
    if scan_items and discovery:
        try:
            candidates = [c for c in discovery.scan(scan_items, exclude=holdings,
                                                    limit=int(mscfg.get("max_candidates", 25)),
                                                    min_move=float(mscfg.get("min_move_pct", 0) or 0))
                          if not _is_foreign(c["ticker"])]
        except Exception as ex:  # noqa: BLE001
            print(f"      candidate discovery failed ({ex})")
        if fetch_full:
            for c in candidates[:int(mscfg.get("full_articles_for_top", 8))]:
                for a in (c.get("articles") or [])[:1]:
                    if a.url and "news.google.com" not in a.url and len(a.summary or "") < 1500:
                        body, final_url = sources.fetch_article_text(a.url)
                        if final_url and "finnhub.io" not in final_url:
                            a.url = final_url
                        if body:
                            a.summary = (a.summary + " " + body).strip()[:3500]
    print(f"      {len(scan_items)} headlines, {len(candidates)} priced candidates")

    print("[2/7] Policy, macro, global and sector news…")
    themes = config.get("market_themes") or []
    items = sources.collect_market(config, scan_items)
    crypto_watch = config.get("crypto_watch") or []
    crypto = {}
    if crypto_watch:
        cnews = sources.google_news("bitcoin OR ethereum crypto market", "Crypto", "topic", 6, lookback)
        crypto = {"snapshot": _crypto_snapshot(crypto_watch), "news": cnews}
        items += cnews
    print(f"      {len(items)} themed articles")

    print("[3/7] Market levels, sector ETFs, calendar…")
    flags = _market_flags(config.get("market_flags"))
    sec_cfg = config.get("sector_etfs") or []
    smoves = prices.batch_moves([s["symbol"] for s in sec_cfg], period="1mo")
    sector_moves = [{"label": s["label"], "symbol": s["symbol"], **smoves[s["symbol"]]}
                    for s in sec_cfg if s["symbol"] in smoves]
    ccfg = config.get("calendar") or {}
    cal_days = int(ccfg.get("horizon_days", 14))
    try:
        cal = econ_calendar.build(cal_days, (start, end), scope="global")
    except Exception as ex:  # noqa: BLE001
        print(f"      calendar failed ({ex})")
        cal = {}

    fl = {x["name"]: x for x in flags}
    policy_facts = []
    if cal.get("fed_rate"):
        policy_facts.append(("Fed funds (upper bound)", cal["fed_rate"]))
    if cal.get("fomc_next"):
        dd = (cal["fomc_next"].date() - dt.date.today()).days
        policy_facts.append(("Next FOMC decision", f"{econ_calendar.fmt_when(cal['fomc_next'])} ({dd}d)"))
    ty = fl.get("10Y yield") or {}
    if ty.get("price") is not None:
        policy_facts.append(("10Y Treasury yield", f"{ty['price']:.2f}%"
                             + (f" ({ty['pct']:+.2f}% on the day)" if ty.get("pct") is not None else "")))
    extras = {"region": "US", "period_label": period, "flags": flags, "sector_moves": sector_moves,
              "policy_facts": policy_facts,
              "calendar": cal, "calendar_days": cal_days, "candidates": candidates,
              "market_scan": scan_items[:40], "crypto": crypto,
              "theme_labels": [t["label"] for t in themes],
              "sector_labels": [t["label"] for t in themes if t.get("kind") == "sector"],
              "theme_etf": {t["label"]: t.get("etf") for t in themes if t.get("etf")},
              "calendar_note": ("Dates from the Federal Reserve, TreasuryDirect, ForexFactory and Nasdaq "
                                "(Nasdaq's dates are cross-checked against the Fed and Treasury every run). "
                                "Where two figures are shown they are month-on-month / year-on-year."),
              "footer_sources": ("Yahoo Finance, Finnhub, Google News, CNBC / MarketWatch / Yahoo / Investing.com RSS, "
                                 "Federal Reserve, ECB, BLS, BEA, TreasuryDirect, Nasdaq & ForexFactory calendars")}

    print("[4/7] AI market analysis…")
    an, status = analyzer.analyze_market(items + scan_items, extras, config)
    print(f"      engine: {status['engine']} (ok={status['ok']})")

    print("[5/7] Stocks to watch…")
    watch_max = int(mscfg.get("watch_max", 3))
    funds, techs, earn, fils = {}, {}, {}, {}
    sh_res, kept = {}, []
    for p in an.get("stocks_to_watch") or []:
        tk = p["ticker"]
        if _is_foreign(tk):
            continue
        f = fundamentals.fetch(tk, tk)
        if f.price is None:
            print(f"      dropped {tk} — no price data")
            continue
        sc = shariah.screen(f)
        if config.get("shariah_only") and sc["status"] == "fail":
            print(f"      dropped {tk} — {sc['reasons'][0]}")
            continue
        sh_res[tk] = sc
        funds[tk] = f
        techs[tk] = tech_mod.compute(tk)
        if market_data and market_data.enabled():
            earn[tk] = market_data.earnings_window(tk)
        if filings:
            fl = filings.latest_filing(tk, within_days=3)
            if fl:
                fils[tk] = fl
        items += _news_for(tk, f.name or tk, lookback, 6, fetch_full=fetch_full, full_limit=2)
        kept.append(p)
        if len(kept) >= watch_max:
            break
    an["stocks_to_watch"] = kept
    extras.update({"technicals": techs, "earnings": earn, "filings": fils, "shariah": sh_res})
    print(f"      {len(kept)} pick(s): {', '.join(p['ticker'] for p in kept) or 'none'}")
    if kept:
        try:
            analyzer.run_pick_debates(kept, funds, extras, items, config)
        except Exception as ex:  # noqa: BLE001
            print(f"[debate] skipped ({ex})")

    print("[6/7] Track record…")
    recent = state_mod.recent_picks(state_data, days=21)
    if recent:
        now_px = prices.batch_moves(sorted({r["ticker"] for r in recent}), period="5d")
        tr = []
        for r in recent[:8]:
            m = now_px.get(r["ticker"])
            if m and r.get("price"):
                tr.append({**r, "now": m["price"], "pct": round((m["price"] - r["price"]) / r["price"] * 100, 2)})
        extras["track_record"] = tr

    print("[7/7] Rendering…")
    subject, html_body, text_body = digest.build_market(an, items + scan_items, funds, extras, econ_calendar.fmt_when)
    if not args.dry_run:
        for p in kept:
            v = ((p.get("debate") or {}).get("verdict") or {})
            state_mod.record_pick(state_data, p["ticker"], v.get("call") or "hold",
                                  funds[p["ticker"]].price, p.get("catalyst", ""))
        if _scheduled():
            state_data.setdefault("market", {})["last_window_end"] = end.replace(microsecond=0).isoformat()
    return "market", subject, html_body, text_body


# =========================================================================== #
#  WEEKLY PORTFOLIO DIGEST
# =========================================================================== #
def run_portfolio(config, state_data, args):
    days = int((config.get("schedule") or {}).get("portfolio_lookback_days", 7))
    end = dt.datetime.now(UTC)
    start = end - dt.timedelta(days=days)
    sources.set_window(start, end)
    lookback = days * 24
    config.setdefault("sources", {})["lookback_hours"] = lookback
    period = f"News from {start.astimezone(LOCAL_TZ):%a %d %b} to {end.astimezone(LOCAL_TZ):%a %d %b %Y}"
    print(f"\n===== PORTFOLIO DIGEST — {period} =====")
    src = config.get("sources", {}) or {}
    stocks = (config.get("watchlist") or {}).get("stocks", []) or []
    fetch_full = bool(src.get("fetch_full_articles", False))
    holdings_n = int(config.get("etf_holdings_news", 8))
    benchmark = config.get("etf_benchmark", "SPY")

    print("[1/6] This week's news on your holdings…")
    items = sources.collect(config)
    print(f"      {len(items)} articles")

    print("[2/6] Fundamentals, technicals, earnings, filings, ETF internals…")
    funds = fundamentals.fetch_many(stocks)
    techs, earn, fils, etf_profiles, etf_hnews = {}, {}, {}, {}, {}
    bench_hist = prices.history(benchmark, "1y")
    progs, prog_txt = {}, {}
    for s in stocks:
        tk = s.get("ticker", "").strip()
        if not tk:
            continue
        techs[tk] = tech_mod.compute(tk)
        progs[tk] = prices.progression(tk, bench_hist=bench_hist)
        prog_txt[tk] = prices.describe(progs[tk])
        if market_data and market_data.enabled():
            earn[tk] = market_data.earnings_window(tk, back_days=8, fwd_days=45)
        if filings:
            fl = filings.latest_filing(tk, within_days=8)
            if fl:
                fils[tk] = fl
        if funds.get(tk) and funds[tk].is_etf:
            prof = etf_mod.enrich(tk, benchmark=benchmark, peer_tickers=s.get("peers"))
            etf_profiles[tk] = prof
            for h in prof.holdings[:holdings_n]:
                arts = _news_for(h.symbol, h.name or h.symbol, lookback, 4, fetch_full=fetch_full, full_limit=1)
                if arts:
                    etf_hnews.setdefault(tk, {})[h.symbol] = arts
            print(f"      {tk}: {len(etf_hnews.get(tk, {}))} of {min(holdings_n, len(prof.holdings))} components had news")

    print("[3/6] Crowd sentiment…")
    crowd = {}
    if src.get("social", True):
        try:
            crowd = social.crowd([s["ticker"] for s in stocks if s.get("ticker")], src.get("adanos_platforms"))
        except Exception as ex:  # noqa: BLE001
            print(f"      crowd unavailable ({ex})")

    events = []
    today = dt.date.today()
    for s in stocks:
        tk = s.get("ticker")
        u = ((earn.get(tk) or {}).get("upcoming") or {})
        d = None
        if u.get("date"):
            try:
                d = dt.date.fromisoformat(u["date"])
            except ValueError:
                d = None
        elif funds.get(tk) and funds[tk].next_earnings:
            try:
                d = dt.date.fromisoformat(funds[tk].next_earnings)
            except ValueError:
                d = None
        if d and 0 <= (d - today).days <= 45:
            hour = {"bmo": " (before the open)", "amc": " (after the close)"}.get((u.get("hour") or "").lower(), "")
            est = f" — EPS est. {u['eps_estimate']}" if u.get("eps_estimate") is not None else ""
            events.append({"date": d, "ticker": tk, "title": f"earnings{hour}{est}"})
    events.sort(key=lambda e: e["date"])

    extras = {"region": "US", "period_label": period, "technicals": techs, "earnings": earn, "filings": fils,
              "etf": etf_profiles, "etf_holding_news": etf_hnews, "crowd": crowd, "progression": progs,
              "progression_text": prog_txt, "holdings_order": stocks, "holding_events": events,
              "sentiment": sentiment.aggregate(items),
              "look_through": portfolio.look_through(stocks, funds, etf_profiles),
              "footer_sources": "Yahoo Finance, Finnhub, Google News, SEC EDGAR, Adanos (crowd)"}

    print("[4/6] AI portfolio review…")
    an, status = analyzer.analyze_portfolio(items, funds, extras, config)
    print(f"      engine: {status['engine']} (ok={status['ok']})")

    print("[5/6] Weekly verdicts (bull · bear · judge)…")
    try:
        analyzer.run_holding_debates(an, funds, extras, items, config, state_data)
    except Exception as ex:  # noqa: BLE001
        print(f"[debate] skipped ({ex})")

    print("[6/6] Rendering…")
    subject, html_body, text_body = digest.build_portfolio(an, items, funds, extras)
    if not args.dry_run:
        for s in an.get("stocks", []):
            v = ((s.get("debate") or {}).get("verdict") or {})
            f = funds.get(s.get("ticker"))
            state_mod.record_verdict(state_data, s.get("ticker", ""), v, f.price if f else None)
    return "portfolio", subject, html_body, text_body


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--report", default=os.environ.get("REPORT", "auto"),
                    choices=["auto", "market", "portfolio", "both"])
    ap.add_argument("--dry-run", action="store_true",
                    default=os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes"))
    ap.add_argument("--no-ai", action="store_true", help="skip every AI call (rule-based fallback)")
    args = ap.parse_args()
    config = load_config(args.config)
    sources.configure(config)
    if args.no_ai:
        config.setdefault("analysis", {}).update(enabled=False, debate=False)
    print("[env] " + " | ".join(f"{k}={'set' if os.environ.get(k) else 'MISSING'}"
                                for k in ["GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "FINNHUB_API_KEY",
                                          "ADANOS_API_KEY", "SEC_USER_AGENT", "EMAIL_PASSWORD"]))
    reports = decide_reports(config, args.report)
    print(f"[plan] reports this run: {', '.join(reports)}"
          + (" (dry run — nothing is sent, no state saved)" if args.dry_run else ""))
    state_data = state_mod.load()
    out_dir = config.get("output_dir", "./digests")
    os.makedirs(out_dir, exist_ok=True)
    built = []
    for kind in reports:
        try:
            built.append((run_market if kind == "market" else run_portfolio)(config, state_data, args))
        except Exception as ex:  # noqa: BLE001 — one email failing must not stop the other
            import traceback
            traceback.print_exc()
            print(f"[{kind}] FAILED: {ex}")
    for kind, subject, html_body, text_body in built:
        path = os.path.join(out_dir, f"digest-{_now_local():%Y-%m-%d}-{kind}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(html_body)
        print(f"      saved {path}")
        if args.dry_run:
            print("\n" + "=" * 64 + f"\n{subject}\n" + "=" * 64 + "\n" + text_body[:2500])
        else:
            print(f"      delivering {kind}…")
            delivery.deliver(config, subject, html_body, text_body)
    if not args.dry_run:
        state_mod.save(state_data)
    else:
        print("\n[dry-run] Not sending; state not saved.")
    return 0 if len(built) == len(reports) else 1


if __name__ == "__main__":
    sys.exit(main())
