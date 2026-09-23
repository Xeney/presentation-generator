"""Планировщик: бриф + контент-пакет + профиль шаблона → JSON-колода.

Основной путь — LLM (Qwen2.5 Instruct через Ollama) со строгой Pydantic-схемой.
Офлайн-fallback включается, если Ollama недоступна или ответ не прошёл валидацию.
Промпты читаются из prompts/ (отдельными файлами, не зашиты в код).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from ..config import get_settings
from ..models.deck import Deck
from .fallback import FallbackPlanner
from .llm import LlmError, OllamaClient, get_llm_client

log = logging.getLogger("planner")

PROMPTS = Path(__file__).resolve().parents[3] / "prompts"
PLANNER_DIR = PROMPTS / "planner"


class PlanningResult:
    """Результат планирования: колода плюс сведения о том, кто её собрал."""

    def __init__(self, deck: Deck, used_llm: bool, attempts: int,
                 provider: str = "offline", model: str = ""):
        self.deck = deck
        self.used_llm = used_llm
        self.attempts = attempts
        self.provider = provider
        self.model = model

    @property
    def label(self) -> str:
        """Строка для отчёта задания: «aitunnel/qwen3.5-9b» или «offline». """
        if not self.used_llm:
            return "offline-fallback"
        return f"{self.provider}/{self.model}" if self.model else self.provider

    def to_dict(self) -> dict:
        return {"used_llm": self.used_llm, "attempts": self.attempts,
                "provider": self.provider, "model": self.model,
                "label": self.label}


def _profile_summary(profile: dict) -> str:
    """Компактный контекст шаблона для LLM (только объём, без координат)."""
    try:
        layouts = profile["layout_groups"]
        fonts = profile.get("headline_font") and profile["headline_font"]
        scale = profile.get("type_scale", {})
        return (
            f"Шаблон: {profile.get('source_file')}, слайд {profile['slide_size']['w_in']:.1f}x"
            f"{profile['slide_size']['h_in']:.1f} in. "
            f"Шрифт заголовков: {fonts}. Размеры: title {scale.get('title', [])[:4]}, "
            f"body {scale.get('body', [])[:4]}. Макетов по ролям: {dict(layouts)}."
        )
    except Exception:
        return "Шаблон: не указан."


class Planner:
    def __init__(self, llm=None):
        self.llm = llm or get_llm_client()
        self.settings = get_settings()
        self.fallback = FallbackPlanner()

    @staticmethod
    def _load_prompt(name: str) -> str:
        return (PLANNER_DIR / name).read_text(encoding="utf-8")

    def _schema(self) -> str:
        return json.dumps(Deck.model_json_schema(), ensure_ascii=False)

    def _render_user(self, brief: str, source: str, purpose: str, profile: dict,
                     corpus=None) -> str:
        return self._load_prompt("user.md").format(
            brief=brief, source=source or "—", purpose=purpose,
            profile=_profile_summary(profile), schema=self._schema(),
            deck_size=self.settings.max_slides,
            corpus_images=(corpus.image_prompt_block() if corpus is not None
                           else "изображений нет"),
        )

    def plan(self, brief: str, source: str = "", purpose: str = "project",
             profile: dict | None = None, corpus=None) -> PlanningResult:
        profile = profile or {}
        brief = (brief or "").strip()
        label = self.settings.planner_label
        last_error = ""

        if self.settings.disable_llm or not self.llm.health():
            last_error = (f"планировщик {label} недоступен "
                          f"(проверьте адрес, ключ и сеть)")
            if self.settings.demo_mode:
                raise LlmError(f"DEMO_MODE: {last_error}; офлайн-fallback отключён")
            log.info("%s — офлайн-планировщик", last_error)
            return PlanningResult(self.fallback.plan(brief, source, purpose, corpus=corpus),
                                  used_llm=False, attempts=0)

        user = self._render_user(brief, source, purpose, profile, corpus)
        system = self._load_prompt("system.md")
        model = self.settings.active_llm_model
        for attempt in range(1, self.settings.planner_llm_max_retries + 1):
            try:
                raw = self.llm.generate_text(user, system, model=model, temperature=0.3)
            except LlmError as exc:
                # провайдер отказал (нет баланса, нет модели, сеть): не роняем
                # задание, а честно уходим в офлайн-планировщик
                last_error = str(exc)
                log.warning("LLM недоступна (попытка %s): %s", attempt, exc)
                break
            data = self.llm._parse_json(raw)
            if data is None:
                log.warning("LLM ответ не JSON (попытка %s)", attempt)
                last_error = "ответ модели не является JSON"
                continue
            try:
                deck = Deck.model_validate(data)
                log.info("LLM-планировщик %s: колода из %s слайдов (попытка %s)",
                         label, len(deck.slides), attempt)
                return PlanningResult(deck, used_llm=True, attempts=attempt,
                                      provider=self.settings.active_llm_provider,
                                      model=model)
            except Exception as exc:
                last_error = f"ответ не прошёл валидацию: {str(exc)[:200]}"
                log.warning("LLM не прошёл Pydantic-валидацию (попытка %s): %s",
                            attempt, str(exc)[:300])
                user += (
                    "\n\nПоследняя попытка не прошла валидацию. Ошибки:\n"
                    + str(exc)[:800]
                    + "\nВерни исправленный JSON строго по схеме."
                )

        if self.settings.demo_mode:
            raise LlmError(f"DEMO_MODE: планировщик {label} не дал валидную колоду "
                           f"({last_error}); офлайн-fallback отключён")
        log.info("LLM-планировщик исчерпал попытки — офлайн-fallback (%s)", last_error)
        return PlanningResult(self.fallback.plan(brief, source, purpose, corpus=corpus),
                              used_llm=False,
                              attempts=self.settings.planner_llm_max_retries)