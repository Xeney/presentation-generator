"""Сквозная проверка интерфейса «глазами бабушки» через headless Chrome.

Проходит весь путь: шаблон → бриф → одна кнопка → три карточки → скачивание
(PPTX, ZIP). Проверяет, что в консоли нет ошибок, нет превью слайдов на
основном экране, скачивание начинается только по клику, а выбранные форматы
действительно фильтруют карточки.

Запуск (сервисы уже подняты):
    python tools/ui_e2e.py --out docs/evidence/ui_final
    python tools/ui_e2e.py --base http://localhost:3000 --brief "..."
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

DEFAULT_BRIEF = (
    "Платформа внутренней аналитики: за квартал время подготовки отчётов "
    "сократилось на 40 %, автоматизированы 12 рутинных задач, охват — 5 "
    "подразделений. Платформой пользуются 2000 сотрудников еженедельно. "
    "План — подключить 10 отделов к концу года, внедрить ML-предсказания выручки."
)


def find_template() -> Path:
    for name in ("Шаблон презентации VK Education.pptx",
                 "VK Tech шаблон.pptx",
                 "ЛЦТ2026 Шаблон презентации.pptx"):
        candidate = ROOT / name
        if candidate.exists():
            return candidate
    raise SystemExit("нет шаблона в корне репозитория")


def run(base: str, template: Path, brief: str, out: Path, timeout_s: int = 240,
        fake_provider: str = "") -> dict:
    from playwright.sync_api import sync_playwright

    out.mkdir(parents=True, exist_ok=True)
    report: dict = {"steps": [], "console_errors": [], "downloads": []}

    def step(name: str, shot: str | None = None):
        report["steps"].append(name)
        if shot:
            page.screenshot(path=str(out / shot), full_page=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(accept_downloads=True, viewport={"width": 1280, "height": 900})
        page = context.new_page()
        page.on("console", lambda msg: report["console_errors"].append(msg.text)
                if msg.type == "error" else None)
        page.on("pageerror", lambda err: report["console_errors"].append(str(err)))
        download_events: list[str] = []
        page.on("download", lambda item: download_events.append(item.suggested_filename))

        page.goto(base, wait_until="networkidle")
        page.get_by_role("heading", name="Генератор презентаций").wait_for(timeout=15000)
        assert page.locator("img").count() == 0, "на главном экране не должно быть миниатюр"
        step("форма открыта", "01_form.png")

        # ШАГ 1: шаблон
        page.set_input_files("input[type=file][accept*='pptx']", str(template))
        page.get_by_text(template.name).wait_for(timeout=15000)
        step("шаблон загружен", "02_template.png")

        # ШАГ 2: бриф
        page.get_by_placeholder("Например: квартальный отчёт").fill(brief)
        step("бриф заполнен", "03_brief.png")

        # «Для опытных» всегда раскрыт (ADR-040); свёрнутой остаётся галерея
        page.get_by_text("Источник нейросети").wait_for(timeout=5000)
        page.get_by_text("Вернуться к локальному").wait_for(timeout=5000)
        assert page.locator("details details[open]").count() == 0
        step("настройки для опытных", "04a_advanced.png")

        if fake_provider:
            # ключ через интерфейс: проверка → применение (маска) → сброс
            page.locator("input[name='provider-source']").nth(1).check()
            page.fill("#provider-url", fake_provider)
            page.fill("#provider-key", "sk-test-123456789")
            page.select_option("#provider-model", "qwen3.5-9b")
            page.get_by_role("button", name="Проверить подключение").click()
            page.get_by_text("Подключение работает").wait_for(timeout=20000)
            step("ключ проверен", "04b_provider_check.png")
            page.get_by_role("button", name="Применить").click()
            page.get_by_text("Работает внешний сервис").wait_for(timeout=20000)
            page.get_by_text("sk-...789").wait_for(timeout=5000)
            step("ключ применён (маска)", "04c_provider_applied.png")
            page.get_by_role("button", name="Вернуться к локальному").click()
            page.get_by_text("Работает локальная нейросеть.").wait_for(timeout=20000)
            report["provider_check"] = "ok"
            step("вернулись к локальному", "04d_provider_local.png")

        # ШАГ 3 + запуск (оба формата включены по умолчанию)
        assert page.get_by_role("button", name="PPTX ✓").count() == 1
        assert page.get_by_role("button", name="PDF ✓").count() == 1
        page.get_by_role("button", name="Создать презентацию").click()
        page.get_by_role("heading", name="Читаем шаблон…").wait_for(timeout=15000)
        step("генерация идёт", "04_progress.png")

        page.get_by_role("heading", name="Готово! Собрано за", exact=False).wait_for(
            timeout=timeout_s * 1000)
        page.get_by_role("button", name=re.compile("Скачать всё")).wait_for(timeout=10000)
        step("результат", "05_result.png")
        report["thumbs_on_result"] = page.locator("img").count()
        assert report["thumbs_on_result"] == 0, "на экране результата нет миниатюр"
        assert not download_events, "файл не должен скачиваться сам, только по клику"
        report["downloads_before_click"] = len(download_events)

        # три карточки вариантов + бейдж «Рекомендуем» только на первой
        for name in ("Компактный", "Карточками", "С данными отдельно"):
            page.get_by_role("heading", name=name).wait_for(timeout=5000)
        report["recommended_badge"] = page.get_by_text("Рекомендуем").count()
        assert report["recommended_badge"] == 1, "бейдж «Рекомендуем» — ровно один"

        # PPTX — главная кнопка, PDF — вторичная; размеры показаны
        pptx_buttons = page.get_by_role("button", name=re.compile("Скачать PPTX"))
        assert pptx_buttons.count() == 3, pptx_buttons.count()
        assert page.get_by_role("button", name=re.compile("Скачать PDF")).count() == 3
        pptx_text = pptx_buttons.first.inner_text()
        assert re.search(r"\d+([.,]\d+)?\s*(МБ|КБ)", pptx_text), pptx_text
        report["pptx_button_text"] = pptx_text
        report["sizes_shown"] = True

        # HTML-путь — внутренняя деталь: ссылок и кнопок с HTML нет
        assert page.get_by_text("Открыть HTML").count() == 0
        assert page.get_by_text(re.compile("через HTML", re.IGNORECASE)).count() == 0
        assert page.locator('a[href*=".html"]').count() == 0
        report["html_hidden"] = True

        # серая плашка появляется только при офлайн-планировщике;
        # в прогоне с LLM её быть не должно, но и двух плашек быть не может
        report["offline_badge"] = page.get_by_text(
            "Собрано без участия нейросети").count()
        assert report["offline_badge"] <= 1

        # блок качества с человеческим статусом
        page.get_by_role("heading", name="Проверка качества").wait_for(timeout=5000)
        assert page.get_by_text(re.compile("Проверено \\d+ правил вёрстки")).count() == 1
        report["quality_panel"] = True
        report["result_files"] = [
            f"presentation_{variant}.{format}"
            for variant in ("compact", "cards", "split")
            for format in ("pptx", "pdf")]

        # скачивание по клику: PPTX одного варианта
        with page.expect_download(timeout=60000) as download_info:
            page.locator("section", has_text="Компактный").get_by_role(
                "button", name="Скачать PPTX").click()
        download = download_info.value
        pptx_path = out / f"downloaded_{download.suggested_filename}"
        download.save_as(str(pptx_path))
        report["downloads"].append({"name": download.suggested_filename,
                                    "kb": round(pptx_path.stat().st_size / 1024, 1)})
        assert download.suggested_filename == "presentation_compact.pptx", download.suggested_filename
        assert pptx_path.stat().st_size > 5000
        step("PPTX скачан", "06_downloaded_pptx.png")

        # ZIP со всеми файлами
        with page.expect_download(timeout=120000) as zip_info:
            page.get_by_role("button", name=re.compile("Скачать всё")).click()
        zip_download = zip_info.value
        zip_path = out / "downloaded_all.zip"
        zip_download.save_as(str(zip_path))
        with zipfile.ZipFile(zip_path) as archive:
            names = sorted(archive.namelist())
        report["downloads"].append({"name": zip_download.suggested_filename, "files": names})
        assert len(names) == 6, names
        step("ZIP скачан", "07_downloaded_zip.png")

        # «Создать ещё раз» → форма, и только PPTX фильтрует карточки
        page.get_by_role("button", name="Создать ещё раз").click()
        page.get_by_role("heading", name="Генератор презентаций").wait_for(timeout=10000)
        page.set_input_files("input[type=file][accept*='pptx']", str(template))
        page.get_by_placeholder("Например: квартальный отчёт").fill(brief)
        page.get_by_role("button", name="PDF ✓").click()   # снимаем PDF
        page.get_by_role("button", name="Создать презентацию").click()
        page.get_by_role("heading", name="Готово! Собрано за", exact=False).wait_for(
            timeout=timeout_s * 1000)
        page.get_by_role("button", name=re.compile("Скачать PPTX")).first.wait_for(
            timeout=5000)
        assert page.get_by_role("button", name=re.compile("Скачать PDF")).count() == 0, \
            "выбран только PPTX — PDF-кнопок быть не должно"
        assert page.get_by_text("PDF недоступен").count() == 0
        report["pptx_only_filter"] = True
        step("только PPTX на экране результата", "08_result_pptx_only.png")

        # простой режим «Я не знаю, что делать»: спросит ключ, зафиксирует
        # HTML+CSS / PPTX+PDF / без проверки нейросетью и скроет шаг 3
        page.get_by_role("button", name="Создать ещё раз").click()
        page.get_by_role("heading", name="Генератор презентаций").wait_for(timeout=10000)
        page.get_by_role("button", name="Включить простой режим").click()
        page.get_by_role("heading", name="Подключите нейросеть").wait_for(timeout=5000)
        assert page.get_by_role("heading", name="Выберите формат скачивания").count() == 0, \
            "в простом режиме шаг выбора форматов не показывается"
        assert page.get_by_text("PPTX + PDF").count() == 1
        assert page.get_by_text("Проверка нейросетью включена").count() == 1
        assert page.locator("#simple-key").count() == 1, "должно спросить API-ключ"
        # «Применить» — именно в простой форме (у «Для опытных» своя кнопка)
        simple_form = page.locator("section", has_text="Подключите нейросеть")
        assert simple_form.get_by_role("button", name="Применить").count() == 1
        step("простой режим включён", "09_simple_mode.png")
        page.get_by_role("button", name="Вернуть ручной режим").click()
        page.get_by_role("heading", name="Выберите формат скачивания").wait_for(timeout=5000)
        report["simple_mode"] = True
        step("вернулись в ручной режим", "10_form_manual_back.png")

        browser.close()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Сквозной прогон интерфейса")
    parser.add_argument("--base", default="http://localhost:3000")
    parser.add_argument("--template", default="")
    parser.add_argument("--brief", default=DEFAULT_BRIEF)
    parser.add_argument("--out", default=str(ROOT / "docs" / "evidence" / "ui_final"))
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--fake-provider", default="",
                        help="базовый URL заглушки OpenAI-сервиса для проверки ключа")
    args = parser.parse_args()

    template = Path(args.template) if args.template else find_template()
    report = run(args.base, template, args.brief, Path(args.out), args.timeout,
                 fake_provider=args.fake_provider)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["console_errors"]:
        print("ОШИБКИ КОНСОЛИ:", report["console_errors"])
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
