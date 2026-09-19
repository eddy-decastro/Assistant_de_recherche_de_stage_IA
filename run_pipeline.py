"""Lancement du pipeline complet : collecte → backfill → scoring LLM (Gemini).

Enchaînement automatique :
  1. Collecte des offres (hybride) via run_scrapers.py (sans reranking).
  2. Enrichissement des descriptions (backfill) pour les nouvelles offres.
  3. Reranking LLM (Gemini) sur toutes les offres non notées.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from run_scrapers import main as run_scrapers_main  # noqa: E402
from backfill_descriptions import main as run_backfill_main  # noqa: E402

logger = logging.getLogger("pipeline")


def main() -> None:
    """Exécute l'intégralité de la chaîne de traitement."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )
    
    print("\n" + "=" * 60)
    print(" 🚀 ÉTAPE 1 : COLLECTE DES OFFRES (LinkedIn & JobTeaser)")
    print("=" * 60)
    # Lancement de la collecte (hybride)
    run_scrapers_main([])

    print("\n" + "=" * 60)
    print(" 📖 ÉTAPE 2 : ENRICHISSEMENT DES DESCRIPTIONS (Backfill)")
    print("=" * 60)
    # Rattrapage pour alimenter correctement le LLM
    run_backfill_main([])

    print("\n" + "=" * 60)
    print(" 🧠 ÉTAPE 3 : RERANKING LLM (Gemini 2.0 Flash)")
    print("=" * 60)
    # Évaluation de l'intégralité des nouvelles offres (limite haute à 1000)
    run_scrapers_main(["--no-collect", "--trigger-rerank", "--top-rerank", "1000"])

    print("\n" + "=" * 60)
    print(" ✅ PIPELINE TERMINÉ ! Le dashboard est prêt.")
    print("=" * 60)


if __name__ == "__main__":
    main()



