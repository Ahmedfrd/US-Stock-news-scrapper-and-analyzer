"""
digest.py — render the two emails.

  build_portfolio(...)  WEEKLY Portfolio Digest — your holdings only: the week's
                        company news, how each name travelled over the week,
                        ETF component news, and a short bull/bear/judge verdict
                        that says what changed since last week.
  build_market(...)     DAILY Market Digest — TL;DR, market levels, policy &
                        rates, macro prints, global, sectors, at most three
                        stocks to watch (catalyst + Catalyst Thesis), crypto,
                        the dated calendar, risks.

Rules the renderer enforces regardless of what the AI returned:
  * no news → no news block (no "no news this week" placards either);
  * the two emails share no sections;
  * bull/bear/judge are pointers, never paragraphs.
Inline styles and tables only — email clients ignore <style> and <details>.
"""

from __future__ import annotations

import datetime as dt
import html
from collections import defaultdict
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("Asia/Hong_Kong")


def _now_local():
    return dt.datetime.now(LOCAL_TZ)


def _esc(x):
    return html.escape(str(x)) if x is not None else ""


# --------------------------------------------------------------------------- #
#  Small building blocks
# --------------------------------------------------------------------------- #
_GREEN, _RED, _AMBER, _GREY, _BLUE = "#0a7d33", "#b3261e", "#8a6d00", "#5f6368", "#1a56c4"
_SENT = {"bullish": (_GREEN, "#e6f6ec"), "bearish": (_RED, "#fdeceb"), "neutral": (_GREY, "#eef0f2"),
         "mixed": (_AMBER, "#fdf5e0")}
_IMPACT = {"high": (_RED, "#fdeceb"), "medium": (_AMBER, "#fdf5e0"), "low": (_GREY, "#eef0f2")}
_CALL = {"buy": (_GREEN, "#e6f6ec"), "accumulate": (_GREEN, "#eef7f0"), "hold": (_GREY, "#eef0f2"),
         "watch": (_BLUE, "#eef3fb"), "reduce": (_RED, "#fdeef0"), "sell": (_RED, "#fdeceb"),
         "avoid": (_RED, "#fdeceb")}


def _pill(t, fg, bg, big=False):
    fs, pad = ("13px", "3px 10px") if big else ("12px", "2px 8px")
    return (f'<span style="display:inline-block;background:{bg};color:{fg};font-size:{fs};font-weight:700;'
            f'padding:{pad};border-radius:10px;text-transform:uppercase;letter-spacing:.3px;'
            f'white-space:nowrap">{_esc(t)}</span>')


def _sent_pill(s):
    s = (s or "neutral").lower()
    return _pill(s, *_SENT.get(s, _SENT["neutral"]))


def _impact_pill(s):
    s = (s or "low").lower()
    return _pill(f"impact {s}", *_IMPACT.get(s, _IMPACT["low"]))


def _call_pill(c, big=True):
    c = (c or "hold").lower()
    return _pill(c, *_CALL.get(c, _CALL["hold"]), big=big)


def _pct(p, nd=2, bold=True):
    if p is None:
        return ""
    c = _GREEN if p >= 0 else _RED
    return f'<span style="color:{c};{"font-weight:600;" if bold else ""}white-space:nowrap">{p:+.{nd}f}%</span>'


def _num(x, s="", pct=False, nd=2):
    if x is None:
        return "n/a"
    try:
        return f"{x*100:.1f}%" if pct else f"{x:,.{nd}f}{s}"
    except Exception:  # noqa: BLE001
        return str(x)


def _money(x, cur="$"):
    if x is None:
        return "n/a"
    try:
        x = float(x)
        for u, d in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
            if abs(x) >= d:
                return f"{cur}{x/d:.2f}{u}"
        return f"{cur}{x:,.0f}"
    except Exception:  # noqa: BLE001
        return "n/a"


def _expense(x):
    return f"{x:.2f}%" if x is not None else "n/a"


def _pts(lst):
    """AI bullets → clean list (accepts a newline string or a list)."""
    if isinstance(lst, str):
        lst = lst.splitlines()
    out = []
    for p in (lst or []):
        t = str(p).strip().lstrip("-•–·* ").strip()
        if t and not t.replace(".", "").replace("-", "").isdigit():
            out.append(t)
    return out


def _bullets(lst, fs="15px", color="#3c4043", limit=8):
    pts = _pts(lst)[:limit]
    if not pts:
        return ""
    return "".join(f'<div style="font-size:{fs};color:{color};margin:4px 0 4px 2px;padding-left:14px;'
                   f'text-indent:-12px">•&nbsp;{_esc(p)}</div>' for p in pts)


def _box(inner, bg="#fafbfc", border="#e6e9ee", pad="10px 13px"):
    return (f'<div style="background:{bg};border:1px solid {border};border-radius:8px;padding:{pad};'
            f'margin:8px 0">{inner}</div>')


def _label(t, color=_GREY):
    return (f'<div style="font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;'
            f'color:{color};margin:0 0 4px">{_esc(t)}</div>')


def _h2(title, sub=""):
    s = f'<div style="font-size:13px;font-weight:400;color:#80868b;margin-top:2px">{_esc(sub)}</div>' if sub else ""
    return (f'<div style="margin:26px 0 10px;padding-bottom:6px;border-bottom:2px solid #eef0f2">'
            f'<div style="font-size:19px;font-weight:700;color:#1a1a1a">{_esc(title)}</div>{s}</div>')


def _chip(label, value, tone="neutral"):
    c = {"good": _GREEN, "bad": _RED, "warn": _AMBER}.get(tone, "#3c4043")
    return (f'<span style="display:inline-block;background:#f1f3f5;border-radius:6px;padding:3px 8px;'
            f'margin:2px 4px 2px 0;font-size:13px;color:{_GREY};white-space:nowrap">{_esc(label)} '
            f'<b style="color:{c}">{_esc(value)}</b></span>')


def _links(items, label="Sources", limit=6):
    """Visible article list (email clients render <details> unreliably)."""
    seen, uniq = set(), []
    for it in items or []:
        k = (it.title or "").strip().lower()
        if k and k not in seen and it.url:
            seen.add(k)
            uniq.append(it)
    uniq = uniq[:limit]
    if not uniq:
        return ""
    rows = "".join(
        f'<div style="font-size:14px;margin:3px 0;padding-left:14px;text-indent:-12px">•&nbsp;'
        f'<a href="{_esc(it.url)}" style="color:{_BLUE};text-decoration:underline">{_esc(it.title)}</a>'
        f' <span style="color:#9aa0a6">— {_esc(it.source)}'
        + (f', {it.published.astimezone(LOCAL_TZ):%d %b}' if getattr(it, "published", None) else "")
        + '</span></div>' for it in uniq)
    return (f'<div style="margin-top:8px;padding-top:6px;border-top:1px solid #eef0f2">'
            f'<div style="font-size:12px;font-weight:700;color:#80868b;text-transform:uppercase;'
            f'letter-spacing:.4px;margin-bottom:2px">{_esc(label)}</div>{rows}</div>')


