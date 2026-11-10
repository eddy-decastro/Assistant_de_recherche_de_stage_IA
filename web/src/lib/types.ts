export interface V4Axis {
  key: string;
  label: string;
  /** Étoiles à afficher : effectives, ou BRUTES du LLM si l'offre est exclue (voir JobItem.v4_axes_raw). */
  stars: number;
  raw_stars: number;
  effective_stars: number;
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

/** État de la note affichée. */
export type ScoreState = "v4" | "non_notee_v4" | "exclue";

/** Compte rendu de candidature (file Claude in Chrome) renvoyé avec l'offre. */
export interface ApplicationReport {
  state: QueueState;
  /** Compte rendu caveman (2 phrases max) ; "" si aucun. */
  report: string;
  method: string;
  method_label: string;
  needs_account: boolean;
  /** Postulé mais Eddy doit finir (envoi manuel, CV à joindre). */
  to_finish: boolean;
  updated_at: string;
}

export interface JobItem {
  id: string;
  title: string;
  company: string;
  location: string;
  /** Date relative calculée sur published_at (cohérente avec age_days). */
  date_label: string;
  /** Jours depuis published_at (repli : date de collecte). */
  age_days: number | null;
  /** Date de publication ISO ; date de collecte si date_estimee. "" si inconnue. */
  published_at: string;
  /** Vrai si la date de publication est inconnue et remplacée par la date de collecte. */
  date_estimee: boolean;
  /** Date de collecte ISO. */
  created_at: string;
  /** Clé de source brute (linkedin, wttj, ats, workday, academic, jobteaser). */
  source: string;
  source_label: string;
  source_color: string;
  /** Ancien score v3 : NE PLUS AFFICHER (compatibilité). Utiliser display_*. */
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
  cover_letter_version: number | null;
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
  v4_adj_reason: string;
  v4_model: string;
  v4_graded_at: string;

  // --- Note unique affichée (N1/U1) : le front n'affiche plus que celle-ci ---
  /** Note v4 affichée ; null si non notée v4 ou exclue. */
  display_score: number | null;
  /** "A".."E" ou "". */
  display_class: string;
  display_class_label: string;
  display_class_tone: string;
  /** Ex. "B · 82", "Non notée v4", "Exclue". */
  display_label: string;
  display_align_label: string;
  display_align_tone: string;
  score_state: ScoreState;
  score_state_label: string;
  v4_graded: boolean;

  // --- Exclusion et contrat (N2) ---
  excluded: boolean;
  exclusion_reason: string;
  /** Offre exclue : v4_axes[].stars sont les étoiles brutes, à marquer « exclue ». */
  v4_axes_raw: boolean;
  contract_to_verify: boolean;
  contract_type_v4: string;
  contract_quote: string;

  // --- Raison d'ignorer (N3) ---
  /** Code IgnoreReason, rempli seulement si le statut est IGNORÉ. */
  ignore_reason: string;
  ignore_reason_label: string;
  ignore_comment: string;

  // --- Correction de note par Eddy (N6) ---
  user_v4_class: string;
  user_v4_score: number | null;
  user_v4_comment: string;
  /** ISO ; "" si aucune correction. */
  user_v4_at: string;

  // --- Regroupement par entreprise (U2) ---
  /** Clé normalisée ("" si entreprise inconnue). */
  company_key: string;
  /** Nombre d'offres de la même entreprise dans tout le résultat filtré (pas seulement la page). */
  company_count: number;

  // --- File de candidature (C1) ---
  application_report: ApplicationReport | null;
}

export interface KeyLabel {
  key: string;
  label: string;
}

export interface V4ClassInfo extends KeyLabel {
  /** Libellé court (menu de correction, pastilles). */
  short_label: string;
}

export interface SourceInfo extends KeyLabel {
  color: string;
  count: number;
}

export interface StatsData {
  total: number;
  by_status: Record<string, number>;
  by_v4_class: Record<string, number>;
  by_source: SourceInfo[];
  /** Toutes les sources connues (Workday compris), dans l'ordre d'affichage. */
  sources: SourceInfo[];
  old_new_14d: number;
  v3_graded: number;
  v4_graded: number;
  /** Offres NOUVEAU non exclues sans note v4. */
  unscored_v4: number;
  /** Offres exclues (onglet Exclues), tous statuts. */
  excluded: number;
  /** Offres NOUVEAU non exclues avec le drapeau « contrat à vérifier ». */
  contract_to_verify: number;
  v4_classes: V4ClassInfo[];
  v4_tags: KeyLabel[];
  ignore_reasons: IgnoreReasonInfo[];
  user_grade: { score_min: number; score_max: number; comment_max_length: number };
}

export interface JobsResponse {
  /** Nombre total d'offres du résultat filtré. */
  total: number;
  offset: number;
  limit: number;
  /** Vrai s'il reste des offres après cette page (chargement au défilement). */
  has_more: boolean;
  /** Offres exclues avec les mêmes filtres (compteur de l'onglet Exclues). */
  excluded_count: number;
  /** Offres « contrat à vérifier » dans le résultat filtré. */
  contract_to_verify_count: number;
  jobs: JobItem[];
}

/** Paramètres de GET /api/jobs (tous optionnels ; listes séparées par des virgules). */
export interface JobsQuery {
  q?: string;
  statuses?: string;
  sources?: string;
  v4_classes?: string;
  v4_tags?: string;
  exclude_v4_tags?: string;
  /** Ne garder qu'une entreprise (JobItem.company_key ou nom brut, normalisé côté API). */
  company_key?: string;
  min_score?: number;
  /** Borne haute exclusive de la note (onglet « 60 à 79 »). */
  max_score?: number;
  hide_processed?: boolean;
  hide_excluded?: boolean;
  /** Onglet Exclues : seulement les offres exclues (hide_processed est alors ignoré). */
  only_excluded?: boolean;
  hide_defense?: boolean;
  only_older_than_days?: number;
  sort_by?: SortKey;
  offset?: number;
  /** 1..2000, défaut 250. */
  limit?: number;
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
  /** Prompt court de la file (à préférer à chrome_prompt). */
  queue_prompt: string;
  queue_url: string;
  queue: { added: string[]; requeued: string[]; skipped: string[]; enabled: boolean };
  claude_project_json: string;
  bundles: { id: string; title: string; company: string; url: string; pdf_path: string }[];
}

// --- Raisons d'ignorer (N3) ---
export type IgnoreReason =
  | "sujet_hors_cible"
  | "pas_de_vrai_ml"
  | "esn_conseil"
  | "entreprise"
  | "lieu"
  | "dates_duree"
  | "contrat"
  | "encadrement_faible"
  | "doublon"
  | "autre";

export interface IgnoreReasonInfo {
  key: IgnoreReason;
  label: string;
}

export interface IgnoreReasonsResponse {
  reasons: IgnoreReasonInfo[];
  comment_max_length: number;
}

export interface IgnoreResponse {
  ok: boolean;
  id: string;
  status: string;
  ignore_reason: IgnoreReason;
  ignore_reason_label: string;
  ignore_comment: string;
}

// --- Correction de note par Eddy (N6) ---
export type V4Class = "A" | "B" | "C" | "D" | "E";

export interface UserGradePayload {
  /** null ou "" : pas de classe. Tout vide = correction effacée. */
  cls: V4Class | "" | null;
  /** 0..100, ou null. */
  score: number | null;
  /** 500 caractères max. */
  comment: string | null;
}

export interface UserGradeResponse {
  ok: boolean;
  id: string;
  user_v4_class: string;
  user_v4_score: number | null;
  user_v4_comment: string;
  /** "" si la correction a été effacée. */
  user_v4_at: string;
}

export interface UserGradeEntry {
  id: string;
  title: string;
  company: string;
  status: string;
  v4_class: string | null;
  v4_display_score: number | null;
  v4_graded_at: string;
  user_v4_class: string | null;
  user_v4_score: number | null;
  user_v4_comment: string | null;
  user_v4_at: string;
}

// --- File de candidature Claude in Chrome (C1-C3) ---
export type QueueState = "pending" | "failed" | "applied";
export type QueueOutcome = "applied" | "failed";

export interface QueueItem {
  job_id: string;
  position: number;
  state: QueueState;
  /** "" = aucun compte rendu. */
  report: string;
  method: string;
  method_label: string;
  needs_account: boolean;
  auto_submit: boolean;
  title: string;
  company: string;
  location: string;
  url: string;
  status: string | null;
  applied_at: string | null;
  created_at: string;
  updated_at: string;
  to_finish: boolean;
}

export interface QueueCandidate {
  first_name: string;
  last_name: string;
  full_name: string;
  email: string;
  phone: string;
  city: string;
  linkedin: string;
  github: string;
  school: string;
  degree_level: string;
  internship_duration: string;
  start_date: string;
  contract_type: string;
  work_authorization: string;
  cv_path: string;
  cv_pdf_path: string;
}

export interface QueueItemFull extends QueueItem {
  method_hint: string;
  contact_email: string;
  candidate: QueueCandidate;
  /** Accroche de 300 caractères max. */
  short_pitch: string;
  short_motivation: string;
  cover_letter: string;
  /** Question -> réponse standard. */
  answers: Record<string, string>;
  /** Chemins absolus des PDF ("" si absent). */
  files: { cover_letter_pdf: string; cv_pdf: string; offer_dir: string };
  /** Consignes à afficher telles quelles. */
  instructions: string[];
  /** Offres en attente, celle-ci comprise. */
  remaining: number;
}

export interface QueueResponse {
  url: string;
  items: QueueItem[];
  counts: Record<QueueState, number>;
}

export interface QueueCurrentResponse {
  url: string;
  /** null = file vide. */
  current: QueueItemFull | null;
}

export interface QueueAddResponse {
  ok: boolean;
  added: string[];
  requeued: string[];
  skipped: string[];
  errors: Record<string, string>;
  missing: string[];
}

export interface QueueReportResponse {
  ok: boolean;
  item: QueueItem;
}

// --- Santé des sources (S1, S2, S4) ---
export type SourceHealthStatus = "ok" | "degradee" | "en_panne" | "inactive";

export interface BoardHealth {
  board: string;
  /** "ok", "vide", "http_404", "timeout", "network_error", "parse_error", "auth_missing", "error", "non_lance"… */
  status: string;
  label: string;
  detail: string;
}

export interface SourceHealth {
  source: string;
  label: string;
  color: string;
  status: SourceHealthStatus;
  message: string;
  /** Pannes en tête. */
  boards: BoardHealth[];
  stop_reasons: string[];
}

export interface SourcesHealthResponse {
  /** null si aucun run de collecte enregistré. */
  run: { id: string; status: string; started_at: string; finished_at: string } | null;
  sources: SourceHealth[];
}

/** POST /api/jobs/quick-archive-old : offres FERMÉES archivées (plus d'archivage par âge). */
export interface ArchiveClosedResponse {
  ok: boolean;
  updated: number;
  status: string;
  reason: "offre_fermee";
  ids: string[];
}

export interface GradeV4Response {
  ok: boolean;
  results: {
    id: string;
    status: string | null;
    v4_class: string | null;
    v4_display_score: number | null;
    v4_class_score: number | null;
    excluded: boolean;
    contract_to_verify: boolean;
  }[];
}

// « score » = meilleure note v4 (défaut) ; « v3 » et « v4 » en sont des alias (compatibilité) ;
// « v4_class » ; « v3_legacy » = ancien ordre v3 ; « freshness » = date de publication.
export type SortKey = "score" | "v3" | "v4" | "v4_class" | "v3_legacy" | "freshness";

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

// Raison posée automatiquement quand la page de l'offre est fermée (S4).
export const CLOSED_OFFER_REASON = "offre_fermee";
