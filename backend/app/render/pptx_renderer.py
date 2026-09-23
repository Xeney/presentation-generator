"""Рендер колоды в PPTX нативными объектами python-pptx.

Правила:
  - каждый слайд создаётся на макете из шаблона (роль определяется профилем);
  - заголовок кладётся в title-плейсхолдер макета (если есть), иначе — текстбоксом;
  - весь контент — нативные объекты (тексты, автофигуры, таблицы, графики);
    слайд картинкой не рисуется никогда;
  - используются только разрешённые токены: шрифты, палитра, типографическая шкала.
"""
from __future__ import annotations

import io
import logging
import re
from typing import Optional

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_SHAPE, PP_PLACEHOLDER
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

from ..layout.engine import DesignContext, LayoutEngine
from ..layout.geometry import Rect
from ..models.deck import Block, Chart, ChartType, Slide, SlideType
from .images import hex_to_rgb

log = logging.getLogger("render")

A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"

# имя фигуры-слота, который остаётся вместо картинки, если изображения нет;
# аудит использует его для проверки image_missing
IMAGE_SLOT_NAME = "image_slot"

CHART_MAP = {
    ChartType.BAR: XL_CHART_TYPE.BAR_CLUSTERED,
    ChartType.COLUMN: XL_CHART_TYPE.COLUMN_CLUSTERED,
    ChartType.LINE: XL_CHART_TYPE.LINE_MARKERS,
    ChartType.PIE: XL_CHART_TYPE.PIE,
    ChartType.DONUT: XL_CHART_TYPE.DOUGHNUT,
}


class RenderError(RuntimeError):
    """Шаблон не передан, повреждён или не является PPTX."""


def _in(v: float) -> Emu:
    return Inches(v)


def _pt(v: float) -> Pt:
    return Pt(v)


def _color(h: Optional[str]) -> Optional[RGBColor]:
    if not h:
        return None
    try:
        return RGBColor(*hex_to_rgb(h))
    except Exception:
        return None


def _apply_run_font(r, name: str, size: float, bold: bool, color: str | None,
                    italic: bool = False):
    r.font.size = Pt(size)
    r.font.bold = bold
    r.font.italic = italic
    if name:
        r.font.name = name
        rPr = r._r.get_or_add_rPr()
        rPr.set("lang", "ru-RU")
        for tag in ("latin", "ea", "cs"):
            el = rPr.find(qn(f"a:{tag}"))
            if el is None:
                el = rPr.makeelement(qn(f"a:{tag}"), {})
                rPr.append(el)
            el.set("typeface", name)
    if color:
        c = _color(color)
        if c is not None:
            r.font.color.rgb = c


def _add_para(tf, text: str, *, first: bool = False) -> None:
    if first and not tf.paragraphs[0].runs:
        p = tf.paragraphs[0]
    else:
        p = tf.add_paragraph()
    return p


