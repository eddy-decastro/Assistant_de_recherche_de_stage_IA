export function formatStars(n: number): string {
  const s = Math.max(0, Math.min(5, Math.round(n || 0)));
  return "★".repeat(s) + "☆".repeat(5 - s);
}

// « [CALENDRIER_DECALE] Date de début… » -> { code: "Calendrier décalé", text: "Date de début…" }
export function splitFlag(raw: string): { code: string; text: string } {
  const m = raw.match(/^\s*\[([A-Z0-9_]+)\]\s*(.*)$/s);
  if (!m) return { code: "", text: raw.trim() };
  const words = m[1].toLowerCase().replace(/_/g, " ");
  const code = words.charAt(0).toUpperCase() + words.slice(1);
  return { code: code.replace(/\bdecale\b/, "décalé").replace(/\bidf\b/, "IDF"), text: m[2].trim() };
}

export function daysSince(iso: string): number | null {
  if (!iso) return null;
  const t = Date.parse(iso.replace(" ", "T"));
  if (Number.isNaN(t)) return null;
  return Math.max(0, Math.floor((Date.now() - t) / 86_400_000));
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n > 1 ? many : one}`;
}

// « 2026-10-13T00:00:00 » -> « 13 oct. 2026 » ; "" si la date est illisible.
export function formatDate(iso: string): string {
  if (!iso) return "";
  const t = Date.parse(iso.replace(" ", "T"));
  if (Number.isNaN(t)) return "";
  return new Date(t).toLocaleDateString("fr-FR", { day: "numeric", month: "short", year: "numeric" });
}

// Date relative d'une offre : « Il y a 22 j », ou « Collectée il y a 22 j » quand la date de
// publication est inconnue (date_estimee) et remplacée par la date de collecte.
export function jobDateLabel(job: { date_label: string; date_estimee: boolean }): string {
  if (!job.date_label) return "";
  if (!job.date_estimee) return job.date_label;
  return `Collectée ${job.date_label.charAt(0).toLowerCase()}${job.date_label.slice(1)}`;
}

export function jobDateTitle(job: { published_at: string; date_estimee: boolean }): string {
  const d = formatDate(job.published_at);
  if (!d) return "Date inconnue";
  return job.date_estimee ? `Date de publication inconnue : collectée le ${d}` : `Publiée le ${d}`;
}
