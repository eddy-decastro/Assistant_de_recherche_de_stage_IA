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
    assert scorer.determine_tier("Owkin") == TIER_1
    # Mistral AI est retiré de tier_1 car dans excluded_defense
    assert scorer.determine_tier("Mistral AI") == TIER_ESN
    assert scorer.determine_tier("Helsing") == TIER_ESN
    assert scorer.determine_tier("Capgemini") == TIER_ESN
    assert scorer.determine_tier("Alten") == TIER_ESN
    assert scorer.determine_tier("Entreprise Inconnue") == TIER_NEUTRAL


def test_bug3_company_tier_regex_boundaries() -> None:
    """Vérifie que les noms d'entreprises ne matchent pas des sous-chaînes partielles."""
    config = load_config()
    scorer = Scorer(config)

    # Sub-Altenative Technologies ne doit PAS être classé ESN (Alten)
    assert scorer.determine_tier("Sub-Altenative Technologies") == TIER_NEUTRAL
    # Datadog doit être classé Tier 1
    assert scorer.determine_tier("Datadog") == TIER_1


def test_short_names_matching() -> None:
    """Vérifie l'absence de faux positifs sur les noms courts et génériques."""
    config = load_config()
    scorer = Scorer(config)

    # Noms exacts ou alias
    assert scorer.determine_tier("NW") == TIER_1
    assert scorer.determine_tier("NW Groupe") == TIER_1
    assert scorer.determine_tier("TSE") == TIER_1
    assert scorer.determine_tier("Swan") == TIER_1

    # Mots courants contenant le nom court : ne doivent PAS matcher
    assert scorer.determine_tier("Black Swan Capital") == TIER_NEUTRAL
    assert scorer.determine_tier("Positive Thinking Company") == TIER_NEUTRAL
    assert scorer.determine_tier("Un nouveau projet") == TIER_NEUTRAL


def test_penalty_keywords_no_code() -> None:
    """Vérifie que la présence d'outils no-code (n8n, make.com, zapier) applique une pénalité."""
    config = load_config()
    scorer = Scorer(config)

    # Offre avec compétences ML
    text_normal = "Stage Machine Learning avec PyTorch et Docker pour modélisation avancée."
    score_normal = scorer.keywords_score(text_normal)

    # Même offre avec n8n et zapier
    text_penalty = "Stage Machine Learning avec PyTorch et Docker, utilisation de n8n et zapier."
    score_penalty = scorer.keywords_score(text_penalty)

    assert score_penalty < score_normal
    assert score_normal - score_penalty >= 15.0



