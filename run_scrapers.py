"""Point d'entrée : collecte (scrapers) -> ingestion SQLite -> scoring -> reranking.

Enchaînement :
  1. ``ScraperManager`` collecte et filtre (anti-BI) les offres des sources ;
  2. ``ingest_raw_jobs`` persiste les offres valides dans la table SQLite ``jobs``
     de façon idempotente (déduplication par identifiant et par URL) ;
  3. un résumé est logué (collectées / BI rejetées / nouvelles / doublons) ;
  4. avec ``--trigger-scoring``, l'étape 1 du ranking (Bi-Encoder
     ``all-MiniLM-L6-v2``) est calculée sur les **seules nouvelles offres** ;
     avec ``--rescore-all``, elle est recalculée sur **toutes** les offres en base
     (utile après un changement de modèle/CV ou pour rattraper un historique non noté) ;
  5. avec ``--trigger-rerank``, l'étape 2 (juge LLM DeepSeek) évalue le Top-N des
     offres non encore analysées (``ranking.top_n_rerank``, surchargeable par
     ``--top-rerank``). Sans clé ``DEEPSEEK_API_KEY``, l'étape est ignorée proprement.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scrapers.manager import ScraperManager  # noqa: E402
from scrapers.models import RawJob, ScrapeResult, ScraperConfig  # noqa: E402
from src.config import load_config  # noqa: E402
from src.ingestion.bridge import find_new_raw_jobs, ingest_raw_jobs, raw_job_to_dict  # noqa: E402
from src.storage.database import Database  # noqa: E402

logger = logging.getLogger("run_scrapers")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Analyse les arguments de la ligne de commande."""
    parser = argparse.ArgumentParser(
        description="Collecte les offres de stage (scrapers) et les persiste en SQLite."
    )
    parser.add_argument(
        "--trigger-scoring",
        action="store_true",
        help="Calcule le score Bi-Encoder (all-MiniLM-L6-v2) des seules nouvelles offres.",
    )
    parser.add_argument(
        "--rescore-all",
        action="store_true",
        help="Recalcule le score Bi-Encoder de TOUTES les offres en base.",
    )
    parser.add_argument(
        "--no-collect",
        action="store_true",
        help="Saute la collecte et travaille sur la base existante (scoring/rerank seuls).",
    )
    parser.add_argument(
        "--trigger-rerank",
        action="store_true",
        help="Étape 2 : juge LLM (DeepSeek) sur le Top-N des offres non encore analysées.",
    )
    parser.add_argument(
        "--reset-rerank",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Ré-évaluation forcée : remet à zéro l'analyse LLM des N meilleures offres "
            "avant le rerank (utile après un enrichissement des descriptions)."
        ),
    )
    parser.add_argument(
        "--top-rerank",
        type=int,
        default=None,
        help="Nombre d'offres envoyées au juge LLM (défaut : ranking.top_n_rerank).",
    )
    return parser.parse_args(argv)


def _sort_key(job: RawJob) -> float:
    """Clé de tri par récence (les offres sans date sont reléguées en fin)."""
    return job.published_at.timestamp() if job.published_at else 0.0


def _load_scorer(config: dict[str, Any]) -> Any | None:
    """Charge le Scorer Bi-Encoder (``None`` si torch/sentence-transformers absents)."""
    try:
        from src.matching.scorer import Scorer
    except ImportError as exc:
        logger.warning("Scoring indisponible (sentence-transformers/torch absents) : %s", exc)
        return None
    return Scorer(config)


def _score_jobs(db: Database, jobs: list[dict[str, Any]], scorer: Any) -> int:
    """Score une liste d'offres (dicts compatibles table ``jobs``) et persiste."""
    for job in jobs:
        db.upsert_job(scorer.score(job))
    return len(jobs)


