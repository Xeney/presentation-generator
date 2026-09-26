"""ThemePair и типовые компоновки (ADR-035, ADR-036).

Идея ролей темы (фон → текст, акцент — декор) и порога контраста взята из
Presenton (Apache-2.0); реализация на python-pptx своя. Здесь проверяются
инварианты: текст использует foreground, акцент не попадает на низкий контраст,
KPI-число не отрывается от подписи, на всех шаблонах нет невидимых текстов.
"""
from __future__ import annotations

import io

import pytest
from pptx import Presentation

from app.audit.checks import Audit, _contrast, _hex
from app.layout.engine import DesignContext, slide_composition
from app.models.deck import Block, Chart, ChartType, Deck, Slide, SlideType, Table
from app.render.images import contrast_ratio, hex_to_rgb
from app.render.pptx_renderer import Renderer
from app.planner.fallback import FallbackPlanner

BRIEF = ("Платформа аналитики сократила время подготовки отчётов на 40%, "
         "автоматизировала 12 задач, охватила 5 подразделений.")

VARIANTS = ("compact", "cards", "split")


def _theme(profile: dict, role: str = "content") -> dict:
    layout = next((l for l in profile["layouts"] if l["role"] == role),
                  profile["layouts"][0])
    return layout.get("theme") or {}


def _declared_layout_names(prs: Presentation) -> set[str]:
    return {layout.name for master in prs.slide_masters
            for layout in master.slide_layouts}


def _scan(prs: Presentation, auditor: Audit, deck: Deck):
    """Все runs слайда с их фактическим фоном и ролями макета."""
    layouts = {l["name"]: l for l in auditor.layouts}
    for si, slide in enumerate(prs.slides):
        layout = layouts.get(slide.slide_layout.name) or {}
        theme = layout.get("theme") or {}
        for shape in slide.shapes:
            if not shape.has_text_frame or not shape.text_frame.text.strip():
                continue
            bg = auditor._effective_bg(slide, shape)
            for p in shape.text_frame.paragraphs:
                for run in p.runs:
                    if not run.text.strip():
                        continue
                    try:
                        color = "#" + str(run.font.color.rgb)
                    except Exception:  # noqa: BLE001
                        continue
                    size = run.font.size.pt if run.font.size else 14.0
                    yield {
                        "slide": si, "shape": shape, "run": run, "text": run.text,
                        "color": color.upper(), "size": size,
                        "bold": bool(run.font.bold), "bg": bg.upper(),
                        "theme": theme,
                    }


def _bullet_deck() -> Deck:
    return Deck(title="Тёмный шаблон: контраст", slides=[
        Slide(slide_type=SlideType.TITLE, heading="Титул презентации"),
        Slide(slide_type=SlideType.CONTENT, heading="Ключевые принципы",
              blocks=[Block(kind="bullets", items=[
                  "Шаблон — это набор данных, а не файл.",
                  "Модель отвечает за смысл, код — за вёрстку.",
                  "Аудит встроен в процесс, а не сбоку.",
              ])]),
        Slide(slide_type=SlideType.FINAL, heading="Финал презентации"),
    ])


def _combo_deck() -> Deck:
    """Колода со всеми типовыми компоновками: bullets, kpi, table, steps, quote."""
    return Deck(title="Все компоновки", slides=[
        Slide(slide_type=SlideType.TITLE, heading="Титул презентации"),
        Slide(slide_type=SlideType.CONTENT, heading="Тезисы проекта",
              blocks=[Block(kind="bullets", items=[
                  "Первое важное утверждение о проекте.",
                  "Второе важное утверждение о проекте.",
              ])]),
        Slide(slide_type=SlideType.CONTENT, heading="Метрики проекта",
              blocks=[Block(kind="factoids", factoids=[
                  {"value": "180", "label": "автоматических тестов"},
                  {"value": "24/24", "label": "чистых комбинаций"},
                  {"value": "34–76 с", "label": "время генерации"},
                  {"value": "0 ₽", "label": "платных вызовов"},
              ])]),
        Slide(slide_type=SlideType.CONTENT, heading="Сравнение подходов",
              blocks=[Block(kind="table", table=Table(
                  header=["Критерий", "Ручная", "Сервис"],
                  rows=[["Время", "8 ч", "1 мин"],
                        ["Ошибки", "15%", "0%"]]))]),
        Slide(slide_type=SlideType.CONTENT, heading="План внедрения",
              blocks=[Block(kind="steps", items=[
                  "Пилот в одном подразделении.",
                  "Два-три корпоративных шаблона.",
                  "Масштабирование за квартал.",
              ])]),
        Slide(slide_type=SlideType.CONTENT, heading="Цитата о подходе",
              blocks=[Block(kind="quote",
                            quote_text="Шаблон — источник правил, модель — источник смысла.",
                            quote_author="Команда проекта")]),
        Slide(slide_type=SlideType.FINAL, heading="Спасибо за внимание"),
    ])


def test_theme_pair_applied(profile_of, template_workspace):
    """На тёмном шаблоне текст использует foreground (белый), не акцент."""
    profile = profile_of(template_workspace)
    theme = _theme(profile)
    assert theme["background"].upper() == "#000000"
    assert theme["foreground"].upper() == "#FFFFFF"
    assert theme["accent"].upper() != theme["foreground"].upper()

    dc = DesignContext.from_profile(profile)
    deck = _bullet_deck()
    for variant in VARIANTS:
        renderer = Renderer(profile, variant=variant, template_bytes=template_workspace)
        pptx = renderer.render_deck(deck, dc)
        prs = Presentation(io.BytesIO(pptx))
        auditor = Audit(profile)
        auditor.W, auditor.H = prs.slide_width, prs.slide_height
        checked = 0
        for item in _scan(prs, auditor, deck):
            bg = item["bg"]
            # фон под текстом тёмный → run обязан быть светлым foreground
            if contrast_ratio(bg, "#FFFFFF") >= 4.5:
                assert item["color"] == "#FFFFFF", (
                    f"{variant}: «{item['text'][:30]}» {item['color']} на {bg}")
            assert item["color"] != theme["accent"].upper() or \
                contrast_ratio(item["color"], bg) >= 4.5
            checked += 1
        assert checked >= 6