def _banner(status):
    if status.get("ok"):
        return (f'<div style="font-size:13px;color:#80868b;margin-bottom:14px">Analysis by '
                f'{_esc(status.get("engine", ""))}</div>')
    reason = _esc(status.get("reason", "") or "no AI provider available")
    return (f'<div style="background:#fdf5e0;border-left:4px solid {_AMBER};padding:8px 12px;border-radius:6px;'
            f'margin-bottom:14px;font-size:14px;color:#6b5300">⚠️ AI unavailable — this email uses the '
            f'rule-based fallback (headlines and computed figures only).<br><span style="font-size:12px">'
            f'{reason}</span></div>')


def _wrap(title, dated, sub, banner_html, body, footer):
    return ('<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1"></head>'
            '<body style="margin:0;padding:0;background:#ffffff">'
            '<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
            'max-width:720px;margin:0 auto;padding:16px 14px;color:#1a1a1a;line-height:1.5;font-size:15px">'
            f'<div style="font-size:24px;font-weight:800;margin:0">{_esc(title)}</div>'
            f'<div style="color:{_GREY};font-size:14px;margin:2px 0 0">{_esc(dated)}</div>'
            + (f'<div style="color:#9aa0a6;font-size:13px;margin:0 0 10px">{_esc(sub)}</div>' if sub else
               '<div style="height:10px"></div>')
            + banner_html + body + footer + '</div></body></html>')


def _footer(sources, extra=""):
    return (f'<div style="margin-top:22px;padding-top:10px;border-top:1px solid #eef0f2;font-size:12px;'
            f'color:#9aa0a6">{extra}Sources: {_esc(sources)}. AI + rule-based context, not predictions. '
            f'Informational only — <b>not investment advice</b>.</div>')


def _grid(rows):
    cells = "".join(f'<tr><td style="padding:2px 10px 2px 0;color:{_GREY};font-size:14px;white-space:nowrap;'
                    f'vertical-align:top">{_esc(l)}</td><td style="padding:2px 0;font-size:14px">{v}</td></tr>'
                    for l, v in rows if v not in (None, "", "n/a"))
    return f'<table style="border-collapse:collapse;width:100%">{cells}</table>' if cells else ""


# --------------------------------------------------------------------------- #
#  Technicals, fundamentals, verdict
# --------------------------------------------------------------------------- #
def _crowd_line(cw):
    """One line of crowd sentiment, with how many people are actually talking."""
    c = cw.get("consensus", {}) or {}
    bits = [f'Crowd {_sent_pill(c.get("label"))}']
    if c.get("bullish") is not None:
        bits.append(f'{c["bullish"]}% bullish / {c["bearish"]}% bearish')
    total = 0
    for sv in (cw.get("sources") or {}).values():
        try:
            total += int(float(sv.get("mentions") or 0))
        except Exception:  # noqa: BLE001
            pass
    if total:
        bits.append(f"{total:,} mentions")
    if cw.get("sources"):
        bits.append(", ".join(cw["sources"].keys()))
    return " · ".join(bits)


def _tech_line(t, tr=None):
    """One compact row of indicator chips + the AI's one-line read."""
    if not t or getattr(t, "error", None):
        return ""
    a50 = "above" if (t.price and t.sma50 and t.price > t.sma50) else "below" if t.sma50 else None
    a200 = "above" if (t.price and t.sma200 and t.price > t.sma200) else "below" if t.sma200 else None
    rsi_tone = "bad" if (t.rsi or 50) >= 70 else "good" if (t.rsi or 50) <= 30 else "neutral"
    chips = [_chip("RSI", t.rsi, rsi_tone) if t.rsi is not None else "",
             _chip("Trend", t.trend, {"up": "good", "down": "bad"}.get(t.trend, "neutral")) if t.trend else "",
             _chip("50d", a50, "good" if a50 == "above" else "bad") if a50 else "",
             _chip("200d", a200, "good" if a200 == "above" else "bad") if a200 else "",
             _chip("MACD", "+" if (t.macd_hist or 0) > 0 else "−", "good" if (t.macd_hist or 0) > 0 else "bad")
             if t.macd_hist is not None else "",
             _chip("ATR", f"{t.atr_pct}%") if t.atr_pct is not None else "",
             _chip("Support", t.support) if t.support is not None else "",
             _chip("Resistance", t.resistance) if t.resistance is not None else ""]
    inner = _label("Technicals") + "".join(chips)
    tr = tr or {}
    rat = _pts(tr.get("rationale"))
    if tr.get("call"):
        inner += (f'<div style="font-size:14px;margin-top:5px">{_call_pill(tr["call"], big=False)} '
                  f'<span style="color:#3c4043">{_esc(rat[0]) if rat else ""}</span></div>')
    return _box(inner)


