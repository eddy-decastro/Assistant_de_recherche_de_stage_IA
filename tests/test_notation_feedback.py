"""Tests de la notation v4 principale (N1-N5) : anciennes notes, statuts, raisons
d'ignorer, boucle de retour dans le prompt, tri et sérialisation de la note unique.

Bases temporaires (tmp_path) et LLM simulés uniquement : aucune écriture dans
data/stage_copilot.db ni data/jobs.db, aucun appel réseau.
"""
from __future__ import annotations

import json
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

from components.job_feed.serialize import serialize_job, split_reasoning  # noqa: E402
from src.matching.scoring_v4 import (  # noqa: E402
    FEEDBACK_HEADER,
    LLMJudgeV4,
    build_feedback_block,
)
from src.storage.database import Database  # noqa: E402
from test_scoring_v4 import _sample_job, _valid_llm_payload  # noqa: E402
from utils.data import Filters, filter_jobs  # noqa: E402


# --------------------------------------------------------------------------- #
# N2 / N4 : anciennes notes stockées (sans contract_quote, avec « reputation »)
# --------------------------------------------------------------------------- #
def test_legacy_raw_json_recomputes_without_crash(tmp_path: Path) -> None:
    """Ancienne note v4 (CDI_CDD sans contract_quote, quote « reputation ») recalculée en
    code pur : pas d'exclusion mais « contrat à vérifier », débouché -1 étoile."""
    db = Database(tmp_path / "legacy_v4.db")
    try:
        job = _sample_job()
        job["status"] = "EXCLU"  # exclusion v3 historique
        db.upsert_job(job)
        legacy = _valid_llm_payload()
        legacy.pop("contract_quote", None)
        legacy["contract_type"] = "CDI_CDD"
        legacy["axes"]["debouche"]["stars"] = 4
        legacy["axes"]["debouche"]["quote"] = "reputation"
        db.update_v4(job["id"], {"v4_raw_json": legacy, "v4_class": "E", "v4_display_score": 0.0})

        assert db.recompute_scores_v4() == 1
        saved = db.get_job(job["id"])
        assert saved is not None
        assert "[CONTRAT_A_VERIFIER]" in saved["v4_flags"]
        assert "[CITATION_NON_VERIFIEE:debouche]" in saved["v4_flags"]
        assert saved["v4_stars"]["debouche"]["stars"] == 3
        assert saved["v4_display_score"] > 0
        # La v4 pilote l'exclusion : EXCLU (automatique) -> NOUVEAU
        assert saved["status"] == "NOUVEAU"
    finally:
        db.engine.dispose()


def test_exclusion_status_sync_never_touches_user_status(tmp_path: Path) -> None:
    """update_v4 : NOUVEAU -> EXCLU si la v4 exclut ; statuts d'Eddy intacts."""
    db = Database(tmp_path / "sync_v4.db")
    try:
        for job_id, status in (("nouveau", "NOUVEAU"), ("postule", "POSTULÉ"), ("ignore", "IGNORÉ")):
            db.upsert_job(
                {"id": job_id, "title": "Stage ML", "company": "X", "url": f"https://x/{job_id}", "status": status}
            )
            db.update_v4(
                job_id,
                {
                    "v4_class": "E",
                    "v4_display_score": 0.0,
                    "v4_flags_json": ["[EXCLU] Type de contrat incompatible (CDI_CDD)"],
                    "excluded": True,
                },
            )
        assert db.get_job("nouveau")["status"] == "EXCLU"
        assert db.get_job("postule")["status"] == "POSTULÉ"
        assert db.get_job("ignore")["status"] == "IGNORÉ"
    finally:
        db.engine.dispose()


def test_evaluate_single_job_v4_alias() -> None:
    """L'API appelle evaluate_single_job_v4 : alias de judge."""
    judge = LLMJudgeV4(api_key="fake-google")
    judge._call_google = lambda _c: json.dumps(_valid_llm_payload(), ensure_ascii=False)  # type: ignore[method-assign]
    res = judge.evaluate_single_job_v4(_sample_job())
    assert res["v4_status"] == "OK"
    assert res["contract_to_verify"] is False


