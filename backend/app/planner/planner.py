"""Планировщик: бриф + контент-пакет + профиль шаблона → JSON-колода.

Основной путь — LLM (Qwen2.5 Instruct через Ollama) со строгой Pydantic-схемой.
Офлайн-fallback включается, если Ollama недоступна или ответ не прошёл валидацию.
Промпты читаются из prompts/ (отдельными файлами, не зашиты в код).
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from ..config import get_settings
from ..models.deck import Deck
from .fallback import FallbackPlanner
from .llm import LlmError, OllamaClient, get_llm_client
from .normalize import describe, normalize_or_report

log = logging.getLogger("planner")

PROMPTS = Path(__file__).resolve().parents[3] / "prompts"
PLANNER_DIR = PROMPTS / "planner"


class PlanningResult:
    """Результат планирования: колода плюс сведения о том, кто её собрал."""

    def __init__(self, deck: Deck, used_llm: bool, attempts: int,
                 provider: str = "offline", model: str = "",
                 normalizations: list[str] | None = None):
        self.deck = deck
        self.used_llm = used_llm
        self.attempts = attempts
        self.provider = provider
        self.model = model
        self.normalizations = normalizations or []

    @property
    def label(self) -> str:
        """Строка для отчёта задания: «aitunnel/qwen3.5-9b», «внешний/…» или «offline»."""
        if not self.used_llm:
            return "offline-fallback"
        if self.provider == "runtime":
            # провайдер, подключённый через интерфейс: не показываем адрес/ключ
            return f"внешний/{self.model}" if self.model else "внешний"
        return f"{self.provider}/{self.model}" if self.model else self.provider

    def to_dict(self) -> dict:
        return {"used_llm": self.used_llm, "attempts": self.attempts,
                "provider": self.provider, "model": self.model,
                "label": self.label, "normalizations": self.normalizations}


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

    def _schema_dict(self) -> dict:
        return Deck.model_json_schema()

    def _schema(self) -> str:
        return json.dumps(self._schema_dict(), ensure_ascii=False)

    @staticmethod
    def _looks_like_schema(data: dict) -> bool:
        """Отличить «эхо схемы» от данных: слабая модель иногда возвращает саму схему."""
        if not isinstance(data, dict):
            return False
        return "$defs" in data or ("properties" in data and "slides" not in data)

    def _render_user(self, brief: str, source: str, purpose: str, profile: dict,
                     corpus=None, deck_size: int | None = None,
                     language: str = "ru") -> str:
        prompt = self._load_prompt("user.md").format(
            brief=brief, source=source or "—", purpose=purpose,
            profile=_profile_summary(profile), schema=self._schema(),
            deck_size=deck_size or self.settings.max_slides,
            corpus_images=(corpus.image_prompt_block() if corpus is not None
                           else "изображений нет"),
        )
        if language == "en":
            prompt += ("\n\nЯзык колоды: английский. Все заголовки, пункты и "
                       "подписи — на английском; поле language = \"en\".")
        return prompt

    def plan(self, brief: str, source: str = "", purpose: str = "project",
             profile: dict | None = None, corpus=None,
             max_slides: int | None = None,
             language: str = "ru") -> PlanningResult:
        profile = profile or {}
        brief = (brief or "").strip()
        language = language if language in ("ru", "en") else "ru"
        # границы схемы Deck: 3..15 слайдов; значение из интерфейса не должно
        # выводить колоду за них
        limit = min(15, max(3, int(max_slides))) if max_slides else self.settings.max_slides
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

        user = self._render_user(brief, source, purpose, profile, corpus,
                                 deck_size=limit, language=language)
        system = self._load_prompt("system.md")
        model = self.settings.active_llm_model
        attempts = 0
        for attempt in range(1, self.settings.planner_llm_max_retries + 1):
            attempts = attempt
            deck, fixes, error, fatal = self._attempt(user, system, model, limit)
            if deck is not None:
                log.info("LLM-планировщик %s: колода из %s слайдов (попытка %s)",
                         label, len(deck.slides), attempt)
                return PlanningResult(deck, used_llm=True, attempts=attempt,
                                      provider=self.settings.active_llm_provider,
                                      model=model, normalizations=fixes)
            last_error = error
            log.warning("LLM-планировщик (попытка %s, %s): %s", attempt, model, error)
            if fatal:
                # постоянная ошибка (ключ, баланс, доступ к модели, таймаут):
                # повторять бессмысленно и дорого
                break
            user += ("\n\nПредыдущий ответ отклонён: " + error
                     + "\nВерни исправленный JSON строго по схеме и ограничениям.")

        # запасная модель: одна попытка более крупной моделью того же провайдера
        fallback_model = self.settings.fallback_llm_model
        if fallback_model and not self.settings.demo_mode:
            log.info("пробуем запасную модель %s", fallback_model)
            deck, fixes, error, _ = self._attempt(user, system, fallback_model, limit)
            if deck is not None:
                return PlanningResult(deck, used_llm=True, attempts=attempts + 1,
                                      provider=self.settings.active_llm_provider,
                                      model=fallback_model, normalizations=fixes)
            last_error = f"{fallback_model}: {error}"
            log.warning("запасная модель не помогла: %s", error)

        if self.settings.demo_mode:
            raise LlmError(f"DEMO_MODE: планировщик {label} не дал валидную колоду "
                           f"({last_error}); офлайн-fallback отключён")
        log.info("LLM-планировщик исчерпал попытки — офлайн-fallback (%s)", last_error)
        return PlanningResult(self.fallback.plan(brief, source, purpose, corpus=corpus),
                              used_llm=False, attempts=attempts)

    def _attempt(self, user: str, system: str, model: str,
                 max_slides: int | None = None
                 ) -> tuple[Optional[Deck], list[str], str, bool]:
        """Одна попытка получить колоду: запрос → нормализация → валидация.

        Возвращает (колода, правки нормализации, текст ошибки, фатальная ли ошибка).
        """
        try:
            raw = self.llm.generate_text(user, system, model=model, temperature=0.3,
                                         format_schema=self._schema_dict())
        except LlmError as exc:
            return None, [], str(exc), not exc.retryable
        data = self.llm._parse_json(raw)
        if data is None or self._looks_like_schema(data):
            return None, [], ("модель вернула схему вместо данных" if data
                              else "ответ модели не является JSON"), False
        data, fixes = normalize_or_report(
            data, max_slides=max_slides or self.settings.max_slides)
        if fixes:
            log.info("LLM-планировщик: нормализация колоды — %s", describe(fixes))
        try:
            return Deck.model_validate(data), fixes, "", False
        except Exception as exc:  # noqa: BLE001 — текст ошибки уходит модели
            return None, fixes, f"ответ не прошёл валидацию: {str(exc)[:200]}", False