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
from .imagegen import generate_images_for_deck
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
    """Артефакты одного варианта: нативный PPTX и, при html-режиме, HTML-путь.

    `pptx`/`audit` — основной результат (в режиме `html` в них кладётся PPTX,
    собранный из HTML). `html`/`html_pptx`/`html_audit` заполняются, когда
    пользователь выбрал режим `html` или `both` (ADR-035, ADR-036).
    """

    variant: str
    pptx: bytes = b""
    audit: dict = field(default_factory=dict)
    html: str = ""
    html_pptx: bytes = b""
    html_audit: dict = field(default_factory=dict)


def build_profile(template_bytes: bytes, template_name: str = "template.pptx") -> dict:
    """Извлекает профиль дизайн-системы из любого PPTX (требование «не заточено»)."""
    from .template.parser import TemplateParser

    parser = TemplateParser(template_bytes)
    parser._name = template_name
    return parser.parse().to_dict()


def plan_deck(brief: str, source: str, purpose: str, profile: dict,
              planner: Planner | None = None,
              corpus: ContentCorpus | None = None,
              max_slides: int | None = None,
              language: str = "ru") -> PlanningResult:
    planner = planner or Planner()
    return planner.plan(brief, source, purpose, profile, corpus=corpus,
                        max_slides=max_slides, language=language)


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


def render_html_variants(deck: Deck, profile: dict, template_bytes: bytes,
                         artifacts: list[VariantArtifact],
                         images: dict | None = None,
                         mode: str = "native") -> list[VariantArtifact]:
    """Дополняет варианты HTML-путём: HTML-файл + PPTX из HTML (ADR-035/036).

    Режимы: `html` — основным результатом становится PPTX из HTML; `both` —
    основной остаётся нативным, а HTML-версия прикладывается для сравнения.
    Оба PPTX проходят один и тот же детерминированный аудит.
    """
    from .render.html_renderer import render_html
    from .render.html_to_pptx import html_to_pptx

    if mode not in ("html", "both"):
        return artifacts
    for artifact in artifacts:
        artifact.html = render_html(deck, profile, variant=artifact.variant,
                                    template_bytes=template_bytes, images=images)
        artifact.html_pptx = html_to_pptx(artifact.html, profile,
                                          template_bytes=template_bytes)
        probe = VariantArtifact(variant=artifact.variant, pptx=artifact.html_pptx)
        artifact.html_audit = audit_variant(deck, probe, profile)
        if mode == "html":
            artifact.pptx = artifact.html_pptx
            artifact.audit = artifact.html_audit
    return artifacts


