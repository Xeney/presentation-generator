"""Клиент Ollama: генерация текста/JSON, проверка доступности, список моделей.

Используется для LLM (Qwen2.5 Instruct), VLM (Qwen2.5-VL) и эмбеддингов (BGE-M3).
Никаких платных API.
"""
from __future__ import annotations

import json
from typing import Optional

import httpx

from ..config import get_settings


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, base_url: Optional[str] = None, timeout_s: Optional[int] = None):
        s = get_settings()
        self.base_url = (base_url or s.ollama_base_url).rstrip("/")
        self.timeout = httpx.Timeout(timeout_s or s.llm_timeout_s)

    def health(self) -> bool:
        try:
            r = httpx.get(f"{self.base_url}/api/tags", timeout=5)
            return r.status_code == 200
        except Exception:
            return False

    def list_models(self) -> list[str]:
        try:
            r = httpx.get(f"{self.base_url}/api/tags", timeout=5)
            r.raise_for_status()
            return [m["name"] for m in r.json().get("models", [])]
        except Exception as exc:
            raise OllamaError(f"Ollama недоступен: {exc}") from exc

    def generate_text(self, prompt: str, system: Optional[str] = None, *,
                      model: Optional[str] = None, temperature: float = 0.3,
                      json_mode: bool = True, format_schema: Optional[dict] = None,
                      images: Optional[list[str]] = None) -> str:
        """Вызов /api/generate. images — список base64 PNG для VLM."""
        s = get_settings()
        payload: dict = {
            "model": model or s.llm_model,
            "prompt": prompt,
            "system": system or "",
            "stream": False,
            "options": {"temperature": temperature, "num_predict": 8192},
        }
        if json_mode:
            payload["format"] = format_schema or "json"
        if images:
            payload["images"] = images
        try:
            r = httpx.post(f"{self.base_url}/api/generate", json=payload, timeout=self.timeout)
        except Exception as exc:
            raise OllamaError(f"Ошибка вызова Ollama: {exc}") from exc
        if r.status_code != 200:
            raise OllamaError(f"Ollama вернул {r.status_code}: {r.text[:400]}")
        return r.json().get("response", "")

    def generate_json(self, prompt: str, system: Optional[str] = None, *,
                      model: Optional[str] = None) -> Optional[dict]:
        """Ответ в виде dict. При сбоях не паникует, возвращает None (fallback-путь)."""
        try:
            text = self.generate_text(prompt, system, model=model)
        except OllamaError:
            return None
        return self._parse_json(text)

    @staticmethod
    def _parse_json(text: str) -> Optional[dict]:
        t = text.strip()
        if t.startswith("```"):
            t = t.strip("`")
            if t.startswith("json"):
                t = t[4:]
            t = t.strip()
        try:
            return json.loads(t)
        except json.JSONDecodeError:
            start, end = t.find("{"), t.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(t[start:end + 1])
                except json.JSONDecodeError:
                    return None
            return None

    def embed(self, texts: list[str], model: Optional[str] = None) -> list[list[float]]:
        """Эмбеддинги BGE-M3 через /api/embed."""
        s = get_settings()
        try:
            r = httpx.post(f"{self.base_url}/api/embed",
                           json={"model": model or s.embedding_model, "input": texts},
                           timeout=httpx.Timeout(120))
            r.raise_for_status()
            return r.json().get("embeddings", [])
        except Exception:
            return []