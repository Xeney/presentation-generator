"""Простой режим «Я не знаю, что делать» и секционная компоновка (ADR-040).

Проверки читают исходники фронтенда как текст: в репозитории нет JS-раннера,
живое поведение закрывает браузерный набор (`test_ui_browser.py` + `ui_e2e.py`).
"""
from __future__ import annotations

from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
FORM = (FRONTEND / "components" / "generator-form.tsx").read_text(encoding="utf-8")
PAGE = (FRONTEND / "app" / "page.tsx").read_text(encoding="utf-8")
PROVIDER = (FRONTEND / "components" / "provider-setup.tsx").read_text(encoding="utf-8")
RESULT = (FRONTEND / "components" / "result-view.tsx").read_text(encoding="utf-8")
ADVANCED = (FRONTEND / "components" / "advanced-settings.tsx").read_text(encoding="utf-8")


def test_simple_mode_toggle_in_form():
    """Кнопка-режим «Я не знаю, что делать» есть в форме и переключается."""
    assert "Я не знаю, что делать" in FORM
    assert "ПРОСТОЙ РЕЖИМ" in FORM
    assert "onSimpleModeChange" in FORM
    assert "Включить простой режим" in FORM
    assert "Вернуть ручной режим" in FORM


def test_simple_mode_fixes_html_vlm_and_formats():
    """Режим фиксирует HTML+CSS, включает проверку нейросетью и PPTX + PDF."""
    assert 'setRenderMode("html")' in PAGE
    assert "setVlm(true)" in PAGE
    assert "setFormats({ pptx: true, pdf: true })" in PAGE
    assert 'renderMode: simpleMode ? "html" : renderMode' in PAGE
    assert "vlm: simpleMode ? true : vlm" in PAGE


def test_simple_mode_asks_provider_key():
    """В режиме автоматически спрашивают ключ внешнего сервиса."""
    assert 'idPrefix="simple"' in PAGE
    assert "forceExternal" in PAGE
    assert "API-ключ" in PROVIDER
    assert "id={`${idPrefix}-key`}" in PROVIDER
    assert "Ключ пока не подключён" in PROVIDER
    assert "Проверить подключение" in PROVIDER


def test_simple_mode_hides_step3():
    """Шаг форматов/режима сборки скрыт, настройки показаны чипами."""
    assert "!simpleMode &&" in FORM
    assert "Настройки зафиксированы" in FORM
    assert "PPTX + PDF" in FORM
    assert "Проверка нейросетью включена" in FORM


def test_form_two_columns():
    """Слева — простой режим, справа — шаги, запуск и «Для опытных» внизу."""
    assert "lg:grid-cols-[minmax(0,380px)_minmax(0,1fr)]" in FORM
    assert "<aside" in FORM
    assert "{advanced}" in FORM
    assert FORM.index("{advanced}") > FORM.index("Создать презентацию"), \
        "панель «Для опытных» — во второй колонке, после кнопки запуска"
    # «Для опытных» всегда раскрыт: это секция, а не сворачиваемый details
    assert '<details className="panel' not in ADVANCED
    assert '<section className="panel p-5">' in ADVANCED
    assert "Для опытных" in ADVANCED


def test_result_page_sections():
    """Результат разбит на секции: варианты и качество слева, итоги справа."""
    assert "ВАРИАНТЫ ОФОРМЛЕНИЯ" in RESULT
    assert "lg:grid-cols-[minmax(0,1fr)_340px]" in RESULT
    assert "<aside" in RESULT
    assert "Все файлы сразу" in RESULT
    assert "ИТОГИ" in RESULT
