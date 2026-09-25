"""Тесты провайдеров LLM: выбор клиента, OpenAI-совместимый шлюз, секретность.

Внешний шлюз (например, OpenCode Zen) — режим разработки без GPU (ADR-019).
Сеть не используется: HTTP-вызовы подменяются, поэтому тесты не тратят кредиты
и не зависят от доступности провайдера.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import get_settings
from app.planner.llm import (
    LlmError,
    OllamaClient,
    OpenAICompatClient,
    get_llm_client,
)

SECRET = "oc_sk_test_key_do_not_leak"


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None,
                 text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or json.dumps(self._payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture
def compat(monkeypatch):
    """Клиент шлюза с подменёнными настройками и перехваченными запросами."""
    monkeypatch.setenv("LLM_PROVIDER", "openai_compat")
    # VLM-провайдер не задан: должен унаследоваться от LLM-провайдера
    monkeypatch.setenv("VLM_PROVIDER", "")
    monkeypatch.setenv("OPENAI_COMPAT_BASE_URL", "https://gateway.example/v1")
    monkeypatch.setenv("OPENAI_COMPAT_API_KEY", SECRET)
    monkeypatch.setenv("OPENAI_COMPAT_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_COMPAT_VLM_MODEL", "")
    get_settings.cache_clear()

    calls: list[dict] = []

    def fake_get(url, headers=None, timeout=None):
        calls.append({"method": "GET", "url": url, "headers": headers or {}})
        return _FakeResponse(200, {"data": [{"id": "test-model"}, {"id": "other"}]})

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"method": "POST", "url": url, "headers": headers or {},
                      "payload": json})
        if json and json.get("model") == "broken-model":
            return _FakeResponse(403, text='{"error": "Model access is disabled"}')
        if "embeddings" in url:
            return _FakeResponse(200, {"data": [{"embedding": [0.1, 0.2]}]})
        return _FakeResponse(200, {"choices": [{"message": {"content": '{"ok": true}'}}]})

    monkeypatch.setattr("app.planner.llm.httpx.get", fake_get)
    monkeypatch.setattr("app.planner.llm.httpx.post", fake_post)
    yield OpenAICompatClient(), calls
    get_settings.cache_clear()


# --------------------------------------------------------------- выбор клиента
def test_factory_returns_ollama_by_default(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    get_settings.cache_clear()
    try:
        assert isinstance(get_llm_client(), OllamaClient)
    finally:
        get_settings.cache_clear()


def test_factory_returns_compat_when_configured(compat):
    client, _ = compat
    assert isinstance(client, OpenAICompatClient)


def test_factory_ignores_compat_without_base_url(monkeypatch):
    """Пустой адрес шлюза — это не «включённый провайдер», а опечатка в конфиге."""
    monkeypatch.setenv("LLM_PROVIDER", "openai_compat")
    monkeypatch.setenv("OPENAI_COMPAT_BASE_URL", "")
    get_settings.cache_clear()
    try:
        assert isinstance(get_llm_client(), OllamaClient)
        assert get_settings().uses_external_provider is False
    finally:
        get_settings.cache_clear()


def test_active_models_switch_with_provider(compat):
    settings = get_settings()
    assert settings.uses_external_provider is True
    assert settings.active_llm_model == "test-model"
    # VLM и эмбеддинги не заданы для шлюза — остаются локальные значения
    assert settings.active_vlm_model == settings.vlm_model
    assert settings.active_embedding_model == settings.embedding_model


# --------------------------------------------------------------- протокол
def test_health_and_models(compat):
    client, calls = compat
    assert client.health() is True
    assert client.list_models() == ["test-model", "other"]
    assert calls[0]["url"].endswith("/models")


def test_generate_text_sends_bearer_and_json_mode(compat):
    client, calls = compat
    text = client.generate_text("привет", "система", temperature=0.1, json_mode=True)
    assert client._parse_json(text) == {"ok": True}

    payload = calls[-1]["payload"]
    assert payload["model"] == "test-model"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][1]["content"] == "привет"
    assert calls[-1]["headers"]["Authorization"] == f"Bearer {SECRET}"


def test_generate_text_sends_images_for_vlm(compat):
    client, calls = compat
    client.generate_text("оцени слайд", images=["AAA="], json_mode=False)
    content = calls[-1]["payload"]["messages"][-1]["content"]
    assert content[0] == {"type": "text", "text": "оцени слайд"}
    assert content[1]["image_url"]["url"] == "data:image/png;base64,AAA="
    assert "response_format" not in calls[-1]["payload"]


def test_provider_error_is_clear_and_hides_key(compat):
    client, _ = compat
    with pytest.raises(LlmError) as error:
        client.generate_text("привет", model="broken-model")
    message = str(error.value)
    assert "403" in message and "Model access is disabled" in message
    assert SECRET not in message


def test_generate_json_returns_none_instead_of_raising(compat):
    client, _ = compat
    assert client.generate_json("привет", model="broken-model") is None


def test_embed_returns_vectors_and_survives_failure(compat, monkeypatch):
    client, _ = compat
    assert client.embed(["текст"]) == [[0.1, 0.2]]

    def broken_post(*args, **kwargs):
        raise RuntimeError("сеть недоступна")

    monkeypatch.setattr("app.planner.llm.httpx.post", broken_post)
    assert client.embed(["текст"]) == []


# ------------------------------------------------- деградация планировщика
def test_planner_falls_back_when_provider_refuses(compat):
    """Отказ шлюза не должен ронять задание: планировщик уходит в офлайн-режим."""
    from app.planner.planner import Planner

    client, _ = compat
    result = Planner(llm=client).plan(
        "Платформа аналитики сократила время отчётов на 40% и охватила 5 подразделений.",
        "", "project")
    assert result.used_llm is False
    assert len(result.deck.slides) >= 4


# ---------------------------------------------------------------- AITUNNEL
AITUNNEL_KEY = "sk-aitunnel-test-0123456789abcdef"

PLAN_JSON = {
    "title": "Платформа аналитики",
    "language": "ru",
    "slides": [
        {"slide_type": "title", "heading": "Платформа аналитики ускорила отчёты",
         "subheading": "Итоги квартала"},
        {"slide_type": "content", "heading": "Отчёты готовятся втрое быстрее",
         "blocks": [{"kind": "bullets", "items": ["Минус 40% времени", "12 задач"]}]},
        {"slide_type": "final", "heading": "Следующие шаги"},
    ],
}


@pytest.fixture
def aitunnel(monkeypatch):
    """AITUNNEL с подменёнными HTTP-вызовами: ключ тестовый, сеть не нужна."""
    monkeypatch.setenv("LLM_PROVIDER", "aitunnel")
    monkeypatch.setenv("VLM_PROVIDER", "aitunnel")
    monkeypatch.setenv("AITUNNEL_BASE_URL", "https://api.aitunnel.ru/v1")
    monkeypatch.setenv("AITUNNEL_API_KEY", AITUNNEL_KEY)
    monkeypatch.setenv("AITUNNEL_LLM_MODEL", "qwen3.5-9b")
    monkeypatch.setenv("AITUNNEL_VLM_MODEL", "qwen3.5-9b")
    monkeypatch.setenv("AITUNNEL_TIMEOUT_SEC", "120")
    monkeypatch.setenv("AITUNNEL_MAX_RETRIES", "1")
    monkeypatch.setenv("DEMO_MODE", "false")
    # окружение CI может выключать модели — для этого теста они нужны включёнными
    monkeypatch.setenv("DISABLE_LLM", "false")
    get_settings.cache_clear()

    calls: list[dict] = []
    state: dict = {"failures": 0, "status": 200, "body": "", "broken_models": set(),
                   "schema_echo_models": set()}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "headers": headers or {}, "payload": json})
        model = (json or {}).get("model", "")
        if model in state.get("schema_echo_models", ()):
            # модель вернула саму схему вместо данных
            return _FakeResponse(200, {"choices": [{"message": {
                "content": '{"$defs": {}, "properties": {}}'}}]})
        if model in state.get("broken_models", ()):
            return _FakeResponse(400, text='{"error":"model unavailable"}')
        if state["failures"] > 0:
            state["failures"] -= 1
            return _FakeResponse(503, text='{"error":"temporary"}')
        if state["status"] != 200:
            return _FakeResponse(state["status"], text=state["body"])
        return _FakeResponse(200, {"choices": [{"message": {
            "content": __import__("json").dumps(PLAN_JSON, ensure_ascii=False)}}]})

    def fake_get(url, headers=None, timeout=None):
        calls.append({"url": url, "headers": headers or {}})
        if state["failures"] > 0:
            state["failures"] -= 1
            return _FakeResponse(503, text='{"error":"temporary"}')
        if state["status"] != 200:
            return _FakeResponse(state["status"], text=state["body"])
        return _FakeResponse(200, {"data": [{"id": "qwen3.5-9b"}, {"id": "qwen3-max"}]})

    monkeypatch.setattr("app.planner.llm.httpx.post", fake_post)
    monkeypatch.setattr("app.planner.llm.httpx.get", fake_get)
    yield state, calls
    get_settings.cache_clear()


def test_aitunnel_is_selected_and_configured(aitunnel):
    from app.planner.llm import aitunnel_client

    client = aitunnel_client()
    assert isinstance(client, OpenAICompatClient)
    assert client.base_url == "https://api.aitunnel.ru/v1"
    assert client.provider_name == "AITUNNEL"
    assert client.max_retries == 1
    assert client.timeout.read == 120
    assert get_settings().active_llm_provider == "aitunnel"
    assert get_settings().planner_label == "aitunnel/qwen3.5-9b"
    assert get_settings().vlm_label == "aitunnel/qwen3.5-9b"


def test_aitunnel_planner_returns_valid_plan(aitunnel):
    """С мок-сервером AITUNNEL планировщик собирает валидную колоду."""
    from app.planner.llm import aitunnel_client
    from app.planner.planner import Planner

    result = Planner(llm=aitunnel_client()).plan(
        "Платформа аналитики: отчёты быстрее на 40%, 12 задач, 5 подразделений.",
        "", "project")

    assert result.used_llm is True
    assert result.provider == "aitunnel"
    assert result.model == "qwen3.5-9b"
    assert result.label == "aitunnel/qwen3.5-9b"
    assert len(result.deck.slides) == 3
    assert result.deck.slides[0].slide_type.value == "title"
    assert result.to_dict()["label"] == "aitunnel/qwen3.5-9b"


def test_fallback_model_takes_over_when_primary_fails(aitunnel, monkeypatch):
    """Если основная модель не даёт колоду, пробуем запасную (та же лицензия)."""
    monkeypatch.setenv("AITUNNEL_FALLBACK_MODEL", "qwen3.5-27b")
    get_settings.cache_clear()
    from app.planner.llm import aitunnel_client
    from app.planner.planner import Planner

    state, calls = aitunnel
    state["schema_echo_models"] = {"qwen3.5-9b"}

    result = Planner(llm=aitunnel_client()).plan(
        "Платформа аналитики ускорила отчёты на 40% и охватила 5 подразделений.",
        "", "project")

    assert result.used_llm is True
    assert result.model == "qwen3.5-27b"
    # три попытки основной моделью + одна запасной
    assert result.attempts == 4
    assert any(call.get("payload", {}).get("model") == "qwen3.5-27b" for call in calls)
    get_settings.cache_clear()


def test_normalization_is_reported_in_result(aitunnel, monkeypatch):
    """Правки структуры не скрываются: они попадают в результат задания."""
    from app.planner.llm import aitunnel_client
    from app.planner.planner import Planner

    state, _ = aitunnel
    # колода без титульного слайда — нормализация обязана его поставить
    state["schema_echo_models"] = set()
    def fake_normalize(data, max_slides=15):
        slides = list(data["slides"])
        slides[0] = {**slides[0], "slide_type": "content"}
        slides.insert(0, {"slide_type": "title", "heading": "Перенесённый титул"})
        return {**data, "slides": slides}, ["титульный слайд перенесён в начало"]

    monkeypatch.setattr("app.planner.planner.normalize_or_report", fake_normalize)
    result = Planner(llm=aitunnel_client()).plan(
        "Платформа аналитики ускорила отчёты на 40%.", "", "project")
    assert result.normalizations == ["титульный слайд перенесён в начало"]
    assert result.to_dict()["normalizations"]
    get_settings.cache_clear()


def test_aitunnel_missing_key_is_explained(monkeypatch):
    """Без ключа — понятная ошибка и мягкий откат на Ollama."""
    monkeypatch.setenv("LLM_PROVIDER", "aitunnel")
    monkeypatch.setenv("AITUNNEL_API_KEY", "")
    get_settings.cache_clear()
    try:
        from app.planner.llm import aitunnel_client, get_llm_client

        assert isinstance(get_llm_client(), OllamaClient)
        assert get_settings().active_llm_provider == "ollama"

        with pytest.raises(LlmError) as error:
            aitunnel_client().list_models()
        message = str(error.value)
        assert "AITUNNEL_API_KEY" in message
        assert ".env" in message
    finally:
        get_settings.cache_clear()


def test_aitunnel_retries_once_on_server_error(aitunnel):
    from app.planner.llm import aitunnel_client

    state, calls = aitunnel
    state["failures"] = 1
    client = aitunnel_client()
    client.retry_backoff_s = 0.0

    assert client.health() is True
    assert len(calls) == 2, "после 503 должен быть один повтор"


def test_aitunnel_does_not_retry_client_error(aitunnel):
    """4xx (кроме 408/409/425/429) повторять бессмысленно."""
    from app.planner.llm import aitunnel_client

    state, calls = aitunnel
    state["status"] = 402
    state["body"] = '{"error":"Insufficient account funds"}'
    client = aitunnel_client()
    client.retry_backoff_s = 0.0

    with pytest.raises(LlmError) as error:
        client.generate_text("привет")
    assert "402" in str(error.value)
    assert len(calls) == 1


def test_aitunnel_disables_thinking_by_default(aitunnel):
    """Qwen3.5 «размышляет» по умолчанию: для JSON-ответов thinking выключаем.

    Проверено на реальном AITUNNEL: chat_template_kwargs и /no_think шлюз
    игнорирует, а reasoning_effort="none" работает (39 c и пустой ответ против
    0.9 c с корректным JSON).
    """
    from app.planner.llm import aitunnel_client

    _, calls = aitunnel
    client = aitunnel_client()
    client.generate_text("привет")
    payload = calls[-1]["payload"]
    assert payload["reasoning_effort"] == "none"
    assert payload["max_tokens"] == 4096
    assert payload["response_format"] == {"type": "json_object"}


def test_thinking_flag_can_be_disabled(monkeypatch):
    monkeypatch.setenv("AITUNNEL_DISABLE_THINKING", "false")
    monkeypatch.setenv("AITUNNEL_MAX_TOKENS", "0")
    monkeypatch.setenv("AITUNNEL_API_KEY", AITUNNEL_KEY)
    get_settings.cache_clear()
    try:
        from app.planner.llm import aitunnel_client

        assert aitunnel_client().extra_payload == {}
    finally:
        get_settings.cache_clear()


def test_unsupported_extra_fields_are_dropped_on_retry(aitunnel):
    """Если шлюз не знает reasoning_effort — повторяем без лишних полей."""
    from app.planner.llm import aitunnel_client

    state, calls = aitunnel
    client = aitunnel_client()
    client.retry_backoff_s = 0.0
    state["status"] = 400
    state["body"] = '{"error":"unknown field reasoning_effort"}'

    with pytest.raises(LlmError):
        client.generate_text("привет")

    assert len(calls) >= 2, "после 400 должен быть повтор без лишних полей"
    assert "reasoning_effort" not in calls[-1]["payload"]
    assert "max_tokens" not in calls[-1]["payload"]
    assert "response_format" not in calls[-1]["payload"]


def test_json_schema_is_used_when_available(aitunnel):
    """Структурированный вывод надёжнее: модель не может вернуть схему вместо данных."""
    from app.planner.llm import aitunnel_client

    _, calls = aitunnel
    aitunnel_client().generate_text("привет", format_schema={"type": "object"})
    assert calls[-1]["payload"]["response_format"]["type"] == "json_schema"


def test_timeout_is_not_retried(monkeypatch):
    """Повтор таймаута стоит ещё одного ожидания — бюджет важнее."""
    monkeypatch.setenv("AITUNNEL_API_KEY", AITUNNEL_KEY)
    monkeypatch.setenv("AITUNNEL_MAX_RETRIES", "1")
    get_settings.cache_clear()
    try:
        import httpx

        from app.planner.llm import aitunnel_client

        calls = {"n": 0}

        def timeout_post(*args, **kwargs):
            calls["n"] += 1
            raise httpx.ReadTimeout("timed out")

        monkeypatch.setattr("app.planner.llm.httpx.post", timeout_post)
        with pytest.raises(LlmError) as error:
            aitunnel_client().generate_text("привет")
        assert error.value.retryable is False
        assert calls["n"] == 1, "таймаут не должен повторяться"
    finally:
        get_settings.cache_clear()


def test_aitunnel_key_is_masked_in_logs(aitunnel, caplog):
    import logging

    from app.planner.llm import aitunnel_client

    state, _ = aitunnel
    state["failures"] = 1
    client = aitunnel_client()
    client.retry_backoff_s = 0.0

    with caplog.at_level(logging.WARNING, logger="llm"):
        client.health()

    assert AITUNNEL_KEY not in caplog.text
    assert AITUNNEL_KEY[:8] in caplog.text  # маска присутствует


def test_aitunnel_key_not_in_error_text(aitunnel):
    from app.planner.llm import aitunnel_client

    state, _ = aitunnel
    state["status"] = 403
    state["body"] = '{"error":"Model access is disabled"}'
    with pytest.raises(LlmError) as error:
        aitunnel_client().generate_text("привет")
    assert AITUNNEL_KEY not in str(error.value)


def test_vlm_client_honours_vlm_provider(aitunnel, monkeypatch):
    from app.planner.llm import get_vlm_client

    assert isinstance(get_vlm_client(), OpenAICompatClient)

    monkeypatch.setenv("VLM_PROVIDER", "off")
    get_settings.cache_clear()
    assert get_vlm_client() is None
    assert get_settings().vlm_label == "off"

    monkeypatch.setenv("VLM_PROVIDER", "ollama")
    get_settings.cache_clear()
    assert isinstance(get_vlm_client(), OllamaClient)
    get_settings.cache_clear()


def test_vlm_audit_reports_off_reason(monkeypatch):
    monkeypatch.setenv("VLM_PROVIDER", "off")
    get_settings.cache_clear()
    try:
        from app.audit.vlm import VlmAudit

        audit = VlmAudit(profile={})
        assert audit.available() is False
        result = audit.audit(b"PK", deck=None)
        assert result["available"] is False
        assert "off" in result["reason"]
    finally:
        get_settings.cache_clear()


def test_demo_mode_disables_silent_fallback(aitunnel, monkeypatch):
    """DEMO_MODE=true: недоступная модель — ошибка, а не офлайн-колода."""
    from app.planner.llm import aitunnel_client
    from app.planner.planner import Planner

    monkeypatch.setenv("DEMO_MODE", "true")
    get_settings.cache_clear()

    state, _ = aitunnel
    state["status"] = 402
    state["body"] = '{"error":"Insufficient account funds"}'

    with pytest.raises(LlmError) as error:
        Planner(llm=aitunnel_client()).plan("Платформа аналитики ускорила отчёты.", "", "project")
    assert "DEMO_MODE" in str(error.value)
    get_settings.cache_clear()


# --------------------------------------------------------------- секретность
def test_env_files_are_ignored_by_git():
    """Секреты живут только в .env: он обязан быть в .gitignore."""
    root = Path(__file__).resolve().parents[2]
    ignored = (root / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in ignored
    assert "!.env.example" in ignored
    assert (root / ".env.example").exists()


def test_example_env_has_no_secret_values():
    """В примере окружения не должно быть ключей — только пустые заготовки."""
    example = (Path(__file__).resolve().parents[2] / ".env.example").read_text(
        encoding="utf-8")
    assert "oc_sk_" not in example
    assert "OPENAI_COMPAT_API_KEY=\n" in example or "OPENAI_COMPAT_API_KEY=" in example
