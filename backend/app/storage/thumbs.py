"""Кэш миниатюр слайдов: ключ — хэш PPTX + номер слайда + рамки проблем.

Миниатюры получаются дорого (LibreOffice → PDF → PNG), а UI запрашивает их
десятками, поэтому результат кладётся на диск в ``data/cache/thumbs/{hash}/``.
Хэш содержимого PPTX автоматически инвалидирует кэш после авто-фиксов: файл
меняется — меняется ключ. TTL совпадает с TTL заданий (ADR-016).
"""
from __future__ import annotations

import hashlib
import logging
import shutil
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("storage.thumbs")


class ThumbCache:
    def __init__(self, data_dir: Path, ttl_hours: float = 12.0):
        self.root = Path(data_dir) / "cache" / "thumbs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = max(60.0, ttl_hours * 3600)
        self._last_cleanup = 0.0

    @staticmethod
    def digest(pptx_bytes: bytes) -> str:
        return hashlib.sha256(pptx_bytes).hexdigest()[:20]

    def _path(self, digest: str, page: int, boxes: bool) -> Path:
        return self.root / digest / f"{page}_{int(bool(boxes))}.png"

    def get(self, digest: str, page: int, boxes: bool) -> Optional[bytes]:
        path = self._path(digest, page, boxes)
        if path.exists():
            try:
                return path.read_bytes()
            except OSError:  # noqa: PERF203 — файл мог исчезнуть при очистке
                return None
        return None

    def put(self, digest: str, page: int, boxes: bool, png: bytes) -> None:
        path = self._path(digest, page, boxes)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(png)
        except OSError as exc:  # noqa: BLE001 — кэш не обязателен для работы
            log.warning("не удалось сохранить миниатюру: %s", exc)

    def prune(self, force: bool = False) -> int:
        """Удаляет каталоги кэша старше TTL. Возвращает число удалённых."""
        now = time.time()
        if not force and now - self._last_cleanup < 600:
            return 0
        self._last_cleanup = now
        removed = 0
        for directory in self.root.iterdir():
            if not directory.is_dir():
                continue
            try:
                newest = max((file.stat().st_mtime for file in directory.iterdir()),
                             default=directory.stat().st_mtime)
            except OSError:
                continue
            if now - newest > self.ttl_seconds:
                shutil.rmtree(directory, ignore_errors=True)
                removed += 1
        if removed:
            log.info("очистка кэша миниатюр: удалено %s каталогов", removed)
        return removed
