"use client";

import { useCallback, useEffect, useRef, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const VARIANTS = ["compact", "cards", "split"] as const;

type Issue = {
  id: string; code: string; severity: string; slide: number; message: string;
  bbox: number[]; deterministic: boolean;
};
type FixOutcome = { issue_id: string; code: string; slide: number; status: string; action: string; detail: string };
type Audit = { passed: boolean; errors: number; warnings: number; issues: Issue[] };
type Variant = { name: string; passed: boolean; errors: number; warnings: number };
type JobSummary = {
  slides: number; used_llm: boolean; elapsed_s: number; variants: Variant[];
  stages?: Record<string, number>; vlm_available?: boolean; corpus_id?: string | null;
};

type JobResp = { status: string; summary?: JobSummary; error?: string };

type CorpusSlide = {
  index: number; heading: string; layout: string; bullets: string[];
  paragraphs: string[]; numbers: string[]; images: string[]; tables: number; charts: number;
};
type Corpus = {
  id: string; source_file: string; kind: string;
  stats: { slides: number; non_empty: number; images: number; numbers: number };
  preview?: CorpusSlide[];
  image_keys?: string[];
};

export default function Page() {
  const [templateName, setTemplateName] = useState("");
  const [brief, setBrief] = useState(
    "Компания запускает мобильное приложение для доставки еды. За полгода охват — 3 города, " +
    "ежемесячная аудитория 150 000 человек, средний чек 870 руб. Снижение времени доставки на 18%. " +
    "Цель — выйти на 10 городов к концу года и повысить LTV на 25%."
  );
  const [source, setSource] = useState("");
  const [purpose, setPurpose] = useState("project");
  const [jobId, setJobId] = useState<string | null>(null);
  const [status, setStatus] = useState<"idle" | "running" | "done" | "error">("idle");
  const [summary, setSummary] = useState<JobSummary | null>(null);
  const [error, setError] = useState("");
  const [variant, setVariant] = useState<(typeof VARIANTS)[number]>("compact");
  const [audit, setAudit] = useState<Audit | null>(null);
  const [slideIdx, setSlideIdx] = useState(0);
  const [showBoxes, setShowBoxes] = useState(true);
  const [deckTitle, setDeckTitle] = useState("");
  const [slidesCount, setSlidesCount] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [version, setVersion] = useState(1);
  const [fixing, setFixing] = useState(false);
  const [fixReport, setFixReport] = useState<{ applied: FixOutcome[]; skipped: FixOutcome[] } | null>(null);
  const [corpus, setCorpus] = useState<Corpus | null>(null);
  const [corpusError, setCorpusError] = useState("");
  const [importing, setImporting] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const corpusRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (status !== "running" || !jobId) return;
    const t = setInterval(async () => {
      try {
        const j: JobResp = await (await fetch(`${API}/api/jobs/${jobId}`)).json();
        if (j.status === "done" && j.summary) {
          setStatus("done");
          setSummary(j.summary);
          setVersion(j.summary.version || 1);
          const first = j.summary.variants[0];
          if (first) setVariant(first.name as typeof VARIANTS[number]);
        } else if (j.status === "error") {
          setStatus("error");
          setError(j.error || "Ошибка генерации");
        }
      } catch (e) {
        setStatus("error");
        setError(String(e));
      }
    }, 700);
    return () => clearInterval(t);
  }, [status, jobId]);

  const loadAudit = useCallback(async (name: string, job: string) => {
    try {
      const r = await fetch(`${API}/api/jobs/${job}/audit?variant=${name}`);
      if (r.ok) setAudit(await r.json());
    } catch {
      /* аудит подтянется при явном выборе */
    }
  }, []);

  useEffect(() => {
    if (status !== "done" || !jobId) return;
    const p = fetch(`${API}/api/jobs/${jobId}/info`).then((r) => r.json());
    p.then((info) => {
      if (info && info.deck) {
        setDeckTitle(info.deck.title || "");
        setSlidesCount(info.deck.slides?.length || 0);
      }
    }).catch(() => {});
  }, [status, jobId]);

  const importCorpus = async () => {
    const file = corpusRef.current?.files?.[0];
    if (!file) {
      setCorpusError("Выбери файл контент-пакета (PPTX, DOCX, TXT или MD)");
      return;
    }
    setImporting(true);
    setCorpusError("");
    try {
      const fd = new FormData();
      fd.append("file", file);
      const r = await fetch(`${API}/api/content/import`, { method: "POST", body: fd });
      const j = await r.json();
      if (!r.ok) {
        setCorpusError(j.detail || "Не удалось разобрать контент-пакет");
        setCorpus(null);
      } else {
        setCorpus(j);
      }
    } catch (e) {
      setCorpusError(String(e));
    } finally {
      setImporting(false);
    }
  };

  const generate = async (extraNotes = "") => {
    if (!fileRef.current?.files?.[0]) {
      setError("Загрузи шаблон PPTX");
      return;
    }
    setError("");
    const fd = new FormData();
    fd.append("template", fileRef.current.files[0]);
    fd.append("brief", brief);
    fd.append("source", (source || "" ) + (extraNotes ? "\n\nИсправления от аудита:\n" + extraNotes : ""));
    fd.append("purpose", purpose);
    if (corpus) fd.append("corpus_id", corpus.id);
    const r = await fetch(`${API}/api/generate`, { method: "POST", body: fd });
    const j = await r.json();
    if (!r.ok) {
      setStatus("error");
      setError(j.detail || "Не удалось запустить генерацию");
      return;
    }
    setJobId(j.job_id);
    setStatus("running");
    setSummary(null);
    setAudit(null);
    setSlideIdx(0);
  };

  const toggleIssue = (id: string) => {
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const fixSelected = async () => {
    if (!jobId || selected.size === 0) {
      setError("Отметь хотя бы одну проблему для исправления");
      return;
    }
    setFixing(true);
    setError("");
    try {
      const r = await fetch(`${API}/api/jobs/${jobId}/fix`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ issue_ids: Array.from(selected), variant }),
      });
      const j = await r.json();
      if (!r.ok) {
        setError(j.detail || "Не удалось применить фиксы");
      } else {
        setFixReport({ applied: j.applied, skipped: j.skipped });
        setSelected(new Set());
        setVersion(j.version);
        if (j.summary) setSummary((prev) => (prev ? { ...prev, ...j.summary } : prev));
        await loadAudit(variant, jobId);
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setFixing(false);
    }
  };

  const thumbUrl = (name: string, s: number) =>
    `${API}/api/jobs/${jobId}/thumb?variant=${name}&s=${s}&boxes=${showBoxes ? 1 : 0}&v=${version}`;

  const thumb = (name: string, s: number, active: boolean) => (
    <img
      key={s}
      src={thumbUrl(name, s)}
      alt={`слайд ${s + 1}`}
      onClick={() => setSlideIdx(s)}
      style={{
        width: "100%", borderRadius: 8, border: active ? "2px solid #4f8cff" : "1px solid #2a2d34",
        boxSizing: "border-box", marginBottom: 8, cursor: "pointer",
      }}
    />
  );

  return (
    <main style={{ display: "flex", height: "100vh", minHeight: 700 }}>
      {/* ---------------- левая панель ---------------- */}
      <aside style={{ width: 380, background: "#12141a", borderRight: "1px solid #23262e", padding: 20, overflowY: "auto", flexShrink: 0 }}>
        <h1 style={{ fontSize: 20, margin: "0 0 4px" }}>Цифровой дизайнер презентаций</h1>
        <p style={{ opacity: 0.55, fontSize: 13, margin: "0 0 20px" }}>VK Tech · хакатон · генерация по брифу на произвольном шаблоне</p>

        <label style={lab}>Шаблон PPTX *</label>
        <input
          ref={fileRef}
          type="file"
          accept=".pptx"
          onChange={(e) => setTemplateName(e.target.files?.[0]?.name || "")}
          style={input}
        />
        <div style={{ fontSize: 12, opacity: 0.6, marginBottom: 12 }}>{templateName || "ни один файл не выбран"}</div>

        <label style={lab}>Контент-пакет (необязательно)</label>
        <input ref={corpusRef} type="file" accept=".pptx,.docx,.txt,.md" style={input} />
        <button
          onClick={importCorpus}
          disabled={importing}
          style={{ ...btn, background: "#2b3240", marginTop: 8, opacity: importing ? 0.6 : 1 }}
        >
          {importing ? "Разбираю…" : "Импортировать контент-пакет"}
        </button>
        {corpusError && <div style={{ color: "#ff6b6b", fontSize: 12, marginTop: 6 }}>{corpusError}</div>}
        {corpus && (
          <div style={{ marginTop: 8, fontSize: 12, background: "#0e1014", border: "1px solid #23262e", borderRadius: 8, padding: 10 }}>
            <div style={{ opacity: 0.9 }}>{corpus.source_file} · {corpus.kind.toUpperCase()}</div>
            <div style={{ opacity: 0.65 }}>
              слайдов {corpus.stats.non_empty} · картинок {corpus.stats.images} · цифр {corpus.stats.numbers}
            </div>
            <div style={{ maxHeight: 140, overflowY: "auto", marginTop: 6 }}>
              {(corpus.preview || []).slice(0, 6).map((s) => (
                <div key={s.index} style={{ opacity: 0.8, marginBottom: 4 }}>
                  <b>{s.index + 1}.</b> {s.heading}
                  {s.bullets.length > 0 && <span style={{ opacity: 0.6 }}> · тезисов {s.bullets.length}</span>}
                  {s.images.length > 0 && <span style={{ opacity: 0.6 }}> · фото {s.images.length}</span>}
                  {s.tables > 0 && <span style={{ opacity: 0.6 }}> · таблиц {s.tables}</span>}
                </div>
              ))}
            </div>
          </div>
        )}

        <label style={lab}>Бриф</label>
        <textarea value={brief} onChange={(e) => setBrief(e.target.value)} rows={6} style={{ ...input, fontFamily: "inherit" }} />

        <label style={lab}>Исходные материалы (необязательно)</label>
        <textarea value={source} onChange={(e) => setSource(e.target.value)} rows={3} style={{ ...input, fontFamily: "inherit" }} />

        <label style={lab}>Цель презентации</label>
        <select value={purpose} onChange={(e) => setPurpose(e.target.value)} style={input}>
          <option value="project">Проект: цели, объём, команда, сроки</option>
          <option value="feature">Новый продукт / фича</option>
          <option value="product">Продукт и его рынок</option>
          <option value="initiative">Инициатива: цели, аудитория, этапы</option>
        </select>

        <button
          onClick={() => generate()}
          disabled={status === "running"}
          style={{ ...btn, background: "#4f8cff", marginTop: 16, opacity: status === "running" ? 0.6 : 1 }}
        >
          {status === "running" ? "Генерация…" : "Сгенерировать 3 варианта"}
        </button>
        {error && <div style={{ color: "#ff6b6b", fontSize: 13, marginTop: 10 }}>{error}</div>}

        {status === "running" && <div style={progress}>Сервис строит колоду, верстает 3 варианта и аудитирует…</div>}

        {summary && (
          <div style={{ marginTop: 18, fontSize: 13, lineHeight: 1.7 }}>
            <div style={{ fontWeight: 600 }}>Отчёт</div>
            <div>Слайдов: {summary.slides} · время {summary.elapsed_s} c · LLM: {summary.used_llm ? "да" : "offline-fallback"}</div>
            {summary.stages && (
              <div style={{ opacity: 0.6, fontSize: 12 }}>
                {Object.entries(summary.stages).map(([k, v]) => `${k.replace("_s", "")} ${v}c`).join(" · ")}
              </div>
            )}
            <div style={{ opacity: 0.6, fontSize: 12 }}>
              VLM-аудит: {summary.vlm_available ? "выполнен" : "недоступен (нет Ollama/VLM)"}
              {summary.corpus_id ? ` · контент-пакет ${summary.corpus_id}` : ""}
            </div>
            {summary.variants.map((v) => (
              <div key={v.name} style={{ opacity: 0.85 }}>
                {v.name}: {v.errors === 0 && v.warnings === 0 ? "✓ чисто" : `ошибок ${v.errors}, предупреждений ${v.warnings}`}
              </div>
            ))}
            <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
              <a href={`${API}/api/jobs/${jobId}/pptx?variant=${variant}`} style={linkBtn}>PPTX</a>
              <a href={`${API}/api/jobs/${jobId}/pdf?variant=${variant}`} style={linkBtn}>PDF</a>
              <a href={`${API}/api/jobs/${jobId}/html`} style={linkBtn} target="_blank">HTML</a>
            </div>
          </div>
        )}
      </aside>

      {/* ---------------- основная область ---------------- */}
      <section style={{ flex: 1, padding: 20, overflowY: "auto", display: "flex", flexDirection: "column", minWidth: 640 }}>
        {status !== "done" ? (
          <div style={{ display: "grid", placeItems: "center", flex: 1, opacity: 0.45, textAlign: "center" }}>
            {status === "running"
              ? "Ждём завершения…"
              : "Загрузи шаблон, напиши бриф и запусти генерацию.\n\nКаждый вариант собирается нативными объектами PowerPoint на макетах твоего шаблона и проверяется детерминированным и VLM-аудитом."}
          </div>
        ) : (
          <>
            <div style={{ display: "flex", gap: 12, alignItems: "center", marginBottom: 14, flexWrap: "wrap" }}>
              <h2 style={{ fontSize: 17, margin: 0 }}>{deckTitle || "Колода"}</h2>
              <span style={{ opacity: 0.6, fontSize: 13 }}>{slidesCount} слайдов</span>
              <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
                {VARIANTS.map((v) => {
                  const vi = summary?.variants.find((x) => x.name === v);
                  return (
                    <button
                      key={v}
                      onClick={() => { setVariant(v); setSlideIdx(0); loadAudit(v, jobId!); }}
                      style={{
                        ...tab, borderColor: variant === v ? "#4f8cff" : "#23262e",
                        color: variant === v ? "#fff" : "inherit", background: variant === v ? "#1c2433" : "transparent",
                      }}
                    >
                      {v}
                      {vi ? (vi.errors || vi.warnings ? ` ✕${vi.errors}⚠${vi.warnings}` : " ✓") : ""}
                    </button>
                  );
                })}
              </div>
              <label style={{ fontSize: 13, opacity: 0.85, display: "flex", gap: 6, alignItems: "center" }}>
                <input type="checkbox" checked={showBoxes} onChange={(e) => setShowBoxes(e.target.checked)} />
                рамки проблем
              </label>
            </div>

            <div style={{ display: "flex", gap: 20, flex: 1, minHeight: 0 }}>
              {/* список слайдов */}
              <div style={{ width: 210, overflowY: "auto", flexShrink: 0, paddingRight: 6 }}>
                {Array.from({ length: slidesCount || summary?.slides || 0 }).map((_, i) => (
                  <div key={i} onClick={() => setSlideIdx(i)} style={{ marginBottom: 6, cursor: "pointer" }}>
                    {thumb(variant, i, i === slideIdx)}
                  </div>
                ))}
              </div>
              {/* главный слайд */}
              <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
                <div style={{ flex: 1, display: "grid", placeItems: "center", minHeight: 300 }}>
                  <div style={{ width: "100%", maxWidth: 900, boxShadow: "0 8px 30px rgba(0,0,0,.5)", borderRadius: 10 }}>
                    {thumb(variant, slideIdx, true)}
                  </div>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 10 }}>
                  <button disabled={slideIdx === 0} onClick={() => setSlideIdx(slideIdx - 1)} style={navBtn}>←</button>
                  <span style={{ fontSize: 13, opacity: 0.8 }}>слайд {slideIdx + 1} из {slidesCount || summary?.slides}</span>
                  <button disabled={slideIdx >= (slidesCount || 0) - 1} onClick={() => setSlideIdx(slideIdx + 1)} style={navBtn}>→</button>
                </div>
              </div>
              {/* аудит */}
              <div style={{ width: 330, overflowY: "auto", borderLeft: "1px solid #23262e", paddingLeft: 16, flexShrink: 0 }}>
                <h3 style={{ fontSize: 14, margin: "0 0 10px" }}>Детерминированный аудит</h3>
                {audit ? (
                  <>
                    <div style={{ fontSize: 13, marginBottom: 10 }}>
                      {audit.passed ? (
                        <span style={{ color: "#4ade80" }}>✓ прошёл</span>
                      ) : (
                        <span style={{ color: "#f87171" }}>✕ найдено {audit.errors} ошибок, {audit.warnings} замечаний</span>
                      )}
                    </div>
                    {audit.issues.length === 0 && <div style={{ opacity: 0.5, fontSize: 13 }}>Проблем не найдено.</div>}
                    {audit.issues.map((i) => {
                      const onSlide = i.slide < 0 || i.slide === slideIdx;
                      return (
                        <label key={i.id} style={{
                          display: "flex", gap: 8, fontSize: 12.5, lineHeight: 1.45, padding: "6px 8px",
                          borderRadius: 6, marginBottom: 6, background: onSlide ? "#1a1e26" : "transparent",
                          opacity: onSlide ? 1 : 0.45, cursor: "pointer",
                        }}>
                          <input type="checkbox" checked={selected.has(i.id)}
                            onChange={() => toggleIssue(i.id)} />
                          <span>
                            <span style={{ color: i.severity === "error" ? "#f87171" : "#fbbf24" }}>
                              [{i.severity === "error" ? "ошибка" : "замечание"}] слайд {i.slide < 0 ? "все" : i.slide + 1}
                            </span>{" "}
                            {i.message}
                          </span>
                        </label>
                      );
                    })}
                    <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
                      <button onClick={fixSelected} disabled={selected.size === 0 || fixing}
                        style={{ ...btn, ...(selected.size === 0 || fixing ? { opacity: 0.4 } : {}), flex: 1 }}>
                        {fixing ? "Применяю фиксы…" : `Исправить выбранное (${selected.size})`}
                      </button>
                      <button
                        onClick={() => setSelected(new Set(audit.issues.map((i) => i.id)))}
                        style={{ ...tab, background: "#1c2433", color: "#cfe3ff" }}>
                        все
                      </button>
                    </div>
                    {fixReport && (
                      <div style={{ marginTop: 12, fontSize: 12, background: "#121a22", border: "1px solid #23262e", borderRadius: 8, padding: 10 }}>
                        <div style={{ fontWeight: 600 }}>Авто-фиксы</div>
                        <div style={{ color: "#4ade80" }}>исправлено: {fixReport.applied.length}</div>
                        {fixReport.applied.map((f) => (
                          <div key={f.issue_id} style={{ opacity: 0.85 }}>
                            слайд {f.slide < 0 ? "все" : f.slide + 1}: {f.action} — {f.detail}
                          </div>
                        ))}
                        {fixReport.skipped.length > 0 && (
                          <>
                            <div style={{ color: "#fbbf24", marginTop: 6 }}>
                              пропущено: {fixReport.skipped.length}
                            </div>
                            {fixReport.skipped.map((f) => (
                              <div key={f.issue_id} style={{ opacity: 0.7 }}>
                                {f.code}: {f.detail}
                              </div>
                            ))}
                          </>
                        )}
                      </div>
                    )}
                  </>
                ) : (
                  <div style={{ opacity: 0.5, fontSize: 13 }}>загрузка аудита…</div>
                )}
              </div>
            </div>
          </>
        )}
      </section>
    </main>
  );
}

