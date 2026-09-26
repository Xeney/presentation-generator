"""Режимы сборки: классический, HTML+CSS и оба (ADR-035, ADR-036)."""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pptx import Presentation
import io

from app.api.main import app

BRIEF = ("Платформа аналитики сократила время подготовки отчётов на 40%, "
         "автоматизировала 12 задач, охватила 5 подразделений.")

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def _wait(client: TestClient, job_id: str, limit: float = 180.0) -> dict:
    deadline = time.time() + limit
    while time.time() < deadline:
        state = client.get(f"/api/jobs/{job_id}").json()
        if state["status"] in ("done", "error"):
            return state
        time.sleep(0.3)
    raise AssertionError("задание не завершилось")


@pytest.fixture(scope="module")
def both_job(client, synthetic_template) -> dict:
    started = client.post(
        "/api/generate",
        files={"template": ("tpl.pptx", synthetic_template, "application/octet-stream")},
        data={"brief": BRIEF, "vlm": "off", "render_mode": "both"})
    assert started.status_code == 200, started.text
    state = _wait(client, started.json()["job_id"])
    assert state["status"] == "done", state
    return {"id": started.json()["job_id"], "summary": state["summary"]}


def test_both_modes_produce_downloadable_files(client, both_job):
    job_id = both_job["id"]
    assert both_job["summary"]["render_mode"] == "both"

    native = client.get(f"/api/jobs/{job_id}/pptx", params={"variant": "compact"})
    assert native.status_code == 200 and native.content.startswith(b"PK")
    assert native.headers["content-disposition"] == \
        'attachment; filename="presentation_compact.pptx"'
    Presentation(io.BytesIO(native.content))

    html_pptx = client.get(f"/api/jobs/{job_id}/pptx",
                           params={"variant": "compact", "render": "html"})
    assert html_pptx.status_code == 200 and html_pptx.content.startswith(b"PK")
    assert html_pptx.headers["content-disposition"] == \
        'attachment; filename="presentation_compact_html.pptx"'
    Presentation(io.BytesIO(html_pptx.content))

    page = client.get(f"/api/jobs/{job_id}/html", params={"variant": "compact"})
    assert page.status_code == 200
    assert "<!DOCTYPE html>" in page.text
    assert 'data-variant="compact"' in page.text
    assert page.headers["content-disposition"].startswith("inline")


def test_audit_runs_on_both_renderers(client, both_job):
    job_id = both_job["id"]
    native = client.get(f"/api/jobs/{job_id}/audit", params={"variant": "compact"}).json()
    html = client.get(f"/api/jobs/{job_id}/audit",
                      params={"variant": "compact", "render": "html"}).json()
    assert native["errors"] == 0, native["issues"][:3]
    assert html["errors"] == 0, html["issues"][:3]
    assert native["total"] >= 0 and html["total"] >= 0
    info = client.get(f"/api/jobs/{job_id}/info").json()
    assert info["render_mode"] == "both"


def test_html_mode_primary_is_html(client, synthetic_template):
    started = client.post(
        "/api/generate",
        files={"template": ("tpl.pptx", synthetic_template, "application/octet-stream")},
        data={"brief": BRIEF, "vlm": "off", "render_mode": "html"})
    state = _wait(client, started.json()["job_id"])
    assert state["status"] == "done", state
    assert state["summary"]["render_mode"] == "html"
    job_id = started.json()["job_id"]
    pptx = client.get(f"/api/jobs/{job_id}/pptx", params={"variant": "cards"})
    assert pptx.status_code == 200 and pptx.content.startswith(b"PK")
    assert _html_ok(client, job_id, "cards")


def _html_ok(client: TestClient, job_id: str, variant: str) -> bool:
    page = client.get(f"/api/jobs/{job_id}/html", params={"variant": variant})
    return page.status_code == 200 and "<!DOCTYPE html>" in page.text


def test_ui_render_mode_selector():
    """В интерфейсе три режима сборки, по умолчанию — классический."""
    form = (FRONTEND / "components" / "generator-form.tsx").read_text(encoding="utf-8")
    for mode in ('id: "native"', 'id: "html"', 'id: "both"'):
        assert mode in form, mode
    assert "Способ сборки" in form
    page = (FRONTEND / "app" / "page.tsx").read_text(encoding="utf-8")
    assert 'useState<RenderMode>("native")' in page
    advanced = (FRONTEND / "components" / "advanced-settings.tsx").read_text(encoding="utf-8")
    assert "Режим сборки по умолчанию" in advanced


def test_render_mode_validation(client, synthetic_template):
    started = client.post(
        "/api/generate",
        files={"template": ("tpl.pptx", synthetic_template, "application/octet-stream")},
        data={"brief": BRIEF, "vlm": "off", "render_mode": "мусор"})
    state = _wait(client, started.json()["job_id"])
    assert state["status"] == "done", state
    assert state["summary"]["render_mode"] == "native", "незнакомый режим → native"
