"use client";

import React, { useEffect, useState } from "react";
import { formatStars, splitFlag } from "@/lib/format";
import type { JobItem } from "@/lib/types";
import { ScoreRuler } from "./ScoreRuler";

interface JobDetailProps {
  job: JobItem | null;
  onBack: () => void;
  onLetter: () => void;
  onPack: () => void;
  onApplied: () => void;
  onArchive: () => void;
  onTrash: () => void;
  onGradeV4: () => void;
  onSaveNotes: (text: string) => Promise<void>;
}

export function JobDetail({ job, ...actions }: JobDetailProps) {
  const [notes, setNotes] = useState("");
  const [notesState, setNotesState] = useState<"idle" | "saving" | "saved">("idle");

  useEffect(() => {
    setNotes(job?.user_notes || "");
    setNotesState("idle");
  }, [job?.id, job?.user_notes]);

  if (!job) {
    return (
      <section className="detail-pane">
        <div className="empty">
          <p>Choisissez une offre dans la liste pour lire son évaluation.</p>
        </div>
      </section>
    );
  }

  const flags = job.red_flags.map(splitFlag);
  const hasV4 = Boolean(job.v4_class);

  return (
    <section className="detail-pane" aria-label="Détail de l'offre">
      <button type="button" className="btn ghost back" onClick={actions.onBack}>
        Retour à la liste
      </button>

      <header className="detail-head">
        <div className="detail-titles">
          <p className="detail-company">{job.company}</p>
          <h1>{job.title}</h1>
          <p className="detail-meta">
            {[job.location, job.date_label, job.source_label].filter(Boolean).join(", ")}
          </p>
        </div>
        <ScoreRuler score={job.score} tone={job.align_tone} label={job.align_label} origin={job.score_origin} />
      </header>

      <div className="actions">
        <div className="actions-main">
          {job.url && (
            <a href={job.url} target="_blank" rel="noopener noreferrer" className="btn primary">
              Ouvrir l&apos;offre
            </a>
          )}
          <button type="button" className="btn" onClick={actions.onLetter}>
            Lettre de motivation
          </button>
          <button type="button" className="btn" onClick={actions.onPack}>
            Pack Claude
          </button>
        </div>
        <div className="actions-status" role="group" aria-label="Changer le statut">
          <button type="button" className="btn positive" onClick={actions.onApplied}>
            J&apos;ai postulé
          </button>
          <button type="button" className="btn" onClick={actions.onArchive}>
            Archiver
          </button>
          <button type="button" className="btn ghost alert" onClick={actions.onTrash}>
            Corbeille
          </button>
        </div>
      </div>

      {job.company_note && <p className="note">{job.company_note}</p>}

      {(job.strengths.length > 0 || flags.length > 0) && (
        <section className="block">
          <h2>Verdict</h2>
          {job.strengths.length > 0 && (
            <ul className="points good">
              {job.strengths.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ul>
          )}
          {flags.length > 0 && (
            <ul className="points watch">
              {flags.map((f) => (
                <li key={f.code + f.text}>
                  {f.code && <strong>{f.code}</strong>} {f.text}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {job.sub_scores.length > 0 && (
        <section className="block">
          <h2>Grille d&apos;évaluation</h2>
          <dl className="grid-scores">
            {job.sub_scores.map((s) => (
              <div key={s.key} className="grid-score" title={s.evidence || undefined}>
                <dt>{s.label}</dt>
                <dd>
                  <span className={`pips tone-${s.tone}`} aria-label={`${s.value} sur 5`}>
                    {[1, 2, 3, 4, 5].map((i) => (
                      <i key={i} className={i <= s.value ? "on" : ""} />
                    ))}
                  </span>
                </dd>
                {s.evidence && <p className="quote">« {s.evidence} »</p>}
              </div>
            ))}
          </dl>
        </section>
      )}

      <section className="block">
        <h2>Notation v4</h2>
        {!hasV4 ? (
          <div className="inline-cta">
            <p className="muted">Pas encore notée avec la grille v4 (classe A à E et étoiles par axe).</p>
            <button type="button" className="btn" onClick={actions.onGradeV4}>
              Noter cette offre
            </button>
          </div>
        ) : (
          <>
            <p className="v4-line">
              <span className={`tag tone-${job.v4_class_tone}`}>
                Classe {job.v4_class} · {job.v4_display_score}/100
              </span>
              <span className="muted">{job.v4_class_label}</span>
            </p>
            {job.v4_summary && <p>{job.v4_summary}</p>}
            {job.v4_class_reason && <p className="muted">{job.v4_class_reason}</p>}
            <div className="axes">
              {job.v4_axes.map((ax) => (
                <div key={ax.key} className="axis">
                  <div className="axis-head">
                    <span>{ax.label}</span>
                    <span className={`stars tone-${ax.tone}`} aria-label={`${ax.stars} étoiles sur 5`}>
                      {formatStars(ax.stars)}
                    </span>
                  </div>
                  {ax.reason && <p>{ax.reason}</p>}
                  {ax.quote && <p className="quote">« {ax.quote} »</p>}
                </div>
              ))}
            </div>
            {(job.v4_tags.length > 0 || job.v4_flags.length > 0) && (
              <div className="tags">
                {job.v4_tags.map((t) => (
                  <span key={t.key} className={`tag tone-${t.tone}`}>
                    {t.label}
                  </span>
                ))}
                {job.v4_flags.map((f) => (
                  <span key={f} className="tag tone-warn">
                    {f}
                  </span>
                ))}
              </div>
            )}
          </>
        )}
      </section>

      <section className="block">
        <h2>
          <label htmlFor="notes">Mes notes</label>
        </h2>
        <textarea
          id="notes"
          className="field notes"
          rows={3}
          placeholder="Contact, date de relance, impression…"
          value={notes}
          onChange={(e) => {
            setNotes(e.target.value);
            setNotesState("idle");
          }}
        />
        <div className="row-end">
          {notesState === "saved" && <span className="muted">Notes enregistrées</span>}
          <button
            type="button"
            className="btn"
            disabled={notesState === "saving" || notes === (job.user_notes || "")}
            onClick={async () => {
              setNotesState("saving");
              try {
                await actions.onSaveNotes(notes);
                setNotesState("saved");
              } catch {
                setNotesState("idle");
              }
            }}
          >
            Enregistrer les notes
          </button>
        </div>
      </section>

      {job.technologies.length > 0 && (
        <section className="block">
          <h2>Technologies</h2>
          <div className="tags">
            {job.technologies.map((t) => (
              <span key={t} className="tag">
                {t}
              </span>
            ))}
          </div>
        </section>
      )}

      <section className="block">
        <h2>Fiche de poste</h2>
        <div className="description">{job.description || "La plateforme d'origine ne fournit pas de description."}</div>
      </section>
    </section>
  );
}
