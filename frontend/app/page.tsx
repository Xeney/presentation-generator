"use client";

import * as React from "react";

import { AuditPanel } from "@/components/audit-panel";
import { BriefPanel } from "@/components/brief-panel";
import { ContextPanel } from "@/components/context-panel";
import { DeckViewer } from "@/components/deck-viewer";
import { ReportPanel } from "@/components/report-panel";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import type {
  Audit,
  FixReport,
  GenerateInput,
  Health,
  JobInfo,
  JobSummary,
  VariantName,
} from "@/lib/types";

type Status = "idle" | "running" | "done" | "error";

export default function Page() {
  const [health, setHealth] = React.useState<Health | null>(null);
  const [jobId, setJobId] = React.useState<string | null>(null);
  const [status, setStatus] = React.useState<Status>("idle");
  const [summary, setSummary] = React.useState<JobSummary | null>(null);
  const [info, setInfo] = React.useState<JobInfo | null>(null);
  const [audit, setAudit] = React.useState<Audit | null>(null);
  const [error, setError] = React.useState("");
  const [variant, setVariant] = React.useState<VariantName>("compact");
  const [version, setVersion] = React.useState(1);
  const [selected, setSelected] = React.useState<Set<string>>(new Set());
  const [fixing, setFixing] = React.useState(false);
  const [fixReport, setFixReport] = React.useState<FixReport | null>(null);
  const [slideIndex, setSlideIndex] = React.useState(0);

  const refreshHealth = React.useCallback(async () => {
    try {
      setHealth(await api.health());
    } catch {
      setHealth(null);
    }
  }, []);

  React.useEffect(() => {
    refreshHealth();
  }, [refreshHealth]);

  // Deep-link на готовое задание: /?job=<id> — для демо и разбора на защите
  // (открыть заранее прогнанную колоду без повторной генерации).
  React.useEffect(() => {
    const requested = new URLSearchParams(window.location.search).get("job");
    if (requested && !jobId) {
      setJobId(requested);
      setStatus("running");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** План Б на демо: смена провайдера без перезапуска сервиса. */
  const switchProvider = async (llm: string, vlm: string) => {
    setError("");
    try {
      await api.switchProvider({ llm_provider: llm, vlm_provider: vlm });
      await refreshHealth();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : String(requestError));
    }
  };

  /* ---------------------------------------------------- опрос задания */
  React.useEffect(() => {
    if (status !== "running" || !jobId) return;
    const timer = setInterval(async () => {
      try {
        const state = await api.job(jobId);
        if (state.status === "done" && state.summary) {
          setSummary(state.summary);
          setVersion(state.summary.version ?? 1);
          setStatus("done");
        } else if (state.status === "error") {
          setError(state.error ?? "не удалось собрать колоду");
          setStatus("error");
        }
      } catch (requestError) {
        setError(requestError instanceof Error ? requestError.message : String(requestError));
        setStatus("error");
      }
    }, 800);
    return () => clearInterval(timer);
  }, [status, jobId]);

  /* ------------------------------------- аудит и инфо текущего варианта */
  React.useEffect(() => {
    if (status !== "done" || !jobId) return;
    let cancelled = false;
    (async () => {
      try {
        const [nextAudit, nextInfo] = await Promise.all([
          api.audit(jobId, variant),
          api.jobInfo(jobId),
        ]);
        if (cancelled) return;
        setAudit(nextAudit);
        setInfo(nextInfo);
      } catch (requestError) {
        if (!cancelled) {
          setError(requestError instanceof Error ? requestError.message : String(requestError));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [status, jobId, variant, version]);

  const startGeneration = async (input: GenerateInput) => {
    setError("");
    setSummary(null);
    setAudit(null);
    setInfo(null);
    setFixReport(null);
    setSelected(new Set());
    setSlideIndex(0);
    setStatus("running");
    try {
      const job = await api.generate(input);
      setJobId(job.job_id);
      setVersion(1);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : String(requestError));
      setStatus("error");
    }
  };

  const toggleIssue = (id: string) => {
    setSelected((previous) => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const applyFixes = async () => {
    if (!jobId || selected.size === 0) return;
    setFixing(true);
    setError("");
    try {
      const report = await api.fix(jobId, Array.from(selected), variant);
      setFixReport(report);
      setSelected(new Set());
      setVersion(report.version);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : String(requestError));
    } finally {
      setFixing(false);
    }
  };

  const slidesCount = info?.deck.slides.length ?? summary?.slides ?? 0;
  const deckTitle = info?.deck.title ?? "";

  return (
    <div className="grid h-screen grid-cols-[360px_minmax(0,1fr)_380px] overflow-hidden">
      <aside className="flex flex-col gap-4 overflow-y-auto border-r border-border p-4">
        <BriefPanel onGenerate={startGeneration} running={status === "running"} />
        <ReportPanel
          jobId={jobId}
          variant={variant}
          onVariantChange={(next) => {
            setVariant(next);
            setSlideIndex(0);
          }}
          summary={summary}
          health={health}
          error={error}
          onProviderChange={switchProvider}
        />
      </aside>

      <main className="flex min-w-0 flex-col gap-3 overflow-hidden p-4">
        <header className="flex items-center gap-3">
          <h1 className="truncate text-sm font-semibold">
            {deckTitle || "Колода появится здесь"}
          </h1>
          {slidesCount > 0 && <Badge variant="secondary">{slidesCount} слайдов</Badge>}
          {status === "running" && <Badge variant="warning">генерация…</Badge>}
        </header>

        {status === "done" && jobId ? (
          <DeckViewer
            jobId={jobId}
            variant={variant}
            version={version}
            slidesCount={slidesCount}
            index={slideIndex}
            onIndexChange={setSlideIndex}
          />
        ) : (
          <div className="flex flex-1 items-center justify-center rounded-lg border border-dashed border-border p-8 text-center text-xs text-muted-foreground">
            {status === "running"
              ? "Сервис разбирает шаблон, планирует колоду, верстает три варианта и аудитирует их…"
              : "Загрузи шаблон, напиши бриф и запусти генерацию: получишь три варианта вёрстки, аудит и экспорт в PPTX, PDF и HTML."}
          </div>
        )}
      </main>

      <aside className="flex min-h-0 flex-col gap-3 overflow-hidden border-l border-border p-4">
        <AuditPanel
          audit={audit}
          selected={selected}
          onToggle={toggleIssue}
          onSelectAll={() => setSelected(new Set((audit?.issues ?? []).map((i) => i.id)))}
          onFix={applyFixes}
          fixing={fixing}
          fixReport={fixReport}
          currentSlide={slideIndex}
        />
        <ContextPanel info={info} />
      </aside>
    </div>
  );
}