def _verdict(s, is_pick=False):
    """Compact bull / bear / judge — pointers only."""
    d = s.get("debate") or {}
    v = d.get("verdict") or {}
    bull, bear = _pts(d.get("bull"))[:3], _pts(d.get("bear"))[:3]
    if not (v.get("call") or bull or bear):
        return ""
    call = (v.get("call") or "").lower()
    shown = "watch" if (is_pick and call == "hold") else call
    conv = (v.get("conviction") or "").lower()
    head = _label("Verdict" + (" — single fund read" if d.get("mode") == "single" else " — bull · bear · judge"))
    if call:
        head += (f'<div style="margin:2px 0 4px">{_call_pill(shown)} '
                 f'<span style="font-size:13px;font-weight:700;color:'
                 f'{ {"high": _GREEN, "medium": _AMBER}.get(conv, "#9aa0a6") }">{_esc(conv.upper())} conviction</span></div>')
    if v.get("withheld_call"):
        head += (f'<div style="font-size:13px;color:{_AMBER}">Directional call ({_esc(v["withheld_call"])}) withheld — '
                 f'the judge could not support it with a complete catalyst thesis.</div>')
    prior = s.get("prior_verdict")
    chg = v.get("changed_since_last_week")
    if prior or chg:
        was = (f'Last week: <b>{_esc(str(prior.get("call", "")).upper())}</b> at {_esc(prior.get("price"))}'
               f' ({_esc(prior.get("date"))})' if prior else "")
        head += (f'<div style="font-size:14px;color:#3c4043;margin:3px 0">🔁 {was}'
                 + (" — " if was and chg else "") + (f'{_esc(chg)}' if chg else "") + '</div>')
    head += _bullets(v.get("pointers"), fs="14px", color="#1a1a1a", limit=3)
    cols = ""
    if bull or bear:
        def col(title, pts, fg, bg):
            items = "".join(f'<div style="margin:3px 0;padding-left:12px;text-indent:-10px">•&nbsp;{_esc(p)}</div>'
                            for p in pts) or '<div style="color:#9aa0a6">—</div>'
            return (f'<td style="width:50%;vertical-align:top;background:{bg};border-radius:6px;padding:7px 9px;'
                    f'font-size:13px;color:#1a1a1a"><div style="font-weight:700;color:{fg};margin-bottom:2px">'
                    f'{title}</div>{items}</td>')
        cols = ('<table style="width:100%;border-collapse:separate;border-spacing:0 0;margin-top:6px"><tr>'
                + col("🐂 Bull", bull, _GREEN, "#f0f8f2") + '<td style="width:6px"></td>'
                + col("🐻 Bear", bear, _RED, "#fdf2f1") + '</tr></table>')
    tail = ""
    if v.get("key_risk"):
        tail += f'<div style="font-size:13px;margin-top:6px"><b>Key risk:</b> {_esc(_pts(v["key_risk"])[0] if _pts(v["key_risk"]) else "")}</div>'
    if v.get("what_would_change_it"):
        w = _pts(v["what_would_change_it"])
        tail += f'<div style="font-size:13px;color:{_GREY}"><b>Would change the call:</b> {_esc(w[0] if w else "")}</div>'
    return _box(head + cols + tail, bg="#f7f9fc", border="#dfe5ee")


# --------------------------------------------------------------------------- #
#  WEEKLY PORTFOLIO
# --------------------------------------------------------------------------- #
def _progression_table(p, cur=""):
    days = (p or {}).get("days") or []
    if not days:
        return ""
    head = "".join(f'<td style="padding:3px 6px;font-size:12px;color:#80868b;text-align:center;white-space:nowrap">'
                   f'{_esc(d["date"][:6])}</td>' for d in days)
    vals = "".join(f'<td style="padding:3px 6px;font-size:13px;text-align:center">{_pct(d.get("pct"), bold=False)}</td>'
                   for d in days)
    closes = "".join(f'<td style="padding:0 6px 3px;font-size:12px;color:{_GREY};text-align:center">{d["close"]:,}</td>'
                     for d in days)
    extra = []
    if p.get("vs_bench_pts") is not None:
        extra.append(f'vs benchmark <b>{p["vs_bench_pts"]:+.2f} pts</b>')
    if p.get("weeks"):
        extra.append("last 4 weeks " + " → ".join(_pct(w, bold=False) for w in p["weeks"] if w is not None))
    if p.get("rsi_now") is not None:
        extra.append(f'RSI {p["rsi_week_ago"]} → {p["rsi_now"]}')
    if p.get("vol_week_vs_avg") is not None:
        extra.append(f'volume {p["vol_week_vs_avg"]}× normal')
    return (f'<table style="border-collapse:collapse;margin:2px 0">'
            f'<tr>{head}</tr><tr>{vals}</tr><tr>{closes}</tr></table>'
            + (f'<div style="font-size:13px;color:{_GREY};margin-top:3px">{" · ".join(extra)}</div>' if extra else ""))


def _component_block(tk, s, extras):
    """Per-component news for a fund — only components that HAD news."""
    hn = (extras.get("etf_holding_news") or {}).get(tk, {}) or {}
    ai = {str(e.get("symbol", "")).upper(): e for e in ((s.get("etf") or {}).get("holdings_news") or [])
          if isinstance(e, dict)}
    prof = (extras.get("etf") or {}).get(tk)
    by_sym = {h.symbol.upper(): h for h in (prof.holdings if prof else [])}
    blocks = []
    for sym, arts in hn.items():
        if not arts:
            continue
        u = sym.upper()
        a, h = ai.get(u, {}), by_sym.get(u)
        stats = []
        if h is not None:
            if h.weight is not None:
                stats.append(f"{h.weight*100:.1f}% of fund")
            if h.ret_1w is not None:
                stats.append(f"week {_pct(h.ret_1w, bold=False)}")
            if h.contribution_1w is not None:
                stats.append(f"{h.contribution_1w:+.2f} pts to the fund")
        name = a.get("company") or (h.name if h else "")
        if (name or "").strip().upper() == u:
            name = ""
        body = _bullets(a.get("news"), fs="14px") if a.get("news") else ""
        if a.get("impact_on_fund"):
            body += f'<div style="font-size:13px;color:{_GREY};margin-top:2px"><b>For the fund:</b> {_esc(a["impact_on_fund"])}</div>'
        blocks.append(
            f'<div style="border-top:1px solid #eef0f2;padding:6px 0">'
            f'<div style="font-size:14px"><b>{_esc(sym)}</b> <span style="color:#80868b">{_esc((name or "")[:40])}</span>'
            + (f' &nbsp;{_sent_pill(a.get("call"))}' if a.get("call") else "")
            + (f'<span style="font-size:13px;color:{_GREY}"> · {" · ".join(stats)}</span>' if stats else "")
            + f'</div>{body}{_links(arts, label="Articles", limit=2)}</div>')
    if not blocks:
        return ""
    return _box(_label("Inside the fund — component company news this week", _BLUE) + "".join(blocks),
                bg="#ffffff", border="#dfe5ee")


