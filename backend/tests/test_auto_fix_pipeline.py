"""Автоматический аудит и авто-фиксы без подтверждения (ADR-032).

После генерации детерминированные проблемы исправляются сразу, а пользователь
видит уже исправленный результат; в отчёте — что именно сделано.
"""
from __future__ import annotations

from app.audit.checks import Audit
from app.audit.fixes import FixEngine
from app.layout.engine import DesignContext
from app.models.deck import Block, Deck, Slide, SlideType
from app.planner.fallback import FallbackPlanner
from app.pipeline import auto_fix_variants, full_generate
from app.render.pptx_renderer import Renderer
from app.template.parser import TemplateParser

BRIEF = ("Платформа аналитики сократила время подготовки отчётов на 40%, "
         "автоматизировала 12 задач, охватила 5 подразделений.")


def _overflow_deck() -> Deck:
    return Deck(title="Проба авто-фиксов", slides=[
        Slide(slide_type=SlideType.TITLE, heading="Титул презентации"),
        Slide(slide_type=SlideType.CONTENT, heading="Много тезисов сразу",
              blocks=[Block(kind="bullets",
                            items=[f"Тезис номер {i} с пояснением" for i in range(1, 8)])]),
        Slide(slide_type=SlideType.CONTENT, heading="Запасной слайд",
              blocks=[Block(kind="bullets", items=["Спокойный тезис"])]),
        Slide(slide_type=SlideType.FINAL, heading="Финал презентации"),
    ])


def test_auto_fix_variants_splits_dense_slide(profile_of, synthetic_template, render_variants):
    from app.pipeline import VariantArtifact

    profile = profile_of(synthetic_template)
    dc = DesignContext.from_profile(profile)
    deck = _overflow_deck()
    renderer = Renderer(profile, variant="compact", template_bytes=synthetic_template)
    pptx = renderer.render_deck(deck, dc)
    audit = Audit(profile).audit(deck, pptx)
    assert any(issue["code"] == "too_many_bullets" for issue in audit["issues"]), \
        "предусловие: перегруженный слайд должен ловиться"

    rendered = render_variants(profile, deck, synthetic_template)
    artifacts = [VariantArtifact(variant=name, pptx=data) for name, data in rendered.items()]
    for artifact in artifacts:
        artifact.audit = Audit(profile).audit(deck, artifact.pptx)
    fixed, artifacts, applied, _skipped = auto_fix_variants(
        deck, profile, synthetic_template, artifacts)
    assert applied and any(item["action"] == "split_slide" for item in applied)
    assert len(fixed.slides) == 5, "часть пунктов уехала на слайд-продолжение"
    for artifact in artifacts:
        codes = {issue["code"] for issue in artifact.audit["issues"]}
        assert "too_many_bullets" not in codes, codes
        assert artifact.audit["errors"] == 0, artifact.audit["issues"][:3]


def test_text_overflow_at_scale_floor_splits_slide(profile_of, synthetic_template):
    """Когда кегль уже на нижней ступени, переполнение лечится переносом."""
    profile = profile_of(synthetic_template)
    deck = _overflow_deck()
    deck.slides[1].type_scale_step = -3
    engine = FixEngine(profile)
    fixed, outcomes = engine.apply(deck, [
        {"id": "i1", "code": "text_overflow", "slide": 1,
         "severity": "warning", "message": ""}])
    assert outcomes[0]["status"] == "applied"
    assert outcomes[0]["action"] == "split_slide"
    assert len(fixed.slides) == 5


def test_full_generate_records_auto_fixes(synthetic_template):
    """Полный прогон возвращает отчёт об авто-фиксах и чистые варианты."""
    result = full_generate(BRIEF, "", "project", synthetic_template, "tpl.pptx",
                           planner=_offline(), vlm=False, auto_fix=True)
    assert "auto_fixes" in result
    assert set(result["auto_fixes"]) == {"applied", "skipped"}
    for variant in result["variants"]:
        assert variant["audit"]["errors"] == 0, variant["audit"]["issues"][:3]


def _offline():
    """Офлайн-планировщик как объект Planner-совместимого вида."""

    class _Planner:
        def plan(self, brief, source, purpose, profile, corpus=None,
                 max_slides=None, language="ru"):
            from app.planner.planner import PlanningResult

            return PlanningResult(FallbackPlanner().plan(brief, source, purpose,
                                                         corpus=corpus),
                                  used_llm=False, attempts=0)

    return _Planner()


def test_prompt_parameters_are_honoured(profile_of, synthetic_template):
    """Число слайдов и язык из интерфейса доходят до промпта планировщика."""
    from app.planner.planner import Planner

    profile = profile_of(synthetic_template)
    planner = Planner()
    prompt_ru = planner._render_user("Бриф презентации", "", "project", profile,
                                     deck_size=6, language="ru")
    assert "6" in prompt_ru and "английский" not in prompt_ru

    prompt_en = planner._render_user("Бриф презентации", "", "project", profile,
                                     deck_size=12, language="en")
    assert "12" in prompt_en
    assert "английский" in prompt_en and 'language = "en"' in prompt_en


def test_max_slides_limit_reaches_normalizer(profile_of, synthetic_template):
    """Число слайдов из интерфейса реально ограничивает колоду модели."""
    import json

    from app.planner.planner import Planner

    profile = profile_of(synthetic_template)
    data = {"title": "Большая колода", "language": "ru", "slides": [
        {"slide_type": "title", "heading": "Титул презентации"}]}
    data["slides"] += [
        {"slide_type": "content", "heading": f"Слайд {i}",
         "blocks": [{"kind": "bullets", "items": [f"Тезис {i}"]}]}
        for i in range(1, 14)]
    data["slides"].append({"slide_type": "final", "heading": "Финал презентации"})

    class _FakeLlm:
        def __init__(self, payload: dict):
            self.payload = json.dumps(payload, ensure_ascii=False)

        def health(self) -> bool:
            return True

        def generate_text(self, *args, **kwargs) -> str:
            return self.payload

        _parse_json = staticmethod(json.loads)

    result = Planner(llm=_FakeLlm(data)).plan(
        "Бриф презентации", "", "project", profile, max_slides=6)
    assert result.used_llm is True
    assert len(result.deck.slides) == 6
    assert any("обрезана" in fix for fix in result.normalizations)
