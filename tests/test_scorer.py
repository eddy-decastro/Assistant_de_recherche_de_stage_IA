"""Tests de qualification des entreprises (Tier 1, ESN, Neutre)."""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_config
from src.constants import TIER_1, TIER_ESN, TIER_NEUTRAL
from src.matching.scorer import Scorer


def test_determine_tier() -> None:
    config = load_config()
    scorer = Scorer(config)

    assert scorer.determine_tier("Doctolib") == TIER_1
    assert scorer.determine_tier("Mistral AI") == TIER_1
    assert scorer.determine_tier("Capgemini") == TIER_ESN
    assert scorer.determine_tier("Alten") == TIER_ESN
    assert scorer.determine_tier("Entreprise Inconnue") == TIER_NEUTRAL


def test_bug3_company_tier_regex_boundaries() -> None:
    """Vérifie que les noms d'entreprises ne matchent pas des sous-chaînes partielles."""
    config = load_config()
    scorer = Scorer(config)

    # Sub-Altenative Technologies ne doit PAS être classé ESN (Alten)
    assert scorer.determine_tier("Sub-Altenative Technologies") == TIER_NEUTRAL
    # Mistral AI doit être classé Tier 1
    assert scorer.determine_tier("Mistral AI") == TIER_1

