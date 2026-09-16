"""Point d'entrée : collecte hybride (scrapers) -> ingestion SQLite -> scoring -> reranking.

Enchaînement :
  1. ``ScraperManager`` exécute la **stratégie hybride** — passe « Fraîcheur »
     (tri par date, filtre temporel serveur, arrêt anticipé dès que le flux
     rejoint le scrape précédent) puis passe « Rattrapage » (classement par
     pertinence, sans arrêt anticipé) — et consigne, pour chaque (source × requête
     × mode), la **raison exacte d'arrêt** (quota, early stopping, fin de flux,
     rate limit…) ;
  2. ``ingest_raw_jobs`` persiste les offres valides dans la table ``jobs`` de
     façon idempotente (déduplication par identifiant et par URL canonique) ;
  3. la **mémoire de collecte** (``seen_jobs``) est mise à jour avec TOUTES les
     cartes croisées (y compris les rejets) : c'est elle qui rend l'arrêt anticipé
     efficace au run suivant, et qui porte la déduplication transverse ;
  4. la **télémétrie** de chaque passe est écrite (``scrape_runs`` /
     ``scrape_query_stats``) puis résumée dans le journal, avec alerte explicite
     lorsqu'une passe a été interrompue (donc que du flux a pu être perdu) ;
  5. un résumé est logué (collectées / BI rejetées / nouvelles / doublons) ;
  6. avec ``--trigger-scoring``, l'étape 1 du ranking (Bi-Encoder
     ``all-MiniLM-L6-v2``) est calculée sur les **seules nouvelles offres** ;
     avec ``--rescore-all``, elle est recalculée sur **toutes** les offres en base ;
  7. avec ``--trigger-rerank``, l'étape 2 (juge LLM DeepSeek) évalue le Top-N des
     offres non encore analysées (``ranking.top_n_rerank``, surchargeable par
     ``--top-rerank``). Sans clé ``DEEPSEEK_API_KEY``, l'étape est ignorée proprement.

Options de pilotage de la collecte : ``--passes freshness,relevance``,
``--only-source linkedin`` et ``--top-telemetry N`` (dernières raisons d'arrêt).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scrapers.base import describe_rejection  # noqa: E402
from scrapers.known import NullKnownIndex  # noqa: E402
from scrapers.manager import ScraperManager  # noqa: E402
from scrapers.models import (  # noqa: E402
    PASS_MODES,
    PassReport,
    RawJob,
    ScrapeResult,
    ScraperConfig,
)
from src.config import load_config  # noqa: E402
from src.constants import (  # noqa: E402
    RUN_OK,
    RUN_PARTIAL,
    STATUS_REJECTED,
    is_incomplete_stop,
    pass_label,
    stop_reason_label,
)
from src.ingestion.bridge import find_new_raw_jobs, ingest_raw_jobs, raw_job_to_dict  # noqa: E402
from src.ingestion.known_index import DatabaseKnownIndex, job_ids_for_jobs  # noqa: E402
from src.storage.cleanup import choose_keeper, find_duplicate_groups  # noqa: E402
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
        "--dedupe",
        action="store_true",
        help="Fusionne les offres en doublon (URL canonique ou entreprise + titre similaire).",
    )
    parser.add_argument(
        "--revalidate",
        action="store_true",
        help="Ré-applique le filtre métier sur les FICHES COMPLÈTES (statut REJETÉ).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simule les étapes d'hygiène (--dedupe/--revalidate) sans rien écrire en base.",
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
    parser.add_argument(
        "--passes",
        default=None,
        metavar="MODES",
        help=(
            "Passes à exécuter, séparées par des virgules (freshness,relevance). "
            "Défaut : toutes les passes activées dans config.yaml, section scrapers.passes."
        ),
    )
    parser.add_argument(
        "--only-source",
        default=None,
        metavar="SOURCE",
        help="Restreint la collecte à une source (linkedin, jobteaser, wttj) — diagnostic.",
    )
    parser.add_argument(
        "--top-telemetry",
        type=int,
        default=0,
        metavar="N",
        help="Affiche les N dernières passes tracées en base (raisons d'arrêt).",
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


def _dedupe_jobs(db: Database, *, dry_run: bool) -> dict[str, int]:
    """Fusionne les doublons (URL canonique ou entreprise + titre très proche).

    La fiche conservée est la plus complète (description la plus longue, puis
    verdict LLM, puis score) ; si elle n'a pas de description et qu'un doublon en
    a une, le texte est transféré avant suppression.
    """
    groups = find_duplicate_groups(db.get_jobs())
    removed = 0
    transferred = 0
    for group in groups:
        keeper, duplicates = choose_keeper(group)
        if not (keeper.get("description") or "").strip():
            donor = next(
                (job for job in duplicates if (job.get("description") or "").strip()), None
            )
            if donor is not None:
                transferred += 1
                if not dry_run:
                    db.update_description(keeper["id"], donor["description"])
        if dry_run:
            removed += len(duplicates)
        else:
            removed += db.delete_jobs([job["id"] for job in duplicates])
    logger.info(
        " Dédoublonnage                : %d groupe(s), %d doublon(s) %s, %d description(s) transférée(s)",
        len(groups),
        removed,
        "à supprimer (simulation)" if dry_run else "supprimé(s)",
        transferred,
    )
    return {"groups": len(groups), "removed": removed, "transferred": transferred}


def _revalidate_jobs(db: Database, config: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    """Ré-applique le filtre métier sur le texte COMPLET des offres documentées.

    Seules les offres ayant une description sont jugées : sans fiche, la
    re-validation n'apporterait aucune information nouvelle (elle ne verrait que
    le titre) et risquerait d'écarter des offres sur un simple effet de style.
    """
    scraper_config = ScraperConfig.from_config(config)
    reasons: dict[str, int] = {}
    rejected = 0
    skipped = 0
    for job in db.get_jobs():
        if job.get("status") == STATUS_REJECTED:
            continue
        if not (job.get("description") or "").strip():
            skipped += 1
            continue
        reason = describe_rejection(
            job.get("title", ""), job.get("description", ""), scraper_config
        )
        if not reason:
            continue
        rejected += 1
        reasons[reason] = reasons.get(reason, 0) + 1
        if not dry_run:
            db.reject_job(job["id"], reason)
    logger.info(
        " Re-validation métier         : %d offre(s) écartée(s)%s | %d sans description (ignorées)",
        rejected,
        " (simulation)" if dry_run else "",
        skipped,
    )
    for reason, count in sorted(reasons.items(), key=lambda item: -item[1])[:8]:
        logger.info("    %3d x %s", count, reason)
    return {"rejected": rejected, "reasons": reasons, "skipped": skipped}


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
            sub_scores=result["sub_scores"],
            hard_cap_triggered=result["hard_cap_triggered"],
            reasoning=result.get("reasoning", ""),
        )
        cap_note = (
            f" [verrou: {result['hard_cap_triggered']}]"
            if result.get("hard_cap_triggered")
            else ""
        )
        logger.info(
            "  %d/%d [%3d] %-10s %s%s",
            index,
            len(candidates),
            result["rerank_score"],
            result["verdict"],
            (job.get("title") or "")[:50],
            cap_note,
        )
    logger.info("Reranking LLM : %d offre(s) analysée(s).", len(candidates))
    return len(candidates)


def _selected_sources(config: ScraperConfig, only_source: str | None) -> None:
    """Restreint la collecte à une source (diagnostic ciblé)."""
    if not only_source:
        return
    if only_source not in config.enabled_sources:
        logger.warning(
            "Source %r absente de scrapers.enabled_sources : aucune collecte ne sera lancée.",
            only_source,
        )
    config.enabled_sources = [only_source]


def _parse_passes(value: str | None) -> list[str] | None:
    """Découpe ``--passes freshness,relevance`` en liste de modes valides."""
    if not value:
        return None
    modes = [item.strip() for item in value.split(",") if item.strip()]
    unknown = [mode for mode in modes if mode not in PASS_MODES]
    if unknown:
        raise SystemExit(
            f"Mode de passe inconnu : {', '.join(unknown)} (attendu : {', '.join(PASS_MODES)})"
        )
    return modes


def _log_pass_summary(reports: Sequence[PassReport]) -> list[PassReport]:
    """Résumé lisible d'une ligne par passe + alerte sur les pertes de flux.

    C'est le cœur de l'observabilité : chaque ligne indique **pourquoi** la passe
    s'est arrêtée — donc si le vivier était épuisé (rien perdu) ou si un quota, un
    plafond de pages ou un rate limit a tronqué le flux (donnée potentiellement
    manquée).
    """
    if not reports:
        logger.info(" Aucune passe tracée (collecte ignorée ou télémétrie désactivée).")
        return []
    logger.info("")
    logger.info(" %-9s | %-28s | %-9s | %5s | %6s | %6s | %6s | %7s | %s",
                "SOURCE", "REQUÊTE", "PASSE", "PAGES", "VUES", "GARDÉES", "CONNUES", "ARRÊT", "DÉTAIL")
    logger.info(" " + "-" * 124)
    for report in reports:
        logger.info(
            " %-9s | %-28s | %-9s | %5d | %6d | %6d | %6d | %7s | %s",
            report.source,
            report.query[:28],
            report.mode,
            report.pages_fetched,
            report.cards_seen,
            report.jobs_kept,
            report.jobs_known,
            report.stop_reason,
            (report.stop_detail or "—")[:60],
        )
    lost = [report for report in reports if is_incomplete_stop(report.stop_reason)]
    if lost:
        logger.warning(
            " %d passe(s) interrompue(s) : du flux a potentiellement été perdu — %s",
            len(lost),
            " ; ".join(
                f"{report.source}/{report.query}/{report.mode} ({report.stop_reason})"
                for report in lost[:6]
            ),
        )
    exhausted = [report for report in reports if report.stop_reason in ("stream_end", "window_end")]
    if exhausted:
        logger.info(
            " %d passe(s) close(s) sur vivier épuisé (aucune perte) : %s",
            len(exhausted),
            " ; ".join(f"{report.source}/{report.mode}" for report in exhausted[:6]),
        )
    return lost


def _log_recent_telemetry(db: Database, limit: int) -> None:
    """Affiche les dernières passes tracées (raison d'arrêt en clair)."""
    rows = db.get_recent_query_stats(limit=limit)
    if not rows:
        logger.info("Télémétrie : aucune passe enregistrée.")
        return
    logger.info("")
    logger.info(" DERNIÈRES PASSES TRACÉES EN BASE (%d)", len(rows))
    logger.info(" %-19s | %-9s | %-24s | %-9s | %6s | %7s | %s",
                "DATE", "SOURCE", "REQUÊTE", "PASSE", "GARDÉES", "ARRÊT", "DÉTAIL")
    logger.info(" " + "-" * 118)
    for row in rows:
        stamp = row.get("finished_at") or row.get("started_at")
        logger.info(
            " %-19s | %-9s | %-24s | %-9s | %6d | %7s | %s",
            stamp.strftime("%Y-%m-%d %H:%M:%S") if stamp else "?",
            row.get("source") or "?",
            (row.get("query") or "")[:24],
            pass_label(row.get("mode")),
            int(row.get("jobs_kept") or 0),
            row.get("stop_reason") or "?",
            stop_reason_label(row.get("stop_reason"))[:40],
        )


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

    # 0. Hygiène de la base (optionnelle) : doublons, puis re-validation métier sur
    #    les fiches complètes (le filtre de collecte ne voyait que les titres).
    if args.dedupe:
        _dedupe_jobs(db, dry_run=args.dry_run)
    if args.revalidate:
        _revalidate_jobs(db, config, dry_run=args.dry_run)

    # 1. Collecte hybride (fraîcheur puis rattrapage) via le manager unifié.
    #    Paramètres lus dans config.yaml → 'scrapers' (passes, quotas, fenêtres).
    scraper_config = ScraperConfig.from_config(config)
    _selected_sources(scraper_config, args.only_source)
    modes = _parse_passes(args.passes) or scraper_config.enabled_modes()
    telemetry = scraper_config.telemetry
    run_id: str | None = None
    known_index: Any = NullKnownIndex()

    if args.no_collect:
        logger.info(" Collecte ignorée (--no-collect) : travail sur la base existante.")
        result = ScrapeResult(jobs=[], found=0, rejected_bi=0)
    elif not modes:
        logger.warning(
            " Aucune passe active (scrapers.passes) : collecte ignorée. "
            "Déclarez au moins 'freshness' ou 'relevance' dans config.yaml."
        )
        result = ScrapeResult(jobs=[], found=0, rejected_bi=0)
    else:
        logger.info(
            " Paramètres scrapers          : %s | %d requête(s) | passes %s | plafond %d",
            ", ".join(scraper_config.enabled_sources),
            len(scraper_config.target_queries),
            "+".join(modes),
            scraper_config.max_offers_per_source,
        )
        for mode in modes:
            pass_config = scraper_config.pass_config(mode)
            logger.info(
                "   - passe %-9s : tri=%-9s | quota %d/requête | fenêtre %s | arrêt anticipé %s",
                mode,
                pass_config.sort,
                pass_config.clamped_quota(),
                f"{pass_config.window_days:g} j" if pass_config.window_days else "aucune",
                pass_config.early_stop_after_known or "désactivé",
            )
        if telemetry.enabled:
            # Mémoire de collecte préchargée : elle porte l'arrêt anticipé et la
            # déduplication transverse entre passes et entre sources.
            known_index = DatabaseKnownIndex(db, scraper_config.enabled_sources)
            run_id = db.start_run(scraper_config.enabled_sources)
        manager = ScraperManager(scraper_config, known_index=known_index)
        result = manager.run(modes=modes)

    # 2. Identifier les nouvelles offres AVANT insertion (pour le scoring ciblé).
    new_jobs = find_new_raw_jobs(result.jobs, db) if args.trigger_scoring else []

    # 3. Ingestion idempotente en SQLite (déduplication id + URL).
    stats = ingest_raw_jobs(result.jobs, db)

    # 4. Télémétrie : mémoire de collecte (toutes les cartes vues, y compris les
    #    rejets) puis une ligne par passe avec sa raison exacte d'arrêt.
    lost_passes: list[PassReport] = []
    if telemetry.enabled:
        if isinstance(known_index, DatabaseKnownIndex):
            written = known_index.persist(result.seen, job_ids_for_jobs(result.jobs))
            logger.info(" Mémoire de collecte          : %d entrée(s) écrite(s)", written)
        if run_id and result.query_reports:
            db.record_query_stats(run_id, result.query_reports)
        removed = db.prune_telemetry(telemetry.retention_days)
        if telemetry.prune_seen_jobs:
            forgotten = db.prune_seen_jobs(telemetry.retention_days)
            if forgotten:
                logger.info(" Mémoire de collecte purgée   : %d entrée(s) ancienne(s)", forgotten)
        if removed["runs"] or removed["query_stats"]:
            logger.info(
                " Télémétrie purgée            : %d run(s), %d passe(s) (> %d j)",
                removed["runs"],
                removed["query_stats"],
                telemetry.retention_days,
            )
    lost_passes = _log_pass_summary(result.query_reports)
    # La consultation de la télémétrie est indépendante du run courant : elle doit
    # fonctionner aussi avec ``--no-collect`` (aucun run ouvert).
    if args.top_telemetry:
        _log_recent_telemetry(db, args.top_telemetry)
    if telemetry.enabled and run_id:
        db.finish_run(
            run_id,
            status=RUN_PARTIAL if lost_passes else RUN_OK,
            total_found=result.found,
            total_validated=len(result.jobs),
            total_rejected=result.rejected_bi,
            total_inserted=stats["new_inserted"],
            total_duplicates=stats["duplicates_skipped"],
            notes="; ".join(sorted({report.stop_reason for report in lost_passes})) or None,
        )

    # 5. Résumé.
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

    # 6. Étape 1 du ranking : scoring Bi-Encoder.
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

    # 7. Étape 2 du ranking : reranking LLM du Top-N non encore analysé.
    if args.reset_rerank:
        reset_ids = db.clear_rerank(args.reset_rerank)
        logger.info(
            " Ré-évaluation forcée         : %d analyse(s) LLM remise(s) à zéro",
            len(reset_ids),
        )
    if args.trigger_rerank:
        logger.info(" Reranking LLM                : %d offre(s)", _rerank_top(db, config, args.top_rerank))

    # 8. Top opportunités (offres fraîchement collectées).
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

    # 9. Top opportunités globales (base complète, tri par score effectif).
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
