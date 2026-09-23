"""Тесты детерминированных авто-фиксов: что реально исправляется, а что нет."""
from __future__ import annotations

from app.audit.checks import Audit
from app.audit.fixes import FixEngine, chart_to_table, split_sentence, table_to_chart
from app.models.deck import Block, Chart, ChartType, Deck, Slide, SlideType, Table

BRIEF = ("Платформа аналитики сократила время подготовки отчётов на 40%, "
         "автоматизировала 12 задач, охватила 5 подразделений.")


def _deck(slides: list[Slide], *, filler: bool = True) -> Deck:
    """Колода с титулом и финалом; при необходимости — с запасным контентным слайдом."""
    body = list(slides)
    if filler and not any(s.slide_type == SlideType.CONTENT for s in body):
        body.append(Slide(slide_type=SlideType.CONTENT, heading="Запасной слайд",
                          blocks=[Block(kind="bullets", items=["Тезис для объёма"])]))
    return Deck(title="Проба фиксов", slides=[
        Slide(slide_type=SlideType.TITLE, heading="Титул презентации"),
        *body,
        Slide(slide_type=SlideType.FINAL, heading="Финал презентации")])


def _apply(deck: Deck, profile: dict, code: str, slide: int = 1,
           issue_id: str = "issue-1") -> tuple[Deck, list[dict]]:
    engine = FixEngine(profile)
    return engine.apply(deck, [{"id": issue_id, "code": code, "slide": slide,
                                "severity": "warning", "message": ""}])


# ------------------------------------------------------------------ helpers
def test_split_sentence_keeps_meaning():
    text = ("Платформа сократила время подготовки отчётов на 40 процентов; "
            "автоматизировала двенадцать рутинных задач и охватила пять подразделений")
    parts = split_sentence(text)
    assert len(parts) == 2
    assert all(part.strip() for part in parts)
    assert " ".join(parts) != text or len(parts[0].split()) <= 15


def test_table_to_chart_only_for_numeric():
    numeric = Table(header=["Метрика", "До", "После"],
                    rows=[["Время", "8", "3"], ["Охват", "2", "5"]])
    chart = table_to_chart(numeric)
    assert chart is not None
    assert chart.categories == ["Время", "Охват"]
    assert [series.name for series in chart.series] == ["До", "После"]
    assert chart.series[0].values == [8.0, 2.0]

    textual = Table(header=["Направление", "Комментарий"],
                    rows=[["Продукт", "нужен рефакторинг"]])
    assert table_to_chart(textual) is None


def test_chart_to_table_roundtrip():
    chart = Chart(type=ChartType.COLUMN, categories=["Q1", "Q2"], unit="%",
                  series=[{"name": "Отчёты", "values": [40.0, 55.0]},
                          {"name": "Задачи", "values": [12.0, 18.0]}])
    table = chart_to_table(chart)
    assert table is not None
    assert table.header == ["Категория", "Отчёты", "Задачи"]
    assert table.rows[0] == ["Q1", "40", "12"]


# -------------------------------------------------------------------- фиксы
def test_shrink_font_fixes_scale_violation(profile_of, render_variants,
                                           synthetic_template):
    """Сдвиг по шкале действительно убирает font_size_not_in_scale."""
    deck = _deck([Slide(slide_type=SlideType.CONTENT, heading="Слайд со шкалой",
                        blocks=[Block(kind="bullets", items=["Тезис один", "Тезис два"])])])
    profile = profile_of(synthetic_template)
    # административно портим шкалу: уменьшаем шкалу профиля до одного размера
    broken = dict(profile)
    broken["type_scale"] = {**profile["type_scale"], "body": [9.0]}

    pptx = render_variants(profile, deck, synthetic_template)["compact"]
    issues = Audit(broken).audit(deck, pptx)["issues"]
    assert any(i["code"] == "font_size_not_in_scale" for i in issues), \
        "предусловие: нарушение шкалы должно ловиться"

    fixed, outcomes = FixEngine(profile).apply(deck, issues, None)
    assert fixed.slides[1].type_scale_step == -1
    assert any(o["action"] == "shrink_font" for o in outcomes)


def test_split_slide_moves_overflow_bullets(profile_of, synthetic_template):
    """Семь буллетов недопустимы — часть уезжает на новый слайд."""
    deck = _deck([Slide(slide_type=SlideType.CONTENT, heading="Много тезисов",
                        blocks=[Block(kind="bullets", items=[f"Тезис номер {i}" for i in range(1, 8)])])])
    profile = profile_of(synthetic_template)
    fixed, outcomes = _apply(deck, profile, "too_many_bullets")
    assert len(fixed.slides) == 4
    assert fixed.slides[2].heading.endswith("(продолжение)")
    assert sum(len(b.items) for b in fixed.slides[1].blocks) <= 6
    assert outcomes[0]["status"] == "applied"


