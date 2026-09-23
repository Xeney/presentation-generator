"""CLI-импорт контент-пакета: PPTX/DOCX/TXT → структурированный корпус.

Показывает декомпозицию (слайд → заголовок, тезисы, таблицы, цифры, картинки),
сохраняет корпус в data/corpora/{id}/ и может выгрузить текст для промпта.

Запуск:
    python tools/import_corpus.py "data/templates/VK Tech шаблон.pptx"
    python tools/import_corpus.py pack.pptx --text          # текст для планировщика
    python tools/import_corpus.py pack.pptx --json          # структура целиком
    python tools/import_corpus.py pack.pptx --dump docs/dev/content_corpus.txt
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from app.content.corpus import list_corpora  # noqa: E402
from app.content.importer import import_content_pack  # noqa: E402


def _rel(path: Path) -> str:
    """Путь относительно корня репозитория (или как есть, если он вне него)."""
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except Exception:  # noqa: BLE001
        return str(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Импорт контент-пакета")
    parser.add_argument("files", nargs="*", help="файлы PPTX/DOCX/TXT/MD")
    parser.add_argument("--data", type=Path, default=ROOT / "data")
    parser.add_argument("--json", action="store_true", help="печать структуры корпуса")
    parser.add_argument("--text", action="store_true", help="печать текста для промпта")
    parser.add_argument("--dump", type=Path, default=None, help="куда выгрузить текст")
    parser.add_argument("--list", action="store_true", help="список сохранённых корпусов")
    args = parser.parse_args()

    if args.list:
        for item in list_corpora(args.data):
            stats = item.get("stats", {})
            print(f"{item['id']}  {item['source_file']}  "
                  f"слайдов {stats.get('non_empty', 0)}, картинок {stats.get('images', 0)}, "
                  f"цифр {stats.get('numbers', 0)}")
        return 0

    files = [Path(f) for f in args.files]
    if not files:
        candidates = sorted((ROOT / "data" / "templates").glob("*.pptx"))
        if not candidates:
            print("укажите файл контент-пакета")
            return 1
        files = candidates[:1]

    failed = 0
    for path in files:
        if not path.exists():
            print(f"нет файла: {path}")
            failed += 1
            continue
        try:
            corpus = import_content_pack(path.read_bytes(), path.name)
        except Exception as exc:  # noqa: BLE001
            print(f"{path.name}: ОШИБКА {type(exc).__name__}: {exc}")
            failed += 1
            continue
        root = corpus.save(args.data)
        stats = corpus.to_dict()["stats"]
        print(f"{path.name}: корпус {corpus.id} → {_rel(root)}")
        print(f"  слайдов {stats['slides']} (непустых {stats['non_empty']}), "
              f"изображений {stats['images']}, цифр {stats['numbers']}")
        for slide in corpus.non_empty_slides()[:8]:
            parts = [f"«{slide.heading[:50]}»" if slide.heading else "(без заголовка)"]
            if slide.bullets:
                parts.append(f"буллетов {len(slide.bullets)}")
            if slide.tables:
                parts.append(f"таблиц {len(slide.tables)}")
            if slide.charts:
                parts.append(f"диаграмм {len(slide.charts)}")
            if slide.images:
                parts.append(f"картинок {len(slide.images)}")
            if slide.numbers:
                parts.append(f"цифры: {', '.join(slide.numbers[:3])}")
            print("   - " + " | ".join(parts))
        if len(corpus.non_empty_slides()) > 8:
            print(f"   … ещё {len(corpus.non_empty_slides()) - 8} слайдов")

        if args.text:
            print("\n--- текст для промпта ---")
            print(corpus.text())
        if args.json:
            print("\n--- структура ---")
            print(json.dumps(corpus.to_dict(), ensure_ascii=False, indent=2)[:8000])
        if args.dump:
            args.dump.parent.mkdir(parents=True, exist_ok=True)
            args.dump.write_text(corpus.text(max_chars=100000), encoding="utf-8")
            print(f"текст выгружен: {_rel(args.dump)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