def test_accent_never_on_low_contrast_text(profile_of, template_workspace):
    """Если акцент к фону < порога — цвет run не акцентный."""
    profile = profile_of(template_workspace)
    dc = DesignContext.from_profile(profile)
    deck = _combo_deck()
    accent_runs = 0
    for variant in VARIANTS:
        renderer = Renderer(profile, variant=variant, template_bytes=template_workspace)
        pptx = renderer.render_deck(deck, dc)
        prs = Presentation(io.BytesIO(pptx))
        auditor = Audit(profile)
        auditor.W, auditor.H = prs.slide_width, prs.slide_height
        for item in _scan(prs, auditor, deck):
            accent = (item["theme"] or {}).get("accent", "").upper()
            if not accent or item["color"] != accent:
                continue
            accent_runs += 1
            threshold = 3.0 if item["size"] >= 24 else 4.5
            assert contrast_ratio(item["color"], item["bg"]) >= threshold, (
                f"{variant}: акцентный текст «{item['text'][:30]}» "
                f"{item['color']} на {item['bg']} при кегле {item['size']}")
    assert accent_runs >= 1, "KPI-числа должны использовать акцент (крупный кегль)"


def test_kpi_card_stays_together(profile_of, template_workspace):
    """Число и подпись KPI лежат в одной ячейке и не разъезжаются."""
    profile = profile_of(template_workspace)
    dc = DesignContext.from_profile(profile)
    deck = _combo_deck()
    kpi_slide = deck.slides[2]
    assert slide_composition(kpi_slide) == "kpi"
    for variant in VARIANTS:
        renderer = Renderer(profile, variant=variant, template_bytes=template_workspace)
        pptx = renderer.render_deck(deck, dc)
        prs = Presentation(io.BytesIO(pptx))
        slide = list(prs.slides)[2]
        value_boxes: dict[str, list] = {}
        label_boxes: dict[str, list] = {}
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            text = shape.text_frame.text.strip()
            for fact in kpi_slide.blocks[0].factoids:
                if text == fact["value"]:
                    value_boxes.setdefault(text, []).append(shape)
                elif text == fact["label"]:
                    label_boxes.setdefault(text, []).append(shape)
        assert len(value_boxes) == 4, f"{variant}: числа не найдены: {value_boxes}"
        assert len(label_boxes) == 4, f"{variant}: подписи потеряны: {label_boxes}"
        for value, shapes in value_boxes.items():
            label = next(fact["label"] for fact in kpi_slide.blocks[0].factoids
                         if fact["value"] == value)
            value_shape = shapes[0]
            label_shape = label_boxes[label][0]
            same_column = abs(value_shape.left - label_shape.left) <= 0.05 * 914400
            below = label_shape.top >= value_shape.top
            close = (label_shape.top - (value_shape.top + value_shape.height)) \
                <= 0.35 * 914400
            assert same_column and below and close, (
                f"{variant}: «{value}» и «{label}» разъехались: "
                f"x {value_shape.left} vs {label_shape.left}, "
                f"y {value_shape.top}+{value_shape.height} vs {label_shape.top}")


def _template_set(profile_of, synthetic_template, synthetic_template_4x3,
                  unfamiliar_template, template_workspace, external_templates):
    items = [("synthetic_16x9", synthetic_template),
             ("synthetic_4x3", synthetic_template_4x3),
             ("unfamiliar", unfamiliar_template),
             ("VK_WorkSpace", template_workspace)]
    items += external_templates
    seen: set[str] = set()
    unique: list[tuple[str, bytes]] = []
    for name, data in items:
        if name in seen:
            continue
        seen.add(name)
        unique.append((name, data))
    return unique[:9]


def test_visibility_across_templates(profile_of, synthetic_template,
                                     synthetic_template_4x3, unfamiliar_template,
                                     template_workspace, external_templates):
    """9 колод на 6+ шаблонах: ни одного run с контрастом < 3:1."""
    templates = _template_set(profile_of, synthetic_template, synthetic_template_4x3,
                              unfamiliar_template, template_workspace,
                              external_templates)
    assert len(templates) >= 6, f"нужно минимум 6 шаблонов, найдено {len(templates)}"
    deck = _combo_deck()
    checked = 0
    for name, template in templates:
        profile = profile_of(template)
        dc = DesignContext.from_profile(profile)
        for variant in VARIANTS:
            renderer = Renderer(profile, variant=variant, template_bytes=template)
            pptx = renderer.render_deck(deck, dc)
            prs = Presentation(io.BytesIO(pptx))
            auditor = Audit(profile)
            auditor.W, auditor.H = prs.slide_width, prs.slide_height
            for item in _scan(prs, auditor, deck):
                ratio = _contrast(*hex_to_rgb(item["color"]), *_hex(item["bg"]))
                assert ratio >= 3.0, (
                    f"{name}/{variant}: «{item['text'][:40]}» {item['color']} "
                    f"на {item['bg']} — контраст {ratio:.2f}")
                checked += 1
    assert checked >= 200, f"проверено слишком мало runs: {checked}"
