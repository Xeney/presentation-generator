"""Тесты структурной классификации макетов: роли, типы и объяснимость.

Главный риск защиты — незнакомый шаблон с именами макетов вида «Slide 1».
Здесь проверяется, что роли выводятся из структуры и что решение объяснимо
(поле `role_reason` заполнено у каждого макета).
"""
from __future__ import annotations

import pytest

from app.models.deck import Block, Chart, ChartType, Slide, SlideType, Table
from app.render.pptx_renderer import Renderer

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
