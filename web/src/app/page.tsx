"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Board, type BoardColumn } from "@/components/Board";
import { JobDetail } from "@/components/JobDetail";
import { JobList, SEGMENTS, type Segment } from "@/components/JobList";
import { LetterModal } from "@/components/LetterModal";
import { PackModal } from "@/components/PackModal";
import { Toast, useToast } from "@/components/Toast";
import { api } from "@/lib/api";
import { plural } from "@/lib/format";
import { STATUS, type ClaudePackResponse, type JobItem, type SortKey, type StatsData } from "@/lib/types";

type View = "triage" | "board";
const ARCHIVE_PAGE = 40;

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return debounced;
}

export default function StageCopilotPage() {
  const [view, setView] = useState<View>("triage");
  const [query, setQuery] = useState("");
  const debouncedQuery = useDebounced(query.trim(), 250);
  const [sortBy, setSortBy] = useState<SortKey>("v3");
  const [segment, setSegment] = useState<Segment>("all");
  const [v4Classes, setV4Classes] = useState<string[]>([]);

  const [triage, setTriage] = useState<{ total: number; jobs: JobItem[] }>({ total: 0, jobs: [] });
  const [boardJobs, setBoardJobs] = useState<JobItem[]>([]);
  const [archived, setArchived] = useState<{ total: number; jobs: JobItem[] }>({ total: 0, jobs: [] });
  const [archiveLimit, setArchiveLimit] = useState(ARCHIVE_PAGE);
  const [stats, setStats] = useState<StatsData | null>(null);
  const [loading, setLoading] = useState(true);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [mobileDetail, setMobileDetail] = useState(false);
  const [checkedIds, setCheckedIds] = useState<Set<string>>(new Set());
  const [lastCheckedId, setLastCheckedId] = useState<string | null>(null);

  const [letterJob, setLetterJob] = useState<JobItem | null>(null);
  const [pack, setPack] = useState<ClaudePackResponse | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const { toast, show: showToast, dismiss: dismissToast, pause: pauseToast, resume: resumeToast } = useToast();

  const fail = useCallback((e: unknown) => showToast({ text: (e as Error).message, tone: "alert" }), [showToast]);

  // ---------- Chargement ----------
  const loadTriage = useCallback(async () => {
    const params = new URLSearchParams({ hide_processed: "true", sort_by: sortBy, limit: "250" });
    if (debouncedQuery) params.set("q", debouncedQuery);
    if (v4Classes.length) params.set("v4_classes", v4Classes.join(","));
    setTriage(await api.jobs(params));
  }, [debouncedQuery, sortBy, v4Classes]);

  const loadBoard = useCallback(async () => {
    const base = { sort_by: "freshness" } as Record<string, string>;
    if (debouncedQuery) base.q = debouncedQuery;
    const active = new URLSearchParams({
      ...base,
      statuses: [STATUS.applied, STATUS.interview, STATUS.rejected].join(","),
      limit: "2000",
    });
    const old = new URLSearchParams({ ...base, statuses: STATUS.archived, limit: String(archiveLimit) });
    const [a, o] = await Promise.all([api.jobs(active), api.jobs(old)]);
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

  // ---------- Sélection ----------
  const segmentTest = SEGMENTS.find((s) => s.key === segment)!.test;
  const visible = useMemo(() => triage.jobs.filter(segmentTest), [triage.jobs, segmentTest]);
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

  // ---------- Statuts (mise à jour immédiate, annulable) ----------
  const moveJobs = useCallback(
    async (jobs: JobItem[], status: string, reason: string | null, label: string) => {
      if (!jobs.length) return;
      const ids = new Set(jobs.map((j) => j.id));
      const triageBefore = triage;
      const boardBefore = new Map(jobs.map((j) => [j.id, j]));
      // La ligne suivante prend la place de celle qui sort de la liste.
      const idx = visible.findIndex((j) => ids.has(j.id));
      const nextJob = visible.slice(idx + 1).find((j) => !ids.has(j.id)) ?? visible.slice(0, idx).reverse().find((j) => !ids.has(j.id));
      setTriage((t) => ({ total: t.total - (status === STATUS.new ? 0 : jobs.length), jobs: t.jobs.filter((j) => !ids.has(j.id) || status === STATUS.new) }));
      setBoardJobs((b) => b.map((j) => (ids.has(j.id) ? { ...j, status, rejection_reason: reason ?? "" } : j)));
      if (nextJob && view === "triage") setSelectedId(nextJob.id);
      setCheckedIds((prev) => new Set([...prev].filter((x) => !ids.has(x))));
      try {
        await api.setStatus([...ids], status, reason);
        showToast({
          text: jobs.length === 1 ? `${label} : ${jobs[0].company}` : `${label} : ${plural(jobs.length, "offre")}`,
          onUndo: async () => {
            // Retour visuel immédiat : les offres reprennent leur place, sans attendre l'API.
            setTriage((t) => {
              const present = new Set(t.jobs.map((j) => j.id));
              return {
                total: triageBefore.total,
                jobs: triageBefore.jobs.filter((j) => present.has(j.id) || ids.has(j.id)),
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
              else loadStats().catch(() => {});
            } catch (e) {
              fail(e);
              reload();
            }
          },
        });
        loadStats().catch(() => {});
      } catch (e) {
        fail(e);
        reload();
      }
    },
    [triage, visible, view, showToast, fail, reload, loadStats]
  );

  const checkedJobs = () => visible.filter((j) => checkedIds.has(j.id));

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
    setBusy(`Notation v4 de ${plural(Math.min(jobs.length, 25), "offre")}…`);
    try {
      await api.gradeV4(jobs.map((j) => j.id));
      await reload();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  };

  const archiveOld = async () => {
    setBusy("Archivage des offres de plus de 14 jours…");
    try {
      const res = await api.archiveOld(14);
      showToast({ text: `${plural(res.updated, "offre archivée", "offres archivées")}` });
      setSegment("all");
      await reload();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  };

  // ---------- Clavier ----------
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (letterJob || pack || view !== "triage") return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (/^(INPUT|TEXTAREA|SELECT)$/.test((e.target as HTMLElement)?.tagName || "")) return;
      if (!selectedJob) return;
      const idx = visible.findIndex((j) => j.id === selectedJob.id);
      const act: Record<string, () => void> = {
        j: () => visible[idx + 1] && setSelectedId(visible[idx + 1].id),
        ArrowDown: () => visible[idx + 1] && setSelectedId(visible[idx + 1].id),
        k: () => idx > 0 && setSelectedId(visible[idx - 1].id),
        ArrowUp: () => idx > 0 && setSelectedId(visible[idx - 1].id),
        o: () => selectedJob.url && window.open(selectedJob.url, "_blank", "noopener,noreferrer"),
        l: () => setLetterJob(selectedJob),
        p: () => moveJobs([selectedJob], STATUS.applied, null, "Marquée postulée"),
        x: () => moveJobs([selectedJob], STATUS.archived, null, "Archivée"),
      };
      if (act[e.key]) {
        e.preventDefault();
        act[e.key]();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [letterJob, pack, view, selectedJob, visible, moveJobs]);

  const boardCount = (stats?.by_status?.[STATUS.applied] ?? 0) + (stats?.by_status?.[STATUS.interview] ?? 0);
  const v4Graded = stats?.v4_graded ?? 0;

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
        <button type="button" className="btn" onClick={() => moveJobs(checkedJobs(), STATUS.archived, "bulk_archive", "Archivées")}>
          Archiver
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
        <span>Ces offres attendent depuis plus de deux semaines.</span>
        <button type="button" className="btn" onClick={archiveOld}>
          Tout archiver ({stats?.old_new_14d ?? visible.length})
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
            À trier <span className="view-n">{triage.total || stats?.by_status?.[STATUS.new] || 0}</span>
          </button>
          <button type="button" className={`view ${view === "board" ? "active" : ""}`} aria-current={view === "board" ? "page" : undefined} onClick={() => setView("board")}>
            Candidatures <span className="view-n">{boardCount}</span>
          </button>
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
              <option value="v3">Meilleur score</option>
              <option value="freshness">Plus récentes</option>
              {v4Graded > 0 && <option value="v4">Note v4</option>}
              {v4Graded > 0 && <option value="v4_class">Note dans la classe v4</option>}
            </select>
          )}
        </div>
      </header>

      {view === "triage" && v4Graded > 0 && (
        <div className="classes" aria-label="Filtrer par classe v4">
          <span className="muted">Classe v4</span>
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
                {c.key} <span className="muted">{stats?.by_v4_class?.[c.key] ?? 0}</span>
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
            jobs={visible}
            allJobs={triage.jobs}
            total={triage.total}
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
            bulkBar={bulkBar}
            loading={loading}
          />
          <JobDetail
            job={selectedJob}
            onBack={() => setMobileDetail(false)}
            onLetter={() => selectedJob && setLetterJob(selectedJob)}
            onPack={() => selectedJob && openPack([selectedJob])}
            onApplied={() => selectedJob && moveJobs([selectedJob], STATUS.applied, null, "Marquée postulée")}
            onArchive={() => selectedJob && moveJobs([selectedJob], STATUS.archived, null, "Archivée")}
            onTrash={() => selectedJob && moveJobs([selectedJob], STATUS.rejected, "manual_trash", "Mise à la corbeille")}
            onGradeV4={() => selectedJob && gradeV4([selectedJob])}
            onSaveNotes={async (text) => {
              if (!selectedJob) return;
              try {
                await api.saveNotes(selectedJob.id, text);
                setTriage((t) => ({ ...t, jobs: t.jobs.map((j) => (j.id === selectedJob.id ? { ...j, user_notes: text } : j)) }));
              } catch (e) {
                fail(e);
                throw e;
              }
            }}
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
      <Toast toast={toast} onDismiss={dismissToast} onPause={pauseToast} onResume={resumeToast} />
    </div>
  );
}
