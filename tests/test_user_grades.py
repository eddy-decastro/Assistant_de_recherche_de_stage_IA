"""N6 : Eddy corrige la note de l'IA (« Safran : j'aurais mis B avec 70 car … »).

Stockage (colonnes utilisateur ``jobs.user_v4_*``), protection contre les
recalculs, injection dans le prompt v4, sérialisation et évaluation IA / Eddy.
Bases temporaires (tmp_path) et LLM simulé uniquement : aucune écriture dans
data/stage_copilot.db ni data/jobs.db, aucun appel réseau.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = Path(__file__).resolve().parent
for path in (PROJECT_ROOT, TESTS_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from components.job_feed.serialize import serialize_job  # noqa: E402
from src.constants import V4_CLASS_LABELS, V4_CLASS_SHORT_LABELS, V4_CLASSES  # noqa: E402
from src.matching.scoring_v4 import (  # noqa: E402
    FEEDBACK_HEADER,
    USER_GRADES_HEADER,
    LLMJudgeV4,
    build_user_grades_block,
    has_user_grade,
    user_grade_reason,
)
from src.storage.database import USER_GRADE_COLUMNS, Database  # noqa: E402
from test_scoring_v4 import _sample_job, _valid_llm_payload  # noqa: E402
from tools import eval_feedback  # noqa: E402


def _db(tmp_path: Path, name: str = "n6.db") -> Database:
    return Database(tmp_path / name)


def _add(db: Database, job_id: str, company: str = "Safran", title: str = "Stage IA", **extra: Any) -> None:
    db.upsert_job(
        {
            "id": job_id,
            "title": title,
            "company": company,
            "url": f"https://example.com/{job_id}",
            "description": "Stage de 6 mois en R&D IA.",
            **extra,
        }
    )


# --------------------------------------------------------------------------- #
# Constantes UI
# --------------------------------------------------------------------------- #
def test_libelles_courts_de_classe_coherents() -> None:
    assert V4_CLASS_SHORT_LABELS == {
        "A": "Recherche",
        "B": "IA / R&D entreprise",
        "C": "Domaine voisin",
        "D": "ESN / Conseil",
        "E": "Hors cible",
    }
    assert set(V4_CLASS_SHORT_LABELS) == set(V4_CLASS_LABELS) == set(V4_CLASSES)
    for cls in V4_CLASSES:
        assert len(V4_CLASS_SHORT_LABELS[cls]) <= len(V4_CLASS_LABELS[cls])


# --------------------------------------------------------------------------- #
# Stockage et migration
# --------------------------------------------------------------------------- #
def test_migration_idempotente_sur_ancienne_base(tmp_path: Path) -> None:
    path = tmp_path / "ancienne.db"
    db = Database(path)
    _add(db, "a")
    db.engine.dispose()
    conn = sqlite3.connect(path)
    for col in USER_GRADE_COLUMNS:  # simule une base d'avant N6
        conn.execute(f"ALTER TABLE jobs DROP COLUMN {col}")
    conn.commit()
    conn.close()

    for _ in range(2):  # deux ouvertures : la migration ne casse pas la seconde fois
        db = Database(path)
        db.engine.dispose()
    conn = sqlite3.connect(path)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    conn.close()
    assert set(USER_GRADE_COLUMNS) <= cols
    db = Database(path)
    try:
        assert db.get_job("a")["user_v4_class"] is None
    finally:
        db.engine.dispose()


def test_set_user_grade_enregistre_et_efface(tmp_path: Path) -> None:
    db = _db(tmp_path)
    try:
        _add(db, "safran", status="POSTULÉ", v4_class="C", v4_display_score=55.0)
        assert db.set_user_grade("safran", "b", "70", "car vraie R&D, équipe ML") is True
        job = db.get_job("safran")
        assert job["user_v4_class"] == "B"
        assert job["user_v4_score"] == 70
        assert job["user_v4_comment"] == "car vraie R&D, équipe ML"
        assert isinstance(job["user_v4_at"], datetime)
        # Ni le statut ni la note de l'IA ne bougent.
        assert job["status"] == "POSTULÉ"
        assert job["v4_class"] == "C" and job["v4_display_score"] == 55.0

        assert db.set_user_grade("safran", "", 72.6, None)
        job = db.get_job("safran")
        assert job["user_v4_class"] is None and job["user_v4_score"] == 73
        assert job["user_v4_comment"] is None

        assert db.set_user_grade("safran", None, None, "  ")  # tout vide = effacer
        job = db.get_job("safran")
        assert all(job[c] is None for c in USER_GRADE_COLUMNS)
        assert db.get_user_grades() == []

        db.set_user_grade("safran", "A", None, "x" * 900)
        assert len(db.get_job("safran")["user_v4_comment"]) == 500
        assert db.set_user_grade("absente", "B", 70, "") is False
    finally:
        db.engine.dispose()


@pytest.mark.parametrize(
    ("cls", "score"),
    [("F", 70), ("BB", None), ("B", 101), ("B", -1), ("B", "beaucoup"), ("B", True), ("B", float("nan"))],
)
def test_set_user_grade_valeurs_invalides(tmp_path: Path, cls: Any, score: Any) -> None:
    db = _db(tmp_path)
    try:
        _add(db, "a")
        with pytest.raises(ValueError):
            db.set_user_grade("a", cls, score, "car …")
        assert db.get_job("a")["user_v4_at"] is None
    finally:
        db.engine.dispose()


def test_get_user_grades_plus_recentes_d_abord(tmp_path: Path) -> None:
    db = _db(tmp_path)
    try:
        for job_id in ("a", "b", "c"):
            _add(db, job_id, v4_class="D", v4_display_score=40.0)
        db.set_user_grade("a", "B", 70, "premier")
        db.set_user_grade("b", "A", None, "")
        db.set_user_grade("c", "C", 50, "dernier")
        with db.engine.begin() as conn:  # horodatages distincts, indépendants de l'horloge
            for job_id, day in (("a", 1), ("b", 2), ("c", 3)):
                conn.exec_driver_sql(
                    "UPDATE jobs SET user_v4_at = ? WHERE id = ?", (f"2026-11-0{day} 10:00:00", job_id)
                )
        grades = db.get_user_grades()
        assert [g["id"] for g in grades] == ["c", "b", "a"]
        assert grades[0]["v4_class"] == "D" and grades[0]["v4_display_score"] == 40.0
        assert set(grades[0]) >= {"title", "company", "user_v4_class", "user_v4_score", "user_v4_comment", "user_v4_at"}
    finally:
        db.engine.dispose()


def test_aucun_recalcul_ni_upsert_n_ecrase_la_correction(tmp_path: Path) -> None:
    db = _db(tmp_path)
    try:
        job = _sample_job()
        db.upsert_job(job)
        db.set_user_grade(job["id"], "B", 70, "car R&D")
        before = {c: db.get_job(job["id"])[c] for c in USER_GRADE_COLUMNS}

        # Re-collecte de la même offre par un scraper (dict brut, même avec des clés user_*).
        db.upsert_job({**job, "title": job["title"] + " (MAJ)", "user_v4_class": "E", "user_v4_score": 0})
        # Notation v4 puis recalcul déterministe.
        db.update_v4(job["id"], {"v4_raw_json": _valid_llm_payload(), "v4_class": "C", "v4_display_score": 60.0})
        assert db.recompute_scores_v4() == 1
        db.clear_rerank_scores()

        after = db.get_job(job["id"])
        assert after["title"].endswith("(MAJ)")
        assert {c: after[c] for c in USER_GRADE_COLUMNS} == before
    finally:
        db.engine.dispose()


# --------------------------------------------------------------------------- #
# Bloc « CORRECTIONS D'EDDY » dans le prompt v4
# --------------------------------------------------------------------------- #
def _grade(job_id: str, at: str, **kw: Any) -> dict[str, Any]:
    base = {
        "id": job_id,
        "title": f"Stage {job_id}",
        "company": "Safran",
        "v4_class": "C",
        "v4_display_score": 55.0,
        "user_v4_class": "B",
        "user_v4_score": 70,
        "user_v4_comment": "car vraie équipe R&D",
        "user_v4_at": at,
    }
    base.update(kw)
    return base


def test_bloc_corrections_contenu_ordre_et_bornes() -> None:
    grades = [
        _grade("vieux", "2026-10-01T10:00:00"),
        _grade("recent", "2026-11-04T10:00:00", company="Thales", user_v4_comment="Parce que défense trop loin"),
        _grade("sans_note", "2026-11-02T10:00:00", user_v4_class=None, user_v4_score=None,
               v4_class=None, v4_display_score=None, user_v4_comment="trop d'admin"),
        _grade("vide", "2026-11-03T10:00:00", user_v4_class=None, user_v4_score=None, user_v4_comment=""),
    ]
    block = build_user_grades_block(grades)
    lines = block.splitlines()
    assert lines[0].startswith(USER_GRADES_HEADER)
    assert "PRIORITAIRES" in lines[0] and "même entreprise" in lines[0]
    assert "ne les recopie pas aveuglément" in lines[0]
    assert lines[1] == "- Thales — Stage recent : IA C 55 → Eddy B 70, car « défense trop loin »"
    assert lines[2] == "- Safran — Stage sans_note : IA non notée → Eddy pas de note précise, car « trop d'admin »"
    assert lines[3] == "- Safran — Stage vieux : IA C 55 → Eddy B 70, car « vraie équipe R&D »"
    assert len(lines) == 4  # la correction vide est ignorée
    assert build_user_grades_block(list(reversed(grades))) == block  # déterministe

    many = [_grade(f"j{i:02d}", f"2026-11-{i + 1:02d}T00:00:00") for i in range(25)]
    assert len(build_user_grades_block(many).splitlines()) == 1 + 10
    assert len(build_user_grades_block(many, max_items=3).splitlines()) == 1 + 3
    bounded = build_user_grades_block(many, max_chars=600)
    assert len(bounded) <= 600 and 1 <= bounded.count("\n") < 10
    assert build_user_grades_block([]) == ""
    assert build_user_grades_block(many, max_items=0) == ""


def test_user_grade_reason_et_has_user_grade() -> None:
    assert user_grade_reason("car  vraie R&D") == "vraie R&D"
    assert user_grade_reason("Parce qu'il y a une thèse") == "il y a une thèse"
    assert user_grade_reason("carte de visite") == "carte de visite"
    assert has_user_grade({"user_v4_score": 0})
    assert not has_user_grade({"user_v4_class": "", "user_v4_comment": " "})


def _judge_cfg(**ug: Any) -> dict[str, Any]:
    return {"scoring_v4": {"feedback": {"enabled": True}, "user_grades": ug}, "llm": {}}


def test_prompt_v4_injecte_les_corrections_avant_les_decisions(tmp_path: Path) -> None:
    db = _db(tmp_path)
    try:
        _add(db, "safran", title="Stage Deep Learning", status="POSTULÉ", v4_class="C", v4_display_score=55.0)
        _add(db, "ign", company="Capgemini", status="NOUVEAU")
        db.record_ignore("ign", "esn_conseil", "ESN")
        db.set_user_grade("safran", "B", 70, "car pôle R&D IA")

        judge = LLMJudgeV4(config=_judge_cfg(), api_key="", groq_api_key="", deepseek_api_key="", db=db)
        prompt = judge.system_prompt
        assert "- Safran — Stage Deep Learning : IA C 55 → Eddy B 70, car « pôle R&D IA »" in prompt
        assert prompt.index(USER_GRADES_HEADER) < prompt.index(FEEDBACK_HEADER)
        assert "RETOURS D'EDDY" in prompt  # consigne permanente du prompt_v4.txt

        off = LLMJudgeV4(
            config=_judge_cfg(enabled=False), api_key="", groq_api_key="", deepseek_api_key="", db=db
        )
        assert USER_GRADES_HEADER not in off.system_prompt
        assert FEEDBACK_HEADER in off.system_prompt  # les décisions restent
        assert USER_GRADES_HEADER not in LLMJudgeV4(
            config=_judge_cfg(), api_key="", groq_api_key="", deepseek_api_key=""
        ).system_prompt  # sans base, rien n'est lu
    finally:
        db.engine.dispose()


def test_bloc_corrections_ne_casse_pas_la_notation(tmp_path: Path) -> None:
    class BrokenDb:
        def get_user_grades(self) -> list[dict[str, Any]]:
            raise RuntimeError("base verrouillée")

        def get_feedback_jobs(self) -> list[dict[str, Any]]:
            return []

    judge = LLMJudgeV4(config=_judge_cfg(), api_key="", groq_api_key="", deepseek_api_key="", db=BrokenDb())
    assert judge.feedback_block == ""


# --------------------------------------------------------------------------- #
# Sérialisation et évaluation
# --------------------------------------------------------------------------- #
def test_serialize_job_expose_la_correction(tmp_path: Path) -> None:
    db = _db(tmp_path)
    try:
        _add(db, "a")
        plain = serialize_job(db.get_job("a"), [])
        assert plain["user_v4_class"] == "" and plain["user_v4_score"] is None
        assert plain["user_v4_comment"] == "" and plain["user_v4_at"] == ""
        db.set_user_grade("a", "B", 70, "car R&D")
        out = serialize_job(db.get_job("a"), [])
        assert out["user_v4_class"] == "B"
        assert out["user_v4_score"] == 70
        assert out["user_v4_comment"] == "car R&D"
        assert datetime.fromisoformat(out["user_v4_at"])
    finally:
        db.engine.dispose()


def test_eval_feedback_ecart_ia_eddy(tmp_path: Path) -> None:
    path = tmp_path / "eval.db"
    db = Database(path)
    try:
        _add(db, "a", v4_class="C", v4_display_score=55.0)  # NOUVEAU mais corrigée
        _add(db, "b", v4_class="B", v4_display_score=80.0, status="POSTULÉ")
        _add(db, "c", v4_class="D", v4_display_score=30.0)
        db.set_user_grade("a", "B", 70, "car R&D")  # classe fausse, IA -15
        db.set_user_grade("b", "B", 70, "")  # classe juste, IA +10
        db.set_user_grade("c", None, None, "car ESN")  # commentaire seul
    finally:
        db.engine.dispose()

    result, report = eval_feedback.run(path)
    grades = result["user_grades"]
    assert grades["count"] == 3
    assert grades["class_compared"] == 2 and grades["class_exact"] == 0.5
    assert grades["score_compared"] == 2
    assert grades["score_mae"] == 12.5
    assert grades["score_bias"] == -2.5
    assert [j["id"] for j in grades["items"]] == ["a", "b", "c"]
    assert "Classe exacte : 50 %" in report
    assert "Écart absolu moyen de note : 12,5" in report
    assert "IA C 55 → Eddy B 70 car « R&D »" in report
    assert result["applied"] == 1  # la NOUVEAU corrigée ne compte pas comme décision
