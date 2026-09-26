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
from app.render.html_renderer import render_html  # noqa: E402
from app.render.html_to_pptx import html_to_pptx  # noqa: E402
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


def _count_kinds(profile: dict) -> dict[str, int]:
    kinds: dict[str, int] = {}
    for layout in profile.get("layouts", []):
        key = layout.get("kind", "?")
        kinds[key] = kinds.get(key, 0) + 1
    return kinds


def run_one(name: str, template_bytes: bytes, deck, with_html: bool = False) -> dict:
    profile = TemplateParser(template_bytes).parse().to_dict()
    dc = DesignContext.from_profile(profile)
    result: dict = {
        "layouts": len(profile["layouts"]),
        "roles": {role: len(ids) for role, ids in profile["layout_groups"].items()},
        "kinds": _count_kinds(profile),
        "headline_font": profile.get("headline_font"),
        "body_font": profile.get("body_font"),
        "palette": len(profile.get("palette", [])),
        "variants": {},
        "variants_html": {},
    }
    for variant in ("compact", "cards", "split"):
        renderer = Renderer(profile, variant=variant, template_bytes=template_bytes)
        audit = Audit(profile).audit(deck, renderer.render_deck(deck, dc))
        codes: dict[str, int] = {}
        for issue in audit["issues"]:
            codes[issue["code"]] = codes.get(issue["code"], 0) + 1
        result["variants"][variant] = {
            "errors": audit["errors"], "warnings": audit["warnings"], "codes": codes,
        }
        if with_html:
            # HTML-путь: тот же план → HTML → PPTX нативными фигурами (ADR-035/036)
            page = render_html(deck, profile, variant=variant,
                               template_bytes=template_bytes)
            converted = html_to_pptx(page, profile, template_bytes=template_bytes)
            html_audit = Audit(profile).audit(deck, converted)
            html_codes: dict[str, int] = {}
            for issue in html_audit["issues"]:
                html_codes[issue["code"]] = html_codes.get(issue["code"], 0) + 1
            result["variants_html"][variant] = {
                "errors": html_audit["errors"], "warnings": html_audit["warnings"],
                "codes": html_codes,
            }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Матрица аудита по шаблонам и вариантам")
    parser.add_argument("templates", nargs="*", help="дополнительные PPTX")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--html", action="store_true",
                        help="добавить HTML-путь (HTML → PPTX нативными фигурами)")
    parser.add_argument("--layouts", action="store_true",
                        help="показать классификацию макетов (имя → роль/тип/причина)")
    parser.add_argument("--picks", action="store_true",
                        help="показать, какой макет выбран для каждого слайда колоды")
    args = parser.parse_args()

    if args.picks:
        for name, template_bytes in collect_templates(args.templates):
            try:
                profile = TemplateParser(template_bytes).parse().to_dict()
                renderer = Renderer(profile, variant="compact", template_bytes=template_bytes)
                deck = FallbackPlanner().plan(BRIEF, "", "project")
            except Exception as exc:  # noqa: BLE001
                print(f"\n{name}: ОШИБКА {exc}")
                continue
            print(f"\n{name}")
            for i, slide in enumerate(deck.slides):
                layout = renderer._pick_layout(slide.slide_type, slide)
                print(f"  {i:2d} {slide.slide_type.value:8s} → {layout['id']:>4} "
                      f"{layout['role']:8s} {layout['kind']:12s} "
                      f"{layout['name'][:26]:26s} | {slide.heading[:44]}")
        return 0

    if args.layouts:
        for name, template_bytes in collect_templates(args.templates):
            try:
                profile = TemplateParser(template_bytes).parse().to_dict()
            except Exception as exc:  # noqa: BLE001
                print(f"\n{name}: ОШИБКА {exc}")
                continue
            print(f"\n{name} — макетов {len(profile['layouts'])}")
            for layout in profile["layouts"]:
                print(f"  {layout['id']:>4}  {layout['name'][:38]:<38} "
                      f"{layout['role']:<8} {layout['kind']:<13} "
                      f"score={layout['score']:.2f}  {layout['role_reason']}")
        return 0

    deck = FallbackPlanner().plan(BRIEF, "", "project")
    report = {}
    for name, template_bytes in collect_templates(args.templates):
        try:
            report[name] = run_one(name, template_bytes, deck, with_html=args.html)
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
        print(f"  типы: {data.get('kinds')}")
        print(f"  шрифты: {data['headline_font']} / {data['body_font']}, "
              f"палитра {data['palette']}")
        for variant, info in data["variants"].items():
            codes = ", ".join(f"{k}×{v}" for k, v in sorted(info["codes"].items())) or "чисто"
            print(f"  {variant:8s} ошибок {info['errors']}, замечаний {info['warnings']}: {codes}")
            html_info = (data.get("variants_html") or {}).get(variant)
            if html_info is not None:
                html_codes = ", ".join(
                    f"{k}×{v}" for k, v in sorted(html_info["codes"].items())) or "чисто"
                diff = html_info["errors"] - info["errors"]
                print(f"  {variant + '·html':8s} ошибок {html_info['errors']}, "
                      f"замечаний {html_info['warnings']}: {html_codes} "
                      f"(расхождение с классикой: {diff:+d})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
