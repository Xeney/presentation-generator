"""HTML+CSS-рендерер: второй путь сборки из того же widget-плана.

Тот же план, что уходит в python-pptx (`Renderer.build_plan`): координаты,
цвета и кегли считаются теми же правилами (ThemePair, контраст WCAG), но
результат — автономный HTML с инлайн-CSS, без CDN и внешних шрифтов.

Слайд — `<section class="slide">` с пропорцией из профиля; блоки позиционируются
в процентах от размера слайда. Это не замена PPTX-пути: оба рендерера живут
параллельно (ADR-035), а HTML→PPTX-конвертер собирает из этой разметки нативные
фигуры (ADR-036), а не растровую картинку.
"""
from __future__ import annotations

import base64
import html as html_module
import json
import logging
from typing import Any, Optional

from ..layout.engine import DesignContext
from ..layout.geometry import Rect, text_height_in
from ..models.deck import Block, ChartType, Deck, Slide, SlideType
from .pptx_renderer import Renderer

log = logging.getLogger("html_render")

# сколько виджетов рисуем для диаграммы, если серий/категорий слишком много
MAX_CHART_BARS = 12


def _esc(value: Any) -> str:
    return html_module.escape(str(value if value is not None else ""), quote=True)


def _pct(value: float, total: float) -> float:
    return round(value / total * 100.0, 3) if total else 0.0


def _style(**pairs) -> str:
    return ";".join(f"{key}:{value}" for key, value in pairs.items() if value not in (None, ""))


def _parse_style(style: str) -> dict:
    """Строка CSS → dict для `**` при склейке стилей."""
    out = {}
    for piece in style.split(";"):
        if ":" in piece:
            key, value = piece.split(":", 1)
            out[key] = value
    return out


def _rect_style(rect: dict, slide_w: float, slide_h: float) -> str:
    return _style(left=f"{_pct(rect['x'], slide_w)}%",
                  top=f"{_pct(rect['y'], slide_h)}%",
                  width=f"{_pct(rect['w'], slide_w)}%",
                  height=f"{_pct(rect['h'], slide_h)}%")


def _font(renderer: Renderer, heading: bool) -> str:
    stack = renderer._headline_font() if heading else renderer._body_font()
    return f"'{stack}', system-ui, sans-serif"


def _radius_style(rect: dict, slide_w: float) -> str:
    return f"border-radius:max(6px,{_pct(min(rect['w'], rect['h']), slide_w)}%)"