def _holding_card(tk, s, funds, extras, by_group):
    f = funds.get(tk)
    is_etf = bool(f and f.is_etf)
    prog = (extras.get("progression") or {}).get(tk) or {}
    arts = by_group.get(tk, [])
    has_news = bool(arts) or any((extras.get("etf_holding_news") or {}).get(tk, {}).values())
    H = ['<div style="border:1px solid #e3e6ea;border-radius:10px;padding:14px 15px;margin:0 0 16px;background:#fff">']
    price = f"{_num(f.price)}" if f and f.price is not None else ""
    week = _pct(prog.get("week_pct")) if prog.get("week_pct") is not None else ""
    pills = (_impact_pill(s.get("impact")) + " " + _sent_pill(s.get("sentiment"))) if has_news else ""
    H.append(f'<table style="width:100%;border-collapse:collapse"><tr>'
             f'<td style="vertical-align:top"><span style="font-size:19px;font-weight:800">{_esc(tk)}</span> '
             f'<span style="color:{_GREY};font-size:14px">{_esc(f.name if f else "")}</span>'
             f'<div style="font-size:15px;margin-top:1px">{price}'
             + (f' &nbsp;· week {week}' if week else "") + '</div></td>'
             f'<td style="text-align:right;vertical-align:top;white-space:nowrap">{pills}</td></tr></table>')
    e = (extras.get("earnings") or {}).get(tk) or {}
    badges = []
    if e.get("recent"):
        rc = e["recent"]; sp = rc.get("eps_surprise_pct")
        if sp is None:       # only the date is known (e.g. PSX) — no beat/miss claim
            badges.append(_pill(f"results out {rc.get('date')}", _BLUE, "#eef3fb"))
        else:
            beat = sp > 0
            badges.append(_pill(f"reported {rc.get('date')}: EPS {'beat' if beat else 'miss'} {sp:+.1f}%",
                                *(_SENT["bullish"] if beat else _SENT["bearish"])))
    if e.get("upcoming"):
        u = e["upcoming"]
        try:
            when = dt.date.fromisoformat(str(u.get("date"))).strftime("%d %b")
        except ValueError:
            when = str(u.get("date"))
        badges.append(_pill(f"{extras.get('earnings_label', 'earnings')} {when} ({u.get('days_away')}d)",
                            _AMBER, "#fdf5e0"))
    if is_etf:
        badges.append(_pill("ETF / fund", _BLUE, "#eef3fb"))
    if badges:
        H.append(f'<div style="margin:6px 0">{" ".join(badges)}</div>')

    # 1) This week's news — only if there WAS news
    if _pts(s.get("summary")):
        inner = _label("This week's news", _BLUE) + _bullets(s.get("summary"), fs="15px", color="#1a1a1a")
        if _pts(s.get("news_impact")):
            inner += f'<div style="margin-top:4px"><b style="font-size:14px">Impact:</b>{_bullets(s.get("news_impact"), fs="14px")}</div>'
        H.append(_box(inner, bg="#f5f8fe", border="#d9e3f5"))
    etf_an = s.get("etf") or {}
    if is_etf and _pts(etf_an.get("move_explainer")):
        H.append(_box(_label("What moved the fund this week", _BLUE) + _bullets(etf_an["move_explainer"], fs="14px"),
                      bg="#f5f8fe", border="#d9e3f5"))
    if is_etf:
        H.append(_component_block(tk, s, extras))

    cw = (extras.get("crowd") or {}).get(tk) or {}
    if cw.get("has_data"):
        H.append(f'<div style="font-size:13px;color:{_GREY};margin:4px 0">👥 {_crowd_line(cw)}</div>')

    # 2) How it travelled over the week
    ptab = _progression_table(prog)
    pro_ai = _bullets(s.get("progression"), fs="14px")
    if ptab or pro_ai:
        H.append(_box(_label("How it travelled this week") + ptab + pro_ai))

    # 3) Earnings & outlook
    ed = s.get("earnings") or {}
    if any(ed.get(k) for k in ("result", "outlook", "management_review")):
        inner = _label("Earnings & outlook", _GREEN)
        for k, lab in (("result", "Result"), ("outlook", "Outlook"), ("management_review", "Management")):
            if ed.get(k):
                inner += f'<div style="font-size:14px;margin:2px 0"><b>{lab}:</b> {_esc(ed[k])}</div>'
        H.append(_box(inner, bg="#f3f9f4", border="#d5ead9"))

    # 4) Technicals + fundamentals (compact)
    H.append(_tech_line((extras.get("technicals") or {}).get(tk), s.get("technical_read")))
    if is_etf and f:
        prof = (extras.get("etf") or {}).get(tk)
        r = (prof.returns if prof else {}) or {}
        rows = [("Returns", " · ".join(f"{k} {_pct(r.get(k), bold=False)}" for k in ("1m", "3m", "ytd", "1y")
                                       if r.get(k) is not None)),
                ("Fund", " · ".join(x for x in [f"expense {_expense(prof.expense_ratio)}" if prof and prof.expense_ratio is not None else "",
                                                f"AUM {_money(prof.aum, extras.get('currency_symbol', '$'))}" if prof and prof.aum else "",
                                                f"top-10 {prof.top10_weight}%" if prof and prof.top10_weight else ""] if x))]
        g = _grid(rows)
        if g:
            H.append(_box(_label("Fund data") + g))
    elif f:
        rows = [("Valuation", " · ".join(x for x in [f"P/E {_num(f.pe, nd=1)}" if (f.pe or 0) > 0 else "",
                                                     f"fwd {_num(f.forward_pe, nd=1)}" if (f.forward_pe or 0) > 0 else "",
                                                     f"P/S {_num(f.ps, nd=1)}" if f.ps else ""] if x)),
                ("Growth / margin", " · ".join(x for x in [f"rev {_num(f.rev_growth, pct=True)}" if f.rev_growth is not None else "",
                                                           f"net margin {_num(f.net_margin, pct=True)}" if f.net_margin else "",
                                                           f"ROE {_num(f.roe, pct=True)}" if f.roe is not None else ""] if x)),
                ("Analysts", f"target {_num(f.target_mean)} ({_num(f.implied_upside, '%', nd=1)})" if f.target_mean else "")]
        g = _grid(rows)
        if g or s.get("fundamental_read"):
            H.append(_box(_label("Fundamentals") + g
                          + (f'<div style="font-size:14px;color:{_GREY};margin-top:3px">{_esc(s["fundamental_read"])}</div>'
                             if s.get("fundamental_read") else "")))
    fil = (extras.get("filings") or {}).get(tk)
    if fil:
        H.append(f'<div style="font-size:14px;margin:4px 0">📄 <a href="{_esc(fil["url"])}" style="color:{_BLUE}">'
                 f'{_esc(fil["form"])} filed {_esc(fil["filed"])}</a></div>')

    # 5) Verdict, then sources last
    H.append(_verdict(s))
    H.append(_links(arts, label="This week's articles", limit=6))
    H.append("</div>")
    return "".join(H)


