"use client";

import {
  CheckCircle2,
  FileText,
  FolderOpen,
  HelpCircle,
  Paperclip,
  X,
} from "lucide-react";
import * as React from "react";

import type { RenderMode } from "@/lib/types";

const RENDER_MODES: { id: RenderMode; title: string; hint: string }[] = [
  { id: "native", title: "Классический", hint: "PPTX нативными объектами python-pptx" },
  { id: "html", title: "Через HTML+CSS", hint: "генерация в браузере и конвертация в PPTX" },
  { id: "both", title: "Оба", hint: "можно сравнить на защите" },
];

function Step({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <section className="panel p-6">
      <div className="flex items-center gap-4">
        <span className="step-num" aria-hidden>
          {n}
        </span>
        <div className="min-w-0 flex-1">
          <div className="eyebrow">ШАГ {n}</div>
          <h2 className="text-2xl font-semibold tracking-tight">{title}</h2>
        </div>
      </div>
      <div className="mt-5">{children}</div>
    </section>
  );
}

/** Двухколоночный экран: слева «Для опытных», справа шаги, режимы и запуск. */
export function GeneratorForm({
  template,
  onTemplate,
  brief,
  onBrief,
  corpusName,
  corpusBusy,
  onCorpusFile,
  onCorpusClear,
  formats,
  onFormats,
  renderMode,
  onRenderMode,
  onSubmit,
  busy,
  error,
  advanced,
  simpleMode,
  onSimpleModeChange,
  providerNode,
}: {
  template: File | null;
  onTemplate: (file: File | null) => void;
  brief: string;
  onBrief: (value: string) => void;
  corpusName: string;
  corpusBusy: boolean;
  onCorpusFile: (file: File) => void;
  onCorpusClear: () => void;
  formats: { pptx: boolean; pdf: boolean };
  onFormats: (formats: { pptx: boolean; pdf: boolean }) => void;
  renderMode: RenderMode;
  onRenderMode: (mode: RenderMode) => void;
  onSubmit: () => void;
  busy: boolean;
  error: string;
  advanced: React.ReactNode;
  simpleMode: boolean;
  onSimpleModeChange: (value: boolean) => void;
  providerNode: React.ReactNode;
}) {
  const fileInput = React.useRef<HTMLInputElement>(null);
  const corpusInput = React.useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = React.useState(false);

  const pick = (files: FileList | null) => {
    const file = files?.[0];
    if (file) onTemplate(file);
  };

  const ready = Boolean(template) && brief.trim().length >= 20 && (formats.pptx || formats.pdf);

  return (
    <div className="rise">
      <div className="grid gap-6 lg:grid-cols-[minmax(0,380px)_minmax(0,1fr)] lg:items-start">
        <aside className="space-y-6 lg:sticky lg:top-24">
          <section className={`panel p-5 ${simpleMode ? "border-[rgba(204,255,88,.45)]" : ""}`}>
            <span className="iconbox" aria-hidden>
              <HelpCircle className="h-6 w-6" />
            </span>
            <div className="eyebrow mt-4">ПРОСТОЙ РЕЖИМ</div>
            <h2 className="mt-1 text-2xl font-semibold tracking-tight">
              Я не знаю, что делать
            </h2>
            <p className="mt-2 text-base text-muted-foreground">
              Настроим всё за вас: попросим ключ, соберём через HTML+CSS, отдадим
              PPTX и PDF и включим проверку нейросетью.
            </p>
            <button
              type="button"
              onClick={() => onSimpleModeChange(!simpleMode)}
              className={`btn mt-4 w-full ${simpleMode ? "" : "btn-primary"}`}
            >
              {simpleMode ? "Вернуть ручной режим" : "Включить простой режим"}
            </button>
          </section>
        </aside>

        <div className="space-y-6">
          <header className="text-center">
            <div className="eyebrow">ГЕНЕРАТОР / ТРИ ВАРИАНТА ВЁРСТКИ</div>
            <h1 className="mt-3 text-4xl font-bold tracking-tight">Генератор презентаций</h1>
            <p className="mx-auto mt-4 max-w-2xl text-xl text-muted-foreground">
              Загрузите шаблон и опишите презентацию — получите три готовых варианта за минуту
            </p>
          </header>

          {simpleMode && (
            <section className="panel p-6">
              <div className="eyebrow">ПРОСТОЙ РЕЖИМ ВКЛЮЧЁН</div>
              <h2 className="mt-1 text-2xl font-semibold tracking-tight">
                Подключите нейросеть
              </h2>
              <p className="mt-1 text-base text-muted-foreground">
                Вставьте ключ доступа — агент напишет презентацию. Без ключа соберём
                офлайн-планировщиком: быстрее, но текст проще.
              </p>
              <div className="mt-3 flex flex-wrap gap-2">
                <span className="chip chip-acid">HTML+CSS</span>
                <span className="chip chip-acid">PPTX + PDF</span>
                <span className="chip chip-acid">Проверка нейросетью включена</span>
              </div>
              <div className="mt-4">{providerNode}</div>
            </section>
          )}

          <Step n={1} title="Загрузите шаблон (файл .pptx)">
            <input
              ref={fileInput}
              type="file"
              accept=".pptx,application/vnd.openxmlformats-officedocument.presentationml.presentation"
              className="hidden"
              onChange={(event) => pick(event.target.files)}
            />
            {template ? (
              <div className="flex flex-wrap items-center gap-4 rounded-2xl border border-border bg-card p-5">
                <CheckCircle2 className="h-8 w-8 text-success" aria-hidden />
                <span className="break-all text-lg">{template.name}</span>
                <button
                  type="button"
                  onClick={() => fileInput.current?.click()}
                  className="btn btn-sm ml-auto"
                >
                  Выбрать другой
                </button>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => fileInput.current?.click()}
                onDragOver={(event) => {
                  event.preventDefault();
                  setDragging(true);
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={(event) => {
                  event.preventDefault();
                  setDragging(false);
                  pick(event.dataTransfer.files);
                }}
                className={`drop ${dragging ? "drag" : ""}`}
              >
                <span className="drop-icon" aria-hidden>
                  <FolderOpen className="h-7 w-7" />
                </span>
                <span className="text-xl font-semibold">Выбрать файл шаблона</span>
                <span className="text-base text-muted-foreground">
                  или перетащите файл сюда
                </span>
              </button>
            )}
          </Step>

          <Step n={2} title="Опишите, о чём презентация">
            <textarea
              value={brief}
              onChange={(event) => onBrief(event.target.value)}
              rows={6}
              placeholder="Например: квартальный отчёт по проекту, ключевые результаты, планы на следующий квартал"
              className="input textarea text-lg leading-relaxed"
            />
            <input
              ref={corpusInput}
              type="file"
              accept=".pptx,.docx,.txt,.md"
              className="hidden"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) onCorpusFile(file);
              }}
            />
            {corpusName ? (
              <div className="mt-3 flex flex-wrap items-center gap-3 rounded-2xl border border-border bg-card px-4 py-3">
                <FileText className="h-6 w-6 text-primary" aria-hidden />
                <span className="break-all text-base">Факты и цифры: {corpusName}</span>
                <button
                  type="button"
                  onClick={onCorpusClear}
                  aria-label="Убрать файл с фактами"
                  className="btn btn-sm btn-ghost ml-auto px-2"
                >
                  <X className="h-5 w-5" aria-hidden />
                </button>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => corpusInput.current?.click()}
                disabled={corpusBusy}
                className="btn mt-4"
              >
                <Paperclip className="h-5 w-5" aria-hidden />
                {corpusBusy ? "Загружаем файл…" : "Прикрепить файл с фактами и цифрами"}
              </button>
            )}
          </Step>

          {!simpleMode && (
            <Step n={3} title="Выберите формат скачивания">
              <div className="flex flex-wrap gap-4">
                {(["pptx", "pdf"] as const).map((format) => {
                  const active = formats[format];
                  return (
                    <button
                      key={format}
                      type="button"
                      aria-pressed={active}
                      onClick={() => onFormats({ ...formats, [format]: !active })}
                      className={`btn btn-lg min-w-[170px] text-lg ${active ? "btn-primary" : ""}`}
                    >
                      {format.toUpperCase()}
                      {active ? " ✓" : ""}
                    </button>
                  );
                })}
              </div>
              {!formats.pptx && !formats.pdf && (
                <p className="mt-3 text-lg text-warning">Выберите хотя бы один формат.</p>
              )}

              <div className="divider my-6" />

              <div>
                <div className="eyebrow">СПОСОБ СБОРКИ</div>
                <div className="mt-3 space-y-2" role="radiogroup" aria-label="Способ сборки">
                  {RENDER_MODES.map((mode) => {
                    const active = renderMode === mode.id;
                    return (
                      <label
                        key={mode.id}
                        className={`flex cursor-pointer items-start gap-3 rounded-2xl border px-4 py-3 transition-colors ${
                          active
                            ? "border-[rgba(204,255,88,.45)] bg-[rgba(204,255,88,.05)]"
                            : "border-border bg-card"
                        }`}
                      >
                        <input
                          type="radio"
                          name="render-mode"
                          value={mode.id}
                          checked={active}
                          onChange={() => onRenderMode(mode.id)}
                          className="mt-1 h-5 w-5"
                        />
                        <span>
                          <span className="text-lg font-medium">{mode.title}</span>
                          <span className="block text-base text-muted-foreground">
                            — {mode.hint}
                          </span>
                        </span>
                      </label>
                    );
                  })}
                </div>
              </div>
            </Step>
          )}

          {simpleMode && (
            <div className="notice">
              <span className="notice-icon text-[hsl(var(--status-yellow))]" aria-hidden>
                i
              </span>
              <div className="text-base text-muted-foreground">
                Настройки зафиксированы автоматически: сборка через HTML+CSS, оба
                формата, смысловая проверка моделью включена. Нужен полный
                контроль — верните ручной режим слева.
              </div>
            </div>
          )}

          {error && (
            <div className="notice notice-error">
              <span className="notice-icon text-[hsl(var(--status-red))]" aria-hidden>
                !
              </span>
              <div className="text-base">{error}</div>
            </div>
          )}

          <div>
            <button
              type="button"
              onClick={onSubmit}
              disabled={!ready || busy}
              className="btn btn-primary btn-lg w-full text-2xl"
            >
              {busy ? "Готовим…" : "Создать презентацию"}
            </button>
            {!ready && (
              <p className="mt-3 text-center text-base text-muted-foreground">
                {!template
                  ? "Сначала загрузите шаблон."
                  : brief.trim().length < 20
                    ? "Опишите презентацию чуть подробнее (минимум 20 символов)."
                    : "Выберите хотя бы один формат."}
              </p>
            )}
          </div>

          {advanced}
        </div>
      </div>
    </div>
  );
}