class HtmlRenderer:
    """Сборка HTML-колоды из widget-плана python-pptx-рендерера."""

    def __init__(self, profile: dict, variant: str = "compact",
                 template_bytes: bytes | None = None,
                 images: dict[str, bytes] | None = None):
        self.profile = profile
        self.variant = variant
        self.template_bytes = template_bytes
        self.images = images or {}
        self.slide_w = float((profile.get("slide_size") or {}).get("w_in", 13.333))
        self.slide_h = float((profile.get("slide_size") or {}).get("h_in", 7.5))
        self.renderer = Renderer(profile, variant=variant,
                                 template_bytes=template_bytes, images=images)

    # ------------------------------------------------------------- публичное
    def render(self, deck: Deck) -> str:
        dc = DesignContext.from_profile(self.profile)
        slides_html = []
        for index, slide in enumerate(deck.slides):
            layout = self.renderer._pick_layout(slide.slide_type, slide)
            self.renderer._layout = layout
            canvas = self.renderer._canvas(layout, slide)
            engine_cls = None
            from ..layout.engine import LayoutEngine

            engine = LayoutEngine(dc, variant=self.variant)
            items = engine.compose(slide, canvas)
            slides_html.append(self._slide(deck, slide, index, layout, items))
        return self._page(deck, slides_html)

    # ---------------------------------------------------------------- слайд
    def _slide(self, deck: Deck, slide: Slide, index: int, layout: dict,
               items: list[dict]) -> str:
        # подзаголовок не рисуем отдельно: как и в python-pptx-рендере, его
        # кладёт композитор витринных слайдов (иначе два наложенных абзаца)
        parts = [self._title(slide, layout)]
        for item in items:
            try:
                parts.append(self._item(item))
            except Exception as exc:  # noqa: BLE001 — один виджет не должен ронять колоду
                log.warning("виджет %s не отрисован: %s", item.get("widget"), exc)
                continue
        inner = "\n".join(part for part in parts if part)
        return (
            f'<section class="slide" data-slide="{index + 1}" '
            f'data-slide-type="{_esc(slide.slide_type.value)}" '
            f'data-layout="{_esc(layout.get("id", ""))}" '
            f'data-layout-name="{_esc(layout.get("name", ""))}">\n{inner}\n</section>')

    def _title_rect(self, layout: dict) -> Rect:
        title = layout.get("title_ph") or {}
        if title.get("w"):
            return Rect(title["x"], title["y"], title["w"], title["h"])
        grid = self.profile.get("grid") or {}
        return Rect(grid.get("body_x", 0.5), 0.35,
                    self.slide_w - grid.get("body_x", 0.5)
                    - grid.get("margin_right", 0.5), 0.75)

    def _title(self, slide: Slide, layout: dict) -> str:
        rect = self._title_rect(layout)
        color = self.renderer._text_on(rect)
        size = max(12.0, min(48.0, self.renderer._scale_pick("title", 28.0)))
        size = self.renderer._fit_size(slide.heading, max(0.4, rect.w - 0.1),
                                       max(0.2, rect.h * 0.95), default=size,
                                       kind="title", min_size=10.0)
        rendered = self.renderer._snap_size(size)
        style = _style(position="absolute", **_parse_style(_rect_style(rect.to_dict(), self.slide_w, self.slide_h)),
                       **{"font-family": _font(self.renderer, True),
                          "font-size": f"{rendered:g}pt", "font-weight": 700,
                          "color": color, "line-height": 1.1})
        return (f'<h1 class="el title" data-widget="title" style="{style}">'
                f'{_esc(slide.heading)}</h1>')

    # ---------------------------------------------------------------- виджеты
    def _item(self, item: dict) -> str:
        widget = item.get("widget", "")
        rect = item.get("rect") or {}
        if widget == "bullets":
            return self._bullets(item, rect)
        if widget == "numbered":
            return self._numbered(item, rect)
        if widget == "text":
            return self._text(item, rect)
        if widget == "paragraph":
            return self._paragraph(item, rect)
        if widget == "factoids":
            return self._factoids(item, rect)
        if widget == "table":
            return self._table(item, rect)
        if widget == "chart":
            return self._chart(item, rect)
        if widget == "quote":
            return self._quote(item, rect)
        if widget == "steps":
            return self._steps(item, rect)
        if widget == "image":
            return self._image(item, rect)
        return ""

    def _block_card(self, rect: dict, style: dict, widget: str) -> str:
        if not style.get("card"):
            return ""
        fill = self.renderer._card_fill(style)
        border = style.get("accent") or self.renderer._accent_color() or "#DDDDDD"
        css = _style(position="absolute", **_parse_style(_rect_style(rect, self.slide_w, self.slide_h)),
                     **{"background-color": fill, "border": f"1pt solid {border}",
                        **_parse_style(_radius_style(rect, self.slide_w))})
        return f'<div class="el card" data-widget="{widget}-card" style="{css}"></div>'

    def _block_title(self, item: dict, rect: dict) -> tuple[str, float]:
        block: Block = item.get("block")
        if block is None or not block.title or item.get("style", {}).get("card"):
            return "", 0.0
        size = self.renderer._scale_pick("title", 20.0)
        snapped = self.renderer._snap_size(size)
        height = max(0.28, snapped * 1.35 / 72.0 + 0.03)
        height = min(height, max(0.28, rect["h"] * 0.6))
        title_rect = {"x": rect["x"], "y": rect["y"], "w": rect["w"], "h": height}
        color = self.renderer._text_on(Rect(**title_rect))
        css = _style(position="absolute",
                     **_parse_style(_rect_style(title_rect, self.slide_w, self.slide_h)),
                     **{"font-family": _font(self.renderer, True),
                        "font-size": f"{snapped:g}pt", "font-weight": 700,
                        "color": color})
        return (f'<div class="el block-title" data-widget="block-title" style="{css}">'
                f'{_esc(block.title)}</div>', height + 0.02)

    def _card_bg(self, item: dict, rect: dict) -> Optional[str]:
        style = item.get("style", {})
        return self.renderer._card_fill(style) if style.get("card") else None

    def _bullets(self, item: dict, rect: dict) -> str:
        block: Block = item["block"]
        style = item.get("style", {})
        card = bool(style.get("card"))
        pad = 0.16 if card else 0.04
        inner = {k: v for k, v in _padded(rect, pad).items()}
        card_fill = self._card_bg(item, rect)
        title_html, shift = self._block_title(item, rect)
        body_rect = dict(inner)
        body_rect["y"] += shift
        body_rect["h"] = max(0.2, body_rect["h"] - shift)
        items = block.items or []
        size = self.renderer._fit_size("\n".join(items), body_rect["w"], body_rect["h"],
                                       default=16.0)
        color = self.renderer._text_on(Rect(**body_rect), background=card_fill)
        accent = self.renderer._accent_on(Rect(**body_rect), background=card_fill)
        lis = "\n".join(f'<li>{_esc(text)}</li>' for text in items)
        css = _style(position="absolute",
                     **_parse_style(_rect_style(body_rect, self.slide_w, self.slide_h)),
                     **{"font-family": _font(self.renderer, False),
                        "font-size": f"{size:g}pt", "color": color,
                        "--bullet-color": accent})
        return ("\n".join(part for part in [
            self._block_card(rect, style, "bullets"), title_html,
            f'<ul class="el bullets" data-widget="bullets" style="{css}">{lis}</ul>'] if part))

    def _numbered(self, item: dict, rect: dict) -> str:
        block: Block = item["block"]
        style = item.get("style", {})
        card = bool(style.get("card"))
        pad = 0.16 if card else 0.04
        inner = _padded(rect, pad)
        card_fill = self._card_bg(item, rect)
        title_html, shift = self._block_title(item, rect)
        body_rect = dict(inner)
        body_rect["y"] += shift
        body_rect["h"] = max(0.2, body_rect["h"] - shift)
        items = (block.items or [])[:6]
        size = self.renderer._fit_size("\n".join(items), body_rect["w"], body_rect["h"],
                                       default=16.0)
        color = self.renderer._text_on(Rect(**body_rect), background=card_fill)
        accent = self.renderer._accent_on(Rect(**body_rect), background=card_fill)
        lis = "\n".join(f'<li>{_esc(text)}</li>' for text in items)
        css = _style(position="absolute",
                     **_parse_style(_rect_style(body_rect, self.slide_w, self.slide_h)),
                     **{"font-family": _font(self.renderer, False),
                        "font-size": f"{size:g}pt", "color": color,
                        "--bullet-color": accent})
        return ("\n".join(part for part in [
            self._block_card(rect, style, "numbered"), title_html,
            f'<ol class="el numbered" data-widget="numbered" style="{css}">{lis}</ol>'] if part))

    def _text(self, item: dict, rect: dict) -> str:
        block: Block = item["block"]
        style = item.get("style", {})
        card = bool(style.get("card"))
        card_fill = self._card_bg(item, rect)
        title_html, shift = self._block_title(item, rect)
        inner = _padded(rect, 0.16 if card else 0.05)
        inner["y"] += shift
        inner["h"] = max(0.2, inner["h"] - shift)
        text = block.text or ""
        size = self.renderer._fit_size(text, inner["w"], inner["h"], default=15.0)
        color = self.renderer._text_on(Rect(**inner), background=card_fill)
        css = _style(position="absolute", **_parse_style(_rect_style(inner, self.slide_w, self.slide_h)),
                     **{"font-family": _font(self.renderer, False),
                        "font-size": f"{size:g}pt", "color": color, "line-height": 1.25})
        return ("\n".join(part for part in [
            self._block_card(rect, style, "text"), title_html,
            f'<div class="el text" data-widget="text" style="{css}">{_esc(text)}</div>'] if part))

    def _paragraph(self, item: dict, rect: dict) -> str:
        data = item.get("data", {})
        text = data.get("text") or ""
        is_sub = bool(data.get("is_sub"))
        size = self.renderer._scale_pick("title", 20.0) * (0.55 if is_sub else 0.8)
        color = self.renderer._text_on(Rect(**rect))
        css = _style(position="absolute", **_parse_style(_rect_style(rect, self.slide_w, self.slide_h)),
                     **{"font-family": _font(self.renderer, is_sub),
                        "font-size": f"{max(11, size):g}pt", "color": color,
                        "font-style": "italic" if is_sub else "normal"})
        return (f'<p class="el paragraph" data-widget="paragraph" style="{css}">'
                f'{_esc(text)}</p>')

    def _factoids(self, item: dict, rect: dict) -> str:
        import math
        block: Block = item["block"]
        style = item.get("style", {})
        items = block.factoids or []
        if not items:
            return ""
        n = len(items)
        cols = n if n <= 3 else (2 if n == 4 else 3)
        rows = math.ceil(n / cols)
        gap = 0.2
        card = bool(style.get("card"))
        card_fill = self._card_bg(item, rect)
        cell_w = (rect["w"] - gap * (cols - 1)) / cols
        cell_h = rect["h"] / rows
        cells = []
        for index, fact in enumerate(items):
            value = str(fact.get("value", ""))
            label = str(fact.get("label", ""))
            x = rect["x"] + (index % cols) * (cell_w + gap)
            y = rect["y"] + (index // cols) * cell_h
            cell = {"x": x + 0.08, "y": y + 0.06,
                    "w": cell_w - 0.16, "h": cell_h - 0.12}
            if card:
                bg = _style(**_parse_style(_rect_style(cell, self.slide_w, self.slide_h)),
                            **{"background-color": card_fill or "transparent",
                               **_parse_style(_radius_style(cell, self.slide_w))})
                cells.append(f'<div class="el factoid-card" style="{bg}"></div>')
                cell = _padded(cell, 0.14)
            text_w = max(0.5, cell["w"] - 0.25)
            big = self.renderer._scale_pick("title", 34.0) * (0.55 if card else 0.9)
            label_size = self.renderer._fit_size(label or value, text_w,
                                                 max(0.18, min(0.5, cell["h"] * 0.4)),
                                                 default=max(8.0, big * 0.35),
                                                 kind="any", min_size=8.0)
            label_final = self.renderer._snap_size(label_size)
            label_h = max(0.16, min(cell["h"] * 0.6,
                                    text_height_in(label, text_w, label_final) + 0.02))
            value_room = max(0.24, cell["h"] - label_h)
            one_line = self.renderer._fit_one_line(value, text_w, value_room, big,
                                                   min_size=10.0)
            if one_line:
                value_size = one_line
                value_h = max(0.24, value_size * 1.35 / 72.0 + 0.02)
            else:
                value_size = self.renderer._fit_size(value, text_w, value_room,
                                                     default=big, kind="any", min_size=10.0)
                value_h = max(0.24, min(cell["h"] - 0.10,
                                        text_height_in(value, text_w, value_size) + 0.02))
            # та же логика, что в python-pptx-рендере: сначала ужимаем пару до
            # минимумов шкалы, и только если «число + подпись» не помещаются
            # вовсе — опускаем подпись (иначе HTML-путь теряет паритет аудита)
            if label and value_h + label_h > cell["h"]:
                tight_label = self.renderer._fit_size(
                    label, text_w, max(0.14, cell["h"] * 0.3),
                    default=max(8.0, big * 0.3), kind="any", min_size=8.0)
                tight_label_final = self.renderer._snap_size(tight_label)
                tight_label_h = max(0.14, min(cell["h"] * 0.5,
                                              text_height_in(label, text_w,
                                                             tight_label_final) + 0.02))
                tight_room = max(0.2, cell["h"] - tight_label_h)
                tight_value = self.renderer._fit_size(value, text_w, tight_room,
                                                      default=big, kind="any",
                                                      min_size=10.0)
                tight_value_h = max(0.2, min(tight_room, text_height_in(
                    value, text_w, tight_value) + 0.02))
                if tight_value_h + tight_label_h <= cell["h"]:
                    value_size, value_h = tight_value, tight_value_h
                    label_size, label_h = tight_label, tight_label_h
                elif value_h <= cell["h"]:
                    label = ""
                    label_h = 0.0
            accent = self.renderer._accent_on(Rect(**cell), large=value_size >= 24,
                                              background=card_fill)
            label_c = self.renderer._text_on(Rect(**cell), background=card_fill)
            group_h = value_h + label_h
            group_y = cell["y"] + max(0.0, (cell["h"] - group_h) / 2)
            v_rect = {"x": cell["x"], "y": group_y, "w": cell["w"], "h": value_h}
            v_css = _style(position="absolute",
                           **_parse_style(_rect_style(v_rect, self.slide_w, self.slide_h)),
                           **{"font-family": _font(self.renderer, True),
                              "font-size": f"{value_size:g}pt", "font-weight": 700,
                              "color": accent, "line-height": 1.15})
            cells.append(f'<div class="el factoid-value" data-widget="factoid-value" '
                         f'data-value="{_esc(value)}" style="{v_css}">{_esc(value)}</div>')
            if label:
                l_rect = {"x": cell["x"], "y": group_y + value_h, "w": cell["w"],
                          "h": label_h}
                l_css = _style(position="absolute",
                               **_parse_style(_rect_style(l_rect, self.slide_w, self.slide_h)),
                               **{"font-family": _font(self.renderer, False),
                                  "font-size": f"{label_size:g}pt", "color": label_c})
                cells.append(f'<div class="el factoid-label" data-widget="factoid-label" '
                             f'data-label="{_esc(label)}" style="{l_css}">{_esc(label)}</div>')
        return "\n".join(cells)

    def _table(self, item: dict, rect: dict) -> str:
        from ..models.deck import Table

        block: Block = item["block"]
        table: Optional[Table] = block.table
        if table is None:
            return ""
        style = item.get("style", {})
        accent = style.get("accent") or self.renderer._accent_color() or "#444444"
        soft = self.renderer._accent_soft()
        header_text = self.renderer._readable_text(accent)
        text_color = self.renderer._text_on(Rect(**rect))
        font = _font(self.renderer, False)
        head = "".join(f'<th>{_esc(cell)}</th>' for cell in table.header)
        rows = []
        for ri, row in enumerate(table.rows):
            zebra = "zebra" if ri % 2 else ""
            cells = "".join(f'<td>{_esc(cell)}</td>' for cell in row)
            rows.append(f'<tr class="{zebra}">{cells}</tr>')
        css = _style(position="absolute", **_parse_style(_rect_style(rect, self.slide_w, self.slide_h)),
                     **{"--header-bg": accent, "--header-color": header_text,
                        "--cell-color": text_color, "--zebra-bg": soft,
                        "font-family": font})
        return (f'<table class="el data-table" data-widget="table" style="{css}">'
                f'<thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table>')

    def _chart(self, item: dict, rect: dict) -> str:
        block: Block = item["block"]
        chart = block.chart
        if chart is None or not chart.series:
            return ""
        palette = self.renderer._palette()[4:] or ["#4472C4", "#ED7D31", "#A5A5A5",
                                                   "#FFC000", "#70AD47"]
        accent = self.renderer._accent_color() or palette[0]
        series_colors = [palette[i % len(palette)] for i in range(len(chart.series))]
        text_color = self.renderer._text_on(Rect(**rect))
        payload = {
            "type": chart.type.value,
            "categories": list(chart.categories),
            "series": [{"name": s.name, "values": list(s.values)}
                       for s in chart.series],
            "colors": series_colors,
            "unit": chart.unit,
        }
        if chart.type in (ChartType.PIE, ChartType.DONUT):
            body = self._chart_pie(chart, series_colors)
        elif chart.type == ChartType.LINE:
            body = self._chart_line(chart, series_colors, text_color)
        else:
            body = self._chart_bars(chart, series_colors, text_color)
        css = _style(position="absolute", **_parse_style(_rect_style(rect, self.slide_w, self.slide_h)),
                     **{"color": text_color})
        return (f'<div class="el chart" data-widget="chart" '
                f'data-chart="{_esc(json.dumps(payload, ensure_ascii=False))}" '
                f'style="{css}">{body}</div>')

    @staticmethod
    def _chart_bars(chart, colors: list[str], text_color: str) -> str:
        categories = list(chart.categories)[:MAX_CHART_BARS]
        series_values = []
        for series in chart.series:
            values = list(series.values)[:MAX_CHART_BARS]
            series_values.append(values)
        top = max((max(values) for values in series_values if values), default=1.0) or 1.0
        groups = []
        for index, category in enumerate(categories):
            bars = []
            for si, values in enumerate(series_values):
                value = values[index] if index < len(values) else 0.0
                height = max(2.0, value / top * 100.0)
                bars.append(
                    f'<div class="bar" style="height:{height:.1f}%;'
                    f'background-color:{colors[si % len(colors)]}" '
                    f'title="{_esc(chart.series[si].name)}: {value:g}"></div>')
            groups.append(
                f'<div class="bar-group"><div class="bars">{"".join(bars)}</div>'
                f'<div class="bar-label">{_esc(category)}</div></div>')
        legend = "".join(
            f'<span class="legend-item"><i style="background-color:{colors[i % len(colors)]}">'
            f'</i>{_esc(series.name)}</span>' for i, series in enumerate(chart.series))
        return (f'<div class="chart-bars">{"".join(groups)}</div>'
                f'<div class="chart-legend">{legend}</div>')

    @staticmethod
    def _chart_line(chart, colors: list[str], text_color: str) -> str:
        categories = list(chart.categories)[:MAX_CHART_BARS]
        points = []
        maxima = max((max(s.values) for s in chart.series if s.values), default=1.0) or 1.0
        for si, series in enumerate(chart.series):
            coords = []
            for index, value in enumerate(list(series.values)[:len(categories)]):
                x = index / max(1, len(categories) - 1) * 100.0
                y = 100.0 - (value / maxima * 90.0)
                coords.append(f"{x:.1f},{y:.1f}")
            points.append(
                f'<polyline points="{" ".join(coords)}" fill="none" '
                f'stroke="{colors[si % len(colors)]}" stroke-width="2"/>')
        labels = "".join(f'<span style="flex:1;text-align:{i and "center" or "left"};'
                         f'color:{text_color}">{_esc(c)}</span>'
                         for i, c in enumerate(categories))
        return (f'<svg class="chart-line" viewBox="0 0 100 100" preserveAspectRatio="none">'
                f'{"".join(points)}</svg><div class="line-labels">{labels}</div>')

    @staticmethod
    def _chart_pie(chart, colors: list[str]) -> str:
        values = list(chart.series[0].values) if chart.series else []
        total = sum(values) or 1.0
        stops = []
        start = 0.0
        for index, value in enumerate(values):
            share = value / total * 100.0
            stops.append(f"{colors[index % len(colors)]} {start:.1f}% {start + share:.1f}%")
            start += share
        legend = "".join(
            f'<span class="legend-item"><i style="background-color:'
            f'{colors[i % len(colors)]}"></i>{_esc(chart.categories[i] if i < len(chart.categories) else "")}'
            f'</span>' for i in range(len(values)))
        return (f'<div class="pie" style="background:conic-gradient({",".join(stops)})"></div>'
                f'<div class="chart-legend">{legend}</div>')

    def _quote(self, item: dict, rect: dict) -> str:
        block: Block = item["block"]
        style = item.get("style", {})
        accent = style.get("accent") or self.renderer._accent_color() or "#888888"
        inner = _sub(rect, 0.25, 0.05, rect["w"] - 0.3, rect["h"] - 0.1)
        size = self.renderer._fit_size(block.quote_text or "", inner["w"],
                                       inner["h"] * 0.8, default=20.0)
        text_color = self.renderer._text_on(Rect(**inner))
        author_color = self.renderer._accent_on(Rect(**inner))
        bar_css = _style(**{
            "position": "absolute",
            "left": f"{_pct(rect['x'], self.slide_w)}%",
            "top": f"{_pct(rect['y'], self.slide_h)}%",
            "width": "0.7%",
            "height": f"{_pct(rect['h'], self.slide_h)}%",
            "background-color": accent})
        text_css = _style(position="absolute",
                          **_parse_style(_rect_style(inner, self.slide_w, self.slide_h)),
                          **{"font-family": _font(self.renderer, True),
                             "font-size": f"{size:g}pt", "font-style": "italic",
                             "color": text_color, "line-height": 1.25})
        author = ""
        if block.quote_author:
            author_rect = dict(inner)
            author_rect["y"] = inner["y"] + inner["h"] * 0.8
            author_rect["h"] = max(0.2, inner["h"] * 0.2)
            a_css = _style(position="absolute",
                           **_parse_style(_rect_style(author_rect, self.slide_w, self.slide_h)),
                           **{"font-family": _font(self.renderer, False),
                              "font-size": f"{max(11.0, size * 0.6):g}pt",
                              "font-weight": 700, "color": author_color})
            author = (f'<div class="el quote-author" data-widget="quote-author" '
                      f'style="{a_css}">— {_esc(block.quote_author)}</div>')
        return (f'<div class="el quote-bar" data-widget="quote-bar" style="{bar_css}"></div>'
                f'<blockquote class="el quote" data-widget="quote" style="{text_css}">'
                f'«{_esc(block.quote_text or "")}»</blockquote>{author}')

    def _steps(self, item: dict, rect: dict) -> str:
        block: Block = item["block"]
        style = item.get("style", {})
        accent = style.get("accent") or self.renderer._accent_color() or "#888888"
        soft = self.renderer._accent_soft()
        items = (block.items or [])[:6]
        if not items:
            return ""
        cells = []
        for index, text in enumerate(items):
            fill = soft if index % 2 else accent
            color = self.renderer._readable_text(fill)
            size = self.renderer._fit_size(text, max(0.5, rect["w"] / len(items) * 0.6),
                                           rect["h"] * 0.7, default=13.0)
            cell = {"x": rect["x"] + rect["w"] * index / len(items), "y": rect["y"],
                    "w": rect["w"] / len(items), "h": rect["h"]}
            css = _style(**{
                **_parse_style(_rect_style(cell, self.slide_w, self.slide_h)),
                "background-color": fill, "color": color,
                "clip-path": "polygon(0 0, calc(100% - 12px) 0, 100% 50%, "
                             "calc(100% - 12px) 100%, 0 100%, 12px 50%)",
                "display": "flex", "align-items": "center",
                "justify-content": "center", "text-align": "center",
                "font-family": _font(self.renderer, False),
                "font-size": f"{size:g}pt", "font-weight": 700,
                "padding": "0 10px"})
            cells.append(f'<div class="el chevron" data-widget="chevron" '
                         f'data-index="{index}" style="{css}">{_esc(text)}</div>')
        return "\n".join(cells)

    def _image(self, item: dict, rect: dict) -> str:
        block: Block = item["block"]
        style = item.get("style", {})
        caption = block.image_caption or block.image_prompt or ""
        cap_h = 0.34 if caption else 0.0
        cap_rect = {"x": rect["x"], "y": rect["y"] + rect["h"] - cap_h,
                    "w": rect["w"], "h": cap_h} if caption else None
        data = self.images.get(block.image_ref) or self.images.get(block.image_caption or "")
        if data:
            mime = "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
            encoded = base64.b64encode(data).decode("ascii")
            # картинка вписывается средствами CSS object-fit
            height = rect["h"] - (cap_h + 0.04 if caption else 0)
            box = {"x": rect["x"], "y": rect["y"], "w": rect["w"], "h": height}
            css = _style(**{
                **_parse_style(_rect_style(box, self.slide_w, self.slide_h)),
                "object-fit": "contain"})
            image = (f'<img class="el image" data-widget="image" '
                     f'src="data:{mime};base64,{encoded}" '
                     f'alt="{_esc(caption or "иллюстрация")}" style="{css}"/>')
        else:
            fill = style.get("accent_soft") or self.renderer._accent_soft()
            border = style.get("accent") or self.renderer._accent_color() or "#CCCCCC"
            css = _style(position="absolute",
                         **_parse_style(_rect_style(rect, self.slide_w, self.slide_h)),
                         **{"background-color": fill, "border": f"1pt solid {border}",
                            **_parse_style(_radius_style(rect, self.slide_w)),
                            "display": "flex", "align-items": "center",
                            "justify-content": "center", "text-align": "center",
                            "color": style.get("text_color") or self.renderer._text_on(Rect(**rect)),
                            "font-family": _font(self.renderer, False),
                            "font-size": "12pt", "padding": "8px", "box-sizing": "border-box"})
            image = (f'<div class="el image-slot" data-widget="image-slot" '
                     f'style="{css}">{_esc(caption[:120])}</div>')
        caption_html = ""
        if cap_rect is not None:
            c_css = _style(position="absolute",
                           **_parse_style(_rect_style(cap_rect, self.slide_w, self.slide_h)),
                           **{"font-family": _font(self.renderer, False),
                              "font-size": "11pt",
                              "color": self.renderer._text_on(Rect(**cap_rect))})
            caption_html = (f'<div class="el image-caption" data-widget="image-caption" '
                            f'style="{c_css}">{_esc(caption)}</div>')
        return image + caption_html

    # ------------------------------------------------------------------ каркас
    def _root_vars(self) -> str:
        profile = self.profile
        content = next((l for l in profile.get("layouts", []) if l.get("role") == "content"),
                       (profile.get("layouts") or [{}])[0])
        theme = (content or {}).get("theme") or {}
        bg = theme.get("background") or self.renderer._bg_color()
        text = theme.get("foreground") or self.renderer._text_on(Rect(0, 0, 1, 1))
        accent = theme.get("accent") or self.renderer._accent_color() or "#1D4ED8"
        muted = theme.get("muted") or text
        scale = profile.get("type_scale") or {}
        titles = [float(v) for v in scale.get("title") or []]
        bodies = [float(v) for v in scale.get("body") or []]
        grid = profile.get("grid") or {}
        return _style(
            **{"--color-bg": bg, "--color-text": text, "--color-accent": accent,
               "--color-muted": muted,
               "--color-card": self.renderer._accent_soft(),
               "--font-heading": _font(self.renderer, True),
               "--font-body": _font(self.renderer, False),
               "--size-h1": f"{max(titles) if titles else 40:g}pt",
               "--size-h2": f"{min(titles) if titles else 24:g}pt",
               "--size-body": f"{max([b for b in bodies if b <= 24] or [16]):g}pt",
               "--slide-w": f"{self.slide_w:g}", "--slide-h": f"{self.slide_h:g}",
               "--grid-margin": f"{grid.get('body_x', 0.5):g}",
               "--grid-top": f"{grid.get('body_y', 1.0):g}"})

    def _page(self, deck: Deck, slides: list[str]) -> str:
        languages = {"ru": "ru", "en": "en"}
        title = deck.title
        return _PAGE.format(
            lang=languages.get(deck.language, "ru"),
            title=_esc(title),
            variant=_esc(self.variant),
            root_vars=self._root_vars(),
            slides="\n".join(slides),
            theme_json=_esc(json.dumps(
                next((l.get("theme") for l in self.profile.get("layouts", [])
                      if l.get("role") == "content"), {}), ensure_ascii=False)),
            slide_w=f"{self.slide_w:g}", slide_h=f"{self.slide_h:g}")


def _padded(rect: dict, pad: float) -> dict:
    return {"x": rect["x"] + pad, "y": rect["y"] + pad,
            "w": max(0.1, rect["w"] - 2 * pad), "h": max(0.1, rect["h"] - 2 * pad)}


def _sub(rect: dict, x: float, y: float, w: float, h: float) -> dict:
    return {"x": rect["x"] + x, "y": rect["y"] + y, "w": max(0.1, w), "h": max(0.1, h)}


def render_html(deck: Deck, profile: dict, variant: str = "compact",
                template_bytes: bytes | None = None,
                images: dict[str, bytes] | None = None) -> str:
    """Один HTML-файл на колоду: та же вёрстка, что у PPTX (ADR-035)."""
    return HtmlRenderer(profile, variant=variant, template_bytes=template_bytes,
                        images=images).render(deck)


_PAGE = """<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{title}</title>
<style>
:root {{ {root_vars} }}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; background: #111318; color: #E5E7EB;
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }}
.deck {{ display: flex; flex-direction: column; align-items: center; gap: 28px;
  padding: 32px 0 64px; }}
.slide {{ position: relative; overflow: hidden; background: var(--color-bg);
  color: var(--color-text); width: min(96vw, calc(92vh * {slide_w} / {slide_h}));
  aspect-ratio: {slide_w} / {slide_h}; box-shadow: 0 10px 30px rgba(0,0,0,.35); }}
.slide[data-slide]::after {{ content: attr(data-slide); position: absolute;
  right: 2.2%; bottom: 2%; font-size: 10pt; color: var(--color-muted); opacity: .55; }}
.el {{ position: absolute; }}
.title {{ margin: 0; }}
.subtitle {{ margin: 0; }}
.bullets, .numbered {{ margin: 0; padding-left: 1.1em; }}
.bullets li, .numbered li {{ margin: 0 0 .18em 0; padding-left: .12em; }}
.bullets li::marker {{ color: var(--bullet-color, var(--color-accent)); }}
.numbered li::marker {{ color: var(--bullet-color, var(--color-accent)); font-weight: 700; }}
.card {{ border-radius: 8px; }}
.text {{ white-space: pre-wrap; }}
.data-table {{ border-collapse: collapse; width: auto; height: auto; table-layout: fixed; }}
.data-table th {{ background: var(--header-bg); color: var(--header-color);
  font-weight: 700; }}
.data-table td {{ color: var(--cell-color); border-bottom: 1px solid rgba(128,128,128,.35); }}
.data-table tr.zebra td {{ background: var(--zebra-bg); }}
.data-table th, .data-table td {{ padding: 4px 8px; font-size: 11pt; text-align: left; }}
.chart {{ display: flex; flex-direction: column; justify-content: flex-end; }}
.chart-bars {{ display: flex; align-items: flex-end; gap: 10px; height: 82%;
  border-bottom: 1px solid rgba(128,128,128,.4); padding-bottom: 2px; }}
.bar-group {{ flex: 1; display: flex; flex-direction: column; align-items: center;
  height: 100%; justify-content: flex-end; }}
.bars {{ display: flex; align-items: flex-end; gap: 2px; height: 88%; width: 100%;
  justify-content: center; }}
.bar {{ width: 18px; min-width: 6px; }}
.bar-label {{ font-size: 9pt; opacity: .8; margin-top: 4px; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; max-width: 100%; }}
.chart-legend {{ display: flex; flex-wrap: wrap; gap: 12px; font-size: 9pt;
  margin-top: 6px; }}
.legend-item {{ display: inline-flex; align-items: center; gap: 5px; }}
.legend-item i {{ width: 10px; height: 10px; display: inline-block; border-radius: 2px; }}
.chart-line {{ width: 100%; height: 82%; }}
.line-labels {{ display: flex; font-size: 9pt; }}
.pie {{ width: 65%; aspect-ratio: 1; border-radius: 50%; margin: 0 auto; }}
.quote {{ margin: 0; }}
.chevron {{ box-sizing: border-box; }}
.image {{ height: auto; }}
.image-caption {{ font-style: italic; opacity: .85; }}
.deck-nav {{ position: fixed; right: 18px; bottom: 18px; display: flex; gap: 8px;
  z-index: 10; }}
.deck-nav button {{ width: 44px; height: 44px; border-radius: 10px; border: 1px solid
  rgba(255,255,255,.25); background: rgba(20,22,28,.85); color: #fff; font-size: 18px;
  cursor: pointer; }}
.deck-nav button:hover {{ background: rgba(40,44,54,.95); }}
@media print {{
  .deck {{ display: block; padding: 0; gap: 0; }}
  .slide {{ width: 100vw; height: 100vh; margin: 0; break-after: page;
    box-shadow: none; }}
  .deck-nav {{ display: none; }}
}}
</style>
</head>
<body>
<main class="deck" data-variant="{variant}" data-theme="{theme_json}">
{slides}
</main>
<nav class="deck-nav" aria-label="Навигация по слайдам">
  <button type="button" data-nav="prev" aria-label="Предыдущий слайд">↑</button>
  <button type="button" data-nav="next" aria-label="Следующий слайд">↓</button>
</nav>
<script>
(function () {{
  var slides = Array.prototype.slice.call(document.querySelectorAll('.slide'));
  var current = 0;
  function go(index) {{
    if (!slides.length) return;
    current = Math.max(0, Math.min(slides.length - 1, index));
    slides[current].scrollIntoView({{ behavior: 'smooth', block: 'center' }});
  }}
  document.querySelectorAll('[data-nav]').forEach(function (button) {{
    button.addEventListener('click', function () {{
      go(current + (button.dataset.nav === 'next' ? 1 : -1));
    }});
  }});
  document.addEventListener('keydown', function (event) {{
    if (event.key === 'ArrowDown' || event.key === 'PageDown') go(current + 1);
    if (event.key === 'ArrowUp' || event.key === 'PageUp') go(current - 1);
  }});
}})();
</script>
</body>
</html>
"""