def _scoreboard(stocks, funds, extras):
    progs = extras.get("progression") or {}
    rows = ""
    for s in stocks:
        tk = s.get("ticker")
        f, p = funds.get(tk), progs.get(tk) or {}
        v = ((s.get("debate") or {}).get("verdict") or {}).get("call")
        prior = (s.get("prior_verdict") or {}).get("call")
        verdict = (f'{_esc(str(prior).upper())} → ' if prior and v and prior != v else "") + (_esc(str(v).upper()) if v else "–")
        rows += (f'<tr><td style="padding:5px 8px 5px 0;font-weight:700">{_esc(tk)}</td>'
                 f'<td style="padding:5px 8px;text-align:right">{_num(f.price) if f and f.price is not None else ""}</td>'
                 f'<td style="padding:5px 8px;text-align:right">{_pct(p.get("week_pct"))}</td>'
                 f'<td style="padding:5px 8px;text-align:right">{_pct(p.get("m1_pct"), bold=False)}</td>'
                 f'<td style="padding:5px 0 5px 8px;text-align:right;font-size:13px;white-space:nowrap">{verdict}</td></tr>')
    if not rows:
        return ""
    head = ('<tr style="color:#80868b;font-size:12px;text-transform:uppercase;letter-spacing:.3px">'
            '<td style="padding:0 8px 4px 0">Holding</td><td style="padding:0 8px 4px;text-align:right">Price</td>'
            '<td style="padding:0 8px 4px;text-align:right">Week</td><td style="padding:0 8px 4px;text-align:right">1 month</td>'
            '<td style="padding:0 0 4px 8px;text-align:right">Verdict</td></tr>')
    return _box(f'<table style="width:100%;border-collapse:collapse;font-size:14px">{head}{rows}</table>')


def _lookthrough(lt):
    # Only meaningful when funds are held — with direct stocks only it just
    # restates the weights.
    if not lt or not lt.get("companies") or not any(c.get("via") for c in lt["companies"]):
        return ""
    comps = lt["companies"][:10]
    mx = max((c["total_pct"] for c in comps), default=1) or 1
    rows = ""
    for c in comps:
        w = max(2, int(c["total_pct"] / mx * 100))
        via = ""
        if c.get("via") and not c.get("direct_pct"):
            via = "via " + ", ".join(v["etf"] for v in c["via"][:3])
        elif c.get("direct_pct") and c.get("via"):
            via = f"direct {c['direct_pct']}% + funds"
        rows += (f'<tr><td style="padding:2px 8px 2px 0;font-size:14px;white-space:nowrap"><b>{_esc(c["ticker"])}</b>'
                 + (' <span style="color:#8a6d00">◆</span>' if c.get("overlap") else "") + '</td>'
                 f'<td style="width:55%;padding:2px 6px"><div style="background:#e9ecef;border-radius:4px;height:10px">'
                 f'<div style="width:{w}%;height:10px;border-radius:4px;background:{_BLUE}"></div></div></td>'
                 f'<td style="padding:2px 0 2px 6px;font-size:14px;text-align:right;font-weight:700">{c["total_pct"]}%</td>'
                 f'<td style="padding:2px 0 2px 8px;font-size:12px;color:#9aa0a6">{_esc(via)}</td></tr>')
    flags = "".join(f'<div style="font-size:13px;color:#6b5300;margin:2px 0">⚠️ {_esc(x)}</div>' for x in (lt.get("flags") or []))
    note = ("Equal weights assumed (add a weight to each holding in config.yaml). " if lt.get("equal_weight") else "")
    return (_h2("Portfolio look-through", "true company exposure across direct holdings and what your ETFs hold")
            + _box(flags + f'<table style="width:100%;border-collapse:collapse">{rows}</table>'
                   + f'<div style="font-size:12px;color:#9aa0a6;margin-top:4px">{note}◆ = held in more than one place. '
                     'ETF figures use each fund\'s disclosed top holdings.</div>'))


def build_portfolio(analysis, items, funds, extras):
    region = extras.get("region", "US")
    by_group = defaultdict(list)
    for it in items:
        by_group[it.group].append(it)
    status = analysis.get("_status", {})
    stocks = [s for s in analysis.get("stocks", []) if s.get("ticker")]
    order = [s.get("ticker") for s in extras.get("holdings_order", [])] or [s["ticker"] for s in stocks]
    stocks.sort(key=lambda s: order.index(s["ticker"]) if s["ticker"] in order else 99)
    period = extras.get("period_label", "")
    week_end = _now_local().strftime("%a %d %b %Y")
    subject = f"📊 Weekly Portfolio Digest {region} — week to {_now_local():%d %b}"
    B = []
    ov = _bullets(analysis.get("week_overview"), fs="15px", color="#1a1a1a", limit=5)
    if ov:
        B.append(f'<div style="background:#f6f8fa;border-left:4px solid {_BLUE};padding:10px 14px;border-radius:6px;margin-bottom:12px">'
                 f'{_label("Your portfolio this week", _BLUE)}{ov}</div>')
    B.append(_scoreboard(stocks, funds, extras))
    prio = [p for p in (analysis.get("priority") or []) if p.get("ticker") and p.get("why")][:4]
    if prio:
        B.append(_h2("What mattered most"))
        B.append("".join(f'<div style="font-size:15px;margin:4px 0"><b>{_esc(p["ticker"])}</b> — {_esc(p["why"])}</div>'
                         for p in prio))
    B.append(_h2("Your holdings", "this week's news, how each name travelled, and the verdict"))
    for s in stocks:
        B.append(_holding_card(s["ticker"], s, funds, extras, by_group))
    B.append(_lookthrough(extras.get("look_through")))
    ev = extras.get("holding_events") or []
    if ev:
        B.append(_h2("Coming up for your holdings"))
        B.append("".join(f'<div style="font-size:15px;margin:4px 0"><b>{_esc(e["date"].strftime("%a %d %b"))}</b> — '
                         f'{_esc(e["ticker"])}: {_esc(e["title"])}</div>' for e in ev))
    risks = _pts(analysis.get("risks"))
    if risks:
        B.append(_h2("Risks to your holdings"))
        B.append(_bullets(risks, limit=5))
    B.append('<div style="margin-top:18px;font-size:13px;color:#80868b">How to read: <b>Week</b> = last 5 sessions; '
             '<b>vs benchmark</b> = the stock\'s week minus the benchmark\'s. <b>Verdict</b> = a judge model\'s call after '
             'reading a bull and a bear analyst, refreshed weekly and compared with last week\'s. Technicals are context, '
             'not the basis of the call.</div>')
    html_body = _wrap(f"Weekly Portfolio Digest {region}", f"Week to {week_end}", period, _banner(status),
                      "".join(B), _footer(extras.get("footer_sources", "")))
    T = [f"WEEKLY PORTFOLIO DIGEST {region} — week to {week_end}", period, ""]
    T += [f"- {p}" for p in _pts(analysis.get("week_overview"))]
    for s in stocks:
        tk = s["ticker"]; f = funds.get(tk); p = (extras.get("progression") or {}).get(tk) or {}
        T.append(f"\n{tk}  {_num(f.price) if f and f.price is not None else ''}"
                 + (f"  week {p['week_pct']:+.2f}%" if p.get("week_pct") is not None else ""))
        T += [f"  - {x}" for x in _pts(s.get("summary"))]
        v = (s.get("debate") or {}).get("verdict") or {}
        if v.get("call"):
            T.append(f"  VERDICT: {v['call'].upper()} ({v.get('conviction', '')}) — " + "; ".join(_pts(v.get("pointers"))))
    return subject, html_body, "\n".join(T)


