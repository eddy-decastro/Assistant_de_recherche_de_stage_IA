"""Tests unitaires pour le juge LLM v3 (scoring, exclusions, planchers, plafonds, citations)."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_config
from src.constants import (
    DEFAULT_SUB_SCORE,
    VERDICT_EXCELLENT,
    VERDICT_GOOD,
    VERDICT_MIXED,
    VERDICT_OFF_TOPIC,
)
from src.matching.llm_judge import (
    LLMJudge,
    ScoreBreakdown,
    anonymize_cv,
    compute_final_score,
    is_title_excluded_contract,
    verdict_from_score,
    verify_citation,
)


def test_neutral_and_extreme_quality_scores() -> None:
    """Vérifie les bornes 0, 50 (neutre) et 100."""
    job = {"title": "Stage ML", "company": "PME Tech", "description": "Modélisation standard"}

    # 1. Neutre partout (3/5) -> 50
    neutral_parsed = {
        "contract_type": "STAGE",
        "sub_scores": {
            "technical_depth": 3,
            "target_alignment": 3,
            "learning_environment": 3,
            "logistics": 3,
        },
    }
    b_neutral = compute_final_score(neutral_parsed, job)
    assert b_neutral.quality_score == 50
    assert b_neutral.final_score == 50

    # 2. Minima (1/5) -> 0
    all_1 = {
        "contract_type": "STAGE",
        "sub_scores": {
            "technical_depth": 1,
            "target_alignment": 1,
            "learning_environment": 1,
            "logistics": 1,
        },
    }
    b_min = compute_final_score(all_1, job)
    assert b_min.quality_score == 0
    assert b_min.final_score == 0

    # 3. Maxima (5/5) -> 100
    all_5 = {
        "contract_type": "STAGE",
        "sub_scores": {
            "technical_depth": 5,
            "target_alignment": 5,
            "learning_environment": 5,
            "logistics": 5,
        },
    }
    b_max = compute_final_score(all_5, job)
    assert b_max.quality_score == 100
    assert b_max.final_score == 100


def test_signals_bonuses_and_penalties() -> None:
    """Bonus plafonné à +10, citations vérifiées, pénalité benchmark -5."""
    desc = (
        "Rejoignez notre équipe R&D. Vous serez encadré par un Senior ML Scientist (PhD). "
        "Vous travaillerez sur les données réelles de nos hôpitaux partenaires. "
        "Possibilité de thèse CIFRE à l'issue du stage."
    )
    job = {"title": "Stage ML Santé", "company": "Hopital Hub", "description": desc}

    parsed = {
        "contract_type": "STAGE",
        "sub_scores": {"technical_depth": 3, "target_alignment": 3, "learning_environment": 3, "logistics": 3},
        "signals": {
            "encadrant_explicite": {"present": True, "evidence": "Senior ML Scientist (PhD)"},
            "donnees_reelles_explicites": {"present": True, "evidence": "données réelles de nos hôpitaux"},
            "suite_explicite": {"present": True, "evidence": "Possibilité de thèse CIFRE"},
        },
    }
    # 50 + 6 + 3 + 3 = 62, plafonné à +10 bonus => 60
    b = compute_final_score(parsed, job)
    assert b.quality_score == 60

    # Si la citation est fausse ou absente du texte -> pas de bonus
    parsed_fake = {
        "contract_type": "STAGE",
        "sub_scores": {"technical_depth": 3, "target_alignment": 3, "learning_environment": 3, "logistics": 3},
        "signals": {
            "encadrant_explicite": {"present": True, "evidence": "encadrement exceptionnel par Turing"},
        },
    }
    b_fake = compute_final_score(parsed_fake, job)
    assert b_fake.quality_score == 50  # Pas de bonus accordé

    # Pénalité benchmark -5 si présent et vérifié
    desc_bench = "Projet académique : évaluation sur les benchmarks publics de référence ImageNet."
    job_bench = {"title": "Stage Benchmark", "company": "PME", "description": desc_bench}
    parsed_bench = {
        "contract_type": "STAGE",
        "sub_scores": {"technical_depth": 3, "target_alignment": 3, "learning_environment": 3, "logistics": 3},
        "signals": {
            "donnees_benchmark_seulement": {"present": True, "evidence": "benchmarks publics de référence ImageNet"},
        },
    }
    b_bench = compute_final_score(parsed_bench, job_bench)
    assert b_bench.quality_score == 45


def test_exclusions_amont_et_aval() -> None:
    """CDI, alternance seule, césure, 3 mois (exclus) ; stage ou alternance et 5 mois (non exclus)."""
    exclusion_kws = ["cdi", "cdd", "alternance", "apprentissage", "contrat pro", "freelance", "vie"]

    # 1. Exclusion amont titre
    assert is_title_excluded_contract("Data Scientist CDI", exclusion_kws)[0] is True
    assert is_title_excluded_contract("Alternance Data Analyst", exclusion_kws)[0] is True
    # "Stage de pré-embauche CDI" contient le mot stage => non exclu en amont
    assert is_title_excluded_contract("Stage de pré-embauche CDI", exclusion_kws)[0] is False

    # 2. Exclusion aval (LLM)
    job = {"title": "Offre Data", "company": "PME", "description": "Offre d'alternance"}
    # Alternance seule => exclu
    assert compute_final_score({"contract_type": "ALTERNANCE"}, job).excluded is True
    # CDI/CDD => exclu
    assert compute_final_score({"contract_type": "CDI_CDD"}, job).excluded is True
    # Césure => exclu
    assert compute_final_score({"contract_type": "STAGE", "is_cesure": True}, job).excluded is True
    # 3 mois (< 4 mois) => exclu
    assert compute_final_score({"contract_type": "STAGE", "duration_months": 3}, job).excluded is True

    # 3. Non exclus : STAGE_OU_ALTERNANCE et stage 5 mois
    b_mix = compute_final_score({"contract_type": "STAGE_OU_ALTERNANCE", "sub_scores": {}}, job)
    assert b_mix.excluded is False
    b_5m = compute_final_score({"contract_type": "STAGE", "duration_months": 5, "sub_scores": {}}, job)
    assert b_5m.excluded is False


def test_floors_application_and_condition() -> None:
    """Planchers 70 (scale-up), 60 (R&D groupe), 50 (labo public) conditionnés à technical_depth >= 3."""
    # Scale-up (Owkin est dans la liste scaleup) avec technical_depth = 4
    job_owkin = {"title": "Stage ML", "company": "Owkin", "description": "Recherche Deep Learning"}
    parsed_owkin = {
        "contract_type": "STAGE",
        "structure_type": "SCALEUP_IA",
        "sub_scores": {"technical_depth": 4, "target_alignment": 3, "learning_environment": 3, "logistics": 3},
    }
    # Base quality = ((0.35*4 + 0.20*3 + 0.30*3 + 0.15*3 - 1)/4)*100 = ((3.35 - 1)/4)*100 = 58.75 -> 59
    b_owkin = compute_final_score(parsed_owkin, job_owkin)
    assert b_owkin.quality_score == 59
    assert b_owkin.floor_value == 70
    assert b_owkin.final_score == 70  # Plancher 70 appliqué

    # Même offre mais technical_depth = 2 : PAS de plancher !
    parsed_low_tech = {
        "contract_type": "STAGE",
        "structure_type": "SCALEUP_IA",
        "sub_scores": {"technical_depth": 2, "target_alignment": 3, "learning_environment": 3, "logistics": 3},
    }
    b_low = compute_final_score(parsed_low_tech, job_owkin)
    assert b_low.floor_value is None
    assert b_low.final_score == b_low.quality_score  # La qualité fait foi

    # Grand groupe R&D (EDF R&D) : plancher 60
    job_edf = {"title": "Stage R&D", "company": "EDF R&D", "description": "Optimisation stochastique"}
    parsed_edf = {
        "contract_type": "STAGE",
        "structure_type": "GRAND_GROUPE_RD",
        "sub_scores": {"technical_depth": 3, "target_alignment": 3, "learning_environment": 3, "logistics": 3},
    }
    b_edf = compute_final_score(parsed_edf, job_edf)
    assert b_edf.floor_value == 60
    assert b_edf.final_score == 60

    # Labo public : plancher 50
    job_inria = {"title": "Stage Inria", "company": "Inria", "description": "Modélisation théorique"}
    parsed_inria = {
        "contract_type": "STAGE",
        "structure_type": "LABO_PUBLIC",
        "sub_scores": {"technical_depth": 3, "target_alignment": 3, "learning_environment": 3, "logistics": 3},
    }
    b_inria = compute_final_score(parsed_inria, job_inria)
    assert b_inria.floor_value == 50
    assert b_inria.final_score == 50

    # Offre à note de qualité supérieure au plancher : max(quality, floor) conserve la note de qualité
    parsed_excellent = {
        "contract_type": "STAGE",
        "structure_type": "SCALEUP_IA",
        "sub_scores": {"technical_depth": 5, "target_alignment": 5, "learning_environment": 5, "logistics": 4},
    }
    b_exc = compute_final_score(parsed_excellent, job_owkin)
    assert b_exc.quality_score >= 85
    assert b_exc.final_score == b_exc.quality_score  # Ne baisse pas à 70


def test_mistral_ai_defense_cap_overrides_floor() -> None:
    """Mistral AI est dans le Next40 mais exclu pour l'éthique : DEFENSE 10 l'emporte sur son plancher 70."""
    job_mistral = {"title": "Stage LLM", "company": "Mistral AI", "description": "Modèles de fondation"}
    parsed = {
        "contract_type": "STAGE",
        "structure_type": "SCALEUP_IA",
        "sub_scores": {"technical_depth": 5, "target_alignment": 5, "learning_environment": 5, "logistics": 5},
    }
    b = compute_final_score(parsed, job_mistral)
    assert b.quality_score == 100
    assert b.cap_applied == "DEFENSE"
    assert b.cap_value == 10
    assert b.final_score == 10  # Plafond DEFENSE l'emporte


def test_cap_citation_verification() -> None:
    """Un plafond avec citation absente du texte n'est pas appliqué."""
    job = {"title": "Stage ML", "company": "PME", "description": "Projet de modélisation classique sans BI."}
    parsed = {
        "contract_type": "STAGE",
        "sub_scores": {"technical_depth": 4, "target_alignment": 3, "learning_environment": 3, "logistics": 3},
        "hard_cap_triggered": "BI_REPORTING",
        "hard_cap_evidence": "dashboards Power BI pour la direction",
    }
    # Citation inventée par le LLM (non présente dans la description)
    b = compute_final_score(parsed, job)
    assert b.cap_applied is None
    assert b.final_score == b.quality_score


def test_cv_anonymization() -> None:
    """Retrait des emails, numéros de téléphone et URLs de réseaux sociaux."""
    raw_cv = (
        "Eddy DE CASTRO\n"
        "Téléphone : 06 98 82 44 85\n"
        "Email : eddyprepa123@gmail.com\n"
        "LinkedIn : https://www.linkedin.com/in/eddy-de-castro/\n"
        "GitHub : https://github.com/eddy-decastro\n"
        "Expérience PyTorch et Graph ML."
    )
    anon = anonymize_cv(raw_cv)
    assert "06 98 82 44 85" not in anon
    assert "[TÉLÉPHONE_MASQUÉ]" in anon
    assert "eddyprepa123@gmail.com" not in anon
    assert "[EMAIL_MASQUÉ]" in anon
    assert "https://www.linkedin.com" not in anon
    assert "[PROFIL_MASQUÉ]" in anon
    assert "Expérience PyTorch et Graph ML." in anon
