"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Board, type BoardColumn } from "@/components/Board";
import { IgnorePicker } from "@/components/IgnorePicker";
import { JobDetail } from "@/components/JobDetail";
import { JobList, groupRows, type Segment, type SegmentInfo } from "@/components/JobList";
import { KeyboardHelp } from "@/components/KeyboardHelp";
import { LetterModal } from "@/components/LetterModal";
import { PackModal } from "@/components/PackModal";
import { Toast, useToast } from "@/components/Toast";
import { api } from "@/lib/api";
import { plural } from "@/lib/format";
import {
  STATUS,
  type ClaudePackResponse,
  type IgnoreReason,
  type IgnoreReasonInfo,
  type JobItem,
  type JobsQuery,
  type SortKey,
  type StatsData,
  type UserGradePayload,
  type UserGradeResponse,
} from "@/lib/types";

type View = "triage" | "board";
const ARCHIVE_PAGE = 40;
// Taille d'une page de la liste « À trier » (chargement au défilement, U5).
const PAGE = 100;
// Offres sondées au plus par « Vérifier les offres fermées » (sonde réseau, lente).
const CLOSED_CHECKS = 20;
const DEFAULT_GRADE_BOUNDS = { score_min: 0, score_max: 100, comment_max_length: 500 };

// Onglets de la liste : filtres appliqués par l'API, pour des compteurs justes malgré la pagination.
const SEGMENT_DEFS: { key: Segment; label: string; title?: string; query: JobsQuery }[] = [
  { key: "all", label: "Toutes", query: {} },
  { key: "core", label: "Cœur de cible", title: "Note de 80 ou plus", query: { min_score: 80 } },
  { key: "good", label: "60 à 79", title: "Note de 60 à 79 (le cœur de cible est dans son propre onglet)", query: { min_score: 60, max_score: 80 } },
  { key: "old", label: "Plus de 14 j", title: "Publiées il y a au moins 14 jours", query: { only_older_than_days: 14 } },
  {
    key: "excluded",
    label: "Exclues",
    title: "Offres écartées par l'IA (contrat prouvé, pas un stage…), avec la raison",
    query: { only_excluded: true },
  },
];

interface TriageState {
  total: number;
  jobs: JobItem[];
  hasMore: boolean;
  /** Offset serveur de la prochaine page (diminue quand des offres sortent de la liste). */
  nextOffset: number;
}

const EMPTY_TRIAGE: TriageState = { total: 0, jobs: [], hasMore: false, nextOffset: 0 };

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return debounced;
}

// Ajoute des offres à la liste sans doublon (une page peut recouvrir des offres déjà chargées).
function mergeJobs(current: JobItem[], incoming: JobItem[]): JobItem[] {
  const seen = new Set(current.map((j) => j.id));
  return [...current, ...incoming.filter((j) => !seen.has(j.id))];
}

function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el) return false;
  if (el.isContentEditable) return true;
  if (el.tagName === "INPUT") {
    const type = (el as HTMLInputElement).type;
    return type !== "checkbox" && type !== "radio" && type !== "button";
  }
  return el.tagName === "TEXTAREA" || el.tagName === "SELECT";
}

