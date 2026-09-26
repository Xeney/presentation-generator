"use client";

import * as React from "react";

import { AuditModal } from "@/components/audit-modal";
import { api } from "@/lib/api";
import { fixText, isFixable, isSemantic, issueText, issueWhere } from "@/lib/human";
import type { Audit, AutoFixReport, FixOutcome, Issue, VariantName } from "@/lib/types";

type IssueWithVariant = Issue & { variant: VariantName };

function mergeIssues(audits: { variant: VariantName; audit: Audit }[]): IssueWithVariant[] {
  const seen = new Set<string>();
  const merged: IssueWithVariant[] = [];
  for (const { variant, audit } of audits) {
    for (const issue of audit.issues) {
      const key = `${issue.code}:${issue.slide}`;
      if (seen.has(key)) continue;
      seen.add(key);
      merged.push({ ...issue, variant });
    }
  }
  return merged;
}

function OutcomeList({ title, items }: { title: string; items: FixOutcome[] }) {
  if (!items.length) return null;
  return (
    <div className="text-base">
      <span className="font-medium">{title}: </span>
      {items.map(fixText).join("; ")}.
    </div>
  );
}

export function QualityPanel({
  jobId,
  version,
  audits,
  autoFixes,
  thumbsAvailable,
  onVersionChange,
}: {
  jobId: string;
  version: number;
  audits: { variant: VariantName; audit: Audit }[];
  autoFixes: AutoFixReport | null;
  thumbsAvailable: boolean;
  onVersionChange: (version: number) => void;
}) {
  const [fixing, setFixing] = React.useState(false);
  const [report, setReport] = React.useState<{ applied: FixOutcome[]; skipped: FixOutcome[] } | null>(null);
  const [modal, setModal] = React.useState<{ src: string; caption: string } | null>(null);
  const [error, setError] = React.useState("");

  const issues = React.useMemo(() => mergeIssues(audits), [audits]);
  const fixable = issues.filter((issue) => isFixable(issue.code) && !isSemantic(issue.code));
  const attention = issues.filter((issue) => !isFixable(issue.code) && !isSemantic(issue.code));
  const semantic = issues.filter((issue) => isSemantic(issue.code));

  const total = issues.length;
  const rating = total === 0 ? "отлично" : total <= 3 ? "хорошее" : "требует внимания";
  const ratingClass = total === 0 ? "text-success" : total <= 3 ? "text-foreground" : "text-warning";

  const fixAll = async () => {
    if (!fixable.length || fixing) return;
    setFixing(true);
    setError("");
    try {
      const result = await api.fix(jobId, fixable.map((issue) => issue.id), "compact");
      setReport({ applied: result.applied, skipped: result.skipped });
      onVersionChange(result.version);
    } catch {
      setError("Не получилось исправить автоматически. Попробуйте ещё раз.");
    } finally {
      setFixing(false);
    }
  };

  const showWhere = (issue: IssueWithVariant) => {
    setModal({
      src: api.thumbUrl(jobId, issue.variant, Math.max(0, issue.slide), true, version),
      caption: `${issueText(issue)} · ${issueWhere(issue)}`,
    });
  };

  return (
    <section className="mt-10 rounded-2xl border border-border bg-card p-6">
      <h2 className="text-3xl font-bold">Проверка качества</h2>
      <p className={`mt-2 text-2xl font-semibold ${ratingClass}`}>Качество: {rating}</p>

      {autoFixes && autoFixes.applied.length > 0 && (
        <div className="mt-4 rounded-xl bg-accent p-4 text-accent-foreground">
          <div className="text-lg font-semibold">Исправлено автоматически</div>
          <OutcomeList title="Что сделано" items={autoFixes.applied} />
          <div className="mt-1 text-base">
            Что осталось — смотрите ниже.
          </div>
        </div>
      )}

      {total === 0 && (
        <p className="mt-4 text-xl text-success">Все проверки пройдены. Ошибок нет.</p>
      )}

      {report && (
        <div className="mt-4 rounded-xl bg-secondary p-4">
          <OutcomeList title="Исправлено" items={report.applied} />
          <OutcomeList title="Не удалось исправить" items={report.skipped} />
        </div>
      )}

      {error && <p className="mt-4 text-lg text-destructive">{error}</p>}

      {fixable.length > 0 && (
        <div className="mt-6">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <h3 className="text-2xl font-semibold">Можно исправить автоматически</h3>
            <button
              type="button"
              onClick={fixAll}
              disabled={fixing}
              className="min-h-[48px] rounded-xl bg-primary px-6 text-lg font-semibold text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            >
              {fixing ? "Исправляем…" : "Исправить все"}
            </button>
          </div>
          <ul className="mt-3 space-y-2">
            {fixable.map((issue) => (
              <IssueRow key={`${issue.variant}-${issue.id}`} issue={issue}
                        onShow={() => showWhere(issue)} showButton={thumbsAvailable} />
            ))}
          </ul>
        </div>
      )}

      {attention.length > 0 && (
        <div className="mt-6">
          <h3 className="text-2xl font-semibold">Требуют внимания</h3>
          <ul className="mt-3 space-y-2">
            {attention.map((issue) => (
              <IssueRow key={`${issue.variant}-${issue.id}`} issue={issue}
                        onShow={() => showWhere(issue)} showButton={thumbsAvailable} />
            ))}
          </ul>
        </div>
      )}

      {semantic.length > 0 && (
        <div className="mt-6">
          <h3 className="text-2xl font-semibold">Смысловые замечания</h3>
          <p className="text-base text-muted-foreground">
            Эти оценки может давать нейросеть: они могут меняться от запуска к запуску.
          </p>
          <ul className="mt-3 space-y-2">
            {semantic.map((issue) => (
              <IssueRow key={`${issue.variant}-${issue.id}`} issue={issue}
                        onShow={() => showWhere(issue)} showButton={thumbsAvailable} />
            ))}
          </ul>
        </div>
      )}

      <AuditModal src={modal?.src ?? null} caption={modal?.caption ?? ""}
                  onClose={() => setModal(null)} />
    </section>
  );
}

function IssueRow({
  issue,
  onShow,
  showButton,
}: {
  issue: IssueWithVariant;
  onShow: () => void;
  showButton: boolean;
}) {
  return (
    <li className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-background px-4 py-3">
      <div>
        <div className="text-lg" title={`Код проверки: ${issue.code}`}>
          {issueText(issue)}
        </div>
        <div className="text-base text-muted-foreground">{issueWhere(issue)}</div>
      </div>
      {showButton && issue.slide >= 0 && issue.bbox.length === 4 && (
        <button
          type="button"
          onClick={onShow}
          className="min-h-[44px] rounded-lg border border-border px-4 text-base font-medium hover:bg-secondary"
        >
          Показать, где это
        </button>
      )}
    </li>
  );
}
