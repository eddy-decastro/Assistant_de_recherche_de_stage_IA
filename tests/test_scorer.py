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

    # 2. Mots-clés d'excellence (déterministe)
    kw_high = scorer.keywords_score("Stage Data Science avec PyTorch, Docker et LLM")
    kw_low = scorer.keywords_score("Stage en gestion de projet")
    assert kw_high > kw_low
    print(f"[OK] keywords_score (haut={kw_high:.1f}, bas={kw_low:.1f})")

    # 3. Sous-score entreprise
    assert scorer.company_score(TIER_1) == 100.0
    assert scorer.company_score(TIER_ESN) == 20.0
    print("[OK] company_score")

    # 4. Scoring complet (déclenche le téléchargement du modèle au 1er appel)
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
