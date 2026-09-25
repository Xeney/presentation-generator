"""Детерминированная нормализация колоды, полученной от модели.

Модель 9B не всегда соблюдает жёсткие ограничения схемы: забывает сделать первый
слайд титульным, отдаёт 16–20 слайдов, ставит семь буллетов или незнакомый тип
блока. Повторный запрос стоит 20–70 секунд, а бюджет генерации — 5 минут,
поэтому структуру правит код: замысел модели сохраняется, а не переспрашивается.

Нормализация не «причёсывает» содержание: длинные буллеты, выдуманные цифры и
пустые слайды остаются на усмотрение аудита и авто-фиксов, иначе проверки
перестанут что-либо ловить.
"""
from __future__ import annotations

from typing import Any, Optional

SLIDE_TYPES = {"title", "section", "agenda", "content", "final"}
BLOCK_KINDS = {"bullets", "text", "factoids", "table", "chart", "quote", "steps",
               "columns", "image"}
MAX_SLIDES = 15
MAX_BLOCKS = 6
MAX_ITEMS = 6
# абзац длиннее не влезает в рамку макета при крупной типографической шкале
MAX_TEXT_CHARS = 180


def _has_payload(block: dict) -> bool:
    """Есть ли в блоке хоть какие-то данные (иначе он станет пустым слайдом)."""
    return bool(block.get("items") or block.get("text") or block.get("factoids")
                or block.get("table") or block.get("chart")
                or block.get("quote_text") or block.get("image_ref")
                or block.get("image_prompt"))


def _dedupe_items(items: list) -> list:
    """Убирает повторы пунктов, сравнивая текст без регистра и пунктуации."""
    import re

    def key(item) -> str:
        text = item if isinstance(item, str) else str(item)
        return re.sub(r"[\W_]+", "", text.lower())

    seen: set[str] = set()
    out: list = []
    for item in items:
        item_key = key(item)
        if not item_key or item_key in seen:
            continue
        seen.add(item_key)
        out.append(item)
    return out


def _split_sentences(text: str, limit: int) -> list[str]:
    """Режет абзац на предложения (не больше limit штук)."""
    import re

    parts = [part.strip(" .") for part in re.split(r"(?<=[.!?;])\s+", text or "")]
    parts = [part for part in parts if len(part) > 8]
    if not parts:
        return [text]
    if len(parts) > limit:
        head = parts[:limit - 1]
        tail = " ".join(parts[limit - 1:])
        return [*head, tail]
    return parts


