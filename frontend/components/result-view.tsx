"use client";

import { Download, FileText } from "lucide-react";
import * as React from "react";

import { QualityPanel } from "@/components/quality-panel";
import { api } from "@/lib/api";
import type { Audit, AutoFixReport, JobSummary, VariantName } from "@/lib/types";

const CARDS: { variant: VariantName; name: string; description: string }[] = [
  { variant: "compact", name: "Компактный", description: "Плотно, для большого объёма" },
  { variant: "cards", name: "Карточками", description: "По блокам, читается легко" },
  { variant: "split", name: "С данными отдельно", description: "Таблицы и графики — сбоку" },
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

function DownloadRow({
  url,
  label,
  filename,
  onFailure,
}: {
  url: string;
  label: string;
  filename: string;
  onFailure?: () => void;
}) {
  const [busy, setBusy] = React.useState(false);
  return (
    <div className="flex flex-col gap-3 rounded-xl bg-secondary px-4 py-3">
      <div className="min-w-0">
        <div className="text-base">📄 <span className="break-all">{filename}</span></div>
      </div>
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
        className="inline-flex min-h-[52px] w-full items-center justify-center gap-2 rounded-xl bg-primary px-5 text-lg font-semibold text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
      >
        <Download className="h-6 w-6" aria-hidden />
        {busy ? "Скачиваем…" : label}
      </button>
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
  version,
  onVersionChange,
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
  version: number;
  onVersionChange: (version: number) => void;
  onRestart: () => void;
  thumbsAvailable: boolean;
}) {
  const [pdfFailed, setPdfFailed] = React.useState<Set<VariantName>>(new Set());
  const seconds = Math.max(1, Math.round(elapsed));
  const selectedVariants = CARDS.map((card) => card.variant);
  const zipFormats = [
    ...(formats.pptx ? ["pptx"] : []),
    ...(formats.pdf && pdfAvailable ? ["pdf"] : []),
  ];

  return (
    <div>
      <header className="text-center">
        <h1 className="text-4xl font-bold">Готово! Собрано за {seconds} секунд</h1>
        <p className="mt-3 text-xl text-muted-foreground">
          Три варианта оформления одного и того же контента.
          Скачайте тот, что понравится, или все.
        </p>
      </header>

      <div className="mt-8 grid gap-6 lg:grid-cols-3">
        {CARDS.map((card) => {
          const pdfOk = pdfAvailable && !pdfFailed.has(card.variant);
          return (
            <section
              key={card.variant}
              className="flex flex-col rounded-2xl border border-border bg-card p-5"
            >
              <FileText className="h-10 w-10 text-primary" aria-hidden />
              <h2 className="mt-3 text-2xl font-semibold">{card.name}</h2>
              <p className="text-base text-muted-foreground">{card.description}</p>

              <div className="mt-4 space-y-3">
                {formats.pptx && (
                  <DownloadRow
                    url={`${api.downloadUrl(jobId, "pptx", card.variant)}`}
                    label="Скачать PPTX"
                    filename={`presentation_${card.variant}.pptx`}
                  />
                )}
                {formats.pdf && (
                  pdfOk ? (
                    <DownloadRow
                      url={api.downloadUrl(jobId, "pdf", card.variant)}
                      label="Скачать PDF"
                      filename={`presentation_${card.variant}.pdf`}
                      onFailure={() =>
                        setPdfFailed((previous) => new Set(previous).add(card.variant))
                      }
                    />
                  ) : (
                    <div className="rounded-xl bg-muted px-4 py-3 text-base text-muted-foreground">
                      📕 <span className="font-medium">PDF недоступен</span>
                      <div>Скачайте PPTX — он собирается всегда.</div>
                    </div>
                  )
                )}
              </div>
            </section>
          );
        })}
      </div>

      <div className="mt-8 flex flex-col items-center gap-4">
        <button
          type="button"
          onClick={() => download(api.zipUrl(jobId, zipFormats, selectedVariants))}
          className="min-h-[56px] w-full max-w-2xl rounded-2xl border-2 border-primary bg-card px-6 text-xl font-semibold text-primary hover:bg-accent"
        >
          Скачать всё одной кнопкой (ZIP)
        </button>
        <button
          type="button"
          onClick={onRestart}
          className="min-h-[48px] rounded-xl border border-border px-6 text-lg hover:bg-secondary"
        >
          Создать ещё раз
        </button>
      </div>

      {audits && (
        <QualityPanel
          jobId={jobId}
          version={version}
          audits={audits}
          autoFixes={autoFixes}
          thumbsAvailable={thumbsAvailable}
          onVersionChange={onVersionChange}
        />
      )}
    </div>
  );
}
