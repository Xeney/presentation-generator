"""Тесты HTTP-API: генерация, контент-пакеты, артефакты, авто-фиксы.

LLM отключена фикстурой окружения в conftest (адрес Ollama недоступен),
поэтому пайплайн идёт по офлайн-планировщику — быстро и детерминированно.
"""
from __future__ import annotations

import io
import time

import pytest
from fastapi.testclient import TestClient
from pptx import Presentation

from app.api.main import app

BRIEF = ("Платформа аналитики сократила время подготовки отчётов на 40%, "
         "автоматизировала 12 задач, охватила 5 подразделений. 2000 сотрудников "
         "используют её еженедельно. План: подключить 10 отделов и внедрить "
         "ML-предсказания выручки к концу года.")
PACK = """# Итоги квартала
- Время подготовки отчётов сократилось на 40%
- Автоматизированы 12 рутинных задач
- Охват вырос до 5 подразделений
"""


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def _wait(client: TestClient, job_id: str, limit: float = 120.0) -> dict:
    deadline = time.time() + limit
    while time.time() < deadline:
        state = client.get(f"/api/jobs/{job_id}").json()
        if state["status"] in ("done", "error"):
            return state
        time.sleep(0.3)
    raise AssertionError("задание не завершилось за отведённое время")


@pytest.fixture(scope="module")
def job(client, synthetic_template) -> dict:
    resp = client.post(
        "/api/generate",
        files={"template": ("synthetic.pptx", synthetic_template,
                            "application/octet-stream")},
        data={"brief": BRIEF, "source": "", "purpose": "project"})
    assert resp.status_code == 200, resp.text
    state = _wait(client, resp.json()["job_id"])
    assert state["status"] == "done", state
    return {"id": resp.json()["job_id"], "summary": state["summary"]}


def test_health_reports_llm_state(client):
    data = client.get("/api/health").json()
    assert data["status"] == "ok"
    assert set(data["variants"]) == {"compact", "cards", "split"}
    assert "llm" in data and "vlm" in data


def test_generate_summary_and_stages(client, job):
    summary = job["summary"]
    assert 3 <= summary["slides"] <= 15
    assert summary["used_llm"] is False
    assert {"parse_s", "plan_s", "render_s", "audit_s"} <= set(summary["stages"])
    assert len(summary["variants"]) == 3


def test_artifacts_are_downloadable(client, job):
    for variant in ("compact", "cards", "split"):
        pptx = client.get(f"/api/jobs/{job['id']}/pptx", params={"variant": variant})
        assert pptx.status_code == 200
        assert pptx.content.startswith(b"PK")
        Presentation(io.BytesIO(pptx.content))  # файл открывается

        thumb = client.get(f"/api/jobs/{job['id']}/thumb",
                           params={"variant": variant, "s": 0})
        # миниатюры требуют LibreOffice: допускаем честный 503 в тестовой среде
        assert thumb.status_code in (200, 503)

    html = client.get(f"/api/jobs/{job['id']}/html")
    assert html.status_code == 200 and "<!DOCTYPE html>" in html.text

    info = client.get(f"/api/jobs/{job['id']}/info").json()
    assert info["profile"]["layouts"]
    assert info["deck"]["slides"]
    assert "audit_summary" in info["variants"][0]


def test_audit_endpoint_matches_variants(client, job):
    audit = client.get(f"/api/jobs/{job['id']}/audit", params={"variant": "cards"}).json()
    assert audit["errors"] == 0, audit["issues"][:3]
    for issue in audit["issues"]:
        assert issue["id"] and issue["severity"] in ("error", "warning")


def test_unknown_variant_and_job(client, job):
    assert client.get(f"/api/jobs/{job['id']}/pptx",
                      params={"variant": "nope"}).status_code == 404
    assert client.get("/api/jobs/unknown-id").status_code == 404


def test_content_import_and_preview(client):
    resp = client.post("/api/content/import",
                       files={"file": ("pack.md", PACK.encode("utf-8"), "text/markdown")})
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["kind"] == "text"
    assert payload["stats"]["non_empty"] >= 1
    assert payload["preview"][0]["heading"].startswith("Итоги квартала")

    listed = client.get("/api/content").json()["corpora"]
    assert any(item["id"] == payload["id"] for item in listed)
    assert client.get(f"/api/content/{payload['id']}").status_code == 200


def test_content_import_rejects_unknown_format(client):
    resp = client.post("/api/content/import",
                       files={"file": ("data.xyz", b"binary", "application/octet-stream")})
    assert resp.status_code == 415


