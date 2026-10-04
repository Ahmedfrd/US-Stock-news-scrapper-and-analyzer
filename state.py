"""
state.py — run-to-run memory, committed back to the repo by the workflow.

Without it every run was amnesiac: the weekly verdict on a holding could not say
what changed since last week, and the daily market window could only guess where
yesterday's email stopped. (Pattern borrowed from the market-digest monorepo,
where a committed JSON file also gives free, auditable history.)

What is kept (small, bounded):
  * market.last_window_end — where the last SCHEDULED market email's news window
    ended, so the next one starts exactly there (no gaps, no repeats).
  * portfolio.verdicts[TICKER] — the last few weekly verdicts (call, conviction,
    price, pointers), so this week's judge can say what changed.
  * picks — stocks-to-watch flagged in the market email, for the track record.

Everything degrades safely: a missing or corrupt file means "no memory", never
a failed run.
"""

from __future__ import annotations

import datetime as dt
import json
import os

PATH = os.path.join("state", "state.json")
_KEEP_VERDICTS = 8
_KEEP_PICK_DAYS = 45


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def parse_iso(value):
    if not value:
        return None
    try:
        d = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except Exception:  # noqa: BLE001
        return None


def load(path: str = PATH) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data.setdefault("market", {})
            data.setdefault("portfolio", {}).setdefault("verdicts", {})
            data.setdefault("picks", [])
            return data
    except FileNotFoundError:
        pass
    except Exception as e:  # noqa: BLE001
        print(f"[state] could not read {path} ({e}); starting from empty memory.")
    return {"market": {}, "portfolio": {"verdicts": {}}, "picks": []}


def save(data: dict, path: str = PATH) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        cutoff = dt.date.today() - dt.timedelta(days=_KEEP_PICK_DAYS)
        data["picks"] = [p for p in (data.get("picks") or [])
                         if (p.get("date") or "") >= cutoff.isoformat()]
        for tk, rows in (data.get("portfolio", {}).get("verdicts") or {}).items():
            data["portfolio"]["verdicts"][tk] = rows[-_KEEP_VERDICTS:]
        data["updated"] = _now_iso()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True, default=str)
            f.write("\n")
        print(f"[state] saved {path}")
    except Exception as e:  # noqa: BLE001
        print(f"[state] could not save {path} ({e})")


# --------------------------------------------------------------------------- #
#  Weekly verdict history
# --------------------------------------------------------------------------- #
def last_verdict(data: dict, ticker: str, before: dt.date | None = None) -> dict | None:
    """Most recent stored verdict for a holding, older than `before` (so a re-run
    on the same day compares against LAST week, not against itself)."""
    rows = (data.get("portfolio", {}).get("verdicts") or {}).get(ticker.upper()) or []
    before = before or dt.date.today()
    older = [r for r in rows if (r.get("date") or "") < before.isoformat()]
    return older[-1] if older else None


def record_verdict(data: dict, ticker: str, verdict: dict, price) -> None:
    if not verdict or not verdict.get("call"):
        return
    rows = data.setdefault("portfolio", {}).setdefault("verdicts", {}).setdefault(ticker.upper(), [])
    today = dt.date.today().isoformat()
    rows[:] = [r for r in rows if r.get("date") != today]       # one entry per day
    rows.append({"date": today, "call": verdict.get("call"),
                 "conviction": verdict.get("conviction", ""),
                 "price": round(float(price), 4) if price is not None else None,
                 "pointers": [str(p)[:200] for p in (verdict.get("pointers") or [])][:3]})


# --------------------------------------------------------------------------- #
#  Stocks-to-watch track record
# --------------------------------------------------------------------------- #
def record_pick(data: dict, ticker: str, call: str, price, catalyst: str = "") -> None:
    today = dt.date.today().isoformat()
    picks = data.setdefault("picks", [])
    picks[:] = [p for p in picks if not (p.get("date") == today and p.get("ticker") == ticker)]
    picks.append({"date": today, "ticker": ticker, "call": call,
                  "price": round(float(price), 4) if price is not None else None,
                  "catalyst": (catalyst or "")[:160]})


def recent_picks(data: dict, days: int = 21, exclude_today: bool = True) -> list[dict]:
    today = dt.date.today()
    cutoff = (today - dt.timedelta(days=days)).isoformat()
    out = [p for p in (data.get("picks") or []) if (p.get("date") or "") >= cutoff
           and p.get("price") and not (exclude_today and p.get("date") == today.isoformat())]
    return sorted(out, key=lambda p: (p.get("date") or ""), reverse=True)
