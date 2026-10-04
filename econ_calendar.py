"""
econ_calendar.py — EXACT dates for upcoming policy-rate decisions and macro data
releases, plus the actual-vs-consensus prints released inside the news window.

Nothing here is model-generated. A confident, specific, wrong date is worse than
no date, so every source is cross-checked:

  * Federal Reserve FOMC calendar page — authoritative meeting dates. The
    decision lands on the SECOND day of a two-day meeting, 14:00 ET.
  * ForexFactory weekly JSON (nfs.faireconomy.media) — this week's events with
    full ISO timestamps, impact rating, forecast and previous.
  * Nasdaq economic-events API — ~4 weeks forward + actuals for released prints.
    Verified 2026-10-05: its `date` parameter is OFF BY ONE (the 16-Sep-2026 Fed
    decision is filed under 17-Sep; every Treasury auction sits a day late). The
    offset is therefore CALIBRATED each run against two authoritative anchors —
    the TreasuryDirect auction schedule and the FOMC page — and Nasdaq is dropped
    entirely if calibration is inconclusive.
  * TreasuryDirect upcoming-auctions JSON — note/bond auction dates.

Times: Nasdaq and ForexFactory publish US Eastern time; the report shows ET and
the Hong Kong equivalent.
"""

from __future__ import annotations

import datetime as dt
import re
import time
from zoneinfo import ZoneInfo

import requests

ET = ZoneInfo("America/New_York")
HKT = ZoneInfo("Asia/Hong_Kong")
UTC = dt.timezone.utc

_BROWSER = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*"}

FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
NASDAQ_URL = "https://api.nasdaq.com/api/calendar/economicevents?date={d}"
NASDAQ_PAGE = "https://www.nasdaq.com/market-activity/economic-calendar"
TREASURY_URL = "https://www.treasurydirect.gov/TA_WS/securities/upcoming?format=json"
TREASURY_PAGE = "https://www.treasurydirect.gov/auctions/upcoming/"

_MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july",
                                       "august", "september", "october", "november", "december"], 1)}

# --------------------------------------------------------------------------- #
#  Which events matter — (pattern, short label, why it matters)
# --------------------------------------------------------------------------- #
US_KEY = [
    (r"^(fed )?interest rate decision$|federal funds rate", "Fed rate decision", "sets the policy rate"),
    (r"fomc (meeting )?minutes", "FOMC minutes", "detail behind the last rate decision"),
    (r"fomc press conference", "Fed press conference", "guidance on the rate path"),
    (r"^(core )?cpi( m/m| y/y)?$", "CPI inflation", "inflation — moves rate-cut odds"),
    (r"^(core )?ppi( m/m| y/y)?$", "PPI (producer prices)", "pipeline inflation"),
    (r"^(core )?pce price index|^core pce", "PCE inflation", "the Fed's preferred inflation gauge"),
    (r"^nonfarm payrolls$|^non-farm employment change$", "Nonfarm payrolls", "jobs — the Fed's other mandate"),
    (r"^unemployment rate$", "Unemployment rate", "labour-market slack"),
    (r"average hourly earnings", "Average hourly earnings", "wage inflation"),
    (r"^(core )?retail sales( m/m)?$", "Retail sales", "consumer demand"),
    (r"^gdp( growth rate)?( q/q)?|advance gdp|prelim gdp|final gdp", "GDP", "growth"),
    (r"ism manufacturing pmi", "ISM manufacturing", "factory activity"),
    (r"ism (non-manufacturing|services) pmi", "ISM services", "services activity"),
    (r"jolts", "JOLTS job openings", "labour demand"),
    (r"^initial jobless claims$|^unemployment claims$", "Jobless claims", "weekly labour read"),
    (r"(michigan|uom) consumer sentiment", "UoM consumer sentiment", "consumer mood & inflation expectations"),
    (r"cb consumer confidence", "Consumer confidence", "consumer mood"),
    (r"durable goods orders", "Durable goods", "business investment"),
    (r"^industrial production", "Industrial production", "factory output"),
    (r"^crude oil inventories$", "EIA crude inventories", "oil supply/demand"),
    (r"^10-year note auction$|^30-year bond auction$", None, "demand for long-dated Treasuries — moves yields"),
    (r"beige book", "Fed Beige Book", "the Fed's regional economic read"),
    (r"(fed chair|powell|warsh).*speaks", None, "Fed chair remarks"),
]
GLOBAL_KEY = [
    (r"(ecb|deposit facility|main refinancing).*rate|^ecb interest rate decision", "ECB rate decision", "euro-area policy rate"),
    (r"boe interest rate decision|official bank rate", "BoE rate decision", "UK policy rate"),
    (r"boj interest rate decision|boj policy rate", "BoJ rate decision", "Japan policy rate — yen carry trade"),
    (r"loan prime rate", "China loan prime rate", "China credit conditions"),
    (r"^opec meeting$|opec-jmmc|opec\+", "OPEC+ meeting", "oil supply quotas"),
]
_GLOBAL_COUNTRIES = {"Euro Zone": "EU", "United Kingdom": "UK", "Japan": "JP", "China": "CN",
                     "EUR": "EU", "GBP": "UK", "JPY": "JP", "CNY": "CN"}


