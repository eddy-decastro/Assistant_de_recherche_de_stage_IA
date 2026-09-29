"""Tests du composant job_feed : sérialisation des offres et actions de statut."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from components.job_feed.serialize import DESCRIPTION_LIMIT, serialize_feed, serialize_job
from src.constants import (
    STATUS_APPLIED,
    STATUS_NEW,
    TIER_1,
    VERDICT_EXCELLENT,
)

_NOW = datetime.now(timezone.utc).replace(tzinfo=None)

JOB = {
    "id": "offre-1",
    "title": "STAGE - Ingénieur.e Recherche Machine Learning (F/H)",
    "company": "Dassault Systèmes",
    "location": "Valbonne, France",
    "url": "https://example.com/offre?a=1&b=2",
    "description": None,
    "source": "jobteaser",
    "company_tier": 2,
    "semantic_score": 52.26,
    "final_score": 46.36,
    "status": STATUS_NEW,
    "created_at": _NOW - timedelta(hours=5),
    "rerank_score": None,
    "verdict": None,
    "match_reasons": [],
    "red_flags": [],
    "tech_stack": [],
}

RERANKED = {
    **JOB,
    "id": "offre-2",
    "company": "Mistral AI",
    "company_tier": TIER_1,
    "rerank_score": 88.0,
    "quality_score": 71.0,
    "verdict": VERDICT_EXCELLENT,
    "match_reasons": ["Modélisation PyTorch avancée"],
    "red_flags": ["Périmètre susceptible d'évoluer"],
    "tech_stack": ["PyTorch", "GNN"],
    "description": "Mission de recherche.\n\n\nPyTorch et GNN.",
    "status": STATUS_APPLIED,
    "reasoning": "Calendrier aligné, mission de modélisation réelle.",
    "sub_scores": {"modeling_depth": 5, "mentorship_team": 4, "career_leverage": 4, "pfe_compatibility": 5},
    "signals": {
        "encadrant_explicite": {"present": True, "evidence": "encadré par un chercheur senior"},
        "donnees_benchmark_seulement": {"present": False},
    },
}


def test_serialize_offre_non_evaluee() -> None:
    data = serialize_job(JOB, ())
    assert data["id"] == "offre-1"
    assert data["reranked"] is False and data["verdict"] == "" and data["verdict_label"] == ""
    assert data["score"] == 46 and data["align_label"] == "Secondaire" and data["align_tone"] == "warn"
    assert data["score_origin"] == "hybride" and data["quality"] is None
    assert data["sub_scores"] == [] and data["signals"] == [] and data["strengths"] == []
    assert data["status"] == STATUS_NEW and data["is_new"] is True and data["status_label"] == "Nouveau"
    assert data["source_label"] == "JobTeaser" and data["source_color"].startswith("#")
    assert data["description"] == "" and data["hard_cap"] == "" and data["group"] == ""


def test_serialize_offre_evaluee_complete() -> None:
    data = serialize_job(RERANKED, ("PyTorch",))
    assert data["reranked"] is True and data["score"] == 88 and data["quality"] == 71.0
    assert data["align_label"] == "Cœur de cible" and data["align_tone"] == "positive"
    assert data["verdict"] == VERDICT_EXCELLENT and data["verdict_label"] == "Excellent"
    assert data["technologies"] == ["PyTorch", "GNN"]
    assert data["strengths"] == ["Modélisation PyTorch avancée"]
    assert data["red_flags"] == ["Périmètre susceptible d'évoluer"]
    assert data["reasoning"].startswith("Calendrier aligné")
    assert data["is_new"] is False and data["status_label"] == "Postulé"
    # Sous-scores : ordre stable, libellés, tonalité selon la valeur
    subs = {item["key"]: item for item in data["sub_scores"]}
    assert set(subs) == {"modeling_depth", "mentorship_team", "career_leverage", "pfe_compatibility"}
    assert subs["modeling_depth"]["value"] == 5 and subs["modeling_depth"]["tone"] == "positive"
    assert subs["mentorship_team"]["value"] == 4 and subs["mentorship_team"]["tone"] == "accent"
    assert subs["modeling_depth"]["label"] == "Modélisation" and subs["modeling_depth"]["short"]
    # Seuls les signaux présents sont exposés, avec leur citation
    assert [s["evidence"] for s in data["signals"]] == ["encadré par un chercheur senior"]
    assert data["signals"][0]["tone"] == "positive"
    # Les sauts de ligne multiples de la fiche sont compactés
    assert data["description"] == "Mission de recherche.\nPyTorch et GNN."


def test_serialize_verrou_bloquant_et_plafond() -> None:
    capped = {**RERANKED, "hard_cap_triggered": " Reporting / dashboards BI "}
    data = serialize_job(capped, ())
    assert data["hard_cap"] == "Reporting / dashboards BI"
    assert any(b["tone"] == "alert" for b in data["badges"])
    assert serialize_job(RERANKED, ())["hard_cap"] == ""


def test_serialize_badges_structure_flags_et_exclusion() -> None:
    job = {
        **JOB,
        "structure_type": "SCALEUP_IA",
        "floor_reason": "Plancher scale-up 70",
        "scaleup_suggested": True,
        "flags": ["esn"],
        "status": "EXCLU",
        "exclusion_reason": "hors sujet",
    }
    labels = [b["label"] for b in serialize_job(job, ())["badges"]]
    assert "Scale-up IA" in labels and "Plancher scale-up 70" in labels
    assert "scale-up suggérée (à confirmer)" in labels
    assert "EXCLU" in labels


def test_serialize_url_non_http_neutralisee() -> None:
    for bad in ("javascript:alert(1)", "data:text/html,<script>1</script>", "//evil.example", "", None):
        assert serialize_job({**JOB, "url": bad}, ())["url"] == "", bad
    assert serialize_job({**JOB, "url": "http://a.example/x"}, ())["url"] == "http://a.example/x"


def test_serialize_ne_echappe_pas_le_html() -> None:
    hostile = '<img src=x onerror=alert(1)> & "guillemets"'
    data = serialize_job({**JOB, "title": hostile, "company": hostile, "description": hostile}, ())
    # Le composant affiche via textContent : la donnée brute doit rester intacte (pas de double échappement).
    assert data["title"] == hostile and data["company"] == hostile and data["description"] == hostile


def test_serialize_description_tronquee_et_json_serialisable() -> None:
    data = serialize_job({**RERANKED, "description": "x" * (DESCRIPTION_LIMIT * 3)}, ())
    assert len(data["description"]) == DESCRIPTION_LIMIT
    json.dumps(data)  # aucune valeur datetime / non sérialisable


def test_serialize_donnees_degradees() -> None:
    weird = {
        "id": 42,  # entier venu de la base
        "title": None,
        "company": None,
        "url": None,
        "status": None,
        "created_at": "pas-une-date",
        "rerank_score": 60,
        "sub_scores": {"modeling_depth": "4/5", "mentorship_team": None, "inconnu": 9},
        "signals": "pas un dict",
        "flags": None,
        "tech_stack": None,
        "match_reasons": None,
    }
    data = serialize_job(weird, ())
    assert data["id"] == "42" and data["title"] == "Offre sans titre" and data["company"] == "Entreprise inconnue"
    assert data["status"] == STATUS_NEW and data["is_new"] is True
    assert data["date_label"] == "Date inconnue" and data["url"] == ""
    values = {s["key"]: s["value"] for s in data["sub_scores"]}
    assert values["modeling_depth"] == 4 and values["mentorship_team"] == 3 and "inconnu" not in values
    assert data["signals"] == [] and data["technologies"] == [] and data["strengths"] == []
    json.dumps(data)


def test_serialize_feed_groupe_par_plateforme() -> None:
    jobs = [
        {**JOB, "id": "a", "source": "jobteaser"},
        {**JOB, "id": "b", "source": "linkedin"},
        {**JOB, "id": "c", "source": "jobteaser"},
    ]
    flat = serialize_feed(jobs, (), grouped=False)
    assert [j["id"] for j in flat] == ["a", "b", "c"] and {j["group"] for j in flat} == {""}
    grouped = serialize_feed(jobs, (), grouped=True)
    assert [(j["group"], j["id"]) for j in grouped] == [("LinkedIn", "b"), ("JobTeaser", "a"), ("JobTeaser", "c")]
    assert serialize_feed([], (), grouped=True) == []


def test_serialize_feed_volume_borne() -> None:
    jobs = [{**RERANKED, "id": f"j{i}", "description": "mot " * 5000} for i in range(1000)]
    payload = json.dumps(serialize_feed(jobs, ()))
    assert len(payload.encode("utf-8")) < 8_000_000, "Le mode « Tout » doit rester sous 8 Mo de charge utile."


from components.job_feed.actions import ALLOWED_STATUSES, apply_status_action, parse_action  # noqa: E402
from src.constants import STATUS_IGNORED, STATUS_INTERVIEW, STATUS_REJECTED  # noqa: E402


def test_parse_action_valide() -> None:
    assert parse_action({"type": "status", "id": "a1", "status": STATUS_APPLIED}) == {
        "type": "status", "id": "a1", "status": STATUS_APPLIED,
    }
    assert parse_action({"type": "letter", "id": "a1", "extra": "ignoré"}) == {"type": "letter", "id": "a1"}
    assert ALLOWED_STATUSES == {STATUS_NEW, STATUS_APPLIED, STATUS_INTERVIEW, STATUS_IGNORED}


def test_parse_action_forgee_ignoree() -> None:
    forged = [
        None, "status", 42, [], {},
        {"type": "status", "id": "", "status": STATUS_APPLIED},
        {"type": "status", "id": None, "status": STATUS_APPLIED},
        {"type": "status", "id": 5, "status": STATUS_APPLIED},
        {"type": "status", "id": "a1", "status": "DROP TABLE jobs"},
        {"type": "status", "id": "a1", "status": STATUS_REJECTED},
        {"type": "status", "id": "a1"},
        {"type": "delete", "id": "a1"},
        {"type": "letter", "id": ""},
    ]
    for raw in forged:
        assert parse_action(raw) is None, raw


def test_apply_status_action() -> None:
    calls: list[tuple] = []

    def fake_set_status(db, job_id, status):
        calls.append((db, job_id, status))

    db = object()
    assert apply_status_action(db, {"type": "status", "id": "a1", "status": STATUS_APPLIED}, fake_set_status) == "applied"
    assert calls == [(db, "a1", STATUS_APPLIED)]

    # Lettre, action forgée ou absente : aucune écriture.
    assert apply_status_action(db, {"type": "letter", "id": "a1"}, fake_set_status) == "ignored"
    assert apply_status_action(db, {"type": "status", "id": "a1", "status": "X"}, fake_set_status) == "ignored"
    assert apply_status_action(db, None, fake_set_status) == "ignored"
    assert len(calls) == 1

    def boom(db, job_id, status):
        raise RuntimeError("base verrouillée")

    assert apply_status_action(db, {"type": "status", "id": "a1", "status": STATUS_NEW}, boom) == "failed"
