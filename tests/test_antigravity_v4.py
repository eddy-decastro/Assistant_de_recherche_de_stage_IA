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
