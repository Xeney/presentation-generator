"""Геометрические утилиты: прямоугольники, оценка размера текста, выбор кегля."""
from __future__ import annotations

import math


class Rect:
    """Прямоугольник в дюймах. Объект неизменяемый (immutable)."""

    __slots__ = ("x", "y", "w", "h")

    def __init__(self, x: float, y: float, w: float, h: float):
        self.x = float(x)
        self.y = float(y)
        self.w = float(w)
        self.h = float(h)

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    def padded(self, pad: float) -> "Rect":
        return Rect(self.x + pad, self.y + pad, self.w - 2 * pad, self.h - 2 * pad)

    def sub(self, x: float, y: float, w: float, h: float) -> "Rect":
        """Подобласть относительно левого верхнего угла текущей."""
        return Rect(self.x + x, self.y + y, w, h)

    def row(self, y: float, h: float) -> "Rect":
        return Rect(self.x, y, self.w, h)

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}

    def __repr__(self) -> str:  # pragma: no cover
        return f"Rect({self.x:.2f},{self.y:.2f},{self.w:.2f}x{self.h:.2f})"


CHAR_FACTOR = 0.56   # средняя ширина символа кириллицы (с запасом на полужирный)
LINE_FACTOR = 1.32   # межстрочный интервал относительно кегля


def lines_needed(text: str, w_in: float, size_pt: float, bullet_indent: float = 0.0) -> int:
    """Сколько строк займёт текст при заданной ширине и кегле."""
    if not text:
        return 0
    usable = max(0.05, w_in - bullet_indent)
    cpl = max(1, int((usable * 72.0) / (CHAR_FACTOR * size_pt)))
    n_newlines = text.count("\n")
    text = text.replace("\n", " ")
    words = text.split()
    if not words:
        return n_newlines + 1
    lines, cur = 0, 0
    for w in words:
        wl = len(w) + 1
        if wl > cpl:
            # слово длиннее строки: PowerPoint переносит его на несколько строк,
            # а не «сжимает» в одну. Без этого «подразделений» считалось одной
            # строкой, кегль выбирался втрое больше нужного и текст вылезал
            # из карточки KPI
            if cur:
                lines += 1
                cur = 0
            lines += math.ceil(wl / cpl)
        elif cur + wl > cpl:
            lines += 1
            cur = wl
        else:
            cur += wl
    if cur:
        lines += 1
    return lines + n_newlines


def text_height_in(text: str, w_in: float, size_pt: float, bullet_indent: float = 0.0) -> float:
    lines = lines_needed(text, w_in, size_pt, bullet_indent)
    return lines * size_pt * LINE_FACTOR / 72.0


def fit_font_size(text: str, w_in: float, h_in: float,
                  size_from: list[float], bullet_indent: float = 0.0,
                  min_size: float = 10.0) -> tuple[float, float]:
    """Подбирает максимальный кегль из шкалы, при котором текст влезает.

    Возвращает (size_pt, height_in).
    """
    cand = sorted((s for s in (size_from or []) if s >= min_size), reverse=True)
    if not cand:
        cand = [min_size]
    for s in cand:
        h = text_height_in(text, w_in, s, bullet_indent)
        if h <= h_in + 1e-6:
            return s, h
    s = min(cand)
    return s, text_height_in(text, w_in, s, bullet_indent)


def nearest(value: float, allowed: list[float], floor: float | None = None) -> float:
    """Ближайшее значение из шкалы (round-half-... с выбором вниз при равенстве)."""
    if not allowed:
        return value
    best, bd = None, None
    for a in allowed:
        d = abs(a - value)
        if bd is None or d < bd:
            best, bd = a, d
    if floor is not None:
        best = max(best, floor)
    return best


def distribute_height(items_h: list[float], total_h: float, weights: list[float] | None = None) -> list[float]:
    """Распределяет лишнюю высоту пропорционально содержимому."""
    total = sum(items_h)
    if total <= 0:
        return [0.0] * len(items_h)
    extra = max(0.0, total_h - total)
    wsum = sum(weights or items_h)
    out = []
    for i, h in enumerate(items_h):
        add = extra * ((weights or items_h)[i] / wsum) if wsum else 0.0
        out.append(h + add)
    return out


def row_cells(rect: Rect, n: int, gap: float) -> list[Rect]:
    """n колонок одинаковой ширины внутри rect с зазором gap."""
    if n <= 1:
        return [rect]
    w = (rect.w - gap * (n - 1)) / n
    return [Rect(rect.x + i * (w + gap), rect.y, w, rect.h) for i in range(n)]


def grid_cells(rect: Rect, cols: int, rows: int, gap: float) -> list[Rect]:
    """Сетка cols x rows с зазором."""
    w = (rect.w - gap * (cols - 1)) / cols
    h = (rect.h - gap * (rows - 1)) / rows
    out = []
    for r in range(rows):
        for c in range(cols):
            out.append(Rect(rect.x + c * (w + gap), rect.y + r * (h + gap), w, h))
    return out