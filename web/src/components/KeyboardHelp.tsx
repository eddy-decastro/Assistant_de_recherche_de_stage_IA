"use client";

import React from "react";
import { Modal } from "./Modal";

// Raccourcis de la vue « À trier ». Ils ne se déclenchent pas quand on tape dans un champ.
export const SHORTCUTS: { keys: string[]; label: string }[] = [
  { keys: ["J", "↓"], label: "Offre suivante" },
  { keys: ["K", "↑"], label: "Offre précédente" },
  { keys: ["O"], label: "Ouvrir l'offre (nouvel onglet)" },
  { keys: ["P"], label: "Marquer postulé" },
  { keys: ["I"], label: "Ignorer : choisir la raison (1…9, 0), Entrée pour valider, Échap pour annuler" },
  { keys: ["L"], label: "Lettre de motivation" },
  { keys: ["?"], label: "Afficher cette aide" },
];

/** Ligne d'aide discrète, visible en permanence sous la liste. */
export function ShortcutHint({ onHelp }: { onHelp: () => void }) {
  return (
    <p className="shortcuts">
      <kbd>J</kbd>/<kbd>K</kbd> naviguer · <kbd>O</kbd> ouvrir · <kbd>P</kbd> postulé · <kbd>I</kbd> ignorer ·{" "}
      <button type="button" className="link-quiet" onClick={onHelp}>
        <kbd>?</kbd> aide
      </button>
    </p>
  );
}

export function KeyboardHelp({ onClose }: { onClose: () => void }) {
  return (
    <Modal title="Raccourcis clavier" subtitle="Dans la vue « À trier », hors des champs de saisie." onClose={onClose}>
      <dl className="help-keys">
        {SHORTCUTS.map((s) => (
          <div key={s.label}>
            <dt>
              {s.keys.map((k, i) => (
                <React.Fragment key={k}>
                  {i > 0 && " ou "}
                  <kbd>{k}</kbd>
                </React.Fragment>
              ))}
            </dt>
            <dd>{s.label}</dd>
          </div>
        ))}
      </dl>
    </Modal>
  );
}
