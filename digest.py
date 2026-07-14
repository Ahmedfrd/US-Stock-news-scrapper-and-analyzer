"""
digest.py — render TWO reports:
  * build_portfolio(...)  -> your holdings, full depth
  * build_market(...)     -> general market: flags, sector highlights, stocks to
                             watch (full analysis incl. technicals + calls),
                             industries/themes, weekly sector deep-dive, macro

Shared _stock_card() gives holdings and watch-stocks identical treatment.
Larger fonts + boxed panels for readability.
"""

from __future__ import annotations

import html
import datetime as dt
from collections import defaultdict

def _esc(x):
    """html.escape that tolerates non-strings (the AI occasionally returns a
    bare number where a text bullet was asked for)."""
    return html.escape(str(x)) if x is not None else ""

_SENT = {"bullish":("#0a7d33","#e6f6ec"),"bearish":("#b3261e","#fdeceb"),
         "neutral":("#5f6368","#eef0f2"),"mixed":("#8a6d00","#fdf5e0"),
         "positive":("#0a7d33","#e6f6ec"),"negative":("#b3261e","#fdeceb"),"n/a":("#9aa0a6","#f1f3f4")}
_IMPACT = {"high":("#b3261e","#fdeceb"),"medium":("#8a6d00","#fdf5e0"),"low":("#5f6368","#eef0f2")}
_CALL = {"buy":("#0a7d33","#e6f6ec"),"accumulate":("#0a7d33","#eef7f0"),"hold":("#5f6368","#eef0f2"),
         "reduce":("#b3261e","#fdeef0"),"sell":("#b3261e","#fdeceb")}

def _pill(t,fg,bg,big=False):
    fs="14px" if big else "13px"; pad="4px 12px" if big else "3px 9px"
    return (f'<span style="background:{bg};color:{fg};font-size:{fs};font-weight:700;padding:{pad};'
            f'border-radius:11px;text-transform:uppercase;letter-spacing:.4px;white-space:nowrap">{_esc(str(t))}</span>')
def _sent_pill(s): fg,bg=_SENT.get((s or "neutral").lower(),_SENT["neutral"]); return _pill(s or "neutral",fg,bg)
def _impact_pill(s): fg,bg=_IMPACT.get((s or "low").lower(),_IMPACT["low"]); return _pill(f"impact {s or 'low'}",fg,bg)
def _call_pill(c): fg,bg=_CALL.get((c or "hold").lower(),_CALL["hold"]); return _pill(c or "hold",fg,bg,big=True)

def _pct_span(p):
    if p is None: return ""
    c="#0a7d33" if p>=0 else "#b3261e"; a="▲" if p>=0 else "▼"
    return f'<span style="color:{c};font-weight:600">{a} {p:+.2f}%</span>'

def _num(x,s="",pct=False,nd=2):
    if x is None: return "n/a"
    try: return f"{x*100:.1f}%" if pct else f"{x:.{nd}f}{s}"
    except Exception: return str(x)

def _expense(x):
    """x is already a percent-number (0.03 == 0.03%)."""
    return f"{x:.2f}%" if x is not None else "n/a"

def _money(x):
    if x is None: return "n/a"
    try:
        x=float(x)
        for u,d in (("T",1e12),("B",1e9),("M",1e6)):
            if abs(x)>=d: return f"${x/d:.2f}{u}"
        return f"${x:,.0f}"
    except Exception: return "n/a"

def _bar(label,val):
    if val is None: return f'<div style="font-size:14px;color:#9aa0a6;margin:3px 0">{label}: n/a</div>'
    hue=int(1.2*val)
    return (f'<div style="display:flex;align-items:center;gap:8px;margin:3px 0">'
            f'<span style="font-size:14px;color:#5f6368;width:80px">{label}</span>'
            f'<span style="flex:1;background:#e9ecef;border-radius:5px;height:12px;display:inline-block">'
            f'<span style="display:block;height:12px;border-radius:5px;width:{val}%;background:hsl({hue},60%,45%)"></span></span>'
            f'<span style="font-size:14px;color:#3c4043;width:30px;text-align:right;font-weight:600">{val:.0f}</span></div>')

def _grid(rows):
    cells="".join(f'<tr><td style="padding:3px 12px 3px 0;color:#5f6368;font-size:15px;white-space:nowrap;vertical-align:top">{_esc(l)}</td>'
                  f'<td style="padding:3px 0;font-size:15px;color:#1a1a1a">{v}</td></tr>' for l,v in rows)
    return f'<table style="border-collapse:collapse;width:100%">{cells}</table>'

def _box(inner, bg="#fafbfc", border="#e9ecef"):
    return f'<div style="background:{bg};border:1px solid {border};border-radius:8px;padding:10px 12px;margin:6px 0">{inner}</div>'

def _prose(text, fs="15px", color="#3c4043"):
    """Render AI free text. The prompts ask for newline-separated '- ' lines, so
    multi-line text becomes a bullet list; single-line text stays a sentence.
    Heuristic-path plain sentences pass through unchanged."""
    if not text: return ""
    lines=[l.strip().lstrip("-•–·* ").strip() for l in str(text).splitlines() if l.strip()]
    # drop empty lines and junk bullets that are just a bare number (model slip)
    lines=[l for l in lines if l and not l.replace(".","").replace("-","").isdigit()]
    if not lines: return ""
    if len(lines)==1:
        return f'<div style="font-size:{fs};color:{color};margin:4px 0">{_esc(lines[0])}</div>'
    return "".join(f'<div style="font-size:{fs};color:{color};margin:4px 0 4px 4px">• {_esc(l)}</div>'
                   for l in lines)

def _chip(label,value,tone="neutral"):
    c={"good":"#0a7d33","bad":"#b3261e","warn":"#8a6d00","neutral":"#3c4043"}.get(tone,"#3c4043")
    return (f'<span style="display:inline-block;background:#eef1f4;border-radius:7px;padding:4px 9px;'
            f'margin:3px 5px 3px 0;font-size:14px;color:#5f6368">{_esc(label)} '
            f'<b style="color:{c}">{value}</b></span>')

def _mentions_total(cw):
    """Sum of raw mention counts across sources (how many people are talking)."""
    total = 0
    for sv in (cw.get("sources") or {}).values():
        try: total += int(float(sv.get("mentions") or 0))
        except Exception: pass
    return total

def _fmt_mentions(m):
    try: return f"{int(float(m)):,}"
    except Exception: return str(m) if m is not None else "–"

def _crowd_line(cw):
    """Compact one-liner: the blended consensus."""
    if not cw or not cw.get("has_data"):
        return '<span style="color:#9aa0a6">crowd: no measurable discussion</span>'
    c = cw.get("consensus", {})
    bits = [f'crowd {_sent_pill(c.get("label"))}']
    if c.get("bullish") is not None:
        bits.append(f'{c["bullish"]}%▲ / {c["bearish"]}%▼ / {c["neutral"]}%–')
    if c.get("buzz") is not None:
        bits.append(f'buzz {c["buzz"]}')
    ment = _mentions_total(cw)
    if ment:
        bits.append(f'{ment:,} mentions')
    src = list(cw.get("sources", {}).keys())
    if src:
        bits.append(f'({len(src)} sources)')
    return " &nbsp;".join(bits)