def _match(name: str, table):
    n = (name or "").strip().lower()
    for pat, label, why in table:
        if re.search(pat, n):
            label = label or name.strip()
            # keep headline and core prints apart (they have different numbers)
            if n.startswith("core ") and not label.lower().startswith("core"):
                label = "Core " + label[0].lower() + label[1:] if label[:3] != "CPI" and label[:3] != "PPI" \
                    and label[:3] != "PCE" else "Core " + label
            return label, why
    return None


# --------------------------------------------------------------------------- #
#  Sources
# --------------------------------------------------------------------------- #
def fomc_decisions() -> list[dt.datetime]:
    """FOMC decision datetimes (ET, 14:00) from the Fed's own calendar page."""
    out = []
    try:
        from bs4 import BeautifulSoup
        r = None
        for attempt in range(3):
            try:
                r = requests.get(FOMC_URL, headers={"User-Agent": _BROWSER["User-Agent"]}, timeout=30)
                r.raise_for_status()
                break
            except Exception:  # noqa: BLE001
                if attempt == 2:
                    raise
                time.sleep(3)
        soup = BeautifulSoup(r.text, "html.parser")
        for panel in soup.select("div.panel"):
            head = panel.select_one(".panel-heading")
            ym = re.search(r"(20\d{2})", head.get_text(" ", strip=True) if head else "")
            if not ym:
                continue
            year = int(ym.group(1))
            for mnode, dnode in zip(panel.select(".fomc-meeting__month"),
                                    panel.select(".fomc-meeting__date")):
                mtxt = mnode.get_text(" ", strip=True).lower()
                # "Apr/May" style spans: the decision is in the LAST month named
                mons = [_MONTHS[k] for w in re.findall(r"[a-z]{3,}", mtxt)
                        for k in _MONTHS if k.startswith(w[:3])]
                nums = re.findall(r"\d{1,2}", dnode.get_text(" ", strip=True))
                if not mons or not nums:
                    continue
                try:
                    day = dt.date(year, mons[-1], int(nums[-1]))
                except ValueError:
                    continue
                out.append(dt.datetime.combine(day, dt.time(14, 0), tzinfo=ET))
    except Exception as e:  # noqa: BLE001
        print(f"[calendar] FOMC page unavailable ({e})")
    return sorted(set(out))


def treasury_auctions() -> list[dict]:
    try:
        r = requests.get(TREASURY_URL, headers={"User-Agent": _BROWSER["User-Agent"]}, timeout=30)
        r.raise_for_status()
        out = []
        for row in r.json() or []:
            d = str(row.get("auctionDate", ""))[:10]
            try:
                out.append({"date": dt.date.fromisoformat(d), "type": row.get("securityType", ""),
                            "term": row.get("securityTerm", "")})
            except ValueError:
                continue
        return out
    except Exception as e:  # noqa: BLE001
        print(f"[calendar] Treasury auctions unavailable ({e})")
        return []


