# Воспроизводимость: проверка на чистой машине (этап G)

> Требование сдачи: «с нуля до работающей презентации за одну команду, без
> ручных правок». Здесь — пошаговый лог реальной проверки, а не инструкция
> по памяти. Проверка выполнена на коммите `9e89574` (после неё менялись
> только документы).

## 0. Условия проверки

| Параметр | Значение |
|---|---|
| Машина | Windows 11, Docker Desktop 4.x, **без GPU** (CPU-инференс) |
| Чистый каталог | `C:\temp\clean-checkout` (латиница: на кириллическом пути Docker BuildKit падает, см. §5) |
| Исходники | `git clone` из рабочего репозитория (аналог клонирования с GitHub) |
| `.env` | **отсутствует**: проверялось, что `docker compose up` работает без него |
| Коммит | `9e89574` |

## 1. Клон без локальных настроек

```
$ git clone <repo> C:\temp\clean-checkout
done.
$ Test-Path C:\temp\clean-checkout\.env
False
$ Get-ChildItem C:\temp\clean-checkout\data -Recurse
data\.gitkeep
data\templates\.gitkeep
```

В клоне нет ни `.env`, ни шаблонов, ни контент-пакетов: только код, тесты,
встроенные примеры (`examples/synthetic_16x9.pptx`, `examples/builtin_corpus.md`)
и JSON-профили шаблонов.

## 2. Одна команда

```
$ docker compose up --build -d
...
 Container dp-ollama Started
 Container dp-ollama Healthy
 Container dp-backend Started
 Container dp-frontend Started
# первая сборка (BuildKit, кэш пустой): 4.2 мин
# повторная сборка (кэш слоёв): 0.2–0.3 мин
```

Первый старт контейнера `ollama` скачивает модели: `qwen2.5:7b-instruct` (4.7 ГБ),
`qwen2.5vl:7b` (6.0 ГБ), `bge-m3` (1.2 ГБ) — около 12 ГБ, на проверке ~9 минут.
Маркер `/tmp/models-ready` создаётся после скачивания, поэтому `backend`
стартует только когда модели на месте (healthcheck контейнера `ollama`).

```
$ docker compose ps
dp-backend   Up   (порты 8000)
dp-frontend  Up   (порты 3000)
dp-ollama    Up   (healthy, порты 11434)
```

## 3. Готовность и генерация

```
$ curl -s localhost:8000/api/health
LLM: ollama/qwen2.5:7b-instruct available=True
VLM: ollama/qwen2.5vl:7b       available=True

$ POST /api/content/import  (examples/builtin_corpus.md)
corpus_id=585d68cd0e21, секций 5, чисел 14

$ POST /api/generate  (examples/synthetic_16x9.pptx, встроенный бриф, corpus_id)
job_id=79e02b259eeb → done за 135.4 c
стадии: parse 0.62 c · plan 120.09 c · render 0.40 c · audit 0.83 c ·
        grounding 9.96 c · итого 131.9 c
слайдов 8, планировщик: offline-fallback (см. §4)
```

Экспорты (все три из одного задания):

| Формат | Код | Размер | Как проверяли |
|---|---|---|---|
| PPTX | 200 | 44 КБ | открыт python-pptx и LibreOffice (PDF-рендер), объекты нативные |
| PDF | 200 | 34 КБ | LibreOffice headless (тот же путь, что в UI) |
| HTML | 200 | 7 КБ | самостоятельная страница со стилями шаблона |
| PNG-миниатюра | 200 | 25 КБ | `/api/jobs/{id}/thumb` — путь миниатюр UI |

**Редактируемость PPTX** (проверено скриптом): 8 слайдов, 28 текстовых фигур
(45 runs), 1 диаграмма, 0 растровых снимков слайдов; каждый текст — фигура
PowerPoint с runs, то есть редактируется в PowerPoint/LibreOffice.

**Аудит**: ошибок 0 во всех трёх вариантах; в `compact` одно замечание
grounding (`близость 0.43 < 0.45`), в `cards`/`split` — замечания
`text_overflow` на плотном слайде с тремя блоками (задача слоя layout,
этап D). Аудит честно помечает и офлайн-планировщик: в отчёте задания
`planning: offline-fallback`, а не «модель».

## 4. Почему на CPU офлайн-планировщик, и что рекомендовано

Локальная Qwen2.5-7B на CPU не отвечает за 120 с (`OLLAMA_TIMEOUT_S`), поэтому
сервис честно переключается на офлайн-планировщик: колода, вёрстка, аудит и
экспорт работают полностью, в отчёте видно `offline-fallback`.

