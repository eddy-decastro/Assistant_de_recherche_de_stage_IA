"""Lancement du pipeline complet : collecte → backfill → scoring LLM.

Enchaînement automatique :
  1. Collecte des offres (hybride) via run_scrapers.py (sans reranking).
  2. Enrichissement des descriptions (backfill) pour les nouvelles offres.
  3. Reranking LLM (fournisseur de config.yaml, llm.provider) sur toutes les offres non notées.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import argparse
from run_scrapers import main as run_scrapers_main  # noqa: E402
from backfill_descriptions import main as run_backfill_main  # noqa: E402
from src.config import load_config  # noqa: E402
from src.constants import STATUS_NEW  # noqa: E402
from src.notifications import (  # noqa: E402
    archive_expired_offers,
    archive_old_offers,
    pregenerate_top_cover_letters,
    send_pipeline_digest,
)
from src.storage.cloud_storage import (  # noqa: E402
    backup_remote_database,
    download_database,
    is_cloud_storage_configured,
    upload_database,
)
from src.storage.database import Database  # noqa: E402
from src.storage.db_merge import USER_COLUMNS  # noqa: E402

logger = logging.getLogger("pipeline")


def main(argv: list[str] | None = None) -> None:
    """Exécute l'intégralité de la chaîne de traitement (avec synchronisation Cloud automatique)."""
    # Force l'encodage UTF-8 pour la console Windows (évite les crashs liés aux emojis)
    if sys.stdout.encoding.lower() != 'utf-8':
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except AttributeError:
            pass

    parser = argparse.ArgumentParser(description="Lancement du pipeline complet de scraping et scoring")
    parser.add_argument(
        "--no-sync",
        action="store_true",
        help="Désactive la synchronisation Cloud (R2) avant et après le scraping",
    )
    parser.add_argument(
        "--regrade-v1",
        action="store_true",
        help="Réévalue les offres v1 avec le nouveau juge LLM v3.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Plafond pour --regrade-v1 (défaut : 20 offres).",
    )
    parser.add_argument(
        "--recompute-scores",
        action="store_true",
        help="Recalcule en code pur les notes finales, planchers et plafonds de toutes les offres v3 sans appel LLM.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )
    
    sync_enabled = not args.no_sync and is_cloud_storage_configured()

    if sync_enabled:
        print("\n" + "=" * 60)
        print(" ☁️  SYNCHRONISATION CLOUD : Récupération des changements (Pull)")
        print("=" * 60)
        try:
            download_database()
            print(" ✅ Base locale synchronisée avec succès depuis le Cloud.")
        except Exception as e:
            logger.warning(f"Impossible de synchroniser depuis le Cloud: {e}")

    if args.recompute_scores:
        print("\n" + "=" * 60)
        print(" 🔄 RECALCUL DES NOTES V3 (Code pur sans appel LLM)")
        print("=" * 60)
        run_scrapers_main(["--recompute-scores"])
        if sync_enabled:
            upload_database(prefer_remote_columns=USER_COLUMNS)
        return

    if args.regrade_v1:
        print("\n" + "=" * 60)
        print(f" 🧠 RÉ-ÉVALUATION DES OFFRES V1 (juge v3, limite={args.limit})")
        print("=" * 60)
        run_scrapers_main(["--regrade-v1", "--limit", str(args.limit)])
        if sync_enabled:
            upload_database(prefer_remote_columns=USER_COLUMNS)
        return

    db = Database()

    print("\n" + "=" * 60)
    print(" 🚀 ÉTAPE 1 : COLLECTE DES OFFRES (LinkedIn & WTTJ)")
    print("=" * 60)
    # Lancement de la collecte (hybride)
    degraded_sources = run_scrapers_main([]) or 0

    print("\n" + "=" * 60)
    print(" 📖 ÉTAPE 2 : ENRICHISSEMENT DES DESCRIPTIONS (Backfill)")
    print("=" * 60)
    # Rattrapage pour alimenter correctement le LLM (uniquement s'il y a des offres manquantes)
    if db.get_jobs_missing_description(limit=1):
        run_backfill_main([])
    else:
        logger.info("Aucune offre sans description : étape 2 (backfill) ignorée.")

    print("\n" + "=" * 60)
    print(" 🧠 ÉTAPE 3 : RERANKING LLM")
    print("=" * 60)
    # Évaluation des nouvelles offres (uniquement si des offres sont non notées)
    if db.get_unranked_jobs(limit=1):
        run_scrapers_main(["--no-collect", "--trigger-rerank", "--top-rerank", "1000"])
    else:
        logger.info("Toutes les offres éligibles sont déjà notées par le juge LLM : étape 3 ignorée.")

    # Étape 4 : n'exécute le recalcul que si des offres v1 subsistent (le juge v3 calcule déjà le score exact)
    if db.get_v1_jobs(limit=1):
        print("\n" + "=" * 60)
        print(" 🔄 ÉTAPE 4 : RECALCUL DÉTERMINISTE DES SCORES V3 (Pénalités & Planchers)")
        print("=" * 60)
        run_scrapers_main(["--recompute-scores"])
    else:
        logger.info("Toutes les offres sont déjà au format v3 : étape 4 (recompute-scores) ignorée.")

    print("\n" + "=" * 60)
    print(" 🤖 ÉTAPE AUTOMATISATIONS : Nettoyage, Pré-génération & Notifications")
    print("=" * 60)
    cfg = load_config()
    auto_cfg = cfg.get("automations") or {}

    if auto_cfg.get("auto_archive_enabled", False):
        old_archived = archive_old_offers(db, cfg)
        if old_archived:
            logger.info("Offres anciennes archivées : %d", old_archived)

    if auto_cfg.get("check_expired_offers_enabled", False):
        archived = archive_expired_offers(db, cfg)
        if archived:
            logger.info("Offres expirées archivées : %d", archived)

    if auto_cfg.get("pregenerate_letters_enabled", False):
        generated = pregenerate_top_cover_letters(db, cfg)
        if generated:
            logger.info("Lettres de motivation pré-générées : %d", generated)

    if auto_cfg.get("cloud_backup_enabled", True) and is_cloud_storage_configured():
        retention = int(auto_cfg.get("cloud_backup_retention_days", 7))
        try:
            backup_remote_database(max_backups=retention)
            logger.info("Sauvegarde distante Cloudflare R2 créée (rétention: %d jours).", retention)
        except Exception as exc:
            logger.warning("Échec de la sauvegarde distante Cloudflare R2 : %s", exc)

    if auto_cfg.get("notifications_enabled", False):
        try:
            recent_new = db.get_jobs(statuses=STATUS_NEW, limit=15)
        except TypeError:
            recent_new = db.get_jobs(status=STATUS_NEW, limit=15)
        send_pipeline_digest(recent_new, cfg, degraded_sources=degraded_sources)

    if sync_enabled:
        print("\n" + "=" * 60)
        print(" ☁️  SYNCHRONISATION CLOUD : Publication des nouvelles offres (Push)")
        print("=" * 60)
        try:
            upload_database(prefer_remote_columns=USER_COLUMNS)
            print(" ✅ Nouvelles offres envoyées sur le Cloud avec succès !")
        except Exception as e:
            logger.warning(f"Impossible de téléverser vers le Cloud: {e}")

    print("\n" + "=" * 60)
    print(" ✅ PIPELINE TERMINÉ ! Le dashboard est prêt et à jour partout.")
    print("=" * 60)

    # Données publiées d'abord ; le job est ensuite marqué en échec pour qu'une
    # source silencieusement cassée ne passe pas inaperçue.
    if degraded_sources:
        logger.error("%d source(s) de scraping dégradée(s) : voir les alertes ci-dessus.", degraded_sources)
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1:])



