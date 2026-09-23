"""Строгая Pydantic-схема JSON-колоды.

Используется одновременно как:
  - контракт для LLM (промпт описывает эту схему, ответ валидируется моделью);
  - внутреннее представление контента между планировщиком, рендером и аудитом.

Схема намеренно жёсткая: значения перечислений валидируются, поля обязательны.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class SlideType(str, Enum):
    TITLE = "title"
    SECTION = "section"
    AGENDA = "agenda"
    CONTENT = "content"
    FINAL = "final"


class ChartType(str, Enum):
    BAR = "bar"
    COLUMN = "column"
    LINE = "line"
    PIE = "pie"
    DONUT = "donut"


class Series(BaseModel):
    """Одна серия диаграммы. value: список чисел категории."""

    name: str = Field(min_length=1, max_length=40)
    values: list[float] = Field(max_length=20)


class Chart(BaseModel):
    """Диаграмма. Не более 5 серий и 12 категорий (внешний гайдлайн: <=5 серий)."""

    type: ChartType
    categories: list[str] = Field(max_length=12)
    series: list[Series] = Field(min_length=1, max_length=5)
    unit: Optional[str] = Field(default=None, max_length=8)
    labels: Optional[bool] = True

    @field_validator("categories")
    @classmethod
    def non_empty_categories(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("categories пуст")
        return v


class Table(BaseModel):
    """Таблица. Ограничения гайдлайна: <=7 строк (вкл. шапку), <=5 колонок."""

    header: list[str] = Field(min_length=1, max_length=5)
    rows: list[list[str]] = Field(min_length=1, max_length=6)

    @field_validator("rows")
    @classmethod
    def rows_width(cls, v: list[list[str]], info) -> list[list[str]]:
        for row in v:
            if len(row) != len(info.data.get("header", [])):
                raise ValueError("ширина строки не равна ширине шапки")
        return v


class Block(BaseModel):
    """Блок контента внутри слайда-контента."""

    kind: Literal[
        "bullets", "text", "factoids", "table", "chart",
        "quote", "steps", "columns", "image",
    ] = "text"
    title: Optional[str] = Field(default=None, max_length=80)
    items: list[str] = Field(default_factory=list, max_length=8)
    text: Optional[str] = Field(default=None, max_length=700)
    table: Optional[Table] = None
    chart: Optional[Chart] = None
    factoids: list[dict[str, str]] = Field(default_factory=list, max_length=6)
    quote_text: Optional[str] = Field(default=None, max_length=300)
    quote_author: Optional[str] = Field(default=None, max_length=80)
    # --- изображения (kind="image") ---
    # image_ref — ключ картинки в контент-пакете (имя файла внутри PPTX/DOCX)
    image_ref: Optional[str] = Field(default=None, max_length=160)
    image_caption: Optional[str] = Field(default=None, max_length=80)
    # image_prompt — описание для text-to-image (используется, если генерация включена)
    image_prompt: Optional[str] = Field(default=None, max_length=300)
    source_ref: Optional[str] = Field(default=None, max_length=120)


class Slide(BaseModel):
    """Один слайд колоды.

    Правила (проверяются в аудите и частично здесь):
      - заголовок должен содержать вывод, а не только тему;
      - на слайде не должно быть больше 6 буллетов; буллет <= 15 слов.
    """

    slide_type: SlideType = SlideType.CONTENT
    heading: str = Field(min_length=3, max_length=120)
    subheading: Optional[str] = Field(default=None, max_length=160)
    # layout_hint — id макета шаблона (например, «L12»), принудительно выбранный
    # для этого слайда; ставится авто-фиксом «сменить макет»
    layout_hint: Optional[str] = Field(default=None, max_length=60)
    # type_scale_step — сдвиг по типографической шкале шаблона (отрицательный —
    # мельче); ставится авто-фиксом «уменьшить шрифт»
    type_scale_step: int = Field(default=0, ge=-3, le=3)
    blocks: list[Block] = Field(default_factory=list, max_length=6)


class Deck(BaseModel):
    """Колода презентации целиком: результат планировщика."""

    title: str = Field(min_length=3, max_length=120)
    language: Literal["ru", "en"] = "ru"
    pitch: str = Field(default="", max_length=200)
    slides: list[Slide] = Field(min_length=3, max_length=15)

    @field_validator("slides")
    @classmethod
    def first_slide_is_title(cls, v: list[Slide]) -> list[Slide]:
        if not v:
            raise ValueError("колода не может быть пустой")
        if v[0].slide_type != SlideType.TITLE:
            raise ValueError("первый слайд должен быть титульным")
        return v