# --------------------------------------------------------------------------- #
#  DAILY MARKET
# --------------------------------------------------------------------------- #
def _flags_grid(flags, per_row=4):
    if not flags:
        return ""
    cells = []
    for fl in flags:
        p = fl.get("pct")
        price = fl.get("price")
        ptxt = f"{price:,.2f}" if isinstance(price, (int, float)) else _esc(price)
        if fl.get("bp") is not None:
            ptxt += "%"
            c = _GREEN if fl["bp"] >= 0 else _RED
            move = f'<span style="color:{c};font-weight:600">{fl["bp"]:+d} bp</span>'
        else:
            move = _pct(p) if p is not None else ""
        cells.append(f'<td style="width:{100//per_row}%;padding:7px 6px;border:1px solid #eef0f2;text-align:center;vertical-align:top">'
                     f'<div style="font-size:12px;color:{_GREY}">{_esc(fl["name"])}</div>'
                     f'<div style="font-size:15px;font-weight:700">{ptxt}</div>'
                     f'<div style="font-size:13px">{move}</div></td>')
    rows = ""
    for i in range(0, len(cells), per_row):
        chunk = cells[i:i + per_row]
        chunk += ['<td style="border:none"></td>'] * (per_row - len(chunk))
        rows += "<tr>" + "".join(chunk) + "</tr>"
    return f'<table style="border-collapse:collapse;width:100%;table-layout:fixed;margin:4px 0 6px">{rows}</table>'


def _facts(pairs):
    """[(label, value)] → one compact line of computed facts."""
    pairs = [(l, v) for l, v in (pairs or []) if v not in (None, "")]
    if not pairs:
        return ""
    return ('<div style="font-size:14px;color:#3c4043;margin-bottom:6px;line-height:1.7">'
            + " · ".join(f'{_esc(l)} <b style="white-space:nowrap">{_esc(v)}</b>' for l, v in pairs) + '</div>')


def _released_table(rel):
    if not rel:
        return ""
    head = ('<tr style="color:#80868b;font-size:12px;text-transform:uppercase"><td style="padding:0 8px 4px 0">Release</td>'
            '<td style="padding:0 8px 4px;text-align:right">Actual</td><td style="padding:0 8px 4px;text-align:right">Consensus</td>'
            '<td style="padding:0 0 4px 8px;text-align:right">Previous</td></tr>')
    rows = "".join(
        f'<tr><td style="padding:4px 8px 4px 0;font-size:14px"><b>{_esc(e["label"])}</b>'
        f'<div style="font-size:12px;color:#9aa0a6">{_esc(e["when"].strftime("%a %d %b, %H:%M ET"))}</div></td>'
        f'<td style="padding:4px 8px;text-align:right;font-weight:700;font-size:14px">{_esc(e["actual"])}</td>'
        f'<td style="padding:4px 8px;text-align:right;font-size:14px">{_esc(e["consensus"] or "–")}</td>'
        f'<td style="padding:4px 0 4px 8px;text-align:right;font-size:14px;color:{_GREY}">{_esc(e["previous"] or "–")}</td></tr>'
        for e in rel)
    return f'<table style="width:100%;border-collapse:collapse">{head}{rows}</table>'


def _calendar_table(events, fmt_when, note=""):
    if not events:
        return ""
    rows = ""
    for e in events:
        meta = []
        if e.get("consensus"):
            meta.append(f"consensus {e['consensus']}")
        if e.get("previous"):
            meta.append(f"previous {e['previous']}")
        rows += (f'<tr><td style="width:1%;padding:6px 14px 6px 0;vertical-align:top;font-size:13px;color:#3c4043;white-space:nowrap">'
                 f'{_esc(fmt_when(e["when"], e.get("timed", True))).replace(" · ", "<br>")}</td>'
                 f'<td style="padding:6px 0;vertical-align:top;font-size:14px"><b>{_esc(e["label"])}</b>'
                 + (f' <span style="color:#80868b;font-size:12px">({_esc(e.get("region"))})</span>' if e.get("region") else "")
                 + (f'<div style="font-size:13px;color:{_GREY}">{_esc(" · ".join(meta))}</div>' if meta else "")
                 + (f'<div style="font-size:12px;color:#9aa0a6">{_esc(e.get("why", ""))}</div>' if e.get("why") else "")
                 + '</td></tr>')
    return (f'<table style="width:100%;border-collapse:collapse">{rows}</table>'
            + (f'<div style="font-size:12px;color:#9aa0a6;margin-top:4px">{note}</div>' if note else ""))


def _thesis_block(p):
    v = ((p.get("debate") or {}).get("verdict") or {})
    th = v.get("thesis")
    if not th:
        return ""
    c = p.get("computed") or {}
    cur = c.get("currency") or ""
    figs = []
    for lab, val in (("price", c.get("price")), ("support", c.get("support")), ("resistance", c.get("resistance"))):
        if val is not None:
            figs.append(_chip(lab, f"{val:,.2f}"))
    if c.get("atr_pct") is not None:
        figs.append(_chip("typical day (ATR)", f"{c['atr_pct']}%"))
    if c.get("upside_to_res_pct") is not None:
        figs.append(_chip("to resistance", f"{c['upside_to_res_pct']:+.1f}%"))
        figs.append(_chip("to support", f"{c['downside_to_sup_pct']:+.1f}%"))
    if c.get("dist_sma50") is not None:
        figs.append(_chip("vs 50d avg", f"{c['dist_sma50']:+.1f}%"))
    nc = c.get("next_catalyst")
    if nc:
        figs.append(_chip("next event", f"{nc['title']} {nc['date']}"))
    rows = ""
    for key, lab in (("catalyst", "Catalyst"), ("mechanism", "How it reaches the price"), ("priced_in", "Priced in"),
                     ("horizon", "Horizon"), ("invalidation", "What would prove it wrong"), ("before_acting", "Verify first")):
        if th.get(key):
            style = f"background:#fdf2f1;border-radius:5px;padding:3px 6px;" if key == "invalidation" else ""
            rows += f'<div style="font-size:14px;margin:4px 0;{style}"><b>{lab}:</b> {_esc(th[key])}</div>'
    return (f'<div style="border:2px solid {_BLUE};border-radius:9px;margin:8px 0;overflow:hidden">'
            f'<div style="background:{_BLUE};color:#fff;padding:6px 11px;font-size:13px;font-weight:800;letter-spacing:.3px">'
            f'CATALYST THESIS — {_esc(p["ticker"])} · {_esc(str(v.get("call", "")).upper())}</div>'
            f'<div style="background:#f4f7fc;padding:6px 10px;border-bottom:1px solid #dfe5ee">'
            f'<div style="font-size:11px;color:#80868b;text-transform:uppercase;letter-spacing:.4px">Computed from price data — no AI'
            + (f' ({_esc(cur)})' if cur and cur != "USD" else "") + f'</div>{"".join(figs)}</div>'
            f'<div style="padding:6px 11px">{rows}</div></div>')


