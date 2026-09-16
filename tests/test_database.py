"""Test de fumée de la couche de persistance (sans dépendance torch)."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import create_engine

from src.constants import STATUS_APPLIED, STATUS_NEW, TIER_1
from src.storage.database import Database, SQLITE_BUSY_TIMEOUT_MS, make_job_id


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "test.db")

        job = {
            "title": "Data Scientist (Stage)",
            "company": "Doctolib",
            "location": "Paris",
            "url": "https://example.com/job/1",
            "description": "PyTorch, Docker, LLM",
            "source": "test",
            "company_tier": TIER_1,
            "semantic_score": 80.0,
            "final_score": 75.0,
        }

        # Insertion
        db.upsert_job(job)
        assert db.count_jobs() == 1, "L'offre devrait être insérée."

        # Pas de doublon
        db.upsert_job({**job, "final_score": 88.0})
        assert db.count_jobs() == 1, "L'upsert ne doit pas créer de doublon."

        rows = db.get_jobs()
        assert rows[0]["final_score"] == 88.0, "L'upsert doit mettre à jour le score."

        # Filtres
        assert len(db.get_jobs(min_score=90.0)) == 0
        assert len(db.get_jobs(min_score=80.0)) == 1
        assert len(db.get_jobs(exclude_esn=True)) == 1
        assert len(db.get_jobs(statuses=STATUS_NEW)) == 1

        # Mise à jour du statut
        job_id = make_job_id(job["title"], job["company"], job["url"])
        assert db.update_status(job_id, STATUS_APPLIED) is True
        assert db.count_jobs(STATUS_APPLIED) == 1

        # Statut invalide
        try:
            db.update_status(job_id, "NEXISTE_PAS")
        except ValueError:
            pass
        else:
            raise AssertionError("Un statut invalide doit lever ValueError.")

        # Filtre par plateforme source (colonne jobs.source).
        db.upsert_job(
            {
                **job,
                "title": "Stage NLP",
                "url": "https://example.com/job/2",
                "source": "linkedin",
            }
        )
        assert db.count_jobs() == 2
        assert len(db.get_jobs(sources=["linkedin"])) == 1, "Le filtre source doit isoler LinkedIn."
        assert len(db.get_jobs(sources=["jobteaser"])) == 0, "Aucune offre JobTeaser attendue."
        assert len(db.get_jobs(sources=["linkedin", "test"])) == 2, "Le filtre doit accepter plusieurs sources."
        assert len(db.get_jobs(sources=None)) == 2, "sources=None doit désactiver le filtre."
        assert len(db.get_jobs(sources=[])) == 2, "Une liste vide doit désactiver le filtre."
        assert dict(db.get_source_counts()) == {"test": 1, "linkedin": 1}, db.get_source_counts()

        # Libère le handle SQLite avant la suppression du dossier temporaire (Windows).
        db.engine.dispose()

    test_migration()
    test_wal_pragmas()
    test_descriptions()
    print("[OK] test_database.py : tous les tests passent.")


def test_migration() -> None:
    """Vérifie que _migrate ajoute les colonnes de l'étape 2 sur une base ancienne."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "old.db"
        engine = create_engine(f"sqlite:///{path}")
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "CREATE TABLE jobs ("
                "id VARCHAR(64) NOT NULL PRIMARY KEY, title VARCHAR(500) NOT NULL, "
                "company VARCHAR(300) NOT NULL, location VARCHAR(300), url VARCHAR(2000) NOT NULL, "
                "description TEXT, source VARCHAR(100), company_tier INTEGER NOT NULL, "
                "semantic_score FLOAT NOT NULL, final_score FLOAT NOT NULL, "
                "status VARCHAR(30) NOT NULL, created_at DATETIME NOT NULL)"
            )
        engine.dispose()

        db = Database(path)  # instancie -> déclenche la migration
        with db.engine.begin() as conn:
            columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(jobs)")}
        expected = {"rerank_score", "verdict", "match_reasons", "red_flags", "tech_stack"}
        missing = expected - columns
        assert not missing, f"Colonnes manquantes après migration : {missing}"
        db.engine.dispose()
    print("[OK] migration : colonnes etape 2 ajoutees sur une base ancienne")


def test_wal_pragmas() -> None:
    """WAL + busy_timeout actifs : lecture du dashboard pendant l'écriture des scrapers."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "wal.db")
        with db.engine.connect() as conn:
            journal_mode = conn.exec_driver_sql("PRAGMA journal_mode").scalar()
            busy_timeout = conn.exec_driver_sql("PRAGMA busy_timeout").scalar()
        assert str(journal_mode).casefold() == "wal", journal_mode
        assert int(busy_timeout) == SQLITE_BUSY_TIMEOUT_MS, busy_timeout
        db.engine.dispose()
    print("[OK] SQLite : WAL + busy_timeout actifs (plus de 'database is locked')")


def test_descriptions() -> None:
    """Rattrapage des descriptions : update, sélection des offres sans texte, comptage."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "descriptions.db")
        base = {
            "company": "Mistral AI",
            "location": "Paris",
            "source": "linkedin",
            "company_tier": TIER_1,
            "semantic_score": 10.0,
            "final_score": 10.0,
        }
        db.upsert_job({**base, "title": "Stage IA", "url": "https://example.com/a", "description": ""})
        db.upsert_job(
            {**base, "title": "Stage ML", "url": "https://example.com/b", "description": "PyTorch"}
        )

        assert db.count_with_description() == 1
        missing = db.get_jobs_missing_description()
        assert len(missing) == 1 and missing[0]["title"] == "Stage IA", missing
        assert db.get_jobs_missing_description(sources=["jobteaser"]) == []

        assert db.update_description(missing[0]["id"], "Description complète") is True
        assert db.count_with_description() == 2
        assert db.get_jobs_missing_description() == [], "Plus aucune offre sans description."
        assert db.update_description("identifiant-inexistant", "x") is False
        db.engine.dispose()
    print("[OK] descriptions : rattrapage + selection des offres sans texte")


if __name__ == "__main__":
    main()
