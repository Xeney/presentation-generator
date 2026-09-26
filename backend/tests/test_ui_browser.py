"""Интерфейс под headless Chrome: весь путь «бабушки» и правила экрана результата.

Тесты поднимают локальные сервисы (uvicorn на :8000 и next start на :3000) и
один раз проходят путь браузером через `tools/ui_e2e.py`. Если сервисы заняты,
Playwright не установлен или фронтенд не собран — тесты честно скипаются с
причиной (полный прогон: `npm run build` + `pytest`, либо `tools/ui_e2e.py`).
"""
from __future__ import annotations

import importlib.util
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
BACKEND = ROOT / "backend"

BRIEF = ("Платформа внутренней аналитики: за квартал время подготовки отчётов "
         "сократилось на 40 %, автоматизированы 12 рутинных задач, охват — 5 "
         "подразделений. Платформой пользуются 2000 сотрудников еженедельно.")


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex(("127.0.0.1", port)) != 0


def _wait_url(url: str, timeout_s: float = 90.0) -> None:
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if response.status < 500:
                    return
        except Exception as exc:  # noqa: BLE001 — сервис ещё поднимается
            last = exc
        time.sleep(1.0)
    raise AssertionError(f"сервис {url} не поднялся: {last}")


def _kill(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                       capture_output=True, check=False)
    else:
        process.terminate()


@pytest.fixture(scope="session")
def ui_report() -> dict:
    if os.environ.get("E2E_UI", "1") == "0":
        pytest.skip("E2E_UI=0: браузерные тесты отключены")
    if importlib.util.find_spec("playwright") is None:
        pytest.skip("playwright не установлен: pip install playwright && playwright install")
    if not (FRONTEND / ".next" / "BUILD_ID").exists():
        pytest.skip("фронтенд не собран: cd frontend && npm run build")
    if not _port_free(8000) or not _port_free(3000):
        pytest.skip("порты 8000/3000 заняты: остановите сервис перед браузерными тестами")

    env = dict(os.environ)
    env.update({"LLM_PROVIDER": "ollama", "VLM_PROVIDER": "off",
                "OLLAMA_BASE_URL": "http://127.0.0.1:1",
                "VLM_AUDIT_ENABLED": "false", "DISABLE_LLM": "false"})
    npm = "npm.cmd" if os.name == "nt" else "npm"
    out_dir = Path(tempfile.mkdtemp(prefix="ui_e2e_"))
    backend = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.api.main:app", "--port", "8000"],
        cwd=BACKEND, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    frontend = subprocess.Popen(
        [npm, "run", "start"], cwd=FRONTEND, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        _wait_url("http://127.0.0.1:8000/api/health")
        _wait_url("http://127.0.0.1:3000")
        from tools.ui_e2e import find_template, run

        report = run("http://localhost:3000", find_template(), BRIEF, out_dir,
                     timeout_s=180)
        report["out_dir"] = str(out_dir)
        return report
    finally:
        _kill(frontend)
        _kill(backend)


def test_ui_full_path(ui_report):
    """Загрузка → бриф → генерация → скачивание трёх вариантов в PPTX и PDF."""
    import io

    from pptx import Presentation

    for name in ("форма открыта", "шаблон загружен", "бриф заполнен",
                 "генерация идёт", "результат", "PPTX скачан", "ZIP скачан"):
        assert name in ui_report["steps"], name
    out = Path(ui_report["out_dir"])
    pptx = out / "downloaded_presentation_compact.pptx"
    presentation = Presentation(io.BytesIO(pptx.read_bytes()))
    assert len(list(presentation.slides)) >= 3
    with zipfile.ZipFile(out / "downloaded_all.zip") as archive:
        names = set(archive.namelist())
    assert names == {
        "presentation_compact.pptx", "presentation_compact.pdf",
        "presentation_cards.pptx", "presentation_cards.pdf",
        "presentation_split.pptx", "presentation_split.pdf"}


def test_result_shows_three_files(ui_report):
    assert ui_report.get("result_files") == [
        "presentation_compact.pptx", "presentation_compact.pdf",
        "presentation_cards.pptx", "presentation_cards.pdf",
        "presentation_split.pptx", "presentation_split.pdf"]


def test_download_only_on_click(ui_report):
    assert ui_report.get("downloads_before_click") == 0, \
        "на экране результата не должно быть автоматических скачиваний"
    names = [item.get("name") for item in ui_report["downloads"]]
    assert "presentation_compact.pptx" in names
    assert any(name and name.endswith(".zip") for name in names)


def test_format_selection_filters_cards(ui_report):
    assert ui_report.get("pptx_only_filter") is True, \
        "при выборе только PPTX PDF-строк на экране быть не должно"


def test_no_console_errors(ui_report):
    assert ui_report["console_errors"] == [], ui_report["console_errors"]


def test_ui_download_only(ui_report):
    """На основном экране нет миниатюр и встроенных просмотрщиков."""
    assert ui_report.get("thumbs_on_result") == 0
    assert not (FRONTEND / "components" / "deck-viewer.tsx").exists(), \
        "компонент deck-viewer с миниатюрами не должен существовать"
    result_view = (FRONTEND / "components" / "result-view.tsx").read_text(encoding="utf-8")
    assert "<img" not in result_view and "<iframe" not in result_view
    page = (FRONTEND / "app" / "page.tsx").read_text(encoding="utf-8")
    for banned in ("deck-viewer", "DeckViewer", "thumbUrl", "<img"):
        assert banned not in page, banned
