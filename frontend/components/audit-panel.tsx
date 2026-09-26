"use client";

import { CheckCircle2, ListChecks, Loader2, Wrench, XCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import type { Audit, FixReport } from "@/lib/types";

function IssueRow({
  issue,
  currentSlide,
  selected,
  onToggle,
}: {
  issue: Audit["issues"][number];
  currentSlide: number;
  selected: Set<string>;
  onToggle: (id: string) => void;
}) {
  const onSlide = issue.slide < 0 || issue.slide === currentSlide;
  return (
    <label
      className={cn(
        "flex cursor-pointer gap-2 rounded-md border p-2 text-xs leading-relaxed transition-colors",
        onSlide ? "border-border bg-muted/40" : "border-transparent opacity-50",
      )}
    >
      <input
        type="checkbox"
        className="mt-0.5 h-3.5 w-3.5 accent-[hsl(var(--primary))]"
        checked={selected.has(issue.id)}
        onChange={() => onToggle(issue.id)}
      />
      <span>
        <span
          className={cn(
            "font-medium",
            issue.severity === "error"
              ? "text-destructive"
              : "text-[hsl(var(--warning))]",
          )}
        >
          {issue.severity === "error" ? "ошибка" : "замечание"}
        </span>
        <span className="text-muted-foreground">
          {" "}
          · слайд {issue.slide < 0 ? "все" : issue.slide + 1}
          {" · "}
          {issue.deterministic ? issue.code : `VLM: ${issue.code}`}
        </span>
        <span className="mt-1 block text-foreground/90">{issue.message}</span>
      </span>
    </label>
  );
}

export function AuditPanel({
  audit,
  selected,
  onToggle,
  onSelectAll,
  onFix,
  fixing,
  fixReport,
  currentSlide,
}: {
  audit: Audit | null;
  selected: Set<string>;
  onToggle: (id: string) => void;
  onSelectAll: () => void;
  onFix: () => void;
  fixing: boolean;
  fixReport: FixReport | null;
  currentSlide: number;
}) {
  // Разделение по природе проверки: детерминированные считаются по геометрии и
  // токенам шаблона (один файл — один результат, годятся для авто-фиксов),
  // контекстуальные — VLM по 11 вопросам ТЗ и эмбеддинги (могут меняться между
  // запусками, авто-фиксов для них нет — только ручное решение).
  const deterministic = (audit?.issues ?? []).filter((i) => i.deterministic);
  const contextual = (audit?.issues ?? []).filter((i) => !i.deterministic);

  return (
    <Card className="flex min-h-0 flex-col">
      <CardHeader className="flex-row items-center justify-between gap-2 pb-2">
        <CardTitle className="flex items-center gap-2">
          <ListChecks className="h-4 w-4 text-primary" /> Аудит
        </CardTitle>
        {audit && (
          <Badge variant={audit.passed ? "success" : "destructive"}>
            {audit.passed ? "пройден" : `${audit.errors} ошибок`}
          </Badge>
        )}
      </CardHeader>
      <CardContent className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto">
        {!audit && (
          <p className="text-xs text-muted-foreground">загрузка аудита…</p>
        )}

        {audit && (
          <section className="flex flex-col gap-2">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Детерминированные проверки · {deterministic.length}
            </p>
            <p className="text-[11px] leading-snug text-muted-foreground">
              геометрия, токены шаблона, контраст, плотность, дубли — считаются по
              файлу, результат повторяем, для них работают авто-фиксы
            </p>
            {deterministic.length === 0 ? (
              <p className="flex items-center gap-2 text-xs text-[hsl(var(--success))]">
                <CheckCircle2 className="h-4 w-4" /> проблем не найдено
              </p>
            ) : (
              deterministic.map((issue) => (
                <IssueRow
                  key={issue.id}
                  issue={issue}
                  currentSlide={currentSlide}
                  selected={selected}
                  onToggle={onToggle}
                />
              ))
            )}
          </section>
        )}

        {audit && (
          <section className="flex flex-col gap-2">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Контекстуальные проверки · {contextual.length}
            </p>
            <p className="text-[11px] leading-snug text-muted-foreground">
              VLM по 11 вопросам Приложения 1 и опора на источник (эмбеддинги):
              оценивают смысл, а не координаты; ответы могут меняться между
              запусками, авто-фиксов нет
            </p>
            {contextual.length === 0 ? (
              <p className="flex items-center gap-2 text-xs text-[hsl(var(--success))]">
                <CheckCircle2 className="h-4 w-4" /> замечаний нет
              </p>
            ) : (
              contextual.map((issue) => (
                <IssueRow
                  key={issue.id}
                  issue={issue}
                  currentSlide={currentSlide}
                  selected={selected}
                  onToggle={onToggle}
                />
              ))
            )}
          </section>
        )}

        {audit && audit.issues.length > 0 && (
          <div className="flex gap-2">
            <Button
              size="sm"
              className="flex-1"
              disabled={selected.size === 0 || fixing}
              onClick={onFix}
            >
              {fixing ? (
                <>
                  <Loader2 className="h-3 w-3 animate-spin" /> применяю…
                </>
              ) : (
                <>
                  <Wrench className="h-3 w-3" /> Исправить выбранное ({selected.size})
                </>
              )}
            </Button>
            <Button size="sm" variant="outline" onClick={onSelectAll}>
              все
            </Button>
          </div>
        )}

        {fixReport && (
          <div className="rounded-md border border-border bg-background/60 p-3 text-xs">
            <p className="font-medium">Авто-фиксы</p>
            <p className="mt-1 flex items-center gap-1 text-[hsl(var(--success))]">
              <CheckCircle2 className="h-3 w-3" /> исправлено: {fixReport.applied.length}
            </p>
            {fixReport.applied.map((item) => (
              <p key={item.issue_id} className="text-muted-foreground">
                слайд {item.slide < 0 ? "все" : item.slide + 1}: {item.action} — {item.detail}
              </p>
            ))}
            {fixReport.skipped.length > 0 && (
              <>
                <p className="mt-2 flex items-center gap-1 text-[hsl(var(--warning))]">
                  <XCircle className="h-3 w-3" /> пропущено: {fixReport.skipped.length}
                </p>
                {fixReport.skipped.map((item) => (
                  <p key={item.issue_id} className="text-muted-foreground">
                    {item.code}: {item.detail}
                  </p>
                ))}
              </>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
