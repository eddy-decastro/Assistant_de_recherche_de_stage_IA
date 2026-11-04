"use client";

import React, { useState } from "react";
import type { ClaudePackResponse } from "@/lib/types";
import { Modal } from "./Modal";

export function PackModal({ pack, onClose }: { pack: ClaudePackResponse; onClose: () => void }) {
  const [tab, setTab] = useState<"chrome" | "project">("chrome");
  const [copied, setCopied] = useState("");
  const content = tab === "chrome" ? pack.chrome_prompt : pack.claude_project_json;

  return (
    <Modal
      title="Pack Claude"
      subtitle={`${pack.count} offre${pack.count > 1 ? "s" : ""}, PDF enregistrés dans ${pack.output_dir}`}
      onClose={onClose}
    >
      <div className="segments" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "chrome"}
          className={`segment ${tab === "chrome" ? "active" : ""}`}
          onClick={() => setTab("chrome")}
        >
          Prompt pour Claude in Chrome
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
          ? "Collez ce prompt dans Claude in Chrome : il remplit les formulaires avec vos coordonnées et le chemin des PDF."
          : "Ajoutez ce fichier à un projet Claude : offres, scores, arguments et lettres."}
      </p>
      <textarea className="field letter" readOnly value={content} aria-label="Contenu du pack" />
      <div className="modal-actions">
        <button
          type="button"
          className="btn primary"
          onClick={async () => {
            await navigator.clipboard.writeText(content);
            setCopied(tab);
          }}
        >
          Copier
        </button>
        {copied === tab && <span className="muted">Copié</span>}
      </div>
      {pack.bundles.length > 0 && (
        <ul className="bundles">
          {pack.bundles.map((b) => (
            <li key={b.id}>
              <span>
                <strong>{b.company}</strong> {b.title}
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
