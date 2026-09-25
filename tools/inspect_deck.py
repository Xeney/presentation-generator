"""Визуальная проверка колоды: рендер слайдов в PNG для просмотра глазами.

Помогает оценивать качество вёрстки (критерий ТЗ №4) и ловить дефекты, которые
детерминированный аудит не видит: текст поверх декора, чёрный текст на тёмной
плашке, отсутствие палитры шаблона, дубликаты пунктов.

Запуск:
    python tools/inspect_deck.py --template "Шаблон презентации VK Education.pptx"
    python tools/inspect_deck.py --variant cards --slides 0,1,2,4 --offline
"""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

BRIEF = (
    "Платформа внутренней аналитики: за квартал время подготовки отчётов "
    "сократилось на 40%, автоматизированы 12 рутинных задач, охват вырос до "
    "5 подразделений. Платформой пользуются 2000 сотрудников еженедельно. "
    "План — подключить 10 отделов к концу года и внедрить ML-предсказания выручки."
)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="PNG-просмотр слайдов колоды")
    parser.add_argument("--template", type=Path, default=None)
    parser.add_argument("--variant", default="compact",
                        choices=["compact", "cards", "split"])
    parser.add_argument("--slides", default="0,1,2,3,4")
    parser.add_argument("--offline", action="store_true",
                        help="офлайн-планировщик (без обращения к модели)")
    parser.add_argument("--pptx", type=Path, default=None,
                        help="готовый PPTX: только PNG, без планирования и аудита")
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "inspect")
    parser.add_argument("--dpi", type=int, default=110)
    args = parser.parse_args(argv)

    indexes = [int(x) for x in args.slides.split(",") if x.strip().isdigit()]

    if args.pptx is not None:
        from app.render.pdf import pptx_to_pngs

        if not args.pptx.exists():
            print(f"файл не найден: {args.pptx}")
            return 1
        args.out.mkdir(parents=True, exist_ok=True)
        pngs = pptx_to_pngs(args.pptx.read_bytes(), dpi=args.dpi)
        for index in indexes:
            if 0 <= index < len(pngs):
                path = args.out / f"{args.pptx.stem}_s{index + 1}.png"
                path.write_bytes(pngs[index])
                try:
                    shown = path.resolve().relative_to(ROOT)
                except ValueError:
                    shown = path
                print("  " + str(shown))
        return 0

    if args.offline:
        import os

        os.environ["DISABLE_LLM"] = "true"
    from app.config import get_settings

    get_settings.cache_clear()

    template = args.template
    if template is None or not template.exists():
        for directory in (ROOT / "data" / "templates", ROOT):
            for candidate in sorted(directory.glob("*.pptx")):
                if not candidate.name.lower().startswith(("vk tech", "unfamiliar")):
                    template = candidate
                    break
            if template:
                break
    if template is None or not template.exists():
        print("шаблон не найден: передайте --template")
        return 1

    from app.audit.checks import Audit
    from app.layout.engine import DesignContext
    from app.planner.planner import Planner
    from app.render.pdf import pptx_to_pngs
    from app.render.pptx_renderer import Renderer
    from app.template.parser import TemplateParser

    settings = get_settings()
    print(f"шаблон: {template.name}")
    print(f"планировщик: {settings.planner_label} | VLM: {settings.vlm_label}")

    profile = TemplateParser(template).parse().to_dict()
    result = Planner().plan(BRIEF, "", "project", profile)
    deck = result.deck
    print(f"колода: {len(deck.slides)} слайдов, LLM: {result.used_llm} "
          f"({result.label}), нормализаций {len(result.normalizations)}")

    # структура колоды: что именно сгенерировала модель
    for index, slide in enumerate(deck.slides):
        blocks = []
        for block in slide.blocks:
            count = (len(block.items) or len(block.factoids)
                     or (1 if block.table else 0) or (1 if block.chart else 0)
                     or (1 if block.text else 0))
            blocks.append(f"{block.kind}({count})")
        print(f"  {index:2d} {slide.slide_type.value:8s} {slide.heading[:44]:46s} "
              f"{' '.join(blocks) or '—'}")

    dc = DesignContext.from_profile(profile)
    renderer = Renderer(profile, variant=args.variant, template_bytes=template.read_bytes())
    pptx = renderer.render_deck(deck, dc)
    audit = Audit(profile).audit(deck, pptx)
    print(f"аудит {args.variant}: ошибок {audit['errors']}, замечаний {audit['warnings']}")
    for issue in audit["issues"][:10]:
        print(f"  [слайд {issue['slide']}] {issue['code']}: {issue['message'][:90]}")

    args.out.mkdir(parents=True, exist_ok=True)
    deck_path = args.out / f"{template.stem}_{args.variant}.pptx"
    deck_path.write_bytes(pptx)
    print(f"PPTX: {deck_path.relative_to(ROOT)}")

    pngs = pptx_to_pngs(pptx, dpi=args.dpi)
    saved = []
    for index in indexes:
        if 0 <= index < len(pngs):
            path = args.out / f"{template.stem}_{args.variant}_s{index + 1}.png"
            path.write_bytes(pngs[index])
            saved.append(str(path.relative_to(ROOT)))
    print("PNG для просмотра:")
    for path in saved:
        print("  " + path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
