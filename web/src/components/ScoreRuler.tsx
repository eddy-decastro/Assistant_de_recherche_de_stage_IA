import type { JobItem } from "@/lib/types";

// Règle 0-100 de la note unique (v4) : situe la note par rapport aux seuils « Pertinent » (60)
// et « Cœur de cible » (80) définis dans utils/data.py (ALIGNMENT_LEVELS).
const THRESHOLDS = [60, 80];

type ScoreJob = Pick<
  JobItem,
  | "score_state"
  | "display_score"
  | "display_class"
  | "display_class_label"
  | "display_class_tone"
  | "display_align_label"
  | "display_align_tone"
  | "exclusion_reason"
>;

/** Pastille compacte de la note unique (liste, cartes) : « B 82 », « non notée » ou « exclue ». */
export function ScorePill({ job }: { job: ScoreJob }) {
  if (job.score_state === "exclue") {
    return (
      <span className="pill pill-none" title={`Exclue : ${job.exclusion_reason || "motif non précisé"}`}>
        exclue
      </span>
    );
  }
  if (job.score_state !== "v4" || job.display_score === null) {
    return (
      <span className="pill pill-none" title="Pas encore notée par l'IA">
        non notée
      </span>
    );
  }
  return (
    <span
      className={`pill tone-${job.display_class_tone || job.display_align_tone}`}
      title={`Classe ${job.display_class} : ${job.display_class_label} · ${job.display_align_label}`}
    >
      <b>{job.display_class}</b> {job.display_score}
    </span>
  );
}

/** Grand affichage de la note dans le détail de l'offre. */
export function ScoreRuler({ job }: { job: ScoreJob }) {
  if (job.score_state === "exclue") {
    return (
      <div className="ruler ruler-off">
        <div className="ruler-figure">
          <span className="ruler-state">Exclue</span>
        </div>
        <p className="muted small">Écartée par l&apos;IA, sans note.</p>
      </div>
    );
  }
  if (job.score_state !== "v4" || job.display_score === null) {
    return (
      <div className="ruler ruler-off">
        <div className="ruler-figure">
          <span className="ruler-state">Non notée</span>
        </div>
        <p className="muted small">L&apos;IA n&apos;a pas encore noté cette offre.</p>
      </div>
    );
  }
  const score = job.display_score;
  const pos = Math.max(0, Math.min(100, score));
  return (
    <div className={`ruler tone-${job.display_align_tone}`} title={`Note ${score}/100, classe ${job.display_class}`}>
      <div className="ruler-figure">
        <span className="ruler-score">
          <span className={`ruler-class tone-${job.display_class_tone}`}>{job.display_class}</span>
          {score}
        </span>
        <span className="ruler-label">
          {job.display_align_label}
          <span className="ruler-sub">{job.display_class_label}</span>
        </span>
      </div>
      <div className="ruler-track" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={score} aria-label="Note de l'offre">
        <div className="ruler-fill" style={{ width: `${pos}%` }} />
        {THRESHOLDS.map((at) => (
          <span key={at} className="ruler-tick" style={{ left: `${at}%` }}>
            <span className="ruler-tick-label">{at}</span>
          </span>
        ))}
      </div>
    </div>
  );
}
