"""Генерация синтетических PPTX-фикстур для тестов и e2e-прогона.

Зачем: шаблоны VK и контент-пакет не хранятся в git (ADR-002), а тесты должны
работать в чистом клоне и в CI. Фикстуры маленькие (десятки КБ), строятся
python-pptx и намеренно не похожи на шаблоны VK: другие палитра, шрифты,
размер слайда и имена макетов.

Дополнительно реализована мутация реального шаблона (`mutate_template`) —
она имитирует «незнакомый шаблон» с финала: имена макетов вида `Slide N`,
другая палитра и другие шрифты.

Запуск:
    python tools/make_fixtures.py            # записать tests/fixtures/*.pptx
    python tools/make_fixtures.py --list     # показать, что будет создано
"""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]

A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"

# намеренно «чужие» токены: не пересекаются с палитрой шаблонов VK
FIXTURES = {
    "synthetic_16x9": dict(accent="#1F7A5C", secondary="#C2410C",
                           major="Georgia", minor="Verdana",
                           slide_size=(13.333, 7.5)),
    "synthetic_4x3": dict(accent="#5B21B6", secondary="#0F766E",
                          major="Trebuchet MS", minor="Tahoma",
                          slide_size=(10.0, 7.5)),
}


def _patch_theme_xml(xml: bytes, accent: str, secondary: str,
                     major: str, minor: str) -> bytes:
    """Правит цветовую схему и шрифты в XML одной темы."""
    from lxml import etree

    root = etree.fromstring(xml)
    for scheme in root.iter(f"{{{A_NS}}}clrScheme"):
        for child in scheme:
            tag = etree.QName(child).localname
            srgb = child.find(f"{{{A_NS}}}srgbClr")
            if srgb is None:
                continue
            if tag == "accent1":
                srgb.set("val", accent.lstrip("#"))
            elif tag == "accent2":
                srgb.set("val", secondary.lstrip("#"))
    for fonts in root.iter(f"{{{A_NS}}}fontScheme"):
        for tag, typeface in (("majorFont", major), ("minorFont", minor)):
            node = fonts.find(f"{{{A_NS}}}{tag}")
            if node is None:
                continue
            latin = node.find(f"{{{A_NS}}}latin")
            if latin is not None:
                latin.set("typeface", typeface)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


# фиксированная дата в zip: фикстуры должны быть побайтово воспроизводимы,
# иначе каждый перезапуск генератора даёт «изменённые» файлы в git
FIXED_ZIP_DATE = (2026, 1, 1, 0, 0, 0)


def _copy_zip(data: bytes, transform=None) -> bytes:
    """Пересобирает PPTX-архив, применяя transform(имя, байты) к каждой части."""
    src = ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with ZipFile(out, "w", ZIP_DEFLATED) as dst:
        for item in src.infolist():
            payload = src.read(item.filename)
            if transform is not None:
                payload = transform(item.filename, payload)
            info = ZipInfo(item.filename, date_time=FIXED_ZIP_DATE)
            info.compress_type = item.compress_type
            info.external_attr = item.external_attr
            dst.writestr(info, payload)
    return out.getvalue()


def _patch_theme(data: bytes, accent: str, secondary: str,
                 major: str, minor: str) -> bytes:
    """Правит тему во всех theme*.xml внутри PPTX."""
    def transform(name: str, payload: bytes) -> bytes:
        if name.startswith("ppt/theme/") and name.endswith(".xml"):
            return _patch_theme_xml(payload, accent, secondary, major, minor)
        return payload

    return _copy_zip(data, transform)


