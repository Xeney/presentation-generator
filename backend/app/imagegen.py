"""Генерация иллюстраций для блоков `kind=image` с полем `image_prompt`.

Провайдер — OpenAI-совместимый эндпоинт `/images/generations`. По умолчанию
стадия выключена (`IMAGE_PROVIDER=off`): пайплайн полностью работает без неё,
слот картинки остаётся плейсхолдером шаблона (аудит честно помечает
`image_missing`).

Лицензии моделей проверены по карточкам первоисточников (не по блогам):
- `flux.2-klein-4b` — Apache 2.0 (huggingface.co/black-forest-labs/FLUX.2-klein-4B:
  «Fully open under Apache 2.0», «Open weights available for commercial use»);
- `qwen-image` — Apache 2.0 (huggingface.co/Qwen/Qwen-Image: «licensed under
  Apache 2.0»).
SDXL/SD3 (CreativeML OpenRAIL / Stability) под требование open weights не
подходят и не используются.
"""
from __future__ import annotations

import base64
import logging
from typing import Optional

import httpx

from .config import get_settings

log = logging.getLogger("imagegen")


class ImageGenError(RuntimeError):
    pass


class ImageGenerator:
    """Клиент генерации изображений: `available()` → `generate()`."""

    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None,
                 model: Optional[str] = None, timeout_s: Optional[int] = None):
        s = get_settings()
        self.base_url = (base_url or s.active_image_base_url or "").rstrip("/")
        self.api_key = api_key if api_key is not None else s.active_image_api_key
        self.model = model or s.image_model
        self.timeout = httpx.Timeout(timeout_s or s.image_timeout_s)

    def available(self) -> bool:
        provider = get_settings().active_image_provider
        return provider != "off" and bool(self.base_url)

    def unavailable_reason(self) -> str:
        s = get_settings()
        if s.active_image_provider == "off":
            return "генерация изображений выключена (IMAGE_PROVIDER=off)"
        if not self.base_url:
            return "не задан адрес эндпоинта генерации (IMAGE_BASE_URL)"
        return "провайдер генерации недоступен"

    def generate(self, prompt: str, *, size: str = "") -> Optional[bytes]:
        """Одна картинка по промпту. None — если провайдер не ответил картинкой."""
        if not prompt.strip():
            return None
        if not self.available():
            return None
        s = get_settings()
        payload = {
            "model": self.model,
            "prompt": prompt.strip()[:900],
            "n": 1,
            "size": size or s.image_size,
            "response_format": "b64_json",
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            response = httpx.post(f"{self.base_url}/images/generations",
                                  json=payload, headers=headers, timeout=self.timeout)
        except httpx.TimeoutException as exc:
            raise ImageGenError(f"генерация не ответила за {self.timeout.read:g} c") from exc
        except Exception as exc:  # noqa: BLE001 — сеть может моргнуть
            raise ImageGenError(f"сеть недоступна: {exc}") from exc
        if response.status_code != 200:
            raise ImageGenError(
                f"провайдер вернул {response.status_code}: {response.text[:200]}")
        data = response.json().get("data") or []
        if not data:
            raise ImageGenError("в ответе нет изображений")
        first = data[0] if isinstance(data[0], dict) else {}
        if first.get("b64_json"):
            try:
                return base64.b64decode(first["b64_json"])
            except Exception as exc:  # noqa: BLE001
                raise ImageGenError(f"base64 не декодируется: {exc}") from exc
        url = first.get("url")
        if url:
            image = httpx.get(url, timeout=self.timeout)
            if image.status_code == 200 and image.content[:8].startswith(
                    (b"\x89PNG", b"\xff\xd8\xff")):
                return image.content
            raise ImageGenError(f"картинка по ссылке не получена ({image.status_code})")
        raise ImageGenError("неизвестный формат ответа провайдера")


def generate_images_for_deck(deck, images: dict | None,
                             generator: Optional[ImageGenerator] = None) -> tuple[dict, list[dict]]:
    """Генерирует картинки для блоков kind=image с `image_prompt`.

    Возвращает (реестр изображений, отчёт по блокам). Никогда не бросает:
    недоступность стадии — не ошибка задания, слот останется плейсхолдером.
    """
    settings = get_settings()
    registry = dict(images or {})
    report: list[dict] = []
    generator = generator or ImageGenerator()
    if not generator.available():
        return registry, report
    budget = max(0, settings.image_max_per_deck)
    made = 0
    for slide_index, slide in enumerate(deck.slides):
        for block_index, block in enumerate(slide.blocks):
            if block.kind != "image":
                continue
            key = f"gen_s{slide_index + 1}_{block_index + 1}.png"
            if block.image_ref and block.image_ref in registry:
                continue  # картинка из контент-пакета уже есть
            prompt = (block.image_prompt or "").strip()
            if not prompt:
                # промпта нет — оставляем слот шаблона как плейсхолдер
                continue
            if made >= budget:
                report.append({"slide": slide_index, "status": "skipped",
                               "reason": f"бюджет картинок исчерпан ({budget})"})
                continue
            try:
                payload = generator.generate(prompt, size=settings.image_size)
            except ImageGenError as exc:
                report.append({"slide": slide_index, "status": "failed",
                               "reason": str(exc)[:200]})
                log.warning("генерация картинки не удалась: %s", exc)
                continue
            if not payload:
                report.append({"slide": slide_index, "status": "failed",
                               "reason": "провайдер не вернул изображение"})
                continue
            registry[key] = payload
            block.image_ref = key
            block.image_prompt = None
            made += 1
            report.append({"slide": slide_index, "status": "generated",
                           "key": key, "bytes": len(payload)})
    return registry, report
