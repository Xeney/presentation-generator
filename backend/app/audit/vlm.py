"""Недетерминированный (VLM) аудит: Qwen2.5-VL оценивает изображение каждого слайда.

Картинки слайдов получаем через миниатюры (LibreOffice → PDF → PNG). Оценка —
ровно по 11 вопросам Приложения 1 ТЗ (`prompts/vlm/system.md`). Вопросы про язык
колоды и логику соседей требуют контекста, поэтому в промпт передаются язык,
заголовки соседних слайдов и краткая выжимка исходных материалов.

VLM доступен только там, где поднята Ollama с моделью qwen2.5vl:7b; иначе модуль
возвращает `{"available": false}` и пайплайн продолжает работу.
"""
from __future__ import annotations

import base64
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from ..config import get_settings
from ..planner.llm import OllamaClient, get_vlm_client
from ..render.pdf import pptx_to_pngs

log = logging.getLogger("audit_vlm")

PROMPTS = Path(__file__).resolve().parents[3] / "prompts" / "vlm"

# формулировки критериев для UI: один в один с prompts/vlm/system.md
CRITERIA = {
    1: "Заголовок содержит вывод, а не просто называет тему",
    2: "Содержимое слайда соответствует заголовку",
    3: "Слайд пересказывается одним предложением",
    4: "Все цифры и факты со слайда есть в исходных материалах",
    5: "На слайде есть содержание, а не только заголовок",
    6: "Картинки и иконки относятся к теме слайда",
    7: "Нет служебного мусора: реплик спикера, кусков промпта",
    8: "Текст без опечаток",
    9: "Слайд на том же языке, что и вся колода",
    10: "Строки таблицы и элементы легенды работают на мысль слайда",
    11: "Слайд связан по логике с соседними слайдами",
}
N_CRITERIA = len(CRITERIA)


def violations_to_issues(result: dict) -> list[dict]:
    """Нарушения VLM как проблемы аудита: попадают в общий список UI.

    Помечаются `deterministic: false` — это контекстуальные проверки, ответ
    модели может отличаться при повторном запуске (Приложение 1 ТЗ).
    """
    issues: list[dict] = []
    for slide in result.get("slides", []):
        summary = (slide.get("summary") or "").strip()
        for number in slide.get("violations", []):
            issues.append({
                "id": f"vlm_criterion_{number}-{slide.get('slide', -1)}",
                "code": f"vlm_criterion_{number}",
                "severity": "warning",
                "slide": slide.get("slide", -1),
                "bbox": [],
                "deterministic": False,
                "message": f"VLM: {CRITERIA.get(number, number)}"
                           + (f" — {summary}" if summary else ""),
            })
    return issues


# Минимальный бюджет стадии и минимальный лимит одного запроса: короче — модель
# (особенно на CPU) не успеет ответить. Вынесены в константы, чтобы тесты могли
# проверить механику бюджета без ожидания в минуты.
MIN_STAGE_BUDGET_S = 30.0
MIN_REQUEST_TIMEOUT_S = 15.0


