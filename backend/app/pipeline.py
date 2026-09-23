"""Сборка конвейера: профиль → колода → 3 варианта вёрстки → рендер → аудит.

Функции независимы друг от друга и используются API-слоем (jobs).
"""
from __future__ import annotations

import io
import json
import logging
from dataclasses import dataclass, field

from .audit.checks import Audit
from .audit.vlm import VlmAudit
from .export.html import deck_to_html
from .layout.engine import DesignContext, LayoutEngine
from .models.deck import Deck
from .planner.planner import PlanningResult, Planner
from .render.pptx_renderer import Renderer

log = logging.getLogger("pipeline")

VARIANTS = ["compact", "cards", "split"]


@dataclass
class VariantArtifact:
    variant: str
    pptx: bytes = b""
    audit: dict = field(default_factory=dict)


def build_profile(template_bytes: bytes, template_name: str = "template.pptx") -> dict:
    """Извлекает профиль дизайн-системы из любого PPTX (требование «не заточено»)."""
    from .template.parser import TemplateParser

    parser = TemplateParser(template_bytes)
    parser._name = template_name
    return parser.parse().to_dict()


def plan_deck(brief: str, source: str, purpose: str, profile: dict,
              planner: Planner | None = None) -> PlanningResult:
    planner = planner or Planner()
    return planner.plan(brief, source, purpose, profile)


def render_variants(deck: Deck, profile: dict, template_bytes: bytes) -> list[VariantArtifact]:
    dc = DesignContext(
        fonts={"headline": profile.get("headline_font"), "body": profile.get("body_font")},
        palette=profile.get("palette", []),
        type_scale=profile.get("type_scale", {}),
        slide_w=profile["slide_size"]["w_in"],
        slide_h=profile["slide_size"]["h_in"],
    )
    artifacts = []
    for variant in VARIANTS:
        renderer = Renderer(profile, variant=variant, template_bytes=template_bytes)
        plan_map = {}
        for i, slide in enumerate(deck.slides):
            layout = renderer._pick_layout(slide.slide_type)
            canvas = renderer._canvas(layout)
            eng = LayoutEngine(dc, variant=variant)
            plan_map[i] = eng.compose(slide, canvas)
        pptx = renderer.render(deck, plan_map)
        artifacts.append(VariantArtifact(variant=variant, pptx=pptx))
    return artifacts


def audit_variant(deck: Deck, artifact: VariantArtifact, profile: dict) -> dict:
    audit = Audit(profile)
    return audit.audit(deck, artifact.pptx)


def audit_vlm(pptx_bytes: bytes, profile: dict) -> dict:
    return VlmAudit(profile=profile).audit(pptx_bytes)


def html_export(deck: Deck, profile: dict) -> str:
    return deck_to_html(deck, profile)


def full_generate(brief: str, source: str, purpose: str,
                  template_bytes: bytes, template_name: str,
                  planner: Planner | None = None) -> dict:
    """Полный прогон: профиль → колода → варианты → аудиты → экспорт."""
    profile = build_profile(template_bytes, template_name)
    result = plan_deck(brief, source, purpose, profile, planner=planner)
    deck = result.deck
    artifacts = render_variants(deck, profile, template_bytes)
    for a in artifacts:
        a.audit = audit_variant(deck, a, profile)
    vlm = audit_vlm(artifacts[0].pptx if artifacts else b"", profile)
    return {
        "profile": profile,
        "deck": json.loads(deck.model_dump_json()),
        "planner": {"used_llm": result.used_llm, "attempts": result.attempts},
        "variants": [
            {"name": a.variant, "pptx": a.pptx, "audit": a.audit}
            for a in artifacts
        ],
        "vlm": vlm,
        "html": html_export(deck, profile),
    }