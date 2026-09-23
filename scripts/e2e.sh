#!/usr/bin/env bash
# Сквозной прогон: тесты → фикстуры → рендер 3 вариантов → детерминированный аудит → замер бюджета.
#
# Использование:
#   scripts/e2e.sh              # фикстуры + все шаблоны из data/templates/
#   scripts/e2e.sh path.pptx    # конкретный шаблон (в том числе незнакомый)
#
# Бюджет ТЗ: колода 10-15 слайдов генерируется не более 300 секунд (ADR-012).
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-python}"
BUDGET_S="${BUDGET_S:-300}"
fail=0

echo "=============================================================="
echo " 1/4  Модульные тесты"
echo "=============================================================="
"$PY" -m pytest backend/tests -q || fail=1

echo
echo "=============================================================="
echo " 2/4  Синтетические фикстуры (незнакомый шаблон)"
echo "=============================================================="
if [[ -f tests/fixtures/make_fixtures.py ]]; then
  "$PY" tests/fixtures/make_fixtures.py || fail=1
else
  echo "  (генератор фикстур появится на этапе 7)"
fi

echo
echo "=============================================================="
echo " 3/4  E2E-рендер: 3 варианта вёрстки + аудит"
echo "=============================================================="
if [[ $# -ge 1 ]]; then
  TARGETS=("$@")
else
  TARGETS=()
  while IFS= read -r -d '' f; do TARGETS+=("$f"); done < <(
    find data/templates tests/fixtures -maxdepth 1 -type f -name '*.pptx' -print0 2>/dev/null)
fi

if [[ ${#TARGETS[@]} -eq 0 ]]; then
  echo "  Нет шаблонов: положите PPTX в data/templates/ или передайте путь аргументом."
else
  # чистим прошлые артефакты: иначе аудит проверит устаревшие колоды,
  # собранные предыдущей версией кода и без профиля рядом
  rm -f data/e2e_out/*.pptx data/e2e_out/*.profile.json
  start=$(date +%s)
  "$PY" tools/e2e_render.py "${TARGETS[@]}" --json | tee data/e2e_report.json || fail=1
  elapsed=$(( $(date +%s) - start ))
  echo
  echo "  Время прогона: ${elapsed}s (бюджет ${BUDGET_S}s на колоду)"
  if [[ $elapsed -gt $BUDGET_S ]]; then
    echo "  ВНИМАНИЕ: бюджет превышен"
    fail=1
  fi
fi

echo
echo "=============================================================="
echo " 4/4  Аудит сгенерированных файлов"
echo "=============================================================="
shopt -s nullglob
decks=(data/e2e_out/*.pptx)
if [[ ${#decks[@]} -gt 0 ]]; then
  if ! "$PY" tools/audit_check.py "${decks[@]}"; then
    echo "  АУДИТ НАШЁЛ ОШИБКИ — см. вывод выше"
    fail=1
  fi
else
  echo "  Нет сгенерированных колод."
fi

echo
if [[ $fail -eq 0 ]]; then
  echo "ИТОГ: OK"
else
  echo "ИТОГ: есть замечания (см. выше)"
fi
exit $fail
