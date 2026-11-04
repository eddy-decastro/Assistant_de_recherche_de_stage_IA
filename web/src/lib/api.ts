import type { ClaudePackResponse, JobsResponse, LetterResponse, StatsData } from "./types";

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

const post = <T>(url: string, body: unknown) =>
  request<T>(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

export const api = {
  jobs: (params: URLSearchParams) => request<JobsResponse>(`/api/jobs?${params}`),
  stats: () => request<StatsData>("/api/stats"),
  setStatus: (ids: string[], status: string, rejection_reason: string | null = null) =>
    post<{ updated: number }>("/api/jobs/bulk-status", { ids, status, rejection_reason }),
  archiveOld: (days: number) =>
    post<{ updated: number; ids: string[] }>("/api/jobs/quick-archive-old", { days, action: "archive" }),
  saveNotes: (id: string, user_notes: string) => post(`/api/jobs/${id}/notes`, { user_notes }),
  letter: (id: string, opts: { regenerate?: boolean; custom_instruction?: string; edited_text?: string }) =>
    post<LetterResponse>(`/api/jobs/${id}/cover-letter`, { regenerate: false, custom_instruction: "", ...opts }),
  claudePack: (ids: string[]) => post<ClaudePackResponse>("/api/claude-pack", { ids, auto_submit: false }),
  gradeV4: (ids: string[]) => post("/api/jobs/grade-v4", { ids: ids.slice(0, 25) }),
};
