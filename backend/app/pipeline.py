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
from .audit.vlm import VlmAudit, violations_to_issues
from .config import get_settings
from .content.corpus import ContentCorpus
from .export.html import deck_to_html
from .layout.engine import DesignContext
from .models.deck import Deck
from .planner.llm import get_llm_client
from .planner.planner import PlanningResult, Planner
from . import prompts_meta
from .render.pptx_renderer import Renderer
from .retrieval.grounding import GroundingChecker, merge_issues

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


def audit_vlm(pptx_bytes: bytes, profile: dict, deck: Deck | None = None,
              source_digest: str = "") -> dict:
    return VlmAudit(profile=profile).audit(pptx_bytes, deck=deck,
                                           source_digest=source_digest)


def ground_deck(deck: Deck, corpus: ContentCorpus | None, brief: str = "") -> dict:
    """Проверка опоры на источник: числа точно, смысл — эмбеддингами BGE-M3."""
    settings = get_settings()
    if not settings.grounding_enabled:
        return {"available": False, "reason": "grounding выключен",
                "issues": [], "issues_count": 0}
    checker = GroundingChecker(
        corpus, llm=get_llm_client(), brief=brief,
        off_source_threshold=settings.grounding_off_source_threshold,
        duplicate_threshold=settings.grounding_duplicate_threshold)
    return checker.check(deck).to_dict()


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

    # проверка опоры на источник: проблемы общие для всех вариантов (это контент),
    # поэтому считаем один раз и добавляем в каждый отчёт
    t0 = time.perf_counter()
    grounding = ground_deck(deck, corpus, brief=brief)
    for artifact in artifacts:
        merge_issues(artifact.audit, [dict(issue) for issue in grounding.get("issues", [])])
    stages["grounding_s"] = round(time.perf_counter() - t0, 2)

    settings = get_settings()
    vlm_result: dict = {"available": False, "slides": [], "reason": "стадия выключена"}
    vlm_by_variant: dict = {}
    if vlm and artifacts:
        t0 = time.perf_counter()
        digest = corpus.text()[:1200] if corpus is not None else ""
        targets = artifacts if settings.vlm_audit_all_variants else artifacts[:1]
        for artifact in targets:
            verdict = audit_vlm(artifact.pptx, profile, deck=deck, source_digest=digest)
            vlm_by_variant[artifact.variant] = verdict
            merge_issues(artifact.audit,
                         [dict(issue) for issue in violations_to_issues(verdict)])
        vlm_result = vlm_by_variant.get(artifacts[0].variant, vlm_result)
        stages["vlm_s"] = round(time.perf_counter() - t0, 2)

    stages["total_s"] = round(sum(stages.values()), 2)

    return {
        "profile": profile,
        "deck": json.loads(deck.model_dump_json()),
        "planner": {"used_llm": result.used_llm, "attempts": result.attempts},
        "prompts": prompts_meta.versions(),
        "corpus": corpus.to_dict() if corpus is not None else None,
        "variants": [
            {"name": a.variant, "pptx": a.pptx, "audit": a.audit}
            for a in artifacts
        ],
        "vlm": vlm_result,
        "vlm_by_variant": vlm_by_variant,
        "grounding": grounding,
        "stages": stages,
        "html": html_export(deck, profile),
    }
