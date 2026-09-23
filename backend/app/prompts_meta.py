"""Версионирование промптов и конфигов агентов (ADR-013).

Промпты лежат отдельными файлами в `prompts/`, а их версии и хэши — в
`prompts/registry.json`. Здесь манифест читается и проверяется: результат
каждого задания содержит версии и хэши использованных артефактов, поэтому по
сохранённой колоде всегда видно, какими инструкциями она сгенерирована.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "prompts" / "registry.json"


class RegistryError(RuntimeError):
    pass


def load_registry(path: Optional[Path] = None) -> dict:
    registry_path = Path(path) if path else REGISTRY_PATH
    if not registry_path.exists():
        raise RegistryError(f"нет манифеста промптов: {registry_path}")
    return json.loads(registry_path.read_text(encoding="utf-8"))


def artifact_hash(artifact: dict, root: Optional[Path] = None) -> str:
    base = Path(root) if root else ROOT
    target = base / "prompts" / artifact["path"]
    if not target.exists():
        return ""
    return hashlib.sha256(target.read_bytes()).hexdigest()[:16]


def versions(ids: Optional[list[str]] = None,
             path: Optional[Path] = None) -> dict[str, dict]:
    """Версии артефактов (по умолчанию все) для записи в результат задания."""
    registry = load_registry(path)
    wanted = set(ids) if ids else None
    out: dict[str, dict] = {}
    for artifact in registry.get("artifacts", []):
        if wanted is not None and artifact["id"] not in wanted:
            continue
        out[artifact["id"]] = {
            "version": artifact.get("version", ""),
            "hash": artifact.get("hash") or artifact_hash(artifact),
            "model": artifact.get("model", ""),
            "kind": artifact.get("kind", "prompt"),
        }
    return out


def verify(path: Optional[Path] = None, root: Optional[Path] = None) -> list[str]:
    """Расхождения между манифестом и файлами (для CI и теста)."""
    registry = load_registry(path)
    problems: list[str] = []
    for artifact in registry.get("artifacts", []):
        expected = artifact.get("hash")
        actual = artifact_hash(artifact, root)
        if not actual:
            problems.append(f"{artifact['id']}: файл {artifact['path']} не найден")
        elif expected and expected != actual:
            problems.append(
                f"{artifact['id']}: хэш изменился (манифест {expected}, файл {actual}) — "
                "запустите scripts/sync_prompts.py")
    return problems
