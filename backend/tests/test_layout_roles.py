"""Тесты структурной классификации макетов: роли, типы и объяснимость.

Главный риск защиты — незнакомый шаблон с именами макетов вида «Slide 1».
Здесь проверяется, что роли выводятся из структуры и что решение объяснимо
(поле `role_reason` заполнено у каждого макета).
"""
from __future__ import annotations

from collections import Counter

import pytest
from pptx.enum.shapes import MSO_SHAPE_TYPE

from app.models.deck import Block, Chart, ChartType, Slide, SlideType, Table
from app.render.pptx_renderer import Renderer
from app.template.parser import TemplateParser
from app.template.profile import PlaceholderInfo

EMU = 914400

ROLES = {"title", "section", "agenda", "content", "final"}
KINDS = {"bullets", "multi_column", "image", "image_text", "table", "chart", "blank"}


def _check_profile(profile: dict, label: str) -> None:
    layouts = profile["layouts"]
    assert layouts, f"{label}: макетов нет"
    for layout in layouts:
        assert layout["role"] in ROLES, f"{label}: {layout['name']} → {layout['role']}"
        assert layout["kind"] in KINDS, f"{label}: {layout['name']} → {layout['kind']}"
        assert layout["role_reason"], f"{label}: у макета {layout['name']} нет обоснования"
        assert 0.0 <= layout["score"] <= 1.0
    groups = profile["layout_groups"]
    assert groups, f"{label}: нет групп ролей"
    for role, ids in groups.items():
        assert role in ROLES
        assert ids, f"{label}: пустая группа роли {role}"
    covered = sum(len(ids) for ids in groups.values())
    assert covered == len(layouts), f"{label}: не все макеты попали в группы"


def test_roles_on_synthetic_template(profile_of, synthetic_template):
    profile = profile_of(synthetic_template)
    _check_profile(profile, "synthetic")
    assert "content" in profile["layout_groups"]


def test_roles_on_unfamiliar_template(profile_of, unfamiliar_template):
    """Имена макетов заменены на «Slide N» — роли обязаны выводиться из структуры."""
    profile = profile_of(unfamiliar_template)
    _check_profile(profile, "unfamiliar")
    assert "content" in profile["layout_groups"], profile["layout_groups"]
    for layout in profile["layouts"]:
        assert not layout["name"].startswith(("1_", "2_"))
        assert "Slide" in layout["name"]


def test_roles_on_all_available_templates(all_templates, profile_of):
    for name, template_bytes in all_templates:
        profile = profile_of(template_bytes)
        _check_profile(profile, name)
        assert "content" in profile["layout_groups"], \
            f"{name}: нет ни одного контентного макета: {profile['layout_groups']}"


def _ph(type_: str, w: float, h: float, x: float = 0.0, y: float = 0.0,
        is_title: bool = False) -> PlaceholderInfo:
    return PlaceholderInfo(idx=0, type=type_, name="ph", x=x, y=y, w=w, h=h,
                           is_title=is_title)


def _classifier() -> TemplateParser:
    """Классификатор как чистая функция: PPTX не нужен, `_classify_layout` — метод."""
    return TemplateParser(b"template.pptx")


def test_cover_subtitle_stays_subtitle():
    """Стандартная обложка Office: подзаголовок крупный, но остаётся обложкой.

    Подзаголовок сопоставим с заголовком (2.6″ против 1.98″) — это конвенция
    обложки, а не тело слайда.
    """
    phs = [_ph("center_title", 11.46, 1.98, is_title=True), _ph("subtitle", 11.46, 2.6)]
    role, kind, _ = _classifier()._classify_layout(
        "Title Slide", "Office", phs, {}, 13.333, 7.5)
    assert (role, kind) == ("title", "blank")


def test_slidesgo_subtitle_is_body():
    """Slidesgo объявляет тело подзаголовком: тело в разы больше заголовка."""
    phs = [_ph("title", 8.44, 0.59, is_title=True), _ph("subtitle", 8.44, 3.97)]
    role, kind, reason = _classifier()._classify_layout(
        "TITLE_AND_BODY", "Slidesgo", phs, {}, 10.0, 5.62)
    assert (role, kind) == ("content", "bullets")
    assert "тело-подзаголовков 1" in reason


def test_several_subtitles_are_columns():
    phs = [_ph("title", 8.44, 0.59, is_title=True)]
    phs += [_ph("subtitle", 2.98, 0.59) for _ in range(4)]
    role, kind, _ = _classifier()._classify_layout(
        "TITLE_AND_TWO_COLUMNS", "Slidesgo", phs, {}, 10.0, 5.62)
    assert (role, kind) == ("content", "multi_column")