class Renderer:
    """Превращает Deck + план вёрстки в файл PPTX (bytes)."""

    def __init__(self, profile: dict, variant: str = "compact",
                 template_bytes: bytes | None = None,
                 images: dict[str, bytes] | None = None):
        self.profile = profile
        self.variant = variant
        self.template_bytes = template_bytes
        # реестр картинок контент-пакета: ключ (имя файла/подпись) -> байты изображения
        self.images = images or {}

    # ------------------------------------------------------------- layout choice
    @staticmethod
    def _slide_kind(slide: Optional[Slide]) -> Optional[str]:
        """Какой композиционный тип макета нужен этому слайду."""
        if slide is None:
            return None
        kinds = [b.kind for b in slide.blocks]
        if not kinds:
            return None
        if "table" in kinds:
            return "table"
        if "chart" in kinds:
            return "chart"
        if "image" in kinds:
            return "image_text"
        if sum(1 for k in kinds if k != "image") >= 3:
            return "multi_column"
        return "bullets"

    def _pick_layout(self, stype: SlideType, slide: Optional[Slide] = None) -> dict:
        """Выбирает макет шаблона под тип слайда и его содержимое.

        Роль макета пришла из структурной классификации профиля; композиционный
        тип (`kind`) даёт бонус, если совпадает с содержимым слайда. Если макетов
        нужной роли в шаблоне нет, используется контентный — деградация без ошибок.
        """
        layouts = self.profile.get("layouts", [])
        if not layouts:
            raise RenderError("в профиле шаблона нет макетов — нечего использовать")
        target = "title" if stype == SlideType.TITLE else \
            "section" if stype == SlideType.SECTION else \
            "final" if stype in (SlideType.FINAL,) else \
            "agenda" if stype == SlideType.AGENDA else "content"
        cands = [l for l in layouts if l.get("role") == target]
        if not cands:
            cands = [l for l in layouts if l.get("role") == "content"]
        if not cands:
            cands = layouts
        want_kind = self._slide_kind(slide)

        def key(l: dict) -> float:
            score = float(l.get("score", 0.0))
            if want_kind and l.get("kind") == want_kind:
                score += 0.3
            return score

        return max(cands, key=key)

    def _canvas(self, layout: dict) -> Rect:
        """Контентная область слайда: тело макета, иначе сетка из профиля."""
        slide_w = self.profile["slide_size"]["w_in"]
        slide_h = self.profile["slide_size"]["h_in"]
        body = layout.get("body") or {}
        grid = self.profile.get("grid") or {}
        if body.get("w", 0) >= 0.45 * slide_w and body.get("h", 0) >= 0.3 * slide_h:
            rect = Rect(body["x"], body["y"], body["w"], body["h"])
        else:
            rect = Rect(grid.get("body_x", 0.5), grid.get("body_y", 1.0),
                        grid.get("body_w", slide_w - 1.0),
                        grid.get("body_h", slide_h - 1.5))
        return self._avoid_title(rect, layout)

    @staticmethod
    def _avoid_title(rect: Rect, layout: dict) -> Rect:
        """Сдвигает область контента ниже заголовка, если она на него заходит.

        На витринных макетах (титул, раздел, финал) тело макета нередко занимает
        весь слайд вместе с заголовком — без этого правила блоки накрывали бы
        заголовок, что аудит справедливо отмечает как наложение.
        """
        title = layout.get("title_ph") or {}
        if not title:
            return rect
        title_top = float(title.get("y", 0.0))
        title_bottom = title_top + float(title.get("h", 0.0))
        if rect.y < title_bottom and rect.bottom > title_top:
            below_h = rect.bottom - (title_bottom + 0.08)
            if below_h >= 0.6:
                return Rect(rect.x, title_bottom + 0.08, rect.w, below_h)
            # на макетах вроде «Section Header» заголовок стоит внизу —
            # тогда контент логично разместить над ним
            above_h = title_top - 0.08 - rect.y
            if above_h >= 0.6:
                return Rect(rect.x, rect.y, rect.w, above_h)
        return rect

    # -------------------------------------------------------------------- render
    def build_plan(self, deck: Deck, dc: DesignContext) -> dict[int, list[dict]]:
        """План вёрстки для всей колоды: макет + композиция блоков на слайд.

        Единая точка входа для API, тестов и инструментов — раньше каждый
        вызывающий собирал план сам и повторял выбор макета.
        """
        plan: dict[int, list[dict]] = {}
        for i, slide in enumerate(deck.slides):
            layout = self._pick_layout(slide.slide_type, slide)
            engine = LayoutEngine(dc, variant=self.variant)
            plan[i] = engine.compose(slide, self._canvas(layout))
        return plan

    def render_deck(self, deck: Deck, dc: DesignContext) -> bytes:
        """План + рендер одной операцией (основной путь использования)."""
        return self.render(deck, self.build_plan(deck, dc))

    def render(self, deck: Deck, plan_map: dict[int, list[dict]]) -> bytes:
        src = self.template_bytes
        if not src:
            raise RenderError(
                "не передан шаблон: слайды собираются только на макетах шаблона")
        try:
            prs = Presentation(io.BytesIO(src))
        except Exception as exc:  # noqa: BLE001 — любая ошибка разбора = невалидный шаблон
            raise RenderError(f"шаблон не открывается как PPTX: {exc}") from exc

        # исходные слайды-образцы шаблона удаляются вместе с частями пакета,
        # иначе они остаются в файле мусором и конфликтуют по именам с новыми
        self._drop_original_slides(prs)

        for i, sl in enumerate(deck.slides):
            layout = self._pick_layout(sl.slide_type, sl)
            new = prs.slides.add_slide(self._layout_object(prs, layout))
            # add_slide уже переносит плейсхолдеры макета: заголовок остаётся,
            # неиспользуемые пустые рамки убираем, чтобы не мусорить в файле
            self._drop_empty_placeholders(new)
            self._draw_slide(new, sl, layout, plan_map.get(i, []))
        buf = io.BytesIO()
        prs.save(buf)
        return buf.getvalue()

    @staticmethod
    def _drop_original_slides(prs: Presentation) -> None:
        """Удаляет слайды шаблона: и ссылки в sldIdLst, и части пакета."""
        sld_id_lst = prs.slides._sldIdLst
        for sld_id in list(sld_id_lst):
            r_id = sld_id.get(qn("r:id"))
            if r_id:
                try:
                    prs.part.drop_rel(r_id)
                except Exception:  # noqa: BLE001 — связь могла быть уже удалена
                    pass
            sld_id_lst.remove(sld_id)

    def _layout_object(self, prs: Presentation, layout_prof: dict):
        wanted_master = layout_prof.get("master_id")
        wanted_name = layout_prof.get("name")
        for mi, master in enumerate(prs.slide_masters):
            matches_master = (not wanted_master) or wanted_master == f"M{mi}"
            for layout in master.slide_layouts:
                if matches_master and layout.name == wanted_name:
                    return layout
        for master in prs.slide_masters:
            for layout in master.slide_layouts:
                if layout.name == layout_prof.get("name"):
                    return layout
        return prs.slide_masters[0].slide_layouts[0]

    @staticmethod
    def _layout_id(layout) -> str:
        return layout.name

    @staticmethod
    def _drop_empty_placeholders(slide) -> None:
        """Удаляет пустые плейсхолдеры, оставшиеся от макета.

        Заголовок сохраняется всегда: он заполняется вёрсткой. Остальные рамки
        (подзаголовок, тело, дата и т.п.) без текста только мусорят файл и
        провоцируют ложные срабатывания анализа наложений.
        """
        try:
            placeholders = list(slide.placeholders)
        except Exception:  # noqa: BLE001
            return
        keep = {PP_PLACEHOLDER.TITLE.value, PP_PLACEHOLDER.CENTER_TITLE.value,
                PP_PLACEHOLDER.SLIDE_NUMBER.value, PP_PLACEHOLDER.FOOTER.value,
                PP_PLACEHOLDER.HEADER.value, PP_PLACEHOLDER.DATE.value}
        for ph in placeholders:
            try:
                ph_type = getattr(ph.placeholder_format.type, "value",
                                  ph.placeholder_format.type)
                if ph_type in keep:
                    continue
                if ph.has_text_frame and not ph.text_frame.text.strip():
                    ph._element.getparent().remove(ph._element)
            except Exception:  # noqa: BLE001
                continue

    def _title_rect(self, slide, layout_prof: dict, sl: Slide) -> Optional[Rect]:
        try:
            for ph in slide.placeholders:
                if ph.placeholder_format.type in (1, 3):
                    return Rect(Emu(ph.left).inches, Emu(ph.top).inches,
                                Emu(ph.width).inches, Emu(ph.height).inches)
        except Exception:
            pass
        tp = layout_prof.get("title_ph")
        if tp:
            return Rect(tp["x"], tp["y"], tp["w"], tp["h"])
        return None

    def _draw_slide(self, slide, sl: Slide, layout: dict, items: list[dict]) -> None:
        self._draw_title(slide, sl, layout)
        for it in items:
            try:
                self._draw_item(slide, it)
            except Exception as e:
                log.warning("не удалось нарисовать виджет %s: %s", it.get("widget"), e)

    def _draw_title(self, slide, sl: Slide, layout: dict) -> None:
        rect = self._title_rect(slide, layout, sl)
        if rect is None:
            g = self.profile.get("grid", {})
            rect = Rect(g.get("body_x", 0.5), 0.35,
                        self.profile["slide_size"]["w_in"] - g.get("body_x", 0.5) - g.get("margin_right", 0.5),
                        0.75)
        dc = self.profile
        size = max(12.0, min(48.0, self._scale_pick("title", 28.0)))
        color = self._text_color()
        for ph in slide.placeholders:
            if ph.placeholder_format.type in (1, 3):
                self._style_placeholder(ph, sl.heading, size, color,
                                        headline_bold=True)
                return
        tb = slide.shapes.add_textbox(_in(rect.x), _in(rect.y), _in(rect.w), _in(rect.h))
        tf = tb.text_frame
        tf.word_wrap = True
        p = _add_para(tf, sl.heading, first=True)
        r = p.add_run()
        r.text = sl.heading
        self._set_run_font(r, self._headline_font(), size, True, color)
        if sl.subheading and sl.slide_type == SlideType.SECTION:
            sub = slide.shapes.add_textbox(_in(rect.x), _in(rect.y + rect.h * 0.9),
                                           _in(rect.w), _in(0.5))
            stf = sub.text_frame
            stf.word_wrap = True
            rr = stf.paragraphs[0].add_run()
            rr.text = sl.subheading
            self._set_run_font(rr, self._body_font(), max(11.0, size * 0.5), False,
                          self._text_color())

    def _style_placeholder(self, ph, text: str, size: float, color: str, headline_bold: bool):
        tf = ph.text_frame
        tf.clear()
        p = tf.paragraphs[0]
        r = p.add_run()
        r.text = text
        self._set_run_font(r, self._headline_font(), size, headline_bold, color)
        p.alignment = PP_ALIGN.LEFT

    # ------------------------------------------------------------ item drawing
    def _draw_item(self, slide, it: dict) -> None:
        widget = it["widget"]
        rect = Rect(**it["rect"])
        if widget == "bullets":
            self._draw_bullets(slide, it, rect)
        elif widget == "text":
            self._draw_text_block(slide, it, rect)
        elif widget == "paragraph":
            self._draw_paragraph(slide, it, rect)
        elif widget == "factoids":
            self._draw_factoids(slide, it, rect)
        elif widget == "table":
            self._draw_table(slide, it, rect)
        elif widget == "chart":
            self._draw_chart(slide, it, rect)
        elif widget == "quote":
            self._draw_quote(slide, it, rect)
        elif widget == "steps":
            self._draw_steps(slide, it, rect)
        elif widget == "image":
            self._draw_image(slide, it, rect)
        else:
            log.warning("неизвестный виджет %s", widget)

    # ---------------------------------------------------------------- helpers
    def _set_run_font(self, run, name: str, size: float, bold: bool,
                      color: str | None, italic: bool = False) -> None:
        """Единая точка установки шрифта run: кегль всегда из шкалы шаблона.

        Аудит проверяет, что кегль входит в типографическую шкалу шаблона
        (`font_size_not_in_scale`), поэтому производные размеры (например,
        подпись в 0.35 от крупного числа) приводятся к ближайшему значению шкалы.
        """
        _apply_run_font(run, name, self._snap_size(size), bold, color, italic)

    def _allowed_sizes(self) -> list[float]:
        scale = self.profile.get("type_scale", {}) or {}
        sizes = [float(x) for x in (scale.get("title") or []) + (scale.get("body") or [])]
        return sorted(set(sizes))

    def _snap_size(self, value: float) -> float:
        allowed = self._allowed_sizes()
        if not allowed:
            return round(float(value), 1)
        return min(allowed, key=lambda a: (abs(a - value), a))

    def _headline_font(self) -> str:
        return self.profile.get("headline_font") or self.profile.get("body_font") or "Arial"

    def _body_font(self) -> str:
        return self.profile.get("body_font") or self.profile.get("headline_font") or "Arial"

    def _scale_pick(self, kind: str, default: float) -> float:
        scale = self.profile.get("type_scale", {}).get(kind) or []
        cands = [x for x in scale if default - 2 <= x <= default + 8]
        if cands:
            return float(min(cands, key=lambda x: abs(x - default)))
        return self._snap_size(default)

    def _body_size(self, default: float = 16.0) -> float:
        scale = self.profile.get("type_scale", {}).get("body") or []
        cands = [x for x in scale if 10 <= x <= 20]
        if cands:
            return float(max(default, max(cands)))
        return self._snap_size(default)

    def _palette(self) -> list[str]:
        return [p.get("hex") for p in self.profile.get("palette", []) if p.get("hex")]

    def _text_color(self) -> str:
        from .images import brightness
        cands = list(self.profile.get("text_colors") or []) + self._palette()
        darks = [c for c in cands if c and brightness(c) < 0.45]
        if darks:
            return min(darks, key=brightness)
        return "#1A1A1A"

    def _accent_color(self) -> Optional[str]:
        pal = self._palette()
        for h in pal[4:]:
            if h and h.upper() not in ("#000000", "#FFFFFF", "#FFFFFFFF"):
                return h
        return pal[1] if len(pal) > 1 else None

    def _bg_color(self) -> str:
        pal = self._palette()
        for h in pal:
            if h and sum(hex_to_rgb(h)) > 600:
                return h
        return "#FFFFFF"

    def _accent_soft(self) -> str:
        bg = self._bg_color()
        ac = self._accent_color() or "#888888"
        return self._mix(ac, bg, 0.85)

    @staticmethod
    def _mix(a: str, b: str, t: float) -> str:
        ra, ga, ba = hex_to_rgb(a)
        rb, gb, bb = hex_to_rgb(b)
        return "#%02X%02X%02X" % (
            round(ra * (1 - t) + rb * t), round(ga * (1 - t) + gb * t), round(ba * (1 - t) + bb * t))

    def _fit_size(self, text: str, w: float, h: float, default: float,
                  min_size: float = 9.0) -> float:
        from ..layout.geometry import fit_font_size
        sizes = self.profile.get("type_scale", {}).get("body") or []
        candidates = [float(x) for x in sizes if float(x) >= min_size] or [default]
        sz, _ = fit_font_size(text, w, h, candidates, min_size=min_size)
        return self._snap_size(sz)

    # ----------------------------------------------------------------- bullets
    def _draw_bullets(self, slide, it: dict, rect: Rect) -> None:
        block: Block = it["block"]
        style = it.get("style", {})
        card = style.get("card", False)
        items = block.items or []
        pad = 0.16 if card else 0.04
        if card:
            self._draw_card_bg(slide, rect, style)
        inner = rect.padded(pad)
        if block.title and not card:
            inner = self._blk_title(slide, inner, block.title, style)
        text_color = style.get("text_color") or self._text_color()
        accent = style.get("accent") or self._accent_color()
        body = style.get("body") or self._body_font()
        # подбираем кегль, чтобы влезли все буллеты
        joined = "\n".join(items)
        size = self._fit_size(joined, inner.w, inner.h, default=16.0)
        tb = slide.shapes.add_textbox(_in(inner.x), _in(inner.y + 0.02),
                                      _in(inner.w), _in(inner.h))
        tf = tb.text_frame
        tf.word_wrap = True
        tf.vertical_anchor = MSO_ANCHOR.TOP
        first = True
        for item in items:
            p = _add_para(tf, item, first=first)
            first = False
            p.alignment = PP_ALIGN.LEFT
            p.space_before = Pt(2 if size < 14 else 4)
            p.space_after = Pt(2)
            p.line_spacing = 1.05
            self._set_bullet(p, accent, size)
            r = p.add_run()
            r.text = item
            self._set_run_font(r, body, size, False, text_color)

    @staticmethod
    def _set_bullet(p, accent: str, size: float):
        pPr = p._pPr if p._pPr is not None else p._p.get_or_add_pPr()
        pPr.set("marL", "182880")
        pPr.set("indent", "-182880")
        for tag in ("buNone", "buChar", "buAutoNum", "buClr", "buSzPct", "buFont"):
            el = pPr.find(qn(f"a:{tag}"))
            if el is not None:
                pPr.remove(el)
        buClr = pPr.makeelement(qn("a:buClr"), {})
        srgb = pPr.makeelement(qn("a:srgbClr"), {"val": (accent or "#888888").lstrip("#")})
        buClr.append(srgb)
        pPr.append(buClr)
        buFont = pPr.makeelement(qn("a:buFont"), {"typeface": "Arial"})
        pPr.append(buFont)
        buChar = pPr.makeelement(qn("a:buChar"), {"char": "▪"})
        pPr.append(buChar)

    def _blk_title(self, slide, rect: Rect, title: str, style: dict) -> Rect:
        size = self._scale_pick("title", 20.0)
        tb = slide.shapes.add_textbox(_in(rect.x), _in(rect.y), _in(rect.w), _in(0.4))
        tf = tb.text_frame
        tf.word_wrap = True
        r = tf.paragraphs[0].add_run()
        r.text = title
        self._set_run_font(r, self._headline_font(), size, True,
                      style.get("accent") or self._accent_color())
        return Rect(rect.x, rect.y + 0.42, rect.w, max(0.2, rect.h - 0.42))

    def _draw_card_bg(self, slide, rect: Rect, style: dict):
        shp = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE,
                                     _in(rect.x), _in(rect.y), _in(rect.w), _in(rect.h))
        shp.adjustments[0] = 0.06
        fill = shp.fill
        fill.solid()
        soft = style.get("accent_soft") or self._accent_soft()
        r, g, b = hex_to_rgb(soft)
        fill.fore_color.rgb = RGBColor(r, g, b)
        shp.line.color.rgb = _color(style.get("accent") or self._accent_color() or "#DDDDDD")
        shp.line.width = Pt(1)
        shp.shadow.inherit = False
        # акцентная полоска сверху
        bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE,
                                     _in(rect.x + 0.1), _in(rect.y + 0.1),
                                     _in(0.5), _in(0.045))
        bar.fill.solid()
        ac = _color(style.get("accent") or self._accent_color() or "#888888")
        bar.fill.fore_color.rgb = ac
        bar.line.fill.background()

    # ------------------------------------------------------------- text/paragraph
    def _draw_paragraph(self, slide, it: dict, rect: Rect) -> None:
        data = it.get("data", {})
        text = data.get("text") or ""
        style = it.get("style", {})
        tb = slide.shapes.add_textbox(_in(rect.x), _in(rect.y), _in(rect.w), _in(rect.h))
        tf = tb.text_frame
        tf.word_wrap = True
        tf.vertical_anchor = MSO_ANCHOR.TOP
        p = _add_para(tf, text, first=True)
        p.alignment = PP_ALIGN.LEFT
        r = p.add_run()
        r.text = text
        is_sub = data.get("is_sub", False)
        size = self._scale_pick("title", 20.0) * (0.55 if is_sub else 0.8)
        self._set_run_font(r, self._body_font(), max(11, size), False,
                      style.get("text_color") or self._text_color())

    def _draw_text_block(self, slide, it: dict, rect: Rect) -> None:
        block: Block = it["block"]
        style = it.get("style", {})
        card = style.get("card", False)
        pad = 0.16 if card else 0.05
        if card:
            self._draw_card_bg(slide, rect, style)
        inner = rect.padded(pad)
        if block.title and not card:
            inner = self._blk_title(slide, inner, block.title, style)
        text = block.text or ""
        tb = slide.shapes.add_textbox(_in(inner.x), _in(inner.y), _in(inner.w), _in(inner.h))
        tf = tb.text_frame
        tf.word_wrap = True
        size = self._fit_size(text, inner.w, inner.h, default=15.0)
        r = tf.paragraphs[0].add_run()
        r.text = text
        self._set_run_font(r, self._body_font(), size, False,
                      style.get("text_color") or self._text_color())

    # ---------------------------------------------------------------- factoids
    def _draw_factoids(self, slide, it: dict, rect: Rect) -> None:
        import math
        block: Block = it["block"]
        style = it.get("style", {})
        items = block.factoids or []
        n = len(items)
        if not n:
            return
        cols = 2 if n > 2 else 1
        rows = math.ceil(n / cols)
        gap = 0.2
        card = style.get("card", False)
        cell_w = (rect.w - gap * (cols - 1)) / cols
        cell_h = rect.h / rows
        big = self._scale_pick("title", 34.0) * (0.55 if card else 0.9)
        accent = style.get("accent") or self._accent_color() or self._text_color()
        label_c = style.get("text_color") or self._text_color()
        for i, fd in enumerate(items):
            value = fd.get("value", "")
            label = fd.get("label", "")
            x = rect.x + (i % cols) * (cell_w + gap)
            y = rect.y + (i // cols) * cell_h
            cell = Rect(x + 0.08, y + 0.06, cell_w - 0.16, cell_h - 0.12)
            if card:
                self._draw_card_bg(slide, cell, style)
                cell = cell.padded(0.14)
            tb = slide.shapes.add_textbox(_in(cell.x), _in(cell.y),
                                          _in(cell.w), _in(cell.h * 0.5))
            tf = tb.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            r = p.add_run()
            r.text = value
            self._set_run_font(r, self._headline_font(), big, True, accent)
            if label:
                lb = slide.shapes.add_textbox(_in(cell.x), _in(cell.y + cell.h * 0.5),
                                              _in(cell.w), _in(cell.h * 0.5))
                ltf = lb.text_frame
                ltf.word_wrap = True
                lr = ltf.paragraphs[0].add_run()
                lr.text = label
                self._set_run_font(lr, self._body_font(), max(11.0, big * 0.35), False, label_c)

    # ------------------------------------------------------------------- table
    def _draw_table(self, slide, it: dict, rect: Rect) -> None:
        block: Block = it["block"]
        style = it.get("style", {})
        tbl = block.table
        if tbl is None:
            return
        n_rows = len(tbl.rows) + 1
        n_cols = len(tbl.header)
        col_w = rect.w / n_cols
        row_h = min(0.42, rect.h / n_rows)
        gf = slide.shapes.add_table(n_rows, n_cols, _in(rect.x), _in(rect.y),
                                    _in(rect.w), _in(max(0.4, row_h * n_rows)))
        table = gf.table
        # запрещаем авто-стиль (флаг на graphicFrame)
        self._strip_table_style(gf)
        table.first_row = False
        table.horz_banding = False
        for c in range(n_cols):
            table.columns[c].width = Inches(col_w)
        for r in range(n_rows):
            table.rows[r].height = Inches(row_h)
        accent = style.get("accent") or self._accent_color() or "#444444"
        soft = style.get("accent_soft") or self._accent_soft()
        body = style.get("body") or self._body_font()
        dark = style.get("text_color") or self._text_color()
        for c in range(n_cols):
            self._cell(table.cell(0, c), tbl.header[c], accent,
                       self._body_font(), self._header_font_size(), light_text=True)
        for ri, row in enumerate(tbl.rows):
            zebra = (ri % 2 == 1)
            for c in range(min(len(row), n_cols)):
                bg = soft if zebra else None
                self._cell(table.cell(ri + 1, c), row[c], dark, body,
                           self._cell_font_size(row[c], col_w), light_text=False, bg=bg)

    @staticmethod
    def _strip_table_style(gf):
        try:
            tbl_el = gf._element.graphic.graphicData.tbl
            tblPr = tbl_el.tblPr
            tblPr.set("firstRow", "0")
            tblPr.set("bandRow", "0")
            tblPr.remove(tblPr.find(qn("a:tableStyleId")))
            sid = tblPr.makeelement(qn("a:tableStyleId"), {})
            sid.text = "{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}"
            tblPr.append(sid)
        except Exception:
            pass

    def _header_font_size(self) -> float:
        return self._snap_size(max(14.0, self._scale_pick("body", 14.0)))

    def _cell_font_size(self, text: str, w: float) -> float:
        """Кегль ячейки таблицы: минимальный из шкалы, пригодный для текста.

        Значения вне шкалы запрещены (аудит `font_size_not_in_scale`), поэтому
        вместо «13 по умолчанию» берётся ближайший меньший размер шкалы.
        """
        scale = sorted(float(x) for x in
                       (self.profile.get("type_scale", {}).get("body") or []))
        small = [x for x in scale if 11.0 <= x <= 16.0]
        if small:
            return max(small)
        if scale:
            return scale[0]
        return 12.0

    def _cell(self, cell, text: str, fg, font, size, light_text: bool, bg=None):
        cell.margin_left = cell.margin_right = Inches(0.08)
        cell.margin_top = cell.margin_bottom = Inches(0.03)
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        tf = cell.text_frame
        tf.word_wrap = True
        tf.clear()
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.LEFT
        r = p.add_run()
        r.text = text
        if light_text:
            self._set_run_font(r, font, size, True, "#FFFFFF")
        else:
            self._set_run_font(r, font, size, False, fg)
        fill_hex = (bg or "#FFFFFF") if not light_text else fg
        cell.fill.solid()
        r_, g_, b_ = hex_to_rgb(fill_hex)
        cell.fill.fore_color.rgb = RGBColor(r_, g_, b_)

    # ------------------------------------------------------------------- chart
    def _draw_chart(self, slide, it: dict, rect: Rect) -> None:
        block: Block = it["block"]
        style = it.get("style", {})
        chart: Chart = block.chart
        if chart is None:
            return
        ctype = CHART_MAP.get(chart.type, XL_CHART_TYPE.COLUMN_CLUSTERED)
        data = CategoryChartData()
        data.categories = chart.categories
        for s in chart.series:
            data.add_series(s.name, s.values)
        gf = slide.shapes.add_chart(ctype, _in(rect.x), _in(rect.y),
                                    _in(rect.w), _in(rect.h), data)
        ch = gf.chart
        is_pie = chart.type in (ChartType.PIE, ChartType.DONUT)
        ch.has_legend = (len(chart.series) > 1) and not is_pie
        if ch.has_legend:
            ch.legend.position = XL_LEGEND_POSITION.BOTTOM
            ch.legend.include_in_layout = False
            self._style_chart_text(ch.legend, size=self._chart_font_size())
        try:
            ch.has_title = False
        except Exception:
            pass
        self._style_chart_data(ch, chart, is_pie)
        self._style_chart_axes(ch, chart, is_pie)
        # цвета серий из палитры
        pal = self._palette()[4:] or ["#4472C4", "#ED7D31", "#A5A5A5", "#FFC000", "#70AD47"]
        try:
            plots = list(ch.plots)
            for si, series in enumerate(plots[0].series):
                h = pal[si % len(pal)]
                series_el = series._element
                self._series_fill(series_el, h)
        except Exception:
            pass
        self._chart_no_auto_shrink(ch)

    @staticmethod
    def _series_fill(series_el, h: str):
        spPr = series_el.find(qn("c:spPr"))
        if spPr is None:
            return
        solid = spPr.find(qn("a:solidFill"))
        if solid is not None:
            spPr.remove(solid)
        solid = spPr.makeelement(qn("a:solidFill"), {})
        srgb = spPr.makeelement(qn("a:srgbClr"), {"val": h.lstrip("#")})
        solid.append(srgb)
        spPr.append(solid)

    @staticmethod
    def _chart_no_auto_shrink(ch):
        try:
            el = ch._chartSpace
            if el is not None:
                pass
        except Exception:
            pass

    def _chart_font_size(self) -> float:
        """Кегль подписей диаграммы: из шкалы шаблона (аудит проверяет кегли)."""
        return self._snap_size(min(self._body_size(12.0), 14.0))

    def _style_chart_text(self, obj, size: float) -> None:
        """Шрифт, кегль и цвет текста объекта диаграммы (легенда, подписи, оси)."""
        try:
            font = obj.font
            font.size = Pt(size)
            font.name = self._body_font()
            color = _color(self._text_color())
            if color is not None:
                font.color.rgb = color
        except Exception:  # noqa: BLE001 — не всякий объект поддерживает шрифт
            pass

    def _style_chart_data(self, ch, chart: Chart, is_pie: bool) -> None:
        """Подписи данных: без них диаграмма нечитаема (проверка chart_unlabeled)."""
        if chart.labels is False:
            return
        try:
            plot = ch.plots[0]
            plot.has_data_labels = True
            labels = plot.data_labels
            labels.number_format_is_linked = False
            labels.number_format = '0"%"' if chart.unit == "%" else "General"
            if is_pie:
                labels.show_percentage = True
                labels.show_value = False
                labels.show_category_name = True
            self._style_chart_text(labels, self._chart_font_size())
        except Exception as exc:  # noqa: BLE001
            log.warning("не удалось включить подписи данных: %s", exc)

    def _style_chart_axes(self, ch, chart: Chart, is_pie: bool) -> None:
        """Единицы измерения: заголовок оси значений + формат подписей категорий."""
        if is_pie:
            return
        try:
            value_axis = ch.value_axis
            if chart.unit:
                value_axis.has_title = True
                value_axis.axis_title.text_frame.text = f"Значение, {chart.unit}"
                self._style_chart_text(value_axis.axis_title, self._chart_font_size())
            self._style_chart_text(value_axis.tick_labels, self._chart_font_size())
            self._style_chart_text(ch.category_axis.tick_labels, self._chart_font_size())
        except Exception as exc:  # noqa: BLE001
            log.warning("не удалось оформить оси диаграммы: %s", exc)

    # -------------------------------------------------------------------- quote
    def _draw_quote(self, slide, it: dict, rect: Rect) -> None:
        block: Block = it["block"]
        style = it.get("style", {})
        accent = style.get("accent") or self._accent_color() or "#888888"
        bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE,
                                     _in(rect.x), _in(rect.y), _in(0.09), _in(rect.h))
        bar.fill.solid()
        bar.fill.fore_color.rgb = _color(accent)
        bar.line.fill.background()
        inner = rect.sub(0.25, 0.05, rect.w - 0.3, rect.h - 0.1)
        text = block.quote_text or ""
        author = block.quote_author or ""
        tb = slide.shapes.add_textbox(_in(inner.x), _in(inner.y), _in(inner.w), _in(inner.h))
        tf = tb.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        r = p.add_run()
        r.text = "«" + text + "»"
        self._set_run_font(r, self._headline_font(),
                      self._fit_size(text, inner.w, inner.h * 0.8, default=20.0),
                      False, style.get("text_color") or self._text_color(), italic=True)
        if author:
            ap = tf.add_paragraph()
            ap.alignment = PP_ALIGN.LEFT
            ar = ap.add_run()
            ar.text = "— " + author
            self._set_run_font(ar, self._body_font(), 14, True, accent)

    # -------------------------------------------------------------------- steps
    def _draw_steps(self, slide, it: dict, rect: Rect) -> None:
        """Шаги-процесс нативными автофигурами (шевроны) — читаемый SmartArt-аналог."""
        block: Block = it["block"]
        style = it.get("style", {})
        items = (block.items or [])[:6]
        n = len(items)
        if not n:
            return
        accent = style.get("accent") or self._accent_color() or "#888888"
        gap = 0.12
        step_w = (rect.w - gap * (n - 1) - 0.8) / n
        step_h = rect.h * 0.55
        start_x = rect.x + 0.0
        soft = style.get("accent_soft") or self._accent_soft()
        for i, txt in enumerate(items):
            x = start_x + i * (step_w + gap) + (0.28 if i else 0.0)
            if n == 1:
                x = rect.x + 0.28
            shp = slide.shapes.add_shape(MSO_SHAPE.CHEVRON, _in(x), _in(rect.y + rect.h * 0.1),
                                         _in(step_w + 0.28), _in(step_h))
            shp.adjustments[0] = 0.28
            shp.fill.solid()
            rr, gg, bb = hex_to_rgb(soft if i % 2 else accent)
            shp.fill.fore_color.rgb = RGBColor(rr, gg, bb)
            shp.line.color.rgb = _color(accent)
            shp.line.width = Pt(0.75)
            shp.shadow.inherit = False
            tf = shp.text_frame
            tf.word_wrap = True
            tf.vertical_anchor = MSO_ANCHOR.MIDDLE
            tf.margin_left = Inches(0.3 if i == 0 else 0.28)
            p = tf.paragraphs[0]
            p.alignment = PP_ALIGN.CENTER
            r = p.add_run()
            r.text = txt
            white = i % 2 == 0
            size = self._fit_size(txt, step_w - 0.2, step_h * 0.8, default=14.0)
            if size < 14:
                size = 14.0
            self._set_run_font(r, self._body_font(), size,
                          True, "#FFFFFF" if white else self._text_color())

    # -------------------------------------------------------------------- image
    def _draw_image(self, slide, it: dict, rect: Rect) -> None:
        """Нативная картинка, вписанная в слот с сохранением пропорций.

        Если байтов нет (нет в контент-пакете, text-to-image выключен) — остаётся
        нативный слот с подписью; аудит отмечает его как `image_missing`.
        Растяжение запрещено: масштаб единый по обеим осям.
        """
        block: Block = it["block"]
        style = it.get("style", {})
        data = self._resolve_image(block)
        if not data:
            log.warning("нет изображения для блока «%s» (image_ref=%s) — оставлен слот",
                        block.image_caption or block.title or "image", block.image_ref)
            self._draw_image_slot(slide, rect, block, style)
            return
        cap_h = 0.34 if block.image_caption else 0.0
        area = Rect(rect.x, rect.y, rect.w, max(0.4, rect.h - cap_h))
        box = self._contain(area, data)
        if box is None:
            self._draw_image_slot(slide, rect, block, style)
            return
        pic = slide.shapes.add_picture(io.BytesIO(data), _in(box.x), _in(box.y),
                                       _in(box.w), _in(box.h))
        pic.name = "image"
        if block.image_caption:
            tb = slide.shapes.add_textbox(_in(rect.x), _in(box.bottom + 0.04),
                                          _in(rect.w), _in(cap_h))
            tf = tb.text_frame
            tf.word_wrap = True
            run = tf.paragraphs[0].add_run()
            run.text = block.image_caption
            self._set_run_font(run, self._body_font(), 11.0, False,
                          style.get("text_color") or self._text_color())

    def _resolve_image(self, block: Block) -> Optional[bytes]:
        """Ищет байты изображения в реестре контент-пакета по ключам блока."""
        for key in (block.image_ref, block.image_caption, block.title):
            if key and key in self.images:
                return self.images[key]
        return None

    @staticmethod
    def _contain(area: Rect, data: bytes) -> Optional[Rect]:
        """Вписывает изображение в область с сохранением пропорций (contain)."""
        try:
            from PIL import Image

            with Image.open(io.BytesIO(data)) as img:
                iw, ih = img.size
        except Exception as exc:  # noqa: BLE001
            log.warning("не удалось прочитать изображение: %s", exc)
            return None
        if not iw or not ih:
            return None
        ratio = iw / ih
        w, h = area.w, area.w / ratio
        if h > area.h:
            h, w = area.h, area.h * ratio
        return Rect(area.x + (area.w - w) / 2, area.y + (area.h - h) / 2, w, h)

    def _draw_image_slot(self, slide, rect: Rect, block: Block, style: dict) -> None:
        """Нативный слот вместо картинки: рамка в токенах шаблона и подпись."""
        shp = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE,
                                     _in(rect.x), _in(rect.y), _in(rect.w), _in(rect.h))
        shp.name = IMAGE_SLOT_NAME
        shp.adjustments[0] = 0.04
        shp.fill.solid()
        r, g, b = hex_to_rgb(style.get("accent_soft") or self._accent_soft())
        shp.fill.fore_color.rgb = RGBColor(r, g, b)
        shp.line.color.rgb = _color(style.get("accent") or self._accent_color() or "#CCCCCC")
        shp.line.width = Pt(1)
        shp.shadow.inherit = False
        caption = block.image_caption or block.image_prompt or ""
        if caption:
            tf = shp.text_frame
            tf.word_wrap = True
            tf.vertical_anchor = MSO_ANCHOR.MIDDLE
            p = tf.paragraphs[0]
            p.alignment = PP_ALIGN.CENTER
            run = p.add_run()
            run.text = caption[:120]
            self._set_run_font(run, self._body_font(), 12.0, False,
                          style.get("text_color") or self._text_color())