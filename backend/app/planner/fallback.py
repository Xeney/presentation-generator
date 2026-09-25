"""Офлайн-планировщик (fallback).

Используется, когда Ollama недоступен (dev-режим, CI) или API вернул битую колоду.
Контент строится только из брифа и исходных материалов — без выдуманных фактов.
Промпты/конфиги также вынесены в prompts/ (purpose.json).
"""
from __future__ import annotations

import json
import random
import re
from pathlib import Path

from ..models.deck import Block, Chart, ChartType, Deck, Slide, SlideType, Table
from .llm import OllamaClient

PURPOSE_PATH = Path(__file__).resolve().parents[3] / "prompts" / "planner" / "purpose.json"

SEQUENCES = {
    "feature": [
        ("section", "Проблема пользователя"),
        ("content", "Что мы предлагаем"),
        ("content", "Почему это лучше решений"),
        ("content", "Ключевые возможности"),
        ("content", "Как это работает"),
        ("final", "Давайте обсудим"),
    ],
    "product": [
        ("section", "Рынок и контекст"),
        ("content", "Проблема рынка"),
        ("content", "Наш продукт"),
        ("content", "Ключевые метрики"),
        ("content", "План развития"),
        ("final", "Итоги и призыв к действию"),
    ],
    "project": [
        ("section", "Цель проекта"),
        ("content", "Что мы делаем"),
        ("content", "Состав работ"),
        ("content", "Команда и сроки"),
        ("content", "Ожидаемые результаты"),
        ("final", "Следующие шаги"),
    ],
    "initiative": [
        ("section", "Цели инициативы"),
        ("content", "Для кого"),
        ("content", "Этапы"),
        ("content", "Ожидаемый эффект"),
        ("final", "Готовы к запуску"),
    ],
}

# Число с ЕДИНИЦЕЙ ИЗМЕРЕНИЯ. Единица обязательна и берётся из закрытого списка:
# прежний вариант с «любыми буквами до слова руб/г.» захватывал куски предложений
# («5 подразделений. Платформой пользую»), из-за чего ломались метрики и график.
METRIC_RE = re.compile(
    r"(?<![\w.,])"
    r"(\d{1,3}(?:[ \u00a0]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
    r"\s*"
    r"(%|₽|\$|€|руб\w*|млн\w*|млрд\w*|тыс\w*|чел\w*|пользовател\w+|задач\w*|"
    r"город\w*|отдел\w*|подразделени\w*|сотрудник\w*|час\w*|минут\w*|секунд\w*|"
    r"дн\w*|недел\w*|месяц\w*|год\w*|квартал\w*|раз\w*|балл\w*|пункт\w*|процент\w*)"
    r"(?![\w])",
    re.IGNORECASE,
)


def metric_value(text: str) -> Optional[float]:
    """Числовая часть метрики («1 500 сотрудников» → 1500.0), иначе None."""
    cleaned = re.sub(r"[^\d.,]", "", text or "").replace(",", ".").strip(".")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _comparable(values: list[float], ratio: float = 50.0) -> bool:
    """Значения сопоставимы для одной диаграммы (нет разброса на порядки).

    Брифы часто смешивают единицы: 3 города, 150 000 человек, 870 руб., 18 %.
    На одной шкале столбцы нечитаемы — «3» и «870» не видно рядом с «150 000»,
    а подписи наезжают друг на друга. Диаграмму в этом случае не строим:
    фактоиды остаются и читаются как KPI.
    """
    positive = [abs(v) for v in values if v]
    if len(positive) < 3:
        return False
    return max(positive) / max(1e-9, min(positive)) <= ratio


def _sentences(text: str, limit: int = 30) -> list[str]:
    parts = [re.sub(r"\s+", " ", s).strip(" .") for s in re.split(r"(?<=[.!?])\s+|\n", text)]
    return [s for s in parts if len(s) > 8][:limit]


def _digest_brief(brief: str) -> str:
    """Краткая сводка по брифу: до 600 символов."""
    sentences = _sentences(brief, 12)
    return " ".join(sentences)[:600]


def _find_metrics(brief: str) -> list[str]:
    matches = METRIC_RE.findall(brief)
    values = [" ".join(part.strip() for part in match if part.strip()) for match in matches]
    return list(dict.fromkeys(value for value in values if value))[:6]


def _title_text(brief: str) -> str:
    """Заголовок обложки: первая фраза брифа, но короткая (до 60 символов).

    Бриф часто начинается перечислением метрик («...на 40%, автоматизировала
    12 задач, охватила 5 подразделений»). В рамке титульного макета такая фраза
    обрезается (аудит: text_overflow), поэтому берём часть до запятой/двоеточия.
    """
    first = _sentences(brief, 1)
    sentence = (first[0] if first else brief.strip()) or "Презентация проекта"
    if len(sentence) <= 60:
        return sentence
    for sep in (",", ":", " — ", " - ", ";", "("):
        head = sentence.split(sep)[0].strip()
        if 20 <= len(head) <= 60:
            return head
    return sentence[:57].rstrip(" ,;:—-") + "…"


def _bullet_items(brief: str, source: str, n: int = 5) -> list[str]:
    sents = _sentences(brief + "\n" + source, 20)
    return sents[:max(1, min(n, 6))]


def _corpus_items(corpus, n: int = 5, used: list[str] | None = None) -> list[str]:
    """Тезисы из контент-пакета без служебной «рыбы» и без повторов."""
    used = used or []
    out: list[str] = []
    for line in corpus.meaningful_lines():
        if line in used or line in out:
            continue
        out.append(line)
        if len(out) >= n:
            break
    return out


