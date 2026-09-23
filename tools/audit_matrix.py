"""Матрица «шаблон × вариант вёрстки»: профиль, роли макетов и результат аудита.

Помогает быстро увидеть, одинаково ли валидны три варианта на разных шаблонах
(требование ТЗ) и какие проблемы воспроизводятся системно.

Запуск:
    python tools/audit_matrix.py                 # синтетические фикстуры + доступные шаблоны
    python tools/audit_matrix.py tpl1.pptx tpl2.pptx
    python tools/audit_matrix.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from app.audit.checks import Audit  # noqa: E402
from app.layout.engine import DesignContext, LayoutEngine  # noqa: E402
from app.planner.fallback import FallbackPlanner  # noqa: E402
from app.render.pptx_renderer import Renderer  # noqa: E402
from app.template.parser import TemplateParser  # noqa: E402

BRIEF = ("Платформа аналитики сократила время подготовки отчётов на 40%, "
         "автоматизировала 12 задач, охватила 5 подразделений. 2000 сотрудников "
         "используют её еженедельно. План: подключить 10 отделов и внедрить "
         "ML-предсказания выручки.")


def collect_templates(extra: list[str]) -> list[tuple[str, bytes]]:
    from tools.make_fixtures import FIXTURES, build_template, mutate_template

    items: list[tuple[str, bytes]] = [
        (name, build_template(**params)) for name, params in FIXTURES.items()]
    items.append(("unfamiliar_mutated",
                  mutate_template(build_template(**FIXTURES["synthetic_16x9"]))))
    for name in extra:
        path = Path(name)
        if path.exists():
            items.append((path.stem[:24], path.read_bytes()))
        else:
            print(f"нет файла: {name}")
    for directory in (ROOT / "data" / "templates", ROOT):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.pptx")):
            if any(path.name == n for n, _ in items):
                continue
            items.append((path.stem[:24], path.read_bytes()))
    return items


def run_one(name: str, template_bytes: bytes, deck) -> dict:
    profile = TemplateParser(template_bytes).parse().to_dict()
    dc = DesignContext(
        fonts={"headline": profile.get("headline_font"), "body": profile.get("body_font")},
        palette=profile.get("palette", []),
        type_scale=profile.get("type_scale", {}),
        slide_w=profile["slide_size"]["w_in"],
        slide_h=profile["slide_size"]["h_in"],
    )
    result: dict = {
        "layouts": len(profile["layouts"]),
        "roles": {role: len(ids) for role, ids in profile["layout_groups"].items()},
        "headline_font": profile.get("headline_font"),
        "body_font": profile.get("body_font"),
        "palette": len(profile.get("palette", [])),
        "variants": {},
    }
    for variant in ("compact", "cards", "split"):
        renderer = Renderer(profile, variant=variant, template_bytes=template_bytes)
        plan_map = {}
        for i, slide in enumerate(deck.slides):
            layout = renderer._pick_layout(slide.slide_type)
            plan_map[i] = LayoutEngine(dc, variant=variant).compose(
                slide, renderer._canvas(layout))
        audit = Audit(profile).audit(deck, renderer.render(deck, plan_map))
        codes: dict[str, int] = {}
        for issue in audit["issues"]:
            codes[issue["code"]] = codes.get(issue["code"], 0) + 1
        result["variants"][variant] = {
            "errors": audit["errors"], "warnings": audit["warnings"], "codes": codes,
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Матрица аудита по шаблонам и вариантам")
    parser.add_argument("templates", nargs="*", help="дополнительные PPTX")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    deck = FallbackPlanner().plan(BRIEF, "", "project")
    report = {}
    for name, template_bytes in collect_templates(args.templates):
        try:
            report[name] = run_one(name, template_bytes, deck)
        except Exception as exc:  # noqa: BLE001
            report[name] = {"error": f"{type(exc).__name__}: {exc}"}

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    for name, data in report.items():
        if "error" in data:
            print(f"\n{name}: ОШИБКА {data['error']}")
            continue
        print(f"\n{name}: макетов {data['layouts']} {data['roles']}")
        print(f"  шрифты: {data['headline_font']} / {data['body_font']}, "
              f"палитра {data['palette']}")
        for variant, info in data["variants"].items():
            codes = ", ".join(f"{k}×{v}" for k, v in sorted(info["codes"].items())) or "чисто"
            print(f"  {variant:8s} ошибок {info['errors']}, замечаний {info['warnings']}: {codes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
