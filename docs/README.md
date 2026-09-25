# README: сетап, окружение, ограничения

> Краткая версия — в корневом [README.md](../README.md). Здесь полный сетап,
> переменные окружения, ограничения и troubleshooting.

## 1. Требования

| Компонент | Версия | Зачем |
|---|---|---|
| Docker Desktop / Docker Engine | 24+ | воспроизводимый запуск (`docker compose up`) |
| Docker Compose | v2 | профиль `docker-compose.yml` |
| GPU (опционально) | NVIDIA + `nvidia-container-toolkit` | ускорение LLM/VLM; на CPU работает, но медленнее |
| Свободное место | ~15 ГБ | образы + модели Ollama |
| RAM | 16 ГБ минимум | qwen2.5:7b-instruct ≈ 5 ГБ, qwen2.5-vl:7b ≈ 6 ГБ |

Локальный запуск без Docker возможен, но требует Python 3.11 и LibreOffice
(см. §6). Рекомендуемый путь — Docker.

## 2. Запуск

```bash
./start.sh                    # то же, что docker compose up --build + ожидание готовности
```

Вручную:

```bash
cp .env.example .env          # Windows PowerShell: Copy-Item .env.example .env
docker compose up --build
```

Режимы `start.sh`: `--no-build` (поднять собранные образы), `--local` (без
Docker: uvicorn + next dev, требуются Python 3.11 и Node 20), `--with-assets DIR`
(скопировать шаблоны и контент-пакет из папки и пересобрать профили), `--logs`,
`-h`. Скрипт идемпотентен и печатает адреса сервисов по готовности.

Сервисы:

| Сервис | Адрес | Назначение |
|---|---|---|
| frontend | http://localhost:3000 | веб-интерфейс |
| backend | http://localhost:8000 | API (Swagger: `/docs`, health: `/api/health`) |
| ollama | http://localhost:11434 | локальный инференс LLM/VLM/эмбеддингов |

Первый старт: контейнер `ollama` скачивает `qwen2.5:7b-instruct` и
`qwen2.5-vl:7b-instruct` (несколько ГБ). Прогресс:

```bash
docker compose logs -f ollama
```

Готовность модели к работе:

```bash
docker compose exec ollama ollama list
```

## 3. Шаблоны и контент-пакеты

Бинарные шаблоны (`*.pptx`) не коммитятся в git (ADR-002). Варианты получения:

1. **Датасет организаторов** (личный кабинет хакатона) — положить файлы в
   `data/templates/`.
2. **Любой свой PPTX** — сервис не заточен под конкретные шаблоны: загрузка
   идёт через UI/API, профиль строится на лету.
3. **Синтетические фикстуры для тестов** — генерируются скриптом
   `python tests/fixtures/make_fixtures.py` (маленькие PPTX, лежат в git).

Проверка, что шаблон разобран:

```bash
python tools/audit_matrix.py --layouts data/templates/my.pptx   # профиль и роли макетов
```

Проверка адаптации к **сторонним** шаблонам (критерий ТЗ №3): три чужих шаблона
с открытыми лицензиями, их профили и скриншоты лежат в `docs/evidence/external/`,
разбор ошибок классификации — ADR-025 и ADR-026. Прогон:

```bash
python tools/e2e_9variants.py --templates data/external_templates/*.pptx --out data/output/external
```

### Контент-пакет

Пакет можно не только вставить текстом, но и загрузить файлом — сервис разберёт
его на слайды/разделы (заголовки, тезисы, таблицы, диаграммы, цифры, картинки):

```bash
python tools/import_corpus.py data/templates/content_pack.pptx --text
```

Поддерживаются PPTX, DOCX, TXT и MD. Изображения из пакета встраиваются в слайды
нативными объектами (`Block.kind = "image"`), а текст уходит планировщику как
единственный источник фактов. Строки-заглушки («Заголовок», «Текст описания»)
отфильтровываются и не попадают в колоду.

## 4. Переменные окружения

Все параметры читаются из `.env` (шаблон — `.env.example`). Для зачётной
конфигурации секретов не требуется: работает локальная Ollama. Если вы включили
внешний шлюз (`LLM_PROVIDER=openai_compat`), ключ лежит только в `.env` —
этот файл в `.gitignore`, в репозиторий попадает лишь `.env.example`.

