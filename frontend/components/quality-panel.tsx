"use client";

import {
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  ChevronDown,
  FileWarning,
  Info,
  Palette,
  Ruler,
  Search,
  Sparkles,
  Type,
  Wrench,
  XCircle,
} from "lucide-react";
import * as React from "react";

import { AuditModal } from "@/components/audit-modal";
import { api } from "@/lib/api";
import {
  DETERMINISTIC_RULES,
  SEMANTIC_QUESTIONS,
  fixActionText,
  isFixable,
  isSemantic,
  issueExplanation,
  issueQuote,
  issueTitle,
  issueWhere,
  labelFor,
} from "@/lib/audit-labels";
import type { Audit, AutoFixReport, Issue, VariantName } from "@/lib/types";

type IssueWithVariant = Issue & { variant: VariantName };

const GROUP_ICON = {
  layout: Ruler,
  text: Type,
  tokens: Palette,
  data: BarChart3,
  source: Search,
  structure: FileWarning,
} as const;

const QUOTE_LIMIT = 150;
const SEMANTIC_VISIBLE = 5;

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

function truncate(text: string, limit: number): string {
  return text.length > limit ? `${text.slice(0, limit).trimEnd()}…` : text;
}

/** Иконка проблемы: по группе проверки (вёрстка, текст, токены, данные, источник). */
function IssueIcon({ code }: { code: string }) {
  const group = isSemantic(code) ? "structure" : labelFor(code).group;
  const Icon = GROUP_ICON[group] || AlertTriangle;
  return <Icon className="h-6 w-6 shrink-0 text-muted-foreground" aria-hidden />;
}