const lab: React.CSSProperties = { display: "block", fontSize: 12, opacity: 0.7, margin: "12px 0 4px" };
const input: React.CSSProperties = {
  width: "100%", background: "#0e1014", color: "#e8eaed", border: "1px solid #23262e",
  borderRadius: 8, padding: 9, fontSize: 13, boxSizing: "border-box",
};
const btn: React.CSSProperties = {
  width: "100%", color: "#fff", border: "none", borderRadius: 8, padding: "11px 14px",
  fontSize: 14, fontWeight: 600, cursor: "pointer",
};
const tab: React.CSSProperties = {
  border: "1px solid", borderRadius: 8, padding: "7px 12px", fontSize: 13, cursor: "pointer", background: "transparent",
};
const navBtn: React.CSSProperties = {
  background: "#1c2433", color: "#fff", border: "1px solid #2a2d34", borderRadius: 8, padding: "6px 14px", cursor: "pointer",
};
const linkBtn: React.CSSProperties = {
  background: "#1c2433", color: "#cfe3ff", border: "1px solid #2a2d34", borderRadius: 6, padding: "6px 12px",
  fontSize: 13, textDecoration: "none",
};
const progress = { color: "#4f8cff", fontSize: 13, marginTop: 12, opacity: 0.85 } as const;