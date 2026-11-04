"use client";

import React, { useEffect, useState } from "react";
import type { JobItem, UserGradePayload, V4Class, V4ClassInfo } from "@/lib/types";

const CLASSES: V4Class[] = ["A", "B", "C", "D", "E"];

interface UserGradeBoxProps {
  job: JobItem;
  classes: V4ClassInfo[];
  scoreMin: number;
  scoreMax: number;
  commentMax: number;
  onSave: (grade: UserGradePayload) => Promise<void>;
  onClear: () => Promise<void>;
}

// « Ma note » (N6) : Eddy corrige la classe et la note de l'IA ; la correction sert d'exemple
// à l'IA pour les offres suivantes.
export function UserGradeBox({ job, classes, scoreMin, scoreMax, commentMax, onSave, onClear }: UserGradeBoxProps) {
  const [cls, setCls] = useState<V4Class | "">("");
  const [score, setScore] = useState("");
  const [comment, setComment] = useState("");
  const [state, setState] = useState<"idle" | "saving" | "saved" | "cleared">("idle");

  useEffect(() => {
    setCls((job.user_v4_class as V4Class) || "");
    setScore(job.user_v4_score === null || job.user_v4_score === undefined ? "" : String(job.user_v4_score));
    setComment(job.user_v4_comment || "");
  }, [job.id, job.user_v4_class, job.user_v4_score, job.user_v4_comment]);

  useEffect(() => setState("idle"), [job.id]);

  const shortLabel = (k: string) => classes.find((c) => c.key === k)?.short_label ?? "";
  const num = score.trim() === "" ? null : Number(score.replace(",", "."));
  const scoreInvalid = num !== null && (Number.isNaN(num) || num < scoreMin || num > scoreMax);
  const empty = !cls && num === null && !comment.trim();
  const unchanged =
    cls === (job.user_v4_class || "") &&
    num === (job.user_v4_score ?? null) &&
    comment.trim() === (job.user_v4_comment || "").trim();
  const hasSaved = Boolean(job.user_v4_at);

  const ia = job.excluded
    ? "exclue"
    : job.v4_graded && job.v4_class
      ? `${job.v4_class}${job.v4_display_score !== null ? ` ${job.v4_display_score}` : ""}`
      : "non notée";
  const mine = hasSaved ? `${job.user_v4_class || "–"}${job.user_v4_score !== null ? ` ${job.user_v4_score}` : ""}` : "";

  return (
    <section className="block grade-box">
      <h2>Ma note</h2>
      <p className="grade-compare">
        <span>
          IA : <strong>{ia}</strong>
        </span>
        {mine && (
          <span>
            {" · "}Moi : <strong>{mine}</strong>
          </span>
        )}
      </p>
      <p className="muted small">Ta correction sert d&apos;exemple à l&apos;IA pour les prochaines offres.</p>

      <div className="grade-classes" role="radiogroup" aria-label="Ma classe">
        {CLASSES.map((k) => (
          <button
            key={k}
            type="button"
            role="radio"
            aria-checked={cls === k}
            className={`grade-class ${cls === k ? "active" : ""}`}
            title={shortLabel(k)}
            onClick={() => {
              setCls(cls === k ? "" : k);
              setState("idle");
            }}
          >
            <b>{k}</b>
            <span>{shortLabel(k)}</span>
          </button>
        ))}
      </div>

      <div className="grade-fields">
        <label className="field-label grade-score">
          Note ({scoreMin}-{scoreMax})
          <input
            className="field"
            inputMode="numeric"
            value={score}
            aria-invalid={scoreInvalid}
            onChange={(e) => {
              setScore(e.target.value);
              setState("idle");
            }}
            placeholder="70"
          />
        </label>
        <label className="field-label grade-why">
          car …
          <input
            className="field"
            maxLength={commentMax}
            value={comment}
            onChange={(e) => {
              setComment(e.target.value);
              setState("idle");
            }}
            placeholder="ex. vrai sujet de vision embarquée, équipe R&D reconnue"
          />
        </label>
      </div>
      {scoreInvalid && <p className="error-text">La note doit être un nombre entre {scoreMin} et {scoreMax}.</p>}

      <div className="row-end">
        {state === "saved" && <span className="muted">Correction enregistrée</span>}
        {state === "cleared" && <span className="muted">Correction effacée</span>}
        {hasSaved && (
          <button
            type="button"
            className="btn ghost"
            disabled={state === "saving"}
            onClick={async () => {
              setState("saving");
              try {
                await onClear();
                setState("cleared");
              } catch {
                setState("idle");
              }
            }}
          >
            Effacer
          </button>
        )}
        <button
          type="button"
          className="btn"
          disabled={state === "saving" || scoreInvalid || empty || unchanged}
          onClick={async () => {
            setState("saving");
            try {
              await onSave({ cls: cls || null, score: num, comment: comment.trim() || null });
              setState("saved");
            } catch {
              setState("idle");
            }
          }}
        >
          Enregistrer ma note
        </button>
      </div>
    </section>
  );
}
