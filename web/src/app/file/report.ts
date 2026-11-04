// Règles du compte rendu caveman, alignées sur src/matching/apply_queue.py
// (validate_report / count_sentences) pour refuser côté page avant l'appel API.

export const REPORT_MAX_CHARS = 300;
export const REPORT_MAX_SENTENCES = 2;

/** Espaces multiples réduits, comme le serveur. */
export function cleanReport(report: string): string {
  return report.replace(/\s+/g, " ").trim();
}

/** Une phrase se termine par . ! ? … suivi d'un blanc ou de la fin (les points d'une URL ne comptent pas). */
export function countSentences(report: string): number {
  return report
    .trim()
    .split(/[.!?…]+(?=\s|$)/)
    .filter((part) => part.trim()).length;
}

/** Message d'erreur en français, ou null si le compte rendu est valide. */
export function reportError(report: string): string | null {
  const cleaned = cleanReport(report);
  if (!cleaned) return "Compte rendu vide : écris 1 ou 2 phrases courtes.";
  if (cleaned.length > REPORT_MAX_CHARS)
    return `Compte rendu trop long (${cleaned.length} caractères, maximum ${REPORT_MAX_CHARS}).`;
  const sentences = countSentences(cleaned);
  if (sentences > REPORT_MAX_SENTENCES)
    return `Compte rendu trop long (${sentences} phrases, maximum ${REPORT_MAX_SENTENCES}).`;
  return null;
}

/** Copie dans le presse-papiers, avec repli execCommand si l'API n'est pas disponible. */
export async function copyText(value: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(value);
    return true;
  } catch {
    try {
      const area = document.createElement("textarea");
      area.value = value;
      area.setAttribute("readonly", "");
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      const ok = document.execCommand("copy");
      document.body.removeChild(area);
      return ok;
    } catch {
      return false;
    }
  }
}