def test_generate_validation(client, synthetic_template):
    resp = client.post("/api/generate",
                       files={"template": ("tpl.pptx", b"not a pptx",
                                           "application/octet-stream")},
                       data={"brief": BRIEF})
    assert resp.status_code == 415

    resp = client.post("/api/generate",
                       files={"template": ("tpl.pptx", synthetic_template,
                                           "application/octet-stream")},
                       data={"brief": "мало", "purpose": "project"})
    assert resp.status_code == 422

    resp = client.post("/api/generate",
                       files={"template": ("tpl.pptx", synthetic_template,
                                           "application/octet-stream")},
                       data={"brief": BRIEF, "corpus_id": "нет-такого"})
    assert resp.status_code == 404


def test_provider_switch_is_instant(client):
    """План Б на демо: смена провайдера без перезапуска сервиса."""
    from app.config import get_settings

    original = (get_settings().llm_provider, get_settings().vlm_provider)
    try:
        response = client.post("/api/provider", json={"llm_provider": "offline",
                                                      "vlm_provider": "off"})
        assert response.status_code == 200, response.text
        body = response.json()
        # ярлык честный: при выключенных моделях провайдер называется offline
        assert body["applied"]["llm_provider"] == "offline"
        assert body["llm"]["disabled"] is True
        assert body["vlm"]["provider"] == "off"

        # офлайн-планировщик работает сразу: колода собирается без моделей
        started = client.post(
            "/api/generate",
            files={"template": ("tpl.pptx", _minimal_template(),
                                "application/octet-stream")},
            data={"brief": BRIEF})
        assert started.status_code == 200
        state = _wait(client, started.json()["job_id"])
        assert state["status"] == "done", state
        assert state["summary"]["used_llm"] is False

        # возвращаем провайдера обратно
        back = client.post("/api/provider", json={"llm_provider": "aitunnel",
                                                  "vlm_provider": "aitunnel"})
        assert back.json()["applied"]["llm_provider"] == "aitunnel"
    finally:
        settings = get_settings()
        settings.llm_provider, settings.vlm_provider = original
        settings.disable_llm = False


def test_provider_switch_validates_input(client):
    assert client.post("/api/provider", json={}).status_code == 422
    assert client.post("/api/provider",
                       json={"llm_provider": "нет-такого"}).status_code == 422


def _minimal_template() -> bytes:
    """Синтетический шаблон для тестов переключения (не зависит от файлов VK)."""
    from tools.make_fixtures import FIXTURES, build_template

    return build_template(**FIXTURES["synthetic_16x9"])


def test_fix_endpoint_requires_selection(client, job):
    assert client.post(f"/api/jobs/{job['id']}/fix",
                       json={"issue_ids": [], "variant": "compact"}).status_code == 422


def test_fix_endpoint_returns_report(client, job):
    """Эндпоинт принимает выбранные проблемы; на чистой колоде отчёт пустой."""
    audit = client.get(f"/api/jobs/{job['id']}/audit", params={"variant": "compact"}).json()
    ids = [issue["id"] for issue in audit["issues"]][:5] or ["нет-такой-проблемы"]
    resp = client.post(f"/api/jobs/{job['id']}/fix",
                       json={"issue_ids": ids, "variant": "compact"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) >= {"applied", "skipped", "summary", "version"}
    assert body["version"] >= 2
    # выбранные проблемы либо применены, либо объяснено, почему нет;
    # несовпадающие id игнорируются — это защита от устаревшего UI
    assert all(item["status"] in ("applied", "skipped") for item in body["applied"] + body["skipped"])

    # после фиксов колода остаётся валидной и артефакты обновляются
    state = client.get(f"/api/jobs/{job['id']}").json()
    assert state["summary"]["version"] == body["version"]
    pptx = client.get(f"/api/jobs/{job['id']}/pptx", params={"variant": "compact"})
    assert pptx.status_code == 200 and pptx.content.startswith(b"PK")


def test_health_reports_version(client):
    """Версия сервиса видна в /api/health и совпадает с манифестами.

    Требование сдачи: зафиксированная версия в репозитории; чтобы она не
    разъезжалась с тегом, её читают из одного места (`app.__version__`) и
    сверяют с pyproject.toml и package.json.
    """
    import json
    import re
    from pathlib import Path

    from app import __version__

    response = client.get("/api/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["version"] == __version__
    assert re.fullmatch(r"\d+\.\d+\.\d+", payload["version"])

    backend = Path(__file__).resolve().parents[1]
    pyproject = (backend / "pyproject.toml").read_text(encoding="utf-8")
    assert f'version = "{__version__}"' in pyproject, "pyproject.toml разошёлся с версией"
    package = json.loads((backend.parent / "frontend" / "package.json").read_text(
        encoding="utf-8"))
    assert package["version"] == __version__, "package.json разошёлся с версией"
