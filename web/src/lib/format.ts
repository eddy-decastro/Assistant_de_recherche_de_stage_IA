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
