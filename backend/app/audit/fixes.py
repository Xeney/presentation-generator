"""Детерминированные авто-фиксы выбранных проблем (без LLM).

Пользователь отмечает проблемы в UI, слой фиксов применяет к колоде (Deck)
обратимые и объяснимые преобразования, после чего колода рендерится заново.
Каждый фикс возвращает, что именно он сделал; проблемы, которые нельзя
исправить на уровне контента (слайд-картинка, чужая гарнитура, низкий контраст
шаблона), честно пропускаются с причиной, а не «делают вид», что исправлены.

Фиксы сознательно не ходят в LLM: результат должен быть предсказуемым и
воспроизводимым (ADR-007).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable, Optional

from ..models.deck import Block, Chart, ChartType, Deck, Slide, SlideType, Table

log = logging.getLogger("fixes")

MIN_SLIDES = 4
MAX_SLIDES = 15
MAX_BLOCKS = 6
MAX_BULLETS = 6
MAX_BULLET_WORDS = 15
MAX_TABLE_ROWS = 6          # без шапки: вместе с шапкой получается 7
MAX_TABLE_COLS = 5

NUMBER_CELL = re.compile(r"^-?\d+(?:[.,]\d+)?\s*%?$")


@dataclass
class FixOutcome:
    """Результат применения одного фикса."""

    issue_id: str
    code: str
    slide: int
    status: str                  # applied | skipped
    action: str = ""
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "issue_id": self.issue_id, "code": self.code, "slide": self.slide,
            "status": self.status, "action": self.action, "detail": self.detail,
        }


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _slide_fingerprint(slide: Slide) -> str:
    parts = [slide.heading]
    for block in slide.blocks:
        parts.extend(block.items or [])
        parts.extend([block.text or "", block.quote_text or "", block.title or ""])
        if block.table is not None:
            parts.extend(block.table.header)
            parts.extend(cell for row in block.table.rows for cell in row)
    return _norm(" ".join(parts))


def split_sentence(text: str, limit: int = MAX_BULLET_WORDS) -> list[str]:
    """Делит длинную фразу на осмысленные части по знакам и союзам."""
    words = text.split()
    if len(words) <= limit:
        return [text]
    for delimiter in (";", " — ", ", ", " и ", " а "):
        if delimiter in text:
            left, _, right = text.partition(delimiter)
            if 2 <= len(left.split()) <= limit and 2 <= len(right.split()) <= limit:
                return [left.strip(" ,;"), right.strip(" ,;")]
    middle = len(words) // 2
    return [" ".join(words[:middle]), " ".join(words[middle:])]


def table_to_chart(table: Table) -> Optional[Chart]:
    """Таблица с числовыми колонками → столбчатая диаграмма (первый столбец — категории)."""
    if not table.rows or len(table.header) < 2 or len(table.header) > MAX_TABLE_COLS:
        return None
    numeric_columns = []
    for column in range(1, len(table.header)):
        values = []
        for row in table.rows:
            if column >= len(row) or not NUMBER_CELL.match(row[column].replace("\u00a0", "")):
                values = []
                break
            values.append(float(row[column].replace(",", ".").replace("%", "").strip()))
        if values:
            numeric_columns.append((column, values))
    if not numeric_columns or len(numeric_columns) > 5:
        return None
    categories = [row[0] for row in table.rows if row]
    if not categories or len(categories) > 12:
        return None
    series = [{"name": table.header[column] or f"Ряд {i + 1}", "values": values}
              for i, (column, values) in enumerate(numeric_columns)]
    unit = "%" if any("%" in row[column] for row in table.rows
                      for column, _ in numeric_columns) else None
    return Chart(type=ChartType.COLUMN, categories=categories, series=series, unit=unit)


def chart_to_table(chart: Chart) -> Optional[Table]:
    """Диаграмма → таблица: универсальный читаемый вид (замена визуала)."""
    if not chart.categories or not chart.series:
        return None
    header = ["Категория"] + [series.name for series in chart.series[:MAX_TABLE_COLS - 1]]
    rows = []
    for index, category in enumerate(chart.categories):
        row = [category]
        for series in chart.series[:MAX_TABLE_COLS - 1]:
            value = series.values[index] if index < len(series.values) else None
            row.append("" if value is None else f"{value:g}")
        rows.append(row)
    if not rows:
        return None
    return Table(header=header, rows=rows[:MAX_TABLE_ROWS])


class FixEngine:
    """Применяет детерминированные исправления к колоде по списку проблем."""

    def __init__(self, profile: dict):
        self.profile = profile
        self.layouts = profile.get("layouts", []) or []
        self.handlers: dict[str, Callable[[Deck, dict], Optional[FixOutcome]]] = {
            "font_size_not_in_scale": self._shrink_font,
            "text_overflow": self._shrink_font,
            "contrast_too_low": self._shrink_font,
            "bullet_too_long": self._shorten_bullet,
            "too_many_bullets": self._split_slide,
            "slide_too_dense": self._split_slide,
            "slide_too_sparse": self._merge_slide,
            "empty_slide": self._merge_slide,
            "image_missing": self._replace_visual,
            "table_too_big": self._replace_visual,
            "chart_unlabeled": self._replace_visual,
            "too_many_series": self._replace_visual,
            "placeholder_text": self._drop_block,
            "misaligned_to_grid": self._switch_layout,
            "content_in_margins": self._switch_layout,
            "layout_not_from_template": self._switch_layout,
            "branding_shifted": self._switch_layout,
            "duplicate_slide": self._drop_duplicate_slide,
            "duplicate_heading": self._rename_duplicate_heading,
            "image_stretched": self._replace_visual,
        }

    # ------------------------------------------------------------------- вход
    def apply(self, deck: Deck, issues: list[dict],
              issue_ids: Optional[list[str]] = None) -> tuple[Deck, list[dict]]:
        """Применяет выбранные фиксы. Возвращает (колода, отчёты по каждому фиксу)."""
        selected = [issue for issue in issues
                    if issue_ids is None or issue.get("id") in set(issue_ids)]
        outcomes: list[FixOutcome] = []
        # сначала визуальные замены и структурные (меняют состав блоков),
        # затем геометрические и типографические
        order = {"replace_visual": 0, "drop_block": 1, "split_slide": 2,
                 "merge_slide": 3, "switch_layout": 4, "shrink_font": 5}
        selected.sort(key=lambda issue: self._order_key(issue, order))

        # одна проблема одного типа на одном слайде исправляется один раз:
        # иначе, например, десять нарушений кегля сдвинули бы шкалу на десять
        # ступеней. Остальные проблемы группы помечаются как исправленные тем же
        applied_groups: dict[tuple[str, int], FixOutcome] = {}
        for issue in selected:
            code = issue.get("code", "")
            key = (code, issue.get("slide", -1))
            previous = applied_groups.get(key)
            if previous is not None and previous.status == "applied":
                outcomes.append(FixOutcome(
                    issue.get("id", ""), code, issue.get("slide", -1), "applied",
                    previous.action, f"исправлено тем же изменением ({previous.action})"))
                continue

            handler = self.handlers.get(code)
            if handler is None:
                outcomes.append(FixOutcome(
                    issue.get("id", ""), code, issue.get("slide", -1), "skipped",
                    detail="фикс не поддержан: исправляется на уровне вёрстки или шаблона"))
                continue
            try:
                outcome = handler(deck, issue)
            except Exception as exc:  # noqa: BLE001 — один фикс не должен ронять остальные
                log.warning("фикс %s не удался: %s", code, exc)
                outcome = FixOutcome(issue.get("id", ""), code, issue.get("slide", -1),
                                     "skipped", detail=f"ошибка применения: {exc}")
            outcome = outcome or FixOutcome(
                issue.get("id", ""), code, issue.get("slide", -1), "skipped",
                detail="нечего исправлять")
            if outcome.status == "applied":
                applied_groups[key] = outcome
            outcomes.append(outcome)
        self._enforce_limits(deck)
        return deck, [outcome.to_dict() for outcome in outcomes]

    @staticmethod
    def _order_key(issue: dict, order: dict) -> tuple:
        name = issue.get("code", "")
        for action, weight in order.items():
            if name in _CODES_BY_ACTION.get(action, ()):
                return (weight, issue.get("slide", -1))
        return (9, issue.get("slide", -1))

    # --------------------------------------------------------------- фиксы
    def _shrink_font(self, deck: Deck, issue: dict) -> FixOutcome:
        slide = self._slide(deck, issue)
        if slide is None:
            return self._skip(issue, "слайд не найден")
        if slide.slide_type != SlideType.CONTENT:
            return self._skip(issue, "витринный слайд не масштабируется")
        if slide.type_scale_step <= -3:
            # шкала исчерпана: текст не влезает не из-за кегля, а из-за объёма —
            # переносим часть контента на следующий слайд
            if issue.get("code") == "text_overflow":
                return self._split_slide(deck, issue)
            return self._skip(issue, "кегль уже на нижней ступени шкалы")
        slide.type_scale_step -= 1
        return self._done(issue, "shrink_font",
                          f"шрифт уменьшен на ступень шкалы (сдвиг {slide.type_scale_step})")

    def _shorten_bullet(self, deck: Deck, issue: dict) -> FixOutcome:
        slide = self._slide(deck, issue)
        block = self._block(slide, lambda b: b.kind == "bullets" and any(
            len(item.split()) > MAX_BULLET_WORDS for item in (b.items or [])))
        if block is None:
            return self._skip(issue, "блок с длинным буллетом не найден")
        items: list[str] = []
        for item in block.items:
            items.extend(split_sentence(item))
        block.items = items[:MAX_BULLETS]
        dropped = max(0, len(items) - MAX_BULLETS)
        detail = "длинные буллеты разделены на части"
        if dropped:
            detail += f"; лишние {dropped} перенесены в отчёт"
        return self._done(issue, "split_bullet", detail)

    def _split_slide(self, deck: Deck, issue: dict) -> FixOutcome:
        if len(deck.slides) >= MAX_SLIDES:
            return self._skip(issue, f"достигнут предел {MAX_SLIDES} слайдов")
        index = issue.get("slide", -1)
        slide = self._slide(deck, issue)
        if slide is None or slide.slide_type != SlideType.CONTENT:
            return self._skip(issue, "переносить нечего: слайд витринный или не найден")

        moved: list[Block] = []
        if len(slide.blocks) > 1:
            half = max(1, len(slide.blocks) // 2)
            moved = slide.blocks[half:]
            slide.blocks = slide.blocks[:half]
        elif slide.blocks and (slide.blocks[0].items or []):
            block = slide.blocks[0]
            items = list(block.items)
            half = max(1, len(items) // 2)
            if len(items) - half < 1:
                return self._skip(issue, "на слайде слишком мало пунктов для переноса")
            block.items = items[:half]
            moved = [Block(kind="bullets", title=block.title, items=items[half:])]
        if not moved:
            return self._skip(issue, "нечего переносить")

        tail = slide.heading[:100]
        new_slide = Slide(slide_type=SlideType.CONTENT,
                          heading=f"{tail} (продолжение)"[:118], blocks=moved)
        deck.slides.insert(index + 1, new_slide)
        return self._done(issue, "split_slide",
                          f"часть контента перенесена на слайд {index + 2}")

    def _merge_slide(self, deck: Deck, issue: dict) -> FixOutcome:
        index = issue.get("slide", -1)
        if index < 0 or index + 1 >= len(deck.slides):
            return self._skip(issue, "следующего слайда нет")
        if len(deck.slides) <= MIN_SLIDES:
            return self._skip(issue, f"в колоде уже минимум {MIN_SLIDES} слайдов")
        slide, nxt = deck.slides[index], deck.slides[index + 1]
        if slide.slide_type != SlideType.CONTENT or nxt.slide_type != SlideType.CONTENT:
            return self._skip(issue, "объединять можно только контентные слайды")
        if len(slide.blocks) + len(nxt.blocks) > MAX_BLOCKS:
            return self._skip(issue, "блоки не помещаются на один слайд (лимит 6)")
        slide.blocks = slide.blocks + nxt.blocks
        deck.slides.pop(index + 1)
        return self._done(issue, "merge_slide",
                          f"контент объединён со слайдом {index + 2}")

    def _replace_visual(self, deck: Deck, issue: dict) -> FixOutcome:
        """Замена визуала: таблица ↔ диаграмма, картинка без файла — удаление."""
        slide = self._slide(deck, issue)
        if slide is None:
            return self._skip(issue, "слайд не найден")
        code = issue.get("code")

        if code in ("image_missing", "image_stretched"):
            block = self._block(slide, lambda b: b.kind == "image")
            if block is None:
                return self._skip(issue, "блок-иллюстрация не найден")
            if len(slide.blocks) == 1:
                return self._skip(issue, "иллюстрация — единственный блок слайда")
            slide.blocks.remove(block)
            return self._done(issue, "drop_image", "блок-иллюстрация убран")

        if code == "table_too_big":
            block = self._block(slide, lambda b: b.kind == "table" and b.table is not None)
            if block is None:
                return self._skip(issue, "таблица не найдена")
            chart = table_to_chart(block.table)
            if chart is not None:
                block.kind = "chart"
                block.chart = chart
                block.table = None
                return self._done(issue, "table_to_chart",
                                  "таблица заменена диаграммой (значения числовые)")
            table = block.table
            table.rows = table.rows[:MAX_TABLE_ROWS]
            table.header = table.header[:MAX_TABLE_COLS]
            return self._done(issue, "trim_table", "таблица обрезана до 7×5")

        if code in ("chart_unlabeled", "too_many_series"):
            block = self._block(slide, lambda b: b.kind == "chart" and b.chart is not None)
            if block is None:
                return self._skip(issue, "диаграмма не найдена")
            table = chart_to_table(block.chart)
            if table is None:
                if block.chart is not None and block.chart.series:
                    block.chart.series = block.chart.series[:5]
                    return self._done(issue, "limit_series", "оставлены 5 серий")
                return self._skip(issue, "диаграмму нельзя представить таблицей")
            block.kind = "table"
            block.table = table
            block.chart = None
            return self._done(issue, "chart_to_table", "диаграмма заменена таблицей")
        return self._skip(issue, "нет подходящего визуального блока")

    def _drop_block(self, deck: Deck, issue: dict) -> FixOutcome:
        """Убирает блок-заглушку (lorem, TODO, «вставьте текст»)."""
        from ..content.corpus import looks_like_placeholder

        slide = self._slide(deck, issue)
        if slide is None:
            return self._skip(issue, "слайд не найден")
        target = None
        for block in slide.blocks:
            texts = list(block.items or []) + [block.text or "", block.quote_text or ""]
            if any(text and looks_like_placeholder(text) for text in texts):
                target = block
                break
        if target is None:
            return self._skip(issue, "блок-заглушка не найден в колоде")
        slide.blocks.remove(target)
        return self._done(issue, "drop_block", "блок-заглушка убран")

    def _switch_layout(self, deck: Deck, issue: dict) -> FixOutcome:
        slide = self._slide(deck, issue)
        if slide is None:
            return self._skip(issue, "слайд не найден")
        if len(self.layouts) < 2:
            return self._skip(issue, "в шаблоне только один макет")
        from ..render.pptx_renderer import Renderer

        renderer = Renderer(self.profile)
        current = renderer._pick_layout(slide.slide_type, slide)
        same_role = [l for l in self.layouts
                     if l["id"] != current["id"] and l["role"] == current["role"]]
        candidates = same_role or [l for l in self.layouts
                                   if l["id"] != current["id"]
                                   and l["role"] == "content"]
        if not candidates:
            return self._skip(issue, "нет альтернативного макета подходящей роли")
        best = max(candidates, key=lambda l: l.get("score", 0.0))
        slide.layout_hint = best["id"]
        return self._done(issue, "switch_layout",
                          f"макет заменён на «{best['name']}» "
                          f"({best['role']}/{best['kind']}, оценка {best['score']})")

    def _drop_duplicate_slide(self, deck: Deck, issue: dict) -> FixOutcome:
        seen: dict[str, int] = {}
        duplicates: list[int] = []
        for index, slide in enumerate(deck.slides):
            fingerprint = _slide_fingerprint(slide)
            if not fingerprint:
                continue
            if fingerprint in seen:
                duplicates.append(index)
            else:
                seen[fingerprint] = index
        if issue.get("slide", -1) >= 0:
            duplicates = [index for index in duplicates if index == issue["slide"]]
        if not duplicates:
            return self._skip(issue, "дубликаты не найдены")
        removed = 0
        for index in sorted(duplicates, reverse=True):
            if len(deck.slides) <= MIN_SLIDES:
                break
            deck.slides.pop(index)
            removed += 1
        if not removed:
            return self._skip(issue, f"в колоде уже минимум {MIN_SLIDES} слайдов")
        return self._done(issue, "drop_duplicate", f"удалено дублей: {removed}")

    def _rename_duplicate_heading(self, deck: Deck, issue: dict) -> FixOutcome:
        counts: dict[str, int] = {}
        renamed = 0
        for slide in deck.slides:
            key = _norm(slide.heading)
            counts[key] = counts.get(key, 0) + 1
            if counts[key] > 1:
                slide.heading = f"{slide.heading[:110]} ({counts[key]})"
                renamed += 1
        if not renamed:
            return self._skip(issue, "повторов заголовков нет")
        return self._done(issue, "rename_heading",
                          f"уточнены повторяющиеся заголовки: {renamed}")

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _slide(deck: Deck, issue: dict) -> Optional[Slide]:
        index = issue.get("slide", -1)
        if not isinstance(index, int) or index < 0 or index >= len(deck.slides):
            return None
        return deck.slides[index]

    @staticmethod
    def _block(slide: Optional[Slide], predicate) -> Optional[Block]:
        if slide is None:
            return None
        for block in slide.blocks:
            if predicate(block):
                return block
        return None

    def _done(self, issue: dict, action: str, detail: str = "") -> FixOutcome:
        return FixOutcome(issue.get("id", ""), issue.get("code", ""),
                          issue.get("slide", -1), "applied", action, detail)

    def _skip(self, issue: dict, reason: str) -> FixOutcome:
        return FixOutcome(issue.get("id", ""), issue.get("code", ""),
                          issue.get("slide", -1), "skipped", detail=reason)

    @staticmethod
    def _enforce_limits(deck: Deck) -> None:
        """После фиксов колода обязана остаться валидной по схеме."""
        if len(deck.slides) < MIN_SLIDES:
            log.warning("после фиксов слайдов %s — меньше минимума", len(deck.slides))
        for slide in deck.slides:
            slide.blocks = slide.blocks[:MAX_BLOCKS]
            for block in slide.blocks:
                if block.items:
                    block.items = block.items[:MAX_BULLETS]
            if not slide.heading.strip():
                slide.heading = "Слайд презентации"


_CODES_BY_ACTION = {
    "replace_visual": ("table_too_big", "chart_unlabeled", "too_many_series",
                       "image_missing", "image_stretched"),
    "drop_block": ("placeholder_text",),
    "split_slide": ("too_many_bullets", "slide_too_dense", "bullet_too_long"),
    "merge_slide": ("slide_too_sparse", "empty_slide"),
    "switch_layout": ("misaligned_to_grid", "content_in_margins",
                      "layout_not_from_template", "branding_shifted"),
    "shrink_font": ("font_size_not_in_scale", "text_overflow", "contrast_too_low"),
}
