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
    monkeypatch.setenv("OPENAI_COMPAT_BASE_URL", "https://gateway.example/v1")
    monkeypatch.setenv("OPENAI_COMPAT_API_KEY", SECRET)
    monkeypatch.setenv("OPENAI_COMPAT_MODEL", "test-model")
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
