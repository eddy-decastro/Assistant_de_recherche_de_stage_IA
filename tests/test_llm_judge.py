from __future__ import annotations

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.matching.llm_judge import (
    LLMJudge,
    calculate_score_from_sub_scores,
    hard_cap_max,
    _normalize_hard_cap,
    verdict_from_score,
)
from src.constants import (
    VERDICT_EXCELLENT,
    VERDICT_GOOD,
    VERDICT_MIXED,
    VERDICT_OFF_TOPIC,
)


def test_score_calculation_weights() -> None:
    # 1. Bornes extrêmes
    all_5 = {"modeling_depth": 5, "mentorship_team": 5, "engineering_practice": 5, "option_value": 5, "logistics": 5}
    assert calculate_score_from_sub_scores(all_5) == 100

    all_1 = {"modeling_depth": 1, "mentorship_team": 1, "engineering_practice": 1, "option_value": 1, "logistics": 1}
    assert calculate_score_from_sub_scores(all_1) == 0

    all_3 = {"modeling_depth": 3, "mentorship_team": 3, "engineering_practice": 3, "option_value": 3, "logistics": 3}
    assert calculate_score_from_sub_scores(all_3) == 50

    # Calibration example: PINNs research lab (5, 5, 3, 5, 5)
    # W = 0.30*5 + 0.25*5 + 0.20*3 + 0.15*5 + 0.10*5 = 1.5 + 1.25 + 0.6 + 0.75 + 0.5 = 4.60
    # Score = (4.60 - 1) / 4 * 100 = 90
    calib_1 = {"modeling_depth": 5, "mentorship_team": 5, "engineering_practice": 3, "option_value": 5, "logistics": 5}
    s1 = calculate_score_from_sub_scores(calib_1)
    assert s1 == 90
    assert verdict_from_score(s1) == VERDICT_EXCELLENT


def test_hard_caps() -> None:
    assert hard_cap_max("ALTERNANCE") == 15
    assert hard_cap_max("NOT_A_PFE") == 15
    assert hard_cap_max("BI_REPORTING") == 30
    assert hard_cap_max("SHALLOW_AI") == 40
    assert hard_cap_max("FINANCE") == 50
    assert hard_cap_max("NONE") is None
    assert _normalize_hard_cap("NONE") is None
    assert _normalize_hard_cap("BI_REPORTING") == "BI_REPORTING"


def test_judge_parsing_new_schema() -> None:
    judge = LLMJudge(api_key="fake")
    job = {"final_score": 50}

    fake_json = """{
      "information_level": "COMPLET",
      "evidence": {
        "modeling_depth": "PyTorch et PINNs",
        "mentorship_team": "chercheur PhD",
        "engineering_practice": "cluster GPU",
        "option_value": "thèse CIFRE envisagée",
        "logistics": "stage 6 mois début avril Paris"
      },
      "reasoning": "Opportunité R&D avec chercheur.",
      "hard_cap_triggered": "NONE",
      "hard_cap_evidence": null,
      "sub_scores": {
        "modeling_depth": 5,
        "mentorship_team": 5,
        "engineering_practice": 3,
        "option_value": 5,
        "logistics": 5
      },
      "flags": ["CALENDRIER_DECALE"],
      "match_reasons": ["R&D PINNs", "Thèse CIFRE"],
      "red_flags": [],
      "tech_stack_detected": ["PyTorch", "JAX"],
      "questions_entretien": ["Quel cluster GPU ?"]
    }"""

    res = judge._parse_response(fake_json, job)
    assert res["rerank_score"] == 90
    assert res["verdict"] == VERDICT_EXCELLENT
    assert res["flags"] == ["CALENDRIER_DECALE"]
    assert res["questions_entretien"] == ["Quel cluster GPU ?"]
    assert res["tech_stack"] == ["PyTorch", "JAX"]
    assert res["hard_cap_triggered"] is None


def test_judge_parsing_hard_cap_capping() -> None:
    judge = LLMJudge(api_key="fake")
    job = {"final_score": 50}

    fake_json_cap = """{
      "information_level": "COMPLET",
      "reasoning": "Mission BI dashboards.",
      "hard_cap_triggered": "BI_REPORTING",
      "hard_cap_evidence": "dashboards Power BI",
      "sub_scores": {
        "modeling_depth": 3,
        "mentorship_team": 4,
        "engineering_practice": 3,
        "option_value": 3,
        "logistics": 5
      }
    }"""
    res_cap = judge._parse_response(fake_json_cap, job)
    assert res_cap["rerank_score"] <= 30
    assert res_cap["hard_cap_triggered"] == "BI_REPORTING"
    assert res_cap["verdict"] == VERDICT_OFF_TOPIC


def test_build_content_limits() -> None:
    judge = LLMJudge(api_key="fake")
    long_desc = "A" * 15000
    long_cv = "B" * 8000
    job = {"title": "Stage ML", "company": "Test", "description": long_desc}

    content = judge._build_content(job, cv_text=long_cv)
    # Vérifie que la description est tronquée exactement à 12000 caractères
    assert ("A" * 12000) in content
    assert ("A" * 12001) not in content
    # Vérifie que le CV est tronqué exactement à 6000 caractères
    assert ("B" * 6000) in content
    assert ("B" * 6001) not in content


def test_judge_api_error_handling() -> None:
    """Vérifie qu'une erreur API (429 ou réseau) ne produit pas de fausse note 0.0."""
    from unittest.mock import patch
    from google.genai.errors import APIError

    judge = LLMJudge(api_key="fake")
    job = {"title": "Stage ML", "final_score": 75.0}

    # 1. Simulation d'une erreur 429 (quota dépassé)
    mock_err = APIError(429, "Quota exceeded for quota metric 'GenerateContent'")
    with patch.object(judge, "_post_chat", side_effect=mock_err):
        res = judge.judge(job)
        assert res["api_error"] is True
        assert res["rerank_score"] is None
        assert res["verdict"] is None
        assert res["information_level"] == "API_ERROR"
        assert any("429" in flag for flag in res["red_flags"])

    # 2. Simulation d'une panne réseau inattendue
    with patch.object(judge, "_post_chat", side_effect=ConnectionResetError("Connexion perdue")):
        res = judge.judge(job)
        assert res["api_error"] is True
        assert res["rerank_score"] is None
        assert res["verdict"] is None
        assert res["information_level"] == "API_ERROR"


def test_judge_missing_key_handling() -> None:
    """Vérifie qu'une clé API manquante ne corrompt pas l'offre."""
    judge = LLMJudge(api_key="")
    job = {"title": "Stage ML", "final_score": 75.0}
    res = judge.judge(job)
    assert res["api_error"] is True
    assert res["rerank_score"] is None
    assert res["verdict"] is None


def test_gemini_rate_limiter_spacing() -> None:
    import time
    from src.matching.llm_judge import GeminiRateLimiter
    limiter = GeminiRateLimiter(rpm=600)  # intervalle 0.1s
    limiter.wait_for_slot()
    t1 = time.time()
    limiter.wait_for_slot()
    t2 = time.time()
    assert (t2 - t1) >= 0.08  # Espacement respecté


def test_gemini_rate_limiter_429_block() -> None:
    import time
    from src.matching.llm_judge import GeminiRateLimiter
    limiter = GeminiRateLimiter(rpm=600)
    limiter.report_429(0.15)
    t0 = time.time()
    limiter.wait_for_slot()
    t1 = time.time()
    assert (t1 - t0) >= 0.12  # Pause globale respectée

