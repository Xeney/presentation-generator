"use client";

import { AlignJustify, Columns2, Download, LayoutGrid } from "lucide-react";
import * as React from "react";

import { QualityPanel } from "@/components/quality-panel";
import { api } from "@/lib/api";
import { formatBytes, plural } from "@/lib/audit-labels";
import type {
  Audit,
  AutoFixReport,
  FileInfo,
  JobInfo,
  JobSummary,
  VariantName,
} from "@/lib/types";

const CARDS: {
  variant: VariantName;
  name: string;
  description: string;
  icon: typeof AlignJustify;
  recommended?: boolean;
}[] = [
  {
    variant: "compact",
    name: "Компактный",
    description: "Плотная раскладка, много текста на слайде",
    icon: AlignJustify,
    recommended: true,
  },
  {
    variant: "cards",
    name: "Карточками",
    description: "Каждый блок — отдельная карточка, читается легко",
    icon: LayoutGrid,
  },
  {
    variant: "split",
    name: "С данными отдельно",
    description: "Текст слева, таблицы и графики — сбоку",
    icon: Columns2,
  },
];

/** Скачивание по клику: тот же экран, никаких новых вкладок и просмотрщиков. */
function download(url: string) {
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
}

/** Кнопка скачивания: PPTX — главная (акцент), PDF — вторичная (контур). */
function DownloadButton({
  url,
  label,
  size,
  primary,
  onFailure,
}: {
  url: string;
  label: string;
  size?: string;
  primary: boolean;
  onFailure?: () => void;
}) {
  const [busy, setBusy] = React.useState(false);
  return (
    <button
      type="button"
      disabled={busy}
      onClick={async () => {
        setBusy(true);
        // быстрая проверка: файл вообще собирается? (HEAD не качает тело)
        try {
          const probe = await fetch(url, { method: "HEAD" });
          if (!probe.ok) {
            onFailure?.();
            setBusy(false);
            return;
          }
        } catch {
          onFailure?.();
          setBusy(false);
          return;
        }
        // прямое скачивание: браузер сам берёт имя из Content-Disposition
        download(url);
        setBusy(false);
      }}
      className={
        primary
          ? "btn btn-primary btn-lg w-full text-base"
          : "btn btn-lg w-full border border-border bg-card text-base text-primary hover:bg-secondary"
      }
    >
      <Download className="h-5 w-5" aria-hidden />
      {busy ? "Скачиваем…" : label}
      {size && !busy && (
        <span
          className={`mono text-[12px] font-normal ${
            primary ? "opacity-70" : "text-muted-foreground"
          }`}
        >
          {size}
        </span>
      )}
    </button>
  );
}

function PdfUnavailable() {
  return (
    <div className="notice notice-warning">
      <span className="notice-icon text-[hsl(var(--status-yellow))]" aria-hidden>
        !
      </span>
      <div className="text-base">
        <span className="font-medium">PDF недоступен</span>
        <div className="text-muted-foreground">Попробуйте пересобрать или скачайте PPTX.</div>
      </div>
    </div>
  );
}