class VlmAudit:
    """Оценка смысла слайдов моделью: контекстная, а не детерминированная часть аудита."""

    def __init__(self, llm: Optional[OllamaClient] = None, profile: Optional[dict] = None):
        self.settings = get_settings()
        # клиент выбирается по VLM_PROVIDER (aitunnel | openai_compat | ollama | off)
        self.llm = llm if llm is not None else get_vlm_client()
        self.profile = profile or {}
        self.system = (PROMPTS / "system.md").read_text(encoding="utf-8")
        self.user_template = (PROMPTS / "user.md").read_text(encoding="utf-8")

    def available(self) -> bool:
        if self.llm is None or self.settings.disable_llm:
            return False
        return self.llm.health()

    def unavailable_reason(self) -> str:
        """Почему стадия недоступна — это уходит в отчёт задания."""
        if self.llm is None:
            return "VLM-аудит выключен (VLM_PROVIDER=off)"
        if self.settings.disable_llm:
            return "DISABLE_LLM=true: модели отключены"
        return (f"VLM {self.settings.vlm_label} недоступна "
                f"(проверьте адрес, ключ и сеть)")

    def prompt_version(self) -> dict:
        """Версия промпта из манифеста prompts/registry.json (ADR-013)."""
        from ..prompts_meta import versions

        try:
            registered = versions(["vlm/audit", "vlm/user"]).get("vlm/audit", {})
        except Exception as exc:  # noqa: BLE001 — без манифеста работаем, но честно молчим
            log.warning("манифест промптов недоступен: %s", exc)
            registered = {}
        return {
            "id": "vlm/audit",
            "version": registered.get("version", "?"),
            "hash": registered.get("hash", ""),
            "model": self.settings.active_vlm_model,
        }

    # ------------------------------------------------------------------ run
    def audit(self, pptx_bytes: bytes, deck=None,
              source_digest: str = "") -> dict:
        """Возвращает результат по слайдам: {'slides': [{violations, summary, ok}...]}."""
        if not self.available():
            reason = self.unavailable_reason()
            log.info("VLM-аудит недоступен: %s", reason)
            return {"available": False, "slides": [], "reason": reason,
                    "provider": self.settings.active_vlm_provider,
                    "criteria": CRITERIA}
        try:
            images = self._slide_pngs_b64(pptx_bytes)
        except Exception as exc:  # noqa: BLE001 — нет миниатюр = нет VLM-аудита
            log.warning("VLM: нет миниатюр: %s", exc)
            return {"available": False, "slides": [], "reason": f"нет миниатюр: {exc}",
                    "criteria": CRITERIA}

        started = time.perf_counter()
        # слайды независимы: спрашиваем модель параллельно, порядок сохраняем.
        # Общий бюджет стадии ограничен — на живом демо важнее уложиться во время,
        # чем проверить каждый слайд; непроверенные честно помечаются.
        verdicts: list[dict] = [{} for _ in images]
        timings: list[float] = [0.0 for _ in images]
        deadline = started + max(MIN_STAGE_BUDGET_S, self.settings.vlm_audit_budget_s)
        workers = max(1, min(self.settings.vlm_audit_workers, len(images)))

        def ask(index: int) -> None:
            began = time.perf_counter()
            context = self._context(index, len(images), deck, source_digest)
            # запрос живёт не дольше остатка бюджета стадии: иначе медленная
            # модель (CPU) держит пул и стадия растягивается на минуты
            budget_left = max(MIN_REQUEST_TIMEOUT_S,
                              deadline - time.perf_counter())
            verdicts[index] = self._ask_one(images[index], context, budget_left)
            timings[index] = round(time.perf_counter() - began, 2)

        # Слайдов больше, чем воркеров: задачи, начавшиеся после дедлайна,
        # получают остаток бюджета (минимум 15 с) и быстро отваливаются по
        # таймауту, поэтому стадия укладывается в бюджет + один запрос.
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(ask, index): index for index in range(len(images))}
            for future in as_completed(futures):
                index = futures[future]
                try:
                    future.result()
                except Exception as exc:  # noqa: BLE001 — один слайд не рушит стадию
                    verdicts[index] = {"error": str(exc)[:200]}
                if time.perf_counter() > deadline and not verdicts[index]:
                    verdicts[index] = {"skipped": True,
                                       "error": "не проверено: исчерпан бюджет стадии"}

        slides = []
        errors = 0
        skipped = 0
        last_error = ""
        for index, verdict in enumerate(verdicts):
            if verdict.get("skipped"):
                skipped += 1
            if verdict.get("error"):
                errors += 1
                last_error = verdict["error"]
            failed = sorted(verdict.get("answers_no", []))
            slides.append({
                "slide": index,
                "ok": not failed and not verdict.get("error"),
                "violations": failed,
                "violations_text": [CRITERIA.get(n, str(n)) for n in failed],
                "criteria_yes": sorted(verdict.get("answers_yes", [])),
                "summary": (verdict.get("summary") or "")[:300],
                "error": verdict.get("error", ""),
            })
        per_slide_seconds = timings
        if errors and errors >= len(images):
            # модель ответила отказом на каждый слайд: честнее сказать «недоступно»,
            # чем показать «замечаний нет»
            return {"available": False, "slides": [], "criteria": CRITERIA,
                    "provider": self.settings.active_vlm_provider,
                    "reason": f"VLM не ответила ни на один слайд: {last_error[:200]}"}
        return {
            "available": True,
            "provider": self.settings.active_vlm_provider,
            "model": self.settings.active_vlm_model,
            "slides": slides,
            "errors": errors,
            "skipped": skipped,
            "criteria": CRITERIA,
            "elapsed_s": round(time.perf_counter() - started, 2),
            "per_slide_s": per_slide_seconds,
            "prompt": self.prompt_version(),
        }

    # -------------------------------------------------------------- helpers
    def _slide_pngs_b64(self, pptx_bytes: bytes) -> list[str]:
        """PNG слайдов в base64, уменьшенные до читаемого для модели размера.

        Рендер в 110 dpi даёт ~340 КБ на слайд: это лишние токены и секунды на
        передачу, а для оценки композиции и текста достаточно 1280 px по ширине.
        """
        from io import BytesIO

        from PIL import Image

        limit = self.settings.vlm_image_max_px
        encoded: list[str] = []
        for png in pptx_to_pngs(pptx_bytes, dpi=110):
            if limit:
                with Image.open(BytesIO(png)) as image:
                    if image.width > limit:
                        ratio = limit / image.width
                        image = image.resize((limit, max(1, round(image.height * ratio))),
                                             Image.LANCZOS)
                    buffer = BytesIO()
                    image.convert("RGB").save(buffer, format="PNG", optimize=True)
                    png = buffer.getvalue()
            encoded.append(base64.b64encode(png).decode("ascii"))
        return encoded

    @staticmethod
    def _context(index: int, total: int, deck, source_digest: str) -> dict:
        def heading(position: int) -> str:
            if deck is None or not (0 <= position < len(deck.slides)):
                return "нет"
            return (deck.slides[position].heading or "")[:90] or "без заголовка"

        return {
            "slide_index": index + 1,
            "slide_count": total,
            "language": getattr(deck, "language", "ru"),
            "prev_heading": heading(index - 1) if index > 0 else "нет (первый слайд)",
            "next_heading": (heading(index + 1)
                             if deck is not None and index + 1 < len(deck.slides)
                             else "нет (последний слайд)"),
            "source_digest": (source_digest or "исходные материалы не переданы")[:1200],
        }

    def _ask_one(self, image_b64: str, context: dict,
                 timeout_s: Optional[float] = None) -> dict:
        prompt = self.user_template.format(**context)
        try:
            text = self.llm.generate_text(
                prompt, self.system, model=self.settings.active_vlm_model,
                temperature=0.0, json_mode=True, images=[image_b64],
                timeout_s=timeout_s)
        except Exception as exc:  # noqa: BLE001
            log.warning("VLM-запрос не удался: %s", exc)
            return {"answers_yes": [], "answers_no": [], "summary": "",
                    "error": str(exc)}
        data = self.llm._parse_json(text)
        if not data:
            return {"answers_yes": [], "answers_no": [], "summary": "",
                    "error": "ответ модели не является JSON"}
        yes = [int(x) for x in data.get("answers_yes", []) if str(x).isdigit()]
        no = [int(x) for x in data.get("answers_no", []) if str(x).isdigit()]
        return {
            "answers_yes": sorted({n for n in yes if n in CRITERIA}),
            "answers_no": sorted({n for n in no if n in CRITERIA}),
            "summary": data.get("summary", ""),
        }