def _rerank_top(db: Database, config: dict[str, Any], top_n: int | None = None) -> int:
    """Étape 2 : évalue le Top-N des offres non analysées avec le juge LLM.

    Retourne le nombre d'offres analysées (0 si la clé API est absente : l'étape
    reste optionnelle et ne bloque jamais le pipeline).
    """
    try:
        from src.matching.llm_judge import LLMJudge
    except ImportError as exc:
        logger.warning("Reranking indisponible : %s", exc)
        return 0

    judge = LLMJudge(config)
    if not judge.available:
        logger.warning(
            "Reranking ignoré : DEEPSEEK_API_KEY absente (.env). "
            "Copiez .env.example en .env puis renseignez votre clé DeepSeek."
        )
        return 0

    limit = top_n or int(config.get("ranking", {}).get("top_n_rerank", 20))
    candidates = db.get_unranked_jobs(limit=limit)
    if not candidates:
        logger.info("Reranking : aucune offre non analysée (Top déjà évalué).")
        return 0

    cv_path = Path(config.get("scoring", {}).get("cv_path", "data/cv_eddy.txt"))
    cv_text = cv_path.read_text(encoding="utf-8") if cv_path.exists() else ""

    for index, job in enumerate(candidates, start=1):
        result = judge.judge(job, cv_text=cv_text)
        db.update_rerank(
            job["id"],
            result["rerank_score"],
            result["verdict"],
            result["match_reasons"],
            result["red_flags"],
            result["tech_stack"],
        )
        logger.info(
            "  %d/%d [%3d] %-10s %s",
            index,
            len(candidates),
            result["rerank_score"],
            result["verdict"],
            (job.get("title") or "")[:50],
        )
    logger.info("Reranking LLM : %d offre(s) analysée(s).", len(candidates))
    return len(candidates)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )
    # Sortie lisible : les bibliothèques tierces sont très bavardes en INFO
    # (httpx loggue chaque requête, HF/transformers affichent des barres de progression).
    for noisy in ("httpx", "httpcore", "huggingface_hub", "urllib3", "filelock"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

    config = load_config()
    db = Database(config["database"]["path"])

    # 1. Collecte via le manager unifié (paramètres lus dans config.yaml → 'scrapers').
    if args.no_collect:
        logger.info(" Collecte ignorée (--no-collect) : travail sur la base existante.")
        result = ScrapeResult(jobs=[], found=0, rejected_bi=0)
    else:
        scraper_config = ScraperConfig.from_config(config)
        logger.info(
            " Paramètres scrapers          : %s | %d requête(s) | %d offre(s)/requête | plafond %d",
            ", ".join(scraper_config.enabled_sources),
            len(scraper_config.target_queries),
            scraper_config.per_query_quota,
            scraper_config.max_offers_per_source,
        )
        manager = ScraperManager(scraper_config)
        result = manager.run()

    # 2. Identifier les nouvelles offres AVANT insertion (pour le scoring ciblé).
    new_jobs = find_new_raw_jobs(result.jobs, db) if args.trigger_scoring else []

    # 3. Ingestion idempotente en SQLite (déduplication id + URL).
    stats = ingest_raw_jobs(result.jobs, db)

    # 4. Résumé.
    logger.info("=" * 60)
    logger.info(" RÉSUMÉ")
    logger.info("=" * 60)
    logger.info(" Offres collectées (validées) : %d", len(result.jobs))
    logger.info(" Offres BI/analyst rejetées   : %d", result.rejected_bi)
    logger.info(" Nouvelles offres persistées  : %d", stats["new_inserted"])
    logger.info(" Doublons ignorés             : %d", stats["duplicates_skipped"])
    logger.info(" Total en base SQLite         : %d", db.count_jobs())
    for source, count in db.get_source_counts():
        logger.info("   - %-14s : %d", source or "(sans source)", count)

    # 5. Étape 1 du ranking : scoring Bi-Encoder.
    if args.trigger_scoring or args.rescore_all:
        scorer = _load_scorer(config)
        if scorer is not None:
            if args.rescore_all:
                targets = db.get_jobs()  # toutes les offres (y compris l'historique)
                logger.info(" Scoring Bi-Encoder (global) sur %d offre(s)…", len(targets))
            else:
                targets = [raw_job_to_dict(job) for job in new_jobs]
                logger.info(" Scoring Bi-Encoder sur %d nouvelle(s) offre(s)…", len(targets))
            if targets:
                logger.info(" Offres scorées               : %d", _score_jobs(db, targets, scorer))
            else:
                logger.info(" Scoring : aucune offre à évaluer.")

    # 6. Étape 2 du ranking : reranking LLM du Top-N non encore analysé.
    if args.reset_rerank:
        reset_ids = db.clear_rerank(args.reset_rerank)
        logger.info(
            " Ré-évaluation forcée         : %d analyse(s) LLM remise(s) à zéro",
            len(reset_ids),
        )
    if args.trigger_rerank:
        logger.info(" Reranking LLM                : %d offre(s)", _rerank_top(db, config, args.top_rerank))

    # 7. Top opportunités (offres fraîchement collectées).
    if result.jobs:
        top = sorted(result.jobs, key=_sort_key, reverse=True)[:5]
        logger.info("")
        logger.info(" Top %d opportunités :", len(top))
        for rank, job in enumerate(top, start=1):
            logger.info(
                "   %d. %s — %s (%s) [%s]",
                rank,
                job.title,
                job.company,
                job.location or "N/A",
                job.source,
            )
            logger.info("      %s", job.url)
    else:
        if not args.no_collect:
            logger.warning("Aucune offre valide collectée.")

    # 8. Top opportunités globales (base complète, tri par score effectif).
    ranked = db.get_jobs(limit=5)
    if ranked:
        logger.info("")
        logger.info(" Top 5 en base (score effectif) :")
        for rank, job in enumerate(ranked, start=1):
            score = job["rerank_score"] if job["rerank_score"] is not None else job["final_score"]
            logger.info(
                "   %d. [%5.1f] %-45s %s",
                rank,
                score,
                (job["title"] or "")[:45],
                job.get("source") or "",
            )

    db.engine.dispose()


if __name__ == "__main__":
    main()
