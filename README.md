# Цифровой дизайнер презентаций

Сервис собирает презентацию из текстового брифа **по произвольному PPTX-шаблону**:
извлекает дизайн-систему (палитра, шрифты, типографическая шкала, сетка, макеты),
генерирует структуру и контент локальной LLM, верстает **нативными объектами**
PowerPoint, аудитирует результат (детерминированно + VLM) и отдаёт три варианта
вёрстки в `.pptx`, `.pdf` и `.html`.

Хакатон VK Tech 2026. Без платных API: только open-weights модели через Ollama.

## Быстрый старт

```bash
cp .env.example .env          # Windows: Copy-Item .env.example .env
docker compose up --build
```

* UI — http://localhost:3000
* API — http://localhost:8000/docs

Первый запуск скачивает модели в volume `ollama_data` (несколько ГБ), поэтому
занимает больше времени; последующие запуски — быстрые.

## Что нужно положить в репозиторий вручную

Шаблоны и контент-пакеты не хранятся в git (большие бинарники, см.
`docs/DECISIONS.md` ADR-002). Положите файлы в `data/templates/` — сервис
принимает любой PPTX через UI, ничего дополнительно настраивать не нужно.

## Документация

| Документ | Содержание |
|---|---|
| [docs/README.md](docs/README.md) | полный сетап, переменные окружения, ограничения, troubleshooting |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | пайплайн и границы слоёв, API, точки расширения |
| [docs/MODELS.md](docs/MODELS.md) | модели, области применения, требования, ссылки |
| [docs/AUDIT.md](docs/AUDIT.md) | проверки аудита, детерминированные и контекстуальные, покрытие |
| [docs/DECISIONS.md](docs/DECISIONS.md) | журнал архитектурных решений (ADR) |
| [docs/DEMO.md](docs/DEMO.md) | сценарий демо и питча на 7 минут |

## Структура репозитория

```
backend/            FastAPI, парсер шаблонов, планировщик, вёрстка, рендер, аудит, экспорт
frontend/           Next.js + Tailwind + shadcn/ui
prompts/            промпты и конфиги агентов отдельными файлами (с версиями)
template_profiles/  JSON-профили дизайн-систем разобранных шаблонов
tests/              сквозные тесты и фикстуры (backend/tests — модульные)
tools/              dev-скрипты (e2e-прогон, смоук API)
scripts/            воспроизводимые сценарии запуска (e2e.sh)
docs/               документация
docker-compose.yml  ollama + backend (с LibreOffice) + frontend
```

## Лицензии моделей

Только Apache 2.0 / MIT, до 35B параметров: Qwen2.5 Instruct (Apache 2.0),
Qwen2.5-VL (Apache 2.0), BGE-M3 (MIT). Подробности — `docs/MODELS.md`.
