"""Tests unitaires pour src/notifications.py et l'orchestration optimisée de run_pipeline.py."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.constants import STATUS_EXCLUDED, STATUS_NEW
from src.notifications import (
    archive_expired_offers,
    pregenerate_top_cover_letters,
    send_pipeline_digest,
)
from src.storage.database import Database


def test_score_effectif_lit_d_abord_la_note_v4() -> None:
    """N1 : la note v4 prime ; les nouvelles offres n'ont plus de note v3."""
    from src.notifications import _effective_score

    assert _effective_score({"v4_display_score": 82, "rerank_score": 40, "final_score": 30}) == 82
    assert _effective_score({"rerank_score": 40, "final_score": 30}) == 40
    assert _effective_score({"final_score": 30}) == 30
    assert _effective_score({}) == 0.0


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
    """S4 : seules les offres prouvées fermées passent en IGNORÉ (« offre_fermee »), avec note."""
    import httpx

    from scrapers.liveness import LivenessChecker
    from src.constants import CLOSED_OFFER_REASON, STATUS_IGNORED

    db = Database(tmp_path / "test.db")
    config = {
        "automations": {
            "check_expired_offers_enabled": True,
            "expired_offers_age_days": 10,
        }
    }

    old_date = datetime.now() - timedelta(days=15)
    recent_date = datetime.now() - timedelta(days=2)

    for job_id, url, created, notes in [
        ("job_old_404", "https://example.com/job/old404", old_date, "Note existante"),
        ("job_old_closed", "https://example.com/job/closed", old_date, None),
        ("job_old_active", "https://example.com/job/active", old_date, None),
        ("job_old_429", "https://limited.example.org/job/429", old_date, None),
        ("job_old_timeout", "https://slow.example.net/job/timeout", old_date, None),
        ("job_recent", "https://example.com/job/recent", recent_date, None),
    ]:
        db.upsert_job({
            "id": job_id, "title": "Stage ML", "company": "Startup", "url": url,
            "status": STATUS_NEW, "created_at": created, "user_notes": notes,
        })

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "old404" in url:
            return httpx.Response(404, text="404 Not Found")
        if "closed" in url:
            return httpx.Response(200, text="<html><body>Cette offre n'est plus disponible pour le moment</body></html>")
        if "429" in url:
            return httpx.Response(429)
        if "timeout" in url:
            raise httpx.ReadTimeout("trop long")
        if "recent" in url:  # pragma: no cover — une offre récente n'est pas sondée
            raise AssertionError("offre récente sondée")
        return httpx.Response(200, text="<html><body>Postulez maintenant ! Description complète du poste...</body></html>")

    checker = LivenessChecker(
        httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True),
        min_interval=0,
        sleep=lambda _s: None,
    )
    with patch("requests.get") as legacy_get:
        count = archive_expired_offers(db, config=config, max_checks=10, checker=checker)
        legacy_get.assert_not_called()
    assert count == 2

    job1 = db.get_job("job_old_404")
    assert job1["status"] == STATUS_IGNORED
    assert job1["rejection_reason"] == CLOSED_OFFER_REASON
    assert "[Auto] Fermée : HTTP 404" in (job1["user_notes"] or "")
    assert "Note existante" in (job1["user_notes"] or "")

    job2 = db.get_job("job_old_closed")
    assert job2["status"] == STATUS_IGNORED
    assert "[Auto] Fermée : la page affiche « cette offre n'est plus disponible »" in (job2["user_notes"] or "")

    # Ouverte, 429 (limite de débit) ou délai dépassé : rien n'est conclu.
    for job_id in ("job_old_active", "job_old_429", "job_old_timeout", "job_recent"):
        assert db.get_job(job_id)["status"] == STATUS_NEW, job_id


def test_archive_expired_offers_never_overrides_user_status(tmp_path: Path) -> None:
    """Un statut saisi pendant la vérification (ex. POSTULÉ) n'est jamais écrasé."""
    from scrapers.liveness import LivenessVerdict
    from src.constants import STATUS_APPLIED

    db = Database(tmp_path / "test.db")
    db.upsert_job({
        "id": "job_x", "title": "Stage ML", "company": "X", "url": "https://example.com/job/x",
        "status": STATUS_NEW, "created_at": datetime.now() - timedelta(days=30),
    })

    class _Checker:
        network_checks = 0

        def check(self, url: str, source: str | None = None) -> LivenessVerdict:
            self.network_checks += 1
            db.update_status("job_x", STATUS_APPLIED)  # Eddy postule pendant la sonde
            return LivenessVerdict("ferme", "HTTP 404 (offre supprimée)", 404)

    config = {"automations": {"check_expired_offers_enabled": True, "expired_offers_age_days": 7}}
    assert archive_expired_offers(db, config=config, checker=_Checker()) == 0
    assert db.get_job("job_x")["status"] == STATUS_APPLIED


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
    assert db.get_job("old_new")["rejection_reason"] == "auto_archive"
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
        mock_db.get_unranked_v4_jobs.return_value = []  # notation v4 (note principale)
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
        mock_db.get_unranked_v4_jobs.return_value = [{"id": "2"}]
        mock_db.get_v1_jobs.return_value = [{"id": "3"}]

        pipeline_main(["--no-sync"])

        assert mock_backfill.call_count == 1
        # Étape 1 + Étape 3 (rerank) + Étape 4 (recompute-scores car v1 présent)
        assert mock_scrapers.call_count == 3
