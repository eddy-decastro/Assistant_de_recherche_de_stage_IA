"""Pont Antigravity : export en lecture seule, ingestion sans toucher aux décisions d'Eddy."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.constants import STATUS_APPLIED, STATUS_EXCLUDED, STATUS_IGNORED, STATUS_NEW  # noqa: E402
from src.storage.database import Database  # noqa: E402
from tests.test_scoring_v4 import _sample_job, _valid_llm_payload  # noqa: E402
from tools import antigravity_v4 as ag  # noqa: E402


def _job(job_id: str, status: str, reason: str | None = None) -> dict:
    job = _sample_job()
    job.update({"id": job_id, "url": f"https://exemple.fr/{job_id}", "status": status,
                "rejection_reason": reason})
    return job


def _rows(db_path: Path) -> list:
    import sqlite3

    con = sqlite3.connect(db_path)
    try:
        return con.execute("SELECT * FROM jobs ORDER BY id").fetchall()
    finally:
        con.close()


def _setup(tmp_path: Path) -> Path:
    db_path = tmp_path / "base.db"
    db = Database(str(db_path))
    db.upsert_job(_job("nouveau", STATUS_NEW))
    db.upsert_job(_job("exclu", STATUS_EXCLUDED))
    db.upsert_job(_job("archive", STATUS_IGNORED, "auto_archive"))
    db.upsert_job(_job("archive_ferme", STATUS_IGNORED, "auto_archive"))
    db.upsert_job(_job("postule", STATUS_APPLIED))
    db.upsert_job(_job("ignore_main", STATUS_IGNORED, "lieu"))
    return db_path


def test_export_skips_eddy_decisions(tmp_path: Path) -> None:
    db_path = _setup(tmp_path)
    before = _rows(db_path)
    work = tmp_path / "ag"
    n = ag.export(10, db_path=db_path, out_dir=work)
    assert n == 4
    ids = {r["id"] for r in json.loads((work / "in" / "lot_001.json").read_text(encoding="utf-8"))}
    assert ids == {"nouveau", "exclu", "archive", "archive_ferme"}
    assert (work / "prompt_systeme_v4.txt").read_text(encoding="utf-8").strip()
    assert _rows(db_path) == before


def test_ingest_grades_and_applies_liveness(tmp_path: Path) -> None:
    db_path = _setup(tmp_path)
    work = tmp_path / "ag"
    (work / "out").mkdir(parents=True)
    ok = {"evaluation": _valid_llm_payload(), "vivacite": {"etat": "ouverte", "verifie": True}}
    results = {
        "exclu": ok,
        "archive": ok,
        "archive_ferme": {"evaluation": _valid_llm_payload(),
                          "vivacite": {"etat": "fermee", "verifie": True, "preuve": "404"}},
        "nouveau": {"evaluation": {"class": "Z"}},  # invalide : sauté
        "postule": ok,  # décision d'Eddy : jamais touchée
    }
    (work / "out" / "lot_001_out.json").write_text(json.dumps(results), encoding="utf-8")
    (work / "out" / "nouvelles_offres.json").write_text(json.dumps([
        {"title": "Stage ML", "company": "Safran", "url": "https://safran.example/1", "description": "x"},
        {"title": "Doublon", "company": "X", "url": "https://exemple.fr/nouveau"},
    ]), encoding="utf-8")

    stats = ag.ingest(False, db_path=db_path, work_dir=work)

    db = Database(str(db_path))
    assert stats["notees"] == 3 and stats["invalides"] == 1 and stats["ignorees_decision_eddy"] == 1
    assert db.get_job("exclu")["status"] == STATUS_NEW  # la v4 ne l'exclut plus
    assert db.get_job("archive")["status"] == STATUS_NEW  # ouverte : remise à trier
    closed = db.get_job("archive_ferme")
    assert closed["status"] == STATUS_IGNORED and closed["rejection_reason"] == "offre_fermee"
    assert db.get_job("postule")["status"] == STATUS_APPLIED
    assert not db.get_job("postule").get("v4_model")
    assert stats["nouvelles_offres"] == 1
    assert list((tmp_path / "backups").glob("stage_copilot_avant_antigravity_*.db"))


def test_export_refuses_when_lots_not_ingested(tmp_path: Path) -> None:
    import pytest

    db_path = _setup(tmp_path)
    work = tmp_path / "ag"
    (work / "out").mkdir(parents=True)
    (work / "out" / "lot_001_out.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ag.PendingLotsError):
        ag.export(10, db_path=db_path, out_dir=work)
    assert ag.export(10, db_path=db_path, out_dir=work, force=True) == 4


def test_real_ingest_marks_lots_as_ingested(tmp_path: Path) -> None:
    db_path = _setup(tmp_path)
    work = tmp_path / "ag"
    (work / "out").mkdir(parents=True)
    (work / "out" / "lot_001_out.json").write_text(
        json.dumps({"nouveau": {"evaluation": _valid_llm_payload()}}), encoding="utf-8"
    )
    ag.ingest(True, db_path=db_path, work_dir=work)  # essai à blanc : ne marque rien
    assert ag.pending_lots(work / "out")
    ag.ingest(False, db_path=db_path, work_dir=work)
    assert ag.pending_lots(work / "out") == []
    assert ag.export(10, db_path=db_path, out_dir=work) == 4


def test_export_stale_only_skips_current_grid(tmp_path: Path) -> None:
    db_path = _setup(tmp_path)
    work = tmp_path / "ag"
    (work / "out").mkdir(parents=True)
    (work / "out" / "lot_001_out.json").write_text(
        json.dumps({"nouveau": {"evaluation": _valid_llm_payload()}}), encoding="utf-8"
    )
    ag.ingest(False, db_path=db_path, work_dir=work)
    job = Database(str(db_path)).get_job("nouveau")
    assert job["v4_grid_version"] == ag.get_grid_version_v4()
    n = ag.export(10, db_path=db_path, out_dir=work, stale_only=True)
    assert n == 3
    ids = {r["id"] for r in json.loads((work / "in" / "lot_001.json").read_text(encoding="utf-8"))}
    assert "nouveau" not in ids


def test_grid_version_ignores_calibration_block() -> None:
    from src.matching.scoring_v4 import get_grid_version_v4

    assert len(get_grid_version_v4()) == 12
    assert get_grid_version_v4() == get_grid_version_v4()


def test_export_verify_only_old_offers_and_ingest_liveness_alone(tmp_path: Path) -> None:
    from datetime import datetime, timedelta

    db_path = tmp_path / "base.db"
    db = Database(str(db_path))
    old = _job("vieille", STATUS_NEW)
    old["published_at"] = datetime.now() - timedelta(days=20)
    recent = _job("recente", STATUS_NEW)
    recent["published_at"] = datetime.now() - timedelta(days=2)
    db.upsert_job(old)
    db.upsert_job(recent)
    work = tmp_path / "ag"
    (work / "out").mkdir(parents=True)
    # Les deux offres sont notées avec la grille actuelle.
    (work / "out" / "lot_001_out.json").write_text(json.dumps({
        "vieille": {"evaluation": _valid_llm_payload()},
        "recente": {"evaluation": _valid_llm_payload()},
    }), encoding="utf-8")
    ag.ingest(False, db_path=db_path, work_dir=work)

    n = ag.export(10, db_path=db_path, out_dir=work, stale_only=True, verify_older_than_days=7)
    lot = json.loads((work / "in" / "lot_001.json").read_text(encoding="utf-8"))
    assert n == 1 and lot[0]["id"] == "vieille" and lot[0]["mission"] == "verifier"

    score_before = Database(str(db_path)).get_job("vieille")["v4_display_score"]
    for p in (work / "out").glob("lot_*_out.json"):
        p.unlink()
    (work / "out" / "lot_001_out.json").write_text(json.dumps({
        "vieille": {"vivacite": {"etat": "fermee", "verifie": True, "preuve": "No longer accepting applications"}},
    }), encoding="utf-8")
    stats = ag.ingest(False, db_path=db_path, work_dir=work)
    job = Database(str(db_path)).get_job("vieille")
    assert stats["verifiees_seulement"] == 1 and stats["fermees"] == 1
    assert job["status"] == STATUS_IGNORED and job["v4_display_score"] == score_before


def test_export_verify_ids_from_code_check(tmp_path: Path) -> None:
    """--verifier-ids : seules les offres non tranchées par le code partent en « vérifier »."""
    db_path = tmp_path / "base.db"
    db = Database(str(db_path))
    for name in ("inconnue", "ouverte"):
        db.upsert_job(_job(name, STATUS_NEW))
    work = tmp_path / "ag"
    (work / "out").mkdir(parents=True)
    (work / "out" / "lot_001_out.json").write_text(json.dumps({
        "inconnue": {"evaluation": _valid_llm_payload()},
        "ouverte": {"evaluation": _valid_llm_payload()},
    }), encoding="utf-8")
    ag.ingest(False, db_path=db_path, work_dir=work)

    n = ag.export(10, db_path=db_path, out_dir=work, stale_only=True, verify_ids={"inconnue", "absente"})
    lot = json.loads((work / "in" / "lot_001.json").read_text(encoding="utf-8"))
    assert n == 1 and lot[0]["id"] == "inconnue" and lot[0]["mission"] == "verifier"
