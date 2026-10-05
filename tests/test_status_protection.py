"""Les statuts saisis par l'utilisateur ne doivent jamais être écrasés par le juge ou le recalcul."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.constants import (
    STATUS_APPLIED,
    STATUS_EXCLUDED,
    STATUS_IGNORED,
    STATUS_INTERVIEW,
    STATUS_NEW,
    STATUS_REJECTED,
)
from src.storage.database import Database

USER_STATUSES = [STATUS_APPLIED, STATUS_INTERVIEW, STATUS_IGNORED, STATUS_REJECTED]


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "status.db")
    yield database
    database.engine.dispose()


def _add_job(db: Database, job_id: str, status: str) -> None:
    db.upsert_job(
        {
            "id": job_id,
            "title": "Stage Data Scientist",
            "company": "Acme",
            "url": f"http://test.com/{job_id}",
            "status": status,
        }
    )


def _exclude_via_judge(db: Database, job_id: str) -> None:
    db.update_rerank(
        job_id,
        rerank_score=0.0,
        verdict="EXCLU",
        exclusion_reason="Stage de césure / court hors PFE",
        contract_type="STAGE",
        grading_version="v3",
    )


@pytest.mark.parametrize("status", USER_STATUSES)
def test_judge_exclusion_keeps_user_status(db: Database, status: str) -> None:
    _add_job(db, "job", status)
    _exclude_via_judge(db, "job")
    job = db.get_job("job")
    assert job["status"] == status
    assert job["exclusion_reason"] == "Stage de césure / court hors PFE"
    assert job["verdict"] == "EXCLU"


def test_judge_exclusion_still_excludes_new_offers(db: Database) -> None:
    _add_job(db, "job", STATUS_NEW)
    _exclude_via_judge(db, "job")
    assert db.get_job("job")["status"] == STATUS_EXCLUDED


@pytest.mark.parametrize("status", USER_STATUSES)
def test_recompute_keeps_user_status_on_excluded_offer(db: Database, status: str) -> None:
    _add_job(db, "job", status)
    db.update_rerank(
        "job",
        rerank_score=80.0,
        verdict="BON",
        contract_type="ALTERNANCE",
        structure_type="INCONNU",
        grading_version="v3",
    )
    assert db.get_job("job")["status"] == status
    db.recompute_scores()
    job = db.get_job("job")
    assert job["status"] == status
    assert job["verdict"] == "EXCLU"