def _pick_card(p, funds, extras, by_group):
    currency_symbol = extras.get("currency_symbol", "$")
    tk = p["ticker"]
    f = funds.get(tk)
    v = ((p.get("debate") or {}).get("verdict") or {})
    call = (v.get("call") or "").lower()
    shown = "watch" if call in ("", "hold") else call
    H = ['<div style="border:1px solid #e3e6ea;border-radius:10px;padding:14px 15px;margin:0 0 16px;background:#fff">']
    price = _num(f.price) if f and f.price is not None else ""
    chg = _pct(f.change_1d) if f and f.change_1d is not None else ""
    H.append(f'<table style="width:100%;border-collapse:collapse"><tr><td style="vertical-align:top">'
             f'<span style="font-size:19px;font-weight:800">{_esc(tk)}</span> '
             f'<span style="color:{_GREY};font-size:14px">{_esc((f.name if f else "") or "")}</span>'
             f'<div style="font-size:15px">{price} {chg}</div></td>'
             f'<td style="text-align:right;vertical-align:top">{_call_pill(shown)}</td></tr></table>')
    sh = (extras.get("shariah") or {}).get(tk)
    if sh:
        col = {"pass": _SENT["bullish"], "review": _SENT["mixed"], "fail": _SENT["bearish"]}.get(sh["status"], _SENT["neutral"])
        lab = sh.get("label") or {"pass": "✓ Shariah screen passed", "review": "⚠ Shariah: needs review",
                                  "fail": "✗ Shariah: fails"}.get(sh["status"], "")
        H.append(f'<div style="margin:4px 0">{_pill(lab, *col)}</div>')
    inner = f'<div style="font-size:15px;color:#1a1a1a"><b>Catalyst:</b> {_esc(p.get("catalyst"))}</div>'
    if p.get("why_now"):
        inner += f'<div style="font-size:14px;color:#3c4043;margin-top:2px"><b>Why now:</b> {_esc(p["why_now"])}</div>'
    H.append(_box(inner, bg="#fffaf0", border="#f0e2bd"))
    H.append(_thesis_block(p))
    H.append(_verdict(p, is_pick=True))
    H.append(_tech_line((extras.get("technicals") or {}).get(tk)))
    if f and not f.is_etf:
        g = _grid([("Valuation", " · ".join(x for x in [f"P/E {_num(f.pe, nd=1)}" if (f.pe or 0) > 0 else "",
                                                       f"fwd {_num(f.forward_pe, nd=1)}" if (f.forward_pe or 0) > 0 else "",
                                                       f"mkt cap {_money(f.market_cap, currency_symbol)}" if f.market_cap else ""] if x)),
                   ("Growth / margin", " · ".join(x for x in [f"rev {_num(f.rev_growth, pct=True)}" if f.rev_growth is not None else "",
                                                              f"net margin {_num(f.net_margin, pct=True)}" if f.net_margin else ""] if x)),
                   ("Analysts", f"target {_num(f.target_mean)} ({_num(f.implied_upside, '%', nd=1)})" if f.target_mean else "")])
        if g:
            H.append(_box(_label("Fundamentals") + g))
    H.append(_links(by_group.get(tk, []), label="Articles", limit=4))
    H.append("</div>")
    return "".join(H)


def _sector_table(moves, label="Sector ETF", cols=("1 day", "5 days")):
    rows = [r for r in (moves or []) if r.get("pct_1d") is not None]
    if not rows:
        return ""
    rows.sort(key=lambda r: -(r["pct_1d"]))
    cells = "".join(f'<tr><td style="padding:3px 8px 3px 0;font-size:14px">{_esc(r["label"])} '
                    f'<span style="color:#9aa0a6;font-size:12px">{_esc(r.get("note") or r["symbol"])}</span></td>'
                    f'<td style="padding:3px 8px;text-align:right;font-size:14px">{_pct(r["pct_1d"])}</td>'
                    f'<td style="padding:3px 0 3px 8px;text-align:right;font-size:13px">{_pct(r.get("pct_5d"), bold=False)}</td></tr>'
                    for r in rows)
    head = (f'<tr style="color:#80868b;font-size:12px;text-transform:uppercase"><td style="padding:0 8px 3px 0">{_esc(label)}</td>'
            f'<td style="padding:0 8px 3px;text-align:right">{_esc(cols[0])}</td>'
            f'<td style="padding:0 0 3px 8px;text-align:right">{_esc(cols[1])}</td></tr>')
    return _box(f'<table style="width:100%;border-collapse:collapse">{head}{cells}</table>')


