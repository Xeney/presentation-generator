"""Экран результата: проверки разметки и текстов (frontend has no JS-runner).

Тесты читают исходники компонентов как текст и проверяют требования задания:
три карточки, PPTX — главная кнопка, HTML-путь скрыт, размеры файлов, статусы
качества человеческим языком, свёрнутая смысловая секция. Живое поведение
проверяется браузерным набором (`test_ui_browser.py` + `tools/ui_e2e.py`).
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.main import app

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
RESULT_VIEW = (FRONTEND / "components" / "result-view.tsx").read_text(encoding="utf-8")
QUALITY_PANEL = (FRONTEND / "components" / "quality-panel.tsx").read_text(encoding="utf-8")
AUDIT_LABELS = (FRONTEND / "lib" / "audit-labels.ts").read_text(encoding="utf-8")
GLOBALS = (FRONTEND / "app" / "globals.css").read_text(encoding="utf-8")

BRIEF = ("Платформа аналитики сократила время подготовки отчётов на 40%, "
         "автоматизировала 12 задач, охватила 5 подразделений.")


def test_result_page_renders_three_variants():
    for variant in ("compact", "cards", "split"):
        assert f'variant: "{variant}"' in RESULT_VIEW, variant
    for name in ("Компактный", "Карточками", "С данными отдельно"):
        assert f'name: "{name}"' in RESULT_VIEW, name
    assert "CARDS.map" in RESULT_VIEW


def test_pptx_button_is_primary():
    """PPTX — залитая акцентная кнопка, PDF — контурная вторичная."""
    pptx_block = re.search(r'label="Скачать PPTX".{0,160}?primary\b',
                           RESULT_VIEW, re.S)
    assert pptx_block, "PPTX-кнопка должна быть главной (primary)"
    assert 'label="Скачать PDF"' in RESULT_VIEW
    assert "primary={false}" in RESULT_VIEW, "PDF-кнопка должна быть вторичной"
    assert "border border-border bg-card" in RESULT_VIEW, "вторичная — контурная"


def test_html_via_pptx_hidden_from_ui():
    """«PPTX (через HTML)» не показывается в интерфейсе."""
    for source in (RESULT_VIEW, QUALITY_PANEL):
        assert 'render="html"' not in source
        assert "html_pptx" not in source
        assert "через HTML" not in source


def test_html_link_hidden_from_result_page():
    """На экране результата нет ссылок «Открыть HTML» и вообще ссылок на .html."""
    for source in (RESULT_VIEW, QUALITY_PANEL):
        assert "Открыть HTML" not in source
        assert "htmlUrl" not in source
        assert 'href*=".html"' not in source
    assert re.search(r'<a\b', RESULT_VIEW) is None, "никаких ссылок на экране"
    page = (FRONTEND / "app" / "page.tsx").read_text(encoding="utf-8")
    assert "htmlUrl" not in page


def test_file_sizes_shown():
    """Размеры приходят из /info и выводятся в кнопках."""
    assert "formatBytes" in RESULT_VIEW
    assert "files.pptx?.bytes" in RESULT_VIEW
    assert "info?.variants" in RESULT_VIEW
    types = (FRONTEND / "lib" / "types.ts").read_text(encoding="utf-8")
    assert "bytes: number | null" in types
    assert "files?: VariantFiles" in types
    assert "КБ" in AUDIT_LABELS and "МБ" in AUDIT_LABELS


def test_quality_status_color_matches_severity():
    """Четыре статуса качества с цветами семейств emerald/amber/orange/red."""
    for cls in ("status-green", "status-yellow", "status-orange", "status-red"):
        assert cls in QUALITY_PANEL, cls
        assert f".{cls}" in GLOBALS, f"нет стиля {cls} в globals.css"
    assert "Всё чисто" in QUALITY_PANEL
    assert "мелкие замечания" in QUALITY_PANEL
    assert "Есть замечания" in QUALITY_PANEL
    assert "критичные проблемы" in QUALITY_PANEL
    # логика: ошибки, которые остались исправимыми → красный; неисправимые → оранжевый
    assert 'fixableErrorsLeft.length > 0 ? "critical"' in QUALITY_PANEL
    assert 'unfixableErrors.length > 0 ? "attention"' in QUALITY_PANEL
    labels = (FRONTEND / "lib" / "audit-labels.ts").read_text(encoding="utf-8")
    assert "status-blue" in GLOBALS and "SEMANTIC_QUESTIONS" in labels


def test_human_readable_audit_labels():
    """Не меньше десяти кодов переведены на человеческий язык."""
    pairs = {
        "text_overflow": "Текст не поместился на слайде",
        "overlap": "Два блока наложились",
        "font_not_allowed": "Шрифт не из шаблона",
        "color_not_allowed": "Цвет не из палитры шаблона",
        "contrast_too_low": "Низкий контраст текста",
        "slide_too_dense": "Слайд перегружен контентом",
        "empty_slide": "Пустой слайд",
        "duplicate_slide": "Дубль слайда",
        "placeholder_text": "Остался текст-заглушка",
        "chart_unlabeled": "Диаграмма без подписей",
        "fact_unverified": "Число не найдено в источниках",
        "content_off_source": "Слайд слабо опирается на источник",
    }
    assert len(pairs) >= 10
    for code, title in pairs.items():
        assert re.search(rf'{code}: \{{[^}}]*title: "{re.escape(title)}"',
                         AUDIT_LABELS, re.S), f"{code} → {title}"
    entries = re.findall(r"^  [a-z_]+: \{", AUDIT_LABELS, re.M)
    assert len(entries) >= 10, f"в маппинге только {len(entries)} кодов"
    assert "Проверка: ${code}" in AUDIT_LABELS, "fallback для неизвестных кодов"
    # коды не показываются на виду — только в title
    assert 'title={`Код проверки: ${issue.code}`}' in QUALITY_PANEL


def test_semantic_section_collapsed_by_default():
    assert "SEMANTIC_VISIBLE = 5" in QUALITY_PANEL
    assert "useState(false)" in QUALITY_PANEL, "секция свёрнута по умолчанию"
    assert "semantic.slice(0, SEMANTIC_VISIBLE)" in QUALITY_PANEL
    assert "Показать все" in QUALITY_PANEL
    assert "QUOTE_LIMIT = 150" in QUALITY_PANEL
    assert "Смысловые замечания нейросети" in QUALITY_PANEL
    assert "может ошибаться" in QUALITY_PANEL


def test_recommended_badge_on_first_variant():
    assert "Рекомендуем" in RESULT_VIEW
    assert RESULT_VIEW.count("recommended: true") == 1
    compact = re.search(r'variant: "compact".{0,200}?recommended: true',
                        RESULT_VIEW, re.S)
    assert compact, "бейдж должен стоять на первом варианте (compact)"


# --------------------------------------------------------------------- API
@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def info(client, synthetic_template) -> dict:
    started = client.post(
        "/api/generate",
        files={"template": ("tpl.pptx", synthetic_template, "application/octet-stream")},
        data={"brief": BRIEF, "vlm": "off"})
    assert started.status_code == 200, started.text
    job_id = started.json()["job_id"]
    deadline = time.time() + 120
    while time.time() < deadline:
        state = client.get(f"/api/jobs/{job_id}").json()
        if state["status"] in ("done", "error"):
            break
        time.sleep(0.3)
    assert state["status"] == "done", state
    return client.get(f"/api/jobs/{job_id}/info").json()


def test_info_reports_file_sizes(info):
    """В /info у каждого варианта есть размеры и доступность форматов."""
    assert len(info["variants"]) == 3
    for variant in info["variants"]:
        files = variant["files"]
        assert files["pptx"]["available"] is True
        assert files["pptx"]["bytes"] > 5000, "PPTX весит больше пяти килобайт"
        assert set(files) >= {"pptx", "pdf", "html_pptx", "html"}
        # PDF собирается при скачивании: доступность без размера
        assert isinstance(files["pdf"]["available"], bool)
        assert files["pdf"]["bytes"] is None
