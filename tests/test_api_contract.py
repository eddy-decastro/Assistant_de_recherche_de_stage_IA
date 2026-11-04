"""Contrat de l'API pour le front : note unique, pagination, regroupement, dates,
onglet Exclues, raisons d'ignorer, correction de note, file de candidature,
santé des sources.

Bases dans ``tmp_path`` uniquement ; aucun appel LLM ni réseau (juge et lettres mockés).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from api.main import company_key, create_app  # noqa: E402
from src.constants import (  # noqa: E402
    IGNORE_REASONS,
    STATUS_APPLIED,
    STATUS_EXCLUDED,
    STATUS_IGNORED,
    STATUS_NEW,
    V4_CLASS_SHORT_LABELS,
    V4_FLAG_CONTRACT_TO_CHECK,
    V4_FLAG_EXCLUDED_PREFIX,
)
from src.storage.database import Database  # noqa: E402

NOW = datetime.now(timezone.utc).replace(tzinfo=None)
LETTER = "Madame, Monsieur,\n\nLettre de test pour la file.\n\nEddy"
SHORT = "Motivation courte de test. " * 40


def _v4(score: float, cls: str = "B", flags: list[str] | None = None) -> dict:
    stars = {"stars": 4, "reason": "ok", "quote": "PyTorch", "unknown": False}
    return {
        "v4_status": "OK",
        "v4_class": cls,
        "v4_stars_json": {k: dict(stars) for k in ("apprendre", "technique", "debouche", "logistique")},
        "v4_center": score,
        "v4_adjustment": 0,
        "v4_class_score": score,
        "v4_display_score": score,
        "v4_tags_json": [],
        "v4_flags_json": flags or [],
        "v4_raw_json": {"summary": "test", "contract_type": "STAGE", "contract_quote": ""},
        "v4_model": "mock",
        # Comme le juge v4 : NOUVEAU <-> EXCLU suit la décision d'exclusion.
        "excluded": any(f.startswith(V4_FLAG_EXCLUDED_PREFIX) for f in flags or []),
    }


def _job(job_id: str, company: str, **extra) -> dict:
    base = {
        "id": job_id,
        "title": f"Stage ML {job_id}",
        "company": company,
        "location": "Paris",
        "url": f"https://jobs.lever.co/{job_id}",
        "description": "Stage de recherche en apprentissage automatique sous PyTorch.",
        "source": "ats",
        "status": STATUS_NEW,
        "created_at": NOW - timedelta(days=1),
    }
    base.update(extra)
    return base


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(str(tmp_path / "contrat.db"))
    # Note v3 élevée mais v4 basse : le tri par défaut doit suivre la v4.
    database.upsert_job(_job("a_v3_haut", "HGH Infrared Systems SAS", rerank_score=95.0))
    database.update_v4("a_v3_haut", _v4(40.0, "C"))
    database.upsert_job(_job("b_v4_haut", "HGH INFRARED SYSTEMS", rerank_score=50.0))
    database.update_v4("b_v4_haut", _v4(90.0, "A"))
    database.upsert_job(_job("c_non_note", "Mistral AI", rerank_score=99.0))
    database.upsert_job(
        _job(
            "d_exclue",
            "UMA",
            published_at=NOW - timedelta(days=135),
            created_at=NOW - timedelta(days=24),
        )
    )
    database.update_v4("d_exclue", _v4(0.0, "E", [f"{V4_FLAG_EXCLUDED_PREFIX} CDI cité : « CDI »"]))
    database.upsert_job(_job("e_contrat", "Kering"))
    database.update_v4("e_contrat", _v4(70.0, "B", [V4_FLAG_CONTRACT_TO_CHECK]))
    return database


def _ids(resp) -> list[str]:
    return [j["id"] for j in resp.json()["jobs"]]


def test_default_sort_is_v4_and_unscored_last(db: Database) -> None:
    client = TestClient(create_app(db))
    data = client.get("/api/jobs").json()
    assert _ids_from(data) == ["b_v4_haut", "e_contrat", "a_v3_haut", "c_non_note"]
    first = data["jobs"][0]
    assert first["display_score"] == 90 and first["display_class"] == "A"
    assert first["score_state"] == "v4"
    last = data["jobs"][-1]
    assert last["display_score"] is None and last["score_state"] == "non_notee_v4"
    # « v3 » reste accepté (alias de la note v4) ; « v3_legacy » garde l'ancien ordre.
    assert _ids(client.get("/api/jobs?sort_by=v3")) == _ids_from(data)
    assert _ids(client.get("/api/jobs?sort_by=v3_legacy"))[:2] == ["c_non_note", "a_v3_haut"]


def _ids_from(data: dict) -> list[str]:
    return [j["id"] for j in data["jobs"]]


def test_pagination_offset_limit_has_more(db: Database) -> None:
    client = TestClient(create_app(db))
    full = _ids(client.get("/api/jobs"))
    page1 = client.get("/api/jobs?limit=2").json()
    assert page1["total"] == 4 and page1["has_more"] is True
    assert page1["offset"] == 0 and page1["limit"] == 2
    page2 = client.get("/api/jobs?limit=2&offset=2").json()
    assert page2["has_more"] is False
    assert _ids_from(page1) + _ids_from(page2) == full
    beyond = client.get("/api/jobs?limit=2&offset=10").json()
    assert beyond["jobs"] == [] and beyond["has_more"] is False and beyond["total"] == 4


def test_excluded_tab_and_contract_counter(db: Database) -> None:
    client = TestClient(create_app(db))
    main = client.get("/api/jobs?hide_processed=true").json()
    assert "d_exclue" not in _ids_from(main)
    assert main["excluded_count"] == 1
    assert main["contract_to_verify_count"] == 1
    contract = next(j for j in main["jobs"] if j["id"] == "e_contrat")
    assert contract["contract_to_verify"] is True

    excl = client.get("/api/jobs?only_excluded=true&hide_processed=true").json()
    assert _ids_from(excl) == ["d_exclue"]
    job = excl["jobs"][0]
    assert job["excluded"] is True and job["display_score"] is None
    assert "CDI" in job["exclusion_reason"]
    assert db.get_job("d_exclue")["status"] == STATUS_EXCLUDED

    stats = client.get("/api/stats").json()
    assert stats["excluded"] == 1 and stats["contract_to_verify"] == 1
    assert stats["unscored_v4"] == 1


def test_company_key_and_count(db: Database) -> None:
    assert company_key("HGH Infrared Systems SAS") == company_key("HGH INFRARED SYSTEMS")
    assert company_key("Société Générale") == "societe generale"
    assert company_key("") == ""
    assert company_key("Naval Group [FR]") == company_key("Naval Group") == "naval"
    client = TestClient(create_app(db))
    jobs = {j["id"]: j for j in client.get("/api/jobs").json()["jobs"]}
    assert jobs["a_v3_haut"]["company_key"] == jobs["b_v4_haut"]["company_key"] == "hgh infrared systems"
    assert jobs["a_v3_haut"]["company_count"] == 2
    assert jobs["c_non_note"]["company_count"] == 1
    # Filtre sur une entreprise (déplier un groupe).
    assert sorted(_ids(client.get("/api/jobs?company_key=HGH Infrared"))) == []
    assert sorted(_ids(client.get("/api/jobs?company_key=hgh infrared systems sas"))) == [
        "a_v3_haut",
        "b_v4_haut",
    ]


def test_publication_date_and_label(db: Database) -> None:
    client = TestClient(create_app(db))
    job = client.get("/api/jobs?only_excluded=true").json()["jobs"][0]
    assert job["date_estimee"] is False
    assert job["age_days"] == 135
    assert job["published_at"].startswith((NOW - timedelta(days=135)).date().isoformat())
    assert job["date_label"] == "Il y a 4 mois"  # cohérent avec age_days, pas avec la collecte (24 j)

    other = next(j for j in client.get("/api/jobs").json()["jobs"] if j["id"] == "b_v4_haut")
    assert other["date_estimee"] is True  # repli sur la date de collecte
    assert other["age_days"] == 1 and other["date_label"] == "Hier"
    assert other["created_at"] and "T" in other["created_at"]


def test_ignore_reasons_and_ignore_endpoint(db: Database) -> None:
    client = TestClient(create_app(db))
    reasons = client.get("/api/ignore-reasons").json()["reasons"]
    assert [r["key"] for r in reasons] == list(IGNORE_REASONS)
    assert all(r["label"] for r in reasons)
    assert client.get("/api/stats").json()["ignore_reasons"] == reasons

    r = client.post("/api/jobs/a_v3_haut/ignore", json={"reason": "esn_conseil", "comment": "boîte de conseil"})
    assert r.status_code == 200 and r.json()["ignore_reason_label"] == "ESN / conseil"
    job = db.get_job("a_v3_haut")
    assert job["status"] == STATUS_IGNORED and job["rejection_reason"] == "esn_conseil"
    assert job["rejection_comment"] == "boîte de conseil"
    listed = next(j for j in client.get("/api/jobs").json()["jobs"] if j["id"] == "a_v3_haut")
    assert listed["ignore_reason"] == "esn_conseil" and listed["ignore_comment"] == "boîte de conseil"

    assert client.post("/api/jobs/a_v3_haut/ignore", json={"reason": "nope"}).status_code == 400
    assert client.post("/api/jobs/inconnue/ignore", json={"reason": "lieu"}).status_code == 404


def test_bulk_status_with_reason(db: Database) -> None:
    client = TestClient(create_app(db))
    r = client.post(
        "/api/jobs/bulk-status",
        json={"ids": ["a_v3_haut", "b_v4_haut", "inconnue"], "status": STATUS_IGNORED, "reason": "doublon"},
    )
    assert r.status_code == 200 and r.json()["updated"] == 2
    assert db.get_job("b_v4_haut")["rejection_reason"] == "doublon"
    bad = client.post(
        "/api/jobs/bulk-status", json={"ids": ["c_non_note"], "status": STATUS_APPLIED, "reason": "doublon"}
    )
    assert bad.status_code == 400
    bad = client.post(
        "/api/jobs/bulk-status", json={"ids": ["c_non_note"], "status": STATUS_IGNORED, "reason": "xx"}
    )
    assert bad.status_code == 400
    assert db.get_job("c_non_note")["status"] == STATUS_NEW


def test_user_grade_endpoint(db: Database) -> None:
    client = TestClient(create_app(db))
    r = client.post("/api/jobs/a_v3_haut/user-grade", json={"cls": "b", "score": "72,5", "comment": "vrai ML"})
    assert r.status_code == 200
    body = r.json()
    assert body["user_v4_class"] == "B" and body["user_v4_score"] in (72, 73)
    assert body["user_v4_comment"] == "vrai ML" and body["user_v4_at"]
    listed = next(j for j in client.get("/api/jobs").json()["jobs"] if j["id"] == "a_v3_haut")
    assert listed["user_v4_class"] == "B" and listed["user_v4_comment"] == "vrai ML"
    grades = client.get("/api/user-grades").json()["grades"]
    assert [g["id"] for g in grades] == ["a_v3_haut"]

    assert client.post("/api/jobs/a_v3_haut/user-grade", json={"cls": "Z"}).status_code == 400
    assert client.post("/api/jobs/a_v3_haut/user-grade", json={"score": 150}).status_code == 400
    assert client.post("/api/jobs/inconnue/user-grade", json={"cls": "A"}).status_code == 404
    # Tout vide : la correction est effacée.
    cleared = client.post("/api/jobs/a_v3_haut/user-grade", json={"cls": None, "score": None, "comment": ""})
    assert cleared.status_code == 200 and cleared.json()["user_v4_at"] == ""
    # Le statut et la note de l'IA ne bougent pas.
    job = db.get_job("a_v3_haut")
    assert job["status"] == STATUS_NEW and job["v4_display_score"] == 40.0


def test_stats_labels(db: Database) -> None:
    stats = TestClient(create_app(db)).get("/api/stats").json()
    assert [c["short_label"] for c in stats["v4_classes"]] == list(V4_CLASS_SHORT_LABELS.values())
    sources = {s["key"]: s for s in stats["sources"]}
    assert sources["workday"]["label"] == "Grands groupes (Workday)" and sources["workday"]["count"] == 0
    assert sources["ats"]["count"] == 5
    assert stats["user_grade"]["score_max"] == 100


def test_job_detail(db: Database) -> None:
    client = TestClient(create_app(db))
    r = client.get("/api/jobs/b_v4_haut")
    assert r.status_code == 200 and r.json()["company_count"] == 2
    assert r.json()["application_report"] is None
    assert client.get("/api/jobs/inconnue").status_code == 404


def test_grade_v4_uses_db_and_saves_exclusions(db: Database) -> None:
    client = TestClient(create_app(db))
    built: list[dict] = []

    class FakeJudge:
        def __init__(self, config=None, db=None, **_):
            built.append({"db": db})

        def evaluate_single_job_v4(self, job):
            result = _v4(0.0, "E", [f"{V4_FLAG_EXCLUDED_PREFIX} CDI"])
            result["v4_status"] = "EXCLU"
            result["excluded"] = True
            return result

    with patch("api.main.LLMJudgeV4", FakeJudge):
        r = client.post("/api/jobs/grade-v4", json={"ids": ["c_non_note"]})
    assert r.status_code == 200
    assert built and built[0]["db"] is db
    assert r.json()["results"][0]["excluded"] is True
    job = db.get_job("c_non_note")
    assert job["v4_display_score"] == 0.0 and job["status"] == STATUS_EXCLUDED


@pytest.fixture
def no_llm():
    with patch("src.matching.cover_letter.CoverLetterGenerator.generate", return_value=LETTER), \
         patch("src.matching.cover_letter.CoverLetterGenerator._generate_short_llm_once", return_value=None), \
         patch("src.matching.cover_letter.CoverLetterGenerator.generate_short_motivation", return_value=SHORT):
        yield


def test_queue_endpoints(db: Database, no_llm) -> None:
    client = TestClient(create_app(db))
    assert client.get("/api/queue/current").json()["current"] is None
    assert client.get("/api/queue").json()["items"] == []

    r = client.post("/api/queue", json={"ids": ["b_v4_haut", "inconnue"]})
    assert r.status_code == 200, r.text
    assert r.json()["added"] == ["b_v4_haut"] and r.json()["missing"] == ["inconnue"]
    assert client.post("/api/queue", json={"ids": ["inconnue"]}).status_code == 404

    queue = client.get("/api/queue").json()
    assert queue["counts"]["pending"] == 1 and queue["items"][0]["job_id"] == "b_v4_haut"
    current = client.get("/api/queue/current").json()["current"]
    assert current["job_id"] == "b_v4_haut" and current["instructions"]
    assert client.get("/api/queue/b_v4_haut").json()["job_id"] == "b_v4_haut"
    assert client.get("/api/queue/inconnue").status_code == 404

    too_long = client.post("/api/queue/b_v4_haut/report", json={"outcome": "applied", "report": "x" * 400})
    assert too_long.status_code == 422
    assert client.post("/api/queue/inconnue/report", json={"outcome": "failed", "report": "Échec."}).status_code == 404

    report = "Postulé chez HGH via Lever. CV à joindre."
    done = client.post("/api/queue/b_v4_haut/report", json={"outcome": "applied", "report": report})
    assert done.status_code == 200 and done.json()["item"]["state"] == "applied"
    assert db.get_job("b_v4_haut")["status"] == STATUS_APPLIED
    # Le compte rendu revient avec l'offre (détail et liste).
    detail = client.get("/api/jobs/b_v4_haut").json()
    assert detail["application_report"]["report"] == report
    assert detail["application_report"]["state"] == "applied"
    listed = next(j for j in client.get("/api/jobs").json()["jobs"] if j["id"] == "b_v4_haut")
    assert listed["application_report"]["report"] == report
    assert client.get("/api/queue/current").json()["current"] is None

    assert client.delete("/api/queue/b_v4_haut").status_code == 200
    assert client.delete("/api/queue/b_v4_haut").status_code == 404


def test_sources_health(db: Database) -> None:
    client = TestClient(create_app(db))
    assert client.get("/api/sources/health").json() == {"run": None, "sources": []}
    run_id = db.start_run(["ats", "wttj"])
    db.record_query_stats(
        run_id,
        [
            {"source": "ats", "query": "lever:mort", "mode": "board", "stop_reason": "http_error",
             "stop_detail": "[http_404] HTTP 404", "http_requests": 1},
            {"source": "ats", "query": "ashby:dust", "mode": "board", "stop_reason": "stream_end",
             "stop_detail": "[ok] 3 offres", "http_requests": 1, "cards_seen": 3},
            {"source": "wttj", "query": "machine learning", "mode": "fraicheur", "stop_reason": "stream_end",
             "http_requests": 2, "cards_seen": 30},
        ],
    )
    data = client.get("/api/sources/health").json()
    assert data["run"]["id"] == run_id
    by_source = {s["source"]: s for s in data["sources"]}
    ats = by_source["ats"]
    assert ats["status"] == "degradee" and ats["label"]
    assert ats["boards"][0]["status"] == "http_404" and ats["boards"][0]["label"] == "Erreur HTTP 404"
    assert by_source["wttj"]["status"] == "ok"