export default function StageCopilotPage() {
  const [view, setView] = useState<View>("triage");
  const [query, setQuery] = useState("");
  const debouncedQuery = useDebounced(query.trim(), 250);
  const [sortBy, setSortBy] = useState<SortKey>("score");
  const [segment, setSegment] = useState<Segment>("all");
  const [v4Classes, setV4Classes] = useState<string[]>([]);

  const [triage, setTriage] = useState<TriageState>(EMPTY_TRIAGE);
  const [counts, setCounts] = useState<Partial<Record<Segment, number>>>({});
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [loadingMore, setLoadingMore] = useState(false);
  const [boardJobs, setBoardJobs] = useState<JobItem[]>([]);
  const [archived, setArchived] = useState<{ total: number; jobs: JobItem[] }>({ total: 0, jobs: [] });
  const [archiveLimit, setArchiveLimit] = useState(ARCHIVE_PAGE);
  const [stats, setStats] = useState<StatsData | null>(null);
  const [reasons, setReasons] = useState<{ list: IgnoreReasonInfo[]; commentMax: number }>({ list: [], commentMax: 500 });
  const [loading, setLoading] = useState(true);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [mobileDetail, setMobileDetail] = useState(false);
  const [checkedIds, setCheckedIds] = useState<Set<string>>(new Set());
  const [lastCheckedId, setLastCheckedId] = useState<string | null>(null);

  const [letterJob, setLetterJob] = useState<JobItem | null>(null);
  const [pack, setPack] = useState<ClaudePackResponse | null>(null);
  const [ignoreTarget, setIgnoreTarget] = useState<JobItem[] | null>(null);
  const [help, setHelp] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const { toast, show: showToast, dismiss: dismissToast, pause: pauseToast, resume: resumeToast } = useToast();
  // Jeton de la dernière requête de liste : une réponse périmée (filtre changé entre-temps) est ignorée.
  const listReq = useRef(0);

  const fail = useCallback((e: unknown) => showToast({ text: (e as Error).message, tone: "alert" }), [showToast]);

  // ---------- Chargement ----------
  const baseQuery = useCallback(
    (seg: Segment): JobsQuery => ({
      hide_processed: true,
      sort_by: sortBy,
      q: debouncedQuery || undefined,
      v4_classes: v4Classes.length ? v4Classes.join(",") : undefined,
      ...SEGMENT_DEFS.find((s) => s.key === seg)!.query,
    }),
    [sortBy, debouncedQuery, v4Classes]
  );

  const loadCounts = useCallback(async () => {
    const keys: Segment[] = ["all", "core", "good", "old"];
    const res = await Promise.all(keys.map((k) => api.jobs({ ...baseQuery(k), limit: 1 })));
    const next: Partial<Record<Segment, number>> = {};
    keys.forEach((k, i) => (next[k] = res[i].total));
    next.excluded = res[0].excluded_count;
    setCounts(next);
  }, [baseQuery]);

  const loadTriage = useCallback(async () => {
    const req = ++listReq.current;
    const [page] = await Promise.all([api.jobs({ ...baseQuery(segment), offset: 0, limit: PAGE }), loadCounts()]);
    if (req !== listReq.current) return;
    setTriage({ total: page.total, jobs: page.jobs, hasMore: page.has_more, nextOffset: page.offset + page.jobs.length });
  }, [baseQuery, segment, loadCounts]);

  const loadMore = useCallback(async () => {
    if (!triage.hasMore || loadingMore) return;
    const req = listReq.current;
    setLoadingMore(true);
    try {
      const page = await api.jobs({ ...baseQuery(segment), offset: triage.nextOffset, limit: PAGE });
      if (req !== listReq.current) return;
      setTriage((t) => ({
        total: page.total,
        jobs: mergeJobs(t.jobs, page.jobs),
        hasMore: page.has_more,
        nextOffset: page.offset + page.jobs.length,
      }));
    } catch (e) {
      fail(e);
    } finally {
      setLoadingMore(false);
    }
  }, [triage.hasMore, triage.nextOffset, loadingMore, baseQuery, segment, fail]);

  const loadBoard = useCallback(async () => {
    const base: JobsQuery = { sort_by: "freshness", q: debouncedQuery || undefined };
    const [a, o] = await Promise.all([
      api.jobs({ ...base, statuses: [STATUS.applied, STATUS.interview, STATUS.rejected].join(","), limit: 2000 }),
      api.jobs({ ...base, statuses: STATUS.archived, limit: archiveLimit }),
    ]);
    setBoardJobs(a.jobs);
    setArchived(o);
  }, [debouncedQuery, archiveLimit]);

  const loadStats = useCallback(async () => setStats(await api.stats()), []);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      await Promise.all([view === "triage" ? loadTriage() : loadBoard(), loadStats()]);
    } catch (e) {
      fail(e);
    } finally {
      setLoading(false);
    }
  }, [view, loadTriage, loadBoard, loadStats, fail]);

  useEffect(() => {
    reload();
  }, [reload]);

  useEffect(() => {
    api
      .ignoreReasons()
      .then((r) => setReasons({ list: r.reasons, commentMax: r.comment_max_length }))
      .catch(() => {});
  }, []);
  const closeIgnore = useCallback(() => setIgnoreTarget(null), []);
  const closeHelp = useCallback(() => setHelp(false), []);
  const reasonList = reasons.list.length ? reasons.list : stats?.ignore_reasons ?? [];

  // ---------- Sélection ----------
  const rows = useMemo(() => groupRows(triage.jobs, expanded), [triage.jobs, expanded]);
  const visible = useMemo(() => rows.map((r) => r.job), [rows]);
  const selectedJob = visible.find((j) => j.id === selectedId) ?? visible[0] ?? null;

  useEffect(() => {
    if (selectedJob && selectedJob.id !== selectedId) setSelectedId(selectedJob.id);
  }, [selectedJob, selectedId]);

  const select = (id: string) => {
    setSelectedId(id);
    setMobileDetail(true);
  };

  const toggleCheck = (id: string, shiftKey: boolean) => {
    const ids = visible.map((j) => j.id);
    setCheckedIds((prev) => {
      const next = new Set(prev);
      const add = !next.has(id);
      const from = lastCheckedId ? ids.indexOf(lastCheckedId) : -1;
      const to = ids.indexOf(id);
      const range = shiftKey && from >= 0 && to >= 0 ? ids.slice(Math.min(from, to), Math.max(from, to) + 1) : [id];
      range.forEach((x) => (add ? next.add(x) : next.delete(x)));
      return next;
    });
    setLastCheckedId(id);
  };

  const toggleAll = () =>
    setCheckedIds((prev) => (visible.every((j) => prev.has(j.id)) ? new Set() : new Set(visible.map((j) => j.id))));

  // Déplier / replier les autres offres d'une entreprise (U2). Les offres pas encore chargées
  // (plus bas dans le tri) sont demandées à l'API avec les mêmes filtres.
  const toggleGroup = async (head: JobItem) => {
    const key = head.company_key;
    if (!key) return;
    if (expanded.has(key)) {
      const members = new Set(triage.jobs.filter((j) => j.company_key === key && j.id !== head.id).map((j) => j.id));
      setCheckedIds((prev) => new Set([...prev].filter((x) => !members.has(x))));
      setExpanded((prev) => {
        const next = new Set(prev);
        next.delete(key);
        return next;
      });
      return;
    }
    const loaded = triage.jobs.filter((j) => j.company_key === key).length;
    if (loaded < (head.company_count || 1)) {
      try {
        const res = await api.jobs({ ...baseQuery(segment), company_key: key, limit: 200 });
        setTriage((t) => ({ ...t, jobs: mergeJobs(t.jobs, res.jobs) }));
      } catch (e) {
        fail(e);
        return;
      }
    }
    setExpanded((prev) => new Set(prev).add(key));
  };

  // ---------- Statuts (mise à jour immédiate, annulable) ----------
  const patchJob = useCallback((id: string, patch: Partial<JobItem>) => {
    setTriage((t) => ({ ...t, jobs: t.jobs.map((j) => (j.id === id ? { ...j, ...patch } : j)) }));
    setBoardJobs((b) => b.map((j) => (j.id === id ? { ...j, ...patch } : j)));
  }, []);

  const moveJobs = useCallback(
    async (
      jobs: JobItem[],
      status: string,
      reason: string | null,
      label: string,
      ignore?: { reason: IgnoreReason; comment: string }
    ) => {
      if (!jobs.length) return;
      const ids = new Set(jobs.map((j) => j.id));
      const triageBefore = triage;
      const boardBefore = new Map(jobs.map((j) => [j.id, j]));
      const leaving = status === STATUS.new ? 0 : jobs.length;
      // La ligne suivante prend la place de celle qui sort de la liste.
      const idx = visible.findIndex((j) => ids.has(j.id));
      const nextJob =
        visible.slice(idx + 1).find((j) => !ids.has(j.id)) ?? visible.slice(0, idx).reverse().find((j) => !ids.has(j.id));
      setTriage((t) => ({
        ...t,
        total: Math.max(0, t.total - leaving),
        jobs: t.jobs.filter((j) => !ids.has(j.id) || status === STATUS.new),
        // Les offres sorties décalent la pagination serveur : on recule d'autant (les doublons sont filtrés).
        nextOffset: Math.max(0, t.nextOffset - leaving),
      }));
      setBoardJobs((b) => b.map((j) => (ids.has(j.id) ? { ...j, status, rejection_reason: reason ?? "" } : j)));
      if (nextJob && view === "triage") setSelectedId(nextJob.id);
      setCheckedIds((prev) => new Set([...prev].filter((x) => !ids.has(x))));
      try {
        if (ignore && jobs.length === 1) await api.ignore(jobs[0].id, ignore.reason, ignore.comment);
        else if (ignore) await api.setStatus([...ids], status, null, { reason: ignore.reason, comment: ignore.comment || undefined });
        else await api.setStatus([...ids], status, reason);
        showToast({
          text: jobs.length === 1 ? `${label} : ${jobs[0].company}` : `${label} : ${plural(jobs.length, "offre")}`,
          onUndo: async () => {
            // Retour visuel immédiat : les offres reprennent leur place, sans attendre l'API.
            setTriage((t) => {
              const present = new Set(t.jobs.map((j) => j.id));
              return {
                ...t,
                total: triageBefore.total,
                jobs: triageBefore.jobs.filter((j) => present.has(j.id) || ids.has(j.id)),
                nextOffset: t.nextOffset + leaving,
              };
            });
            setBoardJobs((b) => b.map((j) => boardBefore.get(j.id) ?? j));
            if (view === "triage") setSelectedId(jobs[0].id);
            try {
              // Restaure chaque offre dans son statut et son motif d'origine.
              const groups = new Map<string, JobItem[]>();
              jobs.forEach((j) => {
                const k = `${j.status}\u0000${j.rejection_reason}`;
                groups.set(k, [...(groups.get(k) ?? []), j]);
              });
              for (const group of groups.values()) {
                await api.setStatus(group.map((j) => j.id), group[0].status, group[0].rejection_reason || null);
              }
              // Les colonnes (dont Archivé, chargée à part) se recalculent depuis la base.
              if (view === "board") reload();
              else {
                loadStats().catch(() => {});
                loadCounts().catch(() => {});
              }
            } catch (e) {
              fail(e);
              reload();
            }
          },
        });
        loadStats().catch(() => {});
        if (view === "triage") loadCounts().catch(() => {});
      } catch (e) {
        fail(e);
        reload();
      }
    },
    [triage, visible, view, showToast, fail, reload, loadStats, loadCounts]
  );

  const checkedJobs = () => triage.jobs.filter((j) => checkedIds.has(j.id));

  const ignoreJobs = (reason: IgnoreReason, comment: string) => {
    const jobs = ignoreTarget ?? [];
    setIgnoreTarget(null);
    const lbl = reasonList.find((r) => r.key === reason)?.label ?? reason;
    moveJobs(jobs, STATUS.archived, null, `Ignorée (${lbl})`, { reason, comment });
  };

  const saveGrade = async (job: JobItem, grade: UserGradePayload | null) => {
    try {
      const res: UserGradeResponse = grade ? await api.setUserGrade(job.id, grade) : await api.clearUserGrade(job.id);
      patchJob(job.id, {
        user_v4_class: res.user_v4_class,
        user_v4_score: res.user_v4_score,
        user_v4_comment: res.user_v4_comment,
        user_v4_at: res.user_v4_at,
      });
    } catch (e) {
      fail(e);
      throw e;
    }
  };

  const openPack = async (jobs: JobItem[]) => {
    if (!jobs.length) return;
    setBusy(`Préparation du Pack Claude pour ${plural(jobs.length, "offre")}…`);
    try {
      setPack(await api.claudePack(jobs.map((j) => j.id)));
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  };

  const gradeV4 = async (jobs: JobItem[]) => {
    if (!jobs.length) return;
    setBusy(`Notation de ${plural(Math.min(jobs.length, 25), "offre")}…`);
    try {
      await api.gradeV4(jobs.map((j) => j.id));
      await reload();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  };

  // Les offres anciennes ne sont plus archivées à l'âge : l'API vérifie si leur page est fermée.
  const archiveClosed = async () => {
    setBusy(`Vérification des offres de plus de 14 jours (${CLOSED_CHECKS} pages au plus)…`);
    try {
      const res = await api.archiveOld(14, CLOSED_CHECKS);
      showToast({
        text: res.updated
          ? `${plural(res.updated, "offre fermée archivée", "offres fermées archivées")}`
          : "Aucune offre fermée trouvée dans ce lot",
      });
      await reload();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  };

  // ---------- Clavier (U4) ----------
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (letterJob || pack || ignoreTarget || help || view !== "triage") return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (isTyping(e.target)) return;
      if (e.key === "?") {
        e.preventDefault();
        setHelp(true);
        return;
      }
      if (!selectedJob) return;
      const idx = visible.findIndex((j) => j.id === selectedJob.id);
      const next = () => {
        if (visible[idx + 1]) setSelectedId(visible[idx + 1].id);
        if (idx >= visible.length - 3) loadMore();
      };
      const prev = () => idx > 0 && setSelectedId(visible[idx - 1].id);
      const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
      const act: Record<string, () => void> = {
        j: next,
        ArrowDown: next,
        k: prev,
        ArrowUp: prev,
        o: () => selectedJob.url && window.open(selectedJob.url, "_blank", "noopener,noreferrer"),
        l: () => setLetterJob(selectedJob),
        p: () => moveJobs([selectedJob], STATUS.applied, null, "Marquée postulée"),
        i: () => setIgnoreTarget([selectedJob]),
      };
      if (act[key]) {
        e.preventDefault();
        act[key]();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [letterJob, pack, ignoreTarget, help, view, selectedJob, visible, moveJobs, loadMore]);

  const boardCount = (stats?.by_status?.[STATUS.applied] ?? 0) + (stats?.by_status?.[STATUS.interview] ?? 0);
  const v4Graded = stats?.v4_graded ?? 0;
  const segments: SegmentInfo[] = SEGMENT_DEFS.map((s) => ({
    key: s.key,
    label: s.label,
    title: s.title,
    count: counts[s.key] ?? null,
  }));

  const bulkBar =
    checkedIds.size > 0 ? (
      <div className="bulk" role="toolbar" aria-label="Actions sur la sélection">
        <strong>{plural(checkedIds.size, "sélectionnée")}</strong>
        <button type="button" className="btn" onClick={() => openPack(checkedJobs())}>
          Pack Claude
        </button>
        <button type="button" className="btn" onClick={() => moveJobs(checkedJobs(), STATUS.applied, null, "Marquées postulées")}>
          Postulé
        </button>
        <button type="button" className="btn" onClick={() => setIgnoreTarget(checkedJobs())}>
          Ignorer…
        </button>
        <button type="button" className="btn ghost alert" onClick={() => moveJobs(checkedJobs(), STATUS.rejected, "bulk_trash", "Mises à la corbeille")}>
          Corbeille
        </button>
        <button type="button" className="btn ghost" onClick={() => setCheckedIds(new Set())}>
          Désélectionner
        </button>
      </div>
    ) : segment === "old" && visible.length > 0 ? (
      <div className="bulk calm">
        <span>Ces offres attendent depuis plus de deux semaines. Sont-elles encore en ligne ?</span>
        <button type="button" className="btn" onClick={archiveClosed} title={`Sonde jusqu'à ${CLOSED_CHECKS} pages et archive celles qui sont fermées`}>
          Vérifier les offres fermées
        </button>
      </div>
    ) : null;

  return (
    <div className={`app ${mobileDetail ? "show-detail" : ""}`}>
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true" />
          Stage Copilot
        </div>
        <nav className="views" aria-label="Vues">
          <button type="button" className={`view ${view === "triage" ? "active" : ""}`} aria-current={view === "triage" ? "page" : undefined} onClick={() => setView("triage")}>
            À trier <span className="view-n">{counts.all ?? stats?.by_status?.[STATUS.new] ?? 0}</span>
          </button>
          <button type="button" className={`view ${view === "board" ? "active" : ""}`} aria-current={view === "board" ? "page" : undefined} onClick={() => setView("board")}>
            Candidatures <span className="view-n">{boardCount}</span>
          </button>
          <a className="view" href="/file" title="File de candidature pour Claude in Chrome">
            File Claude
          </a>
        </nav>
        <div className="tools">
          <input
            type="search"
            className="field search"
            placeholder="Rechercher un poste, une entreprise, une techno"
            aria-label="Rechercher"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          {view === "triage" && (
            <select className="field" aria-label="Trier par" value={sortBy} onChange={(e) => setSortBy(e.target.value as SortKey)}>
              <option value="score">Meilleure note</option>
              <option value="freshness">Plus récentes</option>
              {v4Graded > 0 && <option value="v4_class">Note dans la classe</option>}
            </select>
          )}
        </div>
      </header>

      {view === "triage" && v4Graded > 0 && (
        <div className="classes" aria-label="Filtrer par classe">
          <span className="muted">Classe</span>
          {(stats?.v4_classes ?? []).map((c) => {
            const on = v4Classes.includes(c.key);
            return (
              <button
                key={c.key}
                type="button"
                aria-pressed={on}
                className={`chip ${on ? "active" : ""}`}
                title={c.label}
                onClick={() => setV4Classes((p) => (on ? p.filter((x) => x !== c.key) : [...p, c.key]))}
              >
                <b>{c.key}</b>
                <span className="chip-label">{c.short_label || c.label}</span>
                <span className="chip-n">{stats?.by_v4_class?.[c.key] ?? 0}</span>
              </button>
            );
          })}
        </div>
      )}

      {busy && (
        <div className="busy" role="status">
          <span className="spinner" aria-hidden="true" />
          {busy}
        </div>
      )}

      {view === "triage" ? (
        <main className="triage">
          <JobList
            rows={rows}
            loadedCount={triage.jobs.length}
            total={triage.total}
            segments={segments}
            segment={segment}
            onSegment={(s) => {
              setSegment(s);
              setCheckedIds(new Set());
            }}
            selectedId={selectedJob?.id ?? null}
            onSelect={select}
            checkedIds={checkedIds}
            onToggleCheck={toggleCheck}
            onToggleAll={toggleAll}
            onToggleGroup={toggleGroup}
            bulkBar={bulkBar}
            loading={loading}
            hasMore={triage.hasMore}
            loadingMore={loadingMore}
            onLoadMore={loadMore}
            onHelp={() => setHelp(true)}
          />
          <JobDetail
            job={selectedJob}
            classes={stats?.v4_classes ?? []}
            gradeBounds={stats?.user_grade ?? DEFAULT_GRADE_BOUNDS}
            onBack={() => setMobileDetail(false)}
            onLetter={() => selectedJob && setLetterJob(selectedJob)}
            onPack={() => selectedJob && openPack([selectedJob])}
            onApplied={() => selectedJob && moveJobs([selectedJob], STATUS.applied, null, "Marquée postulée")}
            onIgnore={() => selectedJob && setIgnoreTarget([selectedJob])}
            onTrash={() => selectedJob && moveJobs([selectedJob], STATUS.rejected, "manual_trash", "Mise à la corbeille")}
            onReinstate={() =>
              // L'offre quitte l'onglet Exclues : on recharge pour que listes et compteurs suivent.
              selectedJob && moveJobs([selectedJob], STATUS.new, "remis_a_trier", "Remise à trier").then(reload)
            }
            onGradeV4={() => selectedJob && gradeV4([selectedJob])}
            onSaveNotes={async (text) => {
              if (!selectedJob) return;
              try {
                await api.saveNotes(selectedJob.id, text);
                patchJob(selectedJob.id, { user_notes: text });
              } catch (e) {
                fail(e);
                throw e;
              }
            }}
            onSaveGrade={(grade) => (selectedJob ? saveGrade(selectedJob, grade) : Promise.resolve())}
            onClearGrade={() => (selectedJob ? saveGrade(selectedJob, null) : Promise.resolve())}
          />
        </main>
      ) : (
        <main className="board-wrap">
          <Board
            jobs={[...boardJobs, ...archived.jobs]}
            archivedTotal={archived.total}
            onMore={() => setArchiveLimit((n) => n + ARCHIVE_PAGE)}
            onLetter={setLetterJob}
            onMove={async (job: JobItem, col: BoardColumn) => {
              await moveJobs([job], col.status, col.reason, `Déplacée vers ${col.label}`);
              loadBoard().catch(fail);
            }}
          />
        </main>
      )}

      {letterJob && <LetterModal job={letterJob} onClose={() => setLetterJob(null)} onSaved={() => reload()} />}
      {pack && <PackModal pack={pack} onClose={() => setPack(null)} />}
      {ignoreTarget && ignoreTarget.length > 0 && (
        <IgnorePicker
          target={ignoreTarget.length === 1 ? `${ignoreTarget[0].company} · ${ignoreTarget[0].title}` : plural(ignoreTarget.length, "offre")}
          reasons={reasonList}
          commentMax={reasons.commentMax}
          onPick={ignoreJobs}
          onClose={closeIgnore}
        />
      )}
      {help && <KeyboardHelp onClose={closeHelp} />}
      <Toast toast={toast} onDismiss={dismissToast} onPause={pauseToast} onResume={resumeToast} />
    </div>
  );
}
