"""Тесты рендера: нативность объектов, отказ на мусоре, встраивание изображений."""
from __future__ import annotations

import io

import pytest
from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from app.models.deck import Block, Deck, Slide, SlideType
from app.planner.fallback import FallbackPlanner
from app.render.pptx_renderer import IMAGE_SLOT_NAME, RenderError, Renderer

BRIEF = ("Компания запускает мобильное приложение доставки еды. Охват — 3 города, "
         "150 000 пользователей, средний чек 870 руб., доставка быстрее на 18%. "
         "Цель — 10 городов к концу года и рост LTV на 25%.")


def _deck_with_image(image_ref: str | None) -> Deck:
    deck = FallbackPlanner().plan(BRIEF, "", "project")
    deck.slides.append(Slide(
        slide_type=SlideType.CONTENT,
        heading="Как выглядит интерфейс доставки",
        blocks=[Block(kind="bullets", items=["Заказ в три касания", "Трекинг курьера"]),
                Block(kind="image", image_ref=image_ref, image_caption="Экран заказа")],
    ))
    return deck


def test_render_roundtrip(profile_of, render_variants, synthetic_template):
    deck = FallbackPlanner().plan(BRIEF, "", "project")
    profile = profile_of(synthetic_template)
    variants = render_variants(profile, deck, synthetic_template)
    assert set(variants) == {"compact", "cards", "split"}
    for variant, pptx in variants.items():
        assert pptx.startswith(b"PK")
        prs = Presentation(io.BytesIO(pptx))
        assert len(list(prs.slides)) == len(deck.slides)
        for slide in prs.slides:
            texts = [sh for sh in slide.shapes if getattr(sh, "has_text_frame", False)]
            assert texts, f"пустой слайд в варианте {variant}"
        # ни одного растрового слайда целиком
        for slide in prs.slides:
            for sh in slide.shapes:
                if sh.shape_type == MSO_SHAPE_TYPE.PICTURE:
                    assert not (sh.width >= prs.slide_width * 0.92
                                and sh.height >= prs.slide_height * 0.92)


def test_render_uses_template_layouts(profile_of, render_variants, synthetic_template):
    """Слайды обязаны быть собраны на макетах шаблона, а не на пустых по умолчанию."""
    deck = FallbackPlanner().plan(BRIEF, "", "project")
    profile = profile_of(synthetic_template)
    template_names = {layout["name"] for layout in profile["layouts"]}
    pptx = render_variants(profile, deck, synthetic_template)["compact"]
    prs = Presentation(io.BytesIO(pptx))
    for slide in prs.slides:
        assert slide.slide_layout.name in template_names


@pytest.mark.parametrize("payload", [
    b"",
    b"not a pptx at all",
    b"PK\x03\x04\x00\x00\x00\x00",  # обрезанный zip
])
def test_render_rejects_garbage(payload, profile_of, synthetic_template):
    """Мусорный шаблон должен приводить к явному отказу, а не к пустой колоде."""
    from app.pipeline import build_profile

    with pytest.raises(Exception):
        build_profile(payload, "garbage.pptx")

    profile = profile_of(synthetic_template)
    deck = FallbackPlanner().plan(BRIEF, "", "project")
    renderer = Renderer(profile, template_bytes=payload)
    with pytest.raises(RenderError):
        renderer.render(deck, {})


def test_image_widget_embeds_native_picture(profile_of, render_variants,
                                            synthetic_template, tiny_png):
    deck = _deck_with_image("screen_order")
    profile = profile_of(synthetic_template)
    pptx = render_variants(profile, deck, synthetic_template,
                           images={"screen_order": tiny_png})["compact"]
    prs = Presentation(io.BytesIO(pptx))
    pictures = [sh for sh in prs.slides[-1].shapes
                if sh.shape_type == MSO_SHAPE_TYPE.PICTURE]
    assert pictures, "картинка должна быть вставлена нативным объектом"

    pic = pictures[0]
    with Image.open(io.BytesIO(tiny_png)) as img:
        source_ratio = img.width / img.height
    rendered_ratio = pic.width / pic.height
    assert abs(rendered_ratio - source_ratio) < 0.02, "пропорции картинки нарушены"
    assert pic.left >= 0 and pic.top >= 0
    assert pic.left + pic.width <= prs.slide_width + 1000
    assert pic.top + pic.height <= prs.slide_height + 1000


def test_image_widget_falls_back_to_slot(profile_of, render_variants,
                                         synthetic_template):
    """Если картинки нет — нативный слот с подписью и предупреждение в аудите."""
    from app.audit.checks import Audit

    deck = _deck_with_image("missing_key")
    profile = profile_of(synthetic_template)
    pptx = render_variants(profile, deck, synthetic_template)["compact"]
    prs = Presentation(io.BytesIO(pptx))
    slots = [sh for sh in prs.slides[-1].shapes if sh.name == IMAGE_SLOT_NAME]
    assert slots, "вместо картинки должен остаться слот"
    assert not [sh for sh in prs.slides[-1].shapes
                if sh.shape_type == MSO_SHAPE_TYPE.PICTURE]

    result = Audit(profile).audit(deck, pptx)
    assert "image_missing" in {i["code"] for i in result["issues"]}
