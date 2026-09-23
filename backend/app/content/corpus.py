"""Контент-пакет: структурированный корпус исходных материалов.

Корпус — это то, что сервис знает о фактах до генерации: заголовки, тезисы,
таблицы, диаграммы, цифры и изображения из загруженного файла (PPTX/DOCX/TXT).
Из корпуса собирается текст для промпта планировщика и реестр изображений для
рендера; он же служит источником для проверки фактов (grounding, этап 5).

Корпус сериализуется в ``data/corpora/{id}/corpus.json``, изображения — рядом
в ``images/``. Байты картинок в JSON не попадают.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

# Слова-заглушки: встречаются в шаблонах и контент-пакетах как «рыба».
# Единый источник правды: используется и фильтром контента, и аудитом.
#   ANYWHERE — однозначные маркеры, ищем в любом месте строки;
#   START    — заголовки-подсказки, ищем только в начале короткой строки,
#              чтобы не ругать осмысленный текст, где встретилось слово «заголовок»;
#   EXACT    — строки, которые целиком являются служебными.
PLACEHOLDER_ANYWHERE = (
    "lorem ipsum", "todo", "xxx", "yyy", "вставьте текст", "введите текст",
    "здесь текст", "дважды щёлкните", "дважды щелкните", "your text here",
)
PLACEHOLDER_START = (
    "заголовок", "подзаголовок", "текст описания", "основной текст",
    "название раздела", "название команды", "название презентации",
    "имя спикера", "заполните",
)
PLACEHOLDER_EXACT = (
    "текст", "слайд", "qr-code", "дата", "пункт", "описание", "примечание",
    "заголовок", "подзаголовок", "заглушка", "xx", "хх", "иллюстрация", "графики",
)
PLACEHOLDER_WORDS = PLACEHOLDER_ANYWHERE + PLACEHOLDER_START  # обратная совместимость
PLACEHOLDER_MAX_HINT_LEN = 70


def looks_like_placeholder(text: str) -> bool:
    """Похожа ли строка на служебную «рыбу» из шаблона, а не на контент."""
    normalized = re.sub(r"\s+", " ", (text or "").strip().lower())
    if not normalized:
        return True
    if normalized in PLACEHOLDER_EXACT:
        return True
    if any(word in normalized for word in PLACEHOLDER_ANYWHERE):
        return True
    if len(normalized) <= PLACEHOLDER_MAX_HINT_LEN and \
            any(normalized.startswith(word) for word in PLACEHOLDER_START):
        return True
    return False


# числа с единицами измерения: «40%», «12 задач», «870 руб.», «1,5 млн», «10 000»
NUMBER_RE = re.compile(
    r"\d+(?:[.,]\d+)?\s*"
    r"(?:%|₽|\$|€|руб\w*|млн\w*|млрд\w*|тыс\w*|чел\w*|человек\w*|пользовател\w*|"
    r"задач\w*|город\w*|стран\w*|отдел\w*|дн\w*|недел\w*|месяц\w*|год\w*|кв\w*|"
    r"пункт\w*|раз\w*|процент\w*|балл\w*|минут\w*|час\w*|секунд\w*|"
    r"pt|px|kb|mb|gb|тыс\.|млн\.)",
    re.IGNORECASE)


@dataclass
class CorpusTable:
    header: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CorpusSlide:
    index: int = 0
    layout: str = ""
    heading: str = ""
    subheading: str = ""
    bullets: list[str] = field(default_factory=list)
    paragraphs: list[str] = field(default_factory=list)
    tables: list[CorpusTable] = field(default_factory=list)
    charts: list[dict] = field(default_factory=list)
    numbers: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        data["tables"] = [t.to_dict() for t in self.tables]
        return data

    def is_empty(self) -> bool:
        return not (self.heading or self.subheading or self.bullets or self.paragraphs
                    or self.tables or self.charts or self.images)


@dataclass
class ContentCorpus:
    """Импортированный контент-пакет."""

    id: str = ""
    source_file: str = ""
    kind: str = "text"                       # pptx | docx | text
    slides: list[CorpusSlide] = field(default_factory=list)
    images: dict[str, bytes] = field(default_factory=dict)
    image_labels: dict[str, str] = field(default_factory=dict)
    numbers: list[str] = field(default_factory=list)
    created: float = field(default_factory=time.time)
    warnings: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------ вывод
    def non_empty_slides(self) -> list[CorpusSlide]:
        return [s for s in self.slides if not s.is_empty()]

    def text(self, max_chars: int = 7000) -> str:
        """Компактное текстовое представление для промпта планировщика."""
        lines: list[str] = []
        for slide in self.non_empty_slides():
            lines.append(f"## Слайд {slide.index + 1}: {slide.heading or '(без заголовка)'}")
            if slide.subheading:
                lines.append(f"Подзаголовок: {slide.subheading}")
            lines.extend(f"- {b}" for b in slide.bullets)
            lines.extend(f"{p}" for p in slide.paragraphs)
            for table in slide.tables:
                if table.header:
                    lines.append("[таблица] " + " | ".join(table.header))
                for row in table.rows:
                    lines.append("[таблица] " + " | ".join(row))
            for chart in slide.charts:
                series = ", ".join(f"{s.get('name')}: {s.get('values')}"
                                   for s in chart.get("series", []))
                lines.append(f"[диаграмма] {chart.get('type')} "
                             f"{chart.get('categories')} — {series}")
            if slide.numbers:
                lines.append("[цифры] " + ", ".join(slide.numbers))
            if slide.images:
                lines.append("[изображения] " + ", ".join(slide.images))
        text = "\n".join(lines).strip()
        if len(text) > max_chars:
            text = text[:max_chars].rsplit("\n", 1)[0] + "\n… (корпус усечён)"
        return text

    def image_prompt_block(self, limit: int = 24) -> str:
        """Список доступных изображений для промпта: ключ — где лежит."""
        if not self.images:
            return "изображений нет"
        lines = []
        for key in list(self.images)[:limit]:
            lines.append(f"- {key}: {self.image_labels.get(key, 'без описания')}")
        return "\n".join(lines)

    def meaningful_lines(self, min_chars: int = 24) -> list[str]:
        """Тезисы и абзацы корпуса, пригодные как контент (без служебной «рыбы»).

        Используется офлайн-планировщиком и grounding-проверкой: строки вида
        «Заголовок», «Текст описания» отбрасываются.
        """
        out: list[str] = []
        for slide in self.non_empty_slides():
            for text in list(slide.bullets) + list(slide.paragraphs):
                candidate = re.sub(r"\s+", " ", text or "").strip()
                if len(candidate) < min_chars:
                    continue
                if looks_like_placeholder(candidate):
                    continue
                if candidate not in out:
                    out.append(candidate)
        return out

    def headings(self) -> list[str]:
        """Осмысленные заголовки слайдов корпуса."""
        return [s.heading for s in self.non_empty_slides()
                if s.heading and not looks_like_placeholder(s.heading)
                and len(s.heading) >= 8]

    def all_numbers(self) -> list[str]:
        if self.numbers:
            return self.numbers
        seen: dict[str, None] = {}
        for slide in self.slides:
            for n in slide.numbers:
                seen.setdefault(n, None)
        return list(seen)

    # ------------------------------------------------------------ сериализация
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source_file": self.source_file,
            "kind": self.kind,
            "created": self.created,
            "slides": [s.to_dict() for s in self.slides],
            "image_keys": list(self.images),
            "image_labels": self.image_labels,
            "numbers": self.numbers,
            "warnings": self.warnings,
            "stats": {
                "slides": len(self.slides),
                "non_empty": len(self.non_empty_slides()),
                "images": len(self.images),
                "numbers": len(self.all_numbers()),
            },
        }

    def save(self, data_dir: Path) -> Path:
        root = Path(data_dir) / "corpora" / self.id
        (root / "images").mkdir(parents=True, exist_ok=True)
        (root / "corpus.json").write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        for key, blob in self.images.items():
            (root / "images" / key).write_bytes(blob)
        return root

    @classmethod
    def load(cls, corpus_id: str, data_dir: Path) -> Optional["ContentCorpus"]:
        root = Path(data_dir) / "corpora" / corpus_id
        meta_path = root / "corpus.json"
        if not meta_path.exists():
            return None
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        corpus = cls(
            id=meta["id"], source_file=meta.get("source_file", ""),
            kind=meta.get("kind", "text"), created=meta.get("created", 0.0),
            image_labels=meta.get("image_labels", {}),
            numbers=meta.get("numbers", []),
            warnings=meta.get("warnings", []),
        )
        for raw in meta.get("slides", []):
            corpus.slides.append(CorpusSlide(
                index=raw.get("index", 0), layout=raw.get("layout", ""),
                heading=raw.get("heading", ""), subheading=raw.get("subheading", ""),
                bullets=raw.get("bullets", []), paragraphs=raw.get("paragraphs", []),
                tables=[CorpusTable(**t) for t in raw.get("tables", [])],
                charts=raw.get("charts", []), numbers=raw.get("numbers", []),
                images=raw.get("images", []), notes=raw.get("notes", "")))
        for key in meta.get("image_keys", []):
            path = root / "images" / key
            if path.exists():
                corpus.images[key] = path.read_bytes()
        return corpus


def find_numbers(text: str) -> list[str]:
    """Цифры с единицами измерения, без повторов, в порядке появления."""
    seen: dict[str, None] = {}
    for match in NUMBER_RE.finditer(text or ""):
        value = re.sub(r"\s+", " ", match.group(0)).strip()
        seen.setdefault(value, None)
    return list(seen)


def list_corpora(data_dir: Path) -> list[dict]:
    """Краткие метаданные всех сохранённых корпусов (новые — первыми)."""
    root = Path(data_dir) / "corpora"
    if not root.is_dir():
        return []
    items = []
    for meta_path in root.glob("*/corpus.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — битый файл не должен ломать список
            continue
        items.append({
            "id": meta.get("id", meta_path.parent.name),
            "source_file": meta.get("source_file", ""),
            "created": meta.get("created", 0.0),
            "stats": meta.get("stats", {}),
        })
    items.sort(key=lambda item: item["created"], reverse=True)
    return items