def forexfactory_week() -> list[dict]:
    try:
        r = requests.get(FF_URL, headers=_BROWSER, timeout=30)
        r.raise_for_status()
        out = []
        for e in r.json() or []:
            try:
                when = dt.datetime.fromisoformat(e["date"]).astimezone(ET)
            except Exception:  # noqa: BLE001
                continue
            out.append({"when": when, "name": e.get("title", ""), "country": e.get("country", ""),
                        "impact": e.get("impact", ""), "consensus": e.get("forecast") or "",
                        "previous": e.get("previous") or "", "actual": "",
                        "timed": not (when.hour == 0 and when.minute == 0)})
        return out
    except Exception as e:  # noqa: BLE001
        print(f"[calendar] ForexFactory feed unavailable ({e})")
        return []


_NQ_CACHE: dict[str, list] = {}


def _nasdaq_raw(param_date: dt.date) -> list[dict] | None:
    key = param_date.isoformat()
    if key in _NQ_CACHE:
        return _NQ_CACHE[key]
    rows = None
    try:
        r = requests.get(NASDAQ_URL.format(d=key), headers={**_BROWSER, "Origin": "https://www.nasdaq.com",
                                                            "Referer": "https://www.nasdaq.com/"}, timeout=20)
        r.raise_for_status()
        rows = ((r.json() or {}).get("data") or {}).get("rows") or []
    except Exception:  # noqa: BLE001
        rows = None
    _NQ_CACHE[key] = rows
    time.sleep(0.25)
    return rows


def _clean(v) -> str:
    s = str(v or "").replace("&nbsp;", "").strip()
    return "" if s in ("-", "--") else s


def _auction_name(a: dict) -> str | None:
    """TreasuryDirect security → Nasdaq's event name."""
    term, typ = a.get("term", ""), (a.get("type") or "").lower()
    m = re.match(r"(\d+)-(Week|Year)(?:\s+(\d+)-Month)?", term)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    if unit == "Week":
        if typ != "bill":
            return None
        return {13: "3-Month Bill Auction", 26: "6-Month Bill Auction",
                52: "52-Week Bill Auction"}.get(n, f"{n}-Week Bill Auction")
    years = n + (1 if m.group(3) else 0)          # "9-Year 10-Month" = 10-year reopening
    kind = "Bond" if typ == "bond" else "Note"
    return f"{years}-Year {kind} Auction"


def nasdaq_offset(start: dt.date, end: dt.date, fomc: list[dt.datetime],
                  auctions: list[dict]) -> int | None:
    """Days to SUBTRACT from Nasdaq's date parameter to get the true date.

    Anchors: Treasury auctions and FOMC decisions inside [start, end]. Returns
    None when no shift is clearly supported — Nasdaq is then not used."""
    anchors = [(a["date"], _auction_name(a)) for a in auctions if start <= a["date"] <= end]
    anchors += [(f.date(), "Fed Interest Rate Decision") for f in fomc if start <= f.date() <= end]
    anchors = [(d, n) for d, n in anchors if n][:10]
    if not anchors:
        return None
    score = {}
    for shift in (1, 0, -1):
        hits = 0
        for d, name in anchors:
            rows = _nasdaq_raw(d + dt.timedelta(days=shift)) or []
            if any(_clean(r.get("eventName")).lower() == name.lower() and r.get("country") == "United States"
                   for r in rows):
                hits += 1
        score[shift] = hits
    best = max(score, key=score.get)
    others = [v for k, v in score.items() if k != best]
    if score[best] >= 2 and score[best] > max(others):
        if best:
            print(f"[calendar] Nasdaq dates calibrated: offset {best:+d} day(s) "
                  f"({score[best]}/{len(anchors)} anchors matched)")
        return best
    print(f"[calendar] Nasdaq calibration inconclusive ({score}); not using Nasdaq dates.")
    return None


