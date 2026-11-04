"""Tests unitaires de l'API FastAPI (flux, actions groupées, offres fermées et Pack Claude)."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from api.main import create_app  # noqa: E402
from scrapers.liveness import LivenessVerdict  # noqa: E402
from src.constants import (  # noqa: E402
    CLOSED_OFFER_REASON,
    STATUS_APPLIED,
    STATUS_IGNORED,
    STATUS_NEW,
    STATUS_REJECTED,
)
from src.storage.database import Database  # noqa: E402


def _seed_db(tmp_path: Path) -> Database:
    db = Database(str(tmp_path / "test_api.db"))
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db.upsert_job(
        {
            "id": "job_recent",
            "title": "Stage Research Intern Deep Learning",
            "company": "Mistral AI",
            "location": "Paris",
            "url": "https://mistral.ai/jobs/1",
            "description": "Stage recherche sur les architectures Transformers sous PyTorch.",
            "source": "ats",
            "status": STATUS_NEW,
            "created_at": now - timedelta(days=2),
            "rerank_score": 92.0,
        }
    )
    db.update_v4(
        "job_recent",
        {
            "v4_status": "OK",
            "v4_class": "B",
            "v4_stars": {
                "apprendre": {"stars": 5, "reason": "Chercheurs", "quote": "Transformers", "unknown": False},
                "technique": {"stars": 5, "reason": "PyTorch", "quote": "PyTorch", "unknown": False},
                "debouche": {"stars": 5, "reason": "Mistral", "quote": "reputation", "unknown": False},
                "logistique": {"stars": 5, "reason": "Paris", "quote": "Paris", "unknown": False},
            },
            "v4_center": 100.0,
            "v4_adjustment": 0,
            "v4_class_score": 100.0,
            "v4_display_score": 97.0,
            "v4_tags": ["pre_embauche"],
            "v4_flags": [],
            "v4_raw": {"summary": "Excellent stage R&D chez Mistral AI"},
            "v4_model": "gemini-3.1-flash-lite-preview",
        },
    )
    db.upsert_job(
        {
            "id": "job_old",
            "title": "Stage Machine Learning",
            "company": "Safran",
            "location": "Massy",
            "url": "https://safran.com/jobs/2",
            "description": "Stage ML sur séries temporelles.",
            "source": "wttj",
            "status": STATUS_NEW,
            "created_at": now - timedelta(days=20),
            "rerank_score": 78.0,
        }
    )
    return db


def test_api_stats_and_list_jobs(tmp_path: Path) -> None:
    db = _seed_db(tmp_path)
    client = TestClient(create_app(db))

    r_health = client.get("/api/health")
    assert r_health.status_code == 200 and r_health.json()["status"] == "ok"

    r_stats = client.get("/api/stats")
    assert r_stats.status_code == 200
    stats = r_stats.json()
    assert stats["total"] == 2
    assert stats["old_new_14d"] == 1
    assert stats["by_v4_class"]["B"] == 1

    r_jobs = client.get("/api/jobs?v4_classes=B")
    assert r_jobs.status_code == 200
    data = r_jobs.json()
    assert data["total"] == 1
    assert data["jobs"][0]["id"] == "job_recent"
    assert data["jobs"][0]["v4_display_score"] == 97


def test_api_bulk_status_and_quick_archive_old(tmp_path: Path) -> None:
    db = _seed_db(tmp_path)
    checked: list[str] = []

    class FakeChecker:
        """Sonde « offre fermée » simulée : aucune requête réseau."""

        network_checks = 0

        def check(self, url: str, source: str | None = None) -> LivenessVerdict:
            checked.append(url)
            self.network_checks += 1
            return LivenessVerdict(status="ferme", reason="HTTP 404", http_status=404)

    client = TestClient(create_app(db, liveness_checker=FakeChecker()))

    # Quick archive : seules les offres > 14 jours sont sondées, et archivées si fermées.
    r_arch = client.post("/api/jobs/quick-archive-old", json={"days": 14, "action": "archive"})
    assert r_arch.status_code == 200
    assert r_arch.json()["updated"] == 1
    assert r_arch.json()["ids"] == ["job_old"]
    assert checked == ["https://safran.com/jobs/2"]

    jobs_by_id = {j["id"]: j for j in db.get_jobs()}
    assert jobs_by_id["job_old"]["status"] == STATUS_IGNORED
    assert jobs_by_id["job_old"]["rejection_reason"] == CLOSED_OFFER_REASON
    assert jobs_by_id["job_recent"]["status"] == STATUS_NEW

    # Bulk status vers corbeille (REJETÉ) ou postulé
    r_bulk = client.post(
        "/api/jobs/bulk-status",
        json={"ids": ["job_recent"], "status": STATUS_APPLIED},
    )
    assert r_bulk.status_code == 200 and r_bulk.json()["updated"] == 1
    jobs_by_id = {j["id"]: j for j in db.get_jobs()}
    assert jobs_by_id["job_recent"]["status"] == STATUS_APPLIED

    r_trash = client.post(
        "/api/jobs/bulk-status",
        json={"ids": ["job_old"], "status": STATUS_REJECTED, "rejection_reason": "bulk_trash"},
    )
    assert r_trash.status_code == 200 and r_trash.json()["updated"] == 1
    jobs_by_id = {j["id"]: j for j in db.get_jobs()}
    assert jobs_by_id["job_old"]["status"] == STATUS_REJECTED


def test_api_cache_suit_les_ecritures(tmp_path: Path) -> None:
    """Le cache des offres ne doit jamais servir une donnée périmée."""
    db = _seed_db(tmp_path)
    client = TestClient(create_app(db))
    assert client.get("/api/stats").json()["by_status"][STATUS_NEW] == 2

    # Écriture par l'API : visible tout de suite.
    client.post("/api/jobs/bulk-status", json={"ids": ["job_old"], "status": STATUS_IGNORED})
    assert client.get("/api/stats").json()["by_status"][STATUS_NEW] == 1

    # Écriture par un autre process (pipeline, Streamlit) sur le même fichier : visible aussi.
    Database(str(db.db_path)).bulk_update_status(["job_recent"], STATUS_APPLIED)
    stats = client.get("/api/stats").json()["by_status"]
    assert STATUS_NEW not in stats and stats[STATUS_APPLIED] == 1
    ids = [j["id"] for j in client.get("/api/jobs?hide_processed=true").json()["jobs"]]
    assert ids == []


def test_api_claude_pack_and_cover_letter(tmp_path: Path) -> None:
    db = _seed_db(tmp_path)
    db.update_cover_letter("job_recent", "Madame, Monsieur, Lettre test Mistral.")
    client = TestClient(create_app(db))

    # Aucun appel LLM réel : la motivation courte retombe sur sa version déterministe.
    with patch(
        "src.matching.cover_letter.CoverLetterGenerator._generate_short_llm_once",
        return_value=None,
    ):
        r_pack = client.post("/api/claude-pack", json={"ids": ["job_recent"], "auto_submit": False})
    assert r_pack.status_code == 200
    pack = r_pack.json()
    assert pack["count"] == 1
    assert "Mistral AI" in pack["chrome_prompt"]

    r_pdf = client.get("/api/jobs/job_recent/cover-letter.pdf")
    assert r_pdf.status_code == 200
    assert r_pdf.headers["content-type"] == "application/pdf"
    assert len(r_pdf.content) > 100
