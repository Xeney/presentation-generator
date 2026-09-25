"""Визуальный чек-лист по слайдам: автопроверки глазами дизайнера + PNG.

Инструмент закрывает критерий ТЗ №4 («визуально приемлемая вёрстка»): по каждому
слайду считаются измеримые признаки чек-листа (воздух, выравнивание, контраст,
палитра, визуальный якорь, пустые половины, сироты, читаемость KPI, перекрытие
титула декором) и складываются PNG-миниатюры для осмотра глазами.

Запуск:
    python tools/visual_pass.py --job data/jobs/<id>            # из готового задания
    python tools/visual_pass.py --job data/jobs/<id> --variant compact --slides 0,1,2
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from pptx import Presentation  # noqa: E402

from app.audit.checks import Audit, _lum  # noqa: E402
from app.layout.engine import DesignContext  # noqa: E402
from app.models.deck import Deck  # noqa: E402
from app.render.images import contrast_ratio  # noqa: E402
from app.render.pdf import pptx_to_pngs  # noqa: E402
from app.render.pptx_renderer import Renderer  # noqa: E402

EMU = 914400


def _text_shapes(slide):
    out = []
    for shape in slide.shapes:
        if getattr(shape, "has_text_frame", False) and shape.text_frame.text.strip():
            out.append(shape)
    return out


def _fill_ratio(prs, slide, rect, geo) -> float:
    """Доля площади прямоугольника, занятая контентом (по грубой сетке)."""
    grid = 10
    cells = set()
    x0, y0, w, h = rect
    for g in geo:
        if not g["filled"] or g["w"] <= 0:
            continue
        gx, gy = g["x"] / EMU, g["y"] / EMU
        gw, gh = g["w"] / EMU, g["h"] / EMU
        if gw >= 0.9 * prs.slide_width / EMU and gh >= 0.9 * prs.slide_height / EMU:
            continue  # фон
        ix, iy = max(gx, x0), max(gy, y0)
        ix2, iy2 = min(gx + gw, x0 + w), min(gy + gh, y0 + h)
        if ix2 <= ix or iy2 <= iy:
            continue
        for cx in range(int((ix - x0) / w * grid), int((ix2 - x0) / w * grid) + 1):
            for cy in range(int((iy - y0) / h * grid), int((iy2 - y0) / h * grid) + 1):
                cells.add((min(grid - 1, max(0, cx)), min(grid - 1, max(0, cy))))
    return len(cells) / float(grid * grid)


def _accent_used(prs, slide, profile) -> bool:
    """Есть ли на слайде акцентный цвет шаблона (тот же, что выбирает рендер)."""
    from app.layout.engine import DesignContext

    accent = DesignContext.from_profile(profile).accent
    if not accent:
        return True
    accent = accent.upper()
    for shape in slide.shapes:
        try:
            if shape.has_text_frame:
                for paragraph in shape.text_frame.paragraphs:
                    for run in paragraph.runs:
                        color = run.font.color
                        if color and color.type is not None and color.rgb is not None:
                            if str(color.rgb).upper() == accent.lstrip("#"):
                                return True
            fill = shape.fill
            if fill.type is not None and fill.fore_color and fill.fore_color.rgb is not None:
                if str(fill.fore_color.rgb).upper() == accent.lstrip("#"):
                    return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _biggest_text(prs, slide) -> float:
    biggest = 0.0
    for shape in _text_shapes(slide):
        for paragraph in shape.text_frame.paragraphs:
            for run in paragraph.runs:
                size = run.font.size
                if size:
                    biggest = max(biggest, size.pt)
    return biggest


def _orphans(slide) -> int:
    """Строки-сироты: последняя строка абзаца заметно короче остальных."""
    from app.layout.geometry import lines_needed

    orphans = 0
    for shape in _text_shapes(slide):
        width_in = (shape.width or 0) / EMU
        if width_in <= 0.5:
            continue
        for paragraph in shape.text_frame.paragraphs:
            text = "".join(r.text for r in paragraph.runs).strip()
            sizes = [r.font.size.pt for r in paragraph.runs if r.font.size]
            if not text or not sizes:
                continue
            lines = lines_needed(text, width_in, max(sizes))
            if lines < 2:
                continue
            # приблизительно: сколько символов попадает в последнюю строку
            cpl = max(1, int((width_in * 72) / (0.53 * max(sizes))))
            tail = len(text) % cpl
            if 0 < tail <= max(3, cpl * 0.12):
                orphans += 1
    return orphans


def check_slide(prs, slide, index, profile, geo) -> dict:
    sw = prs.slide_width / EMU
    sh = prs.slide_height / EMU
    title = None
    try:
        if slide.shapes.title is not None:
            title = slide.shapes.title
    except Exception:  # noqa: BLE001
        pass
    title_box = None
    if title is not None and title.width:
        title_box = (title.left / EMU, title.top / EMU,
                     title.width / EMU, title.height / EMU)
    right_half = (sw / 2, 0.0, sw / 2, sh)
    bottom_half = (0.0, sh / 2, sw, sh / 2)
    min_contrast = 9.9
    for shape in _text_shapes(slide):
        try:
            fill_hex = Audit(profile)._shape_fill_hex(shape)
            bg = fill_hex or Audit(profile).bg_color
            for paragraph in shape.text_frame.paragraphs:
                for run in paragraph.runs:
                    color = run.font.color
                    if color and color.type is not None and color.rgb is not None:
                        ratio = contrast_ratio("#" + str(color.rgb).upper(), bg)
                        min_contrast = min(min_contrast, ratio)
        except Exception:  # noqa: BLE001
            continue
    return {
        "slide": index,
        "layout": slide.slide_layout.name,
        "heading": (title.text_frame.text.strip()[:60] if title else ""),
        "words_in_heading": len((title.text_frame.text if title else "").split()),
        "fill_total": round(_fill_ratio(prs, slide, (0, 0, sw, sh), geo), 2),
        "fill_right_half": round(_fill_ratio(prs, slide, right_half, geo), 2),
        "fill_bottom_half": round(_fill_ratio(prs, slide, bottom_half, geo), 2),
        "accent_used": _accent_used(prs, slide, profile),
        "biggest_pt": _biggest_text(prs, slide),
        "min_contrast": round(min_contrast, 2) if min_contrast < 9.9 else None,
        "orphans": _orphans(slide),
        "text_shapes": len(_text_shapes(slide)),
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Визуальный чек-лист слайдов")
    parser.add_argument("--job", type=Path, required=True, help="каталог data/jobs/<id>")
    parser.add_argument("--variant", default="compact")
    parser.add_argument("--slides", default="")
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "visual")
    parser.add_argument("--dpi", type=int, default=100)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    job_file = args.job / "job.json"
    if not job_file.exists():
        print(f"нет job.json: {job_file}")
        return 1
    data = json.loads(job_file.read_text(encoding="utf-8"))
    result = data.get("result") or {}
    deck = Deck.model_validate(result["deck"])
    profile = result["profile"]
    template = (args.job / "template.pptx").read_bytes()

    # картинки контент-пакета: без них блоки kind=image оставляют слот и аудит
    # честно ругается image_missing (это артефакт инструмента, а не дефект колоды)
    images = None
    corpus_id = data.get("corpus_id")
    if corpus_id:
        from app.config import get_settings
        from app.content.corpus import ContentCorpus

        corpus = ContentCorpus.load(corpus_id, get_settings().data_path)
        images = corpus.images if corpus is not None else None

    pptx = Renderer(profile, variant=args.variant, template_bytes=template,
                    images=images).render_deck(deck, DesignContext.from_profile(profile))
    auditor = Audit(profile)
    audit = auditor.audit(deck, pptx)

    prs = Presentation(__import__("io").BytesIO(pptx))
    auditor.W, auditor.H = prs.slide_width, prs.slide_height
    report = []
    for index, slide in enumerate(prs.slides):
        geo = auditor._shape_geoms(slide)
        report.append(check_slide(prs, slide, index, profile, geo))

    indexes = ([int(x) for x in args.slides.split(",") if x.strip().isdigit()]
               if args.slides else list(range(len(prs.slides))))
    args.out.mkdir(parents=True, exist_ok=True)
    stem = f"{args.job.name}_{args.variant}"
    (args.out / f"{stem}.pptx").write_bytes(pptx)
    pngs = pptx_to_pngs(pptx, dpi=args.dpi)
    for index in indexes:
        if 0 <= index < len(pngs):
            (args.out / f"{stem}_s{index + 1}.png").write_bytes(pngs[index])

    if args.json:
        print(json.dumps({"audit": {"errors": audit["errors"],
                                    "warnings": audit["warnings"],
                                    "codes": sorted({i["code"] for i in audit["issues"]})},
                          "slides": report}, ensure_ascii=False, indent=1))
        return 0

    print(f"задание {args.job.name} · вариант {args.variant} · слайдов {len(report)}")
    print(f"аудит: ошибок {audit['errors']}, замечаний {audit['warnings']} "
          f"{sorted({i['code'] for i in audit['issues']})}")
    for issue in audit["issues"][:10]:
        print(f"   [{issue['severity']}] слайд {issue['slide'] + 1}: {issue['code']}: "
              f"{issue['message'][:78]}")
    print(f"{'сл':>3} {'заполн':>7} {'право':>6} {'низ':>5} {'акцент':>7} "
          f"{'кегль':>6} {'контр':>6} {'сироты':>7}  заголовок")
    for row in report:
        print(f"{row['slide'] + 1:>3} {row['fill_total']:>7.2f} "
              f"{row['fill_right_half']:>6.2f} {row['fill_bottom_half']:>5.2f} "
              f"{('да' if row['accent_used'] else 'НЕТ'):>7} "
              f"{row['biggest_pt']:>6.0f} {str(row['min_contrast']):>6} "
              f"{row['orphans']:>7}  {row['heading'][:44]}")
    try:
        shown = args.out.resolve().relative_to(ROOT)
    except ValueError:
        shown = args.out
    print(f"\nPNG: {shown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
