import pytest

from app.planner.fallback import FallbackPlanner
from app.planner.llm import OllamaClient

BRIEF = ("VK Tech запускает внутреннюю платформу аналитики. За квартал платформа сократила "
         "время подготовки отчётов на 40%, автоматизировала 12 задач, охватила 5 подразделений. "
         "2000 сотрудников используют её еженедельно. План: подключить 10 отделов к концу года "
         "и внедрить ML-предсказания выручки.")
META = {
    "layouts": [{"name": "Титул"}, {"name": "Слайд с заголовком"}],
    "masters": [{"name": "Офисное"}],
    "slide_count": 1,
}


def test_fallback_produces_valid_deck():
    deck = FallbackPlanner().plan(BRIEF, "", "project")
    assert 4 <= len(deck.slides) <= 15
    assert deck.title
    assert 0 <= len(deck.pitch) <= 200
    for s in deck.slides:
        assert s.slide_type.value in ("title", "section", "agenda", "content", "final")
        assert len(s.blocks) <= 6
        for b in s.blocks:
            assert len(b.items) <= 6
            if b.table is not None:
                assert len(b.table.rows) <= 7 and len(b.table.rows[0]) <= 5
            if b.chart is not None:
                assert len(b.chart.series) <= 5


def test_short_brief_still_returns_deck():
    deck = FallbackPlanner().plan("стоп машина купить хлеб", "", "project")
    assert len(deck.slides) >= 4


def test_llm_client_returns_fallback_when_offline():
    llm = OllamaClient(base_url="http://127.0.0.1:1", timeout_s=2)
    assert llm.health() is False
    assert llm.list_models() == [] or isinstance(llm.list_models(), list)


def test_purpose_config_file_exists():
    from app.planner.fallback import PURPOSE_PATH
    import json

    data = json.loads(PURPOSE_PATH.read_text(encoding="utf-8"))
    assert "purposes" in data