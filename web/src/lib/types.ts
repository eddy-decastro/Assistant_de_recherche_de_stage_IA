export interface V4Axis {
  key: string;
  label: string;
  stars: number;
  reason: string;
  quote: string;
  unknown: boolean;
  tone: string;
}

export interface V4Tag {
  key: string;
  label: string;
  tone: string;
}

export interface SubScore {
  key: string;
  label: string;
  short: string;
  value: number;
  tone: string;
  evidence: string;
}

export interface JobItem {
  id: string;
  title: string;
  company: string;
  location: string;
  date_label: string;
  age_days: number | null;
  source_label: string;
  source_color: string;
  score: number;
  score_origin: string;
  align_label: string;
  align_tone: string;
  reranked: boolean;
  verdict_label: string;
  verdict_tone: string;
  status: string;
  status_label: string;
  is_new: boolean;
  hard_cap: string;
  badges: { label: string; tone: string }[];
  sub_scores: SubScore[];
  technologies: string[];
  strengths: string[];
  red_flags: string[];
  reasoning: string;
  description: string;
  rejection_reason: string;
  url: string;
  company_note: string;
  questions_entretien: string[];
  user_notes: string;
  cover_letter: string;
  applied_at: string;
  v4_class: string;
  v4_class_label: string;
  v4_class_tone: string;
  v4_display_score: number | null;
  v4_class_score: number | null;
  v4_center: number | null;
  v4_adjustment: number | null;
  v4_axes: V4Axis[];
  v4_tags: V4Tag[];
  v4_flags: string[];
  v4_summary: string;
  v4_class_reason: string;
}

export interface StatsData {
  total: number;
  by_status: Record<string, number>;
  by_v4_class: Record<string, number>;
  old_new_14d: number;
  v3_graded: number;
  v4_graded: number;
  v4_classes: { key: string; label: string }[];
  v4_tags: { key: string; label: string }[];
}

export interface JobsResponse {
  total: number;
  jobs: JobItem[];
}

export interface LetterResponse {
  cover_letter: string;
  source: string;
  warnings: string[];
}

export interface ClaudePackResponse {
  count: number;
  output_dir: string;
  chrome_prompt: string;
  claude_project_json: string;
  bundles: { id: string; title: string; company: string; url: string; pdf_path: string }[];
}

export type SortKey = "v3" | "v4" | "v4_class" | "freshness";

// Codes de statut tels qu'enregistrés en base (src/constants.py).
export const STATUS = {
  new: "NOUVEAU",
  applied: "POSTULÉ",
  interview: "ENTRETIEN",
  archived: "IGNORÉ",
  rejected: "REJETÉ",
} as const;

// Un REJETÉ n'est un refus d'entreprise que s'il porte ce motif ; sinon c'est la corbeille.
export const REFUSAL_REASON = "Refus de l'entreprise";