def _nasdaq_events(true_dates: list[dt.date], offset: int) -> list[dict]:
    out = []
    for d in true_dates:
        for r in _nasdaq_raw(d + dt.timedelta(days=offset)) or []:
            t = _clean(r.get("gmt"))
            try:
                hh, mm = (int(x) for x in t.split(":"))
                when = dt.datetime.combine(d, dt.time(hh, mm), tzinfo=ET)
            except Exception:  # noqa: BLE001
                when = dt.datetime.combine(d, dt.time(0, 0), tzinfo=ET)
            out.append({"when": when, "name": _clean(r.get("eventName")), "country": r.get("country", ""),
                        "impact": "", "consensus": _clean(r.get("consensus")),
                        "previous": _clean(r.get("previous")), "actual": _clean(r.get("actual")),
                        "timed": bool(t)})
    return out


# --------------------------------------------------------------------------- #
#  Public API
# --------------------------------------------------------------------------- #
def _classify(ev: dict, scope: str):
    """US key events always; other central banks only for scope='global';
    OPEC+ always (oil moves both markets)."""
    if ev["country"] in ("United States", "USD"):
        return _match(ev["name"], US_KEY)
    if "opec" in ev["name"].lower() or (scope == "global" and ev["country"] in _GLOBAL_COUNTRIES):
        return _match(ev["name"], GLOBAL_KEY)
    return None


def _merge(rows: list[dict]) -> list[dict]:
    """Collapse the m/m + y/y (or headline + core) duplicates into one line."""
    merged: dict[tuple, dict] = {}
    for e in rows:
        base = re.sub(r"\(.*?\)|\b[my]/[my]\b|\b(mom|yoy|qoq)\b", "", e["name"].lower()).strip()
        k = (e["when"].date(), e["when"].strftime("%H:%M"), e["label"], base)
        if k not in merged:
            merged[k] = {**e, "consensus": [e["consensus"]] if e["consensus"] else [],
                         "previous": [e["previous"]] if e["previous"] else [],
                         "actual": [e["actual"]] if e["actual"] else []}
        else:
            for f in ("consensus", "previous", "actual"):
                if e[f] and e[f] not in merged[k][f]:
                    merged[k][f].append(e[f])
    out = []
    for e in merged.values():
        for f in ("consensus", "previous", "actual"):
            e[f] = " / ".join(e[f][:2])
        out.append(e)
    return sorted(out, key=lambda e: (e["when"], e["label"]))


