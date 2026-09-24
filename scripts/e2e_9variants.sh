#!/usr/bin/env bash
# 9 вариантов презентаций: 3 шаблона × 3 варианта вёрстки на одном контент-пакете.
# Требование ТЗ (стр. 5): результат демонстрации — 9 презентаций.
#
# Использование:
#   scripts/e2e_9variants.sh
#   scripts/e2e_9variants.sh --templates a.pptx b.pptx c.pptx --corpus pack.pptx
#   scripts/e2e_9variants.sh --brief "свой бриф" --out data/output/9variants
#
# Вход:  шаблоны (по умолчанию — все PPTX из data/templates, кроме контент-пакета)
#        и контент-пакет (по умолчанию «VK Tech шаблон.pptx», иначе текстовый).
# Выход: 9 PPTX в data/output/9variants/ + report.json с таблицей
#        «шаблон × вариант → время, число проблем аудита».
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-python}"

echo "=============================================================="
echo " 9 вариантов: 3 шаблона × 3 варианта вёрстки"
echo "=============================================================="

"$PY" tools/e2e_9variants.py "$@"
status=$?

echo
if [[ $status -eq 0 ]]; then
  echo "ИТОГ: 9 вариантов собраны, ошибок аудита нет"
else
  echo "ИТОГ: есть замечания (код $status) — смотрите таблицу выше и report.json"
fi
exit $status
