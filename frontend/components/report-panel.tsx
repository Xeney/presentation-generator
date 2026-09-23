"use client";

import { AlertTriangle, Download, FileCode2, FileText, Presentation } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { Health, JobSummary, VariantName } from "@/lib/types";

const STAGE_LABELS: Record<string, string> = {
  parse_s: "парсинг",
  plan_s: "планирование",
  render_s: "рендер ×3",
  audit_s: "аудит ×3",
  grounding_s: "grounding",
  vlm_s: "VLM",
  fix_s: "фиксы",
  total_s: "итого",
};

export function ReportPanel({
  jobId,
  variant,
  onVariantChange,
  summary,
  health,
  error,
}: {
  jobId: string | null;
  variant: VariantName;
  onVariantChange: (variant: VariantName) => void;
  summary: JobSummary | null;
  health: Health | null;
  error: string;
}) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle>Отчёт</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-xs">
        {error && (
          <p className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-2 text-destructive">
            <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" /> {error}
          </p>
        )}

        {!summary && !error && (
          <p className="text-muted-foreground">
            Загрузи шаблон, напиши бриф и запусти генерацию. Каждый вариант собирается
            нативными объектами PowerPoint на макетах твоего шаблона и проверяется
            детерминированным и контекстуальным аудитом.
          </p>
        )}

        {summary && (
          <>
            <div className="flex flex-wrap items-center gap-2">
              {summary.variants.map((item) => (
                <Button
                  key={item.name}
                  size="sm"
                  variant={variant === item.name ? "default" : "outline"}
                  onClick={() => onVariantChange(item.name)}
                >
                  {item.name}
                  {item.errors === 0 && item.warnings === 0 ? (
                    <Badge variant="success">чисто</Badge>
                  ) : (
                    <Badge variant={item.errors > 0 ? "destructive" : "warning"}>
                      {item.errors}✕ {item.warnings}⚠
                    </Badge>
                  )}
                </Button>
              ))}
            </div>

            <p className="text-muted-foreground">
              слайдов {summary.slides} · время {summary.elapsed_s} c · планировщик:{" "}
              {summary.used_llm ? "Qwen2.5" : "офлайн-fallback"}
            </p>

            {summary.stages && (
              <p className="flex flex-wrap gap-x-3 text-muted-foreground">
                {Object.entries(summary.stages).map(([key, value]) => (
                  <span key={key}>
                    {STAGE_LABELS[key] ?? key} {value}c
                  </span>
                ))}
              </p>
            )}

            <p className="text-muted-foreground">
              VLM-аудит: {summary.vlm_available ? "выполнен" : "недоступен"}
              {summary.corpus_id ? ` · контент-пакет ${summary.corpus_id}` : ""}
              {summary.version && summary.version > 1 ? ` · версия ${summary.version}` : ""}
            </p>

            {jobId && (
              <div className="flex flex-wrap gap-2">
                <a href={api.downloadUrl(jobId, "pptx", variant)} download>
                  <Button size="sm" variant="secondary">
                    <Presentation className="h-3 w-3" /> PPTX
                  </Button>
                </a>
                <a href={api.downloadUrl(jobId, "pdf", variant)} download>
                  <Button size="sm" variant="secondary">
                    <FileText className="h-3 w-3" /> PDF
                  </Button>
                </a>
                <a href={api.downloadUrl(jobId, "html", variant)} target="_blank" rel="noreferrer">
                  <Button size="sm" variant="secondary">
                    <FileCode2 className="h-3 w-3" /> HTML
                  </Button>
                </a>
              </div>
            )}
          </>
        )}

        {health && (
          <div className="flex flex-wrap items-center gap-2 border-t border-border pt-3 text-muted-foreground">
            <span
              className={cn(
                "inline-block h-2 w-2 rounded-full",
                health.llm.available ? "bg-[hsl(var(--success))]" : "bg-[hsl(var(--warning))]",
              )}
            />
            {health.llm.available ? (
              <span>Ollama: {health.llm.model}</span>
            ) : (
              <span>Ollama недоступна — работает офлайн-планировщик</span>
            )}
            <span>· VLM {health.vlm.enabled ? health.vlm.model : "выключен"}</span>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
