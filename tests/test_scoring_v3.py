"""Tests unitaires pour la grille de notation v3 et la vérification des citations."""
from __future__ import annotations

import concurrent.futures
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.matching.scoring_v3 import (
    compute_final_score,
    get_citation_stats,
    reset_citation_stats,
    verify_citation,
)


def test_french_elisions_in_citation_verification() -> None:
    """Vérifie que les élisions françaises (l', d', qu', s', n') ne pénalisent pas le ratio de match."""
    doc = (
        "Ce stage porte sur la modélisation statistique avancée et la quantification "
        "d'incertitude dans les réseaux de neurones. L'apprentissage profond et l'évaluation "
        "robuste sont au centre des travaux qu'il mènera au sein du laboratoire."
    )

    # 1. Élision avec d' (d'incertitude)
    assert verify_citation("quantification d'incertitude dans les réseaux", doc) is True

    # 2. Élision avec l' (l'apprentissage)
    assert verify_citation("l'apprentissage profond et l'évaluation robuste", doc) is True

    # 3. Élision avec qu' (qu'il mènera)
    assert verify_citation("travaux qu'il mènera au sein du laboratoire", doc) is True


def test_stopword_heavy_hallucination_rejection() -> None:
    """Vérifie qu'une citation forgée riche en mots-outils mais sans mots porteurs est rejetée."""
    doc = (
        "Rejoignez notre équipe en pleine croissance pour participer à des missions variées "
        "dans le cadre de nos projets d'ingénierie logicielle."
    )

    # Citation hallucinée : beaucoup de stopwords communs ("dans le cadre de la", "pour le"),
    # mais les mots porteurs ("développement", "modèles", "fondation", "générative") sont absents du doc.
    hallucination = (
        "dans le cadre de la mission pour le développement de modèles de fondation en IA générative"
    )

    assert verify_citation(hallucination, doc) is False


def test_thread_safe_citation_stats() -> None:
    """Vérifie que les compteurs globaux sont thread-safe lors d'appels concurrents."""
    reset_citation_stats()
    stats_init = get_citation_stats()
    assert stats_init == {"checked": 0, "verified": 0, "rejected": 0}

    doc = "Développement d'architectures de vision par ordinateur sous PyTorch."
    citations = [
        "vision par ordinateur sous PyTorch",  # Match
        "architectures de vision",              # Match
        "finance de marché haute fréquence",   # Rejet
        "robotique humanoïde spatiale",         # Rejet
    ] * 25  # 100 vérifications au total (50 matchs, 50 rejets)

    def _check(cit: str) -> bool:
        return verify_citation(cit, doc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(_check, citations))

    stats = get_citation_stats()
    assert stats["checked"] == 100
    assert stats["verified"] == 50
    assert stats["rejected"] == 50
    assert sum(results) == 50

    # Vérification de reset
    reset_citation_stats()
    assert get_citation_stats() == {"checked": 0, "verified": 0, "rejected": 0}


def test_scoring_v3_reproducibility() -> None:
    """Vérifie la reproductibilité du calcul de note sans appel réseau."""
    job = {
        "title": "Stage R&D Machine Learning",
        "company": "Owkin",
        "description": "Recherche en apprentissage profond avec encadrement senior.",
    }
    parsed = {
        "contract_type": "STAGE",
        "structure_type": "SCALEUP_IA",
        "sub_scores": {
            "technical_depth": 4,
            "target_alignment": 4,
            "learning_environment": 4,
            "logistics": 4,
        },
        "signals": {
            "encadrant_explicite": {"present": True, "evidence": "encadrement senior"},
        },
    }
    b1 = compute_final_score(parsed, job)
    b2 = compute_final_score(parsed, job)
    assert b1.final_score == b2.final_score
    assert b1.quality_score == b2.quality_score
    assert b1.floor_value == 70  # Scale-up floor
    assert b1.final_score >= 70
