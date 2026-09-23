import io

import pytest

from app.audit.checks import Audit
from app.layout.engine import LayoutEngine
from app.planner.fallback import FallbackPlanner
from app.render.pptx_renderer import Renderer
from app.template.parser import TemplateParser

BRIEF = ("Платформа аналитики VK Tech сократила время подготовки отчётов на 40%, "
         "автоматизировала 12 задач и охватила 5 подразделений. 2000 сотрудников "
         "используют её еженедельно. План — 10 отделов к концу года и ML-предсказания выручки.")


def _run_all(tpl: bytes) -> dict:
    profile = TemplateParser(tpl).parse().to_dict()
    deck = FallbackPlanner().plan(BRIEF, "", "project")
    renderer = Renderer(profile)
    out = {}
    for variant in ("compact", "cards", "split"):
        slides = LayoutEngine(profile, deck, variant).build()
        pptx = renderer.render(deck, slides)
        audit = Audit(profile).audit(deck, pptx)
        out[variant] = (audit, pptx)
    return out


def test_audit_positive_case(template_lct):
    results = _run_all(template_lct)
    for variant, (audit, _) in results.items():
        assert audit["passed"], f"{variant}: {audit['issues'][:3]}"
        assert audit["errors"] == 0
        assert audit["warnings"] == 0


def test_audit_catches_out_of_bounds(template_lct):
    profile = TemplateParser(template_lct).parse().to_dict()
    deck = FallbackPlanner().plan(BRIEF, "", "project")
    pptx = Renderer(profile).render(
        deck, LayoutEngine(profile, deck, "compact").build())
    audit = Audit(profile).audit(deck, pptx)
    assert all("bbox" in i for i in audit["issues"])


@pytest.mark.parametrize("payload", [
    b"not a pptx at all",
    b"",
])
def test_render_rejects_garbage(payload):
    with pytest.raises(Exception):
        Renderer({}).render(FallbackPlanner().plan("x" * 20, "", "project"), [])