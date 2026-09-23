"use client";

import { useCallback, useEffect, useRef, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const VARIANTS = ["compact", "cards", "split"] as const;

type Issue = { code: string; severity: string; slide: number; message: string; bbox: number[] };
type Audit = { passed: boolean; errors: number; warnings: number; issues: Issue[] };
type Variant = { name: string; passed: boolean; errors: number; warnings: number };
type JobSummary = { slides: number; used_llm: boolean; elapsed_s: number; variants: Variant[] };

type JobResp = { status: string; summary?: JobSummary; error?: string };

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
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (status !== "running" || !jobId) return;
    const t = setInterval(async () => {
      try {
        const j: JobResp = await (await fetch(`${API}/api/jobs/${jobId}`)).json();
        if (j.status === "done" && j.summary) {
          setStatus("done");
          setSummary(j.summary);
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

  const toggleIssue = (code: string) => {
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });
  };

  const fixSelected = () => {
    const notes = audit?.issues
      .filter((i) => selected.has(i.code) && i.severity === "error")
      .map((i) => `[слайд ${i.slide}] ${i.message}`)
      .join("\n");
    if (!notes) {
      setError("Выбери хотя бы одну ошибку для исправления");
      return;
    }
    setSelected(new Set());
    generate(notes);
  };

  const thumbUrl = (name: string, s: number) =>
    `${API}/api/jobs/${jobId}/thumb?variant=${name}&s=${s}&boxes=${showBoxes ? 1 : 0}`;

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
                        <label key={i.code + i.slide + i.message} style={{
                          display: "flex", gap: 8, fontSize: 12.5, lineHeight: 1.45, padding: "6px 8px",
                          borderRadius: 6, marginBottom: 6, background: onSlide ? "#1a1e26" : "transparent",
                          opacity: onSlide ? 1 : 0.45, cursor: "pointer",
                        }}>
                          <input type="checkbox" checked={selected.has(i.code)}
                            onChange={() => toggleIssue(i.code)} />
                          <span>
                            <span style={{ color: i.severity === "error" ? "#f87171" : "#fbbf24" }}>
                              [{i.severity === "error" ? "ошибка" : "замечание"}] слайд {i.slide < 0 ? "все" : i.slide + 1}
                            </span>{" "}
                            {i.message}
                          </span>
                        </label>
                      );
                    })}
                    <button onClick={fixSelected} disabled={selected.size === 0}
                      style={{ ...btn, ...(selected.size === 0 ? { opacity: 0.4 } : {}), marginTop: 8 }}>
                      Исправить выбранное и перегенерировать
                    </button>
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