VLM-аудит на CPU не влезает в бюджет стадии: модель не успевает обработать
изображение слайда. В `.env` из `.env.example` для CPU рекомендовано
`VLM_AUDIT_ENABLED=false` — тогда полный прогон занимает **135 с** и
детерминированный аудит + grounding работают без ограничений.

На GPU (или через внешний шлюз, ADR-020) работают все стадии: замеры — в
`docs/DEMO.md` (репетиция 216 с из 420) и `data/output/external/report.json`
(3 сторонних шаблона × 3 варианта, 141–303 с на шаблон с VLM).

## 5. Что нашла проверка и что исправлено

Проверка на чистой машине нашла реальные дефекты, которых не видел локальный
прогон (в локальном `.env` стоял внешний шлюз, а не локальные веса):

| Коммит | Что было |
|---|---|
| `e63bd0b` | в репозитории не было встроенного шаблона и контент-пакета для быстрого старта; `.env.example` по умолчанию включал внешний шлюз |
| `4590213` | `docker-compose.yml` тянул `qwen2.5-vl:7b-instruct` — такого тега в Ollama нет (нужен `qwen2.5vl:7b`), `bge-m3` не скачивался вовсе: VLM-аудит получал 404, grounding молча пропускал проверку смысла |
| `994f8ae` | бюджет стадии VLM только помечал слайды, но не ограничивал запросы: на CPU стадия занимала 906 с вместо 180 |
| `81758e9` | если модель не отвечает ни на один слайд, стадия ждала все волны; теперь останавливается |
| `9e89574` | проверка `text_overflow` сравнивала дюймы с EMU и не срабатывала никогда — наложения factoid-подписей проходили как «чисто»; заодно исправлены рамки factoid'ов и подбор кегля |
| `5a51a1a` | плавающий сбой теста: кэш профилей ключевался по `id(bytes)` |

## 6. Ограничения и заметки

* **Первая сборка** — 4–9 минут (npm/pip + образы), первый старт — ещё
  ~9 минут на модели (~12 ГБ). Дальше `docker compose up` занимает секунды.
* **Кириллический путь**: `docker compose build` падает на BuildKit из-за
  кириллицы в пути; лечится `DOCKER_BUILDKIT=0` (см. troubleshooting в
  `docs/README.md`). На латинском пути (`C:\temp\clean-checkout`) сборка
  проходит штатно.
* **Docker Desktop 29.2.1 (проверено 2026-09-26)**: `docker compose build`
  может падать до сборки с `failed to dial gRPC: ... header key
  "x-docker-expose-session-sharedkey" contains value with non-printable ASCII
  characters` — это баг bake-планировщика Compose, а не проекта, и
  `DOCKER_BUILDKIT=0` на этой версии уже не поддерживается (502 у classic
  builder). Рабочий обход — собрать образы напрямую и поднять без сборки:

  ```bash
  docker build -f backend/Dockerfile  -t digital-designer-backend  .
  docker build -f frontend/Dockerfile -t digital-designer-frontend .
  docker compose up -d --no-build
  ```

  После перезапуска Docker Desktop обычный `docker build` работает; сборка
  проверена, `/api/health` отдаёт version 1.4.0, генерация на VK WorkSpace —
  0 ошибок и 0 замечаний.
* **`docker compose config` печатает ключи** из `.env` — не публикуйте вывод.
* **Скриншоты**: UI — `docs/evidence/repro/ui_main.png` (headless Chrome,
  состояние чистого клона: форма, встроенный бриф, селекторы `ollama`);
  слайды — `docs/evidence/repro/clean_slide_1..5.png` (миниатюры API, тот же
  рендер, что видит судья в UI).
* **Логи задания** остаются в `data/jobs/{id}/` (переживают рестарт контейнера,
  ADR-016), поэтому проверку можно повторить на тех же данных.

## 7. Как повторить (короткая версия)

```bash
git clone <repo> && cd <repo>
docker compose up --build            # первая сборка 4–9 мин, модели ~12 ГБ
# UI:  http://localhost:3000  →  выбрать examples/synthetic_16x9.pptx,
#      при желании examples/builtin_corpus.md, «Сгенерировать 3 варианта»
# API: http://localhost:8000/docs
```

На CPU-машине перед запуском: `cp .env.example .env` и `VLM_AUDIT_ENABLED=false`.
