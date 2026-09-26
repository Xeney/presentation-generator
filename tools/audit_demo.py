"""Демонстрация аудита и авто-фиксов для защиты: «до» (красные рамки) → «после».

Собирает колоду с намеренными дефектами на выбранном шаблоне, показывает, что
детерминированный аудит их находит, применяет авто-фиксы (без модели) и
показывает результат. Всё детерминировано: один запуск — один и тот же результат.

Запуск:
    python tools/audit_demo.py
    python tools/audit_demo.py --template "VK Tech шаблон.pptx" --out docs/evidence/audit
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402
from pptx import Presentation  # noqa: E402

from app.audit.checks import Audit  # noqa: E402
from app.audit.fixes import FixEngine  # noqa: E402
from app.layout.engine import DesignContext  # noqa: E402
from app.models.deck import Block, Chart, ChartType, Deck, Slide, SlideType, Table  # noqa: E402
from app.render.images import draw_issue_boxes  # noqa: E402
from app.render.pdf import pptx_to_pngs  # noqa: E402
from app.render.pptx_renderer import Renderer  # noqa: E402
from app.template.parser import TemplateParser  # noqa: E402


def defective_deck() -> Deck:
    """Колода с типовыми дефектами, которые ловит детерминированный аудит."""
    long_bullets = [
        "Платформа аналитики сократила время подготовки отчётов на сорок процентов "
        "за квартал и автоматизировала двенадцать рутинных задач",
        "Охват вырос до пяти подразделений",
        "2000 сотрудников используют платформу еженедельно",
        "Подключены десять отделов",
        "Внедрены ML-предсказания выручки",
        "Средний чек вырос на восемь процентов",
        "Время сборки отчёта сократилось до одиннадцати минут",
        "Доля ошибок в данных снижена до одного процента",
    ]
    return Deck(title="Демонстрация аудита", slides=[
        Slide(slide_type=SlideType.TITLE, heading="Демонстрация аудита и авто-фиксов"),
        Slide(slide_type=SlideType.CONTENT, heading="Плотный слайд с длинным списком",
              blocks=[Block(kind="bullets", title="Ключевые тезисы", items=long_bullets)]),
        Slide(slide_type=SlideType.CONTENT, heading="Таблица без служебных данных",
              blocks=[Block(kind="table", table=Table(
                  header=["Метрика", "Значение", "Комментарий"],
                  rows=[["Ошибки", "4%", "за квартал"], ["Охват", "5", "подразделений"]])),
                      Block(kind="chart", chart=Chart(
                          type=ChartType.COLUMN, categories=["Q1", "Q2", "Q3"],
                          series=[{"name": "Без подписей", "values": [3.0, 150.0, 870.0]}]))]),
        Slide(slide_type=SlideType.CONTENT, heading="Тот же плотный слайд с длинным списком",
              blocks=[Block(kind="bullets", title="Ключевые тезисы", items=long_bullets)]),
        Slide(slide_type=SlideType.FINAL, heading="Спасибо за внимание"),
    ])


def render(deck: Deck, profile: dict, template: bytes, variant: str) -> bytes:
    renderer = Renderer(profile, variant=variant, template_bytes=template)
    return renderer.render_deck(deck, DesignContext.from_profile(profile))


def save_slide(png_bytes: bytes, index: int, path: Path, boxes=None) -> None:
    with Image.open(io.BytesIO(png_bytes)) as image:
        if boxes:
            image = draw_issue_boxes(image, boxes)
        image.save(path)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Демо аудита и авто-фиксов")
    parser.add_argument("--template", type=Path, default=None)
    parser.add_argument("--variant", default="compact")
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "evidence" / "audit")
    args = parser.parse_args(argv)

    if args.template and args.template.exists():
        template_path = args.template
    else:
        from tools.make_fixtures import FIXTURES, build_template

        template_bytes = build_template(**FIXTURES["synthetic_16x9"])
        template_path = None

    if template_path is not None:
        template_bytes = template_path.read_bytes()
    profile = TemplateParser(template_bytes).parse().to_dict()
    deck = defective_deck()

    args.out.mkdir(parents=True, exist_ok=True)
    pptx = render(deck, profile, template_bytes, args.variant)
    audit = Audit(profile).audit(deck, pptx)
    issues = audit["issues"]
    print(f"аудит «до»: ошибок {audit['errors']}, замечаний {audit['warnings']}")
    for issue in issues:
        print(f"  слайд {issue['slide'] + 1:2d} · {issue['severity']:7s} · "
              f"{issue['code']:22s} {issue['message'][:60]}")

    pngs = pptx_to_pngs(pptx, dpi=100)
    by_slide: dict[int, list] = {}
    for issue in issues:
        if issue["slide"] >= 0 and issue.get("bbox"):
            by_slide.setdefault(issue["slide"], []).append(tuple(issue["bbox"]))
    for index, boxes in sorted(by_slide.items()):
        save_slide(pngs[index], index, args.out / f"before_s{index + 1}.png", boxes)
    (args.out / "before_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=1), encoding="utf-8")

    # фиксы применяются проходами: разбиение слайда рождает новые слайды,
    # которые тоже нужно проверить (как кнопка «Исправить выбранное» в UI)
    engine = FixEngine(profile)
    applied: list[dict] = []
    skipped: list[dict] = []
    fixed_deck = deck
    fixed_pptx = pptx
    audit_after = audit
    for attempt in range(1, 4):
        current_issues = audit_after["issues"]
        if not current_issues:
            break
        fixed_deck, outcomes = engine.apply(fixed_deck, current_issues)
        pass_applied = [o for o in outcomes if o["status"] == "applied"]
        skipped = [o for o in outcomes if o["status"] == "skipped"]
        applied += pass_applied
        print(f"\nпроход фиксов {attempt}: применено {len(pass_applied)}, "
              f"пропущено {len(skipped)}")
        for item in pass_applied:
            print(f"  слайд {item['slide'] + 1:2d} · {item['action']:14s} "
                  f"{item['detail'][:60]}")
        if not pass_applied:
            break
        fixed_pptx = render(fixed_deck, profile, template_bytes, args.variant)
        audit_after = Audit(profile).audit(fixed_deck, fixed_pptx)

    print(f"\nаудит «после»: ошибок {audit_after['errors']}, "
          f"замечаний {audit_after['warnings']}")
    for issue in audit_after["issues"]:
        print(f"  слайд {issue['slide'] + 1:2d} · {issue['code']:22s} "
              f"{issue['message'][:60]}")

    pngs_after = pptx_to_pngs(fixed_pptx, dpi=100)
    by_slide_after: dict[int, list] = {}
    for issue in audit_after["issues"]:
        if issue["slide"] >= 0 and issue.get("bbox"):
            by_slide_after.setdefault(issue["slide"], []).append(tuple(issue["bbox"]))
    for index in sorted(set(by_slide) | set(by_slide_after)):
        boxes = by_slide_after.get(index)
        save_slide(pngs_after[index], index, args.out / f"after_s{index + 1}.png", boxes)
    (args.out / "after_audit.json").write_text(
        json.dumps(audit_after, ensure_ascii=False, indent=1), encoding="utf-8")
    (args.out / "fixes.json").write_text(
        json.dumps({"applied": applied, "skipped": skipped},
                   ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\nартефакты: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
