"""Tests unitaires pour src/notifications.py et l'orchestration optimisée de run_pipeline.py."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.constants import STATUS_EXCLUDED, STATUS_NEW, STATUS_REJECTED
from src.notifications import (
    archive_expired_offers,
    pregenerate_top_cover_letters,
    send_pipeline_digest,
)
from src.storage.database import Database


def test_automations_disabled_return_immediately(tmp_path: Path) -> None:
    """Vérifie que les trois fonctions quittent immédiatement sans effet si leur toggle est désactivé."""
    db = Database(tmp_path / "test.db")
    config = {
        "automations": {
            "notifications_enabled": False,
            "check_expired_offers_enabled": False,
            "pregenerate_letters_enabled": False,
        }
    }

    dummy_job = {
        "id": "dummy_1",
        "title": "Stage ML",
        "company": "Mistral AI",
        "url": "https://example.com/job/1",
        "rerank_score": 95.0,
        "status": STATUS_NEW,
    }

    assert send_pipeline_digest([dummy_job], config=config) is False
    assert archive_expired_offers(db, config=config) == 0
    assert pregenerate_top_cover_letters(db, config=config) == 0


def test_send_pipeline_digest_discord_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Vérifie le formatage du message Discord et l'envoi réussi via DISCORD_WEBHOOK_URL."""
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/123/xyz")
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    config = {
        "automations": {
            "notifications_enabled": True,
            "notification_min_score": 75,
        }
    }
    jobs = [
        {
            "id": "job_1",
            "title": "Stage ML Engineer",
            "company": "Owkin",
            "url": "https://owkin.com/job/1",
            "rerank_score": 90.0,
            "verdict": "EXCELLENT",
            "status": STATUS_NEW,
        },
        {
            "id": "job_2",
            "title": "Stage BI Analyst",
            "company": "ESN Corp",
            "url": "https://example.com/job/2",
            "rerank_score": 40.0,
            "verdict": "MITIGÉ",
            "status": STATUS_NEW,
        },
        {
            "id": "job_3",
            "title": "Stage Data Science",
            "company": "Tech Corp",
            "url": "https://example.com/job/3",
            "rerank_score": 85.0,
            "verdict": "EXCLU",
            "status": STATUS_EXCLUDED,
        },
    ]

    mock_resp = MagicMock()
    mock_resp.status_code = 204

    with patch("requests.post", return_value=mock_resp) as mock_post:
        result = send_pipeline_digest(jobs, config=config, degraded_sources=1)
        assert result is True
        assert mock_post.called

        call_kwargs = mock_post.call_args[1]
        content = call_kwargs["json"]["content"]
        assert "Stage ML Engineer" in content
        assert "Owkin" in content
        assert "90/100" in content
        # L'offre job_2 (< 75) et job_3 (exclue) ne doivent pas figurer dans le digest
        assert "Stage BI Analyst" not in content
        assert "Stage Data Science" not in content
        # Alerte sur degraded_sources présente
        assert "source(s) de scraping dégradée(s)" in content


def test_send_pipeline_digest_slack_and_telegram(monkeypatch: pytest.MonkeyPatch) -> None:
    """Vérifie l'envoi multi-canaux vers Slack et Telegram."""
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/123/xyz")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:ABC")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "987654")

    config = {
        "automations": {
            "notifications_enabled": True,
            "notification_min_score": 80,
        }
    }
    jobs = [
        {
            "id": "job_1",
            "title": "Research Intern",
            "company": "Kyutai",
            "url": "https://kyutai.org/job/1",
            "final_score": 88.0,
            "verdict": "EXCELLENT",
            "status": STATUS_NEW,
        }
    ]

    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch("requests.post", return_value=mock_resp) as mock_post:
        result = send_pipeline_digest(jobs, config=config)
        assert result is True
        assert mock_post.call_count == 2