def test_full_bleed_picture_is_background():
    """Картинка во весь слайд — фон, а не контентная картинка макета."""
    class _Pic:
        shape_type = MSO_SHAPE_TYPE.PICTURE
        is_placeholder = False
        has_table = has_chart = has_text_frame = False
        left, top = 0, 0
        width, height = int(10.0 * EMU), int(5.62 * EMU)

    class _Layout:
        shapes = [_Pic()]

    kinds = _classifier()._layout_shape_kinds(_Layout(), 10.0, 5.62)
    assert kinds["pictures"] == 0
    phs = [_ph("center_title", 5.82, 2.73, is_title=True)]
    role, kind, _ = _classifier()._classify_layout(
        "MAIN_POINT", "Slidesgo", phs, kinds, 10.0, 5.62)
    assert (role, kind) == ("title", "blank")


def test_roles_stable_when_layouts_renamed(profile_of, synthetic_template):
    """Переименование макетов в «Layout N» не меняет структурные решения.

    Единственное допустимое расхождение — разделитель: у него «заголовок +
    тело», и роль section выводится только из имени (структура неотличима от
    контентного макета). Поэтому сравниваем композиционные типы полностью,
    а роли — всюду, кроме пары section↔content.
    """
    from tools.make_fixtures import mutate_template

    original = profile_of(synthetic_template)
    renamed = profile_of(mutate_template(synthetic_template, prefix="Layout"))
    assert [l["name"] for l in renamed["layouts"]] != [l["name"] for l in original["layouts"]]
    assert (sorted(l["kind"] for l in original["layouts"])
            == sorted(l["kind"] for l in renamed["layouts"]))
    before = Counter(l["role"] for l in original["layouts"])
    after = Counter(l["role"] for l in renamed["layouts"])
    for role in set(before) | set(after):
        if role in ("section", "content"):
            continue
        assert before[role] == after[role], f"{role}: {before[role]} → {after[role]}"
    assert after["content"] >= before["content"]
    assert after["title"] >= 1 and after["content"] >= 1


def test_layout_pick_uses_kind_for_data_slides(profile_of, unfamiliar_template):
    """Слайд с таблицей предпочитает макет типа table, если такой есть."""
    profile = profile_of(unfamiliar_template)
    has_table_layout = any(l["kind"] == "table" for l in profile["layouts"])
    renderer = Renderer(profile, template_bytes=unfamiliar_template)
    slide = Slide(slide_type=SlideType.CONTENT, heading="Итоги пилота",
                  blocks=[Block(kind="table", table=Table(
                      header=["Метрика", "Значение"], rows=[["Ошибки", "4%"]])),
                      Block(kind="bullets", items=["Вывод первый", "Вывод второй"])])
    picked = renderer._pick_layout(slide.slide_type, slide)
    assert picked["id"] in {l["id"] for l in profile["layouts"]}
    if has_table_layout:
        assert picked["kind"] == "table"


def test_layout_pick_falls_back_without_role(profile_of, synthetic_template):
    """Если макетов нужной роли нет, выбор не падает и остаётся внутри профиля."""
    profile = profile_of(synthetic_template)
    stripped = dict(profile)
    stripped["layouts"] = [l for l in profile["layouts"] if l["role"] == "content"]
    stripped["layout_groups"] = {"content": [l["id"] for l in stripped["layouts"]]}
    renderer = Renderer(stripped, template_bytes=synthetic_template)
    for slide_type in (SlideType.TITLE, SlideType.SECTION, SlideType.AGENDA,
                       SlideType.FINAL, SlideType.CONTENT):
        picked = renderer._pick_layout(slide_type)
        assert picked["id"] in {l["id"] for l in stripped["layouts"]}


def test_slide_kind_inference():
    """Композиционный тип слайда выводится из его блоков (для выбора макета)."""
    def kind_of(blocks: list[Block]) -> str | None:
        slide = Slide(slide_type=SlideType.CONTENT, heading="Слайд", blocks=blocks)
        return Renderer._slide_kind(slide)

    assert kind_of([Block(kind="table", table=Table(header=["A"], rows=[["1"]]))]) == "table"
    assert kind_of([Block(kind="chart", chart=Chart(
        type=ChartType.COLUMN, categories=["Q1"], series=[{"name": "s", "values": [1.0]}]))]) == "chart"
    assert kind_of([Block(kind="bullets", items=["один"])]) == "bullets"
    assert kind_of([Block(kind="image", image_ref="k"),
                    Block(kind="bullets", items=["один"])]) == "image_text"
    assert kind_of([Block(kind="bullets", items=["один"]), Block(kind="text", text="два"),
                    Block(kind="steps", items=["три"])]) == "multi_column"
    assert Renderer._slide_kind(Slide(slide_type=SlideType.TITLE, heading="Титул")) is None
