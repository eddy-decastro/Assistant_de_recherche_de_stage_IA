"""Tests unitaires pour les constantes de la grille de scoring v3."""
from __future__ import annotations

import math
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.constants import (
    BENCHMARK_PENALTY,
    CONTRACT_TYPES,
    CONTRACT_TYPE_LABELS,
    DEFAULT_SUB_SCORE,
    FLAG_LABELS,
    FLAG_TONES,
    FLAGS,
    HARD_CAP_LABELS,
    HARD_CAP_RULES,
    MAX_BONUS_TOTAL,
    SIGNAL_BONUSES,
    STRUCTURE_TYPE_LABELS,
    STRUCTURE_TYPE_TONES,
    STRUCTURE_TYPES,
    SUB_SCORE_KEYS,
    SUB_SCORE_LABELS,
    SUB_SCORE_SHORT_LABELS,
    SUB_SCORE_WEIGHTS,
)


def test_sub_score_weights_sum_to_one() -> None:
    """La somme des poids des sous-scores doit valoir exactement 1.0."""
    total = sum(SUB_SCORE_WEIGHTS.values())
    assert math.isclose(total, 1.0, rel_tol=1e-9)


def test_sub_score_keys_alignment() -> None:
    """Toutes les clés de SUB_SCORE_KEYS doivent avoir un poids, un libellé long et un court."""
    assert set(SUB_SCORE_KEYS) == {
        "technical_depth",
        "target_alignment",
        "learning_environment",
        "logistics",
    }
    for key in SUB_SCORE_KEYS:
        assert key in SUB_SCORE_WEIGHTS
        assert key in SUB_SCORE_LABELS
        assert key in SUB_SCORE_SHORT_LABELS


def test_default_sub_score_is_neutral_three() -> None:
    """La valeur par défaut en cas d'information absente doit être 3."""
    assert DEFAULT_SUB_SCORE == 3


def test_contract_types() -> None:
    """Les 5 types de contrats doivent être définis et avoir un libellé."""
    expected = {"STAGE", "STAGE_OU_ALTERNANCE", "ALTERNANCE", "CDI_CDD", "AUTRE"}
    assert set(CONTRACT_TYPES) == expected
    for ct in CONTRACT_TYPES:
        assert ct in CONTRACT_TYPE_LABELS


def test_structure_types_and_tones() -> None:
    """Les 8 types de structure doivent être définis avec libellés et tonalités de couleur."""
    expected = {
        "ESN_CONSEIL",
        "LABO_PUBLIC",
        "LABO_PRIVE",
        "GRAND_GROUPE_RD",
        "SCALEUP_IA",
        "STARTUP_PETITE",
        "AUTRE",
        "INCONNU",
    }
    assert set(STRUCTURE_TYPES) == expected
    for st in STRUCTURE_TYPES:
        assert st in STRUCTURE_TYPE_LABELS
        assert st in STRUCTURE_TYPE_TONES
    # Vérification des tonalités spécifiques
    assert STRUCTURE_TYPE_TONES["ESN_CONSEIL"] == "alert"  # rouge
    assert STRUCTURE_TYPE_TONES["SCALEUP_IA"] == "positive"  # vert
    assert STRUCTURE_TYPE_TONES["GRAND_GROUPE_RD"] == "positive"  # vert
    assert STRUCTURE_TYPE_TONES["LABO_PUBLIC"] == "positive"  # vert


def test_hard_cap_rules_v3() -> None:
    """Les 6 verrous v3 et leurs plafonds stricts."""
    expected_caps = {
        "DEFENSE": 10,
        "TRADING": 25,
        "BI_REPORTING": 30,
        "ESN_REGIE": 35,
        "ENCADREMENT_ABSENT": 35,
        "SHALLOW_AI": 40,
    }
    assert HARD_CAP_RULES == expected_caps
    for cap in HARD_CAP_RULES:
        assert cap in HARD_CAP_LABELS


def test_flags_and_tones() -> None:
    """Les flags v3 et leurs tonalités (rouge : défense/éthique, orange : incertitudes)."""
    expected_flags = {
        "CALENDRIER_DECALE",
        "DUREE_INCERTAINE",
        "HORS_IDF",
        "PETITE_STARTUP",
        "STACK_FLOUE",
        "DEFENSE_INDIRECTE",
        "ETHIQUE_A_EXAMINER",
        "FINANCE",
    }
    assert set(FLAGS) == expected_flags
    for fl in FLAGS:
        assert fl in FLAG_LABELS
        assert fl in FLAG_TONES
    assert FLAG_TONES["DEFENSE_INDIRECTE"] == "alert"
    assert FLAG_TONES["ETHIQUE_A_EXAMINER"] == "alert"
    assert FLAG_TONES["CALENDRIER_DECALE"] == "warn"


def test_signal_bonuses() -> None:
    """Les bonus de signaux et pénalités de benchmark."""
    assert SIGNAL_BONUSES == {
        "encadrant_explicite": 6,
        "donnees_reelles_explicites": 3,
        "suite_explicite": 3,
    }
    assert BENCHMARK_PENALTY == 5
    assert MAX_BONUS_TOTAL == 10


def test_company_penalties_dassault() -> None:
    """Vérifie que la pénalité d'entreprise de 20 points pour Dassault est bien appliquée."""
    from src.config import load_config
    from src.matching.scoring_v3 import compute_final_score
    from src.matching.scorer import Scorer

    cfg = load_config()

    parsed_normal = {
        "contract_type": "STAGE",
        "structure_type": "GRAND_GROUPE_RD",
        "sub_scores": {
            "technical_depth": 5,
            "target_alignment": 5,
            "learning_environment": 4,
            "logistics": 5,
        },
        "signals": {},
        "red_flags": [],
    }

    job_generic = {
        "id": "job_generic",
        "title": "Stage R&D Deep Learning",
        "company": "Autre Entreprise RD",
        "description": "Stage R&D deep learning pour la vision par ordinateur.",
    }
    job_dassault = {
        "id": "job_dassault",
        "title": "Stage R&D Deep Learning",
        "company": "Dassault Systèmes",
        "description": "Stage R&D deep learning pour la vision par ordinateur chez Dassault Systèmes.",
    }

    res_generic = compute_final_score(parsed_normal, job_generic, cfg)
    res_dassault = compute_final_score(parsed_normal, job_dassault, cfg)

    # La note Dassault doit être inférieure de 20 points à celle d'une entreprise similaire
    assert res_generic.final_score - res_dassault.final_score == 20
    assert res_dassault.final_score == res_generic.final_score - 20

    # Vérification également sur Scorer étage 1
    scorer = Scorer(cfg)
    scorer.semantic_score = lambda text: 50.0
    scored_generic = scorer.score(job_generic)
    scored_dassault = scorer.score(job_dassault)
    # Dassault Systèmes a TIER_1 (100) mais subit le malus de 20
    assert scored_dassault["final_score"] <= scored_generic["final_score"] + (0.25 * 100 - 0.25 * 60) - 20 + 0.01