def _stacked(bull, neu, bear, width=150):
    segs = ""
    for pct, col in ((bull, "#0a7d33"), (neu, "#c3c7cc"), (bear, "#b3261e")):
        if pct:
            segs += f'<span style="display:inline-block;height:15px;width:{pct}%;background:{col}"></span>'
    if not segs:
        return '<span style="color:#9aa0a6;font-size:14px">no directional data</span>'
    return (f'<span style="display:inline-flex;width:{width}px;height:15px;border-radius:4px;'
            f'overflow:hidden;vertical-align:middle;background:#eef0f2">{segs}</span>')


def _crowd_panel(cw):
    """Per-source bullish/neutral/bearish chart + consensus (Adanos multi-source)."""
    if not cw or not cw.get("has_data"):
        return ""
    c = cw.get("consensus", {})
    rows = ('<tr style="color:#5f6368;font-size:13px"><td style="padding:2px 8px 2px 0">Source</td>'
            '<td style="padding:2px 8px">Bull / Neutral / Bear</td>'
            '<td style="text-align:right;padding:2px 8px">Split</td>'
            '<td style="text-align:right;padding:2px 8px">Buzz</td>'
            '<td style="text-align:right;padding:2px 8px">Mentions</td>'
            '<td style="text-align:right;padding:2px 0">Trend</td></tr>')
    def row(name, sv, bold=False):
        b, n, br = sv.get("bullish"), sv.get("neutral"), sv.get("bearish")
        split = (f'<span style="color:#0a7d33">{b}%</span> / {n}% / <span style="color:#b3261e">{br}%</span>'
                 if b is not None else (f'score {sv.get("score")}' if sv.get("score") is not None else "n/a"))
        w = "font-weight:700;" if bold else ""
        return (f'<tr><td style="padding:3px 8px 3px 0;font-size:14px;{w}">{_esc(name)}</td>'
                f'<td style="padding:3px 8px">{_stacked(b, n, br)}</td>'
                f'<td style="text-align:right;padding:3px 8px;font-size:14px">{split}</td>'
                f'<td style="text-align:right;padding:3px 8px;font-size:14px">{sv.get("buzz") if sv.get("buzz") is not None else "–"}</td>'
                f'<td style="text-align:right;padding:3px 8px;font-size:14px;{w}">{_fmt_mentions(sv.get("mentions"))}</td>'
                f'<td style="text-align:right;padding:3px 0;font-size:14px;color:#5f6368">{_esc(str(sv.get("trend") or "–"))}</td></tr>')
    for name, sv in cw.get("sources", {}).items():
        rows += row(name, sv)
    if c:
        total = _mentions_total(cw)
        rows += row("Consensus", {**c, "mentions": total or None}, bold=True)
    return _box('<div style="font-size:15px;font-weight:700;margin-bottom:5px">👥 Crowd sentiment '
                '<span style="font-weight:400;color:#9aa0a6">— Adanos: Reddit · X · News · Polymarket</span></div>'
                f'<table style="border-collapse:collapse;width:100%">{rows}</table>', bg="#fbfbfd")

def _tech_panel(t):
    if not t or getattr(t,"error",None): return '<div style="font-size:15px;color:#9aa0a6">Technicals: n/a</div>'
    rsi_tone="bad" if (t.rsi or 50)>=70 else "good" if (t.rsi or 50)<=30 else "neutral"
    macd_tone="good" if (t.macd_hist or 0)>0 else "bad" if (t.macd_hist or 0)<0 else "neutral"
    vol_tone="good" if (t.vol_ratio or 0)>=1.2 else "neutral"
    trend_tone={"up":"good","down":"bad"}.get(t.trend,"neutral")
    a50="above" if (t.price and t.sma50 and t.price>t.sma50) else "below" if t.sma50 else "n/a"
    a200="above" if (t.price and t.sma200 and t.price>t.sma200) else "below" if t.sma200 else "n/a"
    chips=[_chip("RSI",t.rsi if t.rsi is not None else "n/a",rsi_tone),
           _chip("MACD hist",t.macd_hist if t.macd_hist is not None else "n/a",macd_tone),
           _chip("Trend",t.trend or "n/a",trend_tone),
           _chip("vs SMA50",a50,"good" if a50=="above" else "bad" if a50=="below" else "neutral"),
           _chip("vs SMA200",a200,"good" if a200=="above" else "bad" if a200=="below" else "neutral"),
           _chip("ATR",f"{t.atr} ({t.atr_pct}%)" if t.atr is not None else "n/a"),
           _chip("Volume",f"{t.vol_ratio}x" if t.vol_ratio is not None else "n/a",vol_tone),
           _chip("Support",t.support if t.support is not None else "n/a"),
           _chip("Resistance",t.resistance if t.resistance is not None else "n/a")]
    reasons="".join(f'<div style="font-size:14px;color:#5f6368;margin:2px 0">• {_esc(r)}</div>' for r in (t.reasons or [])[:5])
    return (f'<div style="margin:2px 0 6px">{"".join(chips)}</div>'
            f'<div style="font-size:15px;margin-bottom:4px">Rule-based signal: {_sent_pill(t.bias)} '
            f'&nbsp;<b>{_esc(t.signal.upper())}</b> <span style="color:#9aa0a6">(deterministic reference)</span></div>{reasons}')

def _links_block(items,label="Sources & full articles"):
    if not items: return ""
    seen=set(); uniq=[]
    for it in items:  # the same headline can arrive for several holdings/queries
        k=(it.title or "").strip().lower()
        if not k or k in seen: continue
        seen.add(k); uniq.append(it)
    if not uniq: return ""
    rows="".join(f'<div style="font-size:15px;margin:4px 0"><a href="{_esc(it.url)}" '
                 f'style="color:#1a1a1a;text-decoration:none">• {_esc(it.title)}</a> '
                 f'<span style="color:#9aa0a6">— {_esc(it.source)}</span></div>' for it in uniq)
    return (f'<details style="margin-top:8px"><summary style="cursor:pointer;color:#3367d6;font-size:15px;'
            f'font-weight:600;user-select:none">▾ {label} ({len(uniq)}) — click to expand</summary>'
            f'<div style="margin-top:6px;padding-left:4px">{rows}</div></details>')

