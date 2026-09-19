"""Test de fumée de la couche de persistance (sans dépendance torch)."""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import create_engine

from src.constants import (
    RUN_OK,
    RUN_PARTIAL,
    RUN_RUNNING,
    STATUS_APPLIED,
    STATUS_INTERVIEW,
    STATUS_NEW,
    STATUS_REJECTED,
    TIER_1,
)
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
    test_collection_memory()
    test_telemetry()
    test_collection_counters()
    test_objectif_de_passe_et_migration()
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


def test_collection_memory() -> None:
    """Mémoire de collecte : backfill, upsert idempotent, index et élagage."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "memoire.db"
        db = Database(path)
        base = {
            "company": "Doctolib",
            "location": "Paris",
            "company_tier": TIER_1,
            "semantic_score": 0.0,
            "final_score": 0.0,
            "status": STATUS_NEW,
        }
        db.upsert_job(
            {
                **base,
                "title": "Stage IA",
                "url": "https://example.com/a",
                "source": "linkedin",
                "id_externe": "4400",
                "canonical_url": "example.com/a",
            }
        )
        db.engine.dispose()

        db = Database(path)  # réouverture -> backfill de la mémoire de collecte
        # Toute offre déjà en base devient « connue » : sans cela, l'arrêt anticipé
        # ne pourrait pas s'enclencher au premier run hybride.
        pairs, urls = db.load_seen_index()
        assert ("linkedin", "4400") in pairs, pairs
        assert "example.com/a" in urls, urls
        assert db.count_seen_jobs() == 1, db.count_seen_jobs()

        # Upsert : first_seen_at préservé, décision rafraîchie.
        old = datetime.utcnow() - timedelta(days=10)
        stats = db.upsert_seen_jobs(
            [
                {
                    "source": "linkedin",
                    "external_key": "4466",
                    "canonical_url": "example.com/b",
                    "title": "Stage Data Analyst (Power BI)",
                    "decision": "REJECTED_BI",
                    "rejection_reason": "orientation BI / reporting (« power bi »)",
                    "last_seen_at": old,
                },
                {
                    "source": "linkedin",
                    "external_key": "4400",
                    "decision": "KNOWN",
                    "last_seen_at": old,
                },
            ]
        )
        assert stats == 2, stats
        assert db.count_seen_jobs() == 2, db.count_seen_jobs()
        assert db.count_seen_jobs("REJECTED_BI") == 1
        pairs, urls = db.load_seen_index(sources=["linkedin"])
        assert ("linkedin", "4466") in pairs and "example.com/b" in urls

        # Élagage : le bruit ancien part, les clés rattachées à une fiche restent.
        forgotten = db.prune_seen_jobs(days=1)
        assert forgotten == 1, forgotten  # seule la ligne sans fiche est oubliée
        assert db.count_seen_jobs() == 1, db.count_seen_jobs()
        db.engine.dispose()
    print("[OK] memoire de collecte : backfill, upsert, index et elagage")


def test_telemetry() -> None:
    """Runs et passes : écriture, lecture ordonnée, statut et purge de rétention."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "telemetrie.db")
        run_id = db.start_run(["linkedin", "jobteaser"])
        assert db.count_runs() == 1
        assert db.get_run(run_id)["status"] == RUN_RUNNING

        written = db.record_query_stats(
            run_id,
            [
                {
                    "source": "linkedin",
                    "query": "Stage Machine Learning",
                    "mode": "freshness",
                    "pages_fetched": 1,
                    "http_requests": 1,
                    "cards_seen": 10,
                    "jobs_kept": 3,
                    "jobs_known": 7,
                    "stop_reason": "early_stop",
                    "stop_detail": "7 offre(s) consécutive(s) déjà connue(s)",
                    "cle_inconnue": "ignorée",  # les colonnes inconnues sont ignorées
                },
                {
                    "source": "linkedin",
                    "query": "Stage Machine Learning",
                    "mode": "relevance",
                    "pages_fetched": 2,
                    "cards_seen": 20,
                    "jobs_kept": 20,
                    "stop_reason": "max_pages",
                },
            ],
        )
        assert written == 2, written
        rows = db.get_run_query_stats(run_id)
        assert [row["mode"] for row in rows] == ["freshness", "relevance"], rows
        assert rows[0]["stop_reason"] == "early_stop" and rows[0]["jobs_known"] == 7
        assert rows[1]["stop_reason"] == "max_pages"
        assert rows[0]["started_at"] is not None, "started_at est renseigné par défaut."
        assert db.get_recent_query_stats(limit=1)[0]["mode"] == "relevance"

        assert db.finish_run(
            run_id, status=RUN_PARTIAL, total_found=30, total_validated=23, notes="max_pages"
        )
        run = db.get_last_run()
        assert run["status"] == RUN_PARTIAL and run["total_validated"] == 23, run
        assert run["finished_at"] is not None
        assert db.finish_run("identifiant-inexistant", status=RUN_OK) is False

        # Rétention : on vieillit artificiellement la télémétrie de 10 jours.
        with db.engine.begin() as conn:
            conn.exec_driver_sql(
                "UPDATE scrape_runs SET started_at = ?", (datetime.utcnow() - timedelta(days=10),)
            )
            conn.exec_driver_sql(
                "UPDATE scrape_query_stats SET started_at = ?",
                (datetime.utcnow() - timedelta(days=10),),
            )
        removed = db.prune_telemetry(days=1)
        assert removed["runs"] == 1 and removed["query_stats"] == 2, removed
        assert db.count_runs() == 0 and db.get_recent_query_stats() == []
        db.engine.dispose()
    print("[OK] telemetrie : runs, passes (raisons d'arret) et purge")


