"""Republications : détectées à l'ingestion, rangées dans l'existant sans toucher aux décisions d'Eddy."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scrapers.models import RawJob  # noqa: E402
from src.constants import (  # noqa: E402
    CLOSED_OFFER_REASON,
    DUPLICATE_REASON,
    STATUS_APPLIED,
    STATUS_IGNORED,
    STATUS_NEW,
)
from src.ingestion.bridge import description_similarity, ingest_raw_jobs, is_repost  # noqa: E402
from src.storage.database import Database  # noqa: E402
from tools import dedupe_reposts as dd  # noqa: E402

TEXT = ("Au sein de l'équipe R&D, vous développerez des modèles de deep learning pour la "
        "vision par ordinateur, en PyTorch, sur des données industrielles réelles. ") * 6
OTHER = ("Mission de reporting Power BI pour la direction financière, tableaux de bord et "
         "automatisation Excel, en lien avec les équipes commerciales. ") * 6


def _raw(job_id: str, description: str = TEXT, title: str = "Stage Data Scientist H/F") -> RawJob:
    return RawJob(
        id_externe=job_id, source="linkedin", title=title, company="Acme SAS",
        location="Paris", url=f"https://www.linkedin.com/jobs/view/{job_id}", description=description,
    )


def test_description_similarity_bounds() -> None:
    assert description_similarity(TEXT, TEXT) == 1.0
    assert description_similarity(TEXT, "") == 0.0
    assert description_similarity(TEXT, OTHER) < 0.5
    assert is_repost("Acme", "Stage DS", TEXT, "ACME SAS", "stage ds", TEXT + " Postulez !")
    assert not is_repost("Acme", "Stage DS", TEXT, "Acme", "Stage DS", OTHER)


def test_ingestion_skips_same_platform_repost(tmp_path: Path) -> None:
    db = Database(str(tmp_path / "b.db"))
    assert ingest_raw_jobs([_raw("1")], db)["new_inserted"] == 1
    stats = ingest_raw_jobs([_raw("2"), _raw("3", description=OTHER)], db)
    # « 2 » est une republication de « 1 » ; « 3 » a le même titre mais un autre texte.
    assert stats["new_inserted"] == 1 and stats["duplicates_skipped"] == 1


def test_ingestion_keeps_repost_of_closed_offer(tmp_path: Path) -> None:
    db = Database(str(tmp_path / "b.db"))
    ingest_raw_jobs([_raw("1")], db)
    first = db.get_jobs(limit=10)[0]
    db.update_status(first["id"], STATUS_IGNORED, rejection_reason=CLOSED_OFFER_REASON)
    assert ingest_raw_jobs([_raw("2")], db)["new_inserted"] == 1


def _seed(db: Database) -> dict[str, str]:
    ingest_raw_jobs([_raw("a")], db)
    ids = {"a": db.get_jobs(limit=10)[0]["id"]}
    for key, desc in (("b", TEXT + " Bis."), ("c", OTHER)):
        db.upsert_job({
            "title": "Stage Data Scientist H/F", "company": "Acme", "location": "Paris",
            "url": f"https://www.linkedin.com/jobs/view/{key}", "description": desc,
            "source": "linkedin", "status": STATUS_NEW,
        })
    for job in db.get_jobs(limit=10):
        ids[job["url"].rsplit("/", 1)[-1]] = job["id"]
    return ids


def test_dedupe_existing_prefers_eddy_decision(tmp_path: Path) -> None:
    db_path = tmp_path / "b.db"
    db = Database(str(db_path))
    ids = _seed(db)
    db.update_status(ids["b"], STATUS_APPLIED)  # Eddy a postulé à la republication

    assert dd.run(False, db_path=db_path) == 1  # essai à blanc : rien d'écrit
    assert Database(str(db_path)).get_job(ids["a"])["status"] == STATUS_NEW

    assert dd.run(True, db_path=db_path) == 1
    db = Database(str(db_path))
    assert db.get_job(ids["b"])["status"] == STATUS_APPLIED
    a = db.get_job(ids["a"])
    assert a["status"] == STATUS_IGNORED and a["rejection_reason"] == DUPLICATE_REASON
    assert ids["b"] in a["user_notes"]
    assert db.get_job(ids["c"])["status"] == STATUS_NEW  # autre texte : pas un doublon
    assert list((tmp_path / "backups").glob("*.db"))


def test_dedupe_existing_never_hides_live_repost_of_closed(tmp_path: Path) -> None:
    db_path = tmp_path / "b.db"
    db = Database(str(db_path))
    ids = _seed(db)
    db.update_status(ids["a"], STATUS_IGNORED, rejection_reason=CLOSED_OFFER_REASON)
    dd.run(True, db_path=db_path)
    db = Database(str(db_path))
    statuses = {db.get_job(ids[k])["status"] for k in ("a", "b")}
    # « b » (vivante) reste à trier, « a » reste fermée.
    assert db.get_job(ids["b"])["status"] == STATUS_NEW and STATUS_IGNORED in statuses
