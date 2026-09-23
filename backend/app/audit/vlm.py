"""Недетерминированный (VLM) аудит: Qwen2.5-VL оценивает изображение каждого слайда.

Картинки слайдов получаем через миниатюры (LibreOffice → PDF → PNG). Оценка —
по 11 критериям из prompts/vlm/system.md. VLM доступен только там, где поднята
Ollama с моделью qwen2.5-vl; иначе модуль возвращает пустой результат.
"""
from __future__ import annotations

import base64
import io
import json
import logging
from pathlib import Path

from ..config import get_settings
from ..planner.llm import OllamaClient
from ..render.pdf import pptx_to_pngs

log = logging.getLogger("audit_vlm")

PROMPTS = Path(__file__).resolve().parents[3] / "prompts" / "vlm"
N_CRITERIA = 11


class VlmAudit:
    def __init__(self, llm: OllamaClient | None = None, profile: dict | None = None):
        self.settings = get_settings()
        self.llm = llm or OllamaClient()
        self.profile = profile or {}
        self.system = (PROMPTS / "system.md").read_text(encoding="utf-8")
        self.user = (PROMPTS / "user.md").read_text(encoding="utf-8")

    def available(self) -> bool:
        return self.settings.disable_llm is False and self.llm.health()

    def _slide_png_b64(self, pptx_bytes: bytes) -> list[str]:
        pngs = pptx_to_pngs(pptx_bytes, dpi=110)
        return [base64.b64encode(png).decode("ascii") for png in pngs]

    def audit(self, pptx_bytes: bytes) -> dict:
        """Возвращает результат по слайдам: {'slides': [{violations, summary, ok}]...}."""
        if not self.available():
            log.info("VLM-аудит недоступен (Ollama/VLM не в сети)")
            return {"available": False, "slides": []}
        try:
            images = self._slide_png_b64(pptx_bytes)
        except Exception as exc:
            log.warning("VLM: нет миниатюр: %s", exc)
            return {"available": False, "slides": []}

        slides = []
        model = self.settings.vlm_model
        for i, img in enumerate(images):
            verdict = self._ask_one(img, model)
            failed = [n for n in verdict.get("answers_no", [])]
            slides.append({
                "slide": i,
                "ok": len(failed) == 0,
                "violations": failed,
                "criteria_yes": verdict.get("answers_yes", []),
                "summary": (verdict.get("summary") or "")[:300],
            })
        return {"available": True, "model": model, "slides": slides}

    def _ask_one(self, img_b64: str, model: str) -> dict:
        try:
            text = self.llm.generate_text(
                self.user, self.system, model=model, temperature=0.0,
                json_mode=False, images=[img_b64])
        except Exception as exc:
            log.warning("VLM-запрос не удался: %s", exc)
            return {"answers_yes": [], "answers_no": [], "summary": ""}
        data = self.llm._parse_json(text)
        if not data:
            return {"answers_yes": [], "answers_no": [], "summary": ""}
        yes = [int(x) for x in data.get("answers_yes", []) if str(x).isdigit()]
        no = [int(x) for x in data.get("answers_no", []) if str(x).isdigit()]
        return {"answers_yes": sorted(set(yes)), "answers_no": sorted(set(no)),
                "summary": data.get("summary", "")}