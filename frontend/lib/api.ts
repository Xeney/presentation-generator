import type {
  Audit,
  Corpus,
  FixReport,
  GenerateInput,
  Health,
  JobInfo,
  JobState,
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
    return fetch(`${API_URL}/api/generate`, { method: "POST", body }).then(
      parse<{ job_id: string; status: string }>,
    );
  },

  job: (id: string) => fetch(`${API_URL}/api/jobs/${id}`).then(parse<JobState>),

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

  downloadUrl: (id: string, kind: "pptx" | "pdf" | "html", variant: VariantName) =>
    kind === "html"
      ? `${API_URL}/api/jobs/${id}/html`
      : `${API_URL}/api/jobs/${id}/${kind}?variant=${variant}`,
};