def auto_fix_variants(deck: Deck, profile: dict, template_bytes: bytes,
                      artifacts: list[VariantArtifact],
                      images: dict | None = None,
                      engine=None, max_passes: int = 2
                      ) -> tuple[Deck, list[VariantArtifact], list[dict], list[dict]]:
    """Авто-фиксы детерминированных проблем без подтверждения (ADR-032).

    Исправляются только те проблемы, для которых у FixEngine есть детермини-
    рованное преобразование контента; VLM- и grounding-замечания не трогаются.
    После правок все три варианта пере-рендериваются и пере-аудитируются, чтобы
    пользователь видел результат уже с применёнными фиксами.

    Возвращает (колода, артефакты, применённые, пропущенные).
    """
    from .audit.fixes import FixEngine

    # базовый движок нужен только для списка поддерживаемых кодов
    fixer = engine or FixEngine(profile)
    applied: list[dict] = []
    skipped: list[dict] = []
    for _ in range(max(0, max_passes)):
        issues: list[dict] = []
        seen: set[tuple] = set()
        for artifact in artifacts:
            for issue in artifact.audit.get("issues", []):
                key = (issue.get("code"), issue.get("slide"))
                # одна и та же детерминированная проблема есть во всех вариантах
                if key in seen or issue.get("code") not in fixer.handlers:
                    continue
                seen.add(key)
                issues.append(issue)
        if not issues:
            break
        deck, outcomes = fixer.apply(deck, issues, None)
        applied += [o for o in outcomes if o["status"] == "applied"]
        skipped += [o for o in outcomes if o["status"] == "skipped"]
        if not any(o["status"] == "applied" for o in outcomes):
            break
        artifacts = render_variants(deck, profile, template_bytes, images=images)
        for artifact in artifacts:
            artifact.audit = audit_variant(deck, artifact, profile)
    return deck, artifacts, applied, skipped


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
                  vlm: bool = True, auto_fix: bool = True,
                  max_slides: int | None = None,
                  language: str = "ru",
                  render_mode: str = "native") -> dict:
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
    result = plan_deck(brief, source_text, purpose, profile, planner=planner,
                       corpus=corpus, max_slides=max_slides, language=language)
    stages["plan_s"] = round(time.perf_counter() - t0, 2)
    deck = result.deck

    images = corpus.images if corpus is not None else None
    # иллюстрации: если у блока kind=image есть промпт, а картинки в контент-пакете
    # нет — генерируем (IMAGE_PROVIDER=off по умолчанию: слот остаётся заглушкой)
    t0 = time.perf_counter()
    images, imagegen_report = generate_images_for_deck(deck, images)
    if imagegen_report:
        stages["imagegen_s"] = round(time.perf_counter() - t0, 2)

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

    # авто-фиксы детерминированных проблем — без подтверждения (ADR-032):
    # пользователь получает уже исправленный результат, VLM- и grounding-замечания
    # пересчитываются/переносятся, чтобы отчёт остался полным
    auto_report: dict = {"applied": [], "skipped": []}
    if auto_fix and artifacts:
        t0 = time.perf_counter()
        deck, artifacts, applied, skipped = auto_fix_variants(
            deck, profile, template_bytes, artifacts, images=images)
        if applied:
            grounding = ground_deck(deck, corpus, brief=brief)
            for artifact in artifacts:
                verdict = vlm_by_variant.get(artifact.variant)
                if verdict:
                    merge_issues(artifact.audit,
                                 [dict(issue) for issue in violations_to_issues(verdict)])
                merge_issues(artifact.audit,
                             [dict(issue) for issue in grounding.get("issues", [])])
        auto_report = {"applied": applied, "skipped": skipped}
        if applied or skipped:
            stages["autofix_s"] = round(time.perf_counter() - t0, 2)

    render_mode = render_mode if render_mode in ("native", "html", "both") else "native"
    if render_mode in ("html", "both") and artifacts:
        t0 = time.perf_counter()
        artifacts = render_html_variants(deck, profile, template_bytes, artifacts,
                                         images=images, mode=render_mode)
        if render_mode == "html":
            # основные отчёты теперь относятся к PPTX из HTML — переносим в них
            # уже посчитанные VLM- и grounding-замечания
            for artifact in artifacts:
                verdict = vlm_by_variant.get(artifact.variant)
                if verdict:
                    merge_issues(artifact.audit,
                                 [dict(issue) for issue in violations_to_issues(verdict)])
                merge_issues(artifact.audit,
                             [dict(issue) for issue in grounding.get("issues", [])])
        stages["html_s"] = round(time.perf_counter() - t0, 2)

    stages["total_s"] = round(sum(stages.values()), 2)

    return {
        "profile": profile,
        "deck": json.loads(deck.model_dump_json()),
        "planner": result.to_dict(),
        "prompts": prompts_meta.versions(),
        "corpus": corpus.to_dict() if corpus is not None else None,
        "render_mode": render_mode,
        "variants": [
            {"name": a.variant, "pptx": a.pptx, "audit": a.audit,
             "html": a.html, "html_pptx": a.html_pptx, "html_audit": a.html_audit}
            for a in artifacts
        ],
        "vlm": vlm_result,
        "vlm_by_variant": vlm_by_variant,
        "grounding": grounding,
        "imagegen": imagegen_report,
        "auto_fixes": auto_report,
        "stages": stages,
        "html": html_export(deck, profile),
    }
