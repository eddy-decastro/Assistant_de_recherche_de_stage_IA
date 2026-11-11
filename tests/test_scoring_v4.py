"""Tests unitaires de la notation v4 (Spécification section 13).

Tous les tests utilisent exclusivement tmp_path et des clients LLM simulés :
aucune écriture dans data/jobs.db ni data/stage_copilot.db.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.matching.scoring_v4 import (  # noqa: E402
    LLMJudgeV4,
    compute_final_score_v4,
)
from src.storage.database import Database  # noqa: E402


def _sample_job() -> dict[str, Any]:
    return {
        "id": "job_v4_1",
        "title": "Stage PFE - Deep Learning & Quantification d'incertitude (F/H)",
        "company": "Safran Tech",
        "location": "Paris, France",
        "url": "https://example.com/safran-dl",
        "description": (
            "Au sein du pôle R&D IA de Safran Tech, vous serez encadré par un ingénieur "
            "de recherche ML senior. Vous travaillerez sur la quantification d'incertitude "
            "et le fine-tuning de modèles profonds sur nos données capteurs réelles. "
            "Ce stage de 6 mois à partir de mars 2027 à Paris pourra déboucher sur une thèse CIFRE."
        ),
    }


def _valid_llm_payload() -> dict[str, Any]:
    return {
        "contract_type": "STAGE",
        "duration_months": 6,
        "is_cesure": False,
        "class": "B",
        "class_reason": "Pôle R&D IA d'un grand groupe, mission de DL sur données capteurs.",
        "class_doubt": False,
        "axes": {
            "apprendre": {
                "stars": 4,
                "reason": "Encadrant ingénieur de recherche ML identifié.",
                "quote": "vous serez encadré par un ingénieur de recherche ML",
                "unknown": False,
            },
            "technique": {
                "stars": 4,
                "reason": "Quantification d'incertitude et fine-tuning sur données réelles.",
                "quote": "quantification d'incertitude et le fine-tuning de modèles profonds sur nos données capteurs réelles",
                "unknown": False,
            },
            "debouche": {
                "stars": 5,
                "reason": "Grand centre R&D reconnu et thèse CIFRE mentionnée.",
                "quote": "pourra déboucher sur une thèse CIFRE",
                "unknown": False,
            },
            "logistique": {
                "stars": 5,
                "reason": "Paris, 6 mois, mars 2027.",
                "quote": "stage de 6 mois à partir de mars 2027 à Paris",
                "unknown": False,
            },
        },
        "reputation_points": 3,
        "reputation_reason": "Centre de recherche reconnu.",
        "offer_quality_points": 0,
        "offer_quality_reason": "Offre claire et précise.",
        "adjustment": 3,
        "tags": ["donnees_reelles", "these_cifre", "defense"],
        "summary": "Excellent stage R&D en quantification d'incertitude avec piste CIFRE.",
    }


def test_v4_center_band_adjustment_and_class_offset() -> None:
    """Section 4 : Exemple exact de la spécification (Apprendre 4, Technique 4, Débouché 5, Logistique 5, B, +3)."""
    job = _sample_job()
    payload = _valid_llm_payload()

    breakdown = compute_final_score_v4(payload, job)

    assert breakdown.excluded is False
    assert breakdown.final_class == "B"
    # centre = 100 * (1.1*4 + 1.0*4 + 1.3*5 + 0.6*5) / 20 = 100 * 17.9 / 20 = 89.5
    assert breakdown.center == 89.5
    assert breakdown.band_low == 79.5
    assert breakdown.band_high == 99.5
    assert breakdown.adjustment == 3
    assert breakdown.class_score == 92.5
    # classe B -> barème caché -3 -> 89.5
    assert breakdown.display_score == 89.5
    assert round(breakdown.display_score) == 90 or int(round(breakdown.display_score)) in (89, 90)
    assert "defense" in breakdown.tags
    assert "these_cifre" in breakdown.tags
    assert breakdown.flags == []


def test_v4_unverified_quote_deducts_one_star_no_reputation_exception() -> None:
    """Section 3 + N4 : retrait d'1 étoile si citation absente/fausse, sur tous les axes ;
    l'ancienne citation factice « reputation » ne vaut plus preuve."""
    job = _sample_job()
    payload = _valid_llm_payload()
    # Citation inventée sur apprendre (4 -> 3)
    payload["axes"]["apprendre"]["quote"] = "encadré par trois prix Nobel de physique quantique"
    # Citation absente sur technique (4 -> 3)
    payload["axes"]["technique"]["quote"] = ""
    # Ancienne exception 'reputation' sur debouche à 4 étoiles -> descend à 3 comme les autres
    payload["axes"]["debouche"]["stars"] = 4
    payload["axes"]["debouche"]["quote"] = "reputation"

    breakdown = compute_final_score_v4(payload, job)
    assert breakdown.effective_stars["apprendre"] == 3
    assert breakdown.effective_stars["technique"] == 3
    assert breakdown.effective_stars["debouche"] == 3
    assert "[CITATION_NON_VERIFIEE:apprendre]" in breakdown.flags
    assert "[CITATION_NON_VERIFIEE:technique]" in breakdown.flags
    assert "[CITATION_NON_VERIFIEE:debouche]" in breakdown.flags

    # 5 étoiles avec seulement 'reputation' (ou « Réputation ») -> 4 + drapeau
    payload["axes"]["debouche"]["stars"] = 5
    payload["axes"]["debouche"]["quote"] = "Réputation"
    breakdown_5 = compute_final_score_v4(payload, job)
    assert breakdown_5.effective_stars["debouche"] == 4
    assert "[CITATION_NON_VERIFIEE:debouche]" in breakdown_5.flags


