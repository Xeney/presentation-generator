"""Провайдер, заданный через интерфейс: ключ живёт только в памяти процесса.

Пользователь может подключить внешний OpenAI-совместимый сервис прямо в UI
(блок «Для опытных»), не правя .env и не перезапуская сервис. Ключ не пишется
на диск, не логируется, не возвращается в API-ответах и не попадает в job.json
(docs/DECISIONS.md, ADR-031). Сброс возвращает локальную Ollama.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field


def mask_key(key: str) -> str:
    """Маска ключа для интерфейса: «sk-...abc», как в ТЗ."""
    if not key:
        return ""
    if len(key) <= 8:
        return "•" * len(key)
    return f"{key[:3]}...{key[-3:]}"


@dataclass
class RuntimeProvider:
    """Текущий внешний провайдер (in-memory)."""

    source: str = "local"          # local | external
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    status: str = "untested"       # untested | ok | fail
    status_message: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def active(self) -> bool:
        return self.source == "external" and bool(self.base_url) and bool(self.api_key)

    def set(self, base_url: str, api_key: str, model: str,
            status: str = "untested", message: str = "") -> None:
        with self._lock:
            self.source = "external"
            self.base_url = (base_url or "").strip().rstrip("/")
            self.api_key = (api_key or "").strip()
            self.model = (model or "").strip()
            self.status = status
            self.status_message = message

    def mark(self, status: str, message: str = "") -> None:
        with self._lock:
            self.status = status
            self.status_message = message

    def reset(self) -> None:
        with self._lock:
            self.source = "local"
            self.base_url = ""
            self.api_key = ""
            self.model = ""
            self.status = "untested"
            self.status_message = ""

    def public(self) -> dict:
        """Данные для API: ключ только в маске, без значения."""
        return {
            "source": self.source,
            "base_url": self.base_url,
            "model": self.model,
            "masked_key": mask_key(self.api_key),
            "has_key": bool(self.api_key),
            "status": self.status,
            "status_message": self.status_message,
        }


RUNTIME = RuntimeProvider()
