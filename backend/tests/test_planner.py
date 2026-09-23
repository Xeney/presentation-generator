"""Тесты планировщика: валидность офлайн-колоды и корректная деградация LLM-клиента."""
import json

import pytest

from app.planner.fallback import FallbackPlanner, PURPOSE_PATH
from app.planner.llm import OllamaClient, OllamaError

BRIEF = ("VK Tech запускает внутреннюю платформу аналитики. За квартал платформа сократила "
         "время подготовки отчётов на 40%, автоматизировала 12 задач, охватила 5 подразделений. "
         "2000 сотрудников используют её еженедельно. План: подключить 10 отделов к концу года "
         "и внедрить ML-предсказания выручки.")


def test_fallback_produces_valid_deck():
    deck = FallbackPlanner().plan(BRIEF, "", "project")
    assert 4 <= len(deck.slides) <= 15
    assert deck.title
    assert 0 <= len(deck.pitch) <= 200
    assert deck.slides[0].slide_type.value == "title"
    for s in deck.slides:
        assert s.slide_type.value in ("title", "section", "agenda", "content", "final")
        assert len(s.blocks) <= 6
        for b in s.blocks:
            assert len(b.items) <= 6
            if b.table is not None:
                assert len(b.table.rows) <= 6 and len(b.table.header) <= 5
            if b.chart is not None:
                assert len(b.chart.series) <= 5


@pytest.mark.parametrize("purpose", ["feature", "product", "project", "initiative"])
def test_fallback_covers_all_purposes(purpose):
    deck = FallbackPlanner().plan(BRIEF, "", purpose)
    assert len(deck.slides) >= 4
    assert deck.slides[0].slide_type.value == "title"


def test_short_brief_still_returns_deck():
    deck = FallbackPlanner().plan("стоп машина купить хлеб", "", "project")
    assert len(deck.slides) >= 4


def test_llm_client_degrades_when_offline():
    """Без Ollama клиент обязан явно сообщать о недоступности и не бросать исключения
    из «мягких» методов (generate_json возвращает None — это путь к fallback)."""
    llm = OllamaClient(base_url="http://127.0.0.1:1", timeout_s=2)
    assert llm.health() is False
    with pytest.raises(OllamaError):
        llm.list_models()
    assert llm.generate_json("привет") is None
    assert llm.embed(["текст"]) == []


def test_planner_falls_back_without_llm(monkeypatch):
    from app.planner.planner import Planner

    monkeypatch.setenv("DISABLE_LLM", "true")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        result = Planner().plan(BRIEF, "", "project", profile={})
        assert result.used_llm is False
        assert len(result.deck.slides) >= 4
    finally:
        get_settings.cache_clear()


def test_purpose_config_file_exists():
    data = json.loads(PURPOSE_PATH.read_text(encoding="utf-8"))
    assert "purposes" in data
    assert set(data["purposes"]) >= {"feature", "product", "project", "initiative"}
