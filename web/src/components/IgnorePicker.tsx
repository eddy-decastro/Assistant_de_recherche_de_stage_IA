"use client";

import React, { useEffect, useState } from "react";
import type { IgnoreReason, IgnoreReasonInfo } from "@/lib/types";
import { Modal } from "./Modal";

interface IgnorePickerProps {
  /** Libellé de ce qui est ignoré (« HGH Infrared » ou « 3 offres »). */
  target: string;
  reasons: IgnoreReasonInfo[];
  commentMax: number;
  onPick: (reason: IgnoreReason, comment: string) => void;
  onClose: () => void;
}

// Touche associée à la n-ième raison : 1..9 puis 0 pour la dixième.
const keyOf = (i: number) => (i < 9 ? String(i + 1) : i === 9 ? "0" : "");

// Choix rapide de la raison d'ignorer : un clic suffit ; au clavier, un chiffre choisit,
// Entrée valide, Échap annule. Le commentaire est optionnel.
export function IgnorePicker({ target, reasons, commentMax, onPick, onClose }: IgnorePickerProps) {
  const [chosen, setChosen] = useState<number | null>(null);
  const [comment, setComment] = useState("");

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test((e.target as HTMLElement)?.tagName || "");
      if (e.key === "Enter" && !e.shiftKey && chosen !== null) {
        e.preventDefault();
        e.stopPropagation();
        onPick(reasons[chosen].key, comment.trim());
        return;
      }
      if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
      const idx = reasons.findIndex((_, i) => keyOf(i) === e.key);
      if (idx >= 0) {
        e.preventDefault();
        e.stopPropagation();
        setChosen(idx);
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [chosen, comment, reasons, onPick]);

  return (
    <Modal title="Pourquoi ignorer ?" subtitle={target} onClose={onClose}>
      <div className="reasons" role="radiogroup" aria-label="Raison">
        {reasons.map((r, i) => (
          <button
            key={r.key}
            type="button"
            role="radio"
            aria-checked={chosen === i}
            className={`reason ${chosen === i ? "active" : ""}`}
            onClick={() => onPick(r.key, comment.trim())}
          >
            {keyOf(i) && <kbd>{keyOf(i)}</kbd>}
            {r.label}
          </button>
        ))}
      </div>
      <label className="field-label reason-comment">
        Commentaire (optionnel), utilisé par l&apos;IA comme exemple
        <textarea
          className="field"
          rows={2}
          maxLength={commentMax}
          placeholder="ex. mission de reporting, pas de modèle à entraîner"
          value={comment}
          onChange={(e) => setComment(e.target.value)}
        />
      </label>
      <p className="muted small">
        Clic sur une raison pour ignorer tout de suite. Au clavier : <kbd>1</kbd>…<kbd>9</kbd>, <kbd>0</kbd> choisir ·{" "}
        <kbd>Entrée</kbd> valider · <kbd>Échap</kbd> annuler.
      </p>
      <div className="modal-actions">
        <button
          type="button"
          className="btn primary"
          disabled={chosen === null}
          onClick={() => chosen !== null && onPick(reasons[chosen].key, comment.trim())}
        >
          Ignorer{chosen !== null ? ` : ${reasons[chosen].label}` : ""}
        </button>
        <button type="button" className="btn ghost" onClick={onClose}>
          Annuler
        </button>
      </div>
    </Modal>
  );
}
