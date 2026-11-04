"""File d'attente Claude in Chrome (plan C1, C2, C3) : méthode, file, compte rendu, prompt.

Aucun appel LLM réel : la génération de lettre et de motivation courte est mockée.
Toutes les bases sont créées dans ``tmp_path``.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.constants import STATUS_APPLIED, STATUS_INTERVIEW, STATUS_NEW
from src.matching import apply_queue as aq
from src.matching.claude_pack import build_claude_pack, build_queue_prompt
from src.storage.database import Database

LETTER = "Madame, Monsieur,\n\nLettre de test pour la file.\n\nEddy"
SHORT = "Motivation courte de test. " * 40


@pytest.fixture
def no_llm():
    """Coupe tout appel LLM pendant la préparation des dossiers."""
    with patch("src.matching.cover_letter.CoverLetterGenerator.generate", return_value=LETTER), \
         patch("src.matching.cover_letter.CoverLetterGenerator._generate_short_llm_once", return_value=None), \
         patch("src.matching.cover_letter.CoverLetterGenerator.generate_short_motivation", return_value=SHORT):
        yield


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "queue.db")
    yield database
    database.engine.dispose()


def _job(job_id: str, company: str, url: str, source: str = "ats", status: str = STATUS_NEW) -> dict:
    return {
        "id": job_id,
        "title": f"Stage ML {company}",
        "company": company,
        "location": "Paris",
        "url": url,
        "source": source,
        "description": "Stage de fin d'études en apprentissage automatique.",
        "status": status,
        "cover_letter": LETTER,
        "match_reasons": ["Deep learning", "Python"],
        "tech_stack": ["PyTorch", "Python"],
    }


def _seed(db: Database, *jobs: dict) -> list[dict]:
    for job in jobs:
        db.upsert_job(dict(job))
    return [db.get_job(job["id"]) for job in jobs]


# --------------------------------------------------------------------------- #
# C3 — méthode de candidature
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("url", "source", "expected", "needs_account"),
    [
        ("https://www.welcometothejungle.com/fr/companies/x/jobs/y", "wttj", "wttj", False),
        ("https://jobs.lever.co/mistral/123", "ats", "lever", False),
        ("https://jobs.eu.lever.co/acme/123", "ats", "lever", False),
        ("https://boards.greenhouse.io/acme/jobs/1", "ats", "greenhouse", False),
        ("https://job-boards.eu.greenhouse.io/acme/jobs/1", "ats", "greenhouse", False),
        ("https://jobs.ashbyhq.com/dust/abc", "ats", "ashby", False),
        ("https://jobs.smartrecruiters.com/Acme/123", "ats", "smartrecruiters", False),
        ("https://thales.wd3.myworkdayjobs.com/fr-FR/Careers/job/1", "workday", "workday", True),
        ("https://edf.taleo.net/careersection/2/jobdetail.ftl", "ats", "taleo", True),
        ("https://career5.successfactors.eu/career?company=x", "ats", "successfactors", True),
        ("https://emse.jobteaser.com/fr/job-offers/1", "jobteaser", "jobteaser", False),
        ("https://fr.linkedin.com/jobs/view/123", "linkedin", "linkedin_externe", False),
        ("mailto:rh@acme.fr?subject=Stage", "manual", "email", False),
        ("https://acme.fr/carrieres/1", "manual", "inconnu", False),
    ],
)
def test_detect_apply_method(url, source, expected, needs_account) -> None:
    info = aq.detect_apply_method({"url": url, "source": source})
    assert info["method"] == expected
    assert info["needs_account"] is needs_account
    assert info["label"] and info["hint"]


def test_detect_apply_method_linkedin_easy_and_email_fallback() -> None:
    easy = aq.detect_apply_method({"url": "https://fr.linkedin.com/jobs/view/1", "easy_apply": True})
    assert easy["method"] == "linkedin_easy"
    by_text = aq.detect_apply_method(
        {"url": "https://fr.linkedin.com/jobs/view/2", "description": "Candidature simplifiée disponible"}
    )
    assert by_text["method"] == "linkedin_easy"
    external = aq.detect_apply_method(
        {"url": "https://fr.linkedin.com/jobs/view/3", "apply_url": "https://jobs.lever.co/acme/1"}
    )
    assert external["method"] == "lever"

    mail = aq.detect_apply_method({"url": "", "source": "manual", "description": "Envoyez CV à stage@acme.fr."})
    assert mail["method"] == "email"
    assert mail["contact_email"] == "stage@acme.fr"
    assert "stage@acme.fr" in mail["hint"]
    mailto = aq.detect_apply_method({"url": "mailto:rh@acme.fr"})
    assert mailto["contact_email"] == "rh@acme.fr"


def test_source_fallback_when_url_missing() -> None:
    assert aq.detect_apply_method({"url": "", "source": "welcome_to_the_jungle"})["method"] == "wttj"
    assert aq.detect_apply_method({"url": "", "source": "workday"})["needs_account"] is True


# --------------------------------------------------------------------------- #
# C1 — validation du compte rendu caveman
# --------------------------------------------------------------------------- #
def test_validate_report_accepts_caveman() -> None:
    report = "Moi remplir formulaire Thales. CV pas mis, Eddy mettre CV puis cliquer Envoyer."
    assert aq.validate_report(report) == report
    assert aq.validate_report("  Échec : compte requis  ") == "Échec : compte requis"
    # Les points d'une URL ne coupent pas la phrase.
    assert aq.count_sentences("Moi postuler sur jobs.lever.co/acme. Fini.") == 2


@pytest.mark.parametrize(
    "report",
    [
        "",
        "   ",
        None,
        "Une. Deux. Trois.",
        "Moi " + "remplir " * 50 + ".",
    ],
)
def test_validate_report_rejects(report) -> None:
    with pytest.raises(ValueError):
        aq.validate_report(report)


# --------------------------------------------------------------------------- #
# C1 — file d'attente
# --------------------------------------------------------------------------- #
def test_queue_orders_no_account_first_and_builds_current(db, tmp_path, no_llm) -> None:
    jobs = _seed(
        db,
        _job("wd1", "Thales", "https://thales.wd3.myworkdayjobs.com/x/job/1", "workday"),
        _job("lv1", "Mistral AI", "https://jobs.lever.co/mistral/1"),
        _job("gh1", "Dust", "https://boards.greenhouse.io/dust/jobs/1"),
    )
    result = aq.add_jobs_to_queue(db, jobs, output_dir=tmp_path / "ready", export_pdfs=False)
    assert result["added"] == ["wd1", "lv1", "gh1"]
    assert result["errors"] == {}

    queue = aq.get_queue(db)
    assert [i["job_id"] for i in queue["items"]] == ["lv1", "gh1", "wd1"]
    assert queue["counts"] == {"pending": 3, "failed": 0, "applied": 0}
    assert queue["url"] == aq.QUEUE_URL

    current = aq.get_current(db)
    assert current is not None
    assert current["job_id"] == "lv1"
    assert current["method"] == "lever" and current["needs_account"] is False
    assert current["remaining"] == 3
    assert current["cover_letter"] == LETTER
    assert current["short_motivation"] == SHORT
    assert 0 < len(current["short_pitch"]) <= 300
    assert current["candidate"]["first_name"]
    assert set(current["files"]) == {"cover_letter_pdf", "cv_pdf", "offer_dir"}
    assert any("Envoyer" in line for line in current["instructions"])
    assert any("CV à joindre" in line for line in current["instructions"])
    assert current["answers"]["Durée du stage"] == "6 mois"


def test_record_result_applied_sets_status_and_report(db, tmp_path, no_llm) -> None:
    jobs = _seed(db, _job("lv1", "Mistral AI", "https://jobs.lever.co/mistral/1"))
    aq.add_jobs_to_queue(db, jobs, output_dir=tmp_path, export_pdfs=False)

    report = "Moi remplir formulaire Mistral. CV pas mis, Eddy mettre CV puis cliquer Envoyer."
    item = aq.record_result(db, "lv1", "applied", report)
    assert item["state"] == aq.STATE_APPLIED
    assert item["report"] == report
    assert item["status"] == STATUS_APPLIED
    assert item["applied_at"]
    assert item["to_finish"] is True
    stored = db.get_job("lv1")
    assert stored["status"] == STATUS_APPLIED and stored["applied_at"] is not None
    assert aq.get_current(db) is None
    assert aq.get_queue(db)["counts"]["applied"] == 1


def test_record_result_failed_keeps_offer_with_reason(db, tmp_path, no_llm) -> None:
    jobs = _seed(
        db,
        _job("wd1", "Thales", "https://thales.wd3.myworkdayjobs.com/x/job/1", "workday"),
        _job("lv1", "Mistral AI", "https://jobs.lever.co/mistral/1"),
    )
    aq.add_jobs_to_queue(db, jobs, output_dir=tmp_path, export_pdfs=False)

    item = aq.record_result(db, "wd1", "failed", "Échec : compte requis.")
    assert item["state"] == aq.STATE_FAILED
    assert item["report"] == "Échec : compte requis."
    assert db.get_job("wd1")["status"] == STATUS_NEW  # statut d'Eddy intact
    queue = aq.get_queue(db)
    assert {i["job_id"]: i["state"] for i in queue["items"]} == {"lv1": "pending", "wd1": "failed"}
    assert aq.get_current(db)["job_id"] == "lv1"

    # Eddy remet l'offre dans la file : elle repasse en attente, le compte rendu reste visible.
    again = aq.enqueue_bundles(db, [{"id": "wd1", "url": "https://thales.wd3.myworkdayjobs.com/x"}])
    assert again["requeued"] == ["wd1"]
    wd1 = aq.get_queue_item(db, "wd1")
    assert wd1["state"] == aq.STATE_PENDING and wd1["report"] == "Échec : compte requis."


def test_record_result_errors(db, tmp_path, no_llm) -> None:
    jobs = _seed(db, _job("lv1", "Mistral AI", "https://jobs.lever.co/mistral/1"))
    aq.add_jobs_to_queue(db, jobs, output_dir=tmp_path, export_pdfs=False)
    with pytest.raises(ValueError):
        aq.record_result(db, "lv1", "applied", "Un. Deux. Trois.")
    with pytest.raises(ValueError):
        aq.record_result(db, "lv1", "peut-être", "Moi hésiter.")
    with pytest.raises(LookupError):
        aq.record_result(db, "absent", "failed", "Moi pas trouver offre.")
    # Rien n'a changé après les refus.
    assert aq.get_queue_item(db, "lv1")["state"] == aq.STATE_PENDING


def test_record_result_never_downgrades_interview(db, tmp_path, no_llm) -> None:
    jobs = _seed(db, _job("lv1", "Mistral AI", "https://jobs.lever.co/mistral/1"))
    aq.add_jobs_to_queue(db, jobs, output_dir=tmp_path, export_pdfs=False)
    db.update_status("lv1", STATUS_INTERVIEW)
    aq.record_result(db, "lv1", "applied", "Moi déjà postulé.")
    assert db.get_job("lv1")["status"] == STATUS_INTERVIEW


def test_already_applied_offers_are_skipped(db, tmp_path, no_llm) -> None:
    jobs = _seed(db, _job("ap1", "Acme", "https://jobs.lever.co/acme/1", status=STATUS_APPLIED))
    result = aq.add_jobs_to_queue(db, jobs, output_dir=tmp_path, export_pdfs=False)
    assert result["skipped"] == ["ap1"] and result["added"] == []
    assert aq.get_queue(db)["items"] == []


def test_add_ids_to_queue_reports_missing_and_letter_errors(db, tmp_path) -> None:
    job = _job("nl1", "Acme", "https://jobs.lever.co/acme/1")
    job["cover_letter"] = ""
    _seed(db, job)
    with patch("src.matching.cover_letter.CoverLetterGenerator.generate", return_value="⚠️ LLM indisponible"), \
         patch("src.matching.cover_letter.CoverLetterGenerator.generate_short_motivation", return_value=SHORT):
        result = aq.add_ids_to_queue(db, ["nl1", "inconnu"], output_dir=tmp_path, export_pdfs=False)
    assert result["missing"] == ["inconnu"]
    assert "nl1" in result["errors"]
    assert result["added"] == []


def test_remove_from_queue(db, tmp_path, no_llm) -> None:
    jobs = _seed(db, _job("lv1", "Mistral AI", "https://jobs.lever.co/mistral/1"))
    aq.add_jobs_to_queue(db, jobs, output_dir=tmp_path, export_pdfs=False)
    assert aq.remove_from_queue(db, "lv1") is True
    assert aq.remove_from_queue(db, "lv1") is False
    assert db.get_job("lv1") is not None


def test_ensure_queue_table_is_idempotent_and_migrates(db) -> None:
    from sqlalchemy import text

    with db.engine.begin() as conn:
        conn.execute(text("CREATE TABLE apply_queue (job_id VARCHAR(64) PRIMARY KEY, position INTEGER, state VARCHAR(20))"))
    aq.ensure_queue_table(db)
    aq.ensure_queue_table(db)
    with db.engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(apply_queue)"))}
    assert {"report", "method", "needs_account", "bundle_json", "updated_at"} <= cols


# --------------------------------------------------------------------------- #
# Pack Claude : prompt court + mise en file
# --------------------------------------------------------------------------- #
def test_queue_prompt_rules() -> None:
    prompt = build_queue_prompt()
    assert aq.QUEUE_URL in prompt
    assert "compte" in prompt and "mot de passe" in prompt
    assert "CV à joindre" in prompt
    assert "caveman" in prompt and "2 phrases" in prompt
    assert "Marquer postulé" in prompt and "Échec" in prompt
    assert "Ne clique jamais sur le bouton final Envoyer" in prompt
    assert "Ne clique jamais" not in build_queue_prompt(auto_submit=True)


def test_build_claude_pack_enqueues_and_stays_compatible(db, tmp_path, no_llm) -> None:
    jobs = _seed(
        db,
        _job("lv1", "Mistral AI", "https://jobs.lever.co/mistral/1"),
        _job("wd1", "Thales", "https://thales.wd3.myworkdayjobs.com/x/job/1", "workday"),
    )
    pack = build_claude_pack(jobs, db=db, output_dir=tmp_path, save_pdfs=True)
    assert pack["count"] == 2
    assert pack["queue"]["enabled"] is True and pack["queue"]["added"] == ["lv1", "wd1"]
    assert pack["chrome_prompt"].startswith(pack["queue_prompt"])
    assert "Mistral AI" in pack["chrome_prompt"]
    assert "Mistral AI" in pack["claude_project_json"]
    bundle = pack["bundles"][0]
    assert bundle["apply_method"]["method"] == "lever"
    assert "cv_pdf_path" in bundle and bundle["pitch_300"]
    assert Path(bundle["pdf_path"]).exists()
    assert aq.get_current(db)["job_id"] == "lv1"


def test_build_claude_pack_without_db_does_not_enqueue(tmp_path, no_llm) -> None:
    pack = build_claude_pack([_job("x1", "Acme", "https://jobs.ashbyhq.com/acme/1")], output_dir=tmp_path, save_pdfs=False)
    assert pack["queue"]["enabled"] is False
    assert pack["bundles"][0]["apply_method"]["method"] == "ashby"