def build_market(an, items, funds, extras, fmt_when):
    region = extras.get("region", "US")
    flag = {"US": "🇺🇸", "PK": "🇵🇰"}.get(region, "🌐")
    by_group = defaultdict(list)
    for it in items:
        by_group[it.group].append(it)
    status = an.get("_status", {})
    today = _now_local().strftime("%A, %d %B %Y")
    subject = f"{flag} Market Digest {region} — {_now_local():%a %d %b}"
    cal = extras.get("calendar") or {}
    B = []

    tldr = _pts(an.get("tldr"))[:5]
    if tldr:
        B.append('<div style="background:#16191d;color:#fff;border-radius:10px;padding:12px 16px;margin-bottom:14px">'
                 '<div style="font-size:12px;font-weight:700;letter-spacing:.6px;color:#9aa0a6;margin-bottom:4px">TL;DR</div>'
                 + "".join(f'<div style="font-size:15px;margin:5px 0;padding-left:14px;text-indent:-12px">•&nbsp;{_esc(t)}</div>'
                           for t in tldr) + '</div>')
    if extras.get("flags"):
        B.append(_h2("Market levels", "close and 1-day move"))
        B.append(_flags_grid(extras["flags"]))

    # Policy & rates — computed facts (rate, next decision, yields) + the AI's read
    facts = _facts(extras.get("policy_facts"))
    pol = _bullets(an.get("policy_rates"))
    if facts or pol:
        B.append(_h2("Policy & rates"))
        B.append(_box(facts + pol + _links(by_group.get("Policy & rates", []), limit=3)))
    # Macro data — released prints (actual vs consensus) / latest indicators + the AI's read
    rel = cal.get("released") or []
    mfacts = _facts(extras.get("macro_facts"))
    mac = _bullets(an.get("macro_data"))
    if rel or mac or mfacts:
        B.append(_h2("Macro data", extras.get("macro_sub", "released prints: actual vs consensus")))
        B.append(_box(mfacts + _released_table(rel) + mac + _links(by_group.get("Macro data", []), limit=3)))
    # Global
    glo = _bullets(an.get("global"))
    if glo:
        B.append(_h2("Global news & impact"))
        B.append(_box(glo + _links(by_group.get("Global", []), limit=3)))
    # Sectors
    secs = an.get("sectors") or []
    moves = {r["symbol"]: r for r in (extras.get("sector_moves") or [])}
    theme_etf = extras.get("theme_etf") or {}
    if secs or moves:
        B.append(_h2("Sectors", "only sectors with news in the window"))
        B.append(_sector_table(extras.get("sector_moves"), extras.get("sector_table_label", "Sector ETF"),
                               extras.get("sector_cols", ("1 day", "5 days"))))
        for sec in secs:
            th = sec.get("theme", "")
            mv = moves.get(theme_etf.get(th))
            mtxt = (f' <span style="font-size:13px">{_esc(mv["symbol"]) if mv["symbol"] != th else ""} '
                    f'{_pct(mv["pct_1d"])}</span>' if mv and mv.get("pct_1d") is not None else "")
            B.append(_box(f'<table style="width:100%;border-collapse:collapse"><tr><td style="font-size:16px;font-weight:700">'
                          f'{_esc(th)}{mtxt}</td><td style="text-align:right">{_sent_pill(sec.get("call"))}</td></tr></table>'
                          + _bullets(sec.get("points"), limit=3)
                          + _links(by_group.get(th, []), label="Headlines" if sec.get("headlines_only") else "Sources",
                                   limit=4 if sec.get("headlines_only") else 3)))
    # Stocks to watch (≤3)
    picks = an.get("stocks_to_watch") or []
    if picks:
        sub = "at most three, each with a named catalyst; buy/sell calls carry a Catalyst Thesis"
        if extras.get("shariah"):
            sub += extras.get("shariah_sub", " · Shariah-screened")
        B.append(_h2("Stocks to watch", sub))
        for p in picks:
            B.append(_pick_card(p, funds, extras, by_group))
    # Crypto
    crypto = extras.get("crypto") or {}
    ch = an.get("crypto") or {}
    snaps = crypto.get("snapshot") or []
    if snaps:
        ai = {c.get("symbol"): c for c in (ch.get("coins") or []) if isinstance(c, dict)}
        rows = "".join(
            f'<tr><td style="padding:4px 8px 4px 0;font-weight:700;font-size:14px">{_esc(c["symbol"])}</td>'
            f'<td style="padding:4px 8px;text-align:right;font-size:14px">{c["price"]:,.2f}</td>'
            f'<td style="padding:4px 8px;text-align:right;font-size:14px">{_pct(c.get("pct"))}</td>'
            f'<td style="padding:4px 8px;text-align:right;font-size:13px">{_pct(c.get("pct7d"), bold=False)}</td>'
            f'<td style="padding:4px 0 4px 8px;text-align:right">{_call_pill(ai[c["symbol"]].get("call"), big=False) if ai.get(c["symbol"], {}).get("call") else ""}</td></tr>'
            for c in snaps)
        head = ('<tr style="color:#80868b;font-size:12px;text-transform:uppercase"><td>Coin</td><td style="text-align:right">Price</td>'
                '<td style="text-align:right">1d</td><td style="text-align:right">7d</td><td></td></tr>')
        B.append(_h2("Crypto", "major coins"))
        B.append(_box(f'<table style="width:100%;border-collapse:collapse">{head}{rows}</table>'
                      + _bullets(ch.get("points"), limit=3) + _links(by_group.get("Crypto", []), limit=3)))
    # Calendar
    up = cal.get("upcoming") or []
    if up:
        B.append(_h2("Coming up — exact dates", extras.get("calendar_sub") or
                     f"next {extras.get('calendar_days', 14)} days · times in New York (ET) and Hong Kong"))
        B.append(_box(_calendar_table(up, fmt_when, note=extras.get("calendar_note", "")), bg="#ffffff"))
    risks = _pts(an.get("risks"))
    if risks:
        B.append(_h2("Risks to watch"))
        B.append(_bullets(risks, limit=5))
    tr = extras.get("track_record") or []
    if tr:
        rows = "".join(
            f'<tr><td style="padding:3px 8px 3px 0;font-size:14px"><b>{_esc(r["ticker"])}</b> '
            f'<span style="color:#9aa0a6;font-size:12px">{_esc(r["date"][5:])}</span></td>'
            f'<td style="padding:3px 8px;font-size:13px">{_call_pill("watch" if r["call"] in ("hold", "") else r["call"], big=False)}</td>'
            f'<td style="padding:3px 8px;text-align:right;font-size:14px">{_num(r["price"])} → {_num(r.get("now"))}</td>'
            f'<td style="padding:3px 0 3px 8px;text-align:right">{_pct(r.get("pct"))}</td></tr>' for r in tr)
        B.append(_h2("Recent picks — since flagged"))
        B.append(_box(f'<table style="width:100%;border-collapse:collapse">{rows}</table>'
                      '<div style="font-size:12px;color:#9aa0a6;margin-top:4px">Price move since the day it was flagged — '
                      'a running scorecard, not a recommendation.</div>'))
    html_body = _wrap(f"{flag} Market Digest {region}", today, extras.get("period_label", ""), _banner(status),
                      "".join(B), _footer(extras.get("footer_sources", "")))
    T = [f"MARKET DIGEST {region} — {today}", extras.get("period_label", ""), ""]
    T += [f"- {t}" for t in tldr]
    for title, key in (("POLICY & RATES", "policy_rates"), ("MACRO DATA", "macro_data"), ("GLOBAL", "global")):
        pts = _pts(an.get(key))
        if pts:
            T += ["", title] + [f"- {p}" for p in pts]
    for sec in secs:
        T += ["", f"{sec.get('theme')} [{(sec.get('call') or '').upper()}]"] + [f"- {p}" for p in _pts(sec.get("points"))]
    if picks:
        T += ["", "STOCKS TO WATCH"]
        for p in picks:
            v = ((p.get("debate") or {}).get("verdict") or {})
            T.append(f"{p['ticker']}: {(v.get('call') or 'watch').upper()} — catalyst: {p.get('catalyst')}")
    if up:
        T += ["", "COMING UP"] + [f"{fmt_when(e['when'], e.get('timed', True))}  {e['label']}" for e in up]
    return subject, html_body, "\n".join(T)
