"use client";

import * as React from "react";

import { AdvancedSettings } from "@/components/advanced-settings";
import { GeneratorForm } from "@/components/generator-form";
import { ProgressView } from "@/components/progress-view";
import { ResultView } from "@/components/result-view";
import { api } from "@/lib/api";
import type {
  Audit,
  AutoFixReport,
  Health,
  JobSummary,
  ProviderStatus,
  RenderMode,
  VariantName,
} from "@/lib/types";

type Phase = "form" | "running" | "result";

const VARIANTS: VariantName[] = ["compact", "cards", "split"];
const GENERIC_ERROR =
  "Что-то пошло не так, попробуйте ещё раз. Если не помогает — обновите страницу.";

function humanError(raw: unknown): string {
  const text = raw instanceof Error ? raw.message : String(raw || "");
  if (!text || /failed to fetch|networkerror|load failed|http 5\d\d/i.test(text)) {
    return GENERIC_ERROR;
  }
  return text;
}

export default function Page() {
  const [health, setHealth] = React.useState<Health | null>(null);
  const [phase, setPhase] = React.useState<Phase>("form");
  const [template, setTemplate] = React.useState<File | null>(null);
  const [brief, setBrief] = React.useState("");
  const [corpusId, setCorpusId] = React.useState("");
  const [corpusName, setCorpusName] = React.useState("");
  const [corpusBusy, setCorpusBusy] = React.useState(false);
  const [formats, setFormats] = React.useState({ pptx: true, pdf: true });
  const [renderMode, setRenderMode] = React.useState<RenderMode>("native");
  const [vlm, setVlm] = React.useState(true);
  const [slides, setSlides] = React.useState(12);
  const [language, setLanguage] = React.useState("ru");
  const [provider, setProvider] = React.useState<ProviderStatus | null>(null);
  const [jobId, setJobId] = React.useState<string | null>(null);
  const [version, setVersion] = React.useState(1);
  const [summary, setSummary] = React.useState<JobSummary | null>(null);
  const [audits, setAudits] = React.useState<{ variant: VariantName; audit: Audit }[] | null>(null);
  const [autoFixes, setAutoFixes] = React.useState<AutoFixReport | null>(null);
  const [error, setError] = React.useState("");
  const [elapsed, setElapsed] = React.useState(0);
  const [resultElapsed, setResultElapsed] = React.useState(0);
  const [cancelling, setCancelling] = React.useState(false);
  const startedAt = React.useRef(0);
  const cancelled = React.useRef(false);

  React.useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null));
  }, []);

  const importCorpus = async (file: File) => {
    setCorpusBusy(true);
    setError("");
    try {
      const corpus = await api.importCorpus(file);
      setCorpusId(corpus.id);
      setCorpusName(corpus.source_file || file.name);
    } catch {
      setError("Не получилось прочитать файл с фактами. Попробуйте другой файл.");
    } finally {
      setCorpusBusy(false);
    }
  };

  const start = async () => {
    if (!template) return;
    setError("");
    setSummary(null);
    setAudits(null);
    setAutoFixes(null);
    setVersion(1);
    setElapsed(0);
    cancelled.current = false;
    setPhase("running");
    startedAt.current = Date.now();
    try {
      const job = await api.generate({
        template,
        brief,
        source: "",
        purpose: "project",
        corpusId: corpusId || undefined,
        vlm,
        slides,
        language,
        renderMode,
      });
      setJobId(job.job_id);
    } catch (requestError) {
      setError(humanError(requestError));
      setPhase("form");
    }
  };

  /* ------------------------------------------------------------ опрос задания */
  React.useEffect(() => {
    if (phase !== "running" || !jobId) return;
    const timer = setInterval(async () => {
      setElapsed(Math.floor((Date.now() - startedAt.current) / 1000));
      try {
        const state = await api.job(jobId);
        if (state.status === "cancelled") {
          if (cancelled.current) {
            setPhase("form");
            setJobId(null);
          }
          return;
        }
        if (state.status === "error") {
          setError(state.error ? humanError(state.error) : GENERIC_ERROR);
          setPhase("form");
          return;
        }
        if (state.status !== "done") return;
        setResultElapsed((Date.now() - startedAt.current) / 1000);
        setSummary(state.summary ?? null);
        setVersion(state.summary?.version ?? 1);
        const results = await Promise.all(
          VARIANTS.map(async (variant) => ({ variant, audit: await api.audit(jobId, variant) })),
        );
        if (cancelled.current) return;
        setAudits(results);
        try {
          const info = await api.jobInfo(jobId);
          setAutoFixes(info.auto_fixes ?? null);
        } catch {
          setAutoFixes(null);
        }
        setPhase("result");
      } catch (requestError) {
        setError(humanError(requestError));
        setPhase("form");
      }
    }, 1000);
    return () => clearInterval(timer);
  }, [phase, jobId]);

  /* после авто-фиксов перечитываем отчёты */
  const onVersionChange = async (next: number) => {
    if (!jobId) return;
    setVersion(next);
    try {
      const results = await Promise.all(
        VARIANTS.map(async (variant) => ({ variant, audit: await api.audit(jobId, variant) })),
      );
      setAudits(results);
      const info = await api.jobInfo(jobId);
      setAutoFixes(info.auto_fixes ?? null);
      if (info.variants?.[0]) {
        setSummary((previous) =>
          previous
            ? {
                ...previous,
                variants: info.variants.map((item) => ({
                  name: item.name,
                  passed: item.audit_summary.passed,
                  errors: item.audit_summary.errors,
                  warnings: item.audit_summary.warnings,
                })),
              }
            : previous,
        );
      }
    } catch {
      setError(GENERIC_ERROR);
    }
  };

  const cancel = async () => {
    if (!jobId) return;
    setCancelling(true);
    cancelled.current = true;
    try {
      await api.cancel(jobId);
    } catch {
      /* задание всё равно завершится само — пользователь уже вернулся на форму */
    }
    setJobId(null);
    setPhase("form");
    setCancelling(false);
  };

  const restart = () => {
    setPhase("form");
    setError("");
    setJobId(null);
    setSummary(null);
    setAudits(null);
    setAutoFixes(null);
  };

  return (
    <main className="mx-auto w-full max-w-[800px] px-5 py-10">
      {phase === "form" && (
        <GeneratorForm
          template={template}
          onTemplate={setTemplate}
          brief={brief}
          onBrief={setBrief}
          corpusName={corpusName}
          corpusBusy={corpusBusy}
          onCorpusFile={importCorpus}
          onCorpusClear={() => {
            setCorpusId("");
            setCorpusName("");
          }}
          formats={formats}
          onFormats={setFormats}
          renderMode={renderMode}
          onRenderMode={setRenderMode}
          onSubmit={start}
          busy={false}
          error={error}
          advanced={
            <AdvancedSettings
              health={health}
              vlm={vlm}
              onVlmChange={setVlm}
              slides={slides}
              onSlidesChange={setSlides}
              language={language}
              onLanguageChange={setLanguage}
              renderMode={renderMode}
              onRenderMode={setRenderMode}
              provider={provider}
              onProviderChange={setProvider}
              jobId={jobId}
              version={version}
            />
          }
        />
      )}

      {phase === "running" && (
        <ProgressView elapsed={elapsed} cancelling={cancelling} onCancel={cancel} />
      )}

      {phase === "result" && jobId && (
        <ResultView
          jobId={jobId}
          elapsed={resultElapsed}
          summary={summary}
          formats={formats}
          renderMode={renderMode}
          pdfAvailable={health?.pdf?.available !== false}
          audits={audits}
          autoFixes={autoFixes}
          version={version}
          onVersionChange={onVersionChange}
          onRestart={restart}
          thumbsAvailable={health?.pdf?.available !== false}
        />
      )}
    </main>
  );
}