def test_archive_expired_offers(tmp_path: Path) -> None:
    """Vérifie l'archivage automatique en STATUS_REJECTED avec note quand le lien retourne 404 ou est fermé."""
    db = Database(tmp_path / "test.db")
    config = {
        "automations": {
            "check_expired_offers_enabled": True,
            "expired_offers_age_days": 10,
        }
    }

    old_date = datetime.now() - timedelta(days=15)
    recent_date = datetime.now() - timedelta(days=2)

    # Offre 1 : ancienne (15 jours), lien 404 -> archivée
    db.upsert_job({
        "id": "job_old_404",
        "title": "Stage ML Ancien",
        "company": "Startup A",
        "url": "https://example.com/job/old404",
        "status": STATUS_NEW,
        "created_at": old_date,
        "user_notes": "Note existante",
    })

    # Offre 2 : ancienne (15 jours), lien 200 avec mention d'expiration -> archivée
    db.upsert_job({
        "id": "job_old_closed",
        "title": "Stage R&D Expiré",
        "company": "Startup B",
        "url": "https://example.com/job/closed",
        "status": STATUS_NEW,
        "created_at": old_date,
    })

    # Offre 3 : ancienne (15 jours), lien 200 actif -> conservée
    db.upsert_job({
        "id": "job_old_active",
        "title": "Stage Toujours Ouvert",
        "company": "Startup C",
        "url": "https://example.com/job/active",
        "status": STATUS_NEW,
        "created_at": old_date,
    })

    # Offre 4 : récente (2 jours) -> ignorée du contrôle
    db.upsert_job({
        "id": "job_recent",
        "title": "Stage Tout Neuf",
        "company": "Startup D",
        "url": "https://example.com/job/recent",
        "status": STATUS_NEW,
        "created_at": recent_date,
    })

    def mock_get(url: str, **kwargs: Any) -> MagicMock:
        resp = MagicMock()
        if "old404" in url:
            resp.status_code = 404
            resp.text = "404 Not Found"
        elif "closed" in url:
            resp.status_code = 200
            resp.text = "<html><body>Cette offre n'est plus disponible pour le moment</body></html>"
        else:
            resp.status_code = 200
            resp.text = "<html><body>Postulez maintenant ! Description complète du poste...</body></html>"
        return resp

    with patch("requests.get", side_effect=mock_get):
        count = archive_expired_offers(db, config=config, max_checks=10)
        assert count == 2

    job1 = db.get_job("job_old_404")
    assert job1 is not None
    assert job1["status"] == STATUS_REJECTED
    assert "[Auto] Offre expirée sur la plateforme" in (job1["user_notes"] or "")
    assert "Note existante" in (job1["user_notes"] or "")

    job2 = db.get_job("job_old_closed")
    assert job2 is not None
    assert job2["status"] == STATUS_REJECTED
    assert "[Auto] Offre expirée sur la plateforme" in (job2["user_notes"] or "")

    job3 = db.get_job("job_old_active")
    assert job3 is not None
    assert job3["status"] == STATUS_NEW

    job4 = db.get_job("job_recent")
    assert job4 is not None
    assert job4["status"] == STATUS_NEW


def test_archive_old_offers(tmp_path: Path) -> None:
    """Archive (IGNORÉ) les offres NOUVEAU de plus de 21 jours, épargne les autres statuts."""
    from src.constants import STATUS_APPLIED, STATUS_IGNORED
    from src.notifications import archive_old_offers

    db = Database(tmp_path / "test.db")
    config = {"automations": {"auto_archive_enabled": True, "auto_archive_age_days": 21}}
    old = datetime.now() - timedelta(days=30)
    recent = datetime.now() - timedelta(days=5)

    for job_id, status, created in [
        ("old_new", STATUS_NEW, old),
        ("recent_new", STATUS_NEW, recent),
        ("old_applied", STATUS_APPLIED, old),
    ]:
        db.upsert_job({
            "id": job_id, "title": "Stage ML", "company": "X",
            "url": f"https://example.com/{job_id}", "status": status, "created_at": created,
        })

    with patch("requests.get") as mock_get:
        assert archive_old_offers(db, config=config) == 1
        mock_get.assert_not_called()

    assert db.get_job("old_new")["status"] == STATUS_IGNORED
    assert "[Auto] Archivée" in (db.get_job("old_new")["user_notes"] or "")
    assert db.get_job("recent_new")["status"] == STATUS_NEW
    assert db.get_job("old_applied")["status"] == STATUS_APPLIED

    assert archive_old_offers(db, config={"automations": {"auto_archive_enabled": False}}) == 0


