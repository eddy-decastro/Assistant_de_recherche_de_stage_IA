"""Candidatures hors scraping : saisie manuelle, import des mails, dédoublonnage."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.constants import (  # noqa: E402
    REFUSAL_REASON,
    SOURCE_GMAIL,
    SOURCE_MANUAL,
    STATUS_APPLIED,
    STATUS_INTERVIEW,
    STATUS_NEW,
    STATUS_REJECTED,
)
from src.storage.database import Database, application_key  # noqa: E402
from tools import import_applications  # noqa: E402


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "test.db")


def test_cle_ignore_casse_accents_et_ponctuation() -> None:
    assert application_key("Hugging Face", "Stage – ML Engineer") == application_key("hugging-face", "stage ml engineer")
    assert application_key("Société Générale", "Data") == application_key("Societe Generale", "DATA")


def test_creation_d_une_candidature_manuelle(db: Database) -> None:
    job_id, created = db.record_application("Owkin", "ML Research Intern", applied_at=datetime(2026, 9, 28))
    job = db.get_job(job_id)
    assert created
    assert job["source"] == SOURCE_MANUAL
    assert job["status"] == STATUS_APPLIED
    assert job["applied_at"] == datetime(2026, 9, 28)
    assert job["url"] == ""


def test_offre_scrapee_existante_mise_a_jour_sans_doublon(db: Database) -> None:
    db.upsert_job({"title": "Stage Data Scientist", "company": "Aqemia", "url": "https://x/1", "source": "linkedin"})
    job_id, created = db.record_application("AQEMIA", "Stage data scientist", source=SOURCE_GMAIL)
    assert not created
    job = db.get_job(job_id)
    assert job["source"] == "linkedin"
    assert job["status"] == STATUS_APPLIED
    assert job["applied_at"] is not None
    assert db.count_jobs() == 1


def test_le_statut_avance_mais_ne_recule_jamais(db: Database) -> None:
    job_id, _ = db.record_application("Alan", "ML Intern", status=STATUS_INTERVIEW)
    db.record_application("Alan", "ML Intern", status=STATUS_APPLIED)
    assert db.get_job(job_id)["status"] == STATUS_INTERVIEW
    db.record_application("Alan", "ML Intern", status=STATUS_REJECTED)
    job = db.get_job(job_id)
    assert job["status"] == STATUS_REJECTED
    assert job["rejection_reason"] == REFUSAL_REASON
    db.record_application("Alan", "ML Intern", status=STATUS_INTERVIEW)
    assert db.get_job(job_id)["status"] == STATUS_REJECTED


def test_une_exclusion_metier_n_empeche_pas_de_postuler(db: Database) -> None:
    db.upsert_job({"title": "Stage IA", "company": "CEA", "url": "https://x/2", "status": STATUS_NEW})
    job_id = db.find_application("CEA", "Stage IA")["id"]
    db.reject_job(job_id, "orientation BI")
    db.record_application("CEA", "Stage IA")
    assert db.get_job(job_id)["status"] == STATUS_APPLIED


def test_identifiant_externe_rend_l_import_idempotent(db: Database) -> None:
    first, created = db.record_application("Qantev", "AI Engineer Intern", source=SOURCE_GMAIL, external_id="thread-1")
    second, created_again = db.record_application(
        "Qantev", "AI Engineer Intern (2027)", status=STATUS_INTERVIEW, source=SOURCE_GMAIL, external_id="thread-1"
    )
    assert created and not created_again
    assert first == second
    assert db.get_job(first)["status"] == STATUS_INTERVIEW
    assert db.count_jobs() == 1


def test_champs_obligatoires_et_statut_valide(db: Database) -> None:
    with pytest.raises(ValueError):
        db.record_application("", "Stage")
    with pytest.raises(ValueError):
        db.record_application("Dataiku", "Stage", status=STATUS_NEW)


def test_cli_apercu_puis_ecriture(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rows = [
        {"company": "Ekimetrics", "title": "Stage AI/ML Engineer", "status": "postule", "date": "2026-09-27",
         "source": "gmail", "external_id": "t-1"},
        {"company": "ChapsVision", "title": "Stage DevOps", "status": "refus", "date": "2026-09-29", "source": "gmail"},
        {"company": "Oups", "title": "Stage", "status": "inconnu"},
    ]
    source = tmp_path / "candidatures.json"
    source.write_text(json.dumps(rows), encoding="utf-8")
    db_path = tmp_path / "cli.db"

    assert import_applications.main([str(source), "--db", str(db_path)]) == 1
    assert Database(db_path).count_jobs() == 0
    assert "Aperçu seulement" in capsys.readouterr().out

    assert import_applications.main([str(source), "--db", str(db_path), "--apply"]) == 1
    database = Database(db_path)
    assert database.count_jobs() == 2
    refused = database.find_application("ChapsVision", "Stage DevOps")
    assert refused["status"] == STATUS_REJECTED
    assert refused["applied_at"] == datetime(2026, 9, 29)
    assert "2 créée(s)" in capsys.readouterr().out


def test_auto_import_ne_retrograde_jamais_un_statut(tmp_path: Path) -> None:
    from tools import auto_import

    imports, db_path, backups = tmp_path / "imports", tmp_path / "auto.db", tmp_path / "bk"
    imports.mkdir()
    db = Database(db_path)
    interview_id, _ = db.record_application("Alan", "ML Intern", status=STATUS_INTERVIEW, external_id="t-9")
    refused_id, _ = db.record_application("Qonto", "Data Intern", status=STATUS_REJECTED)
    rows = [
        {"company": "Alan", "title": "ML Intern", "status": "postule", "external_id": "t-9"},
        {"company": "Qonto", "title": "Data Intern", "status": "postule"},
        {"company": "Qonto", "title": "Data Intern", "status": "entretien"},
    ]
    (imports / "candidatures_gmail_2026-09-29.json").write_text(json.dumps(rows), encoding="utf-8")
    args = ["--imports-dir", str(imports), "--db", str(db_path), "--backup-dir", str(backups)]

    assert auto_import.main(args) == 0
    db = Database(db_path)
    assert db.get_job(interview_id)["status"] == STATUS_INTERVIEW
    assert db.get_job(refused_id)["status"] == STATUS_REJECTED
    assert len(list(backups.glob("auto_*.db"))) == 1
    assert (imports / "processed.txt").read_text(encoding="utf-8").split() == ["candidatures_gmail_2026-09-29.json"]
    assert "refus importés (0)" in (imports / "import_log.txt").read_text(encoding="utf-8")

    assert auto_import.main(args) == 0  # déjà au registre : rien à refaire
    assert len(list(backups.glob("auto_*.db"))) == 1


def test_auto_import_erreur_code_non_nul_et_fichier_non_inscrit(tmp_path: Path) -> None:
    from tools import auto_import

    imports = tmp_path / "imports"
    imports.mkdir()
    (imports / "candidatures_gmail_2026-10-06.json").write_text(
        json.dumps([{"company": "Oups", "title": "Stage", "status": "inconnu"}]), encoding="utf-8"
    )
    args = ["--imports-dir", str(imports), "--db", str(tmp_path / "e.db"), "--backup-dir", str(tmp_path / "bk")]
    assert auto_import.main(args) == 1
    assert not (imports / "processed.txt").exists()