class FallbackPlanner:
    """Собирает валидную колоду без модели."""

    def __init__(self):
        self.purposes = json.loads(PURPOSE_PATH.read_text(encoding="utf-8"))
        self._seq = SEQUENCES

    def plan(self, brief: str, source: str = "", purpose: str = "project",
             corpus=None) -> Deck:
        """Собирает валидную колоду без модели.

        Если передан контент-пакет, его изображения добавляются на слайд-иллюстрацию,
        чтобы встраивание картинок работало и в офлайн-режиме.
        """
        brief = (brief or "").strip()
        source = (source or "").strip()
        lang = "ru"
        title = _title_text(brief)
        digest = _digest_brief(brief)
        metrics = _find_metrics(brief if brief else source)

        slides: list[Slide] = [
            Slide(
                slide_type=SlideType.TITLE,
                heading=title[:100],
                # подзаголовок — суть брифа, а не шаблонная фраза: на обложке
                # «проект: цели, объём, команда, сроки — краткий обзор» выглядит
                # заполнителем и ничего не сообщает (визуальный чек-лист, п. 1)
                subheading=(digest[:150] or
                            f"{self.purposes['purposes'].get(purpose, 'Проект')}"),
                blocks=[],
            )
        ]

        seq = self._seq.get(purpose, self._seq["project"])
        # блок «метрики» только если в брифе есть цифры
        has_metrics = len(metrics) >= 2 or (len(metrics) == 1 and any(c.isdigit() for c in metrics[0]))
        sections = [t for t, _ in seq]
        slides.append(Slide(
            slide_type=SlideType.AGENDA,
            heading="Что будет в презентации",
            blocks=[Block(kind="bullets", items=sections[:6])],
        ))

        used_items: list[str] = []
        corpus_headings = corpus.headings() if corpus is not None else []
        for idx, (stype_name, h) in enumerate(seq):
            stype = SlideType(stype_name)
            blocks: list[Block] = []
            if stype == SlideType.CONTENT:
                if corpus is not None:
                    items = _corpus_items(corpus, 5, used_items)
                    used_items.extend(items)
                else:
                    items = _bullet_items(
                        brief, source,
                        n=max(2, min(5, len(_sentences(brief + '\n' + source)) // 2)))
                if not items:
                    items = _bullet_items(brief, source, n=3)
                blocks.append(Block(kind="bullets", title="Ключевые тезисы", items=items))
                if has_metrics and "метрик" in h.lower() or (idx == 2 and has_metrics):
                    if metrics:
                        blocks.append(Block(
                            kind="factoids",
                            factoids=[{"value": m, "label": "показатель из брифа"} for m in metrics[:4]],
                        ))
                    numeric = [(metric, metric_value(metric)) for metric in metrics]
                    numeric = [(metric, value) for metric, value in numeric
                               if value is not None]
                    if (has_metrics and len(numeric) >= 3
                            and _comparable([value for _, value in numeric])):
                        blocks.append(Block(
                            kind="chart",
                            title="Ключевые показатели из брифа",
                            chart=Chart(
                                type=ChartType.COLUMN,
                                categories=[metric[:18] for metric, _ in numeric[:4]],
                                series=[{"name": "Значение",
                                         "values": [value for _, value in numeric[:4]]}],
                                unit="",
                            ),
                        ))
            elif stype == SlideType.SECTION:
                # короткий тезис: длинный абзац не влезает в рамку разделителя
                blocks.append(Block(kind="text", text=digest[:150]))
            elif stype == SlideType.FINAL:
                blocks.append(Block(kind="text", text="Спасибо за внимание. Готовы ответить на вопросы."))
            slides.append(Slide(
                slide_type=stype,
                heading=h,
                subheading=None,
                blocks=[b for b in blocks if b_kind(b) != "empty"],
            ))

        # иллюстрация из контент-пакета: встраивание картинок работает и без LLM
        if corpus is not None and corpus.images:
            content_slides = [s for s in slides if s.slide_type == SlideType.CONTENT]
            if content_slides:
                key = next(iter(corpus.images))
                content_slides[0].blocks.append(Block(
                    kind="image", image_ref=key, title="Иллюстрация"))

        # гарантируем 10-15 слайдов: добираем содержанием из источника/корпуса
        used_headings = {s.heading for s in slides}
        while len(slides) < 10:
            if corpus is not None:
                items = _corpus_items(corpus, 5, used_items)
                heading = corpus_headings.pop(0) if corpus_headings else ""
            else:
                items = _bullet_items(source, "", 5)
                heading = ""
            if not items:
                break
            used_items.extend(items)
            if not heading:
                heading = f"Детали: {digest[:60]}" if digest else "Дополнительные детали"
            while heading in used_headings:
                heading = f"{heading[:110]} (продолжение)"
            used_headings.add(heading)
            slides.append(Slide(
                slide_type=SlideType.CONTENT,
                heading=heading[:118],
                blocks=[Block(kind="bullets", items=items)],
            ))
            digest = digest[60:]
            if corpus is None and len(digest) < 20:
                break
        while len(slides) > 15:
            slides = slides[:1] + slides[1:14] + slides[-1:]

        # исправляем потенциально длинные заголовки (<=120 по схеме)
        for s in slides:
            s.heading = s.heading[:118]
            for b in s.blocks:
                if b.items:
                    b.items = b.items[:6]
        return Deck(title=title[:100], language=lang, pitch=digest[:200], slides=slides)


def b_kind(b: Block) -> str:
    if b.kind == "bullets" and not b.items:
        return "empty"
    if b.kind == "factoids" and not b.factoids:
        return "empty"
    if b.kind in ("table", "chart") and b.table is None and b.chart is None:
        return "empty"
    if b.kind in ("text", "quote") and not b.text and not b.quote_text:
        return "empty"
    return "ok"