export function ResultView({
  jobId,
  elapsed,
  summary,
  formats,
  pdfAvailable,
  audits,
  autoFixes,
  info,
  version,
  onRestart,
  thumbsAvailable,
}: {
  jobId: string;
  elapsed: number;
  summary: JobSummary | null;
  formats: { pptx: boolean; pdf: boolean };
  pdfAvailable: boolean;
  audits: { variant: VariantName; audit: Audit }[] | null;
  autoFixes: AutoFixReport | null;
  info: JobInfo | null;
  version: number;
  onRestart: () => void;
  thumbsAvailable: boolean;
}) {
  const [pdfFailed, setPdfFailed] = React.useState<Set<VariantName>>(new Set());
  const seconds = Math.max(1, Math.round(elapsed));
  const selectedVariants = CARDS.map((card) => card.variant);

  const filesOf = (variant: VariantName): Partial<Record<"pptx" | "pdf", FileInfo>> =>
    info?.variants?.find((item) => item.name === variant)?.files ?? {};

  const pdfState = (variant: VariantName): { ok: boolean; size?: string } => {
    const file = filesOf(variant).pdf;
    const available = file ? file.available : pdfAvailable;
    return { ok: available && !pdfFailed.has(variant), size: formatBytes(file?.bytes) };
  };

  const pptxSizes = selectedVariants
    .map((variant) => filesOf(variant).pptx?.bytes)
    .filter((bytes): bytes is number => Boolean(bytes));
  const pptxKnown = pptxSizes.length === selectedVariants.length;
  const pptxTotal = pptxSizes.reduce((sum, bytes) => sum + bytes, 0);
  const anyPdf = formats.pdf && selectedVariants.some((variant) => pdfState(variant).ok);
  const zipFiles = (formats.pptx ? selectedVariants.length : 0)
    + (anyPdf ? selectedVariants.length : 0);

  const offline = summary?.used_llm === false;

  return (
    <div className="rise">
      <header className="text-center">
        <div className="eyebrow">ГОТОВО / {seconds} СЕК</div>
        <h1 className="mt-2 text-4xl font-bold tracking-tight">
          Готово! Собрано за {seconds} секунд
        </h1>
        <p className="mx-auto mt-3 max-w-2xl text-xl text-muted-foreground">
          Три варианта оформления одного и того же контента. Выберите тот,
          что больше подходит, или скачайте все.
        </p>
        {offline && (
          <div className="notice mx-auto mt-5 max-w-2xl text-left">
            <span className="notice-icon text-[hsl(var(--status-yellow))]" aria-hidden>
              i
            </span>
            <div className="text-base text-muted-foreground">
              Собрано без участия нейросети — быстро, но текст проще. Запустите
              снова с моделью для лучшего результата.
            </div>
          </div>
        )}
      </header>

      <div className="mt-8 grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px] lg:items-start">
        <div className="space-y-6">
          {/* не section: карточки сами секции, обёртка не должна перехватывать поиск */}
          <div>
            <div className="eyebrow">ВАРИАНТЫ ОФОРМЛЕНИЯ</div>
            <div className="mt-3 space-y-4">
              {CARDS.map((card, index) => {
                const Icon = card.icon;
                const files = filesOf(card.variant);
                const pdf = pdfState(card.variant);
                return (
                  <section
                    key={card.variant}
                    className={`panel card-lift rise-${index + 1} flex flex-col gap-4 p-5 sm:flex-row sm:items-center`}
                  >
                    <span className="iconbox shrink-0" aria-hidden>
                      <Icon className="h-6 w-6" />
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="eyebrow">ВАРИАНТ {index + 1}</div>
                      <h2 className="mt-0.5 flex flex-wrap items-center gap-2 text-2xl font-semibold tracking-tight">
                        {card.name}
                        {card.recommended && <span className="badge-acid">Рекомендуем</span>}
                      </h2>
                      <p className="mt-1 text-base text-muted-foreground">
                        {card.description}
                      </p>
                    </div>
                    <div className="flex w-full flex-col gap-3 sm:w-[280px] sm:shrink-0">
                      {formats.pptx && (
                        <DownloadButton
                          url={api.downloadUrl(jobId, "pptx", card.variant, "native")}
                          label="Скачать PPTX"
                          size={formatBytes(files.pptx?.bytes)}
                          primary
                        />
                      )}
                      {formats.pdf &&
                        (pdf.ok ? (
                          <DownloadButton
                            url={api.downloadUrl(jobId, "pdf", card.variant)}
                            label="Скачать PDF"
                            size={pdf.size}
                            primary={false}
                            onFailure={() =>
                              setPdfFailed((previous) => new Set(previous).add(card.variant))
                            }
                          />
                        ) : (
                          <PdfUnavailable />
                        ))}
                    </div>
                  </section>
                );
              })}
            </div>
          </div>

          {audits && (
            <QualityPanel
              jobId={jobId}
              version={version}
              audits={audits}
              autoFixes={autoFixes}
              thumbsAvailable={thumbsAvailable}
            />
          )}
        </div>

        <aside className="space-y-4 lg:sticky lg:top-24">
          <section className="panel p-5">
            <div className="eyebrow">СКАЧАТЬ</div>
            <h2 className="mt-1 text-xl font-semibold tracking-tight">Все файлы сразу</h2>
            <p className="mt-1 text-base text-muted-foreground">
              Один архив: три варианта в выбранных форматах.
            </p>
            {zipFiles > 0 ? (
              <div className="mt-4">
                <button
                  type="button"
                  onClick={() =>
                    download(
                      api.zipUrl(
                        jobId,
                        [
                          ...(formats.pptx ? ["pptx"] : []),
                          ...(anyPdf ? ["pdf"] : []),
                        ],
                        selectedVariants,
                      ),
                    )
                  }
                  className="btn btn-lg w-full border-[rgba(204,255,88,.35)] text-primary"
                >
                  Скачать всё (ZIP)
                </button>
                <div className="mono mt-2 text-center text-[12px] text-muted-foreground">
                  {zipFiles} {plural(zipFiles, "файл", "файла", "файлов")}
                  {formats.pptx && pptxKnown && !anyPdf
                    ? `, всего ${formatBytes(pptxTotal)}`
                    : ""}
                </div>
              </div>
            ) : (
              <p className="mt-3 text-base text-muted-foreground">
                Выберите хотя бы один формат.
              </p>
            )}
            <div className="divider my-4" />
            <button type="button" onClick={onRestart} className="btn w-full">
              Создать ещё раз
            </button>
          </section>

          <section className="panel p-5">
            <div className="eyebrow">ИТОГИ</div>
            <dl className="mt-3 space-y-2 text-base">
              <div className="flex items-center justify-between gap-3">
                <dt className="text-muted-foreground">Слайдов</dt>
                <dd className="mono">{summary?.slides ?? "—"}</dd>
              </div>
              <div className="flex items-center justify-between gap-3">
                <dt className="text-muted-foreground">Время</dt>
                <dd className="mono">{seconds} с</dd>
              </div>
              <div className="flex items-center justify-between gap-3">
                <dt className="text-muted-foreground">Сборка</dt>
                <dd className="mono">{info?.render_mode ?? summary?.render_mode ?? "native"}</dd>
              </div>
              <div className="flex items-center justify-between gap-3">
                <dt className="text-muted-foreground">Нейросеть</dt>
                <dd className="mono text-right">
                  {summary?.used_llm
                    ? summary?.planner_label ?? "модель"
                    : "офлайн-планировщик"}
                </dd>
              </div>
            </dl>
          </section>
        </aside>
      </div>
    </div>
  );
}