def test_v4_reputation_quote_not_verified_even_if_word_in_offer() -> None:
    """N4 : « reputation » ne passe pas verify_citation même si le mot figure dans l'offre."""
    job = _sample_job()
    job["description"] += " Notre réputation internationale est reconnue."
    payload = _valid_llm_payload()
    payload["axes"]["debouche"]["stars"] = 4
    payload["axes"]["debouche"]["quote"] = "réputation"
    breakdown = compute_final_score_v4(payload, job)
    assert breakdown.effective_stars["debouche"] == 3
    assert "[CITATION_NON_VERIFIEE:debouche]" in breakdown.flags


def test_v4_contract_without_quote_is_flagged_not_excluded() -> None:
    """N2 : CDI_CDD / ALTERNANCE sans citation vérifiable -> « contrat à vérifier », noté normalement."""
    job = _sample_job()
    payload = _valid_llm_payload()
    payload["contract_type"] = "CDI_CDD"
    payload["contract_quote"] = ""
    breakdown = compute_final_score_v4(payload, job)
    assert breakdown.excluded is False
    assert "[CONTRAT_A_VERIFIER]" in breakdown.flags
    assert breakdown.display_score == 89.5

    # Citation introuvable dans l'offre -> même traitement
    payload["contract_type"] = "ALTERNANCE"
    payload["contract_quote"] = "contrat d'apprentissage de 24 mois"
    breakdown_fake = compute_final_score_v4(payload, job)
    assert breakdown_fake.excluded is False
    assert "[CONTRAT_A_VERIFIER]" in breakdown_fake.flags


def test_v4_contract_with_verified_quote_is_excluded_with_raw_stars() -> None:
    """N2 : contrat incompatible prouvé par une citation exacte -> exclusion, étoiles brutes gardées."""
    job = _sample_job()
    job["description"] += " Poste en CDI à pourvoir rapidement."
    payload = _valid_llm_payload()
    payload["contract_type"] = "CDI_CDD"
    payload["contract_quote"] = "Poste en CDI"
    breakdown = compute_final_score_v4(payload, job)
    assert breakdown.excluded is True
    assert breakdown.exclusion_reason == "Type de contrat incompatible (CDI_CDD)"
    assert breakdown.flags == ["[EXCLU] Type de contrat incompatible (CDI_CDD)"]
    assert breakdown.axes["apprendre"]["stars"] == 4  # étoiles brutes conservées


