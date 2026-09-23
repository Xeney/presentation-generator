/** Контракты API бэкенда (см. backend/app/api/main.py). */

export const VARIANTS = ["compact", "cards", "split"] as const;
export type VariantName = (typeof VARIANTS)[number];

export type Issue = {
  id: string;
  code: string;
  severity: "error" | "warning";
  slide: number;
  message: string;
  bbox: number[];
  deterministic: boolean;
};

export type Audit = {
  passed: boolean;
  errors: number;
  warnings: number;
  total: number;
  issues: Issue[];
};

export type VariantSummary = {
  name: VariantName;
  passed: boolean;
  errors: number;
  warnings: number;
};

export type StageTimings = Record<string, number>;

export type JobSummary = {
  slides: number;
  used_llm: boolean;
  elapsed_s: number;
  variants: VariantSummary[];
  stages?: StageTimings;
  vlm_available?: boolean;
  corpus_id?: string | null;
  version?: number;
  fixes?: number;
};

export type JobState = {
  id: string;
  status: "pending" | "running" | "done" | "error";
  summary?: JobSummary;
  error?: string;
  version?: number;
};

export type FixOutcome = {
  issue_id: string;
  code: string;
  slide: number;
  status: "applied" | "skipped";
  action: string;
  detail: string;
};

export type FixReport = { applied: FixOutcome[]; skipped: FixOutcome[]; version: number };

export type CorpusSlidePreview = {
  index: number;
  heading: string;
  layout: string;
  bullets: string[];
  paragraphs: string[];
  numbers: string[];
  images: string[];
  tables: number;
  charts: number;
};

export type Corpus = {
  id: string;
  source_file: string;
  kind: string;
  stats: { slides: number; non_empty: number; images: number; numbers: number };
  preview?: CorpusSlidePreview[];
  image_keys?: string[];
  warnings?: string[];
};

export type VlmSlide = {
  slide: number;
  ok: boolean;
  violations: number[];
  violations_text: string[];
  summary: string;
};

export type VlmResult = {
  available: boolean;
  model?: string;
  slides: VlmSlide[];
  reason?: string;
  criteria?: Record<string, string>;
  elapsed_s?: number;
  per_slide_s?: number[];
  prompt?: { id: string; version: string; hash: string; model: string };
};

export type GroundingIssue = {
  id: string;
  code: string;
  severity: string;
  slide: number;
  message: string;
  deterministic: boolean;
};

export type GroundingResult = {
  available: boolean;
  reason?: string;
  model?: string;
  issues: GroundingIssue[];
  issues_count?: number;
};

export type ProfileLayout = {
  id: string;
  name: string;
  role: string;
  kind: string;
  role_reason: string;
  score: number;
};

export type JobInfo = {
  profile: {
    source_file: string;
    slide_size: { w_in: number; h_in: number };
    headline_font?: string;
    body_font?: string;
    palette: { hex: string; count: number }[];
    type_scale: { title: number[]; body: number[] };
    layouts: ProfileLayout[];
    layout_groups: Record<string, string[]>;
  };
  deck: { title: string; language: string; slides: { heading: string; slide_type: string }[] };
  planner: { used_llm: boolean; attempts: number };
  corpus?: Corpus | null;
  vlm: VlmResult;
  grounding?: GroundingResult;
  prompts?: Record<string, { version: string; hash: string; model: string; kind: string }>;
  stages?: StageTimings;
  variants: { name: VariantName; audit_summary: VariantSummary }[];
  elapsed_s?: number;
};

export type Health = {
  status: string;
  variants: VariantName[];
  llm: { available: boolean; disabled: boolean; model: string; models: string[] };
  vlm: { enabled: boolean; model: string };
  content_formats: string[];
};

export type GenerateInput = {
  template: File;
  brief: string;
  source: string;
  purpose: string;
  corpusId?: string;
};