def _links_list(items,label="Sources & full articles",limit=8,prefix_group=False):
    """VISIBLE (non-collapsible) article list — email clients render <details>
    unreliably, which hid the news. Used inside stock/ETF cards."""
    if not items: return ""
    seen=set(); uniq=[]
    for it in items:
        k=(it.title or "").strip().lower()
        if not k or k in seen: continue
        seen.add(k); uniq.append(it)
    uniq=uniq[:limit]
    if not uniq: return ""
    rows=""
    for it in uniq:
        pre=f'<b>{_esc(it.group)}</b> · ' if (prefix_group and getattr(it,"group","")) else ""
        rows+=(f'<div style="font-size:15px;margin:5px 0">• {pre}<a href="{_esc(it.url)}" '
               f'style="color:#1a40b0;text-decoration:underline">{_esc(it.title)}</a> '
               f'<span style="color:#9aa0a6">— {_esc(it.source)}</span></div>')
    return (f'<div style="margin-top:10px;padding-top:8px;border-top:1px solid #eef0f2">'
            f'<div style="font-size:15px;font-weight:700;margin-bottom:2px">📰 {label} ({len(uniq)})</div>{rows}</div>')

def _pts(lst):
    """Clean an AI bullet array: drop empties and bare-number junk items."""
    out=[]
    for p in (lst or []):
        t=str(p).strip().lstrip("-•–·* ").strip()
        if t and not t.replace(".","").replace("-","").isdigit():
            out.append(t)
    return out

def _banner(status):
    if status.get("ok"):
        return (f'<div style="background:#e6f6ec;border-left:4px solid #0a7d33;padding:9px 13px;'
                f'border-radius:6px;margin-bottom:16px;font-size:15px;color:#0a5227">'
                f'✅ Analysis by <b>{_esc(status.get("engine",""))}</b></div>')
    reason=_esc(status.get("reason","") or "no AI provider available")
    return (f'<div style="background:#fdf5e0;border-left:4px solid #8a6d00;padding:9px 13px;border-radius:6px;'
            f'margin-bottom:16px;font-size:15px;color:#6b5300">⚠️ AI unavailable — used the free '
            f'<b>heuristic</b>.<br><span>Reason: {reason}</span></div>')

def _h2(title, sub=""):
    subhtml=f' <span style="font-size:15px;font-weight:400;color:#9aa0a6">— {_esc(sub)}</span>' if sub else ""
    return (f'<h2 style="font-size:19px;margin:26px 0 12px;padding-bottom:6px;border-bottom:2px solid #eef0f2">'
            f'{_esc(title)}{subhtml}</h2>')

def _flags_row(flags):
    if not flags: return ""
    cells=""
    for fl in flags:
        pct=fl.get("pct"); col="#0a7d33" if (pct or 0)>=0 else "#b3261e"; arr="▲" if (pct or 0)>=0 else "▼"
        pcts=f'<span style="color:{col};font-weight:600">{arr} {pct:+.2f}%</span>' if pct is not None else ""
        cells+=(f'<td style="padding:8px 12px;border:1px solid #eef0f2;text-align:center">'
                f'<div style="font-size:14px;color:#5f6368">{_esc(fl["name"])}</div>'
                f'<div style="font-size:16px;font-weight:700">{fl.get("price")}</div>'
                f'<div style="font-size:14px">{pcts}</div></td>')
    return (f'<div style="margin-bottom:18px">{_h2("Global market flags")}'
            f'<table style="border-collapse:collapse;width:100%"><tr>{cells}</tr></table></div>')

def _legend():
    return ("""<div style="margin-top:24px;background:#fafafa;border:1px solid #eee;border-radius:8px;padding:14px 16px;font-size:15px;color:#3c4043">
      <div style="font-weight:700;margin-bottom:6px;font-size:16px">How to read this report</div>
      <div style="margin-bottom:4px"><b>Factor scores (0–100), higher = stronger:</b>
        <span style="color:#b3261e">0–33 weak</span> · <span style="color:#8a6d00">34–66 average</span> · <span style="color:#0a7d33">67–100 strong</span></div>
      <div>• <b>Value</b> cheapness (P/E, P/S, PEG) · <b>Growth</b> revenue/earnings · <b>Profit</b> margins/ROE · <b>Momentum</b> trend & 52-wk position · <b>Health</b> balance sheet · <b>Composite</b> average.</div>
      <div style="margin-top:4px">• <b>News tone</b> headline wording (−1..+1) · <b>Crowd</b> Adanos multi-source (Reddit · X · News · Polymarket); <b>Mentions</b> = how many people are actually talking (judge whether the %s represent a big population) · <b>Impact</b> materiality of today's news.</div>
      <div style="margin-top:4px"><b>Technicals:</b> RSI (&gt;70 overbought, &lt;30 oversold) · MACD (momentum) · SMA/EMA (trend) · ATR (volatility) · Volume (confirmation) · support/resistance. <b>Rule-based signal</b> = deterministic reference. <b>Technical read</b> = the AI's call from the technicals. <b>Research verdict</b> = the bull/bear/judge debate's synthesis.</div>
      <div style="margin-top:4px;color:#b3261e"><b>Not investment advice.</b> Signals/calls describe the current setup, not a recommendation or forecast. Do your own research.</div></div>""")

def _footer():
    return ('<div style="margin-top:14px;font-size:14px;color:#9aa0a6">Sources: Yahoo Finance, Finnhub, '
            'Google News RSS, SEC EDGAR, Adanos (Reddit), central-bank feeds. Rule-based/AI context, not predictions. '
            'Informational only, not investment advice.</div></div></body></html>')

def _wrap(title, dated, banner_html, body):
    return ('<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1"></head>'
            '<body style="margin:0;background:#ffffff">'
            f'<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
            f'max-width:740px;margin:0 auto;color:#1a1a1a;line-height:1.55;font-size:16px">'
            f'<h1 style="font-size:26px;margin:0 0 2px">{_esc(title)}</h1>'
            f'<div style="color:#5f6368;font-size:15px;margin-bottom:12px">{dated}</div>'
            f'{banner_html}{body}')


