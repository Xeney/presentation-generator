import type {
  Audit,
  Corpus,
  FixReport,
  GenerateInput,
  Health,
  JobInfo,
  JobState,
  ProviderCheck,
  ProviderStatus,
  RenderMode,
  VariantName,
} from "./types";

export const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

async function parse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      detail = body.detail || detail;
    } catch {
      /* тело может быть пустым — оставляем код */
    }
    throw new ApiError(detail, response.status);
  }
  return (await response.json()) as T;
}

export const api = {
  health: () => fetch(`${API_URL}/api/health`).then(parse<Health>),

  switchProvider: (body: { llm_provider?: string; vlm_provider?: string }) =>
    fetch(`${API_URL}/api/provider`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(
      parse<{
        applied: Record<string, string>;
        llm: { provider: string; model: string; label: string; available: boolean };
        vlm: { provider: string; model: string; label: string; available: boolean };
      }>,
    ),

  importCorpus: (file: File) => {
    const body = new FormData();
    body.append("file", file);
    return fetch(`${API_URL}/api/content/import`, { method: "POST", body }).then(parse<Corpus>);
  },

  generate: (input: GenerateInput) => {
    const body = new FormData();
    body.append("template", input.template);
    body.append("brief", input.brief);
    body.append("source", input.source);
    body.append("purpose", input.purpose);
    if (input.corpusId) body.append("corpus_id", input.corpusId);
    body.append("vlm", input.vlm === false ? "off" : "on");
    if (input.slides) body.append("slides", String(input.slides));
    if (input.language) body.append("language", input.language);
    if (input.renderMode) body.append("render_mode", input.renderMode);
    return fetch(`${API_URL}/api/generate`, { method: "POST", body }).then(
      parse<{ job_id: string; status: string }>,
    );
  },

  job: (id: string) => fetch(`${API_URL}/api/jobs/${id}`).then(parse<JobState>),

  cancel: (id: string) =>
    fetch(`${API_URL}/api/jobs/${id}/cancel`, { method: "POST" }).then(
      parse<{ status: string }>,
    ),

  providerStatus: () =>
    fetch(`${API_URL}/api/provider/status`).then(
      parse<{ provider: ProviderStatus; label: string; local_label: string }>,
    ),

  providerTest: (body: { base_url: string; api_key: string; model: string }) =>
    fetch(`${API_URL}/api/provider/test`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(parse<ProviderCheck>),

  providerSet: (body: { base_url: string; api_key: string; model: string }) =>
    fetch(`${API_URL}/api/provider/set`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(parse<ProviderCheck & { label?: string }>),

  providerReset: () =>
    fetch(`${API_URL}/api/provider/reset`, { method: "POST" }).then(
      parse<{ provider: ProviderStatus; label: string }>,
    ),

  zipUrl: (id: string, formats: string[], variants: string[]) =>
    `${API_URL}/api/jobs/${id}/download?formats=${formats.join(",")}&variants=${variants.join(",")}`,

  jobInfo: (id: string) => fetch(`${API_URL}/api/jobs/${id}/info`).then(parse<JobInfo>),

  audit: (id: string, variant: VariantName) =>
    fetch(`${API_URL}/api/jobs/${id}/audit?variant=${variant}`).then(parse<Audit>),

  fix: (id: string, issueIds: string[], variant: VariantName) =>
    fetch(`${API_URL}/api/jobs/${id}/fix`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ issue_ids: issueIds, variant }),
    }).then(parse<FixReport & { summary?: unknown }>),

  thumbUrl: (id: string, variant: VariantName, page: number, boxes: boolean, version: number) =>
    `${API_URL}/api/jobs/${id}/thumb?variant=${variant}&s=${page}&boxes=${boxes ? 1 : 0}&v=${version}`,

  downloadUrl: (id: string, kind: "pptx" | "pdf" | "html", variant: VariantName,
                render: RenderMode | "native" = "native") =>
    kind === "html"
      ? `${API_URL}/api/jobs/${id}/html`
      : `${API_URL}/api/jobs/${id}/${kind}?variant=${variant}&render=${render}`,

  /** HTML-колода варианта: открывается в браузере (ADR-035). */
  htmlUrl: (id: string, variant: VariantName) =>
    `${API_URL}/api/jobs/${id}/html?variant=${variant}`,
};