def normalize_deck(data: Any, *, max_slides: int = MAX_SLIDES) -> tuple[Any, list[str]]:
    """Приводит ответ модели к схеме. Возвращает (данные, список правок)."""
    fixes: list[str] = []
    if not isinstance(data, dict):
        return data, fixes

    slides = data.get("slides")
    if not isinstance(slides, list) or not slides:
        return data, fixes
    slides = [slide for slide in slides if isinstance(slide, dict)]
    if not slides:
        return data, fixes

    # 1. первый слайд обязан быть титульным
    if slides[0].get("slide_type") != "title":
        for index, slide in enumerate(slides):
            if slide.get("slide_type") == "title":
                slides.insert(0, slides.pop(index))
                fixes.append("титульный слайд перенесён в начало")
                break
        else:
            slides[0]["slide_type"] = "title"
            fixes.append("первому слайду присвоен тип «титульный»")

    # 2. объём колоды
    if len(slides) > max_slides:
        fixes.append(f"колода обрезана с {len(slides)} до {max_slides} слайдов")
        slides = slides[:max_slides]

    # 3. заголовки, типы и блоки
    for index, slide in enumerate(slides):
        if slide.get("slide_type") not in SLIDE_TYPES:
            slide["slide_type"] = "content"
            fixes.append(f"слайд {index + 1}: неизвестный тип заменён на «контент»")
        heading = str(slide.get("heading") or "").strip()
        if len(heading) < 3:  # схема требует минимум три символа
            slide["heading"] = f"Слайд {index + 1}"
            fixes.append(f"слайд {index + 1}: подставлен заголовок")
        slide["heading"] = slide["heading"][:120]

        blocks = slide.get("blocks")
        if not isinstance(blocks, list):
            slide["blocks"] = []
            continue
        cleaned: list[dict] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            kind = block.get("kind")
            if kind not in BLOCK_KINDS:
                # незнакомый тип: сохраняем данные, меняем только ярлык
                if block.get("items") or block.get("text"):
                    block["kind"] = "bullets" if block.get("items") else "text"
                else:
                    fixes.append(f"слайд {index + 1}: удалён блок без данных")
                    continue
                fixes.append(f"слайд {index + 1}: неизвестный тип блока заменён")
            elif not _has_payload(block):
                # модель назвала тип, но данных не дала: пустой блок превратился бы
                # в пустой слайд, поэтому выбрасываем его сразу
                fixes.append(f"слайд {index + 1}: удалён пустой блок «{kind}»")
                continue
            # одинаковые пункты (в т.ч. различающиеся только регистром и знаками)
            # схлопываем: модель иногда дублирует список целиком
            items = block.get("items")
            if isinstance(items, list) and items:
                unique = _dedupe_items(items)
                if len(unique) != len(items):
                    fixes.append(f"слайд {index + 1}: убраны дубликаты пунктов "
                                 f"({len(items) - len(unique)})")
                    block["items"] = unique
            items = block.get("items")
            if isinstance(items, list) and len(items) > MAX_ITEMS:
                block["items"] = items[:MAX_ITEMS]
                fixes.append(f"слайд {index + 1}: буллетов оставлено {MAX_ITEMS}")
            text = block.get("text")
            if isinstance(text, str) and len(text) > MAX_TEXT_CHARS:
                # длинный абзац не влезает в рамку макета: у шаблонов с крупной
                # шкалой (например, 32 pt минимум) он обрезается краем слайда.
                # Разбиваем на короткие пункты — их вместимость проверяется аудитом.
                sentences = _split_sentences(text, MAX_ITEMS)
                if len(sentences) >= 2:
                    block["kind"] = "bullets"
                    block["items"] = [s[:MAX_TEXT_CHARS] for s in sentences]
                    block["text"] = None
                    fixes.append(f"слайд {index + 1}: длинный абзац разбит на "
                                 f"{len(sentences)} пункта")
                else:
                    block["text"] = text[:MAX_TEXT_CHARS].rstrip() + "…"
                    fixes.append(f"слайд {index + 1}: абзац сокращён до "
                                 f"{MAX_TEXT_CHARS} символов")
            cleaned.append(block)
        if len(cleaned) > MAX_BLOCKS:
            fixes.append(f"слайд {index + 1}: блоков оставлено {MAX_BLOCKS}")
            cleaned = cleaned[:MAX_BLOCKS]
        slide["blocks"] = cleaned

    # 4. обязательные поля колоды
    title = str(data.get("title") or "").strip()
    if len(title) < 3:
        data["title"] = str(slides[0].get("heading") or "Презентация")[:120]
        fixes.append("заголовок колоды взят с первого слайда")
    if data.get("language") not in ("ru", "en"):
        data["language"] = "ru"
        fixes.append("язык колоды не указан — принят русский")
    data["slides"] = slides
    return data, fixes


def normalize_or_report(data: Any, max_slides: int = MAX_SLIDES) -> tuple[Any, list[str]]:
    """Обёртка для вызова из планировщика (единая точка входа)."""
    return normalize_deck(data, max_slides=max_slides)


def describe(fixes: list[str], limit: int = 6) -> str:
    """Короткая строка правок для логов и отчёта."""
    if not fixes:
        return ""
    head = "; ".join(fixes[:limit])
    return head if len(fixes) <= limit else f"{head}; ещё {len(fixes) - limit}"


def has_title_first(data: Any) -> Optional[bool]:
    """Вспомогательная проверка для тестов и диагностики."""
    if not isinstance(data, dict):
        return None
    slides = data.get("slides")
    if not isinstance(slides, list) or not slides:
        return None
    return slides[0].get("slide_type") == "title"
