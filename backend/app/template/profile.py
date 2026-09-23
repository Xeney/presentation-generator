"""Модель JSON-профиля шаблона презентации.

Профиль извлекается парсером и передаётся в планировщик (для контекста),
вёрсточный движок (правила композиции) и аудит (разрешённые токены).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class ColorToken:
    hex: str = ""
    count: int = 0
    source: str = "shape"  # shape | theme | text | image

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FontToken:
    name: str = ""
    bold: bool = False
    count: int = 0

    def label(self) -> str:
        return f"{self.name} {'Bold' if self.bold else 'Regular'}"


@dataclass
class PlaceholderInfo:
    idx: int = 0
    type: str = ""          # title | body | subtitle | picture | slide_number | footer | ...
    name: str = ""
    x: float = 0.0          # inches
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0
    font: Optional[str] = None
    size: Optional[float] = None
    color: Optional[str] = None
    align: str = "left"
    is_title: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class LayoutProfile:
    id: str = ""            # L0, L1...
    master_id: str = ""
    name: str = ""
    role: str = "content"   # title | section | agenda | content | final
    score: float = 0.0
    placeholders: list[PlaceholderInfo] = field(default_factory=list)
    title_ph: Optional[dict] = None
    body: Optional[dict] = None       # главная свободная зона под контент
    columns: list[dict] = field(default_factory=list)
    has_logo: bool = False
    style_sample: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


@dataclass
class TemplateProfile:
    template_id: str = ""
    source_file: str = ""
    slide_size: dict = None            # {"w_in","h_in","w_emus","h_emus"}
    theme_fonts: dict = field(default_factory=dict)
    theme_colors: dict = field(default_factory=dict)
    palette: list[ColorToken] = field(default_factory=list)
    text_colors: list[str] = field(default_factory=list)
    fonts: list[FontToken] = field(default_factory=list)
    headline_font: Optional[str] = None
    body_font: Optional[str] = None
    type_scale: dict = field(default_factory=dict)
    grid: dict = field(default_factory=dict)
    layouts: list[LayoutProfile] = field(default_factory=list)
    layout_groups: dict = field(default_factory=dict)   # role -> [layout ids]
    example_patterns: list[dict] = field(default_factory=list)
    slide_count: int = 0
    layout_count: int = 0
    notes: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["palette"] = [c.to_dict() for c in self.palette]
        d["fonts"] = [f.__dict__ for f in self.fonts]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "TemplateProfile":
        raise NotImplementedError("from_dict не требуется: профили только для чтения")

    def hexes(self) -> list[str]:
        return [c.hex for c in self.palette if c.hex]

    def color(self, i: int = 0) -> str:
        """i-й разрешённый цвет палитры (по частоте использования)."""
        hs = self.hexes()
        return hs[i] if i < len(hs) else "#333333"