"use client";

import React, { useEffect, useRef } from "react";
import { jobDateLabel, jobDateTitle, plural } from "@/lib/format";
import type { JobItem } from "@/lib/types";
import { ShortcutHint } from "./KeyboardHelp";
import { ScorePill } from "./ScoreRuler";

export type Segment = "all" | "core" | "good" | "old" | "excluded";

/** Onglet de la liste ; le filtre est appliqué par l'API (compteurs justes même avec la pagination). */
export interface SegmentInfo {
  key: Segment;
  label: string;
  title?: string;
  count: number | null;
}

/** Ligne affichée : une offre de tête (première de son entreprise) ou une offre dépliée sous elle. */
export interface ListRow {
  job: JobItem;
  member: boolean;
  /** Tête de groupe : nombre d'autres offres de la même entreprise (dans tout le résultat filtré). */
  others: number;
  expanded: boolean;
}

// Regroupe les offres d'une même entreprise sous la mieux classée (U2). Les autres offres
// n'apparaissent que si le groupe est déplié, juste sous leur tête, dans l'ordre du tri.
export function groupRows(jobs: JobItem[], expanded: Set<string>): ListRow[] {
  const byKey = new Map<string, JobItem[]>();
  for (const j of jobs) {
    if (!j.company_key) continue;
    byKey.set(j.company_key, [...(byKey.get(j.company_key) ?? []), j]);
  }
  const rows: ListRow[] = [];
  const seen = new Set<string>();
  for (const j of jobs) {
    const key = j.company_key;
    if (!key) {
      rows.push({ job: j, member: false, others: 0, expanded: false });
      continue;
    }
    if (seen.has(key)) continue;
    seen.add(key);
    const members = (byKey.get(key) ?? []).slice(1);
    const open = expanded.has(key);
    const others = Math.max((j.company_count || 1) - 1, members.length);
    rows.push({ job: j, member: false, others, expanded: open });
    if (open) members.forEach((m) => rows.push({ job: m, member: true, others: 0, expanded: false }));
  }
  return rows;
}

interface JobListProps {
  rows: ListRow[];
  loadedCount: number;
  total: number;
  segments: SegmentInfo[];
  segment: Segment;
  onSegment: (s: Segment) => void;
  selectedId: string | null;
  onSelect: (id: string) => void;
  checkedIds: Set<string>;
  onToggleCheck: (id: string, shiftKey: boolean) => void;
  onToggleAll: () => void;
  onToggleGroup: (job: JobItem) => void;
  bulkBar: React.ReactNode;
  loading: boolean;
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore: () => void;
  onHelp: () => void;
}

export function JobList(props: JobListProps) {
  const { rows, segment, selectedId, checkedIds, hasMore, loadingMore, onLoadMore } = props;
  const listRef = useRef<HTMLUListElement>(null);
  const sentinelRef = useRef<HTMLLIElement>(null);

  // Garde la ligne sélectionnée visible quand on navigue au clavier.
  useEffect(() => {
    listRef.current
      ?.querySelector<HTMLElement>(`[data-id="${CSS.escape(selectedId ?? "")}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [selectedId]);

  // Chargement au défilement (U5) : la page suivante arrive quand le bas de la liste approche.
  useEffect(() => {
    const el = sentinelRef.current;
    if (!el || !hasMore) return;
    const obs = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting) && !loadingMore) onLoadMore();
      },
      { root: listRef.current, rootMargin: "400px 0px" }
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, [hasMore, loadingMore, onLoadMore, rows.length]);

  const allChecked = rows.length > 0 && rows.every((r) => checkedIds.has(r.job.id));
  const excludedTab = segment === "excluded";

  return (
    <section className="list-pane" aria-label="Offres à trier">
      <div className="segments" role="tablist" aria-label="Filtrer les offres">
        {props.segments.map((s) => (
          <button
            key={s.key}
            type="button"
            role="tab"
            aria-selected={segment === s.key}
            title={s.title}
            className={`segment ${segment === s.key ? "active" : ""}`}
            onClick={() => props.onSegment(s.key)}
          >
            {s.label}
            <span className="segment-n">{s.count ?? "…"}</span>
          </button>
        ))}
      </div>

      {props.bulkBar ?? (
        <div className="list-head">
          <label className="check-all">
            <input type="checkbox" checked={allChecked} onChange={props.onToggleAll} disabled={!rows.length} />
            {props.total > props.loadedCount
              ? `${props.loadedCount} chargées sur ${props.total}`
              : plural(props.total, "offre")}
          </label>
          {props.loading && <span className="muted">Mise à jour…</span>}
        </div>
      )}

      {rows.length === 0 ? (
        <div className="empty">
          <p>{props.loading ? "Chargement des offres…" : excludedTab ? "Aucune offre exclue." : "Rien à trier ici."}</p>
          {!props.loading && segment !== "all" && (
            <button type="button" className="btn" onClick={() => props.onSegment("all")}>
              Voir toutes les offres
            </button>
          )}
        </div>
      ) : (
        <ul ref={listRef} className="rows" role="listbox" aria-label="Offres" tabIndex={-1}>
          {rows.map(({ job, member, others, expanded }) => {
            const selected = job.id === selectedId;
            const date = jobDateLabel(job);
            return (
              <li
                key={job.id}
                data-id={job.id}
                role="option"
                aria-selected={selected}
                className={`row ${member ? "member" : ""} ${selected ? "selected" : ""} ${checkedIds.has(job.id) ? "checked" : ""}`}
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
                <ScorePill job={job} />
                <span className="row-main">
                  <span className="row-title">{job.title}</span>
                  <span className="row-meta">
                    {!member && <span className="row-company">{job.company}</span>}
                    {job.location && <span>{job.location}</span>}
                    {date && (
                      <span className={job.date_estimee ? "date-est" : undefined} title={jobDateTitle(job)}>
                        {date}
                      </span>
                    )}
                  </span>
                  {job.excluded && job.exclusion_reason && <span className="row-why">{job.exclusion_reason}</span>}
                  {!member && others > 0 && (
                    <button
                      type="button"
                      className="group-toggle"
                      aria-expanded={expanded}
                      onClick={(e) => {
                        e.stopPropagation();
                        props.onToggleGroup(job);
                      }}
                    >
                      {expanded ? "Replier" : `${plural(others, "autre offre", "autres offres")} chez ${job.company}`}
                    </button>
                  )}
                </span>
                <span className="row-flags">
                  {job.contract_to_verify && (
                    <span className="tag tone-warn small-tag" title={job.contract_quote || "Le contrat n'est pas prouvé par une citation"}>
                      Contrat ?
                    </span>
                  )}
                  {job.user_v4_at && (
                    <span className="tag small-tag" title={`Ma note : ${job.user_v4_class || "–"} ${job.user_v4_score ?? ""}`.trim()}>
                      corrigée
                    </span>
                  )}
                </span>
              </li>
            );
          })}
          {hasMore && (
            <li ref={sentinelRef} className="row-more" aria-hidden="true">
              {loadingMore ? "Chargement…" : (
                <button type="button" className="link" onClick={onLoadMore}>
                  Charger la suite
                </button>
              )}
            </li>
          )}
        </ul>
      )}

      <ShortcutHint onHelp={props.onHelp} />
    </section>
  );
}