# --------------------------------------------------------------------------- #
# N3 : raisons d'ignorer et boucle de retour dans le prompt
# --------------------------------------------------------------------------- #
def _decision_job(job_id: str, status: str, **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": job_id,
        "title": f"Stage {job_id}",
        "company": f"Entreprise {job_id}",
        "url": f"https://example.com/{job_id}",
        "status": status,
        "v4_class": "B",
        "v4_display_score": 80.0,
        "created_at": datetime(2026, 10, 1, 12, 0, 0),
    }
    base.update(extra)
    return base


def test_record_ignore_stores_code_and_comment(tmp_path: Path) -> None:
    """record_ignore : statut IGNORÉ + code + commentaire ; code invalide refusé ; migration idempotente."""
    db_path = tmp_path / "ignore.db"
    db = Database(db_path)
    try:
        db.upsert_job({"id": "j1", "title": "Stage", "company": "C", "url": "https://c/1"})
        assert db.record_ignore("j1", "pas_de_vrai_ml", "  Que du dashboard Power BI  ") is True
        saved = db.get_job("j1")
        assert saved["status"] == "IGNORÉ"
        assert saved["rejection_reason"] == "pas_de_vrai_ml"
        assert saved["rejection_comment"] == "Que du dashboard Power BI"
        # Commentaire vide -> effacé
        assert db.record_ignore("j1", "lieu") is True
        assert db.get_job("j1")["rejection_comment"] is None
        with pytest.raises(ValueError):
            db.record_ignore("j1", "code_inconnu")
        assert db.record_ignore("absent", "lieu") is False
        feedback = db.get_feedback_jobs()
        assert [j["id"] for j in feedback] == ["j1"]
        assert feedback[0]["rejection_reason"] == "lieu"
    finally:
        db.engine.dispose()
    # Seconde ouverture : la migration ne plante pas (colonne déjà présente)
    Database(db_path).engine.dispose()


def test_ignore_reason_constants_have_labels() -> None:
    from src.constants import IGNORE_REASON_LABELS, IGNORE_REASONS, ignore_reason_label

    assert set(IGNORE_REASONS) == set(IGNORE_REASON_LABELS)
    assert "auto_archive" not in IGNORE_REASONS and "offre_fermee" not in IGNORE_REASONS
    assert ignore_reason_label("pas_de_vrai_ml") == "Pas de vrai ML"
    assert ignore_reason_label(None) == ""


def test_build_feedback_block_balanced_bounded_deterministic() -> None:
    """Bloc équilibré, borné, déterministe ; auto_archive et offre_fermee exclus."""
    jobs = [
        _decision_job(f"p{i}", "POSTULÉ", applied_at=datetime(2026, 10, i + 1)) for i in range(10)
    ] + [
        _decision_job(f"i{i}", "IGNORÉ", rejection_reason="esn_conseil", rejection_comment="régie déguisée")
        for i in range(10)
    ] + [
        _decision_job("auto", "IGNORÉ", rejection_reason="auto_archive"),
        _decision_job("ferme", "IGNORÉ", rejection_reason="offre_fermee"),
        _decision_job("rev", "REJETÉ", rejection_reason="orientation BI"),  # rejet métier, pas une candidature
    ]
    block = build_feedback_block(jobs, max_examples=6, max_chars=5000)
    assert block.startswith(FEEDBACK_HEADER)
    lines = block.splitlines()[1:]
    assert len(lines) == 6
    assert sum(line.startswith("- POSTULÉ") for line in lines) == 3
    assert sum(line.startswith("- IGNORÉ (ESN / conseil) « régie déguisée »") for line in lines) == 3
    assert "Stage auto" not in block and "Stage ferme" not in block and "Stage rev " not in block
    # Les plus récentes d'abord
    assert lines[0].startswith("- POSTULÉ : Stage p9")
    # Déterministe, indépendant de l'ordre d'entrée
    assert build_feedback_block(list(reversed(jobs)), max_examples=6, max_chars=5000) == block
    # Borné en caractères
    short = build_feedback_block(jobs, max_examples=12, max_chars=400)
    assert 0 < len(short) <= 400
    # Rien à montrer -> bloc vide
    assert build_feedback_block([_decision_job("auto", "IGNORÉ", rejection_reason="auto_archive")]) == ""
    # Un seul camp disponible : complété jusqu'à max_examples
    only_pos = build_feedback_block(jobs[:10], max_examples=4, max_chars=5000)
    assert len(only_pos.splitlines()) == 5


