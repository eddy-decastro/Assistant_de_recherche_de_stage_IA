"use client";

import React, { useEffect, useState } from "react";
import { formatDate, formatStars, splitFlag } from "@/lib/format";
import { STATUS, type ApplicationReport, type JobItem, type UserGradePayload, type V4ClassInfo } from "@/lib/types";
import { ApplicationReportLine } from "./ApplicationReportLine";
import { ScoreRuler } from "./ScoreRuler";
import { UserGradeBox } from "./UserGradeBox";

interface JobDetailProps {
  job: JobItem | null;
  classes: V4ClassInfo[];
  gradeBounds: { score_min: number; score_max: number; comment_max_length: number };
  onBack: () => void;
  onLetter: () => void;
  onPack: () => void;
  onApplied: () => void;
  onIgnore: () => void;
  onTrash: () => void;
  onReinstate: () => void;
  onGradeV4: () => void;
  onSaveNotes: (text: string) => Promise<void>;
  onSaveGrade: (grade: UserGradePayload) => Promise<void>;
  onClearGrade: () => Promise<void>;
}

// Date de l'offre dans l'en-tête : vraie date de publication, ou date de collecte signalée comme telle.
function detailDate(job: JobItem): string {
  const d = formatDate(job.published_at);
  if (!d) return job.date_label;
  return job.date_estimee ? `collectée le ${d} (date de publication inconnue)` : `publiée le ${d} (${job.date_label.toLowerCase()})`;
}

export function JobDetail({ job, classes, gradeBounds, ...actions }: JobDetailProps) {
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
  const hasV4 = job.v4_graded && Boolean(job.v4_class);
  const rawAxes = job.v4_axes_raw;
  const report: ApplicationReport | null = job.application_report;

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
            {job.location && <span>{job.location}</span>}
            <span className={job.date_estimee ? "date-est" : undefined}>{detailDate(job)}</span>
            {job.source_label && (
              <span className="source">
                <i className="source-dot" style={{ background: job.source_color || undefined }} aria-hidden="true" />
                {job.source_label}
              </span>
            )}
          </p>
        </div>
        <ScoreRuler job={job} />
      </header>

      {job.excluded && (
        <p className="note note-alert">
          <strong>Offre exclue.</strong> {job.exclusion_reason || "Motif non précisé."}{" "}
          <button type="button" className="btn ghost" onClick={actions.onReinstate}>
            Remettre à trier
          </button>
        </p>
      )}
      {job.contract_to_verify && (
        <p className="note note-warn">
          <strong>Contrat à vérifier.</strong> L&apos;IA pense à un{" "}
          {job.contract_type_v4 === "ALTERNANCE" ? "contrat d'alternance" : job.contract_type_v4 === "CDI_CDD" ? "CDI ou CDD" : "contrat autre qu'un stage"}{" "}
          sans le prouver par une citation : l&apos;offre reste notée.
          {job.contract_quote && <span className="quote"> « {job.contract_quote} »</span>}
        </p>
      )}
      {job.status === STATUS.archived && job.ignore_reason && (
        <p className="note">
          <strong>Ignorée : {job.ignore_reason_label || job.ignore_reason}.</strong> {job.ignore_comment}
        </p>
      )}

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
          <button type="button" className="btn positive" onClick={actions.onApplied} title="Raccourci : P">
            J&apos;ai postulé
          </button>
          <button type="button" className="btn" onClick={actions.onIgnore} title="Raccourci : I">
            Ignorer…
          </button>
          <button type="button" className="btn ghost alert" onClick={actions.onTrash}>
            Corbeille
          </button>
        </div>
      </div>

      {report && (
        <section className="block">
          <h2>Candidature via la file Claude</h2>
          <ApplicationReportLine report={report} />
        </section>
      )}

      {job.company_note && <p className="note">{job.company_note}</p>}

      <section className="block">
        <h2>Évaluation de l&apos;IA</h2>
        {!hasV4 ? (
          <div className="inline-cta">
            <p className="muted">Pas encore notée (classe A à E et étoiles par axe).</p>
            <button type="button" className="btn" onClick={actions.onGradeV4}>
              Noter cette offre
            </button>
          </div>
        ) : (
          <>
            <p className="v4-line">
              <span className={`tag tone-${job.v4_class_tone}`}>
                Classe {job.v4_class}
                {!job.excluded && job.display_score !== null ? ` · ${job.display_score}/100` : ""}
              </span>
              <span className="muted">{job.v4_class_label}</span>
            </p>
            {job.v4_summary && <p>{job.v4_summary}</p>}
            {job.v4_class_reason && <p className="muted">{job.v4_class_reason}</p>}
            {rawAxes && (
              <p className="muted small axes-raw-note">
                Étoiles brutes de l&apos;IA, non comptées : l&apos;offre est exclue.
              </p>
            )}
            <div className={`axes ${rawAxes ? "axes-raw" : ""}`}>
              {job.v4_axes.map((ax) => (
                <div key={ax.key} className="axis">
                  <div className="axis-head">
                    <span>{ax.label}</span>
                    <span
                      className={`stars tone-${rawAxes ? "mute" : ax.tone}`}
                      aria-label={`${ax.stars} étoiles sur 5${rawAxes ? " (brutes, offre exclue)" : ""}`}
                    >
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

      <UserGradeBox
        job={job}
        classes={classes}
        scoreMin={gradeBounds.score_min}
        scoreMax={gradeBounds.score_max}
        commentMax={gradeBounds.comment_max_length}
        onSave={actions.onSaveGrade}
        onClear={actions.onClearGrade}
      />

      {(job.strengths.length > 0 || flags.length > 0) && (
        <section className="block">
          <h2>Points forts et points de vigilance</h2>
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

      {job.sub_scores.length > 0 && (
        <details className="block legacy">
          <summary>Ancienne grille v3 (archive, ne compte plus)</summary>
          <dl className="grid-scores">
            {job.sub_scores.map((s) => (
              <div key={s.key} className="grid-score" title={s.evidence || undefined}>
                <dt>{s.label}</dt>
                <dd>
                  <span className="pips tone-mute" aria-label={`${s.value} sur 5`}>
                    {[1, 2, 3, 4, 5].map((i) => (
                      <i key={i} className={i <= s.value ? "on" : ""} />
                    ))}
                  </span>
                </dd>
                {s.evidence && <p className="quote">« {s.evidence} »</p>}
              </div>
            ))}
          </dl>
        </details>
      )}
    </section>
  );
}
