"""Tests du worker de notation en direct (au fil de l'eau) avec LLM."""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any
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