def build_template(accent: str = "#1F7A5C", secondary: str = "#C2410C",
                   major: str = "Georgia", minor: str = "Verdana",
                   slide_size: tuple[float, float] = (13.333, 7.5),
                   layout_names: list[str] | None = None) -> bytes:
    """Собирает небольшой PPTX-шаблон: титул, раздел, два контентных слайда, финал."""
    from pptx import Presentation
    from pptx.util import Inches, Pt

    prs = Presentation()
    prs.slide_width = Inches(slide_size[0])
    prs.slide_height = Inches(slide_size[1])

    layouts = {layout.name: layout for layout in prs.slide_masters[0].slide_layouts}
    order = list(layouts)

    # слайд-образец 1: титул (даёт парсеру типографику заголовка)
    title_slide = prs.slides.add_slide(layouts[order[0]])
    if title_slide.shapes.title is not None:
        tf = title_slide.shapes.title.text_frame
        tf.text = "Название презентации"
        tf.paragraphs[0].runs[0].font.size = Pt(40)
    for ph in title_slide.placeholders:
        if ph.placeholder_format.idx == 1:
            ph.text_frame.text = "Имя спикера, должность"
            for p in ph.text_frame.paragraphs:
                for r in p.runs:
                    r.font.size = Pt(18)

    # слайд-образец 2: заголовок + контент (буллеты и подзаголовок)
    content_layout = layouts.get("Title and Content") or layouts[order[1]]
    slide = prs.slides.add_slide(content_layout)
    if slide.shapes.title is not None:
        slide.shapes.title.text_frame.text = "Заголовок слайда"
        slide.shapes.title.text_frame.paragraphs[0].runs[0].font.size = Pt(32)
    for ph in slide.placeholders:
        if ph.placeholder_format.idx == 1:
            tf = ph.text_frame
            tf.text = "Первый тезис"
            for i, extra in enumerate(("Второй тезис", "Третий тезис"), start=1):
                tf.add_paragraph().text = extra
            for p in tf.paragraphs:
                for r in p.runs:
                    r.font.size = Pt(16)

    # слайд-образец 3: две колонки — даёт парсеру паттерн сетки
    two_col = layouts.get("Two Content")
    if two_col is not None:
        slide = prs.slides.add_slide(two_col)
        if slide.shapes.title is not None:
            slide.shapes.title.text_frame.text = "Два блока"
            slide.shapes.title.text_frame.paragraphs[0].runs[0].font.size = Pt(30)
        for ph in slide.placeholders:
            if ph.placeholder_format.idx in (1, 2):
                ph.text_frame.text = "Текст блока"
                for p in ph.text_frame.paragraphs:
                    for r in p.runs:
                        r.font.size = Pt(14)

    buf = io.BytesIO()
    prs.save(buf)
    data = _patch_theme(buf.getvalue(), accent, secondary, major, minor)

    if layout_names:
        data = _rename_layouts(data, layout_names)
    return data


def _rename_layouts(data: bytes, names: list[str]) -> bytes:
    """Переименовывает макеты (имитация шаблона без говорящих имён)."""
    from lxml import etree

    src = ZipFile(io.BytesIO(data))
    layout_files = sorted(n for n in src.namelist()
                          if n.startswith("ppt/slideLayouts/slideLayout") and n.endswith(".xml"))
    mapping = {name: names[i % len(names)] for i, name in enumerate(layout_files)}

    def transform(name: str, payload: bytes) -> bytes:
        if name not in mapping:
            return payload
        root = etree.fromstring(payload)
        for node in root.iter():
            if etree.QName(node).localname == "cSld":
                node.set("name", mapping[name])
                break
        return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)

    return _copy_zip(data, transform)


def mutate_template(data: bytes, *, prefix: str = "Slide",
                    accent: str = "#B91C1C", major: str = "Courier New",
                    minor: str = "Courier New") -> bytes:
    """Мутирует произвольный шаблон: имена макетов, палитра, шрифты.

    Используется как «незнакомый шаблон»: если решение опирается на имена
    макетов или конкретные цвета, оно сломается на такой мутации.
    """
    from pptx import Presentation
    from lxml import etree

    prs = Presentation(io.BytesIO(data))
    buf = io.BytesIO()
    prs.save(buf)
    src = ZipFile(io.BytesIO(buf.getvalue()))
    layout_files = sorted(n for n in src.namelist()
                          if n.startswith("ppt/slideLayouts/slideLayout") and n.endswith(".xml"))
    mapping = {name: f"{prefix} {i + 1}" for i, name in enumerate(layout_files)}

    def transform(name: str, payload: bytes) -> bytes:
        if name in mapping:
            root = etree.fromstring(payload)
            for node in root.iter():
                if etree.QName(node).localname == "cSld":
                    node.set("name", mapping[name])
                    break
            return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
        if name.startswith("ppt/theme/") and name.endswith(".xml"):
            return _patch_theme_xml(payload, accent, accent, major, minor)
        return payload

    return _copy_zip(buf.getvalue(), transform)


def _rel(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except Exception:  # noqa: BLE001
        return str(path)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Синтетические PPTX-фикстуры")
    parser.add_argument("--out", type=Path, default=ROOT / "tests" / "fixtures")
    parser.add_argument("--list", action="store_true", help="только показать план")
    args = parser.parse_args(argv)

    for name, params in FIXTURES.items():
        print(f"  {name}.pptx: accent={params['accent']}, шрифты {params['major']}/"
              f"{params['minor']}, слайд {params['slide_size'][0]}×{params['slide_size'][1]}")
    if args.list:
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    for name, params in FIXTURES.items():
        data = build_template(**params)
        target = args.out / f"{name}.pptx"
        target.write_bytes(data)
        print(f"записано: {_rel(target)} ({len(data) // 1024} КБ)")

    # незнакомый шаблон: мутация 16:9-фикстуры
    mutated = mutate_template(build_template(**FIXTURES["synthetic_16x9"]))
    target = args.out / "unfamiliar_mutated.pptx"
    target.write_bytes(mutated)
    print(f"записано: {_rel(target)} ({len(mutated) // 1024} КБ)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