/** Одна карточка проблемы с раскрытием «Подробнее». */
function DecisionCard({
  issue,
  onShow,
  showWhere,
}: {
  issue: IssueWithVariant;
  onShow: () => void;
  showWhere: boolean;
}) {
  const [open, setOpen] = React.useState(false);
  return (
    <li
      className="rounded-2xl border border-border bg-card px-4 py-3"
      style={{ borderLeft: "2px solid hsl(var(--status-orange))" }}
    >
      <div className="flex items-start gap-3">
        <IssueIcon code={issue.code} />
        <div className="min-w-0 flex-1">
          <div className="text-lg font-medium" title={`Код проверки: ${issue.code}`}>
            {issueTitle(issue)}
          </div>
          <div className="mono mt-0.5 text-[12px] uppercase tracking-wider text-muted-foreground">
            {issueWhere(issue)}
          </div>
          <button
            type="button"
            onClick={() => setOpen((value) => !value)}
            aria-expanded={open}
            className="btn btn-sm btn-ghost mt-2 px-2"
          >
            Подробнее
            <ChevronDown
              className={`h-4 w-4 transition-transform ${open ? "rotate-180" : ""}`}
              aria-hidden
            />
          </button>
          {open && (
            <div className="mt-2 space-y-3">
              <p className="text-base text-muted-foreground">{issueExplanation(issue)}</p>
              {showWhere && (
                <button type="button" onClick={onShow} className="btn btn-sm">
                  Показать, где это
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </li>
  );
}

/** Одно смысловое замечание нейросети: цитата + слайд. */
function SemanticCard({
  issue,
  onShow,
  showWhere,
}: {
  issue: IssueWithVariant;
  onShow: () => void;
  showWhere: boolean;
}) {
  const [expanded, setExpanded] = React.useState(false);
  const quote = issueQuote(issue);
  const long = quote.length > QUOTE_LIMIT;
  return (
    <li className="quote">
      <p className="text-lg" title={`Код проверки: ${issue.code}`}>
        «{expanded ? quote : truncate(quote, QUOTE_LIMIT)}»
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-3">
        <span className="mono text-[12px] uppercase tracking-wider text-muted-foreground">
          {issueWhere(issue)}
        </span>
        {long && (
          <button
            type="button"
            onClick={() => setExpanded((value) => !value)}
            className="btn btn-sm btn-ghost px-2 text-primary"
          >
            {expanded ? "Свернуть" : "Развернуть"}
          </button>
        )}
        {showWhere && (
          <button type="button" onClick={onShow} className="btn btn-sm btn-ghost px-2 text-primary">
            Показать, где это
          </button>
        )}
      </div>
    </li>
  );
}

export function QualityPanel({
  jobId,
  version,
  audits,
  autoFixes,
  thumbsAvailable,
}: {
  jobId: string;
  version: number;
  audits: { variant: VariantName; audit: Audit }[];
  autoFixes: AutoFixReport | null;
  thumbsAvailable: boolean;
}) {
  const [modal, setModal] = React.useState<{ src: string; caption: string } | null>(null);
  const [showAllSemantic, setShowAllSemantic] = React.useState(false);

  const issues = React.useMemo(() => mergeIssues(audits), [audits]);
  const semantic = issues.filter((issue) => isSemantic(issue.code));
  const decision = issues.filter((issue) => !isSemantic(issue.code));
  const errors = decision.filter((issue) => issue.severity === "error");
  const fixableErrorsLeft = errors.filter((issue) => isFixable(issue.code));
  const unfixableErrors = errors.filter((issue) => !isFixable(issue.code));
  const problems = errors.length;
  const notes = issues.length - problems;

  const status = issues.length === 0 ? "clean"
    : fixableErrorsLeft.length > 0 ? "critical"
      : unfixableErrors.length > 0 ? "attention"
        : "minor";

  const STATUSES = {
    clean: { label: "✓ Всё чисто", className: "status-green", icon: CheckCircle2 },
    minor: {
      label: "✓ Хорошо, есть мелкие замечания",
      className: "status-yellow",
      icon: CheckCircle2,
    },
    attention: {
      label: "⚠ Есть замечания",
      className: "status-orange",
      icon: AlertTriangle,
    },
    critical: {
      label: "✗ Есть критичные проблемы",
      className: "status-red",
      icon: XCircle,
    },
  } as const;
  const current = STATUSES[status];
  const StatusIcon = current.icon;

  const fixed = autoFixes?.applied ?? [];
  const semanticVisible = showAllSemantic
    ? semantic
    : semantic.slice(0, SEMANTIC_VISIBLE);

  const showWhere = (issue: IssueWithVariant) => {
    setModal({
      src: api.thumbUrl(jobId, issue.variant, Math.max(0, issue.slide), true, version),
      caption: `${issueTitle(issue)} · ${issueWhere(issue)}`,
    });
  };

  return (
    <section className="panel rise mt-10 p-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <div className="eyebrow">АУДИТ / АВТОМАТИЧЕСКИЙ</div>
          <h2 className="mt-1 text-3xl font-bold tracking-tight">Проверка качества</h2>
        </div>
        <div
          className={`${current.className} inline-flex items-center gap-2 rounded-xl px-4 py-2 text-xl font-semibold`}
        >
          <StatusIcon className="h-6 w-6" aria-hidden />
          {current.label}
        </div>
      </div>

      <p className="mt-4 text-lg text-muted-foreground">
        Проверено {DETERMINISTIC_RULES} правил вёрстки и {SEMANTIC_QUESTIONS} смысловых
        вопросов.{" "}
        {problems === 0 && notes === 0
          ? "Ничего не найдено."
          : `Найдено: ${problems} проблем, ${notes} замечаний.`}
      </p>

      {fixed.length > 0 && (
        <div className="status-green text-foreground mt-6 rounded-2xl px-5 py-4">
          <h3 className="flex items-center gap-2 text-xl font-semibold text-[hsl(var(--status-green))]">
            <Wrench className="h-6 w-6" aria-hidden />
            Исправлено автоматически
          </h3>
          <ul className="mt-2 list-disc space-y-1 pl-6 text-base">
            {fixed.map((outcome, index) => (
              <li key={`${outcome.issue_id}-${index}`}>{fixActionText(outcome)}</li>
            ))}
          </ul>
        </div>
      )}

      {decision.length > 0 && (
        <div className="mt-6">
          <h3 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
            <AlertTriangle
              className="h-6 w-6 text-[hsl(var(--status-orange))]"
              aria-hidden
            />
            Требует вашего решения
          </h3>
          <p className="mt-1 text-base text-muted-foreground">
            Эти замечания нельзя исправить автоматически — посмотрите слайд
            и решите, критично ли это.
          </p>
          <ul className="mt-3 space-y-3">
            {decision.map((issue) => (
              <DecisionCard
                key={`${issue.variant}-${issue.id}`}
                issue={issue}
                onShow={() => showWhere(issue)}
                showWhere={thumbsAvailable && issue.slide >= 0 && issue.bbox.length === 4}
              />
            ))}
          </ul>
        </div>
      )}

      {semantic.length > 0 && (
        <div className="status-blue text-foreground mt-6 rounded-2xl px-5 py-4">
          <h3 className="flex items-center gap-2 text-2xl font-semibold tracking-tight text-[hsl(var(--status-blue))]">
            <Sparkles className="h-6 w-6" aria-hidden />
            Смысловые замечания нейросети
          </h3>
          <p className="mt-1 text-base text-muted-foreground">
            Эти оценки даёт нейросеть. Она может ошибаться и отвечать по-разному
            при повторных запусках — решайте сами, критично ли это.
          </p>
          <ul className="mt-3 space-y-3">
            {semanticVisible.map((issue) => (
              <SemanticCard
                key={`${issue.variant}-${issue.id}`}
                issue={issue}
                onShow={() => showWhere(issue)}
                showWhere={thumbsAvailable && issue.slide >= 0 && issue.bbox.length === 4}
              />
            ))}
          </ul>
          {semantic.length > SEMANTIC_VISIBLE && !showAllSemantic && (
            <button
              type="button"
              onClick={() => setShowAllSemantic(true)}
              className="btn btn-sm mt-3"
            >
              <Info className="h-4 w-4" aria-hidden />
              Показать все ({semantic.length})
            </button>
          )}
        </div>
      )}

      <AuditModal src={modal?.src ?? null} caption={modal?.caption ?? ""}
                  onClose={() => setModal(null)} />
    </section>
  );
}
