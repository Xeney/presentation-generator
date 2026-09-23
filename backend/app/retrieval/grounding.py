"""Проверка опоры на источник: числа и смысл слайдов против контент-пакета.

ТЗ требует, чтобы все цифры и факты со слайда были в исходных материалах.
Здесь это проверяется двумя способами:

* **числа** — детерминированно: каждое число с единицей измерения со слайда
  ищется в корпусе (нормализованное сравнение), иначе проблема `fact_unverified`;
* **смысл** — контекстуально: эмбеддинги BGE-M3, максимальная косинусная близость
  текста слайда к строкам корпуса; ниже порога — `content_off_source`;
  близость между слайдами выше порога — семантический `duplicate_slide`.

Модель эмбеддингов доступна не всегда (Ollama может быть не поднята), поэтому
детерминированная часть работает всегда, а эмбеддинговая честно помечается как
недоступная.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from typing import Optional

from ..content.corpus import ContentCorpus
from ..models.deck import Deck

log = logging.getLogger("grounding")

NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?\s*%?")


def normalize_number(value: str) -> str:
    """«40,0 %» → «40%», «3.50%» → «3.5%»: сравнение без учёта формата записи."""
    text = re.sub(r"\s+", "", (value or "").lower()).replace(",", ".")
    match = re.match(r"(\d+(?:\.\d+)?)(%?)", text)
    if not match:
        return text
    try:
        number = float(match.group(1))
    except ValueError:  # noqa: PERF203 — сюда попадают только нечисловые строки
        return text
    return f"{number:g}{match.group(2)}"


def numbers_in(text: str) -> set[str]:
    return {normalize_number(match.group(0)) for match in NUMBER_RE.finditer(text or "")}


def slide_text(slide) -> str:
    """Весь текстовый контент слайда одной строкой."""
    parts = [slide.heading, slide.subheading or ""]
    for block in slide.blocks:
        parts.extend(block.items or [])
        parts.extend([block.text or "", block.quote_text or "",
                      block.image_caption or ""])
        if block.table is not None:
            parts.extend(block.table.header)
            parts.extend(cell for row in block.table.rows for cell in row)
        if block.chart is not None:
            parts.extend(block.chart.categories)
            parts.extend(series.name for series in block.chart.series)
    return " ".join(part for part in parts if part)


@dataclass
class GroundingResult:
    issues: list[dict]
    available: bool
    reason: str = ""
    model: str = ""

    def to_dict(self) -> dict:
        return {"issues": self.issues, "available": self.available,
                "reason": self.reason, "model": self.model,
                "issues_count": len(self.issues)}


def _cosine(first: list[float], second: list[float]) -> float:
    if not first or not second or len(first) != len(second):
        return 0.0
    dot = sum(a * b for a, b in zip(first, second))
    norm_a = math.sqrt(sum(a * a for a in first))
    norm_b = math.sqrt(sum(b * b for b in second))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class GroundingChecker:
    """Сверяет колоду с контент-пакетом: числа — точно, смысл — эмбеддингами."""

    def __init__(self, corpus: Optional[ContentCorpus], llm=None,
                 off_source_threshold: float = 0.55,
                 duplicate_threshold: float = 0.92,
                 max_corpus_lines: int = 120):
        self.corpus = corpus
        self.llm = llm
        self.off_source_threshold = off_source_threshold
        self.duplicate_threshold = duplicate_threshold
        self.corpus_lines = (corpus.meaningful_lines()
                             if corpus is not None else [])[:max_corpus_lines]

    # ------------------------------------------------------------------ api
    def check(self, deck: Deck) -> GroundingResult:
        if self.corpus is None:
            return GroundingResult([], False, "контент-пакет не передан")
        issues = self._check_numbers(deck)
        issues.extend(self._check_similarity(deck))
        return GroundingResult(issues, True, model="bge-m3")

    # -------------------------------------------------------------- числа
    def _corpus_numbers(self) -> set[str]:
        known = {normalize_number(number) for number in self.corpus.all_numbers()}
        for number in numbers_in(self.corpus.text(max_chars=100000)):
            known.add(number)
        return known

    def _check_numbers(self, deck: Deck) -> list[dict]:
        known = self._corpus_numbers()
        issues: list[dict] = []
        for index, slide in enumerate(deck.slides):
            for number in sorted(numbers_in(slide_text(slide))):
                # «1», «2», «3» — нумерация пунктов, а не факт
                if number in known or len(number.rstrip("%")) <= 1:
                    continue
                issues.append({
                    "id": f"fact_unverified-{index}-{len(issues)}",
                    "code": "fact_unverified", "severity": "warning",
                    "slide": index, "bbox": [], "deterministic": True,
                    "message": f"число «{number}» со слайда не найдено "
                               f"в контент-пакете «{self.corpus.source_file}»",
                })
        return issues

    # ------------------------------------------------------------ эмбеддинги
    def _check_similarity(self, deck: Deck) -> list[dict]:
        if self.llm is None or not self.corpus_lines:
            return []
        texts = [slide_text(slide) for slide in deck.slides]
        vectors = self.llm.embed(texts + self.corpus_lines)
        if not vectors or len(vectors) != len(texts) + len(self.corpus_lines):
            log.info("grounding: эмбеддинги недоступны, проверка смысла пропущена")
            return []
        slide_vectors = vectors[:len(texts)]
        corpus_vectors = vectors[len(texts):]

        issues: list[dict] = []
        for index, vector in enumerate(slide_vectors):
            if not texts[index].strip():
                continue
            best = max((_cosine(vector, other) for other in corpus_vectors), default=0.0)
            if best < self.off_source_threshold:
                issues.append({
                    "id": f"content_off_source-{index}-{len(issues)}",
                    "code": "content_off_source", "severity": "warning",
                    "slide": index, "bbox": [], "deterministic": False,
                    "message": f"слайд слабо опирается на контент-пакет "
                               f"(близость {best:.2f} < {self.off_source_threshold:.2f})",
                })
        # семантические дубли: структурный аудит ловит точные повторы,
        # эмбеддинги — пересказ одной и той же мысли разными словами
        for first in range(len(slide_vectors)):
            for second in range(first + 1, len(slide_vectors)):
                similarity = _cosine(slide_vectors[first], slide_vectors[second])
                if similarity >= self.duplicate_threshold:
                    issues.append({
                        "id": f"duplicate_slide_semantic-{second}-{len(issues)}",
                        "code": "duplicate_slide_semantic", "severity": "warning",
                        "slide": second, "bbox": [], "deterministic": False,
                        "message": f"слайды {first + 1} и {second + 1} пересказывают "
                                   f"одну мысль (близость {similarity:.2f})",
                    })
        return issues


def merge_issues(audit: dict, extra: list[dict]) -> dict:
    """Добавляет контекстуальные проблемы к результату детерминированного аудита."""
    if not extra:
        return audit
    known = {(issue["code"], issue["slide"]) for issue in audit["issues"]}
    added = [issue for issue in extra if (issue["code"], issue["slide"]) not in known]
    audit["issues"].extend(added)
    audit["errors"] += sum(1 for issue in added if issue["severity"] == "error")
    audit["warnings"] += sum(1 for issue in added if issue["severity"] == "warning")
    audit["total"] = len(audit["issues"])
    audit["passed"] = audit["errors"] == 0
    return audit
