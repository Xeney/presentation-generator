"""Генерация JSON-профилей дизайн-системы для всех PPTX-шаблонов проекта.

Ищет файлы в ``data/templates/`` и в корне репозитория, профили складывает в
``template_profiles/``. Профили — единственный артефакт разбора шаблона, который
хранится в git (сами PPTX исключены, см. docs/DECISIONS.md ADR-002).

Запуск:
    python tools/make_profiles.py                  # все найденные шаблоны
    python tools/make_profiles.py path/to/tpl.pptx # конкретный файл
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.template.parser import TemplateParser  # noqa: E402

OUT = ROOT / "template_profiles"
SEARCH_DIRS = (ROOT / "data" / "templates", ROOT)


def discover() -> list[Path]:
    found: dict[str, Path] = {}
    for directory in SEARCH_DIRS:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.pptx")):
            found.setdefault(path.name, path)
    return list(found.values())


def build(path: Path) -> bool:
    print(f"==> {path.name}")
    try:
        profile = TemplateParser(path).parse()
        slug = profile.template_id.replace(".pptx", "")
        OUT.mkdir(exist_ok=True)
        target = OUT / f"{slug}.json"
        target.write_text(
            json.dumps(profile.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        groups = {role: len(ids) for role, ids in profile.layout_groups.items()}
        print(f"    OK: {target.name}: макетов {len(profile.layouts)} {groups}, "
              f"палитра {len(profile.palette)}, шрифты {[f.name for f in profile.fonts][:4]}")
        return True
    except Exception as exc:  # noqa: BLE001 — CLI-скрипт, печатаем и продолжаем
        import traceback

        traceback.print_exc()
        print(f"    FAIL: {exc}")
        return False


def main(argv: list[str]) -> int:
    targets = [Path(a) for a in argv] or discover()
    if not targets:
        print("PPTX-шаблоны не найдены. Положите файлы в data/templates/ "
              "(см. docs/README.md §3) или передайте путь аргументом.")
        return 0
    ok = sum(1 for path in targets if path.exists() and build(path))
    print(f"\nГотово: {ok} из {len(targets)} профилей обновлено в {OUT}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