def test_drop_placeholder_block(profile_of, synthetic_template):
    deck = _deck([Slide(slide_type=SlideType.CONTENT, heading="Слайд с заглушкой",
                        blocks=[Block(kind="text", text="TODO: вставьте текст"),
                                Block(kind="bullets", items=["Нормальный тезис"])])])
    profile = profile_of(synthetic_template)
    fixed, outcomes = _apply(deck, profile, "placeholder_text")
    assert len(fixed.slides[1].blocks) == 1
    assert fixed.slides[1].blocks[0].kind == "bullets"
    assert outcomes[0]["action"] == "drop_block"


def test_drop_duplicate_slide(profile_of, synthetic_template):
    same = [Block(kind="bullets", items=["Один и тот же тезис", "И второй тоже"])]
    deck = _deck([
        Slide(slide_type=SlideType.CONTENT, heading="Повтор", blocks=list(same)),
        Slide(slide_type=SlideType.CONTENT, heading="Повтор", blocks=list(same)),
        Slide(slide_type=SlideType.CONTENT, heading="Уникальный слайд",
              blocks=[Block(kind="bullets", items=["Другой тезис"])]),
    ])
    profile = profile_of(synthetic_template)
    fixed, outcomes = _apply(deck, profile, "duplicate_slide", slide=-1)
    assert len(fixed.slides) == 4
    assert outcomes[0]["action"] == "drop_duplicate"


def test_switch_layout_sets_hint(profile_of, synthetic_template):
    deck = _deck([Slide(slide_type=SlideType.CONTENT, heading="Слайд с полями",
                        blocks=[Block(kind="bullets", items=["Тезис"])])])
    profile = profile_of(synthetic_template)
    fixed, outcomes = _apply(deck, profile, "content_in_margins")
    hint = fixed.slides[1].layout_hint
    assert hint and hint.startswith("L")
    assert any(o["action"] == "switch_layout" for o in outcomes)
    # подсказка действительно влияет на выбор макета
    from app.render.pptx_renderer import Renderer

    picked = Renderer(profile, template_bytes=synthetic_template)._pick_layout(
        fixed.slides[1].slide_type, fixed.slides[1])
    assert picked["id"] == hint


def test_merge_sparse_slide(profile_of, synthetic_template):
    deck = _deck([
        Slide(slide_type=SlideType.CONTENT, heading="Почти пустой",
              blocks=[Block(kind="bullets", items=["Один тезис"])]),
        Slide(slide_type=SlideType.CONTENT, heading="Продолжение мысли",
              blocks=[Block(kind="bullets", items=["Второй тезис"])]),
        Slide(slide_type=SlideType.CONTENT, heading="Запасной слайд",
              blocks=[Block(kind="bullets", items=["Тезис для объёма"])]),
    ])
    profile = profile_of(synthetic_template)
    fixed, outcomes = _apply(deck, profile, "slide_too_sparse")
    assert len(fixed.slides) == 4
    assert sum(len(b.items) for b in fixed.slides[1].blocks) == 2
    assert outcomes[0]["action"] == "merge_slide"


def test_unsupported_issue_is_skipped_with_reason(profile_of, synthetic_template):
    deck = _fallback_deck()
    profile = profile_of(synthetic_template)
    _, outcomes = _apply(deck, profile, "raster_slide")
    assert outcomes[0]["status"] == "skipped"
    assert "вёрстки или шаблона" in outcomes[0]["detail"]


def test_limits_are_respected_after_fixes(profile_of, synthetic_template):
    """Фиксы не должны ломать схему: слайдов 3..15, блоков ≤ 6, буллетов ≤ 6."""
    deck = _fallback_deck()
    profile = profile_of(synthetic_template)
    engine = FixEngine(profile)
    issues = [{"id": f"i{n}", "code": code, "slide": 1, "severity": "warning", "message": ""}
              for n, code in enumerate(("too_many_bullets", "slide_too_dense",
                                        "duplicate_slide", "duplicate_heading"))]
    fixed, _ = engine.apply(deck, issues, None)
    assert 3 <= len(fixed.slides) <= 15
    for slide in fixed.slides:
        assert len(slide.blocks) <= 6
        for block in slide.blocks:
            assert len(block.items) <= 8


def _fallback_deck() -> Deck:
    from app.planner.fallback import FallbackPlanner

    return FallbackPlanner().plan(BRIEF, "", "project")