# --------------------------------------------------------------------------- #
#  Shared full stock/ETF card
# --------------------------------------------------------------------------- #
def _stock_card(tk, s, funds, extras, by_group, watch_reason=None):
    f=funds.get(tk); e=(extras.get("earnings") or {}).get(tk) or {}
    sg=(extras.get("sentiment") or {}).get(tk,{}); cw=(extras.get("crowd") or {}).get(tk,{})
    fils=extras.get("filings") or {}; is_etf=bool(f and f.is_etf)
    price=_num(f.price) if f else "n/a"; chg=_pct_span(f.change_1d) if f else ""
    cur=(getattr(f,"currency","") or "") if f else ""
    if cur and cur!="USD" and price!="n/a":
        price+=f' <span style="color:#8a6d00;font-size:13px;font-weight:700">{_esc(cur)}</span>'
    H=[]
    H.append(f'<div style="border:1px solid #e5e7eb;border-radius:10px;padding:16px 18px;margin-bottom:16px;background:#fff">')
    H.append(f'<div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:6px">'
             f'<span style="font-size:20px;font-weight:800">{_esc(tk)}'
             f'<span style="font-weight:400;color:#5f6368;font-size:16px"> {_esc(f.name if f else "")}</span></span>'
             f'<span>{_impact_pill(s.get("impact"))}&nbsp;{_sent_pill(s.get("sentiment"))}</span></div>')
    if watch_reason:
        H.append(f'<div style="font-size:14px;color:#8a6d00;margin-top:2px">Flagged because: {_esc(watch_reason)}</div>')
    sh = (extras.get("shariah") or {}).get(tk)
    if sh:
        col = {"pass":("#0a7d33","#e6f6ec"),"review":("#8a6d00","#fdf5e0"),"fail":("#b3261e","#fdeceb")}.get(sh["status"],("#5f6368","#eef0f2"))
        rlab = {"pass":"✓ Shariah: passed (auto-screen)","review":"⚠ Shariah: needs review","fail":"✗ Shariah: not compliant"}.get(sh["status"],"Shariah: n/a")
        ratios = ""
        if sh.get("ratios"):
            ratios = " · " + ", ".join(f"{k} {v}%" for k, v in sh["ratios"].items())
        H.append(f'<div style="margin-top:4px">{_pill(rlab, *col)}'
                 f'<span style="font-size:13px;color:#9aa0a6">{ratios}</span></div>')
    H.append(f'<div style="font-size:16px;color:#3c4043;margin:6px 0 4px">{price} &nbsp; {chg}</div>')
    if sg.get("n"):
        tone=f'news tone {sg.get("score","n/a")} ({sg.get("label","n/a")})'
        if sg.get("basis")=="holdings":
            tone+=' <span style="color:#9aa0a6;font-size:13px">(from holdings news)</span>'
    else:
        tone='<span style="color:#9aa0a6">news tone: no direct news this run</span>'
    H.append(f'<div style="font-size:15px;color:#3c4043;margin-bottom:8px">'
             f'{tone} &nbsp;·&nbsp; {_crowd_line(cw)}</div>')

    # badges
    badges=""
    if e.get("upcoming"):
        u=e["upcoming"]; d=u.get("days_away")
        col=("#b3261e","#fdeceb") if (d is not None and d<=7) else ("#8a6d00","#fdf5e0")
        badges+=_pill(f"earnings in {d}d",*col)+"&nbsp;"
    if e.get("recent"):
        rc=e["recent"]; sp=rc.get("eps_surprise_pct"); beat=sp is not None and sp>0
        col=("#0a7d33","#e6f6ec") if beat else ("#b3261e","#fdeceb")
        lab=f"reported {rc.get('days_ago')}d ago: EPS {'beat' if beat else 'miss'}"+(f" {sp:+.1f}%" if sp is not None else "")
        badges+=_pill(lab,*col)+"&nbsp;"
    if is_etf: badges+=_pill("ETF / fund","#3367d6","#eef3fb")+"&nbsp;"
    if badges: H.append(f'<div style="margin-bottom:8px">{badges}</div>')

    # ---- 1) NEWS — what happened and what it means (the point of the report) ----
    H.append(f'<div style="margin-bottom:6px">{_prose(s.get("summary",""),fs="16px",color="#1a1a1a")}</div>')
    if s.get("news_impact"):
        H.append(_box(f'<b style="color:#3367d6">News impact on the company:</b>{_prose(s["news_impact"])}',bg="#eef3fb",border="#d5e2f7"))
    if s.get("divergence"):
        H.append(_box(f'⚡ <b>Divergence:</b> {_esc(s["divergence"])}',bg="#fdf5e0",border="#ecdca6"))

    # ---- 2) CROWD sentiment ----
    H.append(_crowd_panel(cw))
    if not (cw and cw.get("has_data")):
        st = extras.get("crowd_status")
        if st == "no_key":
            H.append('<div style="font-size:14px;color:#8a6d00;margin:2px 0">👥 Crowd sentiment off — set '
                     '<b>ADANOS_API_KEY</b> (free at adanos.org) to enable Reddit · X · News · Polymarket sentiment.</div>')
        elif st == "empty":
            H.append('<div style="font-size:14px;color:#9aa0a6;margin:2px 0">👥 Crowd sentiment: no Adanos data for this name this run.</div>')
        elif st == "pk_thin":
            H.append('<div style="font-size:14px;color:#9aa0a6;margin:2px 0">👥 Crowd sentiment: no Reddit chatter found for this name (PSX discussion is thin; set REDDIT_CLIENT_ID/SECRET for reliable search).</div>')
    if s.get("crowd_note"):
        H.append(f'<div style="font-size:15px;color:#5f6368;margin-bottom:6px">👥 {_esc(s["crowd_note"])}</div>')

    # ---- 3) TECHNICALS — rule-based indicators + the AI's technical read, together ----
    tech=(extras.get("technicals") or {}).get(tk)
    tr=s.get("technical_read") or {}
    tech_inner=('<div style="font-size:14px;font-weight:700;text-transform:uppercase;letter-spacing:.4px;color:#5f6368;margin-bottom:4px">Technical analysis</div>'
                +_tech_panel(tech))
    if tr.get("call"):
        tech_inner+=(f'<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:8px;padding-top:8px;border-top:1px solid #e9ecef">'
                     f'<span style="font-size:14px;font-weight:700;text-transform:uppercase;letter-spacing:.4px;color:#5f6368">Technical read</span>'
                     f'{_call_pill(tr.get("call"))}<span style="font-size:13px;color:#9aa0a6">AI, technicals only</span></div>'
                     f'<div style="margin-top:4px">{_prose(tr.get("rationale",""))}</div>')
    H.append(_box(tech_inner))

    # ---- 4) FUNDAMENTALS / fund data ----
    if is_etf and f:
        prof=(extras.get("etf") or {}).get(tk); r=(prof.returns if prof else {}) or {}
        left=_grid([("Returns",f"1d {_num(r.get('1d'),'%')} · 1w {_num(r.get('1w'),'%')} · 1m {_num(r.get('1m'),'%')} · 3m {_num(r.get('3m'),'%')}"),
                    ("",f"6m {_num(r.get('6m'),'%')} · YTD {_num(r.get('ytd'),'%')} · 1y {_num(r.get('1y'),'%')}"),
                    ("Risk",(f"vol {_num(prof.vol_1y,'%',nd=1)} · maxDD {_num(prof.max_drawdown_1y,'%',nd=1)} · beta {prof.beta if prof and prof.beta is not None else 'n/a'}") if prof else "n/a")])
        right=_grid([("Category",_esc((prof.category if prof else f.category) or "n/a")),("AUM",_money(prof.aum if prof else f.aum)),
                     ("Expense",_expense(prof.expense_ratio if prof else None)),("Yield",_num(prof.etf_yield if prof else f.etf_yield,pct=True)),
                     ("NAV prem/disc",_num(prof.premium_discount if prof else None,"%")),("Top-10 wt",_num(prof.top10_weight if prof else None,"%",nd=1))])
        H.append(_box(f'<div style="display:flex;gap:20px;flex-wrap:wrap"><div style="flex:1;min-width:250px">{left}</div><div style="flex:1;min-width:230px">{right}</div></div>'))
        if prof and prof.rel_market and not prof.tracks_benchmark:
            rel=" · ".join(f"{k} {v:+.1f}" for k,v in prof.rel_market.items())
            H.append(f'<div style="font-size:15px;color:#3c4043;margin:2px 0 6px"><b>vs {_esc(prof.benchmark)} (% pts):</b> {rel}</div>')
        elif prof and prof.tracks_benchmark:
            H.append(f'<div style="font-size:14px;color:#9aa0a6;margin:2px 0 6px">Tracks the {_esc(prof.benchmark)} benchmark (relative performance ≈ 0).</div>')
        if prof and prof.holdings:
            rows=('<tr style="color:#5f6368;font-size:14px"><td style="padding:3px 8px 3px 0">Holding</td>'
                  '<td style="text-align:right;padding:3px 8px">Weight</td><td style="text-align:right;padding:3px 8px">1d</td>'
                  '<td style="text-align:right;padding:3px 0">Contribution</td></tr>')
            for h in prof.holdings[:8]:
                cc=h.contribution; ccol="#0a7d33" if (cc or 0)>0 else "#b3261e" if (cc or 0)<0 else "#5f6368"
                rows+=(f'<tr><td style="padding:3px 8px 3px 0;font-size:15px">{_esc(h.symbol)} '
                       f'<span style="color:#9aa0a6">{_esc((h.name or "")[:20])}</span></td>'
                       f'<td style="text-align:right;padding:3px 8px;font-size:15px">{_num((h.weight or 0)*100,"%",nd=1)}</td>'
                       f'<td style="text-align:right;padding:3px 8px;font-size:15px">{_num(h.ret_1d,"%")}</td>'
                       f'<td style="text-align:right;padding:3px 0;font-size:15px;color:{ccol}">{("%+.3f"%cc) if cc is not None else "n/a"}</td></tr>')
            rows+=(f'<tr><td colspan="3" style="padding:5px 0;font-weight:700;font-size:15px">≈ explained move</td>'
                   f'<td style="text-align:right;font-weight:700;font-size:15px">{("%+.3f"%prof.explained_move) if prof.explained_move is not None else "n/a"} pts</td></tr>')
            H.append(_box('<div style="font-size:15px;font-weight:700;margin-bottom:4px">What moved the fund today</div>'
                          f'<table style="border-collapse:collapse;width:100%">{rows}</table>'))
        sw=(prof.sector_weights if prof else f.sector_weights) or {}
        if sw:
            H.append(f'<div style="font-size:15px;color:#3c4043;margin:4px 0"><b>Sectors:</b> '
                     + ", ".join(f"{_esc(k)} {v}%" for k,v in list(sw.items())[:6])+'</div>')
        if prof and prof.peers:
            rows=('<tr style="color:#5f6368;font-size:14px"><td style="padding:3px 8px 3px 0">ETF</td>'
                  '<td style="text-align:right;padding:3px 8px">1y</td><td style="text-align:right;padding:3px 8px">Expense</td>'
                  '<td style="text-align:right;padding:3px 0">AUM</td></tr>')
            rows+=(f'<tr><td style="padding:3px 8px 3px 0;font-size:15px"><b>{_esc(tk)} (this)</b></td>'
                   f'<td style="text-align:right;padding:3px 8px;font-size:15px">{_num(r.get("1y"),"%")}</td>'
                   f'<td style="text-align:right;padding:3px 8px;font-size:15px">{_expense(prof.expense_ratio)}</td>'
                   f'<td style="text-align:right;padding:3px 0;font-size:15px">{_money(prof.aum)}</td></tr>')
            for pe in prof.peers:
                rows+=(f'<tr><td style="padding:3px 8px 3px 0;font-size:15px">{_esc(pe.ticker)}</td>'
                       f'<td style="text-align:right;padding:3px 8px;font-size:15px">{_num(pe.ret_1y,"%")}</td>'
                       f'<td style="text-align:right;padding:3px 8px;font-size:15px">{_expense(pe.expense)}</td>'
                       f'<td style="text-align:right;padding:3px 0;font-size:15px">{_money(pe.aum)}</td></tr>')
            H.append(_box('<div style="font-size:15px;font-weight:700;margin-bottom:4px">vs competitor ETFs</div>'
                          f'<table style="border-collapse:collapse;width:100%">{rows}</table>'))
    elif f:
        sc=f.scores or {}
        bars="".join(_bar(l,sc.get(k)) for l,k in [("Value","value"),("Growth","growth"),("Profit","profitability"),("Momentum","momentum"),("Health","health")])
        bars+=f'<div style="font-size:15px;margin-top:4px"><b>Composite: {sc.get("composite","n/a")}/100</b></div>'
        right=_grid([("Valuation",f"P/E {_num(f.pe)} · fwd {_num(f.forward_pe)} · P/S {_num(f.ps)} · PEG {_num(f.peg)}"),
                     ("Growth",f"rev {_num(f.rev_growth,pct=True)} · EPS {_num(f.earn_growth,pct=True)}"),
                     ("Profitability",f"net {_num(f.net_margin,pct=True)} · gross {_num(f.gross_margin,pct=True)} · ROE {_num(f.roe,pct=True)}"),
                     ("Momentum",f"1m {_num(f.ret_1m,'%')} · 3m {_num(f.ret_3m,'%')} · 6m {_num(f.ret_6m,'%')}"),
                     ("Analyst",f"target {_num(f.target_mean)} ({_num(f.implied_upside,'%')} upside)"),
                     ("Next earnings",(f"{_esc(f.next_earnings)} ({f.days_to_earnings}d)" if f.next_earnings else "n/a"))])
        H.append(_box(f'<div style="display:flex;gap:20px;flex-wrap:wrap"><div style="flex:1;min-width:240px">{bars}</div><div style="flex:1;min-width:250px">{right}</div></div>'))

    if s.get("fundamental_read"):
        H.append(f'<div style="font-size:15px;color:#5f6368;margin-bottom:6px">📊 {_esc(s["fundamental_read"])}</div>')

    ed=s.get("earnings") or {}
    if any(ed.get(k) for k in ("result","outlook","management_review")):
        inner='<div style="font-weight:700;color:#0a7d33;margin-bottom:3px;font-size:15px">Earnings &amp; outlook</div>'
        for k,lab in [("result","Result"),("outlook","Outlook"),("management_review","Management")]:
            if ed.get(k): inner+=f'<div style="font-size:15px"><b>{lab}:</b> {_esc(ed[k])}</div>'
        H.append(_box(inner,bg="#f1f7f1",border="#d5ead5"))

    etf_an=s.get("etf") or {}
    if is_etf and any(etf_an.get(k) for k in etf_an):
        inner='<div style="font-weight:700;color:#3367d6;margin-bottom:4px;font-size:15px">Fund analysis</div>'
        for key,lab in [("move_explainer","What moved it today"),("holdings_news_impact","Holdings news & impact"),
                        ("nav_read","NAV"),("vs_market","vs market"),("vs_peers","vs competitors"),("risks","Risks")]:
            if etf_an.get(key): inner+=f'<div style="margin:4px 0;font-size:15px"><b>{lab}:</b>{_prose(etf_an[key])}</div>'
        H.append(_box(inner,bg="#f4f6fb",border="#dde4f0"))

    fil=fils.get(tk)
    if fil: H.append(f'<div style="font-size:15px;margin-bottom:6px">📄 <a href="{_esc(fil["url"])}" style="color:#3367d6">{_esc(fil["form"])} filed {_esc(fil["filed"])}</a></div>')

    # ---- 5) DEBATE — research verdict + bull vs bear ----
    H.append(_debate_panel(s))

    # ---- 6) NEWS ARTICLES — visible, at the end of the card ----
    if is_etf:
        hn=(extras.get("etf_holding_news") or {}).get(tk,{})
        hitems=[a for arts in hn.values() for a in arts]
        if hitems:
            H.append(_links_list(hitems,label="News on major holdings",limit=16,prefix_group=True))
    H.append(_links_list(by_group.get(tk, []),label="Sources & full articles",limit=8))
    H.append("</div>")
    return "".join(H)


