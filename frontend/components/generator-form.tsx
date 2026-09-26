"use client";

import { CheckCircle2, FileText, FolderOpen, Paperclip, X } from "lucide-react";
import * as React from "react";

import type { RenderMode } from "@/lib/types";

const RENDER_MODES: { id: RenderMode; title: string; hint: string }[] = [
  { id: "native", title: "Классический", hint: "PPTX нативными объектами python-pptx" },
  { id: "html", title: "Через HTML+CSS", hint: "генерация в браузере и конвертация в PPTX" },
  { id: "both", title: "Оба", hint: "можно сравнить на защите" },
];

function StepNumber({ n }: { n: number }) {
  return (
    <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary text-lg font-bold text-primary-foreground">
      {n}
    </span>
  );
}

function Step({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <section className="flex gap-4">
      <StepNumber n={n} />
      <div className="min-w-0 flex-1">
        <h2 className="text-2xl font-semibold">{title}</h2>
        <div className="mt-3">{children}</div>
      </div>
    </section>
  );
}

/** Один экран: шаг 1 — шаблон, шаг 2 — бриф, шаг 3 — форматы, одна кнопка запуска. */
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
    <div>
      <header className="text-center">
        <h1 className="text-4xl font-bold">Генератор презентаций</h1>
        <p className="mt-3 text-xl text-muted-foreground">
          Загрузите шаблон и опишите презентацию — получите три готовых варианта за минуту
        </p>
      </header>

      <div className="mt-10 space-y-10">
        <Step n={1} title="Загрузите шаблон (файл .pptx)">
          <input
            ref={fileInput}
            type="file"
            accept=".pptx,application/vnd.openxmlformats-officedocument.presentationml.presentation"
            className="hidden"
            onChange={(event) => pick(event.target.files)}
          />
          {template ? (
            <div className="flex flex-wrap items-center gap-4 rounded-xl border border-border bg-card p-5">
              <CheckCircle2 className="h-8 w-8 text-success" aria-hidden />
              <span className="break-all text-xl">{template.name}</span>
              <button
                type="button"
                onClick={() => fileInput.current?.click()}
                className="min-h-[44px] rounded-lg border border-border px-4 text-base hover:bg-secondary"
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
              className={`flex w-full flex-col items-center gap-3 rounded-xl border-2 border-dashed p-8 transition-colors ${
                dragging ? "border-primary bg-accent" : "border-border bg-card hover:bg-secondary"
              }`}
            >
              <FolderOpen className="h-10 w-10 text-primary" aria-hidden />
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
            className="w-full resize-y rounded-xl border border-input bg-card px-4 py-3 text-lg leading-relaxed placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
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
            <div className="mt-3 flex flex-wrap items-center gap-3 rounded-xl bg-accent px-4 py-3 text-accent-foreground">
              <FileText className="h-6 w-6" aria-hidden />
              <span className="break-all text-base">Факты и цифры: {corpusName}</span>
              <button
                type="button"
                onClick={onCorpusClear}
                aria-label="Убрать файл с фактами"
                className="ml-auto flex h-11 w-11 items-center justify-center rounded-lg hover:bg-black/10"
              >
                <X />
              </button>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => corpusInput.current?.click()}
              disabled={corpusBusy}
              className="mt-3 inline-flex min-h-[44px] items-center gap-2 rounded-lg border border-border px-4 text-base hover:bg-secondary disabled:opacity-50"
            >
              <Paperclip className="h-5 w-5" aria-hidden />
              {corpusBusy ? "Загружаем файл…" : "Прикрепить файл с фактами и цифрами"}
            </button>
          )}
        </Step>

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
                  className={`min-h-[56px] rounded-xl border-2 px-8 text-xl font-semibold transition-colors ${
                    active
                      ? "border-primary bg-primary text-primary-foreground"
                      : "border-border bg-card hover:bg-secondary"
                  }`}
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

          <div className="mt-6">
            <div className="text-xl font-semibold">Способ сборки</div>
            <div className="mt-2 space-y-2" role="radiogroup" aria-label="Способ сборки">
              {RENDER_MODES.map((mode) => (
                <label key={mode.id}
                       className="flex cursor-pointer items-start gap-3 rounded-xl border border-border bg-card px-4 py-3">
                  <input
                    type="radio"
                    name="render-mode"
                    value={mode.id}
                    checked={renderMode === mode.id}
                    onChange={() => onRenderMode(mode.id)}
                    className="mt-1 h-5 w-5"
                  />
                  <span>
                    <span className="text-lg font-medium">{mode.title}</span>
                    <span className="block text-base text-muted-foreground">— {mode.hint}</span>
                  </span>
                </label>
              ))}
            </div>
          </div>
        </Step>
      </div>

      {advanced}

      {error && (
        <p className="mt-6 rounded-xl bg-destructive/10 px-4 py-3 text-lg text-destructive">
          {error}
        </p>
      )}

      <button
        type="button"
        onClick={onSubmit}
        disabled={!ready || busy}
        className="mt-8 min-h-[64px] w-full rounded-2xl bg-primary px-8 text-2xl font-bold text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-40"
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
  );
}