def test_v4_title_filter_stays_hard_exclusion() -> None:
    """N2 : les filtres titre restent des exclusions dures, même sans citation de contrat."""
    job = _sample_job()
    job["title"] = "Alternance Data Scientist (F/H)"
    payload = _valid_llm_payload()
    payload["contract_type"] = "ALTERNANCE"
    payload["contract_quote"] = ""
    breakdown = compute_final_score_v4(payload, job)
    assert breakdown.excluded is True
    assert "[CONTRAT_A_VERIFIER]" not in breakdown.flags


def test_v4_schema_and_prompt_require_contract_quote() -> None:
    """N2/N4 : contract_quote dans le schéma et le prompt ; plus d'exception « reputation »."""
    from src.matching.scoring_v4 import get_response_schema_v4, get_system_prompt_v4

    schema = get_response_schema_v4()
    assert "contract_quote" in schema["properties"]
    assert "contract_quote" in schema["required"]
    prompt = get_system_prompt_v4()
    assert "contract_quote" in prompt
    assert '"quote": "reputation"' not in prompt
    assert "UNIQUEMENT par \"reputation_points\"" in prompt


def test_v4_cifre_these_alias_normalized() -> None:
    """N5 : « cifre_these » est remappé vers « these_cifre » (une seule étiquette)."""
    from src.constants import V4_TAGS

    assert "cifre_these" not in V4_TAGS
    payload = _valid_llm_payload()
    payload["tags"] = ["cifre_these", "these_cifre"]
    breakdown = compute_final_score_v4(payload, _sample_job())
    assert breakdown.tags == ["these_cifre"]


def test_v4_weak_axis_rules() -> None:
    """Section 5 : Apprendre <= 1 plafonne class_score à 55 ; Technique == 0 force la classe E."""
    job = _sample_job()

    # Cas 1 : Apprendre = 1 -> class_score <= 55 + drapeau ENCADREMENT_FAIBLE
    payload_weak_learn = _valid_llm_payload()
    payload_weak_learn["axes"]["apprendre"] = {
        "stars": 1,
        "reason": "Encadrement flou.",
        "quote": "",
        "unknown": False,
    }
    b_learn = compute_final_score_v4(payload_weak_learn, job)
    assert b_learn.class_score == 55.0
    assert "ENCADREMENT_FAIBLE" in b_learn.flags
    # Classe B (-3) -> display_score = 52.0
    assert b_learn.display_score == 52.0

    # Cas 2 : Technique = 0 -> classe forcée à E (-35)
    payload_no_tech = _valid_llm_payload()
    payload_no_tech["axes"]["technique"] = {
        "stars": 0,
        "reason": "Que du reporting PowerBI.",
        "quote": "",
        "unknown": False,
    }
    b_tech = compute_final_score_v4(payload_no_tech, job)
    assert b_tech.final_class == "E"
    assert b_tech.display_score == max(0.0, b_tech.class_score - 35.0)


def test_v4_inconsistent_adjustment_recalculated_and_clamped() -> None:
    """Section 6 : le code recalcule la somme reputation_points + offer_quality_points et borne à [-10, +10]."""
    job = _sample_job()
    payload = _valid_llm_payload()
    payload["reputation_points"] = 5
    payload["offer_quality_points"] = 2
    payload["adjustment"] = -9  # incohérent avec 5 + 2 = 7

    breakdown = compute_final_score_v4(payload, job)
    assert breakdown.adjustment == 7

    # Test du bornage à [-10, +10]
    payload["reputation_points"] = 8
    payload["offer_quality_points"] = 5
    breakdown_clamped = compute_final_score_v4(payload, job)
    assert breakdown_clamped.adjustment == 10


