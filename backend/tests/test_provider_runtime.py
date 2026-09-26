"""Ключ внешнего провайдера через интерфейс (ADR-031).

Ключ живёт только в памяти процесса: не пишется в логи, не возвращается в
API-ответах, не попадает в job.json. Задания при этом действительно идут через
внешний сервис — проверяется локальной заглушкой OpenAI-совместимого API.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.config import get_settings
from app.planner.fallback import FallbackPlanner
from app.runtime_provider import RUNTIME, mask_key

BRIEF = ("Платформа аналитики сократила время подготовки отчётов на 40%, "
         "автоматизировала 12 задач, охватила 5 подразделений.")
SECRET = "sk-secret-abcdef-123456789"


def _deck_json() -> str:
    deck = FallbackPlanner().plan(BRIEF, "", "project")
    return deck.model_dump_json()


class _FakeOpenAI(BaseHTTPRequestHandler):
    """Заглушка: /models, /chat/completions (валидная колода), /embeddings."""

    def log_message(self, *args):  # noqa: D102 — тишина в тестовом выводе
        pass

    def _send(self, payload: dict, status: int = 200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path.endswith("/models"):
            return self._send({"data": [{"id": "qwen3.5-9b"}]})
        return self._send({"error": "not found"}, 404)

    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path.endswith("/chat/completions"):
            return self._send({"choices": [
                {"message": {"role": "assistant", "content": _deck_json()}}]})
        if self.path.endswith("/embeddings"):
            return self._send({"data": [{"embedding": [0.1, 0.2, 0.3]}]})
        return self._send({"error": "not found"}, 404)


@pytest.fixture()
def fake_provider():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOpenAI)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}/v1"
    try:
        yield base
    finally:
        server.shutdown()
        RUNTIME.reset()


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client
    RUNTIME.reset()


def _wait(client: TestClient, job_id: str, limit: float = 120.0) -> dict:
    deadline = time.time() + limit
    while time.time() < deadline:
        state = client.get(f"/api/jobs/{job_id}").json()
        if state["status"] in ("done", "error"):
            return state
        time.sleep(0.3)
    raise AssertionError("задание не завершилось")


def _template() -> bytes:
    from tools.make_fixtures import FIXTURES, build_template

    return build_template(**FIXTURES["synthetic_16x9"])


def test_provider_lifecycle(fake_provider, client):
    """Проверка → применение (маска) → сброс к локальному."""
    check = client.post("/api/provider/test", json={
        "base_url": fake_provider, "api_key": SECRET, "model": "qwen3.5-9b"})
    assert check.status_code == 200 and check.json()["ok"] is True

    applied = client.post("/api/provider/set", json={
        "base_url": fake_provider, "api_key": SECRET, "model": "qwen3.5-9b"})
    body = applied.json()
    assert body["ok"] is True
    assert body["provider"]["source"] == "external"
    assert body["provider"]["masked_key"] == "sk-...789"
    assert body["label"].startswith("внешний/")

    status = client.get("/api/provider/status").json()
    assert status["provider"]["masked_key"] == "sk-...789"
    assert status["provider"]["status"] == "ok"

    reset = client.post("/api/provider/reset").json()
    assert reset["provider"]["source"] == "local"
    assert reset["provider"]["masked_key"] == ""


def test_provider_test_reports_bad_key(client):
    response = client.post("/api/provider/test", json={
        "base_url": "http://127.0.0.1:1/v1", "api_key": "sk-nope", "model": "x"})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "не ответил" in body["message"] or "не отвечает" in body["message"]


def test_generation_uses_runtime_provider(fake_provider, client):
    """Ключ валиден — задание идёт через внешний сервис (used_llm=True)."""
    client.post("/api/provider/set", json={
        "base_url": fake_provider, "api_key": SECRET, "model": "qwen3.5-9b"})
    started = client.post(
        "/api/generate",
        files={"template": ("tpl.pptx", _template(), "application/octet-stream")},
        data={"brief": BRIEF, "vlm": "off"})
    assert started.status_code == 200
    state = _wait(client, started.json()["job_id"])
    assert state["status"] == "done", state
    assert state["summary"]["used_llm"] is True
    assert state["summary"]["planner_label"].startswith("внешний/")


def test_api_key_never_in_response(fake_provider, client):
    client.post("/api/provider/set", json={
        "base_url": fake_provider, "api_key": SECRET, "model": "qwen3.5-9b"})
    for path in ("/api/provider/status", "/api/health"):
        assert SECRET not in client.get(path).text, path
    provider = client.get("/api/provider/status").json()["provider"]
    assert provider["masked_key"] and SECRET not in json.dumps(provider)

    started = client.post(
        "/api/generate",
        files={"template": ("tpl.pptx", _template(), "application/octet-stream")},
        data={"brief": BRIEF, "vlm": "off"})
    state = _wait(client, started.json()["job_id"])
    assert SECRET not in json.dumps(state)
    assert SECRET not in client.get(f"/api/jobs/{state['id']}/info").text


def test_api_key_never_in_logs(fake_provider, client, caplog):
    with caplog.at_level(logging.DEBUG):
        client.post("/api/provider/test", json={
            "base_url": fake_provider, "api_key": SECRET, "model": "qwen3.5-9b"})
        client.post("/api/provider/set", json={
            "base_url": fake_provider, "api_key": SECRET, "model": "qwen3.5-9b"})
        started = client.post(
            "/api/generate",
            files={"template": ("tpl.pptx", _template(), "application/octet-stream")},
            data={"brief": BRIEF, "vlm": "off"})
        state = _wait(client, started.json()["job_id"])
    assert SECRET not in caplog.text

    # job.json на диске тоже не должен содержать ключ
    job_file = get_settings().data_path / "jobs" / state["id"] / "job.json"
    assert job_file.exists()
    assert SECRET not in job_file.read_text(encoding="utf-8")


def test_mask_key_format():
    assert mask_key(SECRET) == "sk-...789"
    assert mask_key("short") == "•" * 5
    assert mask_key("") == ""


def test_provider_validation(client):
    assert client.post("/api/provider/test",
                       json={"base_url": "x", "api_key": "k"}).status_code == 422
    assert client.post("/api/provider/set",
                       json={"base_url": "http://x", "api_key": ""}).json()["ok"] is False
