"use client";

import React, { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { JobItem } from "@/lib/types";
import { Modal } from "./Modal";

type Phase = "loading" | "empty" | "generating" | "ready" | "error";

export function LetterModal({ job, onClose, onSaved }: { job: JobItem; onClose: () => void; onSaved: () => void }) {
  const [phase, setPhase] = useState<Phase>(job.cover_letter ? "loading" : "empty");
  const [text, setText] = useState("");
  const [savedText, setSavedText] = useState("");
  const [warnings, setWarnings] = useState<string[]>([]);
  const [instruction, setInstruction] = useState("");
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");

  // Lettre déjà en base : on la relit (avec la vérification automatique) sans appeler le LLM.
  useEffect(() => {
    if (!job.cover_letter) return;
    api
      .letter(job.id, {})
      .then((res) => {
        setText(res.cover_letter);
        setSavedText(res.cover_letter);
        setWarnings(res.warnings || []);
        setPhase("ready");
      })
      .catch((e: Error) => {
        setError(e.message);
        setPhase("error");
      });
  }, [job.id, job.cover_letter]);

  const generate = async () => {
    setPhase("generating");
    setError("");
    try {
      const res = await api.letter(job.id, { regenerate: true, custom_instruction: instruction });
      setText(res.cover_letter);
      setSavedText(res.cover_letter);
      setWarnings(res.warnings || []);
      setPhase("ready");
      onSaved();
    } catch (e) {
      setError((e as Error).message);
      setPhase(text ? "ready" : "error");
    }
  };

  const save = async () => {
    try {
      const res = await api.letter(job.id, { edited_text: text });
      setSavedText(text);
      setWarnings(res.warnings || []);
      setFeedback("Lettre enregistrée");
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const copy = async () => {
    await navigator.clipboard.writeText(text);
    setFeedback("Lettre copiée");
  };

  const dirty = text !== savedText;
  const words = text.trim() ? text.trim().split(/\s+/).length : 0;

  return (
    <Modal title="Lettre de motivation" subtitle={`${job.company}, ${job.title}`} onClose={onClose}>
      {phase === "loading" && <p className="muted">Chargement de la lettre enregistrée…</p>}

      {(phase === "empty" || phase === "generating" || (phase === "error" && !text)) && (
        <div className="letter-start">
          <p>
            Aucune lettre pour cette offre. La rédaction part de votre profil et de la fiche de poste, et prend
            une vingtaine de secondes.
          </p>
          <InstructionField value={instruction} onChange={setInstruction} />
          <button type="button" className="btn primary" onClick={generate} disabled={phase === "generating"}>
            {phase === "generating" ? "Rédaction en cours…" : "Rédiger la lettre"}
          </button>
        </div>
      )}

      {error && <p className="error-text">{error}</p>}

      {phase !== "loading" && text && (
        <>
          {warnings.length > 0 && (
            <div className="warnings">
              <strong>À vérifier avant envoi</strong>
              <ul>
                {warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            </div>
          )}
          <textarea
            className="field letter"
            aria-label="Texte de la lettre"
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              setFeedback("");
            }}
          />
          <p className="muted small">
            {words} mots, {text.length} caractères{dirty ? ", modifications non enregistrées" : ""}
          </p>
          <div className="modal-actions">
            <button type="button" className="btn primary" onClick={copy}>
              Copier
            </button>
            <button type="button" className="btn" onClick={save} disabled={!dirty}>
              Enregistrer
            </button>
            <a
              className={`btn ${dirty ? "disabled" : ""}`}
              href={`/api/jobs/${job.id}/cover-letter.pdf`}
              target="_blank"
              rel="noopener noreferrer"
              aria-disabled={dirty}
              title={dirty ? "Enregistrez d'abord vos modifications" : undefined}
              onClick={(e) => dirty && e.preventDefault()}
            >
              Télécharger le PDF
            </a>
            {feedback && <span className="muted">{feedback}</span>}
          </div>
          <details className="regen">
            <summary>Réécrire la lettre</summary>
            <InstructionField value={instruction} onChange={setInstruction} />
            <button type="button" className="btn" onClick={generate} disabled={phase === "generating"}>
              {phase === "generating" ? "Rédaction en cours…" : "Réécrire (remplace le texte actuel)"}
            </button>
          </details>
        </>
      )}
    </Modal>
  );
}

function InstructionField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <label className="field-label">
      Consigne pour la rédaction (facultatif)
      <input
        type="text"
        className="field"
        placeholder="Par exemple : insister sur PyTorch et la vision par ordinateur"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}