def test_collection_counters() -> None:
    """Compteurs de pilotage : cherchées / refusées / déjà vues / acceptées, par source."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "compteurs.db")
        run_id = db.start_run(["linkedin"])
        db.record_query_stats(
            run_id,
            [
                {
                    "source": "linkedin",
                    "query": "Stage Data Scientist",
                    "mode": "freshness",
                    "pages_fetched": 6,
                    "http_requests": 6,
                    "cards_seen": 60,
                    "jobs_kept": 17,
                    "jobs_known": 9,
                    "jobs_rejected": 34,
                    "target_new": 40,
                    "stop_reason": "max_pages",
                },
                {
                    "source": "linkedin",
                    "query": "Stage Machine Learning",
                    "mode": "freshness",
                    "pages_fetched": 1,
                    "cards_seen": 10,
                    "jobs_known": 2,
                    "jobs_duplicate": 8,
                    "target_new": 40,
                    "stop_reason": "duplicate_page",
                },
                {
                    "source": "linkedin",
                    "query": "Stage Recherche IA",
                    "mode": "relevance",
                    "cards_seen": 30,
                    "jobs_kept": 8,
                    "jobs_known": 4,
                    "jobs_duplicate": 2,
                    "jobs_rejected": 16,
                    "target_new": 10,
                    "stop_reason": "stream_end",
                },
            ],
        )
        db.finish_run(run_id, status=RUN_OK)

        counters = db.get_collection_counters(limit=5)
        assert len(counters) == 1, counters
        row = counters[0]
        assert row["run_id"] == run_id and row["source"] == "linkedin", row
        assert row["cards_seen"] == 100, row            # 60 + 10 + 30 cherchées
        assert row["jobs_kept"] == 25, row              # 17 + 8 acceptées
        assert row["already_seen"] == 25, row           # (9+2+4) connues + (8+2) doublons
        assert row["refused"] == 50, row                # 34 + 16 refusées (BI / hors fenêtre)
        assert row["pages"] == 7 and row["http_requests"] == 6, row
        db.engine.dispose()
    print("[OK] compteurs de collecte : cherchées / refusées / déjà vues / acceptées")


def test_objectif_de_passe_et_migration() -> None:
    """Objectif de source tracé (atteint ou non) et colonne ajoutée sur une base antérieure."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "objectifs.db")
        run_id = db.start_run(["linkedin"])
        db.record_query_stats(
            run_id,
            [
                {
                    "source": "linkedin",
                    "query": "Stage Data Scientist",
                    "mode": "freshness",
                    "jobs_kept": 17,
                    "target_new": 40,
                    "stop_reason": "max_pages",
                },
                {
                    "source": "linkedin",
                    "query": "Stage Recherche IA",
                    "mode": "freshness",
                    "jobs_kept": 7,
                    "target_new": 40,
                    "stop_reason": "duplicate_page",
                },
                {
                    "source": "linkedin",
                    "query": "Stage Data Scientist",
                    "mode": "relevance",
                    "jobs_kept": 10,
                    "target_new": 10,
                    "stop_reason": "quota",
                },
            ],
        )
        db.finish_run(run_id, status=RUN_OK)

        objectives = db.get_pass_objectives(limit=5)
        assert [item["mode"] for item in objectives] == ["freshness", "relevance"], objectives
        freshness, relevance = objectives
        assert freshness["target_new"] == 40 and freshness["jobs_kept"] == 24, freshness
        # Objectif manqué et flux tronqué (plafond de pages) -> à relancer.
        assert freshness["reached"] is False and freshness["incomplete"] is True, freshness
        assert relevance["target_new"] == 10 and relevance["jobs_kept"] == 10, relevance
        assert relevance["reached"] is True and relevance["incomplete"] is False, relevance
        db.engine.dispose()

    # Base « antérieure » : la table existe sans la colonne d'objectif.
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "ancienne.db"
        legacy = sqlite3.connect(path)
        legacy.execute(
            "CREATE TABLE scrape_query_stats ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, run_id VARCHAR(36) NOT NULL, "
            "source VARCHAR(50) NOT NULL, query VARCHAR(300) NOT NULL, "
            "mode VARCHAR(20) NOT NULL, started_at DATETIME NOT NULL, "
            "finished_at DATETIME, duration_seconds FLOAT, pages_fetched INTEGER, "
            "http_requests INTEGER, cards_seen INTEGER, jobs_kept INTEGER, "
            "jobs_known INTEGER, jobs_duplicate INTEGER, jobs_rejected INTEGER, "
            "jobs_out_of_window INTEGER, stop_reason VARCHAR(30) NOT NULL, "
            "stop_detail VARCHAR(300), stop_page INTEGER, newest_published_at DATETIME, "
            "oldest_published_at DATETIME, error VARCHAR(300))"
        )
        legacy.execute(
            "INSERT INTO scrape_query_stats (run_id, source, query, mode, started_at, "
            "pages_fetched, cards_seen, jobs_kept, stop_reason) VALUES "
            "('r1', 'linkedin', 'Stage ML', 'freshness', '2026-09-16 21:21:00', 6, 60, 24, 'max_pages')"
        )
        legacy.commit()
        legacy.close()

        db = Database(path)  # create_all puis migration additive
        with db.engine.begin() as conn:
            columns = {
                row[1]
                for row in conn.exec_driver_sql("PRAGMA table_info(scrape_query_stats)")
            }
        assert "target_new" in columns, columns
        rows = db.get_recent_query_stats()
        assert rows[0]["target_new"] == 0, "Valeur par défaut, données existantes préservées."
        assert rows[0]["cards_seen"] == 60 and rows[0]["jobs_kept"] == 24, rows[0]
        db.engine.dispose()
    print("[OK] objectif de passe : suivi atteint/manqué et migration additive")