def test_v4_invalid_json_retry_fallback_and_non_note() -> None:
    """Section 8 & 13 : JSON invalide -> nouvel essai -> secours Groq -> secours DeepSeek -> NON_NOTE."""
    job = _sample_job()
    valid_json_str = json.dumps(_valid_llm_payload(), ensure_ascii=False)

    # 1. Premier essai Google invalide, second essai Google valide
    judge_retry = LLMJudgeV4(api_key="fake-google")
    calls_google: list[int] = []

    def fake_google_retry(_content: str) -> str:
        calls_google.append(1)
        if len(calls_google) == 1:
            return "{json cassé"
        return valid_json_str

    judge_retry._call_google = fake_google_retry  # type: ignore[method-assign]
    res_retry = judge_retry.judge(job)
    assert res_retry["api_error"] is False
    assert res_retry["v4_status"] == "OK"
    assert res_retry["v4_display_score"] == 89.5
    assert len(calls_google) == 2

    # 2. Google échoue 2 fois -> bascule sur Groq qui réussit
    judge_fallback = LLMJudgeV4(api_key="fake-google", groq_api_key="fake-groq")
    judge_fallback._call_google = lambda _c: "not json"  # type: ignore[method-assign]
    judge_fallback._call_openai_compat = lambda prov, _k, _m, _c: valid_json_str  # type: ignore[method-assign]
    res_fb = judge_fallback.judge(job)
    assert res_fb["api_error"] is False
    assert res_fb["v4_model"].startswith("groq:")
    assert res_fb["v4_display_score"] == 89.5

    # 3. Tous les fournisseurs échouent -> statut NON_NOTE, jamais de note inventée
    judge_fail = LLMJudgeV4(
        api_key="fake-google",
        groq_api_key="fake-groq",
        deepseek_api_key="fake-deepseek",
    )
    judge_fail._call_google = lambda _c: "{invalid"  # type: ignore[method-assign]
    judge_fail._call_openai_compat = lambda _p, _k, _m, _c: "{invalid"  # type: ignore[method-assign]
    res_non_note = judge_fail.judge(job)
    assert res_non_note["api_error"] is True
    assert res_non_note["v4_status"] == "NON_NOTE"
    assert res_non_note["v4_display_score"] is None
    assert res_non_note["v4_class_score"] is None
    assert res_non_note["v4_class"] is None


def test_v4_recompute_and_status_protection(tmp_path: Path) -> None:
    """Section 10 & 13 : --recompute-scores-v4 identique à l'origine et protection absolue des statuts d'Eddy."""
    db = Database(tmp_path / "test_v4_recompute.db")
    try:
        job = _sample_job()
        applied_time = datetime(2026, 10, 15, 12, 0, 0)
        job.update(
            {
                "status": "POSTULÉ",
                "rejection_reason": "mon_motif_perso",
                "applied_at": applied_time,
                "rerank_score": 84.0,
                "verdict": "BON",
            }
        )
        db.upsert_job(job)

        judge = LLMJudgeV4(api_key="fake-google")
        judge._call_google = lambda _c: json.dumps(_valid_llm_payload(), ensure_ascii=False)  # type: ignore[method-assign]
        res = judge.judge(job)

        db.update_v4(job["id"], res)
        saved = db.get_job(job["id"])
        assert saved is not None
        # Aucune colonne d'Eddy ni v3 n'est touchée
        assert saved["status"] == "POSTULÉ"
        assert saved["rejection_reason"] == "mon_motif_perso"
        assert saved["rerank_score"] == 84.0
        assert saved["verdict"] == "BON"
        assert saved["v4_display_score"] == 89.5
        assert saved["v4_class_score"] == 92.5
        assert saved["v4_center"] == 89.5
        assert saved["v4_class"] == "B"

        # Recalcul déterministe sans appel API : résultat strictement identique
        count = db.recompute_scores_v4()
        assert count == 1
        after_recomp = db.get_job(job["id"])
        assert after_recomp is not None
        assert after_recomp["v4_display_score"] == 89.5
        assert after_recomp["v4_class_score"] == 92.5
        assert after_recomp["v4_center"] == 89.5
        assert after_recomp["status"] == "POSTULÉ"
        assert after_recomp["rejection_reason"] == "mon_motif_perso"

        # Si Eddy change le barème de la classe B de -3 à -5, tout se recalcule sans API
        custom_cfg = {
            "scoring_v4": {
                "class_offsets": {"A": 0, "B": -5, "C": -10, "D": -18, "E": -35},
            }
        }
        db.recompute_scores_v4(config=custom_cfg)
        after_custom = db.get_job(job["id"])
        assert after_custom is not None
        assert after_custom["v4_display_score"] == 87.5
    finally:
        db.engine.dispose()