# --------------------------------------------------------------------------- #
#  PORTFOLIO report
# --------------------------------------------------------------------------- #
def _lookthrough_panel(lt):
    if not lt or not lt.get("companies"):
        return ""
    B=[_h2("Portfolio look-through", "true exposure across your holdings + what your ETFs hold")]
    if lt.get("flags"):
        fl="".join(f'<div style="font-size:15px;color:#6b5300;margin:2px 0">⚠️ {_esc(x)}</div>' for x in lt["flags"])
        B.append(_box(fl,bg="#fdf9ec",border="#ecdca6"))
    # top companies as bars (scaled to the largest so bars are readable)
    comps=lt["companies"][:12]
    mx=max((c["total_pct"] for c in comps), default=1) or 1
    rows=""
    for c in comps:
        w=max(2,int(c["total_pct"]/mx*100))
        tag=' <span style="color:#8a6d00;font-weight:700">◆ overlap</span>' if c["overlap"] else ""
        detail=""
        if c["direct_pct"]>0 and c["via"]:
            detail=f' <span style="color:#9aa0a6">(direct {c["direct_pct"]}% + funds {round(c["total_pct"]-c["direct_pct"],2)}%)</span>'
        elif c["via"] and c["direct_pct"]==0:
            detail=f' <span style="color:#9aa0a6">(via {", ".join(v["etf"] for v in c["via"][:3])})</span>'
        elif c["direct_pct"]>0:
            detail=' <span style="color:#9aa0a6">(direct)</span>'
        rows+=(f'<div style="display:flex;align-items:center;gap:8px;margin:3px 0">'
               f'<span style="font-size:15px;width:150px"><b>{_esc(c["ticker"])}</b>{tag}</span>'
               f'<span style="flex:1;background:#e9ecef;border-radius:5px;height:12px"><span style="display:block;height:12px;border-radius:5px;width:{w}%;background:#3367d6"></span></span>'
               f'<span style="font-size:15px;width:52px;text-align:right;font-weight:700">{c["total_pct"]}%</span>'
               f'<span style="font-size:14px;flex-basis:100%;padding-left:158px;margin-top:-2px">{detail}</span></div>')
    B.append(_box('<div style="font-weight:700;font-size:16px;margin-bottom:6px">Top companies by true exposure</div>'+rows))
    # sectors
    if lt.get("sectors"):
        smx=max((s["pct"] for s in lt["sectors"]), default=1) or 1
        srows=""
        for s in lt["sectors"][:8]:
            w=max(2,int(s["pct"]/smx*100))
            srows+=(f'<div style="display:flex;align-items:center;gap:8px;margin:3px 0">'
                    f'<span style="font-size:15px;width:170px">{_esc(s["sector"])}</span>'
                    f'<span style="flex:1;background:#e9ecef;border-radius:5px;height:12px"><span style="display:block;height:12px;border-radius:5px;width:{w}%;background:#0a7d33"></span></span>'
                    f'<span style="font-size:15px;width:46px;text-align:right;font-weight:700">{s["pct"]}%</span></div>')
        B.append(_box('<div style="font-weight:700;font-size:16px;margin-bottom:6px">Sector allocation (look-through)</div>'+srows))
    note=("Equal weight assumed (add a <b>weight</b> to each holding in config for real allocation). "
          if lt.get("equal_weight") else "")
    B.append(f'<div style="font-size:14px;color:#9aa0a6;margin-top:2px">{note}'
             f'Company figures use each ETF\'s disclosed top holdings (largest overlaps); '
             f'sector figures use full ETF sector weightings.</div>')
    return "".join(B)


