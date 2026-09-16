"""Affichage du verdict LLM de la meilleure offre + du nouveau Top 5.

Outil de restitution (lecture seule) : imprime le JSON complet persisté pour la
meilleure offre — raisonnement, sous-scores 1-5, verrou éventuel, raisons, alertes
et pile technique — puis le classement courant par score effectif.

Usage : ``python tools/show_llm_verdict.py``
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_config
from src.constants import SUB_SCORE_LABELS, STATUS_REJECTED
from src.storage.database import Database

# Champs du juge LLM restitués tels quels (JSON persisté en base).
LLM_FIELDS = (
    "rerank_score",
    "verdict",
    "hard_cap_triggered",
    "sub_scores",
    "reasoning",
    "match_reasons",
    "red_flags",
    "tech_stack",
)


def _stream() -> None:
    """Force l'UTF-8 (console Windows en cp1252 par défaut)."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    _stream()
    db = Database(load_config()["database"]["path"])
    jobs = [job for job in db.get_jobs() if job.get("status") != STATUS_REJECTED]
    ranked = [job for job in jobs if job.get("rerank_score") is not None]
    if not ranked:
        print("Aucune offre évaluée par le juge LLM (lancez --trigger-rerank).")
        db.engine.dispose()
        return

    best = max(ranked, key=lambda job: float(job["rerank_score"]))
    print("=" * 78)
    print(" MEILLEURE OFFRE — VERDICT DU JUGE LLM")
    print("=" * 78)
    print(f"Titre      : {best.get('title')}")
    print(f"Entreprise : {best.get('company')} — {best.get('location') or 'N/A'}")
    print(f"Source     : {best.get('source')} | publication : {best.get('published_at')}")
    print(f"URL        : {best.get('url')}")
    print("-" * 78)
    payload = {field: best.get(field) for field in LLM_FIELDS}
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))

    print("-" * 78)
    subs = best.get("sub_scores") or {}
    print("Grille d'évaluation :")
    for key, value in subs.items():
        print(f"  - {SUB_SCORE_LABELS.get(key, key):<15} : {value}/5")
    cap = (best.get("hard_cap_triggered") or "").strip()
    print(f"Verrou bloquant     : {cap or 'aucun'}")

    print("")
    print("=" * 78)
    print(f" NOUVEAU TOP 5 (sur {len(ranked)} offre(s) évaluée(s) par le juge)")
    print("=" * 78)
    for rank, job in enumerate(ranked[:5], start=1):
        subs = job.get("sub_scores") or {}
        grid = " | ".join(f"{SUB_SCORE_LABELS.get(k, k)[:6]} {subs.get(k, '-')}/5" for k in subs)
        cap = (job.get("hard_cap_triggered") or "").strip()
        print(f"{rank}. [{float(job['rerank_score']):5.0f}] {str(job.get('verdict')):<10} "
              f"{(job.get('title') or '')[:52]}")
        print(f"     {job.get('company')} — {job.get('source')} | {grid}")
        if cap:
            print(f"     ⚠ verrou : {cap}")
    db.engine.dispose()


if __name__ == "__main__":
    main()