> `docker compose config` печатает уже разрешённые значения, включая ключ, —
> не публикуйте вывод этой команды.

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `LLM_PROVIDER` | `aitunnel` | `aitunnel` (шлюз с моделями Qwen, ADR-020), `ollama` (эталон сдачи: открытые веса, локально) или `openai_compat` (ADR-019) |
| `VLM_PROVIDER` | `aitunnel` | провайдер VLM-аудита: `aitunnel`, `ollama`, `openai_compat` или `off` |
| `AITUNNEL_BASE_URL` | `https://api.aitunnel.ru/v1` | адрес OpenAI-совместимого API AITUNNEL |
| `AITUNNEL_API_KEY` | — | ключ AITUNNEL; хранится только в `.env` |
| `AITUNNEL_LLM_MODEL` / `_VLM_MODEL` | `qwen3.5-9b` | мультимодальная Qwen: планировщик и VLM-аудит |
| `AITUNNEL_TIMEOUT_SEC` / `_MAX_RETRIES` | `120` / `1` | таймаут запроса и один повтор на сетевых сбоях и 5xx/429 |
| `DEMO_MODE` | `false` | `true` — не подменять недоступную модель офлайн-колодой (для живого демо) |
| `OPENAI_COMPAT_BASE_URL` | — | база OpenAI-совместимого шлюза, например `https://opencode.ai/zen/v1` |
| `OPENAI_COMPAT_API_KEY` | — | ключ шлюза; хранится только в `.env` (в git не попадает) |
| `OPENAI_COMPAT_MODEL` / `_VLM_MODEL` / `_EMBEDDING_MODEL` | — | модели шлюза; пустые значения означают «использовать локальные» |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | адрес Ollama; в compose подставляется `http://ollama:11434` |
| `LLM_MODEL` | `qwen2.5:7b-instruct` | планировщик колоды |
| `LLM_MODEL_14B` | `qwen2.5:14b-instruct` | более сильный планировщик (если есть ресурсы) |
| `VLM_MODEL` | `qwen2.5-vl:7b-instruct` | недетерминированный аудит слайдов |
| `EMBEDDING_MODEL` | `bge-m3` | эмбеддинги: дедупликация слайдов, grounding фактов |
| `DISABLE_LLM` | `false` | `true` — принудительный офлайн-планировщик (dev/CI) |
| `LLM_TIMEOUT_S` | `300` | таймаут запроса к Ollama |
| `PLANNER_LLM_MAX_RETRIES` | `3` | попытки получить валидный JSON по Pydantic-схеме |
| `MAX_SLIDES` / `MIN_SLIDES` | `15` / `4` | целевой объём колоды (ТЗ: 10–15) |
| `TARGET_DURATION_MIN` | `5` | бюджет времени генерации, минуты |
| `DATA_DIR` | `./data` | хранилище заданий, кэша миниатюр, выгрузок |
| `MAX_UPLOAD_MB` | `60` | лимит размера загружаемого шаблона |
| `LIBREOFFICE_BIN` | `soffice` | бинарь LibreOffice для PDF и миниатюр |
| `CORS_ORIGINS` | `http://localhost:3000` | разрешённые origin'ы фронтенда |
| `JOB_CLEANUP_HOURS` | `12.0` | TTL заданий и кэша миниатюр |
| `VLM_AUDIT_ENABLED` | `true` | выключить VLM-аудит, если нет ресурсов/времени |
| `VLM_AUDIT_ALL_VARIANTS` | `false` | `true` — VLM по всем трём вариантам, а не только по одному |
| `GROUNDING_ENABLED` | `true` | проверка опоры на контент-пакет (числа и смысл) |
| `GROUNDING_OFF_SOURCE_THRESHOLD` | `0.45` | порог косинусной близости слайда к корпусу |
| `GROUNDING_DUPLICATE_THRESHOLD` | `0.92` | порог семантического дубля слайдов |

## 4.1 Хранилище и кэш

```
data/jobs/{id}/job.json        метаданные задания (переживают рестарт)
data/jobs/{id}/template.pptx   исходный шаблон задания (нужен для авто-фиксов)
data/jobs/{id}/{variant}.pptx  три варианта колоды
data/cache/thumbs/{hash}/      миниатюры слайдов по хэшу PPTX
data/corpora/{id}/             разобранные контент-пакеты
```

Всё старше `JOB_CLEANUP_HOURS` удаляется: при старте сервиса и лениво при новых
запросах. Кэш миниатюр инвалидируется сам после авто-фиксов, потому что ключ —
хэш содержимого PPTX.

## 5. API (кратко)

| Метод | Путь | Назначение |
|---|---|---|
| `POST` | `/api/generate` | шаблон + бриф → `job_id` (асинхронно) |
| `GET` | `/api/jobs/{id}` | статус, сводка по вариантам |
| `GET` | `/api/jobs/{id}/info` | профиль, колода, planner, VLM, аудит-сводка |
| `GET` | `/api/jobs/{id}/pptx?variant=…` | скачать PPTX-вариант |
| `GET` | `/api/jobs/{id}/pdf?variant=…` | PDF (LibreOffice headless) |
| `GET` | `/api/jobs/{id}/html` | HTML-версия колоды |
| `GET` | `/api/jobs/{id}/thumb?variant=…&s=…` | PNG-миниатюра слайда (`&boxes=1` — красные рамки проблем) |
| `POST` | `/api/jobs/{id}/fix` | детерминированные авто-фиксы выбранных проблем |
| `POST` | `/api/content/import` | импорт контент-пакета (PPTX/DOCX/TXT/MD) → корпус |
| `GET` | `/api/content` | список импортированных корпусов |
| `GET` | `/api/content/{id}` | структура корпуса (слайды, цифры, изображения) |
| `GET` | `/api/content/{id}/image/{key}` | изображение из контент-пакета |
| `GET` | `/api/health` | состояние сервиса и доступность Ollama |

