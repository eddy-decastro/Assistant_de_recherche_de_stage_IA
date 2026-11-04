"use client";

import React, { useEffect, useRef } from "react";
import type { JobItem } from "@/lib/types";

export type Segment = "all" | "core" | "good" | "old";

export const SEGMENTS: { key: Segment; label: string; test: (j: JobItem) => boolean }[] = [
  { key: "all", label: "Toutes", test: () => true },
  { key: "core", label: "Cœur de cible", test: (j) => j.align_tone === "positive" },
  { key: "good", label: "Pertinentes", test: (j) => j.align_tone === "accent" },
  { key: "old", label: "Plus de 14 jours", test: (j) => (j.age_days ?? 0) >= 14 },
];

interface JobListProps {
  jobs: JobItem[];
  allJobs: JobItem[];
  total: number;
  segment: Segment;
  onSegment: (s: Segment) => void;
  selectedId: string | null;
  onSelect: (id: string) => void;
  checkedIds: Set<string>;
  onToggleCheck: (id: string, shiftKey: boolean) => void;
  onToggleAll: () => void;
  bulkBar: React.ReactNode;
  loading: boolean;
}

export function JobList(props: JobListProps) {
  const { jobs, allJobs, total, segment, selectedId, checkedIds } = props;
  const listRef = useRef<HTMLUListElement>(null);

  // Garde la ligne sélectionnée visible quand on navigue au clavier.
  useEffect(() => {
    listRef.current
      ?.querySelector<HTMLElement>(`[data-id="${CSS.escape(selectedId ?? "")}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [selectedId]);

  const allChecked = jobs.length > 0 && jobs.every((j) => checkedIds.has(j.id));

  return (
    <section className="list-pane" aria-label="Offres à trier">
      <div className="segments" role="tablist" aria-label="Filtrer par pertinence">
        {SEGMENTS.map((s) => (
          <button
            key={s.key}
            type="button"
            role="tab"
            aria-selected={segment === s.key}
            className={`segment ${segment === s.key ? "active" : ""}`}
            onClick={() => props.onSegment(s.key)}
          >
            {s.label}
            <span className="segment-n">{allJobs.filter(s.test).length}</span>
          </button>
        ))}
      </div>

      {props.bulkBar ?? (
        <div className="list-head">
          <label className="check-all">
            <input type="checkbox" checked={allChecked} onChange={props.onToggleAll} disabled={!jobs.length} />
            {total > allJobs.length ? `${allJobs.length} premières sur ${total}` : `${jobs.length} offre${jobs.length > 1 ? "s" : ""}`}
          </label>
          {props.loading && <span className="muted">Mise à jour…</span>}
        </div>
      )}

      {jobs.length === 0 ? (
        <div className="empty">
          <p>{props.loading ? "Chargement des offres…" : "Rien à trier ici."}</p>
          {!props.loading && segment !== "all" && (
            <button type="button" className="btn" onClick={() => props.onSegment("all")}>
              Voir toutes les offres
            </button>
          )}
        </div>
      ) : (
        <ul ref={listRef} className="rows" role="listbox" aria-label="Offres" tabIndex={-1}>
          {jobs.map((job) => {
            const selected = job.id === selectedId;
            return (
              <li
                key={job.id}
                data-id={job.id}
                role="option"
                aria-selected={selected}
                className={`row ${selected ? "selected" : ""} ${checkedIds.has(job.id) ? "checked" : ""}`}
                onClick={() => props.onSelect(job.id)}
              >
                <input
                  type="checkbox"
                  aria-label={`Sélectionner ${job.title}`}
                  checked={checkedIds.has(job.id)}
                  onClick={(e) => {
                    e.stopPropagation();
                    props.onToggleCheck(job.id, e.shiftKey);
                  }}
                  onChange={() => {}}
                />
                <span className={`row-score tone-${job.align_tone}`} title={`${job.align_label} (${job.score_origin})`}>
                  {job.score}
                </span>
                <span className="row-main">
                  <span className="row-title">{job.title}</span>
                  <span className="row-meta">
                    <span className="row-company">{job.company}</span>
                    {job.location && <span>{job.location}</span>}
                    <span>{job.date_label}</span>
                  </span>
                </span>
                {job.v4_class && job.v4_display_score !== null && (
                  <span className={`tag tone-${job.v4_class_tone}`} title={job.v4_class_label}>
                    {job.v4_class} {job.v4_display_score}
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      )}

      <p className="shortcuts">
        <kbd>j</kbd>/<kbd>k</kbd> naviguer · <kbd>o</kbd> ouvrir · <kbd>l</kbd> lettre · <kbd>p</kbd> postulé ·{" "}
        <kbd>x</kbd> archiver
      </p>
    </section>
  );
}