def test_unranked_jobs_ordered_by_freshness_not_semantic(tmp_path: Path) -> None:
    """Section 1 : l'étape 1 sémantique est supprimée de l'ordre de passage au juge (tri par fraîcheur)."""
    db = Database(tmp_path / "test_freshness_order.db")
    try:
        # Offre ancienne mais avec un score sémantique/final_score de 99
        db.upsert_job(
            {
                "id": "old_high_sem",
                "title": "Stage ML Ancien",
                "company": "A",
                "url": "https://example.com/old",
                "description": "Courte description.",
                "semantic_score": 99.0,
                "final_score": 99.0,
                "published_at": datetime(2026, 10, 1, 10, 0, 0),
                "created_at": datetime(2026, 10, 1, 10, 0, 0),
            }
        )
        # Offre récente mais avec un score sémantique/final_score de 10
        db.upsert_job(
            {
                "id": "fresh_low_sem",
                "title": "Stage ML Récent",
                "company": "B",
                "url": "https://example.com/fresh",
                "description": "Courte description.",
                "semantic_score": 10.0,
                "final_score": 10.0,
                "published_at": datetime(2026, 11, 2, 10, 0, 0),
                "created_at": datetime(2026, 11, 2, 10, 0, 0),
            }
        )
        queue = db.get_unranked_jobs(limit=10)
        assert [j["id"] for j in queue] == ["fresh_low_sem", "old_high_sem"]
    finally:
        db.engine.dispose()




def test_apprentissage_statistique_ne_prouve_pas_une_alternance() -> None:
    from src.matching.scoring_v4 import quote_supports_contract

    assert not quote_supports_contract("ALTERNANCE", "apprentissage statistique")
    assert not quote_supports_contract("ALTERNANCE", "apprentissage profond")
    assert not quote_supports_contract("ALTERNANCE", "Stage/ Alternance")
    assert quote_supports_contract("ALTERNANCE", "Contrat d'apprentissage de 24 mois")
    assert quote_supports_contract(
        "ALTERNANCE", "You must already be enrolled in a work-study program in a French school"
    )
    assert quote_supports_contract("CDI_CDD", "Poste en CDI basé à Paris")
    assert not quote_supports_contract("CDI_CDD", "Stage pouvant déboucher sur un CDI")


def test_fausse_alternance_non_exclue_mais_contrat_a_verifier() -> None:
    from src.constants import V4_FLAG_CONTRACT_TO_CHECK
    from src.matching.scoring_v4 import V4JudgeOutput, compute_final_score_v4

    job = _sample_job()
    job["description"] += " Méthodes d'apprentissage statistique sur séries temporelles."
    payload = _valid_llm_payload()
    payload["contract_type"] = "ALTERNANCE"
    payload["contract_quote"] = "apprentissage statistique"
    breakdown = compute_final_score_v4(V4JudgeOutput.model_validate(payload), job, None)
    assert not breakdown.excluded
    assert V4_FLAG_CONTRACT_TO_CHECK in breakdown.flags
