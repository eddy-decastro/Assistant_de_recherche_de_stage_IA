"""Validation bout-en-bout de la collecte hybride, en 2 runs réels sur LinkedIn.

Démontre, sur la plateforme réelle et sur une base TEMPORAIRE (aucun impact sur
``data/stage_copilot.db``) :

1. **Run 1** — passe « Fraîcheur » : mémoire vide, pagination jusqu'au quota ;
2. **Run 2** — même requête : les offres du run 1 sont connues ⇒ **arrêt anticipé**
   (moins de pages, jonction avec le scrape précédent) ;
3. la déduplication transverse (aucune offre retraitée/dupliquée) ;
4. l'écriture de la télémétrie (``scrape_runs`` / ``scrape_query_stats``) et de la
   mémoire de collecte (``seen_jobs``).

Usage : ``python tools/validate_hybrid_run.py`` (réseau requis, ~10 requêtes).
"""

from __future__ import annotations

import logging
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scrapers.manager import ScraperManager
from scrapers.models import PassConfig, ScraperConfig
from src.constants import is_incomplete_stop, stop_reason_label
from src.ingestion.bridge import ingest_raw_jobs
from src.ingestion.known_index import DatabaseKnownIndex, job_ids_for_jobs
from src.storage.database import Database

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s — %(message)s")
for noisy in ("httpx", "httpcore"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

QUERY = "Stage Machine Learning"


def _config() -> ScraperConfig:
    """Passe Fraîcheur resserrée : 1 requête, quota 5, 3 pages maximum, fenêtre 7 j."""
    return ScraperConfig(
        enabled_sources=["linkedin"],
        target_queries=[QUERY],
        max_offers_per_source=20,
        passes=PassConfig.only(
            "freshness",
            max_offers_per_query=5,
            window_days=7,
            max_pages_per_query=3,
            early_stop_after_known=3,
            # L'arrêt anticipé est neutralisé par défaut sur LinkedIn (ordre mesuré
            # non chronologique) : on l'active ici EXPLICITEMENT pour démontrer le
            # mécanisme (réglage expert, non recommandé en production).
            trust_source_order=True,
        ),
    )


def _run(db: Database, label: str) -> None:
    config = _config()
    index = DatabaseKnownIndex(db, config.enabled_sources)
    run_id = db.start_run(config.enabled_sources)
    result = ScraperManager(config, known_index=index).run(modes=["freshness"])
    stats = ingest_raw_jobs(result.jobs, db)
    index.persist(result.seen, job_ids_for_jobs(result.jobs))
    db.record_query_stats(run_id, result.query_reports)
    db.finish_run(
        run_id,
        status="OK",
        total_validated=len(result.jobs),
        total_inserted=stats["new_inserted"],
        total_duplicates=stats["duplicates_skipped"],
    )

    print(f"\n================ {label} ================")
    print(f"offres retenues={len(result.jobs)} | nouvelles={stats['new_inserted']} | "
          f"doublons={stats['duplicates_skipped']} | rejets={result.rejected_bi}")
    for report in result.query_reports:
        print(
            f"  {report.mode} | pages={report.pages_fetched} | HTTP={report.http_requests} | "
            f"vues={report.cards_seen} | retenues={report.jobs_kept} | connues={report.jobs_known} | "
            f"arrêt={report.stop_reason} ({stop_reason_label(report.stop_reason)})"
        )
        print(f"    détail : {report.stop_detail}")
        if is_incomplete_stop(report.stop_reason):
            print("    ⚠ flux potentiellement perdu")
    for job in result.jobs[:5]:
        stamp = job.published_at.date().isoformat() if job.published_at else "sans date"
        print(f"    + {stamp} | {job.title[:58]} | {job.company[:22]}")


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "validation.db")
        try:
            _run(db, "RUN 1 — mémoire vide (pagination jusqu'au quota)")
            _run(db, "RUN 2 — jonction attendue (arrêt anticipé)")
            print("\n=== TÉLÉMÉTRIE EN BASE ===")
            print(f"runs={db.count_runs()} | mémoire de collecte={db.count_seen_jobs()} | "
                  f"offres={db.count_jobs()}")
            for row in db.get_recent_query_stats(limit=10)[::-1]:
                print(f"  {row['mode']} | vues={row['cards_seen']} | retenues={row['jobs_kept']} | "
                      f"connues={row['jobs_known']} | arrêt={row['stop_reason']} | {row['stop_detail']}")
        finally:
            db.engine.dispose()
    print("\nValidation terminée (aucune écriture dans data/stage_copilot.db).")


if __name__ == "__main__":
    main()
