"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";

interface V4Axis {
  key: string;
  label: string;
  stars: number;
  reason: string;
  quote: string;
  unknown: boolean;
  tone: string;
}

interface V4Tag {
  key: string;
  label: string;
  tone: string;
}

interface SubScore {
  key: string;
  label: string;
  short: string;
  value: number;
  tone: string;
  evidence: string;
}

interface JobItem {
  id: string;
  title: string;
  company: string;
  location: string;
  date_label: string;
  age_days: number | null;
  source_label: string;
  source_color: string;
  score: number;
  score_origin: string;
  align_label: string;
  align_tone: string;
  reranked: boolean;
  verdict_label: string;
  verdict_tone: string;
  status: string;
  status_label: string;
  is_new: boolean;
  hard_cap: string;
  badges: { label: string; tone: string }[];
  sub_scores: SubScore[];
  technologies: string[];
  strengths: string[];
  red_flags: string[];
  reasoning: string;
  description: string;
  rejection_reason: string;
  url: string;
  company_note: string;
  questions_entretien: string[];
  user_notes: string;
  cover_letter: string;
  v4_class: string;
  v4_class_label: string;
  v4_class_tone: string;
  v4_display_score: number | null;
  v4_class_score: number | null;
  v4_center: number | null;
  v4_adjustment: number | null;
  v4_axes: V4Axis[];
  v4_tags: V4Tag[];
  v4_flags: string[];
  v4_summary: string;
  v4_class_reason: string;
}

interface StatsData {
  total: number;
  by_status: Record<string, number>;
  by_v4_class: Record<string, number>;
  old_new_14d: number;
  v3_graded: number;
  v4_graded: number;
  v4_classes: { key: string; label: string }[];
  v4_tags: { key: string; label: string }[];
}

interface ClaudePackResponse {
  count: number;
  output_dir: string;
  chrome_prompt: string;
  claude_project_json: string;
  bundles: {
    id: string;
    title: string;
    company: string;
    url: string;
    pdf_path: string;
  }[];
}

function formatStars(n: number): string {
  const s = Math.max(0, Math.min(5, Math.round(n || 0)));
  return "★".repeat(s) + "☆".repeat(5 - s);
}

const KANBAN_COLUMNS = [
  { code: "NOUVEAU", label: "Nouveau" },
  { code: "POSTULÉ", label: "Postulé" },
  { code: "ENTRETIEN", label: "Entretien" },
  { code: "IGNORÉ", label: "Archivé" },
  { code: "REJETÉ", label: "Corbeille" },
];

