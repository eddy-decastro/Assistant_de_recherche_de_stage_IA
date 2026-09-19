"""Rétroaction : débloque les faux négatifs de la table seen_jobs.

Ce script examine toutes les offres précédemment rejetées dans la mémoire de collecte
(``seen_jobs``) avec le motif « aucun signal Data Science / ML dans l'annonce ».
Il leur réapplique le filtre métier mis à jour (acronymes IA/AI/GenAI/RL, normalisation
d'espaces, mots-clés enrichis).

Les offres devenues valides sont purgées de ``seen_jobs`` (ou mises à jour)
afin d'être ré-ingérées lors des prochains runs des scrapers.
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scrapers.base import screen_rejection
from scrapers.models import ScraperConfig
from src.config import load_config

logger = logging.getLogger("recover_false_negatives")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Débloque les faux négatifs de la table seen_jobs."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simule la ré-évaluation sans modifier la base SQLite.",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Chemin vers le fichier SQLite (défaut : data/stage_copilot.db).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    config_dict = load_config()
    scraper_config = ScraperConfig.from_config(config_dict)

    db_path = Path(
        args.db_path
        or config_dict.get("database", {}).get("path")
        or (PROJECT_ROOT / "data" / "stage_copilot.db")
    )
    if not db_path.exists():
        logger.error("Fichier de base de données introuvable : %s", db_path)
        sys.exit(1)

    logger.info("Connexion à la base SQLite : %s", db_path)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT source, external_key, canonical_url, title, rejection_reason
        FROM seen_jobs
        WHERE decision = 'REJECTED_BI' AND rejection_reason LIKE '%aucun signal%'
    """)
    rows = cursor.fetchall()
    total_candidates = len(rows)
    logger.info(
        "Offres candidates analysées ('aucun signal Data Science / ML') : %d", total_candidates
    )

    recovered: list[tuple[str, str, str]] = []
    still_rejected: list[tuple[str, str, str]] = []

    for source, ext_key, canon_url, title, reason in rows:
        rejection = screen_rejection(title or "", "", scraper_config)
        if not rejection:
            recovered.append((source, ext_key, title or ""))
        else:
            still_rejected.append((source, ext_key, title or ""))

    logger.info("")
    logger.info("=" * 60)
    logger.info(" BILAN DE RÉ-ÉVALUATION")
    logger.info("=" * 60)
    logger.info(" Total examiné       : %d", total_candidates)
    logger.info(" Faux négatifs sauvés: %d (éligibles DS/ML/IA)", len(recovered))
    logger.info(" Rejets confirmés    : %d (hors sujet)", len(still_rejected))
    logger.info("")

    # Détail par source
    by_source: dict[str, int] = {}
    for src, _, _ in recovered:
        by_source[src] = by_source.get(src, 0) + 1
    for src, count in sorted(by_source.items()):
        logger.info("  -> Source %-12s : %d offre(s) récupérée(s)", src, count)

    if recovered:
        logger.info("\nExemples d'offres récupérées :")
        for src, _, title in recovered[:10]:
            logger.info("  [+] (%s) %s", src, title)

    if not args.dry_run and recovered:
        keys_to_delete = [(src, k) for src, k, _ in recovered]
        cursor.executemany("DELETE FROM seen_jobs WHERE source = ? AND external_key = ?", keys_to_delete)
        conn.commit()
        logger.info(
            "\n%d entrée(s) purgée(s) de seen_jobs avec succès. Elles seront collectées au prochain run !",
            len(recovered),
        )
    elif args.dry_run:
        logger.info("\nMode --dry-run activé : aucune modification n'a été enregistrée en base.")

    conn.close()


if __name__ == "__main__":
    main()
