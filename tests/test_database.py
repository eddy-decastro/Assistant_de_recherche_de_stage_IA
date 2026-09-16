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
from src.storage.database import Database, make_job_id


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


if __name__ == "__main__":
    main()
