# Матрица качества и замеры времени

Документ для защиты: что именно проверено, с какими цифрами, и что влияет на
время. Все замеры воспроизводимы командами из раздела 5.

## 1. Матрица: 8 шаблонов × 3 варианта

`python tools/audit_matrix.py` — детерминированный аудит каждой колоды
(офлайн-планировщик, один и тот же бриф):

| Шаблон | Макетов | compact | cards | split |
|---|---|---|---|---|
| `synthetic_16x9` (фикстура, в git) | 11 | чисто | чисто | чисто |
| `synthetic_4x3` (фикстура, в git) | 11 | чисто | чисто | чисто |
| `unfamiliar_mutated` (имена `Slide N`, чужие цвета/шрифты) | 11 | чисто | чисто | чисто |
| `unfamiliar_16x10` (мутация 16:10) | 11 | чисто | чисто | чисто |
| `VK Tech шаблон` (39 макетов) | 39 | чисто | чисто | чисто |
| `VK_WorkSpace_Клиентская_конференция` (тёмный, `p:bg=#000000`) | 15 | чисто | чисто | чисто |
| `ЛЦТ2026 Шаблон презентации` | 23 | чисто | чисто | чисто |
| `Шаблон презентации VK Education` | 30 | чисто | чисто | чисто |

**Итог: 24 из 24 комбинаций — 0 ошибок и 0 замечаний.** Три варианта одинаково
валидны на всех шаблонах, включая тёмный VK WorkSpace и шаблон без осмысленных
имён макетов.

### 1b. Матрица обоих путей: классика + HTML (v2.0)

`python tools/audit_matrix.py --html` — к тем же 24 комбинациям добавляется
HTML-путь (HTML+CSS → PPTX нативными фигурами, ADR-035/036):

| Метрика | Значение |
|---|---|
| Комбинаций классического пути | 24 из 24 чисто |
| Комбинаций HTML-пути | 24 из 24 чисто |
| **Всего** | **48 из 48 чисто, расхождений 0** |
| Среднее время HTML-пути на комбинацию | +0.4 с (рендер HTML + конвертация + аудит) |

Расхождения не подгоняются: аудит один и тот же, и на LLM-колодах он честно
нашёл дефект конвертера (`image_stretched` — картинка вставлялась по размеру
рамки); лечится contain-боксом в `html_to_pptx.py` (`_contain_box`).

### Регресс тёмного шаблона и фикс ADR-034

На VK WorkSpace (фон макетов `p:bg = #000000`) тексты стали невидимыми: рендер
и аудит выбирали фон по светлой палитре темы и ставили тёмный текст на чёрный
слайд, а `contrast_too_low` молчал. Правки:

| Что | Причина | Правка |
|---|---|---|
| контент не виден (слайды 2–7, 9, 10, 13) | фон макета не читался: `p:bg` игнорировался, брали палитру темы | парсер кладёт `layout.background`; рендер и аудит используют один алгоритм: заливка фигуры → декор → `p:bg` → палитра |
| «0 / 0 / 0» на титуле | фоновые картинки Google-Slides-экспорта (три — за границей слайда, одна шире слайда) | рендер удаляет крупные почти чёрные выходящие за границы картинки |
| класс «текст одного цвета с фоном» не ловился | не было проверки абсолютного порога | новые коды `text_invisible` (error, <3:1) и `empty_text_frame` (warning) |
| фактоиды без подписей | подписи были чёрными на чёрном | тот же фикс контраста; подписи стали светлыми |

Старое битое задание (`data/jobs/f56c66005cae`) теперь даёт 43 ошибки
`text_invisible` в каждом варианте вместо «0 ошибок» — аудит видит дефект.
Сквозная проверка видимости: `python tools/check_visibility.py deck.pptx template.pptx`.

## 2. Живой e2e: три VK-шаблона × три варианта

`VLM_AUDIT_ENABLED=false python tools/e2e_9variants.py --templates "VK_WorkSpace…" "VK Education…" "VK Tech…" --out data/output/9variants_final`
(реальный планировщик AITUNNEL/qwen3.5-9b, контент-пакет VK Tech):

| Шаблон | Вариант | Слайдов | Ошибок | Замечаний | Видимых текстов (невидимых) |
|---|---|---|---|---|---|
| VK WorkSpace (тёмный) | compact / cards / split | 12 | 0 | 0 | 33 / 33 / 33 (0) |
| VK Education | compact / cards / split | 10 | 0 | 0 | 34 / 34 / 34 (0) |
| VK Tech | compact / cards / split | 14 | 0 | 1 / 1 / 1 | 46 / 44 / 46 (0) |

Единственное замечание на VK Tech — `fact_unverified`: планировщик поставил
число «2024», которого нет в брифе и контент-пакете. Это **не дефект вёрстки**,
а честная пометка уровня grounding; авто-фиксы такие замечания не трогают и
показывают их в секции «Смысловые замечания».