_REGION_FLAG = {"US": "🇺🇸", "PK": "🇵🇰"}


def build_portfolio(analysis, items, funds, extras):
    today=dt.datetime.now().strftime("%A, %d %B %Y")
    by_group=defaultdict(list)
    for it in items: by_group[it.group].append(it)
    status=analysis.get("_status", {})
    region=extras.get("region","US")
    subject=f"📊 Portfolio Digest {region} — {dt.datetime.now():%b %d}"
    B=[]
    B.append(f'<div style="background:#f6f8fa;border-left:4px solid #3367d6;padding:12px 16px;border-radius:6px;margin-bottom:18px">'
             f'<div style="font-weight:700;font-size:15px;text-transform:uppercase;letter-spacing:.5px;color:#3367d6;margin-bottom:6px">Portfolio overview</div>'
             f'{_prose(analysis.get("market_overview",""),fs="16px",color="#1a1a1a")}</div>')
    B.append(_flags_row(extras.get("flags")))
    B.append(_lookthrough_panel(extras.get("look_through")))
    prio=analysis.get("priority", [])
    if prio:
        lis="".join(f'<li style="margin:4px 0"><b>{_esc(p.get("ticker",""))}</b> — {_esc(p.get("why",""))}</li>' for p in prio[:6])
        B.append(f'{_h2("What matters today")}<ol style="margin:0;padding-left:20px;font-size:16px">{lis}</ol>')
    B.append(_h2("Your holdings"))
    for tk,s in {x.get("ticker"):x for x in analysis.get("stocks", [])}.items():
        B.append(_stock_card(tk,s,funds,extras,by_group))
    macro=analysis.get("macro", {}) or {}
    if macro.get("summary") or macro.get("points"):
        B.append(_h2("Macro backdrop"))
        if macro.get("summary"):
            B.append(_prose(macro["summary"],fs="16px",color="#1a1a1a"))
        for p in _pts(macro.get("points"))[:6]:
            B.append(f'<div style="font-size:15px;color:#3c4043;margin:3px 0">• {_esc(p)}</div>')
        if macro.get("watch"):
            B.append('<div style="font-size:15px;color:#5f6368;margin-top:4px">Watch: '+" · ".join(_esc(w) for w in _pts(macro.get("watch")))+'</div>')
    B.append(_legend())
    html_body=_wrap(f"Portfolio Digest {region}",today,_banner(status),"".join(B))+_footer()
    # text
    T=[f"PORTFOLIO DIGEST {region} — {today}","",("AI: "+status.get("engine","")) if status.get("ok") else ("AI FAILED — heuristic. "+status.get("reason","")),"",analysis.get("market_overview","")]
    for tk,s in {x.get("ticker"):x for x in analysis.get("stocks", [])}.items():
        T+=_stock_text(tk,s,funds,extras,by_group)
    return subject,html_body,"\n".join(str(x) for x in T)


