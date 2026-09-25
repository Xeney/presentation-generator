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


def test_slow_model_respects_stage_budget(deck, monkeypatch):
    """Медленная модель (CPU) не растягивает стадию: остаток бюджета — в таймаут.

    Раньше бюджет только помечал слайды, но запросы продолжали висеть до
    собственного таймаута: стадия VLM на CPU занимала 15 минут вместо трёх.
    """
    import time

    from app.audit import vlm as vlm_module

    class _SlowLlm(_FakeLlm):
        def __init__(self):
            super().__init__()
            self.timeouts: list[float] = []

        def generate_text(self, prompt, system=None, **kwargs):  # noqa: D102
            self.timeouts.append(float(kwargs.get("timeout_s") or 30))
            time.sleep(self.timeouts[-1])
            return json.dumps(ANSWER)

    monkeypatch.setattr("app.audit.vlm.pptx_to_pngs", _fake_pngs(4))
    monkeypatch.setattr(vlm_module, "get_settings", lambda: _BudgetSettings())
    # минимальные бюджет и лимит запроса уменьшены, чтобы тест не спал минуту
    monkeypatch.setattr(vlm_module, "MIN_STAGE_BUDGET_S", 0.4)
    monkeypatch.setattr(vlm_module, "MIN_REQUEST_TIMEOUT_S", 0.2)
    llm = _SlowLlm()
    audit = VlmAudit(llm=llm, profile={})
    started = time.perf_counter()
    result = audit.audit(b"PK", deck=deck)
    elapsed = time.perf_counter() - started

    assert result["available"] is True
    # 4 слайда, 2 воркера, бюджет 0.4 с: первая волна получает весь бюджет,
    # вторая — минимум 0.2 с; стадия свободна через ~0.6 с
    assert result["elapsed_s"] < 1, "стадия обязана уложиться в бюджет + одну волну"
    assert elapsed < 2
    assert llm.timeouts, "запросы должны получать остаток бюджета"
    assert max(llm.timeouts) <= 0.4 + 0.01, "запрос не может ждать дольше бюджета"
    assert min(llm.timeouts) == pytest.approx(0.2), \
        "запросы после дедлайна получают минимальный лимит, а не полный таймаут"
    assert result["per_slide_s"], "тайминги слайдов должны сохраняться"


class _BudgetSettings:
    """Настройки с крошечным бюджетом: два воркера, четыре слайда."""
    vlm_audit_enabled = True
    vlm_audit_budget_s = 0.4
    vlm_audit_workers = 2
    vlm_image_max_px = 0
    disable_llm = False
    active_vlm_provider = "ollama"
    active_vlm_model = "fake-vlm"
    vlm_label = "fake-vlm"


def test_stage_stops_when_model_never_answers(deck, monkeypatch):
    """Ни один слайд не ответил — стадия останавливается, а не ждёт все слайды.

    На CPU (или при недоступной модели) это экономит минуты: остальные слайды
    честно помечаются «не проверено».
    """
    import time

    from app.audit import vlm as vlm_module

    class _DeadLlm(_FakeLlm):
        def generate_text(self, prompt, system=None, **kwargs):  # noqa: D102
            time.sleep(kwargs.get("timeout_s") or 30)
            raise TimeoutError("модель не ответила")

    monkeypatch.setattr("app.audit.vlm.pptx_to_pngs", _fake_pngs(9))
    monkeypatch.setattr(vlm_module, "get_settings", lambda: _BudgetSettings())
    monkeypatch.setattr(vlm_module, "MIN_STAGE_BUDGET_S", 0.2)
    monkeypatch.setattr(vlm_module, "MIN_REQUEST_TIMEOUT_S", 0.1)
    audit = VlmAudit(llm=_DeadLlm(), profile={})
    started = time.perf_counter()
    result = audit.audit(b"PK", deck=deck)
    elapsed = time.perf_counter() - started

    assert result["available"] is False
    assert "не ответила" in result["reason"]
    # 9 слайдов, 2 воркера: после двух отказов стадия останавливается,
    # а не ждёт ещё семь слайдов
    assert elapsed < 2, f"стадия ждала лишние слайды: {elapsed:.1f} c"


def test_unknown_criteria_numbers_are_ignored(deck, monkeypatch):
    """Модель может вернуть мусорные номера — они не должны ломать аудит."""
    monkeypatch.setattr("app.audit.vlm.pptx_to_pngs", _fake_pngs(3))
    llm = _FakeLlm(answer={"answers_yes": [1, 42], "answers_no": [99],
                           "summary": ""})
    result = VlmAudit(llm=llm, profile={}).audit(b"PK", deck=deck)
    assert result["slides"][0]["violations"] == []
    assert result["slides"][0]["criteria_yes"] == [1]
