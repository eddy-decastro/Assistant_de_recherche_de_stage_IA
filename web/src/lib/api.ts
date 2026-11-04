import type {
  ArchiveClosedResponse,
  ClaudePackResponse,
  GradeV4Response,
  IgnoreReason,
  IgnoreReasonsResponse,
  IgnoreResponse,
  JobItem,
  JobsQuery,
  JobsResponse,
  LetterResponse,
  QueueAddResponse,
  QueueCurrentResponse,
  QueueItemFull,
  QueueOutcome,
  QueueReportResponse,
  QueueResponse,
  SourcesHealthResponse,
  StatsData,
  UserGradeEntry,
  UserGradePayload,
  UserGradeResponse,
} from "./types";

// Erreur lisible : reprend le `detail` renvoyé par FastAPI quand il existe.
async function request<T>(url: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, init);
  } catch {
    throw new Error("Serveur injoignable : vérifiez que l'API tourne sur le port 8000.");
  }
  if (!res.ok) {
    let detail = `Erreur ${res.status}`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      /* corps non JSON */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

const send = <T>(method: "POST" | "DELETE", url: string, body?: unknown) =>
  request<T>(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

const post = <T>(url: string, body: unknown) => send<T>("POST", url, body);

/** Construit les paramètres de GET /api/jobs (valeurs vides ou indéfinies omises). */
export function jobsParams(query: JobsQuery): URLSearchParams {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;
    params.set(key, String(value));
  }
  return params;
}

const enc = encodeURIComponent;

export const api = {
  /** Liste paginée : accepte des URLSearchParams (existant) ou un JobsQuery typé. */
  jobs: (params: URLSearchParams | JobsQuery) =>
    request<JobsResponse>(`/api/jobs?${params instanceof URLSearchParams ? params : jobsParams(params)}`),
  job: (id: string) => request<JobItem>(`/api/jobs/${enc(id)}`),
  stats: () => request<StatsData>("/api/stats"),
  /** Changement de statut groupé ; `reason` (avec le statut IGNORÉ) = raison d'ignorer en un clic. */
  setStatus: (
    ids: string[],
    status: string,
    rejection_reason: string | null = null,
    opts: { reason?: IgnoreReason; comment?: string } = {},
  ) => post<{ updated: number }>("/api/jobs/bulk-status", { ids, status, rejection_reason, ...opts }),
  ignoreReasons: () => request<IgnoreReasonsResponse>("/api/ignore-reasons"),
  ignore: (id: string, reason: IgnoreReason, comment?: string) =>
    post<IgnoreResponse>(`/api/jobs/${enc(id)}/ignore`, { reason, comment: comment || null }),
  setUserGrade: (id: string, grade: UserGradePayload) =>
    post<UserGradeResponse>(`/api/jobs/${enc(id)}/user-grade`, grade),
  clearUserGrade: (id: string) =>
    post<UserGradeResponse>(`/api/jobs/${enc(id)}/user-grade`, { cls: null, score: null, comment: null }),
  userGrades: () => request<{ grades: UserGradeEntry[] }>("/api/user-grades"),
  /** Archive les offres NOUVEAU d'au moins `days` jours dont la page est FERMÉE (sonde réseau, plus d'âge seul). */
  archiveOld: (days: number, max_checks = 20) =>
    post<ArchiveClosedResponse>("/api/jobs/quick-archive-old", { days, action: "archive", max_checks }),
  saveNotes: (id: string, user_notes: string) => post(`/api/jobs/${enc(id)}/notes`, { user_notes }),
  letter: (id: string, opts: { regenerate?: boolean; custom_instruction?: string; edited_text?: string }) =>
    post<LetterResponse>(`/api/jobs/${enc(id)}/cover-letter`, { regenerate: false, custom_instruction: "", ...opts }),
  claudePack: (ids: string[]) => post<ClaudePackResponse>("/api/claude-pack", { ids, auto_submit: false }),
  gradeV4: (ids: string[]) => post<GradeV4Response>("/api/jobs/grade-v4", { ids: ids.slice(0, 25) }),

  // --- File de candidature Claude in Chrome ---
  queue: (include_applied = true) => request<QueueResponse>(`/api/queue?include_applied=${include_applied}`),
  queueCurrent: () => request<QueueCurrentResponse>("/api/queue/current"),
  queueItem: (id: string) => request<QueueItemFull>(`/api/queue/${enc(id)}`),
  queueAdd: (ids: string[], auto_submit = false) =>
    post<QueueAddResponse>("/api/queue", { ids: ids.slice(0, 50), auto_submit }),
  /** « Marquer postulé » (applied) ou « Échec » (failed) ; compte rendu de 2 phrases / 300 caractères max. */
  queueReport: (id: string, outcome: QueueOutcome, report: string) =>
    post<QueueReportResponse>(`/api/queue/${enc(id)}/report`, { outcome, report }),
  queueRemove: (id: string) => send<{ ok: boolean; id: string }>("DELETE", `/api/queue/${enc(id)}`),

  // --- Santé des sources ---
  sourcesHealth: () => request<SourcesHealthResponse>("/api/sources/health"),
};
