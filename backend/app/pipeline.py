"""Сборка конвейера: профиль → колода → 3 варианта вёрстки → рендер → аудит → экспорт.

Функции независимы друг от друга и используются API-слоем (jobs). Каждая стадия
измеряется: время попадает в результат задания (docs/DECISIONS.md ADR-012).
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field

from .audit.checks import Audit
from .audit.vlm import VlmAudit
from .content.corpus import ContentCorpus
from .export.html import deck_to_html
from .layout.engine import DesignContext
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
              planner: Planner | None = None,
              corpus: ContentCorpus | None = None) -> PlanningResult:
    planner = planner or Planner()
    return planner.plan(brief, source, purpose, profile, corpus=corpus)


def render_variants(deck: Deck, profile: dict, template_bytes: bytes,
                    images: dict | None = None) -> list[VariantArtifact]:
    """Три варианта вёрстки одного контента на одном шаблоне."""
    dc = DesignContext.from_profile(profile)
    artifacts = []
    for variant in VARIANTS:
        renderer = Renderer(profile, variant=variant, template_bytes=template_bytes,
                            images=images)
        artifacts.append(VariantArtifact(variant=variant, pptx=renderer.render_deck(deck, dc)))
    return artifacts


def audit_variant(deck: Deck, artifact: VariantArtifact, profile: dict) -> dict:
    return Audit(profile).audit(deck, artifact.pptx)


def audit_vlm(pptx_bytes: bytes, profile: dict) -> dict:
    return VlmAudit(profile=profile).audit(pptx_bytes)


def html_export(deck: Deck, profile: dict) -> str:
    return deck_to_html(deck, profile)


def full_generate(brief: str, source: str, purpose: str,
                  template_bytes: bytes, template_name: str,
                  planner: Planner | None = None,
                  corpus: ContentCorpus | None = None,
                  vlm: bool = True) -> dict:
    """Полный прогон: профиль → колода → варианты → аудиты → экспорт.

    `corpus` — импортированный контент-пакет: его текст уходит планировщику как
    источник фактов, изображения — в рендер (реестр картинок).
    """
    stages: dict[str, float] = {}
    t0 = time.perf_counter()

    profile = build_profile(template_bytes, template_name)
    stages["parse_s"] = round(time.perf_counter() - t0, 2)

    source_text = source or ""
    if corpus is not None:
        corpus_text = corpus.text()
        source_text = corpus_text + (f"\n\n{source}" if source.strip() else "")

    t0 = time.perf_counter()
    result = plan_deck(brief, source_text, purpose, profile, planner=planner, corpus=corpus)
    stages["plan_s"] = round(time.perf_counter() - t0, 2)
    deck = result.deck

    images = corpus.images if corpus is not None else None
    t0 = time.perf_counter()
    artifacts = render_variants(deck, profile, template_bytes, images=images)
    stages["render_s"] = round(time.perf_counter() - t0, 2)

    t0 = time.perf_counter()
    for artifact in artifacts:
        artifact.audit = audit_variant(deck, artifact, profile)
    stages["audit_s"] = round(time.perf_counter() - t0, 2)

    vlm_result: dict = {"available": False, "slides": []}
    if vlm and artifacts:
        t0 = time.perf_counter()
        vlm_result = audit_vlm(artifacts[0].pptx, profile)
        stages["vlm_s"] = round(time.perf_counter() - t0, 2)

    stages["total_s"] = round(sum(stages.values()), 2)

    return {
        "profile": profile,
        "deck": json.loads(deck.model_dump_json()),
        "planner": {"used_llm": result.used_llm, "attempts": result.attempts},
        "corpus": corpus.to_dict() if corpus is not None else None,
        "variants": [
            {"name": a.variant, "pptx": a.pptx, "audit": a.audit}
            for a in artifacts
        ],
        "vlm": vlm_result,
        "stages": stages,
        "html": html_export(deck, profile),
    }