def test_llm_judge_v4_injects_feedback_from_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """LLMJudgeV4(db=...) injecte les décisions d'Eddy dans le prompt système ; désactivable."""
    db = Database(tmp_path / "feedback.db")
    try:
        db.upsert_job(_decision_job("p1", "POSTULÉ", applied_at=datetime(2026, 10, 2)))
        db.upsert_job(_decision_job("i1", "NOUVEAU"))
        db.record_ignore("i1", "dates_duree", "début en septembre")

        enabled_cfg = {"scoring_v4": {"feedback": {"enabled": True, "max_examples": 12, "max_chars": 1500}}}
        judge = LLMJudgeV4(config=enabled_cfg, api_key="", groq_api_key="", deepseek_api_key="fake", db=db)
        assert FEEDBACK_HEADER in judge.system_prompt
        assert "IGNORÉ (Dates / durée) « début en septembre »" in judge.system_prompt
        assert "pas des règles absolues" in judge.system_prompt.lower()

        # Le prompt réellement envoyé au fournisseur (simulé) contient le bloc
        captured: dict[str, str] = {}

        class FakeProvider:
            def __init__(self, **_kwargs: Any) -> None:
                pass

            def generate(self, _user_content: str, system_prompt: str) -> str:
                captured["prompt"] = system_prompt
                return json.dumps(_valid_llm_payload(), ensure_ascii=False)

        from src.matching import scoring_v4 as module

        monkeypatch.setattr(module, "OpenAICompatibleProvider", FakeProvider)
        assert judge.judge(_sample_job())["v4_status"] == "OK"
        assert FEEDBACK_HEADER in captured["prompt"]

        disabled_cfg = {"scoring_v4": {"feedback": {"enabled": False}}}
        judge_off = LLMJudgeV4(config=disabled_cfg, api_key="fake-google", db=db)
        assert FEEDBACK_HEADER not in judge_off.system_prompt
        # Sans base : jamais de lecture implicite
        assert FEEDBACK_HEADER not in LLMJudgeV4(config=enabled_cfg, api_key="fake").system_prompt
    finally:
        db.engine.dispose()


# --------------------------------------------------------------------------- #
# N1 / N2 / N5 : tri par note v4 et sérialisation de la note unique
# --------------------------------------------------------------------------- #
def _feed_job(job_id: str, **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": job_id,
        "title": f"Stage {job_id}",
        "company": "Labo",
        "url": f"https://example.com/{job_id}",
        "status": "NOUVEAU",
        "source": "linkedin",
        "company_tier": 2,
    }
    base.update(extra)
    return base


def test_default_sort_uses_v4_display_score_unscored_last() -> None:
    jobs = [
        _feed_job("v3_haut", rerank_score=95.0),
        _feed_job("v4_moyen", rerank_score=40.0, v4_class="C", v4_display_score=60.0),
        _feed_job("v4_haut", rerank_score=20.0, v4_class="A", v4_display_score=88.0),
        _feed_job("sans_note"),
    ]
    for sort_by in ("score", "v3", "v4"):
        ordered = [j["id"] for j in filter_jobs(jobs, Filters(sort_by=sort_by))]
        assert ordered == ["v4_haut", "v4_moyen", "v3_haut", "sans_note"]
    assert Filters().sort_by == "score"
    assert Filters().is_default() is True
    # Ancien ordre v3 toujours disponible explicitement (ordre d'entrée conservé)
    assert [j["id"] for j in filter_jobs(jobs, Filters(sort_by="v3_legacy"))][0] == "v3_haut"


def test_v4_exclusion_drives_hiding_and_excluded_tab() -> None:
    jobs = [
        # Exclue par la v3 mais la v4 la note : la v4 décide (contrat à vérifier)
        _feed_job(
            "a_verifier",
            exclusion_reason="Type de contrat incompatible",
            verdict="EXCLU",
            v4_class="A",
            v4_display_score=85.0,
            v4_flags=["[CONTRAT_A_VERIFIER]"],
        ),
        _feed_job(
            "exclue_v4",
            v4_class="E",
            v4_display_score=0.0,
            v4_flags=["[EXCLU] Type de contrat incompatible (CDI_CDD)"],
        ),
        _feed_job("exclue_v3", exclusion_reason="Durée trop courte"),
    ]
    visible = [j["id"] for j in filter_jobs(jobs, Filters())]
    assert visible == ["a_verifier"]
    excluded_tab = [j["id"] for j in filter_jobs(jobs, Filters(only_excluded=True))]
    assert set(excluded_tab) == {"exclue_v4", "exclue_v3"}