def test_unranked_jobs_and_stats() -> None:
    """Sélection des offres non notées : gestion des erreurs API, tri par description et stats."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "test_unranked.db"
        db = Database(path)
        try:
            base = {
                "company": "TestCorp",
                "location": "Paris",
                "company_tier": TIER_1,
                "semantic_score": 0.0,
                "final_score": 0.0,
                "status": STATUS_NEW,
            }

            # 1. Offre déjà notée avec succès
            db.upsert_job({**base, "title": "Offre Notée", "url": "http://test.com/1", "rerank_score": 85.0})
            # 2. Offre non notée SANS description
            db.upsert_job({**base, "title": "Offre Sans Desc", "url": "http://test.com/2", "description": ""})
            # 3. Offre non notée AVEC description complète
            db.upsert_job({**base, "title": "Offre Avec Desc", "url": "http://test.com/3", "description": "Longue description de mission ML"})
            # 4. Offre ayant échoué avec erreur API (doit être considérée comme non notée)
            db.upsert_job({
                **base,
                "title": "Offre Erreur API",
                "url": "http://test.com/4",
                "rerank_score": 0.0,
                "red_flags": ["Erreur API Gemini (429 - Quota)"],
                "description": "Description ML suite",
            })
            # 5. Offre rejetée (ne doit pas être éligible)
            db.upsert_job({**base, "title": "Offre Rejetée", "url": "http://test.com/5", "status": STATUS_REJECTED})

            stats = db.get_scoring_stats()
            assert stats["total"] == 5
            assert stats["active"] == 4
            assert stats["rejected"] == 1
            assert stats["rated"] == 1
            assert stats["unrated"] == 3  # Sans desc + Avec desc + Erreur API
            assert stats["unrated_with_desc"] == 2  # Avec desc + Erreur API

            unranked = db.get_unranked_jobs(limit=10)
            assert len(unranked) == 3
            # Les offres avec description doivent arriver en premier
            titles = [u["title"] for u in unranked]
            assert titles[0] in ("Offre Avec Desc", "Offre Erreur API")
            assert titles[1] in ("Offre Avec Desc", "Offre Erreur API")
            assert titles[2] == "Offre Sans Desc"
        finally:
            db.engine.dispose()
    print("[OK] unranked jobs et stats : tri intelligent et reprise après erreur API")


def test_applied_at_tracking_and_regions() -> None:
    """Vérifie l'horodatage automatique de applied_at et la normalisation géographique."""
    from pages.statistiques import normalize_region

    # 1. Normalisation régionale
    assert normalize_region("Paris (75)") == "Paris & Île-de-France"
    assert normalize_region("Vélizy-Villacoublay") == "Paris & Île-de-France"
    assert normalize_region("Sophia Antipolis") == "PACA & Côte d'Azur"
    assert normalize_region("Lyon, Auvergne-Rhône-Alpes") == "Auvergne-Rhône-Alpes"
    assert normalize_region("Toulouse") == "Occitanie"
    assert normalize_region("Bordeaux") == "Nouvelle-Aquitaine"
    assert normalize_region("Rennes") == "Bretagne & Pays de la Loire"
    assert normalize_region("Lille") == "Hauts-de-France & Grand Est"
    assert normalize_region("") == "Autres / Non précisé"
    assert normalize_region(None) == "Autres / Non précisé"

    # 2. Persistance et horodatage applied_at
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "test_applied.db"
        db = Database(db_path)
        try:
            job_payload = {
                "id": "app_test_1",
                "title": "Stage Deep Learning",
                "company": "Inria",
                "location": "Sophia Antipolis",
                "url": "http://test.com/app1",
                "status": STATUS_NEW,
                "company_tier": TIER_1,
                "semantic_score": 85.0,
                "final_score": 85.0,
            }
            db.upsert_job(job_payload)
            jobs = db.get_jobs()
            j = next((x for x in jobs if x["id"] == "app_test_1"), None)
            assert j is not None
            assert j["applied_at"] is None

            # Transition vers POSTULÉ
            db.update_status("app_test_1", STATUS_APPLIED)
            jobs_after = db.get_jobs()
            j_after = next((x for x in jobs_after if x["id"] == "app_test_1"), None)
            assert j_after is not None
            assert j_after["status"] == STATUS_APPLIED
            assert j_after["applied_at"] is not None

            applied_time = j_after["applied_at"]
            # Transition vers ENTRETIEN : conserve la date de candidature
            db.update_status("app_test_1", STATUS_INTERVIEW)
            jobs_interview = db.get_jobs()
            j_interview = next((x for x in jobs_interview if x["id"] == "app_test_1"), None)
            assert j_interview["status"] == STATUS_INTERVIEW
            assert j_interview["applied_at"] == applied_time
        finally:
            db.engine.dispose()
    print("[OK] applied_at tracking et normalisation régionale")


if __name__ == "__main__":
    main()
