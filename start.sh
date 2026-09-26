#!/usr/bin/env bash
# Запуск «Цифрового дизайнера презентаций».
#
#   ./start.sh                     # docker compose up --build (рекомендуется)
#   ./start.sh --no-build          # поднять уже собранные образы
#   ./start.sh --local             # без Docker: uvicorn + next dev (нужны Python и Node)
#   ./start.sh --with-assets DIR   # скопировать шаблоны/контент-пакет из папки DIR
#   ./start.sh --logs              # после старта показать логи всех сервисов
#   ./start.sh -h                  # справка
#
# Скрипт идемпотентен: .env создаётся из .env.example, каталоги data/ — при
# необходимости, повторный запуск просто перезапускает сервисы.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

MODE="docker"
BUILD=1
SHOW_LOGS=0
ASSETS_DIR=""
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-600}"

# --------------------------------------------------------------------- утилиты
bold() { printf '\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
fail() { printf '\n\033[31mОшибка:\033[0m %s\n' "$*" >&2; exit 1; }

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "$2"
}

usage() {
  sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-build) BUILD=0 ;;
    --local) MODE="local" ;;
    --logs) SHOW_LOGS=1 ;;
    --with-assets)
      [[ $# -ge 2 ]] || fail "после --with-assets нужен путь к папке с шаблонами"
      ASSETS_DIR="$2"; shift ;;
    -h|--help) usage ;;
    *) fail "неизвестный аргумент: $1 (см. ./start.sh -h)" ;;
  esac
  shift
done

# ------------------------------------------------------------- подготовка
bold "Цифровой дизайнер презентаций — запуск ($MODE)"

if [[ ! -f .env ]]; then
  cp .env.example .env
  ok ".env создан из .env.example"
else
  ok ".env уже есть"
fi

mkdir -p data/templates data/jobs data/cache/thumbs data/corpora
ok "каталоги data/ готовы"

if [[ -n "$ASSETS_DIR" ]]; then
  [[ -d "$ASSETS_DIR" ]] || fail "нет папки с материалами: $ASSETS_DIR"
  bash scripts/fetch_assets.sh "$ASSETS_DIR"
fi

if [[ -z "$(ls -A data/templates 2>/dev/null | grep -v '^\.gitkeep$' || true)" ]]; then
  warn "в data/templates/ нет шаблонов — их можно загрузить прямо в интерфейсе"
fi

wait_for_http() {
  local url="$1" seconds="$2" name="$3" waited=0
  printf '  ждём %s' "$name"
  while (( waited < seconds )); do
    if curl -fsS -o /dev/null "$url" 2>/dev/null; then
      printf '\n'; ok "$name готов"
      return 0
    fi
    sleep 3; waited=$((waited + 3)); printf '.'
  done
  printf '\n'
  warn "$name не ответил за ${seconds}s — смотрите логи"
  return 1
}

print_ready() {
  echo
  bold "Готово"
  info "интерфейс:  http://localhost:${FRONTEND_PORT}"
  info "API:        http://localhost:${BACKEND_PORT}/docs"
  info "здоровье:   http://localhost:${BACKEND_PORT}/api/health"
  echo
  bold "Полезные команды"
  if [[ "$MODE" == "docker" ]]; then
    info "логи:       docker compose logs -f"
    info "остановка:  docker compose down"
    info "перезапуск: ./start.sh --no-build"
  else
    info "остановка:  Ctrl+C (оба процесса будут завершены)"
  fi
  info "тесты:      pytest"
  info "сквозной:   scripts/e2e.sh"
}