def test_tag_filter_maps_cifre_alias() -> None:
    jobs = [_feed_job("old", v4_class="A", v4_display_score=80.0, v4_tags=["cifre_these"])]
    assert len(filter_jobs(jobs, Filters(v4_tags=("these_cifre",)))) == 1
    assert len(filter_jobs(jobs, Filters(v4_tags=("cifre_these",)))) == 1


def test_serialize_single_display_score_fields() -> None:
    graded = serialize_job(
        _feed_job(
            "g",
            rerank_score=40.0,
            v4_class="B",
            v4_display_score=82.4,
            v4_flags=["[CONTRAT_A_VERIFIER]"],
            v4_raw={"contract_type": "CDI_CDD", "contract_quote": ""},
            v4_tags=["cifre_these", "these_cifre"],
        )
    )
    assert graded["display_score"] == 82
    assert graded["display_class"] == "B"
    assert graded["display_label"] == "B · 82"
    assert graded["score_state"] == "v4"
    assert graded["v4_graded"] is True
    assert graded["excluded"] is False
    assert graded["contract_to_verify"] is True
    assert graded["contract_type_v4"] == "CDI_CDD"
    assert [t["key"] for t in graded["v4_tags"]] == ["these_cifre"]

    unscored = serialize_job(_feed_job("u", rerank_score=90.0))
    assert unscored["display_score"] is None
    assert unscored["display_class"] == ""
    assert unscored["score_state"] == "non_notee_v4"
    assert unscored["score_state_label"] == "Non notée v4"


def test_serialize_excluded_exposes_reason_and_raw_stars() -> None:
    stars = {
        "apprendre": {"stars": 0, "raw_stars": 4, "reason": "Encadrant ML", "quote": "q", "unknown": False},
        "technique": {"stars": 0, "raw_stars": 5, "reason": "DL", "quote": "q", "unknown": False},
        "debouche": {"stars": 0, "raw_stars": 3, "reason": "", "quote": "", "unknown": False},
        "logistique": {"stars": 0, "raw_stars": 5, "reason": "", "quote": "", "unknown": False},
    }
    data = serialize_job(
        _feed_job(
            "x",
            v4_class="E",
            v4_display_score=0.0,
            v4_stars=stars,
            v4_flags=["[EXCLU] Type de contrat incompatible (CDI_CDD)"],
        )
    )
    assert data["excluded"] is True
    assert data["exclusion_reason"] == "Type de contrat incompatible (CDI_CDD)"
    assert data["display_score"] is None
    assert data["score_state"] == "exclue"
    assert data["v4_axes_raw"] is True
    assert [a["stars"] for a in data["v4_axes"]] == [4, 5, 3, 5]
    assert all(a["effective_stars"] == 0 for a in data["v4_axes"])


def test_serialize_ignore_reason_and_comment() -> None:
    data = serialize_job(
        _feed_job("i", status="IGNORÉ", rejection_reason="esn_conseil", rejection_comment="régie")
    )
    assert data["ignore_reason"] == "esn_conseil"
    assert data["ignore_reason_label"] == "ESN / conseil"
    assert data["ignore_comment"] == "régie"


def test_reasoning_questions_block_split() -> None:
    text = (
        "Bonne offre de recherche.\n\n💡 Questions clés pour l'entretien :\n"
        "• Qui encadre ?\n• Piste CIFRE ?"
    )
    reasoning, questions = split_reasoning(text)
    assert reasoning == "Bonne offre de recherche."
    assert questions == ["Qui encadre ?", "Piste CIFRE ?"]
    data = serialize_job(_feed_job("r", rerank_score=70.0, reasoning=text))
    assert "Questions clés" not in data["reasoning"]
    assert data["questions_entretien"] == ["Qui encadre ?", "Piste CIFRE ?"]
    # Les questions déjà stockées à part priment
    data2 = serialize_job(
        _feed_job("r2", rerank_score=70.0, reasoning=text, questions_entretien=["Stockée"])
    )
    assert data2["questions_entretien"] == ["Stockée"]
    assert split_reasoning("Sans bloc.") == ("Sans bloc.", [])
