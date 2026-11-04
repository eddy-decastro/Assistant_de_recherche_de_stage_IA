"use client";

import React, { useState } from "react";
import { api } from "@/lib/api";
import type { ClaudePackResponse } from "@/lib/types";
import { Modal } from "./Modal";

// Règle d'envoi du prompt court (alignée sur build_queue_prompt dans src/matching/claude_pack.py).
const NO_SUBMIT_MARK = "Ne clique jamais sur le bouton final Envoyer";
const AUTO_SUBMIT_RULE =
  "   - Si rien d'obligatoire ne manque, vérifie chaque champ puis clique Envoyer ; sinon ne soumets pas.";

/** Prompt court adapté à l'option d'envoi automatique. */
function promptFor(base: string, autoSubmit: boolean): string {
  if (!autoSubmit || !base) return base;
  const lines = base.split("\n");
  const i = lines.findIndex((l) => l.includes(NO_SUBMIT_MARK));
  if (i >= 0) lines[i] = AUTO_SUBMIT_RULE;
  return lines.join("\n");
}

function plural(n: number, word: string) {
  return `${n} ${word}${n > 1 ? "s" : ""}`;
}

export function PackModal({ pack, onClose }: { pack: ClaudePackResponse; onClose: () => void }) {
  const [tab, setTab] = useState<"chrome" | "project">("chrome");
  const [copied, setCopied] = useState("");
  const [queue, setQueue] = useState(pack.queue);
  const [autoSubmit, setAutoSubmit] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const queueUrl = pack.queue_url || "http://localhost:3000/file";
  const inQueue = Boolean(queue?.enabled);
  // Repli sur l'ancien long prompt si l'API ne gère pas encore la file.
  const basePrompt = inQueue && pack.queue_prompt ? pack.queue_prompt : pack.chrome_prompt;
  const prompt = promptFor(basePrompt, autoSubmit);
  const content = tab === "chrome" ? prompt : pack.claude_project_json;
  const skipped = new Set(queue?.skipped ?? []);
  const ids = pack.bundles.map((b) => b.id).filter((id) => !skipped.has(id));

  // Ajoute (ou remet) les offres dans la file avec l'option d'envoi choisie.
  const enqueue = async (submit: boolean) => {
    if (!ids.length) return false;
    setBusy(true);
    setError("");
    try {
      const res = await api.queueAdd(ids, submit);
      setQueue({ added: res.added, requeued: res.requeued, skipped: res.skipped, enabled: true });
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const toggleAutoSubmit = async (next: boolean) => {
    if (await enqueue(next)) setAutoSubmit(next);
  };

  const inFile = (queue?.added.length ?? 0) + (queue?.requeued.length ?? 0);

  return (
    <Modal
      title="Pack Claude"
      subtitle={`${plural(pack.count, "offre")}, PDF enregistrés dans ${pack.output_dir}`}
      onClose={onClose}
    >
      <div className="warnings" role="status" style={{ marginBottom: 12 }}>
        {inQueue ? (
          <>
            <strong>{plural(inFile, "offre")} dans la file d&apos;attente</strong>
            {queue && queue.requeued.length > 0 && ` (dont ${queue.requeued.length} remise${queue.requeued.length > 1 ? "s" : ""} en attente)`}
            {queue && queue.skipped.length > 0 &&
              `, ${plural(queue.skipped.length, "offre")} déjà postulée${queue.skipped.length > 1 ? "s" : ""} ignorée${queue.skipped.length > 1 ? "s" : ""}`}
            .{" "}
            <a className="link" href="/file" target="_blank" rel="noopener noreferrer">
              Ouvrir la file (/file)
            </a>
          </>
        ) : (
          <>
            Ces offres ne sont pas encore dans la file d&apos;attente.{" "}
            <button type="button" className="link" disabled={busy || !ids.length} onClick={() => void enqueue(false)}>
              Ajouter à la file
            </button>
          </>
        )}
      </div>

      <div className="segments" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "chrome"}
          className={`segment ${tab === "chrome" ? "active" : ""}`}
          onClick={() => setTab("chrome")}
        >
          Prompt court Claude in Chrome
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "project"}
          className={`segment ${tab === "project" ? "active" : ""}`}
          onClick={() => setTab("project")}
        >
          JSON pour un projet Claude
        </button>
      </div>
      <p className="muted small">
        {tab === "chrome"
          ? `Collez ce prompt dans Claude in Chrome : il ouvre ${queueUrl}, remplit chaque offre de la file, écrit un compte rendu et passe à la suivante. Le CV et la lettre PDF restent à joindre par vous.`
          : "Ajoutez ce fichier à un projet Claude : offres, scores, arguments et lettres."}
      </p>
      <textarea
        className="field letter"
        style={tab === "chrome" && inQueue ? { minHeight: 220 } : undefined}
        readOnly
        value={content}
        aria-label={tab === "chrome" ? "Prompt court Claude in Chrome" : "JSON pour un projet Claude"}
      />
      <div className="modal-actions">
        <button
          type="button"
          className="btn primary"
          onClick={async () => {
            await navigator.clipboard.writeText(content);
            setCopied(`${tab}-${autoSubmit}`);
          }}
        >
          Copier
        </button>
        {copied === `${tab}-${autoSubmit}` && <span className="muted">Copié</span>}
        {inQueue && (
          <a className="btn" href="/file" target="_blank" rel="noopener noreferrer">
            Ouvrir la file d&apos;attente
          </a>
        )}
      </div>

      {inQueue && tab === "chrome" && (
        <div style={{ marginTop: 14, display: "grid", gap: 6 }}>
          <label style={{ display: "flex", gap: 8, alignItems: "center", fontWeight: 550 }}>
            <input
              type="checkbox"
              checked={autoSubmit}
              disabled={busy || !ids.length}
              onChange={(e) => void toggleAutoSubmit(e.target.checked)}
            />
            Laisser Claude cliquer Envoyer (envoi automatique)
          </label>
          <p className="small" style={{ color: autoSubmit ? "var(--t-alert)" : "var(--muted)" }}>
            {autoSubmit
              ? "Attention : Claude enverra les candidatures sans relecture quand rien d'obligatoire ne manque. Une candidature envoyée ne se reprend pas."
              : "Désactivé (recommandé) : Claude remplit les formulaires sans les envoyer, vous relisez et cliquez Envoyer."}
          </p>
          {busy && <p className="muted small">Mise à jour de la file…</p>}
        </div>
      )}
      {error && <p className="error-text">{error}</p>}

      {pack.bundles.length > 0 && (
        <ul className="bundles">
          {pack.bundles.map((b) => (
            <li key={b.id}>
              <span>
                <strong>{b.company}</strong> {b.title}
                {skipped.has(b.id) && <span className="muted small"> (déjà postulée)</span>}
              </span>
              {b.url && (
                <a className="link" href={b.url} target="_blank" rel="noopener noreferrer">
                  Offre
                </a>
              )}
            </li>
          ))}
        </ul>
      )}
    </Modal>
  );
}