## 6. Запуск без Docker (для отладки)

Требуется Python 3.11 (в 3.12+ часть зафиксированных зависимостей, например
PyMuPDF, может не собраться) и установленный LibreOffice.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r backend/requirements.txt -r backend/requirements-dev.txt
uvicorn app.api.main:app --reload --app-dir backend --port 8000
```

Без Ollama сервис работает в режиме офлайн-планировщика (`DISABLE_LLM=true`):
колода собирается из брифа детерминированно, аудит и экспорт работают полностью.
Без LibreOffice недоступны PDF-экспорт и миниатюры (HTTP 503 с пояснением).

## 7. Ограничения

* **Только десктоп.** Мобильные и планшетные разрешения не поддерживаются (ТЗ, §3).
* **Время генерации.** Бюджет — до 5 минут на колоду 10–15 слайдов на GPU.
  На CPU дольше; VLM-аудит — самая дорогая стадия, его можно отключить флагом.
* **VLM-аудит недетерминирован.** Один и тот же слайд может получать разные
  оценки при повторных прогонах; это свойство метода, а не баг (ТЗ, Приложение 1).
* **Шрифты.** Если гарнитура шаблона не установлена в контейнере, LibreOffice
  подставит свою при экспорте в PDF/миниатюры. В PPTX шрифт сохраняется как есть.
* **PDF-экспорт** зависит от LibreOffice: часть сложных эффектов (тени,
  SmartArt, некоторые градиенты) может отрисоваться иначе, чем в PowerPoint.
* **Колода ≤ 15 слайдов** — ограничение Pydantic-схемы и ТЗ.
* **Факты.** Планировщик обязан использовать только данные брифа и контент-пакета;
  проверка «факт есть в источнике» выполняется grounding-проверкой (BGE-M3),
  но не является доказательством корректности — финальная ответственность на авторе.

## 8. Troubleshooting

| Симптом | Причина / решение |
|---|---|
| `next start` предупреждает про `output: standalone` | для локальной проверки сборки используйте `npm run dev` или `node .next/standalone/server.js` (как в Dockerfile) |
| Задания/миниатюры пропали после рестарта | они лежат в `data/jobs/` и `data/cache/`; проверьте права на каталог `data/` и значение `JOB_CLEANUP_HOURS` |
| `503 нужен LibreOffice для миниатюр` | контейнер backend собран без LibreOffice либо локальный запуск без `soffice` в PATH |
| В отчёте «планировщик: офлайн-fallback», хотя ключ задан | проверьте провайдера: `python tools/llm_check.py --json-call`. Частые ответы шлюзов: `403 FreeTierError` (бесплатные модели доступны только внутри клиента провайдера) и `402 Insufficient account funds` (нет баланса). До пополнения сервис работает на офлайн-планировщике |
| Нужно вернуться к локальным открытым весам | `LLM_PROVIDER=ollama` в `.env` и `docker compose up -d backend` |
| `LLM_PROVIDER=aitunnel`, но в логе «AITUNNEL_API_KEY пуст» | ключ не вставлен в `.env` (сервис при этом работает на Ollama, а без неё — на офлайн-планировщике) |
| `AITUNNEL вернул 401/402/403` | неверный ключ, нет баланса или модель закрыта для аккаунта — проверьте `python tools/llm_check.py --json-call` |
| В отчёте «планировщик: offline-fallback» при включённом `DEMO_MODE=false` | модель недоступна: смотрите причину в логе (`llm`), при `DEMO_MODE=true` задание завершится ошибкой с тем же текстом |
| Генерация идёт дольше 5 минут | модели ещё скачиваются (`docker compose logs ollama`) либо нет GPU; проверьте `docker compose exec ollama ollama list` |
| `LLM: offline-fallback` в отчёте | Ollama недоступна или `DISABLE_LLM=true`; проверьте `GET /api/health` |
| Пустой результат VLM-аудита | `VLM_AUDIT_ENABLED=false` или модель `qwen2.5-vl:7b-instruct` не скачана |
| `413 шаблон больше 60 МБ` | поднимите `MAX_UPLOAD_MB` в `.env` |
| Кириллица в именах файлов искажена в консоли Windows | `chcp 65001` перед запуском команд |

## 9. Воспроизводимость и версия

```bash
git tag            # зафиксированная версия сдачи
scripts/e2e.sh     # сквозной прогон: тесты + генерация на фикстурах + замер времени
```