# ------------------------------------------------------------------ docker
start_docker() {
  need_cmd docker "Docker не найден. Установите Docker Desktop или запустите ./start.sh --local"
  docker compose version >/dev/null 2>&1 \
    || fail "нужен Docker Compose v2 (команда «docker compose»)"
  if ! docker info >/dev/null 2>&1; then
    fail "демон Docker не запущен: откройте Docker Desktop и повторите (или ./start.sh --local)"
  fi

  local args=(up -d)
  (( BUILD )) && args+=(--build)
  info "docker compose ${args[*]}"
  if (( BUILD )); then
    # Docker Compose v5 (Docker Desktop 29.2.x) падает на сборке bake-сессией:
    # failed to dial gRPC: header key "x-docker-expose-session-sharedkey" ...
    # Это баг Compose, не проекта: обходим его сборкой образов напрямую
    # (docker build) и запуском уже собранных (docker compose up --no-build).
    local err_log
    err_log="$(mktemp)"
    if ! docker compose "${args[@]}" 2>"$err_log"; then
      if grep -qi "x-docker-expose-session-sharedkey" "$err_log"; then
        warn "Compose v5 упал на bake-сессии — собираю образы напрямую (обход бага)"
        docker build -f backend/Dockerfile -t digital-designer-backend . \
          || fail "не удалось собрать backend-образ"
        docker build -f frontend/Dockerfile -t digital-designer-frontend . \
          || fail "не удалось собрать frontend-образ"
        docker compose up -d --no-build
        ok "образы собраны напрямую, сервисы подняты"
      else
        cat "$err_log" >&2
        rm -f "$err_log"
        fail "docker compose не смог собрать сервисы (см. вывод выше)"
      fi
    fi
    rm -f "$err_log"
  else
    docker compose "${args[@]}"
  fi

  # ollama на первом запуске скачивает модели — это несколько минут
  wait_for_http "http://localhost:${BACKEND_PORT}/api/health" "$HEALTH_TIMEOUT" "backend" || true
  wait_for_http "http://localhost:${FRONTEND_PORT}/" "$HEALTH_TIMEOUT" "frontend" || true

  if curl -fsS "http://localhost:${BACKEND_PORT}/api/health" 2>/dev/null | grep -q '"available":true'; then
    ok "Ollama доступна: планировщик работает на Qwen2.5"
  else
    warn "Ollama недоступна — сервис работает в режиме офлайн-планировщика"
    warn "первый запуск скачивает модели: docker compose logs -f ollama"
  fi

  (( SHOW_LOGS )) && docker compose logs -f
  print_ready
}

# ------------------------------------------------------------------- локально
start_local() {
  need_cmd python "Python не найден (нужен 3.11+)"
  need_cmd npm "Node.js/npm не найден (нужен Node 20+)"

  if ! python -c "import fastapi, pptx, pydantic" >/dev/null 2>&1; then
    info "ставлю зависимости бэкенда…"
    python -m pip install -q -r backend/requirements.txt
  fi
  ok "зависимости бэкенда на месте"

  if [[ ! -d frontend/node_modules ]]; then
    info "ставлю зависимости фронтенда…"
    (cd frontend && npm ci --no-audit --no-fund 2>/dev/null || npm install --no-audit --no-fund)
  fi
  ok "зависимости фронтенда на месте"

  if ! command -v soffice >/dev/null 2>&1 && ! command -v libreoffice >/dev/null 2>&1; then
    warn "LibreOffice не найден: миниатюры и PDF-экспорт будут недоступны (HTTP 503)"
  fi

  info "запускаю backend (uvicorn) и frontend (next dev)…"
  # запускаем из корня: каталог data/ тогда один и тот же, что и в Docker
  ( cd "$ROOT" && DISABLE_LLM="${DISABLE_LLM:-false}" \
      python -m uvicorn app.api.main:app --app-dir backend \
      --host 127.0.0.1 --port "$BACKEND_PORT" ) &
  BACKEND_PID=$!
  ( cd frontend && NEXT_PUBLIC_API_URL="http://localhost:${BACKEND_PORT}" \
      npm run dev -- --port "$FRONTEND_PORT" ) &
  FRONTEND_PID=$!

  cleanup() {
    echo
    info "останавливаю процессы…"
    kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
    wait "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
  }
  trap cleanup INT TERM EXIT

  wait_for_http "http://localhost:${BACKEND_PORT}/api/health" 120 "backend" || true
  wait_for_http "http://localhost:${FRONTEND_PORT}/" 180 "frontend" || true
  print_ready
  info "нажмите Ctrl+C для остановки"
  wait
}

if [[ "$MODE" == "docker" ]]; then
  start_docker
else
  start_local
fi
