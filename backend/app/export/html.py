"""HTML-экспорт презентации (отдельная страница на слайд, без PPTX-зависимости).

Контент берётся из Deck (JSON), шрифты/цвета из профиля шаблона — единый
рендеринг-контракт с PPTX-версией.
"""
from __future__ import annotations

import html
import json

from ..models.deck import Deck
from ..render.images import hex_to_rgb


def _esc(s: str) -> str:
    return html.escape(s or "")


def _mix(a: str, b: str, t: float) -> str:
    def ch(h):
        return tuple(int((h or "#000000").lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    aa, bb = ch(a), ch(b)
    return "#%02X%02X%02X" % tuple(round(aa[i] * (1 - t) + bb[i] * t) for i in range(3))


def _style(profile: dict) -> str:
    pal = [p.get("hex") for p in profile.get("palette", []) if p.get("hex")]
    bg = next((h for h in pal if sum(hex_to_rgb(h)) > 500), "#FFFFFF")
    dark = next((h for h in pal if sum(hex_to_rgb(h)) < 300), "#1A1A1A")
    accent = next((h for h in pal[1:] if h not in (bg, dark)), "#E31E52")
    headline = profile.get("headline_font") or "Arial"
    body = profile.get("body_font") or "Arial"
    soft = _mix(accent, bg, 0.86)
    return f"""
:root {{ --bg:{bg}; --text:{dark}; --accent:{accent}; --soft:{soft}; }}
* {{ box-sizing:border-box; margin:0; }}
body {{ background:#0f0f12; font-family:'{body}', Arial, sans-serif; }}
.slide {{ width:100vw; height:100vh; display:none; background:var(--bg);
         color:var(--text); padding:6vh 7vw; position:relative; }}
.slide.active {{ display:flex; flex-direction:column; }}
h1 {{ font-family:'{headline}', Arial; font-size:clamp(24px,4vw,52px); color:var(--text); margin-bottom:2vh; }}
h2 {{ font-family:'{headline}', Arial; font-size:clamp(18px,2.6vw,34px); color:var(--accent); margin-bottom:1vh; }}
.sub {{ color:var(--text); opacity:.75; font-size:clamp(14px,2vw,22px); }}
ul {{ list-style:none; }}
li {{ font-size:clamp(15px,2.2vw,26px); line-height:1.5; padding:.35em 0 .35em 1.4em; position:relative; }}
li::before {{ content:'▪'; color:var(--accent); position:absolute; left:0; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:1.2vw; }}
.card {{ background:var(--soft); border-top:4px solid var(--accent); border-radius:10px; padding:1.2em; }}
.card .value {{ font-family:'{headline}', Arial; font-size:clamp(22px,3.4vw,44px); font-weight:700; color:var(--accent); }}
.card .label {{ opacity:.8; }}
table {{ border-collapse:collapse; width:100%; font-size:clamp(13px,1.9vw,22px); }}
th {{ background:var(--accent); color:#fff; padding:.5em .8em; text-align:left; }}
td {{ padding:.45em .8em; border-bottom:1px solid var(--soft); }}
.steps {{ display:flex; align-items:stretch; gap:1vw; }}
.step {{ flex:1; background:var(--accent); color:#fff; clip-path:polygon(0 0, 92% 0, 100% 50%, 92% 100%, 0 100%);
        padding:1.2em .8em 1.2em 1.4em; font-weight:700; }}
.step.alt {{ background:var(--soft); color:var(--text); }}
.counters {{ position:absolute; right:3vw; bottom:2vh; color:var(--text); opacity:.5; }}
.nav {{ position:fixed; left:0; right:0; bottom:0; display:flex; justify-content:center; gap:2em;
        background:rgba(15,15,18,.92); padding:14px; z-index:10; }}
.nav button {{ background:var(--accent); color:#fff; border:0; padding:10px 26px; border-radius:8px;
               font-size:16px; cursor:pointer; }}
.nav button:disabled {{ opacity:.4; cursor:default; }}
"""


def _blocks_to_html(deck: Deck, slide) -> str:
    parts = []
    for b in slide.blocks:
        if b.kind == "bullets":
            items = "".join(f"<li>{_esc(it)}</li>" for it in b.items)
            title = f"<h2>{_esc(b.title)}</h2>" if b.title else ""
            parts.append(f"<div>{title}<ul>{items}</ul></div>")
        elif b.kind == "text" or (b.kind == "paragraph" and b.text):
            parts.append(f"<p>{_esc(b.text)}</p>")
        elif b.kind == "factoids":
            cards = "".join(
                f'<div class="card"><div class="value">{_esc(fd.get("value", ""))}</div>'
                f'<div class="label">{_esc(fd.get("label", ""))}</div></div>'
                for fd in b.factoids)
            parts.append(f'<div class="grid">{cards}</div>')
        elif b.kind == "table" and b.table:
            head = "".join(f"<th>{_esc(h)}</th>" for h in b.table.header)
            rows = "".join(f"<tr>{''.join(f'<td>{_esc(c)}</td>' for c in r)}</tr>"
                           for r in b.table.rows)
            parts.append(f"<table><tr>{head}</tr>{rows}</table>")
        elif b.kind == "chart" and b.chart:
            series = b.chart.series[0] if b.chart.series else None
            if series:
                bars = "".join(
                    f'<div style="margin:.35em 0"><span style="display:inline-block;width:200px">{_esc(cat)}: '
                    f'<b>{val}</b></span></div>'
                    for cat, val in zip(b.chart.categories, series.values))
                parts.append(f"<div>{bars}</div>")
        elif b.kind == "quote":
            parts.append(
                f'<blockquote style="font-size:clamp(18px,2.8vw,34px);border-left:6px solid var(--accent);'
                f'padding-left:1em">«{_esc(b.quote_text)}»'
                f'<div style="font-size:.6em;color:var(--accent)">— {_esc(b.quote_author)}</div></blockquote>')
        elif b.kind == "steps":
            steps = "".join(
                f'<div class="step{" alt" if i % 2 else ""}">{_esc(t)}</div>'
                for i, t in enumerate(b.items))
            parts.append(f'<div class="steps">{steps}</div>')
    return "".join(parts)


def deck_to_html(deck: Deck, profile: dict, brief: str = "") -> str:
    slides_html = []
    for i, s in enumerate(deck.slides, start=1):
        body = _blocks_to_html(deck, s)
        slides_html.append(
            f'<section class="slide {"active" if i == 1 else ""}" id="s{i}">'
            f'<h1>{_esc(s.heading)}</h1>'
            f'{"<div class=\"sub\">" + _esc(s.subheading) + "</div>" if s.subheading else ""}'
            f'{body}<div class="counters">{i} / {len(deck.slides)}</div></section>')
    nav_js = """const slides=document.querySelectorAll('.slide');let cur=0;
function go(n){cur=(n+slides.length)%slides.length;slides.forEach((s,i)=>s.classList.toggle('active',i===cur));}
document.addEventListener('keydown',e=>{if(e.key==='ArrowRight')go(cur+1);if(e.key==='ArrowLeft')go(cur-1);});"""
    return f"""<!DOCTYPE html><html lang="{deck.language}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(deck.title)}</title><style>{_style(profile)}</style></head>
<body>
{''.join(slides_html)}
<nav class="nav"><button onclick="go(cur-1)">←</button>
<button onclick="go(cur+1)">→</button>
<span style="color:#888">генерирует «Цифровой дизайнер презентаций»</span></nav>
<script>{nav_js}</script></body></html>"""