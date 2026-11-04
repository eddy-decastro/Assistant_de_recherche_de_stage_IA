"use client";

import React from "react";
import { daysSince } from "@/lib/format";
import { REFUSAL_REASON, STATUS, type JobItem } from "@/lib/types";
import { ApplicationReportLine } from "./ApplicationReportLine";
import { ScorePill } from "./ScoreRuler";

// Une colonne = un statut. « Refusé » ne garde que les refus d'entreprise : la corbeille n'apparaît pas ici.
export const BOARD_COLUMNS = [
  { key: "applied", label: "Postulé", status: STATUS.applied, reason: null },
  { key: "interview", label: "Entretien", status: STATUS.interview, reason: null },
  { key: "refused", label: "Refusé", status: STATUS.rejected, reason: REFUSAL_REASON },
  { key: "archived", label: "Archivé", status: STATUS.archived, reason: null },
] as const;

export type BoardColumn = (typeof BOARD_COLUMNS)[number];

export function columnOf(job: JobItem): BoardColumn["key"] | null {
  if (job.status === STATUS.applied) return "applied";
  if (job.status === STATUS.interview) return "interview";
  if (job.status === STATUS.rejected) return job.rejection_reason === REFUSAL_REASON ? "refused" : null;
  if (job.status === STATUS.archived) return "archived";
  return null;
}

const FOLLOW_UP_DAYS = 14;

interface BoardProps {
  jobs: JobItem[];
  archivedTotal: number;
  onMore: () => void;
  onMove: (job: JobItem, column: BoardColumn) => void;
  onLetter: (job: JobItem) => void;
}

export function Board({ jobs, archivedTotal, onMore, onMove, onLetter }: BoardProps) {
  return (
    <div className="board">
      {BOARD_COLUMNS.map((col) => {
        const members = jobs.filter((j) => columnOf(j) === col.key);
        const count = col.key === "archived" ? archivedTotal : members.length;
        return (
          <section key={col.key} className={`board-col col-${col.key}`} aria-label={col.label}>
            <h2 className="board-head">
              {col.label}
              <span className="board-n">{count}</span>
            </h2>
            <div className="board-cards">
              {members.length === 0 && <p className="muted board-empty">Aucune candidature</p>}
              {members.map((job) => {
                const days = daysSince(job.applied_at);
                const followUp = col.key === "applied" && days !== null && days >= FOLLOW_UP_DAYS;
                return (
                  <article key={job.id} className="card">
                    <div className="card-top">
                      <strong className="card-company">{job.company}</strong>
                      <ScorePill job={job} />
                    </div>
                    <p className="card-title">{job.title}</p>
                    {(col.key === "applied" || col.key === "interview") && days !== null && (
                      <p className={followUp ? "card-due" : "muted card-days"}>
                        {followUp ? `À relancer : postulé il y a ${days} jours` : `Postulé il y a ${days} j`}
                      </p>
                    )}
                    {job.application_report && <ApplicationReportLine report={job.application_report} compact />}
                    {job.user_notes && <p className="card-notes">{job.user_notes}</p>}
                    <div className="card-actions">
                      {job.url && (
                        <a className="link" href={job.url} target="_blank" rel="noopener noreferrer">
                          Offre
                        </a>
                      )}
                      <button type="button" className="link" onClick={() => onLetter(job)}>
                        Lettre
                      </button>
                      <select
                        className="field move"
                        aria-label={`Déplacer ${job.title}`}
                        value=""
                        onChange={(e) => {
                          const target = BOARD_COLUMNS.find((c) => c.key === e.target.value);
                          if (target) onMove(job, target);
                        }}
                      >
                        <option value="" disabled>
                          Déplacer vers…
                        </option>
                        {BOARD_COLUMNS.filter((c) => c.key !== col.key).map((c) => (
                          <option key={c.key} value={c.key}>
                            {c.label}
                          </option>
                        ))}
                      </select>
                    </div>
                  </article>
                );
              })}
              {col.key === "archived" && members.length < archivedTotal && (
                <button type="button" className="btn ghost" onClick={onMore}>
                  Afficher 40 de plus
                </button>
              )}
            </div>
          </section>
        );
      })}
    </div>
  );
}
