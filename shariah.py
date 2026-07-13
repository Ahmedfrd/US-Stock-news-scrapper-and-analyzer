"""
shariah.py — automated Shariah-compliance screen for equities.

Two standard layers, using only free data (yfinance sector/industry + balance sheet):

  1. Business-activity screen — exclude companies whose core business is
     non-permissible: conventional banks/insurers/interest-based finance, alcohol,
     tobacco, gambling, adult content, pork, and weapons/defense.

  2. Financial-ratio screen (AAOIFI / Dow Jones Islamic style, 33% thresholds):
        interest-bearing debt / market cap  < 33%
        cash + interest-bearing securities / market cap < 33%

IMPORTANT — this is an automated *heuristic*, not a certified ruling. It cannot
check the receivables screen or the <5% non-permissible-income screen (that data
isn't freely available), and sector labels are coarse. Treat "pass" as "worth
verifying", and confirm with a certified screener (Zoya, Musaffa, IdealRatings)
before acting.
"""

from __future__ import annotations

# business-activity exclusions matched against sector / industry / name
_EXCLUDE = [
    "bank", "insurance", "insurer", "capital markets", "financial services & holding",
    "credit services", "mortgage", "consumer finance", "financial conglomerate",
    "brewer", "distiller", "winer", "alcohol", "beverages - alcoholic", "tobacco",
    "casino", "gambling", "gaming", "resorts & casinos", "betting",
    "aerospace & defense", "defense", "weapon", "firearm",
    "adult", "pork",
]
# terms that look excluded but are fine (avoid false positives)
_ALLOW_HINTS = ["software", "semiconductor", "technology", "beverages - non-alcoholic",
                "biotech", "internet", "gaming (video)"]

_THRESHOLD = 0.33


def screen(f) -> dict:
    """f: a Fundamentals object. Returns {status, reasons, ratios}."""
    if f is None:
        return {"status": "review", "reasons": ["no data"], "ratios": {}}

    hay = f"{getattr(f, 'sector', '')} {getattr(f, 'industry', '')} {getattr(f, 'name', '')}".lower()
    reasons = []

    # video-game 'gaming' is permissible; casino 'gaming' is not — disambiguate.
    excluded_hit = None
    for term in _EXCLUDE:
        if term in hay:
            if term == "gaming" and ("video" in hay or "entertainment" in hay):
                continue
            excluded_hit = term
            break

    if excluded_hit:
        return {"status": "fail",
                "reasons": [f"business activity looks non-compliant ({excluded_hit})"],
                "ratios": {}}

    ratios = {}
    status = "pass"

    mc = getattr(f, "market_cap", None)
    total_debt = getattr(f, "total_debt", None)
    total_cash = getattr(f, "total_cash", None)
    if mc and mc > 0:
        if total_debt is not None:
            dr = total_debt / mc
            ratios["debt/mktcap"] = round(dr * 100, 1)
            if dr >= _THRESHOLD:
                status = "fail"
                reasons.append(f"debt/market-cap {ratios['debt/mktcap']}% ≥ 33%")
        if total_cash is not None:
            cr = total_cash / mc
            ratios["cash/mktcap"] = round(cr * 100, 1)
            if cr >= _THRESHOLD:
                # cash-heavy is a softer flag (cash isn't necessarily interest-bearing)
                if status != "fail":
                    status = "review"
                reasons.append(f"cash/market-cap {ratios['cash/mktcap']}% ≥ 33% (verify if interest-bearing)")
        if total_debt is None and total_cash is None:
            reasons.append("balance-sheet data unavailable — ratios not checked (business screen only)")
    else:
        status = "review"
        reasons.append("market cap unavailable — ratios not checked")

    if status == "pass" and not reasons:
        reasons.append("business activity ok; debt & cash ratios within 33%")
    return {"status": status, "reasons": reasons, "ratios": ratios}


def label(status: str) -> str:
    return {"pass": "Shariah: passed (automated screen)",
            "review": "Shariah: needs review",
            "fail": "Shariah: not compliant"}.get(status, "Shariah: unknown")