# --------------------------------------------------------------------------- #
#  MARKET report
# --------------------------------------------------------------------------- #
def build_market(port_analysis, watch_analysis, items, funds, extras, watch_reasons=None):
    today=dt.datetime.now().strftime("%A, %d %B %Y")
    weekly=bool(extras.get("weekly")); watch_reasons=watch_reasons or {}
    by_group=defaultdict(list)
    for it in items: by_group[it.group].append(it)
    status=port_analysis.get("_status", {})
    region=extras.get("region","US")
    flag=_REGION_FLAG.get(region,"🌐")
    subject=f"{flag} {'Weekly ' if weekly else ''}Market Digest {region} — {dt.datetime.now():%b %d}"
    B=[]
    B.append(f'<div style="background:#f6f8fa;border-left:4px solid #3367d6;padding:12px 16px;border-radius:6px;margin-bottom:18px">'
             f'<div style="font-weight:700;font-size:15px;text-transform:uppercase;letter-spacing:.5px;color:#3367d6;margin-bottom:6px">Market overview</div>'
             f'{_prose(port_analysis.get("market_overview",""),fs="16px",color="#1a1a1a")}</div>')
    B.append(_flags_row(extras.get("flags")))

    sh=port_analysis.get("sector_highlights", [])
    if sh:
        B.append(_h2("Sector highlights","from today's news flow"))
        for sec in sh:
            pts="".join(f'<div style="font-size:15px;color:#3c4043;margin:4px 0">• {_esc(p)}</div>' for p in _pts(sec.get("points"))[:6])
            B.append(_box(f'<div style="display:flex;justify-content:space-between;align-items:center">'
                          f'<span style="font-weight:700;font-size:17px">{_esc(sec.get("sector",""))}</span> {_sent_pill(sec.get("call"))}</div>{pts}'))

    ws=watch_analysis.get("stocks", []) if watch_analysis else []
    if ws:
        wsub = "full analysis — technicals + combined call"
        if extras.get("shariah"):
            wsub += " · Shariah-screened"
        B.append(_h2("Stocks to watch", wsub))
        for s in ws:
            tk=s.get("ticker")
            B.append(_stock_card(tk,s,funds,extras,by_group,watch_reason=watch_reasons.get(tk)))

    # Crypto highlight (all configured major coins)
    ch = port_analysis.get("crypto_highlight") or {}
    crypto = extras.get("crypto") or {}
    snaps = crypto.get("snapshot") or []
    snap = {c["symbol"]: c for c in snaps}
    csent = crypto.get("sentiment") or {}
    ctech = crypto.get("technicals") or {}
    ai_coins = {c.get("symbol"): c for c in (ch.get("coins") or [])}
    # union: every coin we have data for (snapshot order), plus any AI-only coins
    order = [c["symbol"] for c in snaps] + [s for s in ai_coins if s not in snap]
    if order or ch.get("points"):
        B.append(_h2("Crypto", "major coins"))
        head = (f'<div style="display:flex;justify-content:space-between;align-items:center">'
                f'<span style="font-weight:700;font-size:17px">Crypto market</span> {_sent_pill(ch.get("call"))}</div>')
        pts = "".join(f'<div style="font-size:15px;color:#3c4043;margin:4px 0">• {_esc(p)}</div>'
                      for p in _pts(ch.get("points"))[:5])
        rows = ""
        for sym in order[:10]:
            c = ai_coins.get(sym, {})
            sp = snap.get(sym, {})
            move = _pct_span(sp.get("pct")) if sp.get("pct") is not None else ""
            m7 = f' <span style="color:#9aa0a6">7d {sp["pct7d"]:+.1f}%</span>' if sp.get("pct7d") is not None else ""
            price = f'<span style="color:#5f6368">{sp.get("price")}</span> ' if sp.get("price") is not None else ""
            con = (csent.get(sym) or {}).get("consensus", {})
            crowd_bit = (f' · crowd {con.get("label")} ({con.get("bullish")}%▲)'
                         if con.get("label") not in (None, "n/a") else "")
            tpanel = ""
            if ctech.get(sym) and not getattr(ctech[sym], "error", None):
                tpanel = (f'<details style="margin-top:4px"><summary style="cursor:pointer;color:#3367d6;'
                          f'font-size:14px;font-weight:600;user-select:none">▾ Technicals for {_esc(sym)}</summary>'
                          f'<div style="margin-top:6px">{_tech_panel(ctech[sym])}</div></details>')
            rows += (f'<tr><td style="padding:8px 10px 8px 0;font-weight:700;white-space:nowrap;vertical-align:top">{_esc(sym)}</td>'
                     f'<td style="padding:8px 10px 8px 0;vertical-align:top">{_call_pill(c.get("call"))}</td>'
                     f'<td style="padding:8px 0;font-size:15px;color:#3c4043">{price}{move}{m7}'
                     f'<span style="color:#9aa0a6;font-size:14px">{crowd_bit}</span>'
                     f'<br>{_esc(c.get("rationale") or c.get("reason") or "")}{tpanel}</td></tr>')
        cnews = _links_block(by_group.get("Crypto", [])[:8], label="Crypto news")
        B.append(_box(head + pts + (f'<table style="border-collapse:collapse;width:100%;margin-top:6px">{rows}</table>' if rows else "") + cnews))
        B.append('<div style="font-size:14px;color:#9aa0a6;margin-top:-6px">Per-coin call = combined view (price/technicals + crypto news). '
                 'Crypto is informational; digital-asset permissibility under Shariah is debated — verify independently.</div>')

    topics=port_analysis.get("topics", [])
    if topics:
        B.append(_h2("Industries & themes"))
        for t in topics:
            name=t.get("topic","")
            inner=(f'<div style="display:flex;justify-content:space-between;align-items:center">'
                   f'<span style="font-weight:700;font-size:17px">{_esc(name)}</span> {_sent_pill(t.get("sentiment"))}</div>'
                   f'{_prose(t.get("summary",""))}')
            kc=t.get("key_companies") or []
            if kc:
                inner+='<div style="font-size:15px;margin:4px 0"><b>Key movers (not in your portfolio):</b></div>'
                for c in kc[:5]: inner+=f'<div style="font-size:15px;margin:2px 0">• <b>{_esc(c.get("name",""))}</b> — {_esc(c.get("note",""))}</div>'
            inner+=_links_block(by_group.get(name, [])[:6])
            B.append(_box(inner))

    sectors=port_analysis.get("sectors", []); sec_news=extras.get("sector_news", {})
    if weekly and (sectors or sec_news):
        B.append(_h2("Weekly sector deep-dive"))
        for sec in sectors:
            name=sec.get("sector","")
            inner=f'<div style="font-weight:700;font-size:17px">{_esc(name)}</div>{_prose(sec.get("summary",""))}'
            for d in _pts(sec.get("developments")): inner+=f'<div style="font-size:15px;margin:2px 0">• {_esc(d)}</div>'
            if sec.get("read_across"): inner+=f'<div style="font-size:15px;color:#5f6368;margin-top:4px">Read-across: {_esc(sec["read_across"])}</div>'
            inner+=_links_block(sec_news.get(name, [])[:5])
            B.append(_box(inner))

    macro=port_analysis.get("macro", {}) or {}; macro_items=by_group.get("Macro", [])
    if macro.get("summary") or macro.get("points") or macro_items:
        B.append(_h2("Macro"))
        if macro.get("summary"): B.append(_prose(macro["summary"],fs="16px",color="#1a1a1a"))
        for p in _pts(macro.get("points"))[:6]:
            B.append(f'<div style="font-size:15px;color:#3c4043;margin:3px 0">• {_esc(p)}</div>')
        if macro.get("watch"): B.append('<div style="font-size:15px;color:#5f6368;margin:6px 0">Watch: '+" · ".join(_esc(w) for w in _pts(macro.get("watch")))+'</div>')
        B.append(_links_block(macro_items[:8]))
    B.append(_legend())
    html_body=_wrap(("Weekly " if weekly else "")+f"Market Digest {region}",today,_banner(status),"".join(B))+_footer()

    T=[f"MARKET DIGEST {region}{' (WEEKLY)' if weekly else ''} — {today}","",port_analysis.get("market_overview","")]
    if sh:
        T.append("\nSECTOR HIGHLIGHTS:")
        for sec in sh:
            T.append(f"\n{sec.get('sector','')} [{(sec.get('call') or '').upper()}]")
            for p in _pts(sec.get("points"))[:4]: T.append(f"  • {p}")
    if ws:
        T.append("\nSTOCKS TO WATCH (full analysis):")
        for s in ws: T+=_stock_text(s.get("ticker"),s,funds,extras,by_group)
    return subject,html_body,"\n".join(str(x) for x in T)


