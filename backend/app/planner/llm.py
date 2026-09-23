"""Клиенты LLM: локальная Ollama и OpenAI-совместимый шлюз (только для разработки).

Основной путь — Ollama: открытые веса, локальный инференс, без платных API
(требование ТЗ). Второй клиент нужен, когда под рукой нет GPU/Ollama: он умеет
говорить с любым OpenAI-совместимым шлюзом (например, OpenCode Zen), включается
переменной `LLM_PROVIDER=openai_compat` и в сдаче не используется (ADR-019).

Ключ внешнего шлюза читается только из окружения/.env: он не попадает ни в код,
ни в репозиторий, ни в сообщения об ошибках.
"""
from __future__ import annotations

import json
from typing import Optional

import httpx

from ..config import get_settings


class LlmError(RuntimeError):
    """Ошибка обращения к модели (локальной или внешней)."""


class OllamaError(LlmError):
    """Совместимое имя для ошибок Ollama."""


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
                           json={"model": model or s.active_embedding_model, "input": texts},
                           timeout=httpx.Timeout(120))
            r.raise_for_status()
            return r.json().get("embeddings", [])
        except Exception:
            return []


class OpenAICompatClient:
    """Клиент OpenAI-совместимого шлюза (chat/completions + embeddings).

    Назначение — локальная разработка без GPU: тот же интерфейс, что у
    `OllamaClient`, поэтому планировщик, VLM-аудит и grounding работают без
    изменений. Ключ не логируется и не возвращается в текстах ошибок.
    """

    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None,
                 timeout_s: Optional[int] = None):
        s = get_settings()
        self.base_url = (base_url or s.openai_compat_base_url or "").rstrip("/")
        self.api_key = api_key if api_key is not None else s.openai_compat_api_key
        self.timeout = httpx.Timeout(timeout_s or s.llm_timeout_s)

    # ------------------------------------------------------------- служебное
    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    @staticmethod
    def _safe(text: str, limit: int = 300) -> str:
        """Обрезает ответ провайдера: наружу не уходят длинные тела и заголовки."""
        return (text or "")[:limit]

    def health(self) -> bool:
        if not self.base_url:
            return False
        try:
            r = httpx.get(f"{self.base_url}/models", headers=self._headers(), timeout=10)
            return r.status_code == 200
        except Exception:
            return False

    def list_models(self) -> list[str]:
        if not self.base_url:
            raise LlmError("не задан OPENAI_COMPAT_BASE_URL")
        try:
            r = httpx.get(f"{self.base_url}/models", headers=self._headers(), timeout=15)
        except Exception as exc:
            raise LlmError(f"шлюз недоступен: {exc}") from exc
        if r.status_code != 200:
            raise LlmError(f"шлюз вернул {r.status_code}: {self._safe(r.text)}")
        payload = r.json()
        items = payload.get("data", payload if isinstance(payload, list) else [])
        return [item.get("id", "") for item in items if isinstance(item, dict)]

    # ------------------------------------------------------------- генерация
    def generate_text(self, prompt: str, system: Optional[str] = None, *,
                      model: Optional[str] = None, temperature: float = 0.3,
                      json_mode: bool = True, format_schema: Optional[dict] = None,
                      images: Optional[list[str]] = None) -> str:
        """Запрос к /chat/completions. images — base64 PNG для VLM-моделей."""
        if not self.base_url:
            raise LlmError("не задан OPENAI_COMPAT_BASE_URL")
        s = get_settings()
        content: object = prompt
        if images:
            content = [{"type": "text", "text": prompt}]
            content += [{"type": "image_url",
                         "image_url": {"url": f"data:image/png;base64,{image}"}}
                        for image in images]
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": content})
        payload: dict = {
            "model": model or s.active_llm_model,
            "messages": messages,
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        response = self._post(payload)
        if response.status_code >= 400 and json_mode:
            # часть шлюзов не поддерживает response_format — повторяем без него
            payload.pop("response_format", None)
            response = self._post(payload)
        if response.status_code != 200:
            raise LlmError(f"шлюз вернул {response.status_code}: {self._safe(response.text)}")
        try:
            data = response.json()
            return data["choices"][0]["message"]["content"] or ""
        except Exception as exc:  # noqa: BLE001
            raise LlmError(f"неожиданный ответ шлюза: {self._safe(response.text)}") from exc

    def _post(self, payload: dict) -> httpx.Response:
        try:
            return httpx.post(f"{self.base_url}/chat/completions", json=payload,
                              headers=self._headers(), timeout=self.timeout)
        except Exception as exc:
            raise LlmError(f"ошибка вызова шлюза: {exc}") from exc

    def generate_json(self, prompt: str, system: Optional[str] = None, *,
                      model: Optional[str] = None) -> Optional[dict]:
        try:
            text = self.generate_text(prompt, system, model=model)
        except LlmError:
            return None
        return self._parse_json(text)

    # ------------------------------------------------------------ эмбеддинги
    def embed(self, texts: list[str], model: Optional[str] = None) -> list[list[float]]:
        """Эмбеддинги через /embeddings; при неудаче — пустой список (не критично)."""
        if not self.base_url:
            return []
        s = get_settings()
        try:
            r = httpx.post(f"{self.base_url}/embeddings",
                           json={"model": model or s.active_embedding_model, "input": texts},
                           headers=self._headers(), timeout=httpx.Timeout(120))
            r.raise_for_status()
            data = r.json().get("data", [])
            return [item.get("embedding", []) for item in data]
        except Exception:
            return []

    _parse_json = staticmethod(OllamaClient._parse_json)


def get_llm_client():
    """Клиент по конфигурации: Ollama (по умолчанию) или внешний шлюз."""
    settings = get_settings()
    if settings.uses_external_provider:
        return OpenAICompatClient()
    return OllamaClient()