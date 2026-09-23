"""Пересчитывает хэши промптов и конфигов в prompts/registry.json.

Запускать после любой правки промптов: манифест должен соответствовать файлам,
иначе тест `test_prompts_registry_is_in_sync` падает, а в задании окажется
неверная версия артефакта (ADR-013).

Использование:
    python scripts/sync_prompts.py           # обновить хэши
    python scripts/sync_prompts.py --check   # только проверить (код возврата 1 при расхождении)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.prompts_meta import artifact_hash, load_registry  # noqa: E402

REGISTRY = ROOT / "prompts" / "registry.json"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Синхронизация манифеста промптов")
    parser.add_argument("--check", action="store_true",
                        help="не писать, только сообщить о расхождениях")
    args = parser.parse_args(argv)

    registry = load_registry(REGISTRY)
    changed = 0
    missing = 0
    for artifact in registry.get("artifacts", []):
        actual = artifact_hash(artifact)
        if not actual:
            print(f"  нет файла: {artifact['path']}")
            missing += 1
            continue
        if artifact.get("hash") != actual:
            print(f"  {artifact['id']}: {artifact.get('hash') or '—'} → {actual}")
            if not args.check:
                artifact["hash"] = actual
            changed += 1
        else:
            print(f"  {artifact['id']}: {actual} (без изменений)")

    if args.check:
        if changed or missing:
            print(f"\nРасхождения: {changed}, отсутствуют файлы: {missing}")
            return 1
        print("\nМанифест синхронен с файлами")
        return 0

    if changed:
        REGISTRY.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        print(f"\nОбновлено артефактов: {changed}")
    else:
        print("\nОбновлять нечего")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