def build(horizon_days: int = 14, window: tuple[dt.datetime, dt.datetime] | None = None,
          scope: str = "global") -> dict:
    """Returns {"upcoming": [...], "released": [...], "fomc_next": dt|None,
    "fed_rate": str|None, "sources": [...]}. Each event:
    {when (ET datetime), label, name, country, why, consensus, previous, actual, source, url}."""
    now_et = dt.datetime.now(ET)
    today = now_et.date()
    end = today + dt.timedelta(days=horizon_days)
    fomc = fomc_decisions()
    auctions = treasury_auctions()
    ff = forexfactory_week()
    offset = nasdaq_offset(today - dt.timedelta(days=3), end, fomc, auctions)

    ff_days = {e["when"].date() for e in ff}
    ff_last = max(ff_days) if ff_days else today - dt.timedelta(days=1)
    events = []
    for e in ff:
        if today <= e["when"].date() <= end:
            hit = _classify(e, scope)
            if hit and (e["impact"] in ("High", "Medium") or e["country"] == "USD"):
                events.append({**e, "label": hit[0], "why": hit[1], "source": "ForexFactory",
                               "url": "https://www.forexfactory.com/calendar"})
    released = []
    if offset is not None:
        later = [today + dt.timedelta(days=i) for i in range(horizon_days + 1)]
        for e in _nasdaq_events([d for d in later if d > ff_last], offset):
            hit = _classify(e, scope)
            if hit:
                events.append({**e, "label": hit[0], "why": hit[1], "source": "Nasdaq",
                               "url": NASDAQ_PAGE})
        if window:
            w0, w1 = window
            days = sorted({(w0.astimezone(ET).date() + dt.timedelta(days=i))
                           for i in range((w1.astimezone(ET).date() - w0.astimezone(ET).date()).days + 1)})
            for e in _nasdaq_events(days, offset):
                if e["country"] != "United States" or not e["actual"]:
                    continue
                if not (w0 <= e["when"].astimezone(UTC) <= w1):
                    continue
                hit = _match(e["name"], US_KEY)
                if hit:
                    released.append({**e, "label": hit[0], "why": hit[1], "source": "Nasdaq",
                                     "url": NASDAQ_PAGE})

    # Authoritative FOMC decision dates win over any feed's copy (when the Fed
    # page answered; otherwise the calibrated feed copy stands).
    if fomc:
        events = [e for e in events if e["label"] != "Fed rate decision"]
    nxt = next((f for f in fomc if f.date() >= today), None)
    if nxt is None:
        nxt = next((e["when"] for e in sorted(events, key=lambda e: e["when"])
                    if e["label"] == "Fed rate decision"), None)
    for f in fomc:
        if today <= f.date() <= end:
            events.append({"when": f, "label": "Fed rate decision", "name": "FOMC rate decision",
                           "country": "United States", "why": "sets the policy rate",
                           "consensus": "", "previous": "", "actual": "", "source": "Federal Reserve",
                           "url": FOMC_URL})
    # 10y/30y auctions straight from TreasuryDirect (authoritative) if Nasdaq is off.
    if offset is None:
        for a in auctions:
            nm = _auction_name(a) or ""
            if today <= a["date"] <= end and nm in ("10-Year Note Auction", "30-Year Bond Auction"):
                events.append({"when": dt.datetime.combine(a["date"], dt.time(13, 0), tzinfo=ET),
                               "label": nm, "name": nm, "country": "United States",
                               "why": "demand for long-dated Treasuries — moves yields",
                               "consensus": "", "previous": "", "actual": "",
                               "source": "TreasuryDirect", "url": TREASURY_PAGE})

    # Current policy rate: the 'previous' on the next decision's row, else the
    # 'actual' on the last decision's row.
    fed_rate = None
    if offset is not None:
        last = [f for f in fomc if f.date() < today]
        probes = ([(nxt.date(), "previous")] if nxt else []) + ([(last[-1].date(), "actual")] if last else [])
        for day, field in probes:
            for e in _nasdaq_events([day], offset):
                if e["country"] == "United States" and _match(e["name"], US_KEY[:1]) and e[field]:
                    fed_rate = e[field]
                    break
            if fed_rate:
                break
    if fed_rate:
        for e in events:
            if e["label"] == "Fed rate decision" and not e["previous"]:
                e["previous"] = fed_rate
    # Drop anything already out (a timed event earlier today has happened).
    events = [e for e in events if (e["when"] >= now_et if e.get("timed", True) else e["when"].date() >= today)]
    upcoming = _merge(events)
    released = _merge(released)
    print(f"[calendar] {len(upcoming)} upcoming event(s) in {horizon_days}d; "
          f"{len(released)} print(s) released in the window; next FOMC {nxt.date() if nxt else 'n/a'}")
    return {"upcoming": upcoming, "released": released, "fomc_next": nxt, "fed_rate": fed_rate,
            "nasdaq_offset": offset}


def fmt_when(when: dt.datetime, timed: bool = True) -> str:
    """'Wed 14 Oct · 08:30 ET (20:30 HKT)'."""
    day = when.strftime("%a %d %b")
    if not timed or (when.hour == 0 and when.minute == 0):
        return day
    hk = when.astimezone(HKT)
    hk_s = hk.strftime("%H:%M") + (" +1d" if hk.date() > when.date() else "")
    return f"{day} · {when.strftime('%H:%M')} ET ({hk_s} HKT)"