Видимость подтверждена `tools/check_visibility.py` (контраст каждого run к
фактическому фону): **0 невидимых (<3:1) и 0 ниже WCAG на всех девяти колодах**.

### 2b. Живой e2e обоих путей (v2.0)

`VLM_AUDIT_ENABLED=false python tools/e2e_9variants.py --render-mode both …`
(3 VK-шаблона × 3 варианта, 129.5 с):

| Шаблон | Классика, ошибок/замечаний | HTML-путь, ошибок/замечаний |
|---|---|---|
| VK WorkSpace (тёмный) | 0 / 1 | 0 / 0 |
| VK Education | 0 / 0 | 0 / 0 |
| VK Tech | 0 / 0 | 0 / 0 |

Единственное замечание классики — grounding `content_off_source` (семантика
пересказа брифа), к вёрстке отношения не имеет. HTML-путь на этих же колодах
чист; сохранены и PPTX, и HTML по всем вариантам
(`data/output/e2e_both/*.pptx`, `*.html`).

## 3. Замеры времени: что на что влияет

| Прогон | Итого | Конфигурация | Что влияет |
|---|---|---|---|
| Фикс VK Education, питч-бриф | **20 с** | AITUNNEL, VLM off, 15 слайдов | планирование 14 с; рендер+аудит 4 с |
| Финал e2e, 9 вариантов | **109 с** | AITUNNEL, VLM off | Grounding/план на шаблон 19–64 с |
| Живое демо, шлюз, VLM | **58–76 с** | AITUNNEL (планировщик + VLM), 10–14 слайдов | VLM-стадия 20–25 с; планирование 12–18 с |
| Питч-колода, шлюз, VLM | **216 с** | AITUNNEL, 15 слайдов, `purpose=product` | VLM-стадия 186 с — самый дорогой шаг |
| Чистая машина, CPU | **135 с** | офлайн-планировщик, VLM off | ожидание локальной модели 120 с (`OLLAMA_TIMEOUT_S`) и уход в офлайн |
| Без моделей (CI) | **2–7 с** | офлайн-планировщик, 3 варианта | парсинг + рендер + аудит |
| UI-прогон (headless Chrome, офлайн) | **55–60 с** | два прохождения браузером (оба формата и только PPTX) | ожидание генерации и скачиваний |

Скачивание всех шести файлов (3 PPTX + 3 PDF через ZIP): 8–12 с, включая
конвертацию LibreOffice.

## 4. Тесты

| Набор | Команда | Результат |
|---|---|---|
| Backend, ядро (аудит/рендер/нормализация/тёмный шаблон/ThemePair/HTML-рендереры) | `pytest backend/tests/test_audit*.py test_fixes.py test_normalize.py test_dark_template.py test_theme_pair.py test_vk_education.py test_layout_roles.py test_render.py test_export_html.py test_html_renderer.py test_html_to_pptx.py` | **90 passed** |
| Backend, API/скачивание/провайдер/пайплайн/режимы сборки | `pytest backend/tests/test_api.py test_downloads.py test_provider_runtime.py test_auto_fix_pipeline.py test_pipeline.py test_storage.py test_content_import.py test_render_modes.py` | **55 passed** |
| Backend, прочее (планировщик, VLM, grounding, образцы, производительность) | `pytest backend/tests/test_block_robustness.py test_grounding.py test_imagegen.py test_llm_providers.py test_performance.py test_planner.py test_prompts_registry.py test_vlm.py` | **82 passed** |
| Браузерные (headless Chrome, поднимают uvicorn+next) | `pytest backend/tests/test_ui_browser.py` | **6 passed** |

Итого **233 теста** (227 backend + 6 браузерных). Браузерные тесты
автоматически скипаются, если не собран фронтенд, не установлен Playwright или
заняты порты 8000/3000 (остановите `docker compose stop` перед запуском).

## 5. Как воспроизвести

```bash
python tools/audit_matrix.py                     # матрица 8×3 классического пути
python tools/audit_matrix.py --html              # + HTML-путь: 48/48, расхождения видны
python tools/e2e_9variants.py --render-mode both \
  --templates "VK_WorkSpace_Клиентская_конференция_Шаблон_03.pptx" \
  "Шаблон презентации VK Education.pptx" "VK Tech шаблон.pptx" --out data/output/e2e_both
python tools/check_visibility.py data/output/9variants_final/*_compact.pptx "VK Tech шаблон.pptx"
pytest backend/tests/test_dark_template.py -q    # тёмный шаблон: контраст и шум
pytest backend/tests/test_html_renderer.py backend/tests/test_html_to_pptx.py -q
pytest backend/tests/test_ui_browser.py -q       # интерфейс в headless Chrome
```

Скриншоты: `docs/examples/` (3 шаблона × 3 варианта), `docs/evidence/visual/`
(пары «до/после»), `docs/evidence/pitch/`, `docs/evidence/ui_final/` (интерфейс),
`docs/evidence/vk_education/` (фикс VK Education: PPTX+PDF+PNG).
