"""Бюджет времени: колода должна собираться заметно быстрее 5 минут (ADR-012).

Тест измеряет стадии на синтетическом шаблоне без LLM и VLM (в CI их нет) и
проверяет, что бюджет 300 секунд не расходуется даже близко. Реальные замеры на
шаблонах VK смотрите в отчёте `scripts/e2e.sh` (`data/e2e_report.json`).
"""
from __future__ import annotations

import time

from app.pipeline import full_generate

BRIEF = (
    "Платформа аналитики VK Tech: за квартал время подготовки отчётов сократилось на 40%, "
    "автоматизированы 12 рутинных задач, охват вырос до 5 подразделений. Платформой "
    "пользуются 2000 сотрудников еженедельно. План — подключить 10 отделов к концу года, "
    "внедрить ML-предсказания выручки и снизить стоимость отчётности на 25%. "
)
SOURCE = (
    "Контент-пакет: пилот прошёл в двух отделах, время отчёта упало с 8 до 3 часов, "
    "доля ошибок снизилась с 15% до 4%. Целевые метрики следующего квартала: "
    "12 подключённых отделов, 5000 активных пользователей, NPS 45. "
)

BUDGET_S = 300.0


def test_pipeline_fits_time_budget(synthetic_template):
    started = time.perf_counter()
    result = full_generate(BRIEF, SOURCE, "project", synthetic_template, "synthetic.pptx",
                           vlm=False)
    elapsed = time.perf_counter() - started

    stages = result["stages"]
    assert elapsed < 60.0, f"офлайн-прогон занял {elapsed:.1f} c — это подозрительно много"
    assert stages["total_s"] < BUDGET_S
    for stage in ("parse_s", "plan_s", "render_s", "audit_s"):
        assert stage in stages, f"стадия {stage} не измеряется"
        assert stages[stage] >= 0

    # три варианта, все проходят аудит
    assert len(result["variants"]) == 3
    for variant in result["variants"]:
        assert variant["audit"]["errors"] == 0, variant["audit"]["issues"][:3]


def test_deck_size_is_within_target(synthetic_template):
    """Целевой объём ТЗ — 10–15 слайдов; офлайн-планировщик обязан к нему стремиться."""
    result = full_generate(BRIEF, SOURCE, "project", synthetic_template, "synthetic.pptx",
                           vlm=False)
    slides = len(result["deck"]["slides"])
    assert 4 <= slides <= 15
    assert slides >= 8, f"получилось {slides} слайдов — слишком мало для целевого объёма"
