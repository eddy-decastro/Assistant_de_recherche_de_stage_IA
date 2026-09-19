"""Tests de fumée du pont d'ingestion RawJob -> SQLite (sans réseau ni torch)."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scrapers.models import RawJob
from src.ingestion.bridge import find_new_raw_jobs, ingest_raw_jobs, raw_job_to_dict
from src.storage.database import Database


def _make_job(title: str, url: str, company: str = "Doctolib") -> RawJob:
    return RawJob(
        id_externe=url.rsplit("/", 1)[-1],
        source="wttj",
        title=title,
        company=company,
        location="Paris",
        url=url,
        description="PyTorch, LLM",
    )


def test_bridge_ingestion() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "test.db")
        try:
            jobs = [
                _make_job("Stage Data Scientist", "https://example.com/1"),
                _make_job("Stage Machine Learning", "https://example.com/2"),
            ]

            # 1. Première ingestion : tout est nouveau.
            stats = ingest_raw_jobs(jobs, db)
            assert stats["total_scraped"] == 2, stats
            assert stats["new_inserted"] == 2, stats
            assert stats["duplicates_skipped"] == 0, stats
            assert db.count_jobs() == 2

            # 2. Ré-ingestion identique : tout est doublon.
            stats2 = ingest_raw_jobs(jobs, db)
            assert stats2["new_inserted"] == 0, stats2
            assert stats2["duplicates_skipped"] == 2, stats2
            assert db.count_jobs() == 2

            # 3. Même URL mais titre différent -> doublon (déduplication par URL).
            variant = [_make_job("Stage Data Scientist (H/F)", "https://example.com/1")]
            stats3 = ingest_raw_jobs(variant, db)
            assert stats3["new_inserted"] == 0, stats3
            assert stats3["duplicates_skipped"] == 1, stats3

            # 4. Doublons au sein du même lot.
            stats4 = ingest_raw_jobs(
                [
                    _make_job("Stage NLP", "https://example.com/3"),
                    _make_job("Stage NLP bis", "https://example.com/3"),
                ],
                db,
            )
            assert stats4["new_inserted"] == 1, stats4
            assert stats4["duplicates_skipped"] == 1, stats4

            # 5. find_new_raw_jobs ne retourne que les inédites.
            candidate = [
                _make_job("Stage Data Scientist", "https://example.com/1"),   # déjà en base
                _make_job("Stage Deep Learning", "https://example.com/4"),    # nouvelle
            ]
            new = find_new_raw_jobs(candidate, db)
            assert len(new) == 1 and new[0].url == "https://example.com/4", new

            # 6. Conversion du modèle -> colonnes de la table.
            record = raw_job_to_dict(_make_job("Stage X", "https://example.com/9"))
            assert record["source"] == "wttj"
            assert record["final_score"] == 0.0
            assert record["semantic_score"] == 0.0
        finally:
            # Libère le handle SQLite avant suppression du dossier temporaire (Windows).
            db.engine.dispose()

    print("[OK] test_bridge.py : tous les tests passent.")


if __name__ == "__main__":
    test_bridge_ingestion()
