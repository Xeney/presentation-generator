"""Вёрстка: превращает блоки слайда в позиционированные виджеты для рендера.

Три варианта одной вёрстки различаются по осям:
  0 compact — плотность: полосы во всю ширину, компактные отступы, единый поток;
  1 cards   — группировка: контент раскладывается в карточки сеткой 2xN;
  2 split   — порядок и визуализация: текстовые и данные-блоки разведены в
              колонки, таблицы/диаграммы переставляются выше и меняют тип.

Все три варианта используют только разрешённые токены шаблона (палитра,
шрифты, типографическая шкала) и генерируют одинаково валидные слайды.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..models.deck import Block, Slide, SlideType
from .geometry import Rect, distribute_height, fit_font_size, grid_cells, nearest, row_cells

log = logging.getLogger("layout")

VARIANTS = {
    "compact": {
        "mode": "stack", "gap": 0.16, "padding": 0.10, "inner": 0.10,
        "bullets_inline": True, "columns_limit": 2, "bold_titles": True,
        "desc": "плотность: компактные полосы, минимум воздуха",
    },
    "cards": {
        "mode": "cards", "gap": 0.22, "padding": 0.18, "inner": 0.16,
        "bullets_inline": False, "columns_limit": 3, "bold_titles": True,
        "desc": "группировка: контент в карточках-сетках",
    },
    "split": {
        "mode": "split", "gap": 0.3, "padding": 0.1, "inner": 0.12,
        "bullets_inline": True, "columns_limit": 2, "bold_titles": True,
        "desc": "порядок и визуализация: текст и данные по колонкам",
    },
}


def _html_lum(hex_color: str) -> float:
    h = (hex_color or "#000000").lstrip("#")
    if len(h) != 6:
        return 0.05
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _saturation(hex_color: str) -> float:
    h = (hex_color or "#000000").lstrip("#")
    if len(h) != 6:
        return 0.0
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    mx, mn = max(r, g, b), min(r, g, b)
    if mx == 0:
        return 0.0
    return (mx - mn) / mx


def _mix(hex_a: str, hex_b: str, t: float) -> str:
    def ch(h):
        h = h.lstrip("#")
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    a, b = ch(hex_a), ch(hex_b)
    m = tuple(round(a[i] * (1 - t) + b[i] * t) for i in range(3))
    return "#%02X%02X%02X" % m


@dataclass
class DesignContext:
    """Токены дизайна, извлечённые из профиля шаблона."""

    fonts: dict = field(default_factory=lambda: {"headline": None, "body": None})
    palette: list = field(default_factory=list)
    type_scale: dict = field(default_factory=dict)
    slide_w: float = 13.33
    slide_h: float = 7.5

    def __post_init__(self):
        pal = [p.get("hex") for p in self.palette if p.get("hex")]
        self._pal = pal
        self.text_color = self._first_text()
        self.bg_color = self._first_light()
        self.accent = self._first_accent()
        self.accent_soft = _mix(self.accent, self.bg_color, 0.86) if self.accent else "#E8E8E8"

    def _hexes(self) -> list[str]:
        return self._pal

    def _is_dark(self, h: str) -> bool:
        return _html_lum(h) < 0.55

    def _first_text(self) -> str:
        darks = [h for h in self._hexes() if self._is_dark(h)]
        return darks[0] if darks else "#1A1A1A"

    def _first_light(self) -> str:
        lights = [h for h in self._hexes() if not self._is_dark(h)]
        if lights:
            return max(lights, key=_html_lum)
        return "#FFFFFF"

    def _first_accent(self) -> str | None:
        chroma = [h for h in self._hexes() if self._is_dark(h) and _saturation(h) > 0.15]
        if chroma:
            return max(chroma, key=_saturation)
        return None

    def slide_title_size(self) -> float:
        ts = self.type_scale.get("title") or []
        for v in ts:
            if v <= 40:
                return float(v)
        return 28.0

    def block_title_size(self) -> float:
        ts = self.type_scale.get("title") or []
        for v in ts:
            if v <= 24:
                return max(16.0, float(v))
        return 18.0

    def body_sizes(self) -> list[float]:
        return [float(v) for v in self.type_scale.get("body") or [] if 10 <= float(v) <= 24] or [14.0]

    def font(self, headline: bool) -> str:
        stock = "Arial"
        if headline:
            return self.fonts.get("headline") or self.fonts.get("body") or stock
        return self.fonts.get("body") or self.fonts.get("headline") or stock

    def fit(self, text: str, w: float, h: float, default: float = 16.0,
            min_size: float = 10.0) -> float:
        sz, _ = fit_font_size(text, w, h, self.body_sizes() + [default], min_size=min_size)
        return sz


def _is_data_block(b: Block) -> bool:
    return b.kind in ("table", "chart", "factoids")


def _is_text_block(b: Block) -> bool:
    return b.kind in ("bullets", "text", "quote", "steps", "columns", "image")


def _block_min_h(b: Block, dc: DesignContext, w_in: float) -> float:
    """Минимальная высота блока при ширине w_in по его содержимому."""
    gap = 0.16
    if b.kind == "bullets":
        items = b.items or []
        n = len(items)
        joined = "\n".join(items)
        h = text_height(joined, max(1.0, w_in)) + n * 0.15
        return max(gap + 0.45, h)
    if b.kind == "text":
        return text_height(b.text or "", max(1.0, w_in)) + gap
    if b.kind == "factoids":
        rows = (len(b.factoids) + 1) // 2
        return rows * 0.7 + gap
    if b.kind == "chart":
        return 2.4 + gap
    if b.kind == "table":
        n = len(b.table.rows) + 1 if b.table else 2
        return n * 0.42 + gap
    if b.kind == "quote":
        return 1.6 + gap
    if b.kind == "steps":
        return 1.1 + gap
    return gap + 0.2


def text_height(text: str, w: float) -> float:
    return text_height_in_fallback(text, w, 16)


def text_height_in_fallback(text: str, w: float, size: float) -> float:
    from .geometry import text_height_in
    return text_height_in(text, w, size)


def _sum_words(items: list[str]) -> int:
    return sum(len(it.split()) for it in items)


class LayoutEngine:
    """Композитор: slide + variant --> план виджетов (items)."""

    def __init__(self, dc: DesignContext, variant: str = "compact"):
        if variant not in VARIANTS:
            raise KeyError(f"неизвестный вариант {variant}")
        self.dc = dc
        self.variant = variant
        self.cfg = VARIANTS[variant]
        self.part = compact if variant == "compact" else cards if variant == "cards" else split

    def compose(self, slide: Slide, canvas: Rect) -> list[dict]:
        blocks = self._prepare_blocks(slide)
        plan = []
        if slide.slide_type in (SlideType.TITLE, SlideType.SECTION, SlideType.AGENDA,
                                SlideType.FINAL):
            plan = self._compose_featured(slide, canvas, blocks)
        else:
            plan = self.part(self, slide, blocks, canvas)
        return plan

    # --------------------------------------------------------- подготовка блоков
    def _prepare_blocks(self, slide: Slide) -> list[Block]:
        blocks = [b for b in slide.blocks if not self._empty(b)]
        if self.variant == "split":
            data = [b for b in blocks if _is_data_block(b)]
            text = [b for b in blocks if not _is_data_block(b)]
            # порядок: данные выше, если их >= 1, иначе сохраняем исходный
            return data + text if (data and text) else blocks
        return blocks

    @staticmethod
    def _empty(b: Block) -> bool:
        if b.kind == "bullets":
            return not b.items
        if b.kind == "text":
            return not b.text
        if b.kind == "factoids":
            return not b.factoids
        if b.kind == "chart":
            return b.chart is None or not b.chart.series
        if b.kind == "table":
            return b.table is None or not b.table.rows
        if b.kind == "quote":
            return not b.quote_text
        if b.kind == "steps":
            return not b.items
        return False

    # ------------------------------------------------------ фоновые/витринные
    def _compose_featured(self, slide: Slide, canvas: Rect, blocks: list[Block]) -> list[dict]:
        """Титул/раздел/оглавление/финал: автоконтент только если блоков нет."""
        items = []
        if slide.slide_type == SlideType.TITLE:
            if not blocks:
                sub = slide.subheading or ""
                items.append(self._item("paragraph", canvas.to_dict(),
                                        {"text": sub, "is_sub": True},
                                        slide_type=str(slide.slide_type.value)))
        elif slide.slide_type == SlideType.FINAL:
            if not blocks:
                items.append(self._item("paragraph", canvas.to_dict(),
                                        {"text": "Спасибо за внимание", "is_sub": False},
                                        slide_type="final"))
        use = canvas.to_dict()
        for b in blocks[:2]:
            items.append(self._widget_item(b, use, canvas))
        return items

    def _widget_item(self, b: Block, rect: dict, canvas: Rect, *, card: bool = False) -> dict:
        style = {
            "font": self.dc.font(headline=True),
            "text_color": self.dc.text_color,
            "accent": self.dc.accent,
            "bg": self.dc.bg_color,
            "card": card,
            "accent_soft": self.dc.accent_soft,
            "block_title_size": self.dc.block_title_size(),
            "body": self.dc.font(headline=False),
        }
        return {"widget": b.kind, "rect": rect, "block": b, "style": style}

    def _item(self, widget: str, rect: dict, data: dict, *, slide_type: str = "content",
              style: dict | None = None) -> dict:
        return {"widget": widget, "rect": rect, "data": data, "style": style or {},
                "slide_type": slide_type}


# ---------------------------------------------------------------- полосы (compact)
def compact(engine: LayoutEngine, slide: Slide, blocks: list[Block], canvas: Rect) -> list[dict]:
    dc, cfg = engine.dc, engine.cfg
    gap = cfg["gap"]
    inner = canvas.padded(cfg["padding"] * 0.8)
    n_blocks = len(blocks)
    items = []
    if n_blocks == 0:
        return items

    # сохраняем порядок (variant compact не переставляет) и помещаемся в канву
    heights = [_block_min_h(b, dc, inner.w) for b in blocks]
    total_needed = sum(heights) + gap * (n_blocks - 1)
    avail = max(0.5, inner.h)
    scale = min(1.0, avail / total_needed) if total_needed > 0 else 1.0
    heights = [h * scale for h in heights]
    y = inner.y
    for bp, h in zip(blocks, heights):
        rect = Rect(inner.x, y, inner.w, max(0.3, h))
        items.append(engine._widget_item(bp, rect.to_dict(), canvas))
        y += max(0.3, h) + gap
    return items


# ------------------------------------------------------------------- карточки
def cards(engine: LayoutEngine, slide: Slide, blocks: list[Block], canvas: Rect) -> list[dict]:
    dc, cfg = engine.dc, engine.cfg
    n = len(blocks)
    if n == 0:
        return []
    cols = min(cfg["columns_limit"], n)
    if n == 1:
        cols = 1
    rows_t = max(1, math_ceil(n / cols))
    gap = cfg["gap"]
    inner = canvas.padded(0.05)
    cells = grid_cells(inner, cols, rows_t, gap)
    items = []
    for i, b in enumerate(blocks):
        items.append(engine._widget_item(b, cells[i].to_dict(), canvas, card=True))
    return items


def math_ceil(x: float) -> int:
    from math import ceil
    return ceil(x)


# ---------------------------------------------------------------------- сплит
def split(engine: LayoutEngine, slide: Slide, blocks: list[Block], canvas: Rect) -> list[dict]:
    dc, cfg = engine.dc, engine.cfg
    data_blocks = [b for b in blocks if _is_data_block(b)]
    text_blocks = [b for b in blocks if not _is_data_block(b)]
    gap = cfg["gap"]
    inner = canvas.padded(cfg["padding"] * 0.8)
    items = []
    if data_blocks and text_blocks:
        left_w = inner.w * 0.5
        right_w = inner.w - left_w - gap
        left = Rect(inner.x, inner.y, left_w, inner.h)
        right = Rect(inner.x + left_w + gap, inner.y, right_w, inner.h)

        def _stack(box: Rect, parts: list[Block], min_h: float) -> list[dict]:
            hsum = sum(_block_min_h(b, dc, box.w) for b in parts) + 0.12 * max(0, len(parts) - 1)
            sc = min(1.0, box.h / hsum) if hsum > 0 else 1.0
            inner_items, yy = [], box.y
            for b in parts:
                h = max(min_h, _block_min_h(b, dc, box.w) * sc)
                inner_items.append(engine._widget_item(b, Rect(box.x, yy, box.w, min(box.bottom - yy, h)).to_dict(), canvas))
                yy += h + 0.12
            return inner_items

        items += _stack(left, text_blocks, 0.4)
        items += _stack(right, data_blocks, 0.6)
        return items
    return compact(engine, slide, blocks, canvas)