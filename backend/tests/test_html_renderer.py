"""HTML+CSS-рендерер: второй путь сборки, тот же widget-план (ADR-035)."""
from __future__ import annotations

import re

from lxml import html as lxml_html

from app.models.deck import Block, Deck, Slide, SlideType
from app.planner.fallback import FallbackPlanner
from app.render.html_renderer import render_html

BRIEF = ("Платформа аналитики сократила время подготовки отчётов на 40%, "
         "автоматизировала 12 задач, охватила 5 подразделений.")


def _deck() -> Deck:
    return FallbackPlanner().plan(BRIEF, "", "project")


def _image_deck() -> Deck:
    return Deck(title="Колода с картинкой", slides=[
        Slide(slide_type=SlideType.TITLE, heading="Титул презентации"),
        Slide(slide_type=SlideType.CONTENT, heading="Иллюстрация проекта",
              blocks=[Block(kind="bullets", items=["Пояснение к картинке."]),
                      Block(kind="image", image_ref="pic.png",
                            image_caption="Схема процесса")]),
        Slide(slide_type=SlideType.FINAL, heading="Финал презентации"),
    ])


def test_html_render_produces_valid_html(profile_of, synthetic_template):
    profile = profile_of(synthetic_template)
    deck = _deck()
    html = render_html(deck, profile, variant="compact",
                       template_bytes=synthetic_template)
    assert html.startswith("<!DOCTYPE html>")
    document = lxml_html.fromstring(html)
    sections = document.xpath("//section[contains(@class, 'slide')]")
    assert len(sections) == len(deck.slides)
    for index, section in enumerate(sections):
        assert section.get("data-slide") == str(index + 1)
        assert section.get("data-slide-type")
        assert section.get("data-layout")
    assert document.xpath("//main[@data-variant='compact']")
    assert ":root" in html and "--color-bg" in html


def test_html_uses_template_tokens(profile_of, template_workspace):
    profile = profile_of(template_workspace)
    deck = _deck()
    html = render_html(deck, profile, variant="compact",
                       template_bytes=template_workspace)
    root = re.search(r":root \{(.*?)\}", html, re.S)
    assert root, "нет :root с токенами"
    tokens = {
        key.strip(): value.strip()
        for key, value in (piece.split(":", 1) for piece in root.group(1).split(";") if ":" in piece)
    }
    content = next(l for l in profile["layouts"] if l["role"] == "content")
    theme = content.get("theme") or {}
    assert tokens["--color-bg"].upper() == theme["background"].upper()
    assert tokens["--color-text"].upper() == theme["foreground"].upper()
    assert tokens["--color-accent"].upper() == theme["accent"].upper()
    assert profile["headline_font"] in tokens["--font-heading"]
    assert profile["body_font"] in tokens["--font-body"]
    for name in ("--size-h1", "--size-h2", "--size-body", "--grid-margin"):
        assert tokens.get(name), f"нет токена {name}"
    # никаких хардкодных цветов помимо токенов: все блоки ссылаются на переменные
    # или на цвета, посчитанные рендерером (проверяем, что CSS не пустой)
    assert "var(--color-bg)" in html


def test_html_no_full_slide_image(profile_of, synthetic_template, tiny_png):
    profile = profile_of(synthetic_template)
    deck = _image_deck()
    html = render_html(deck, profile, variant="compact",
                       template_bytes=synthetic_template,
                       images={"pic.png": tiny_png})
    document = lxml_html.fromstring(html)
    images = document.xpath("//img")
    assert images, "картинка из контент-пакета должна попасть в HTML"
    for image in images:
        style = image.get("style") or ""
        width = re.search(r"width:([0-9.]+)%", style)
        height = re.search(r"height:([0-9.]+)%", style)
        if width:
            assert float(width.group(1)) <= 60, "картинка не должна занимать весь слайд"
        if height:
            assert float(height.group(1)) <= 70
        assert "image" in (image.get("class") or "")
    # картинка не подменяет слайд целиком: на слайде есть текст
    assert "Пояснение к картинке" in html


def test_html_three_variants_differ(profile_of, synthetic_template):
    profile = profile_of(synthetic_template)
    deck = _deck()
    rendered = {
        variant: render_html(deck, profile, variant=variant,
                             template_bytes=synthetic_template)
        for variant in ("compact", "cards", "split")
    }
    assert len(set(rendered.values())) == 3, "варианты обязаны отличаться"
    cards = lxml_html.fromstring(rendered["cards"])
    compact = lxml_html.fromstring(rendered["compact"])
    assert len(cards.xpath("//*[contains(@class, 'card')]")) > 0
    assert len(compact.xpath("//*[contains(concat(' ', normalize-space(@class), ' '), ' el card ')]")) == 0
    # сплит разводит данные и текст по колонкам: левые координаты отличаются
    split = rendered["split"]
    assert "data-variant=\"split\"" in split
    lefts_compact = re.findall(r"left:([0-9.]+)%", rendered["compact"])
    lefts_split = re.findall(r"left:([0-9.]+)%", split)
    assert lefts_compact != lefts_split, "геометрия вариантов не должна совпадать"
