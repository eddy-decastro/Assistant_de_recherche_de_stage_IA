"""Tests de tools/eval_feedback.py sur une base factice (tmp_path), en lecture seule."""
from __future__ import annotations

import hashlib
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.storage.database import Database  # noqa: E402
from tools import eval_feedback  # noqa: E402


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_fake_db(path: Path) -> None:
    db = Database(path)
    try:
        rows = [
            ("p1", "POSTULÉ", None, 90.0, 60.0),
            ("p2", "ENTRETIEN", None, 80.0, 70.0),
            ("p3", "REJETÉ", "Refus de l'entreprise", 70.0, 50.0),
            ("i1", "IGNORÉ", "pas_de_vrai_ml", 85.0, 40.0),
            ("i2", "IGNORÉ", "lieu", 30.0, 80.0),
            ("i3", "IGNORÉ", None, 20.0, 30.0),
            ("auto", "IGNORÉ", "auto_archive", 99.0, 99.0),
            ("ferme", "IGNORÉ", "offre_fermee", 99.0, 99.0),
            ("neuf", "NOUVEAU", None, 50.0, 50.0),
        ]
        for job_id, status, reason, v4, v3 in rows:
            db.upsert_job(
                {
                    "id": job_id,
                    "title": f"Stage {job_id}",
                    "company": f"Société {job_id}",
                    "url": f"https://example.com/{job_id}",
                    "status": status,
                    "rejection_reason": reason,
                    "v4_class": "B",
                    "v4_display_score": v4,
                    "rerank_score": v3,
                    "applied_at": datetime(2026, 10, 1) if status != "IGNORÉ" and status != "NOUVEAU" else None,
                }
            )
        db.record_ignore("i1", "pas_de_vrai_ml", "que du reporting")
    finally:
        db.engine.dispose()


def test_auc_basic() -> None:
    assert eval_feedback.auc([3.0, 2.0], [1.0]) == 1.0
    assert eval_feedback.auc([1.0], [1.0]) == 0.5
    assert eval_feedback.auc([], [1.0]) is None


def test_eval_feedback_on_fake_db_is_read_only(tmp_path: Path) -> None:
    db_path = tmp_path / "factice.db"
    _build_fake_db(db_path)
    before = _digest(db_path)

    result, report = eval_feedback.run(db_path, high_threshold=70)

    assert _digest(db_path) == before  # la base source n'est jamais modifiée
    assert result["applied"] == 3
    assert result["ignored"] == 3  # auto_archive et offre_fermee exclus
    # Positifs 90, 80, 70 contre ignorés 85, 30, 20 : 7 victoires sur 9
    assert abs(result["auc_v4"] - 7 / 9) < 1e-9
    # v3 : 60, 70, 50 contre 40, 80, 30 -> 6 victoires sur 9
    assert abs(result["auc_v3"] - 6 / 9) < 1e-9
    codes = {row["code"]: row for row in result["by_reason"]}
    assert codes["pas_de_vrai_ml"]["count"] == 1
    assert codes["pas_de_vrai_ml"]["label"] == "Pas de vrai ML"
    assert abs(codes["pas_de_vrai_ml"]["auc_v4"] - 1 / 3) < 1e-9  # seul 90 bat 85
    assert [j["id"] for j in result["high_ignored"]] == ["i1"]

    assert "AUC v4 : 0,78" in report
    assert "Pas de vrai ML" in report
    assert "« que du reporting »" in report
    assert "Stage auto" not in report


def test_eval_feedback_main_missing_db(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    code = eval_feedback.main(["--db", str(tmp_path / "absente.db")])
    assert code == 1
    assert "introuvable" in capsys.readouterr().err