def test_pregenerate_top_cover_letters(tmp_path: Path) -> None:
    """Vérifie la génération et sauvegarde en base des lettres pour les offres éligibles >= min_score."""
    db = Database(tmp_path / "test.db")
    config = {
        "automations": {
            "pregenerate_letters_enabled": True,
            "pregenerate_letters_top_n": 2,
            "pregenerate_letters_min_score": 85,
        }
    }

    # Offre 1 : score 92 (éligible)
    db.upsert_job({
        "id": "job_top_1",
        "title": "Stage ML 1",
        "company": "Company 1",
        "url": "https://example.com/1",
        "rerank_score": 92.0,
        "status": STATUS_NEW,
    })

    # Offre 2 : score 88 (éligible)
    db.upsert_job({
        "id": "job_top_2",
        "title": "Stage ML 2",
        "company": "Company 2",
        "url": "https://example.com/2",
        "rerank_score": 88.0,
        "status": STATUS_NEW,
    })

    # Offre 3 : score 86 (éligible mais exclu par top_n=2)
    db.upsert_job({
        "id": "job_top_3",
        "title": "Stage ML 3",
        "company": "Company 3",
        "url": "https://example.com/3",
        "rerank_score": 86.0,
        "status": STATUS_NEW,
    })

    # Offre 4 : score 70 (< min_score)
    db.upsert_job({
        "id": "job_low",
        "title": "Stage Low",
        "company": "Company 4",
        "url": "https://example.com/4",
        "rerank_score": 70.0,
        "status": STATUS_NEW,
    })

    # Offre 5 : score 95 mais disposant déjà d'une lettre
    db.upsert_job({
        "id": "job_already_letter",
        "title": "Stage Deja Fait",
        "company": "Company 5",
        "url": "https://example.com/5",
        "rerank_score": 95.0,
        "status": STATUS_NEW,
        "cover_letter": "Lettre existante déjà rédigée.",
    })

    mock_generator = MagicMock()
    mock_generator.generate.side_effect = lambda job: f"Lettre générée pour {job['title']}"

    with patch("src.notifications.CoverLetterGenerator", return_value=mock_generator):
        count = pregenerate_top_cover_letters(db, config=config)
        assert count == 2

    job1 = db.get_job("job_top_1")
    assert job1 is not None
    assert job1["cover_letter"] == "Lettre générée pour Stage ML 1"

    job2 = db.get_job("job_top_2")
    assert job2 is not None
    assert job2["cover_letter"] == "Lettre générée pour Stage ML 2"

    job3 = db.get_job("job_top_3")
    assert job3 is not None
    assert not job3.get("cover_letter")

    job4 = db.get_job("job_low")
    assert job4 is not None
    assert not job4.get("cover_letter")

    job5 = db.get_job("job_already_letter")
    assert job5 is not None
    assert job5["cover_letter"] == "Lettre existante déjà rédigée."


def test_pipeline_conditional_steps(tmp_path: Path) -> None:
    """Vérifie que run_pipeline saute intelligemment les étapes inutiles quand la base est déjà à jour."""
    from run_pipeline import main as pipeline_main

    with patch("run_pipeline.Database") as mock_db_cls, \
         patch("run_pipeline.run_scrapers_main", return_value=0) as mock_scrapers, \
         patch("run_pipeline.run_backfill_main") as mock_backfill, \
         patch("run_pipeline.is_cloud_storage_configured", return_value=False):

        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db

        # Cas 1 : aucune offre sans description, aucune offre non notée, aucune v1
        mock_db.get_jobs_missing_description.return_value = []
        mock_db.get_unranked_jobs.return_value = []
        mock_db.get_v1_jobs.return_value = []
        mock_db.get_jobs.return_value = []

        pipeline_main(["--no-sync"])

        # Étape 1 appelée uniquement
        assert mock_scrapers.call_count == 1
        # Étape 2 ignorée
        assert mock_backfill.call_count == 0

        # Cas 2 : offres manquantes -> backfill appelé, rerank appelé, recalcul appelé
        mock_scrapers.reset_mock()
        mock_backfill.reset_mock()
        mock_db.get_jobs_missing_description.return_value = [{"id": "1"}]
        mock_db.get_unranked_jobs.return_value = [{"id": "2"}]
        mock_db.get_v1_jobs.return_value = [{"id": "3"}]

        pipeline_main(["--no-sync"])

        assert mock_backfill.call_count == 1
        # Étape 1 + Étape 3 (rerank) + Étape 4 (recompute-scores car v1 présent)
        assert mock_scrapers.call_count == 3