def _stock_text(tk,s,funds,extras,by_group):
    f=funds.get(tk); sg=(extras.get("sentiment") or {}).get(tk,{}); cw=(extras.get("crowd") or {}).get(tk,{})
    cur=(getattr(f,"currency","") or "") if f else ""
    curtag=f" {cur}" if cur and cur!="USD" else ""
    L=[f"\n{tk}"+((f"  {f.price:.2f}{curtag}" if f and f.price is not None else "")+(f" ({f.change_1d:+.2f}%)" if f and f.change_1d is not None else ""))
       +f"  [{(s.get('impact') or '').upper()} / {(s.get('sentiment') or '').upper()}]"]
    con=(cw or {}).get("consensus",{})
    tone=(f"news tone {sg.get('score','n/a')} ({sg.get('label','n/a')})"
          + (" [from holdings news]" if sg.get("basis")=="holdings" else "")) if sg.get("n") \
         else "news tone: no direct news this run"
    ment=_mentions_total(cw or {})
    L.append(f"  {tone}; "
             f"crowd {con.get('label','n/a')} ({con.get('bullish','n/a')}% bull / {con.get('bearish','n/a')}% bear, "
             f"buzz {con.get('buzz','n/a')}, {ment} mentions, {len((cw or {}).get('sources',{}))} sources)")
    L.append(f"  {s.get('summary','')}")
    if s.get("news_impact"): L.append(f"  NEWS IMPACT: {s['news_impact']}")
    tech=(extras.get("technicals") or {}).get(tk)
    if tech and not tech.error: L.append(f"  TECHNICALS: {tech.signal.upper()} | RSI {tech.rsi} | MACD {tech.macd_hist} | trend {tech.trend} | S/R {tech.support}/{tech.resistance}")
    tr=s.get("technical_read") or {}
    if tr.get("call"): L.append(f"  TECHNICAL READ (AI): {tr['call'].upper()} — {tr.get('rationale','')}")
    for it in by_group.get(tk, [])[:5]: L.append(f"  • {it.title} — {it.source}\n    {it.url}")
    return L


def _debate_panel(s):
    """Bull/bear/judge verdict — the opening-research headline for a name."""
    d = s.get("debate") or {}
    v = d.get("verdict") or {}
    if not v.get("call"):
        return ""
    conv = (v.get("conviction") or "").lower()
    conv_col = {"high": "#0a7d33", "medium": "#8a6d00", "low": "#9aa0a6"}.get(conv, "#5f6368")
    risks = "".join(f'<li style="margin:2px 0">{_esc(r)}</li>' for r in _pts(v.get("key_risks"))[:3])
    extra = ""
    if v.get("start_here"):
        extra += f'<div style="font-size:14px;margin-top:6px"><b>Start here:</b> {_esc(v["start_here"])}</div>'
    if v.get("what_would_change_it"):
        extra += f'<div style="font-size:14px;color:#5f6368;margin-top:3px"><b>Would change the call:</b> {_esc(v["what_would_change_it"])}</div>'
    bull = _prose(d.get("bull", ""), fs="14px", color="#1a1a1a")
    bear = _prose(d.get("bear", ""), fs="14px", color="#1a1a1a")
    roles = d.get("roles", {})
    rlabel = (f'bull: {roles.get("bull","?")} · bear: {roles.get("bear","?")} · judge: {roles.get("judge","?")}'
              if roles else "")
    debate_block = (
        f'<details style="margin-top:8px"><summary style="cursor:pointer;color:#3367d6;'
        f'font-size:14px;font-weight:600;user-select:none">▾ Bull vs Bear debate</summary>'
        f'<div style="margin-top:8px"><div style="background:#e6f6ec;border-left:3px solid #0a7d33;'
        f'padding:8px 10px;border-radius:6px;font-size:14px;margin-bottom:6px"><b>🐂 Bull</b><br>{bull}</div>'
        f'<div style="background:#fdeceb;border-left:3px solid #b3261e;padding:8px 10px;border-radius:6px;'
        f'font-size:14px"><b>🐻 Bear</b><br>{bear}</div>'
        f'<div style="font-size:12px;color:#9aa0a6;margin-top:4px">{_esc(rlabel)}</div></div></details>')
    return _box(
        f'<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:5px">'
        f'<span style="font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:.4px;color:#5f6368">Research verdict</span>'
        f'{_call_pill(v.get("call"))}'
        f'<span style="font-size:13px;font-weight:700;color:{conv_col}">{conv.upper()} conviction</span>'
        f'<span style="font-size:12px;color:#9aa0a6">3-model debate</span></div>'
        f'{_prose(v.get("verdict",""),color="#1a1a1a")}'
        + (f'<div style="font-size:14px;margin-top:6px"><b>Key risks:</b><ul style="margin:3px 0;padding-left:20px">{risks}</ul></div>' if risks else "")
        + extra + debate_block,
        bg="#f7f9fc")
