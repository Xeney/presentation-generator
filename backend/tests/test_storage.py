"""Тесты хранилища: задания переживают «перезапуск», кэш миниатюр работает по хэшу."""
from __future__ import annotations

import json
import time
from pathlib import Path

from app.storage import JobStore, ThumbCache


def _job(job_id: str = "abc123", *, created: float | None = None) -> dict:
    return {
        "id": job_id,
        "status": "done",
        "created": created if created is not None else time.time(),
        "template": b"PK\x03\x04template",
        "template_name": "tpl.pptx",
        "corpus_id": None,
        "elapsed_s": 12.5,
        "result": {
            "profile": {"layouts": []},
            "deck": {"title": "Колода", "slides": []},
            "variants": [
                {"name": "compact", "pptx": b"PK\x03\x04compact",
                 "audit": {"passed": True, "errors": 0, "warnings": 0, "issues": []}},
                {"name": "cards", "pptx": b"PK\x03\x04cards", "audit": {}},
                {"name": "split", "pptx": b"PK\x03\x04split", "audit": {}},
            ],
            "html": "<!DOCTYPE html>",
        },
    }


def test_job_survives_restart(tmp_path: Path):
    store = JobStore(tmp_path, ttl_hours=12)
    store.put(_job())

    # новый экземпляр = перезапуск сервиса: данные читаются с диска
    restarted = JobStore(tmp_path, ttl_hours=12)
    loaded = restarted.get("abc123")
    assert loaded is not None
    assert loaded["template"] == b"PK\x03\x04template"
    assert loaded["elapsed_s"] == 12.5
    variants = {item["name"]: item["pptx"] for item in loaded["result"]["variants"]}
    assert variants == {"compact": b"PK\x03\x04compact",
                        "cards": b"PK\x03\x04cards",
                        "split": b"PK\x03\x04split"}
    assert loaded["result"]["deck"]["title"] == "Колода"

    # в метаданных нет ни байтов PPTX, ни самих файлов — только флаги и имена
    meta_text = (tmp_path / "jobs" / "abc123" / "job.json").read_text(encoding="utf-8")
    meta = json.loads(meta_text)
    assert "PK\x03\x04" not in meta_text
    assert meta["has_template"] is True
    assert "template" not in meta
    for variant in meta["result"]["variants"]:
        assert set(variant) == {"name", "audit"}
    assert (tmp_path / "jobs" / "abc123" / "compact.pptx").read_bytes() == b"PK\x03\x04compact"


def test_missing_job_returns_none(tmp_path: Path):
    assert JobStore(tmp_path).get("нет-такого") is None


def test_cleanup_removes_expired_jobs(tmp_path: Path):
    store = JobStore(tmp_path, ttl_hours=1)
    store.put(_job("fresh"))
    store.put(_job("stale", created=time.time() - 7200))

    removed = store.cleanup(force=True)
    assert removed == 1
    assert store.get("fresh") is not None
    assert store.get("stale") is None
    assert not (tmp_path / "jobs" / "stale").exists()


def test_thumb_cache_roundtrip_and_hash_invalidation(tmp_path: Path):
    cache = ThumbCache(tmp_path, ttl_hours=12)
    first = cache.digest(b"PK\x03\x04one")
    second = cache.digest(b"PK\x03\x04two")
    assert first != second

    assert cache.get(first, 0, False) is None
    cache.put(first, 0, False, b"png-plain")
    cache.put(first, 0, True, b"png-boxed")
    assert cache.get(first, 0, False) == b"png-plain"
    assert cache.get(first, 0, True) == b"png-boxed"
    # другой файл — другой ключ, старые миниатюры не подмешиваются
    assert cache.get(second, 0, False) is None


def test_thumb_prune_by_ttl(tmp_path: Path):
    cache = ThumbCache(tmp_path, ttl_hours=1)
    digest = cache.digest(b"PK\x03\x04old")
    cache.put(digest, 0, False, b"png")
    old = time.time() - 7200
    for path in (tmp_path / "cache" / "thumbs" / digest).iterdir():
        import os

        os.utime(path, (old, old))
    assert cache.prune(force=True) == 1
    assert cache.get(digest, 0, False) is None
