"""Test du scoring hybride (tier + mots-clés déterministes, sémantique optionnelle)."""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_config
from src.constants import TIER_1, TIER_ESN, TIER_NEUTRAL
from src.matching.scorer import Scorer


def main() -> None:
    config = load_config()
    scorer = Scorer(config)

    # 1. Détection de typologie (déterministe)
    assert scorer.determine_tier("Doctolib") == TIER_1
    assert scorer.determine_tier("Mistral AI") == TIER_1
    assert scorer.determine_tier("Capgemini") == TIER_ESN
    assert scorer.determine_tier("Alten") == TIER_ESN
    assert scorer.determine_tier("Entreprise Inconnue") == TIER_NEUTRAL
    print("[OK] determine_tier")

    # 2. Mots-clés d'excellence (déterministe, courbe saturante)
    kw_high = scorer.keywords_score("Stage Data Science avec PyTorch, Docker et LLM")
    kw_none = scorer.keywords_score("Stage en gestion de projet")
    kw_saturated = scorer.keywords_score(
        "PyTorch, TensorFlow, GNN, Docker, Kubernetes, Airflow, MLflow, Spark"
    )
    assert kw_none == 0.0, kw_none
    assert kw_high == 60.0, f"3 mots-clés clés sur 5 doivent donner 60, obtenu {kw_high}"
    assert kw_saturated == 100.0, f"Au-delà de 5 mots-clés, le score sature : {kw_saturated}"
    print(
        f"[OK] keywords_score saturant (3 mots-clés={kw_high:.1f}, "
        f"8 mots-clés={kw_saturated:.1f}, aucun={kw_none:.1f})"
    )

    # 3. Sous-score entreprise
    assert scorer.company_score(TIER_1) == 100.0
    assert scorer.company_score(TIER_ESN) == 20.0
    print("[OK] company_score")

    # 4. Scoring complet (déclenche le chargement du modèle au 1er appel)
    try:
        from sentence_transformers import SentenceTransformer  # noqa: F401
    except ImportError as exc:
        # Environnement sans torch/sentence-transformers (ou DLL bloqué par une
        # stratégie de contrôle d'application Windows) : les sous-scores
        # déterministes ci-dessus restent vérifiés, la partie sémantique est
        # signalée comme non testée plutôt que de faire échouer la suite.
        print(f"[SKIP] score() : modèle sémantique indisponible ({exc}) - test partiel")
        return

    job = {
        "title": "Data Scientist (Stage) — NLP & LLM",
        "company": "Doctolib",
        "location": "Paris",
        "url": "https://example.com/job/1",
        "description": "Vous travaillerez sur des LLM, PyTorch, Docker et le NLP.",
    }
    scored = scorer.score(job)
    assert 0.0 <= scored["final_score"] <= 100.0
    assert scored["company_tier"] == TIER_1
    print(
        f"[OK] score() final={scored['final_score']:.1f}, "
        f"semantic={scored['semantic_score']:.1f}"
    )


if __name__ == "__main__":
    main()
