// Règle 0-100 : situe le score par rapport aux seuils « Pertinent » (60) et « Cœur de cible » (80)
// définis dans utils/data.py (ALIGNMENT_LEVELS).
const THRESHOLDS = [60, 80];

export function ScoreRuler({ score, tone, label, origin }: { score: number; tone: string; label: string; origin: string }) {
  const pos = Math.max(0, Math.min(100, score));
  return (
    <div className={`ruler tone-${tone}`} title={`Score R&D ${score}/100 (${origin})`}>
      <div className="ruler-figure">
        <span className="ruler-score">{score}</span>
        <span className="ruler-label">{label}</span>
      </div>
      <div className="ruler-track" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={score} aria-label="Score R&D">
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
