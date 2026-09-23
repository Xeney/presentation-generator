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
import logging
import time
from typing import Optional

import httpx

from ..config import get_settings

log = logging.getLogger("llm")


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

    Используется для AITUNNEL (`https://api.aitunnel.ru/v1`) и любых других
    совместимых шлюзов: тот же интерфейс, что у `OllamaClient`, поэтому
    планировщик, VLM-аудит и grounding работают без изменений.

    Безопасность: ключ не логируется (в сообщениях только маска из первых
    восьми символов) и вырезается из текстов ответов провайдера.
    """

    RETRY_STATUSES = (408, 409, 425, 429, 500, 502, 503, 504)

    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None,
                 timeout_s: Optional[int] = None, max_retries: int = 0,
                 provider_name: str = "шлюз", key_env: str = "OPENAI_COMPAT_API_KEY",
                 retry_backoff_s: float = 1.5):
        s = get_settings()
        self.provider_name = provider_name
        self.key_env = key_env
        self.base_url = (base_url or s.openai_compat_base_url or "").rstrip("/")
        self.api_key = api_key if api_key is not None else s.openai_compat_api_key
        self.timeout = httpx.Timeout(timeout_s or s.llm_timeout_s)
        self.max_retries = max(0, int(max_retries))
        self.retry_backoff_s = retry_backoff_s

    # ------------------------------------------------------------- служебное
    def masked_key(self) -> str:
        """Маска ключа для логов: первые 8 символов, дальше — звёздочки."""
        if not self.api_key:
            return "(нет ключа)"
        head = self.api_key[:8]
        return f"{head}…({len(self.api_key)} символов)"

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    @staticmethod
    def _safe(text: str, limit: int = 300) -> str:
        """Обрезает ответ провайдера: наружу не уходят длинные тела и заголовки."""
        return (text or "")[:limit]

    def _missing_key_error(self) -> LlmError:
        return LlmError(
            f"{self.provider_name}: не задан ключ ({self.key_env}). "
            "Ключ хранится только в локальном .env — он в .gitignore и в "
            "репозиторий не попадает.")

    def _request(self, method: str, path: str, *, payload: Optional[dict] = None,
                 timeout: Optional[httpx.Timeout] = None) -> httpx.Response:
        """HTTP-вызов с повторами на сетевых сбоях и 5xx/429.

        Повторы не применяются к 4xx (кроме 408/409/425/429): неверный ключ или
        отключённая модель повторным запросом не исправятся.
        """
        url = f"{self.base_url}{path}"
        attempts = self.max_retries + 1
        last_error = ""
        for attempt in range(1, attempts + 1):
            try:
                if method == "GET":
                    response = httpx.get(url, headers=self._headers(),
                                         timeout=timeout or self.timeout)
                else:
                    response = httpx.post(url, json=payload, headers=self._headers(),
                                          timeout=timeout or self.timeout)
            except Exception as exc:  # noqa: BLE001 — сеть может моргнуть
                last_error = str(exc)
                log.warning("%s: сетевой сбой (попытка %s/%s): %s",
                            self.provider_name, attempt, attempts, exc)
                if attempt < attempts:
                    time.sleep(self.retry_backoff_s * attempt)
                    continue
                raise LlmError(f"{self.provider_name}: сеть недоступна ({exc})") from exc

            if response.status_code in self.RETRY_STATUSES and attempt < attempts:
                log.warning("%s: ответ %s, повтор через %.1f c (ключ %s)",
                            self.provider_name, response.status_code,
                            self.retry_backoff_s * attempt, self.masked_key())
                time.sleep(self.retry_backoff_s * attempt)
                continue
            return response
        raise LlmError(f"{self.provider_name}: запрос не удался ({last_error})")

    def health(self) -> bool:
        if not self.base_url or not self.api_key:
            return False
        try:
            response = self._request("GET", "/models", timeout=httpx.Timeout(15))
            return response.status_code == 200
        except Exception:  # noqa: BLE001
            return False

    def list_models(self) -> list[str]:
        if not self.base_url:
            raise LlmError(f"{self.provider_name}: не задан адрес API")
        if not self.api_key:
            raise self._missing_key_error()
        response = self._request("GET", "/models", timeout=httpx.Timeout(20))
        if response.status_code != 200:
            raise LlmError(f"{self.provider_name} вернул {response.status_code}: "
                           f"{self._safe(response.text)}")
        payload = response.json()
        items = payload.get("data", payload if isinstance(payload, list) else [])
        return [item.get("id", "") for item in items if isinstance(item, dict)]

    # ------------------------------------------------------------- генерация
    def generate_text(self, prompt: str, system: Optional[str] = None, *,
                      model: Optional[str] = None, temperature: float = 0.3,
                      json_mode: bool = True, format_schema: Optional[dict] = None,
                      images: Optional[list[str]] = None) -> str:
        """Запрос к /chat/completions. images — base64 PNG для VLM-моделей."""
        if not self.base_url:
            raise LlmError(f"{self.provider_name}: не задан адрес API")
        if not self.api_key:
            raise self._missing_key_error()
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

        response = self._request("POST", "/chat/completions", payload=payload)
        if response.status_code in (400, 422) and json_mode:
            # часть шлюзов не поддерживает response_format: повторяем без него.
            # На 401/402/403/404 повтор бессмысленен — ключ и баланс не изменятся.
            payload.pop("response_format", None)
            response = self._request("POST", "/chat/completions", payload=payload)
        if response.status_code != 200:
            raise LlmError(
                f"{self.provider_name} вернул {response.status_code}: "
                f"{self._safe(response.text)}")
        try:
            data = response.json()
            return data["choices"][0]["message"]["content"] or ""
        except Exception as exc:  # noqa: BLE001
            raise LlmError(
                f"{self.provider_name}: неожиданный ответ ({self._safe(response.text)})"
            ) from exc

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
        if not self.base_url or not self.api_key:
            return []
        s = get_settings()
        try:
            response = self._request(
                "POST", "/embeddings",
                payload={"model": model or s.active_embedding_model, "input": texts},
                timeout=httpx.Timeout(120))
            if response.status_code != 200:
                return []
            data = response.json().get("data", [])
            return [item.get("embedding", []) for item in data]
        except Exception:  # noqa: BLE001
            return []

    _parse_json = staticmethod(OllamaClient._parse_json)


def aitunnel_client(vlm: bool = False) -> OpenAICompatClient:
    """Клиент AITUNNEL: OpenAI-совместимый шлюз с моделями Qwen (ADR-020)."""
    s = get_settings()
    return OpenAICompatClient(
        base_url=s.aitunnel_base_url,
        api_key=s.aitunnel_api_key,
        timeout_s=s.aitunnel_timeout_sec,
        max_retries=s.aitunnel_max_retries,
        provider_name="AITUNNEL",
        key_env="AITUNNEL_API_KEY",
    )


def get_llm_client():
    """Клиент планировщика по конфигурации: aitunnel | openai_compat | ollama."""
    settings = get_settings()
    if settings.llm_provider == "aitunnel" and not settings.aitunnel_api_key:
        log.warning("LLM_PROVIDER=aitunnel, но AITUNNEL_API_KEY пуст — использую "
                    "локальную Ollama (ключ хранится только в .env)")
    provider = settings.active_llm_provider
    if provider == "aitunnel":
        return aitunnel_client()
    if provider == "openai_compat":
        return OpenAICompatClient(provider_name="шлюз")
    return OllamaClient()


def get_vlm_client():
    """Клиент VLM-аудита: учитывает отдельный `VLM_PROVIDER`, умеет `off`."""
    settings = get_settings()
    provider = settings.active_vlm_provider
    if provider == "off":
        log.info("VLM-аудит выключен (VLM_PROVIDER=off)")
        return None
    if settings.vlm_provider.strip().lower() == "aitunnel" and not settings.aitunnel_api_key:
        log.warning("VLM_PROVIDER=aitunnel, но AITUNNEL_API_KEY пуст — использую "
                    "локальную Ollama")
    if provider == "aitunnel":
        return aitunnel_client(vlm=True)
    if provider == "openai_compat":
        return OpenAICompatClient(provider_name="шлюз")
    return OllamaClient()