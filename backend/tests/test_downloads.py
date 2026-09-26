"""Скачивание результатов: Content-Disposition, ZIP, честный отказ PDF.

Правила интерфейса: файл отдаётся вложением (attachment), ZIP содержит все
выбранные форматы всех вариантов, а без LibreOffice PPTX продолжает работать,
PDF получает человеческую ошибку, а не техническую трассировку.
"""
from __future__ import annotations

import io
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.render.pdf import find_soffice

BRIEF = ("Платформа аналитики сократила время подготовки отчётов на 40%, "
         "автоматизировала 12 задач, охватила 5 подразделений.")
HAS_SOFFICE = bool(find_soffice())


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def job(client, synthetic_template) -> str:
    started = client.post(
        "/api/generate",
        files={"template": ("tpl.pptx", synthetic_template, "application/octet-stream")},
        data={"brief": BRIEF, "vlm": "off"})
    assert started.status_code == 200
    job_id = started.json()["job_id"]
    deadline = time.time() + 120
    while time.time() < deadline:
        state = client.get(f"/api/jobs/{job_id}").json()
        if state["status"] in ("done", "error"):
            assert state["status"] == "done", state
            return job_id
        time.sleep(0.3)
    raise AssertionError("задание не завершилось")


def test_download_content_disposition(client, job):
    for kind, expected in (("pptx", "presentation_compact.pptx"),
                           ("pdf", "presentation_compact.pdf")):
        response = client.get(f"/api/jobs/{job}/{kind}", params={"variant": "compact"})
        if kind == "pdf" and response.status_code == 503:
            pytest.skip("LibreOffice недоступен: PDF-проверка пропущена")
        assert response.status_code == 200
        disposition = response.headers.get("content-disposition", "")
        assert disposition == f'attachment; filename="{expected}"', disposition


def test_download_all_zip(client, job):
    response = client.get(f"/api/jobs/{job}/download",
                          params={"formats": "pptx,pdf", "variants": "compact,cards,split"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert "attachment" in response.headers.get("content-disposition", "")
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = set(archive.namelist())
    assert {"presentation_compact.pptx", "presentation_cards.pptx",
            "presentation_split.pptx"} <= names
    if HAS_SOFFICE:
        assert len(names) == 6, names
    else:
        assert len(names) == 3, names
        assert response.headers.get("x-pdf-skipped") == "1"


def test_download_only_pptx_format(client, job):
    response = client.get(f"/api/jobs/{job}/download",
                          params={"formats": "pptx", "variants": "compact,cards,split"})
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = set(archive.namelist())
    assert names == {"presentation_compact.pptx", "presentation_cards.pptx",
                     "presentation_split.pptx"}


def test_download_rejects_empty_formats(client, job):
    response = client.get(f"/api/jobs/{job}/download", params={"formats": "docx"})
    assert response.status_code == 422


def test_pdf_unavailable_graceful(client, job, monkeypatch):
    """Без LibreOffice PPTX скачивается, PDF даёт человеческую ошибку."""
    monkeypatch.setattr("app.render.pdf.find_soffice", lambda explicit=None: "")

    pptx = client.get(f"/api/jobs/{job}/pptx", params={"variant": "compact"})
    assert pptx.status_code == 200 and pptx.content.startswith(b"PK")

    pdf = client.get(f"/api/jobs/{job}/pdf", params={"variant": "compact"})
    assert pdf.status_code == 503
    message = pdf.json()["detail"]
    assert "LibreOffice" in message and "PPTX" in message
    assert "Traceback" not in message

    only_pdf = client.get(f"/api/jobs/{job}/download", params={"formats": "pdf"})
    assert only_pdf.status_code == 503
    assert "LibreOffice" in only_pdf.json()["detail"]

    mixed = client.get(f"/api/jobs/{job}/download", params={"formats": "pptx,pdf"})
    assert mixed.status_code == 200
    with zipfile.ZipFile(io.BytesIO(mixed.content)) as archive:
        names = set(archive.namelist())
    assert len(names) == 3, names


def test_missing_file_graceful(client, job, monkeypatch):
    """PDF «не собрался» — интерфейс покажет серую плашку, API не падает."""
    monkeypatch.setattr("app.render.pdf.find_soffice", lambda explicit=None: "")
    response = client.get(f"/api/jobs/{job}/pdf", params={"variant": "cards"})
    assert response.status_code == 503
    assert "Скачайте PPTX" in response.json()["detail"]


def test_cancel_running_job(client, synthetic_template):
    started = client.post(
        "/api/generate",
        files={"template": ("tpl.pptx", synthetic_template, "application/octet-stream")},
        data={"brief": BRIEF, "vlm": "off"})
    job_id = started.json()["job_id"]
    cancelled = client.post(f"/api/jobs/{job_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] in ("cancelled", "done")
    state = client.get(f"/api/jobs/{job_id}").json()
    assert state["status"] in ("cancelled", "done")
