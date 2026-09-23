"""Тесты проверки опоры на источник: числа точно, смысл — эмбеддингами."""
from __future__ import annotations

from app.content.importer import import_content_pack
from app.models.deck import Block, Chart, ChartType, Deck, Slide, SlideType, Table
from app.retrieval.grounding import GroundingChecker, merge_issues, normalize_number

PACK = """# Итоги квартала
- Время подготовки отчётов сократилось на 40%
- Автоматизированы 12 рутинных задач
- Охват вырос до 5 подразделений
Платформой пользуются 2000 сотрудников еженедельно.
"""


def _corpus():
    return import_content_pack(PACK.encode("utf-8"), "pack.md")


def _deck(slides: list[Slide]) -> Deck:
    return Deck(title="Проба grounding", slides=[
        Slide(slide_type=SlideType.TITLE, heading="Титул презентации"),
        *slides,
        Slide(slide_type=SlideType.FINAL, heading="Финал презентации"),
    ])


def test_normalize_number():
    assert normalize_number("40,0 %") == "40%"
    assert normalize_number(" 12 ") == "12"
    assert normalize_number("3.50%") == "3.5%"


def test_numbers_from_source_pass():
    deck = _deck([Slide(slide_type=SlideType.CONTENT, heading="Результаты квартала",
                        blocks=[Block(kind="bullets", items=[
                            "Время отчётов сократилось на 40%",
                            "Автоматизированы 12 задач"])])])
    result = GroundingChecker(_corpus(), llm=None).check(deck)
    assert result.available
    assert result.issues == [], result.issues


def test_invented_number_is_flagged():
    deck = _deck([Slide(slide_type=SlideType.CONTENT, heading="Сомнительные цифры",
                        blocks=[Block(kind="bullets", items=[
                            "Выручка выросла на 87%",
                            "Охват — 5 подразделений"])])])
    result = GroundingChecker(_corpus(), llm=None).check(deck)
    codes = {issue["code"] for issue in result.issues}
    assert "fact_unverified" in codes
    message = next(i["message"] for i in result.issues if i["code"] == "fact_unverified")
    assert "87%" in message
    assert "5" not in message


def test_numbers_in_tables_and_charts_are_checked():
    deck = _deck([Slide(slide_type=SlideType.CONTENT, heading="Данные слайда",
                        blocks=[
                            Block(kind="table", table=Table(
                                header=["Метрика", "Значение"],
                                rows=[["Конверсия", "3.7%"]])),
                            Block(kind="chart", chart=Chart(
                                type=ChartType.COLUMN, categories=["Q1"],
                                series=[{"name": "Выручка", "values": [999.0]}]))])])
    codes = {issue["code"] for issue in GroundingChecker(_corpus(), llm=None).check(deck).issues}
    assert "fact_unverified" in codes


def test_without_corpus_check_is_unavailable():
    deck = _deck([Slide(slide_type=SlideType.CONTENT, heading="Любой слайд",
                        blocks=[Block(kind="bullets", items=["Тезис"])])])
    result = GroundingChecker(None, llm=None).check(deck)
    assert result.available is False
    assert "контент-пакет" in result.reason


class _FakeEmbedder:
    """Заглушка Ollama: эмбеддинги задаются тестом явно."""

    def __init__(self, vectors: list[list[float]]):
        self.vectors = vectors
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        self.calls.append(list(texts))
        return self.vectors


def test_semantic_duplicates_and_off_source_with_embeddings():
    deck = _deck([
        Slide(slide_type=SlideType.CONTENT, heading="Слайд про отчёты",
              blocks=[Block(kind="bullets", items=["Отчёты быстрее"])]),
        Slide(slide_type=SlideType.CONTENT, heading="Другой слайд",
              blocks=[Block(kind="bullets", items=["Совсем другая мысль"])]),
    ])
    # 4 текста: 2 слайда (титул, контент, контент, финал = 4) + 4 строки корпуса
    vectors = [
        [1.0, 0.0],   # титул
        [1.0, 0.0],   # контентный слайд 1 — близнец слайда 2
        [1.0, 0.0],   # контентный слайд 2 — близнец слайда 1
        [0.0, 1.0],   # финал
        [1.0, 0.0],   # строка корпуса 1
        [0.9, 0.1],   # строка корпуса 2
        [0.8, 0.2],   # строка корпуса 3
        [0.7, 0.3],   # строка корпуса 4
    ]
    checker = GroundingChecker(_corpus(), llm=_FakeEmbedder(vectors),
                              off_source_threshold=0.9, duplicate_threshold=0.9)
    codes = [issue["code"] for issue in checker.check(deck).issues]
    assert "duplicate_slide_semantic" in codes
    assert "content_off_source" in codes


def test_merge_issues_updates_counts():
    audit = {"passed": True, "errors": 0, "warnings": 0, "total": 0, "issues": []}
    extra = [
        {"id": "a", "code": "fact_unverified", "severity": "warning", "slide": 1,
         "message": "", "bbox": [], "deterministic": True},
        {"id": "b", "code": "content_off_source", "severity": "warning", "slide": 2,
         "message": "", "bbox": [], "deterministic": False},
    ]
    merged = merge_issues(audit, extra)
    assert merged["total"] == 2 and merged["warnings"] == 2
    assert merged["passed"] is True  # предупреждения не делают колоду невалидной

    # повторное слияние тех же проблем не дублирует их
    merge_issues(merged, extra)
    assert merged["total"] == 2
