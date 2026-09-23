#!/usr/bin/env bash
# Кладёт PPTX-шаблоны и контент-пакет в data/templates/ и пересобирает JSON-профили.
#
# Использование:
#   scripts/fetch_assets.sh /путь/к/папке/с/датасетом
#   scripts/fetch_assets.sh            # только пересобрать профили из того, что уже есть
#
# Бинарные шаблоны не хранятся в git (docs/DECISIONS.md ADR-002).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$ROOT/data/templates"
mkdir -p "$DEST"

if [[ $# -ge 1 ]]; then
  SRC="$1"
  if [[ ! -d "$SRC" ]]; then
    echo "Нет такой папки: $SRC" >&2
    exit 1
  fi
  count=0
  while IFS= read -r -d '' file; do
    cp -f "$file" "$DEST/"
    echo "  + $(basename "$file")"
    count=$((count + 1))
  done < <(find "$SRC" -maxdepth 2 -type f \( -iname '*.pptx' -o -iname '*.docx' \) -print0)
  echo "Скопировано файлов: $count"
fi

echo
echo "Содержимое $DEST:"
ls -1 "$DEST" 2>/dev/null | grep -v '^\.gitkeep$' || echo "  (пусто)"

echo
echo "Пересборка профилей..."
python "$ROOT/tools/make_profiles.py"
