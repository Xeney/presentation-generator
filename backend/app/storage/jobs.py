"""Хранилище заданий на диске: результаты переживают перезапуск сервиса (ADR-016).

Схема: ``data/jobs/{id}/job.json`` — метаданные (профиль, колода, аудиты, стадии),
``template.pptx`` и ``{variant}.pptx`` — бинарные артефакты рядом. В памяти
держится LRU-кэш горячих заданий, чтобы UI не читал диск на каждый запрос.

Байты PPTX в JSON не попадают: они восстанавливаются из файлов при загрузке.
"""
from __future__ import annotations

import json
import logging
import shutil
import time
from collections import OrderedDict
from pathlib import Path
from typing import Optional

log = logging.getLogger("storage.jobs")

VARIANTS = ("compact", "cards", "split")
CACHE_LIMIT = 8


class JobStore:
    """Файловое хранилище заданий с кэшем в памяти и очисткой по TTL."""

    def __init__(self, data_dir: Path, ttl_hours: float = 12.0, cache_limit: int = CACHE_LIMIT):
        self.root = Path(data_dir) / "jobs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = max(60.0, ttl_hours * 3600)
        self.cache_limit = cache_limit
        self._cache: "OrderedDict[str, dict]" = OrderedDict()
        self._last_cleanup = 0.0

    # ------------------------------------------------------------------- api
    def put(self, job: dict) -> None:
        """Сохраняет задание в память и на диск."""
        self._cache[job["id"]] = job
        self._cache.move_to_end(job["id"])
        while len(self._cache) > self.cache_limit:
            self._cache.popitem(last=False)
        try:
            self._flush(job)
        except Exception as exc:  # noqa: BLE001 — падение диска не должно ломать генерацию
            log.warning("не удалось сохранить задание %s: %s", job.get("id"), exc)

    def get(self, job_id: str) -> Optional[dict]:
        if job_id in self._cache:
            self._cache.move_to_end(job_id)
            return self._cache[job_id]
        job = self._load(job_id)
        if job is not None:
            self._cache[job_id] = job
        return job

    def ids(self) -> list[str]:
        return sorted(path.parent.name for path in self.root.glob("*/job.json"))

    def cleanup(self, force: bool = False) -> int:
        """Удаляет задания старше TTL. Возвращает число удалённых."""
        now = time.time()
        if not force and now - self._last_cleanup < 600:
            return 0
        self._last_cleanup = now
        removed = 0
        for meta_path in self.root.glob("*/job.json"):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                created = float(meta.get("created", 0.0))
            except Exception:  # noqa: BLE001 — битый файл тоже чистим
                created = 0.0
            if created and now - created < self.ttl_seconds:
                continue
            shutil.rmtree(meta_path.parent, ignore_errors=True)
            self._cache.pop(meta_path.parent.name, None)
            removed += 1
        if removed:
            log.info("очистка заданий: удалено %s", removed)
        return removed

    # --------------------------------------------------------------- диск
    def _job_dir(self, job_id: str) -> Path:
        return self.root / job_id

    def _flush(self, job: dict) -> None:
        directory = self._job_dir(job["id"])
        directory.mkdir(parents=True, exist_ok=True)
        meta: dict = {}
        for key, value in job.items():
            if key == "template":
                if value:
                    (directory / "template.pptx").write_bytes(value)
                meta["has_template"] = bool(value)
            elif key == "result":
                meta["result"] = self._result_to_meta(value, directory)
            else:
                meta[key] = value
        tmp = directory / "job.json.tmp"
        tmp.write_text(json.dumps(meta, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(directory / "job.json")   # атомарная замена

    @staticmethod
    def _result_to_meta(result: dict, directory: Path) -> dict:
        meta = {key: value for key, value in result.items() if key != "variants"}
        variants = []
        for variant in result.get("variants", []):
            name = variant.get("name", "variant")
            pptx = variant.get("pptx")
            if pptx:
                (directory / f"{name}.pptx").write_bytes(pptx)
            variants.append({key: value for key, value in variant.items() if key != "pptx"})
        meta["variants"] = variants
        return meta

    def _load(self, job_id: str) -> Optional[dict]:
        directory = self._job_dir(job_id)
        meta_path = directory / "job.json"
        if not meta_path.exists():
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            log.warning("задание %s повреждено: %s", job_id, exc)
            return None
        job = dict(meta)
        if meta.get("has_template"):
            template = directory / "template.pptx"
            job["template"] = template.read_bytes() if template.exists() else None
        result = meta.get("result")
        if isinstance(result, dict):
            variants = []
            for variant in result.get("variants", []):
                item = dict(variant)
                pptx_path = directory / f"{item.get('name')}.pptx"
                item["pptx"] = pptx_path.read_bytes() if pptx_path.exists() else b""
                variants.append(item)
            result = dict(result)
            result["variants"] = variants
            job["result"] = result
        return job
