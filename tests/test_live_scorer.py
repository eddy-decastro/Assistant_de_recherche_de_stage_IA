"""Tests du worker de notation en direct (au fil de l'eau) avec LLM."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scrapers.models import RawJob
from src.matching.live_scorer import LiveRerankWorker, create_batch_callback
from src.storage.database import Database, make_job_id


def test_live_rerank_worker_enqueue_and_process(tmp_path: Path) -> None:
    """Vérifie que le worker consomme la file et met à jour la base de données."""
    db_file = tmp_path / "test_live.db"
    db = Database(str(db_file))

    # Configuration minimale de test
    config = {
        "llm": {"provider": "google", "tier": "free", "concurrency": 1},
        "scoring": {"cv_path": "data/cv_eddy.txt"},
        "ranking": {"top_n_rerank": 5},
    }

    worker = LiveRerankWorker(db, config, max_jobs=5, concurrency=1)

    # Mocker le judge pour ne pas faire de vrais appels réseau dans le test unitaire
    mock_judge = MagicMock()
    mock_judge.available = True
    mock_judge.judge.return_value = {
        "rerank_score": 88.0,
        "verdict": "EXCELLENT",
        "match_reasons": ["Bonne adéquation R&D"],
        "red_flags": [],
        "tech_stack": ["Python", "PyTorch"],
        "sub_scores": {"modeling_depth": 5},
        "hard_cap_triggered": None,
        "reasoning": "Offre parfaite.",
    }
    worker.judge = mock_judge

    # Insérer une offre préliminaire en base
    job_id = make_job_id("Stage R&D IA", "Mistral AI", "https://mistral.ai/stage")
    db.upsert_job({
        "id": job_id,
        "title": "Stage R&D IA",
        "company": "Mistral AI",
        "url": "https://mistral.ai/stage",
        "description": "Super stage en LLM",
        "source": "wttj",
    })

    # Démarrer le worker
    worker.start()

    # Enqueue le job
    added = worker.enqueue([{
        "id": job_id,
        "title": "Stage R&D IA",
        "company": "Mistral AI",
        "url": "https://mistral.ai/stage",
        "description": "Super stage en LLM",
    }])
    assert added == 1

    # Attendre la fin du traitement
    evaluated = worker.wait_completion(timeout=5.0)
    assert evaluated == 1
    assert worker.evaluated_count == 1

    # Vérifier que la note a bien été enregistrée en SQLite
    job_in_db = db.get_job_by_id(job_id)
    assert job_in_db is not None
    assert job_in_db["rerank_score"] == 88.0
    assert job_in_db["verdict"] == "EXCELLENT"


def test_scoring_modes_v4_main_v3_behind_flag() -> None:
    """N1 : la v4 est la note principale ; la v3 ne tourne que derrière son flag."""
    from src.matching.live_scorer import scoring_modes

    assert scoring_modes({}) == (False, True)
    assert scoring_modes({"scoring_v4": {"enabled": True}}) == (True, False)
    assert scoring_modes(
        {"scoring_v4": {"enabled": True}, "scoring_v3": {"rerank_enabled": True}}
    ) == (True, True)


def test_live_worker_v4_mode_persists_v4_only(tmp_path: Path) -> None:
    """N1 : en mode v4, le worker note avec LLMJudgeV4 (simulé) et n'écrit pas de note v3."""
    db = Database(str(tmp_path / "test_live_v4.db"))
    config = {
        "llm": {"provider": "google", "tier": "free", "concurrency": 1},
        "scoring": {"cv_path": "data/cv_eddy.txt"},
        "ranking": {"top_n_rerank": 5},
        "scoring_v4": {"enabled": True, "feedback": {"enabled": False}},
    }
    worker = LiveRerankWorker(db, config, max_jobs=5, concurrency=1)
    assert worker.judge is None  # juge v3 non instancié

    mock_v4 = MagicMock()
    mock_v4.available = True
    mock_v4.judge.return_value = {
        "v4_status": "OK",
        "v4_class": "B",
        "v4_display_score": 84.0,
        "v4_class_score": 87.0,
        "v4_flags_json": "[]",
        "excluded": False,
        "api_error": False,
    }
    worker.judge_v4 = mock_v4

    job_id = make_job_id("Stage LLM", "Mistral AI", "https://mistral.ai/v4")
    job = {"id": job_id, "title": "Stage LLM", "company": "Mistral AI", "url": "https://mistral.ai/v4"}
    db.upsert_job(dict(job))
    worker.start()
    assert worker.enqueue([job]) == 1
    assert worker.wait_completion(timeout=5.0) == 1

    saved = db.get_job_by_id(job_id)
    assert saved is not None
    assert saved["v4_display_score"] == 84.0
    assert saved["v4_class"] == "B"
    assert saved["rerank_score"] is None
    # Déjà notée v4 : plus remise en file
    assert worker._already_scored(saved) is True
    db.engine.dispose()


def test_create_batch_callback(tmp_path: Path) -> None:
    """Vérifie que create_batch_callback ingère et transmet au worker."""
    db_file = tmp_path / "test_callback.db"
    db = Database(str(db_file))

    raw_jobs = [
        RawJob(
            id_externe="ext1",
            source="wttj",
            title="Stage Data Science",
            company="Veolia",
            location="Paris",
            url="https://wttj.co/jobs/1",
            description="Mission ML",
        ),
        RawJob(
            id_externe="ext2",
            source="wttj",
            title="Stage Deep Learning",
            company="Amundi",
            location="Paris",
            url="https://wttj.co/jobs/2",
            description="Mission DL",
        ),
    ]

    mock_worker = MagicMock()
    mock_worker.available = True

    callback = create_batch_callback(db, worker=mock_worker)
    callback(raw_jobs)

    # 1. Vérifier l'ingestion en SQLite
    jobs = db.get_jobs()
    assert len(jobs) == 2

    # 2. Vérifier que le worker a été appelé
    mock_worker.enqueue.assert_called_once()
    args, _ = mock_worker.enqueue.call_args
    assert len(args[0]) == 2