export default function StageCopilotPage() {
  const [view, setView] = useState<"flux" | "kanban">("flux");
  const [jobs, setJobs] = useState<JobItem[]>([]);
  const [stats, setStats] = useState<StatsData | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [checkedIds, setCheckedIds] = useState<Set<string>>(new Set());
  const [lastCheckedId, setLastCheckedId] = useState<string | null>(null);

  // Filters
  const [query, setQuery] = useState("");
  const [selectedClasses, setSelectedClasses] = useState<string[]>([]);
  const [includedTags, setIncludedTags] = useState<string[]>([]);
  const [excludedTags, setExcludedTags] = useState<string[]>([]);
  const [hideProcessed, setHideProcessed] = useState(true);
  const [onlyOlder14d, setOnlyOlder14d] = useState(false);
  const [sortBy, setSortBy] = useState<"v3" | "v4" | "v4_class" | "freshness">("v3");

  // Modals & busy states
  const [busyMessage, setBusyMessage] = useState<string | null>(null);
  const [letterModalJob, setLetterModalJob] = useState<JobItem | null>(null);
  const [letterText, setLetterText] = useState("");
  const [letterInstruction, setLetterInstruction] = useState("");
  const [claudePack, setClaudePack] = useState<ClaudePackResponse | null>(null);
  const [notesDraft, setNotesDraft] = useState("");

  const fetchAll = useCallback(async () => {
    const params = new URLSearchParams();
    if (query.trim()) params.set("q", query.trim());
    if (selectedClasses.length) params.set("v4_classes", selectedClasses.join(","));
    if (includedTags.length) params.set("v4_tags", includedTags.join(","));
    if (excludedTags.length) params.set("exclude_v4_tags", excludedTags.join(","));
    if (view === "flux" && hideProcessed) params.set("hide_processed", "true");
    if (onlyOlder14d) params.set("only_older_than_days", "14");
    params.set("sort_by", sortBy);
    params.set("limit", view === "kanban" ? "600" : "250");

    const [jobsRes, statsRes] = await Promise.all([
      fetch(`/api/jobs?${params.toString()}`),
      fetch("/api/stats"),
    ]);
    if (jobsRes.ok) {
      const data = await jobsRes.json();
      setJobs(data.jobs || []);
      if (data.jobs?.length && (!selectedId || !data.jobs.some((j: JobItem) => j.id === selectedId))) {
        setSelectedId(data.jobs[0].id);
      }
    }
    if (statsRes.ok) {
      setStats(await statsRes.json());
    }
  }, [query, selectedClasses, includedTags, excludedTags, hideProcessed, onlyOlder14d, sortBy, view, selectedId]);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  const selectedJob = useMemo(
    () => jobs.find((j) => j.id === selectedId) || jobs[0] || null,
    [jobs, selectedId]
  );

  useEffect(() => {
    setNotesDraft(selectedJob?.user_notes || "");
  }, [selectedJob?.id, selectedJob?.user_notes]);

  const updateStatus = useCallback(
    async (ids: string[], status: string, reason?: string) => {
      if (!ids.length) return;
      await fetch("/api/jobs/bulk-status", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids, status, rejection_reason: reason || null }),
      });
      setCheckedIds((prev) => {
        const next = new Set(prev);
        ids.forEach((id) => next.delete(id));
        return next;
      });
      await fetchAll();
    },
    [fetchAll]
  );

  const handleQuickArchive14d = async (action: "archive" | "trash") => {
    setBusyMessage(action === "archive" ? "Archivage des offres > 14j..." : "Mise en corbeille > 14j...");
    try {
      await fetch("/api/jobs/quick-archive-old", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ days: 14, action }),
      });
      setOnlyOlder14d(false);
      await fetchAll();
    } finally {
      setBusyMessage(null);
    }
  };

  const openCoverLetter = async (job: JobItem, regenerate = false) => {
    setLetterModalJob(job);
    setBusyMessage("Génération / chargement de la lettre...");
    try {
      const res = await fetch(`/api/jobs/${job.id}/cover-letter`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          regenerate,
          custom_instruction: letterInstruction,
        }),
      });
      if (res.ok) {
        const data = await res.json();
        setLetterText(data.cover_letter || "");
        await fetchAll();
      }
    } finally {
      setBusyMessage(null);
    }
  };

  const saveCoverLetter = async () => {
    if (!letterModalJob) return;
    await fetch(`/api/jobs/${letterModalJob.id}/cover-letter`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        regenerate: false,
        edited_text: letterText,
      }),
    });
    await fetchAll();
  };

  const openClaudePack = async (ids: string[]) => {
    if (!ids.length) return;
    setBusyMessage(`Préparation du Pack Claude (${ids.length} offre(s))...`);
    try {
      const res = await fetch("/api/claude-pack", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids, auto_submit: false }),
      });
      if (res.ok) {
        setClaudePack(await res.json());
      }
    } finally {
      setBusyMessage(null);
    }
  };

  const gradeSelectedInV4 = async (ids: string[]) => {
    if (!ids.length) return;
    setBusyMessage(`Notation v4 en cours (${ids.length} offre(s))...`);
    try {
      await fetch("/api/jobs/grade-v4", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids: ids.slice(0, 25) }),
      });
      await fetchAll();
    } finally {
      setBusyMessage(null);
    }
  };

  const toggleCheck = (id: string, shiftKey: boolean) => {
    const visibleIds = jobs.map((j) => j.id);
    setCheckedIds((prev) => {
      const next = new Set(prev);
      const shouldAdd = !next.has(id);
      if (shiftKey && lastCheckedId && visibleIds.includes(lastCheckedId) && visibleIds.includes(id)) {
        const i1 = visibleIds.indexOf(lastCheckedId);
        const i2 = visibleIds.indexOf(id);
        const [start, end] = i1 < i2 ? [i1, i2] : [i2, i1];
        for (let i = start; i <= end; i += 1) {
          if (shouldAdd) next.add(visibleIds[i]);
          else next.delete(visibleIds[i]);
        }
      } else if (shouldAdd) {
        next.add(id);
      } else {
        next.delete(id);
      }
      return next;
    });
    setLastCheckedId(id);
  };

  // Keyboard shortcuts
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName || "";
      if (/^(INPUT|TEXTAREA|SELECT)$/.test(tag)) return;
      if (!jobs.length) return;
      const idx = jobs.findIndex((j) => j.id === selectedJob?.id);
      if (e.key === "j" || e.key === "ArrowDown") {
        e.preventDefault();
        const next = jobs[Math.min(jobs.length - 1, Math.max(0, idx + 1))];
        if (next) setSelectedId(next.id);
      } else if (e.key === "k" || e.key === "ArrowUp") {
        e.preventDefault();
        const prev = jobs[Math.max(0, idx - 1)];
        if (prev) setSelectedId(prev.id);
      } else if (e.key === "x" && selectedJob) {
        updateStatus([selectedJob.id], "IGNORÉ");
      } else if (e.key === "p" && selectedJob) {
        updateStatus([selectedJob.id], "POSTULÉ");
      } else if (e.key === "l" && selectedJob) {
        openCoverLetter(selectedJob);
      } else if (e.key === "c" && selectedJob) {
        openClaudePack(checkedIds.size > 0 ? Array.from(checkedIds) : [selectedJob.id]);
      } else if (e.key === "o" && selectedJob?.url) {
        window.open(selectedJob.url, "_blank", "noopener,noreferrer");
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  });

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-row">
          <div className="brand-title">Stage Copilot · v3 + v4</div>
          <nav className="nav-tabs">
            <button
              type="button"
              className={`tab-btn ${view === "flux" ? "active" : ""}`}
              onClick={() => setView("flux")}
            >
              Flux ({jobs.length})
            </button>
            <button
              type="button"
              className={`tab-btn ${view === "kanban" ? "active" : ""}`}
              onClick={() => setView("kanban")}
            >
              Kanban ({stats?.total || 0})
            </button>
          </nav>
        </div>

        <div className="toolbar">
          <input
            type="search"
            className="input"
            placeholder="Rechercher titre, entreprise, stack..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <select
            className="select"
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value as "v3" | "v4" | "v4_class" | "freshness")}
          >
            <option value="v3">Tri : Score v3 (par défaut)</option>
            <option value="v4">Tri : Note affichée v4</option>
            <option value="v4_class">Tri : Note de classe v4</option>
            <option value="freshness">Tri : Fraîcheur (date)</option>
          </select>
          <label className="btn" style={{ gap: 6 }}>
            <input
              type="checkbox"
              checked={hideProcessed}
              onChange={(e) => setHideProcessed(e.target.checked)}
            />
            Nouveau seul
          </label>
          <button
            type="button"
            className="btn primary"
            onClick={() =>
              openClaudePack(
                checkedIds.size > 0
                  ? Array.from(checkedIds)
                  : selectedJob
                  ? [selectedJob.id]
                  : []
              )
            }
          >
            Pack Claude ({checkedIds.size || (selectedJob ? 1 : 0)})
          </button>
        </div>
      </header>

      {busyMessage && (
        <div className="banner-14d" style={{ background: "rgba(59,130,246,0.18)" }}>
          <span>{busyMessage}</span>
        </div>
      )}

      {stats && stats.old_new_14d > 0 && (
        <div className="banner-14d">
          <div>
            <strong>{stats.old_new_14d} offre(s) NOUVEAU</strong> ont plus de 14 jours (archivage rapide facultatif).
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button
              type="button"
              className="btn"
              onClick={() => setOnlyOlder14d((v) => !v)}
            >
              {onlyOlder14d ? "Afficher toutes les dates" : "Filtrer > 14 jours"}
            </button>
            <button
              type="button"
              className="btn warn"
              onClick={() => handleQuickArchive14d("archive")}
            >
              Archiver &gt; 14j ({stats.old_new_14d})
            </button>
            <button
              type="button"
              className="btn alert"
              onClick={() => handleQuickArchive14d("trash")}
            >
              Corbeille &gt; 14j ({stats.old_new_14d})
            </button>
          </div>
        </div>
      )}

      <div className="filter-strip">
        <span style={{ fontSize: 12, color: "var(--fg-muted)", fontWeight: 600 }}>Classes v4 :</span>
        {(stats?.v4_classes || [
          { key: "A", label: "Recherche" },
          { key: "B", label: "IA / R&D" },
          { key: "C", label: "Domaine voisin" },
          { key: "D", label: "ESN" },
          { key: "E", label: "Hors cible" },
        ]).map((c) => {
          const active = selectedClasses.includes(c.key);
          const count = stats?.by_v4_class?.[c.key] ?? 0;
          return (
            <button
              key={c.key}
              type="button"
              className={`pill ${active ? "active" : ""}`}
              onClick={() =>
                setSelectedClasses((prev) =>
                  prev.includes(c.key) ? prev.filter((x) => x !== c.key) : [...prev, c.key]
                )
              }
              title={`${c.label} (cliquer pour filtrer ; 1 classe = tri par note de classe)`}
            >
              Classe {c.key} ({count})
            </button>
          );
        })}

        <span style={{ fontSize: 12, color: "var(--fg-muted)", fontWeight: 600, marginLeft: 10 }}>
          Étiquettes (clic=inclure, Shift+clic=exclure) :
        </span>
        {(stats?.v4_tags || []).slice(0, 8).map((t) => {
          const inc = includedTags.includes(t.key);
          const exc = excludedTags.includes(t.key);
          return (
            <button
              key={t.key}
              type="button"
              className={`pill ${inc ? "active" : ""} ${exc ? "excluded" : ""}`}
              onClick={(e) => {
                if (e.shiftKey) {
                  setIncludedTags((p) => p.filter((x) => x !== t.key));
                  setExcludedTags((p) => (p.includes(t.key) ? p.filter((x) => x !== t.key) : [...p, t.key]));
                } else {
                  setExcludedTags((p) => p.filter((x) => x !== t.key));
                  setIncludedTags((p) => (p.includes(t.key) ? p.filter((x) => x !== t.key) : [...p, t.key]));
                }
              }}
            >
              {t.label}
            </button>
          );
        })}
      </div>

      {view === "flux" ? (
        <div className="split-view">
          <section className="feed-list-pane">
            <div className="feed-list-head">
              <span style={{ fontSize: 12.5, color: "var(--fg-muted)" }}>
                {jobs.length} offre(s) · v4 notées : {stats?.v4_graded ?? 0}
              </span>
              <div style={{ display: "flex", gap: 6 }}>
                <button
                  type="button"
                  className="btn"
                  style={{ height: 26, fontSize: 11.5 }}
                  onClick={() => {
                    if (checkedIds.size === jobs.length) {
                      setCheckedIds(new Set());
                    } else {
                      setCheckedIds(new Set(jobs.map((j) => j.id)));
                    }
                  }}
                >
                  {checkedIds.size === jobs.length && jobs.length > 0 ? "Tout décocher" : "Tout sélectionner"}
                </button>
              </div>
            </div>

            {checkedIds.size > 0 && (
              <div className="bulk-toolbar">
                <strong style={{ fontSize: 12 }}>{checkedIds.size} sélectionnée(s)</strong>
                <button
                  type="button"
                  className="btn warn"
                  style={{ height: 28 }}
                  onClick={() => updateStatus(Array.from(checkedIds), "IGNORÉ", "bulk_archive")}
                >
                  Archiver
                </button>
                <button
                  type="button"
                  className="btn alert"
                  style={{ height: 28 }}
                  onClick={() => updateStatus(Array.from(checkedIds), "REJETÉ", "bulk_trash")}
                >
                  Corbeille
                </button>
                <button
                  type="button"
                  className="btn positive"
                  style={{ height: 28 }}
                  onClick={() => updateStatus(Array.from(checkedIds), "POSTULÉ")}
                >
                  Postulé
                </button>
                <button
                  type="button"
                  className="btn"
                  style={{ height: 28 }}
                  onClick={() => gradeSelectedInV4(Array.from(checkedIds))}
                >
                  Noter v4
                </button>
                <button
                  type="button"
                  className="btn primary"
                  style={{ height: 28 }}
                  onClick={() => openClaudePack(Array.from(checkedIds))}
                >
                  Pack Claude
                </button>
              </div>
            )}

            <ul className="feed-list">
              {jobs.map((job) => {
                const isSelected = selectedJob?.id === job.id;
                const isChecked = checkedIds.has(job.id);
                return (
                  <li
                    key={job.id}
                    className={`feed-row ${isSelected ? "selected" : ""}`}
                    onClick={() => setSelectedId(job.id)}
                  >
                    <input
                      type="checkbox"
                      checked={isChecked}
                      onClick={(e) => {
                        e.stopPropagation();
                        toggleCheck(job.id, e.shiftKey);
                      }}
                      onChange={() => {}}
                    />
                    <div
                      className={`score-badge tone-${job.align_tone}`}
                      title={`Score v3 : ${job.score}/100 (${job.score_origin})`}
                    >
                      {job.score}
                    </div>
                    <div style={{ minWidth: 0 }}>
                      <div className="row-title" title={job.title}>
                        {job.title}
                      </div>
                      <div className="row-meta">
                        {[job.company, job.location, job.date_label].filter(Boolean).join(" · ")}
                      </div>
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                      {job.v4_class && job.v4_display_score !== null && (
                        <span
                          className={`v4-badge tone-${job.v4_class_tone}`}
                          title={`Classe ${job.v4_class} · Note affichée v4 : ${job.v4_display_score}/100`}
                        >
                          {job.v4_class} · {job.v4_display_score}
                        </span>
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          </section>

          <section className="detail-pane">
            {!selectedJob ? (
              <div style={{ color: "var(--fg-muted)", padding: 40, textAlign: "center" }}>
                Aucune offre sélectionnée.
              </div>
            ) : (
              <>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 16 }}>
                  <div>
                    <h1 style={{ margin: "0 0 6px", fontSize: 21 }}>{selectedJob.title}</h1>
                    <div style={{ color: "var(--fg-muted)", fontSize: 13.5 }}>
                      {[
                        selectedJob.company,
                        selectedJob.location,
                        selectedJob.date_label,
                        selectedJob.source_label,
                        selectedJob.status_label,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                    <div className={`score-badge tone-${selectedJob.align_tone}`} style={{ width: 58, height: 52 }}>
                      <div style={{ textAlign: "center", lineHeight: 1.1 }}>
                        <div style={{ fontSize: 17 }}>{selectedJob.score}</div>
                        <div style={{ fontSize: 10, opacity: 0.75 }}>v3</div>
                      </div>
                    </div>
                    {selectedJob.v4_class && selectedJob.v4_display_score !== null && (
                      <div
                        className={`score-badge tone-${selectedJob.v4_class_tone}`}
                        style={{ width: 72, height: 52 }}
                      >
                        <div style={{ textAlign: "center", lineHeight: 1.1 }}>
                          <div style={{ fontSize: 16 }}>
                            {selectedJob.v4_class} · {selectedJob.v4_display_score}
                          </div>
                          <div style={{ fontSize: 10, opacity: 0.75 }}>v4</div>
                        </div>
                      </div>
                    )}
                  </div>
                </div>

                <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 14 }}>
                  {selectedJob.url && (
                    <a
                      href={selectedJob.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="btn primary"
                    >
                      Postuler ↗
                    </a>
                  )}
                  <button
                    type="button"
                    className="btn"
                    onClick={() => openCoverLetter(selectedJob)}
                  >
                    Lettre de motivation
                  </button>
                  <button
                    type="button"
                    className="btn primary"
                    onClick={() => openClaudePack([selectedJob.id])}
                  >
                    Pack Claude
                  </button>
                  <button
                    type="button"
                    className="btn positive"
                    onClick={() => updateStatus([selectedJob.id], "POSTULÉ")}
                  >
                    Marquer postulé (p)
                  </button>
                  <button
                    type="button"
                    className="btn warn"
                    onClick={() => updateStatus([selectedJob.id], "IGNORÉ")}
                  >
                    Archiver (x)
                  </button>
                  <button
                    type="button"
                    className="btn alert"
                    onClick={() => updateStatus([selectedJob.id], "REJETÉ", "manual_trash")}
                  >
                    Corbeille
                  </button>
                  <button
                    type="button"
                    className="btn"
                    onClick={() => gradeSelectedInV4([selectedJob.id])}
                  >
                    Noter en v4 (Gemini Flash)
                  </button>
                </div>

                {/* Section v4 : étoiles + fourchette + classe */}
                <div className="card-section">
                  <h3>Notation v4 (« étoiles + fourchette + classe »)</h3>
                  {!selectedJob.v4_class ? (
                    <div style={{ color: "var(--fg-muted)", fontSize: 13 }}>
                      Offre non encore évaluée en v4. Cliquez sur{" "}
                      <strong>Noter en v4 (Gemini Flash)</strong> ci-dessus pour calculer sa classe et ses étoiles.
                    </div>
                  ) : (
                    <>
                      <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center", marginBottom: 10 }}>
                        <span className={`v4-badge tone-${selectedJob.v4_class_tone}`}>
                          Classe {selectedJob.v4_class} · {selectedJob.v4_display_score}/100
                        </span>
                        <span style={{ color: "var(--fg-muted)", fontSize: 13 }}>
                          {selectedJob.v4_class_label} · Note de classe :{" "}
                          <strong>{selectedJob.v4_class_score}/100</strong> (centre{" "}
                          {selectedJob.v4_center?.toFixed(1)}, ajustement{" "}
                          {(selectedJob.v4_adjustment ?? 0) >= 0
                            ? `+${selectedJob.v4_adjustment ?? 0}`
                            : selectedJob.v4_adjustment}
                          )
                        </span>
                      </div>
                      {selectedJob.v4_summary && (
                        <p style={{ margin: "0 0 8px", fontWeight: 500 }}>{selectedJob.v4_summary}</p>
                      )}
                      {selectedJob.v4_class_reason && (
                        <p style={{ margin: "0 0 12px", fontSize: 12.5, color: "var(--fg-muted)" }}>
                          Mission / classe : {selectedJob.v4_class_reason}
                        </p>
                      )}
                      <div className="axes-grid">
                        {selectedJob.v4_axes.map((ax) => (
                          <div
                            key={ax.key}
                            className="axis-card"
                            title={`${ax.reason}${ax.quote ? ` — « ${ax.quote} »` : ""}`}
                          >
                            <div className="axis-head">
                              <span>{ax.label}</span>
                              <span className={`stars-str tone-${ax.tone}`} style={{ padding: "0 6px", borderRadius: 4 }}>
                                {formatStars(ax.stars)} ({ax.stars}/5)
                              </span>
                            </div>
                            {ax.reason && <div className="axis-reason">{ax.reason}</div>}
                            {ax.quote && <div className="axis-quote">« {ax.quote} »</div>}
                          </div>
                        ))}
                      </div>
                      {(selectedJob.v4_tags.length > 0 || selectedJob.v4_flags.length > 0) && (
                        <div className="chips-row">
                          {selectedJob.v4_tags.map((t) => (
                            <span key={t.key} className={`chip tone-${t.tone}`}>
                              {t.label}
                            </span>
                          ))}
                          {selectedJob.v4_flags.map((f) => (
                            <span key={f} className="chip tone-warn">
                              {f}
                            </span>
                          ))}
                        </div>
                      )}
                    </>
                  )}
                </div>

                {/* Section v3 : conservée en parallèle */}
                <div className="card-section">
                  <h3>Grille v3 (en parallèle) — Score {selectedJob.score}/100</h3>
                  {selectedJob.sub_scores.length > 0 && (
                    <div className="axes-grid" style={{ marginBottom: 10 }}>
                      {selectedJob.sub_scores.map((s) => (
                        <div key={s.key} className="axis-card" title={s.evidence || s.label}>
                          <div className="axis-head">
                            <span>{s.label}</span>
                            <span className={`chip tone-${s.tone}`}>{s.value}/5</span>
                          </div>
                          {s.evidence && <div className="axis-quote">« {s.evidence} »</div>}
                        </div>
                      ))}
                    </div>
                  )}
                  {selectedJob.strengths.length > 0 && (
                    <div style={{ fontSize: 13, marginTop: 6 }}>
                      <strong>Points forts :</strong> {selectedJob.strengths.join(" · ")}
                    </div>
                  )}
                  {selectedJob.red_flags.length > 0 && (
                    <div style={{ fontSize: 13, marginTop: 4, color: "#f87171" }}>
                      <strong>Vigilance :</strong> {selectedJob.red_flags.join(" · ")}
                    </div>
                  )}
                </div>

                {/* Notes personnelles */}
                <div className="card-section">
                  <h3>Notes personnelles & Suivi</h3>
                  <div style={{ display: "flex", gap: 8 }}>
                    <input
                      type="text"
                      className="input"
                      style={{ flex: 1 }}
                      placeholder="Ajouter une note (contact, relance, ressenti...)"
                      value={notesDraft}
                      onChange={(e) => setNotesDraft(e.target.value)}
                    />
                    <button
                      type="button"
                      className="btn"
                      onClick={async () => {
                        await fetch(`/api/jobs/${selectedJob.id}/notes`, {
                          method: "POST",
                          headers: { "Content-Type": "application/json" },
                          body: JSON.stringify({ user_notes: notesDraft }),
                        });
                        await fetchAll();
                      }}
                    >
                      Enregistrer
                    </button>
                  </div>
                </div>

                {/* Fiche de poste */}
                <div className="card-section">
                  <h3>Fiche de poste</h3>
                  <pre
                    style={{
                      margin: 0,
                      whiteSpace: "pre-wrap",
                      fontFamily: "inherit",
                      lineHeight: 1.55,
                      fontSize: 13.5,
                    }}
                  >
                    {selectedJob.description || "Aucune description disponible."}
                  </pre>
                </div>
              </>
            )}
          </section>
        </div>
      ) : (
        <div className="kanban-board">
          {KANBAN_COLUMNS.map((col) => {
            const colJobs = jobs.filter((j) => j.status === col.code);
            return (
              <div key={col.code} className="kanban-col">
                <div className="kanban-col-head">
                  <span>{col.label}</span>
                  <span style={{ color: "var(--fg-muted)" }}>{colJobs.length}</span>
                </div>
                <div className="kanban-cards">
                  {colJobs.slice(0, 60).map((job) => (
                    <div key={job.id} className="kanban-card">
                      <div style={{ display: "flex", justifyContent: "space-between", gap: 6 }}>
                        <strong style={{ fontSize: 13 }}>{job.company}</strong>
                        <div style={{ display: "flex", gap: 4 }}>
                          {job.v4_class && job.v4_display_score !== null && (
                            <span className={`v4-badge tone-${job.v4_class_tone}`}>
                              {job.v4_class} · {job.v4_display_score}
                            </span>
                          )}
                          <span className={`chip tone-${job.align_tone}`}>{job.score}</span>
                        </div>
                      </div>
                      <div style={{ fontSize: 12.5, marginTop: 4 }}>{job.title}</div>
                      <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 8 }}>
                        {col.code !== "POSTULÉ" && (
                          <button
                            type="button"
                            className="btn positive"
                            style={{ height: 24, padding: "0 8px", fontSize: 11 }}
                            onClick={() => updateStatus([job.id], "POSTULÉ")}
                          >
                            Postulé
                          </button>
                        )}
                        {col.code !== "ENTRETIEN" && (
                          <button
                            type="button"
                            className="btn"
                            style={{ height: 24, padding: "0 8px", fontSize: 11 }}
                            onClick={() => updateStatus([job.id], "ENTRETIEN")}
                          >
                            Entretien
                          </button>
                        )}
                        {col.code !== "IGNORÉ" && (
                          <button
                            type="button"
                            className="btn warn"
                            style={{ height: 24, padding: "0 8px", fontSize: 11 }}
                            onClick={() => updateStatus([job.id], "IGNORÉ")}
                          >
                            Archiver
                          </button>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Modal Lettre de motivation */}
      {letterModalJob && (
        <div className="modal-backdrop" onClick={() => setLetterModalJob(null)}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
              <h2 style={{ margin: 0, fontSize: 18 }}>
                Lettre de motivation — {letterModalJob.company} ({letterModalJob.title})
              </h2>
              <button type="button" className="btn" onClick={() => setLetterModalJob(null)}>
                Fermer
              </button>
            </div>
            <textarea
              className="textarea-full"
              value={letterText}
              onChange={(e) => setLetterText(e.target.value)}
            />
            <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
              <input
                type="text"
                className="input"
                style={{ flex: 1 }}
                placeholder="Consigne spécifique pour régénérer (ex: insister sur PyTorch et PINNs...)"
                value={letterInstruction}
                onChange={(e) => setLetterInstruction(e.target.value)}
              />
              <button
                type="button"
                className="btn"
                onClick={() => openCoverLetter(letterModalJob, true)}
              >
                Régénérer via IA
              </button>
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 14 }}>
              <button
                type="button"
                className="btn primary"
                onClick={() => {
                  navigator.clipboard.writeText(letterText);
                }}
              >
                Copier la lettre
              </button>
              <button type="button" className="btn positive" onClick={saveCoverLetter}>
                Sauvegarder les modifications
              </button>
              <a
                href={`/api/jobs/${letterModalJob.id}/cover-letter.pdf`}
                className="btn"
                target="_blank"
                rel="noopener noreferrer"
              >
                Télécharger PDF
              </a>
            </div>
          </div>
        </div>
      )}

      {/* Modal Pack Claude */}
      {claudePack && (
        <div className="modal-backdrop" onClick={() => setClaudePack(null)}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
              <h2 style={{ margin: 0, fontSize: 18 }}>
                Pack Claude ({claudePack.count} offre(s)) — Claude in Chrome & Projet Claude
              </h2>
              <button type="button" className="btn" onClick={() => setClaudePack(null)}>
                Fermer
              </button>
            </div>
            <p style={{ color: "var(--fg-muted)", fontSize: 13, marginTop: 0 }}>
              PDFs générés automatiquement dans : <code>{claudePack.output_dir}</code>
            </p>
            <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
              <button
                type="button"
                className="btn primary"
                onClick={() => navigator.clipboard.writeText(claudePack.chrome_prompt)}
              >
                Copier le Prompt Claude in Chrome
              </button>
              <button
                type="button"
                className="btn"
                onClick={() => navigator.clipboard.writeText(claudePack.claude_project_json)}
              >
                Copier le JSON Projet Claude
              </button>
            </div>
            <textarea
              className="textarea-full"
              readOnly
              value={claudePack.chrome_prompt}
            />
          </div>
        </div>
      )}
    </div>
  );
}
