"""Сквозной прогон конвейера без API: шаблон → колода → 3 варианта вёрстки → PPTX + аудит.

Запуск:
    python tools/e2e_render.py                          # все PPTX из data/templates/
    python tools/e2e_render.py path/to/template.pptx
    python tools/e2e_render.py --json template_profiles/x.json

Печатает время каждой стадии и результат детерминированного аудита по вариантам —
используется для замера бюджета 5 минут (docs/DECISIONS.md ADR-012).
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.audit.checks import Audit  # noqa: E402
from app.layout.engine import DesignContext  # noqa: E402
from app.models.deck import Block, Chart, ChartType, Deck, Slide, SlideType, Table  # noqa: E402
from app.planner.fallback import FallbackPlanner  # noqa: E402
from app.render.pptx_renderer import Renderer  # noqa: E402
from app.template.parser import TemplateParser  # noqa: E402

BRIEF = ("VK Tech запускает внутреннюю платформу аналитики. За квартал платформа "
         "сократила время подготовки отчётов на 40%, автоматизировала 12 рутинных "
         "задач, охватила 5 подразделений. 2000 сотрудников используют её еженедельно. "
         "План: подключить 10 отделов к концу года и внедрить ML-предсказания выручки.")

VARIANTS = ("compact", "cards", "split")


def build_deck() -> Deck:
    """Колода с покрытием всех виджетов: буллеты, текст, таблица, диаграмма, фактоиды, шаги, цитата."""
    deck = FallbackPlanner().plan(BRIEF, "", "project")
    deck.slides.append(Slide(
        slide_type=SlideType.CONTENT,
        heading="Пилот подтвердил эффект: время отчёта сократилось втрое",
        blocks=[
            Block(kind="steps", items=["Пилот в одном отделе", "Масштабирование",
                                       "ML-модуль", "Аналитика выручки"]),
            Block(kind="table", title="Результаты пилота", table=Table(
                header=["Метрика", "До", "После"],
                rows=[["Время отчёта", "8 ч", "3 ч"], ["Охват отделов", "2", "5"],
                      ["Ошибки", "15%", "4%"]]).model_dump()),
            Block(kind="chart", title="Динамика", chart=Chart(
                type=ChartType.COLUMN,
                categories=["Q1", "Q2", "Q3", "Q4"],
                series=[{"name": "Отчёты", "values": [40.0, 55.0, 70.0, 90.0]}])),
            Block(kind="quote", quote_text="Главное — скорость принятия решений",
                  quote_author="Команда платформы"),
        ],
    ))
    return deck


def find_templates(argv: list[str]) -> list[Path]:
    if argv:
        return [Path(a) for a in argv]
    found: dict[str, Path] = {}
    for directory in (ROOT / "data" / "templates", ROOT):
        if directory.is_dir():
            for path in sorted(directory.glob("*.pptx")):
                found.setdefault(path.name, path)
    return list(found.values())


def render_all(template: Path, out_dir: Path, deck: Deck) -> dict:
    t0 = time.perf_counter()
    profile = TemplateParser(template).parse().to_dict()
    t_profile = time.perf_counter() - t0
    # профиль кладём рядом с колодами: аудит подхватит его по имени файла,
    # иначе сравнивал бы результат с дизайн-системой другого шаблона
    (out_dir / f"{template.stem}.profile.json").write_text(
        json.dumps(profile, ensure_ascii=False), encoding="utf-8")

    dc = DesignContext.from_profile(profile)
    template_bytes = template.read_bytes()
    audit = Audit(profile)
    summary: dict = {"profile_s": round(t_profile, 2), "variants": {}}

    for variant in VARIANTS:
        t0 = time.perf_counter()
        renderer = Renderer(profile, variant=variant, template_bytes=template_bytes)
        pptx = renderer.render_deck(deck, dc)
        t_render = time.perf_counter() - t0

        t0 = time.perf_counter()
        result = audit.audit(deck, pptx)
        t_audit = time.perf_counter() - t0

        from pptx import Presentation

        n_slides = len(Presentation(io.BytesIO(pptx)).slides)
        ok = n_slides == len(deck.slides)
        target = out_dir / f"{template.stem}_{variant}.pptx"
        target.write_bytes(pptx)
        summary["variants"][variant] = {
            "slides": n_slides, "expected": len(deck.slides), "ok": ok,
            "render_s": round(t_render, 2), "audit_s": round(t_audit, 2),
            "errors": result["errors"], "warnings": result["warnings"],
            "issues": [f"{i['code']}@{i['slide']}" for i in result["issues"][:8]],
            "file": str(target.relative_to(ROOT)),
        }
    summary["total_s"] = round(
        t_profile + sum(v["render_s"] + v["audit_s"] for v in summary["variants"].values()), 2)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="E2E-прогон рендера и аудита")
    parser.add_argument("templates", nargs="*", help="пути к PPTX-шаблонам")
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "e2e_out")
    parser.add_argument("--json", action="store_true", help="вывести сводку в JSON")
    args = parser.parse_args()

    templates = find_templates(args.templates)
    if not templates:
        print("PPTX-шаблоны не найдены: положите файлы в data/templates/ "
              "(см. docs/README.md §3) или передайте путь аргументом.")
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    deck = build_deck()
    report: dict = {}
    failures = 0
    for template in templates:
        if not template.exists():
            print(f"ПРОПУСК: нет файла {template}")
            continue
        print(f"\n=== {template.name} ===")
        try:
            summary = render_all(template, args.out, deck)
        except Exception as exc:  # noqa: BLE001
            import traceback

            traceback.print_exc()
            print(f"  ОШИБКА: {exc}")
            failures += 1
            continue
        report[template.name] = summary
        print(f"  профиль {summary['profile_s']} c")
        for variant, info in summary["variants"].items():
            mark = "OK" if info["ok"] else "FAIL(слайды)"
            print(f"  {variant:8s} {info['slides']}/{info['expected']} слайдов {mark} "
                  f"| рендер {info['render_s']} c, аудит {info['audit_s']} c "
                  f"| ошибок {info['errors']}, замечаний {info['warnings']}"
                  + (f" | {info['issues']}" if info["issues"] else ""))
            if not info["ok"]:
                failures += 1
        print(f"  ИТОГО {summary['total_s']} c на 3 варианта")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
