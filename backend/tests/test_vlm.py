"""Тесты VLM-аудита: контекст, разбор ответа, деградация без Ollama.

Модель не вызывается: подменяются health/generate_text и получение миниатюр.
Проверяется логика — 11 критериев ТЗ, межслайдовый контекст, маппинг нарушений
в проблемы аудита.
"""
from __future__ import annotations

import json

import pytest

from app.audit.vlm import CRITERIA, VlmAudit, violations_to_issues
from app.models.deck import Block, Deck, Slide, SlideType
from app.planner.llm import OllamaClient

ANSWER = {"answers_yes": [1, 2, 3, 5, 6, 7, 8, 9, 10, 11],
          "answers_no": [4], "summary": "число не подтверждено источником"}


class _FakeLlm(OllamaClient):
    def __init__(self, *, online: bool = True, answer: dict | None = None):
        super().__init__(base_url="http://127.0.0.1:1", timeout_s=1)
        self._online = online
        self._answer = answer or ANSWER
        self.prompts: list[str] = []

    def health(self) -> bool:  # noqa: D102
        return self._online

    def generate_text(self, prompt, system=None, **kwargs):  # noqa: D102
        self.prompts.append(prompt)
        return json.dumps(self._answer, ensure_ascii=False)


def _fake_pngs(count: int = 3):
    """Реальные PNG вместо заглушек: модуль уменьшает картинки через Pillow."""
    import io

    from PIL import Image

    def factory(data: bytes, dpi: int = 110) -> list[bytes]:
        out = []
        for _ in range(count):
            buffer = io.BytesIO()
            Image.new("RGB", (320, 180), "white").save(buffer, format="PNG")
            out.append(buffer.getvalue())
        return out

    return factory


@pytest.fixture(autouse=True)
def _llm_enabled(monkeypatch):
    """VLM-аудит должен быть включён: в CI окружение может стоять DISABLE_LLM=true."""
    from app.config import get_settings

    monkeypatch.setenv("DISABLE_LLM", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def deck() -> Deck:
    return Deck(title="Проба VLM", slides=[
        Slide(slide_type=SlideType.TITLE, heading="Титул презентации"),
        Slide(slide_type=SlideType.CONTENT, heading="Средний слайд",
              blocks=[Block(kind="bullets", items=["Тезис"])]),
        Slide(slide_type=SlideType.FINAL, heading="Финал презентации"),
    ])


def test_criteria_match_the_technical_specification():
    """Ровно 11 критериев Приложения 1, порядок и смысл совпадают."""
    assert len(CRITERIA) == 11
    assert CRITERIA[1].startswith("Заголовок содержит вывод")
    assert CRITERIA[4].startswith("Все цифры и факты")
    assert CRITERIA[9].startswith("Слайд на том же языке")
    assert CRITERIA[11].startswith("Слайд связан по логике")


def test_unavailable_without_ollama(deck, monkeypatch):
    audit = VlmAudit(llm=_FakeLlm(online=False), profile={})
    result = audit.audit(b"PK", deck=deck)
    assert result["available"] is False
    assert result["reason"]
    assert result["criteria"] == CRITERIA


def test_audit_returns_violations_with_text(deck, monkeypatch):
    monkeypatch.setattr("app.audit.vlm.pptx_to_pngs", _fake_pngs(3))
    audit = VlmAudit(llm=_FakeLlm(), profile={})
    result = audit.audit(b"PK", deck=deck, source_digest="40% и 12 задач")

    assert result["available"] is True
    assert len(result["slides"]) == 3
    second = result["slides"][1]
    assert second["ok"] is False
    assert second["violations"] == [4]
    assert second["violations_text"] == [CRITERIA[4]]
    assert "источник" in second["summary"]
    assert result["elapsed_s"] >= 0
    assert len(result["per_slide_s"]) == 3
    assert result["prompt"]["hash"], "версия промпта должна попадать в результат"
    assert result["prompt"]["version"] == "2.0"


def test_prompt_contains_neighbour_context(deck, monkeypatch):
    monkeypatch.setattr("app.audit.vlm.pptx_to_pngs", _fake_pngs(3))
    llm = _FakeLlm()
    VlmAudit(llm=llm, profile={}).audit(b"PK", deck=deck, source_digest="выжимка")

    assert "Слайд 2 из 3" in llm.prompts[1]
    assert "Титул презентации" in llm.prompts[1]      # предыдущий
    assert "Финал презентации" in llm.prompts[1]      # следующий
    assert "выжимка" in llm.prompts[1]
    assert "первый слайд" in llm.prompts[0]
    assert "последний слайд" in llm.prompts[2]


def test_violations_become_audit_issues():
    result = {"slides": [{"slide": 2, "violations": [4, 8],
                          "summary": "цифры и опечатки"}]}
    issues = violations_to_issues(result)
    assert [issue["code"] for issue in issues] == ["vlm_criterion_4", "vlm_criterion_8"]
    assert all(issue["severity"] == "warning" for issue in issues)
    assert all(issue["deterministic"] is False for issue in issues)
    assert issues[0]["slide"] == 2
    assert CRITERIA[4] in issues[0]["message"]
    assert "цифры и опечатки" in issues[0]["message"]


def test_unknown_criteria_numbers_are_ignored(deck, monkeypatch):
    """Модель может вернуть мусорные номера — они не должны ломать аудит."""
    monkeypatch.setattr("app.audit.vlm.pptx_to_pngs", _fake_pngs(3))
    llm = _FakeLlm(answer={"answers_yes": [1, 42], "answers_no": [99],
                           "summary": ""})
    result = VlmAudit(llm=llm, profile={}).audit(b"PK", deck=deck)
    assert result["slides"][0]["violations"] == []
    assert result["slides"][0]["criteria_yes"] == [1]
