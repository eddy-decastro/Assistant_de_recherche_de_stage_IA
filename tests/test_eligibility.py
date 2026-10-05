"""Une offre qui n'est pas un stage, ou qui exige de l'expérience, n'est pas retenue."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.eligibility import experience_reason, not_internship_reason

LONG = " Missions : modéliser des séries temporelles, déployer des modèles en production." * 4


@pytest.mark.parametrize(
    "title, description",
    [
        ("Data Scientist", "Rejoignez notre équipe data." + LONG),
        ("Lead Data Scientist", "Poste en CDI." + LONG),
    ],
)
def test_offer_without_any_internship_mention_is_not_an_internship(title, description):
    assert "stage" in not_internship_reason(title, description)


@pytest.mark.parametrize(
    "title, description",
    [
        ("Stage - Data Scientist", LONG),
        ("Data Scientist", "Stage de fin d'études de 6 mois." + LONG),
        ("Machine Learning Researcher", "This internship lasts six months." + LONG),
        ("Final Year Intership - Consultant", LONG),
        ("Data Scientist (Praktikant/in)", LONG),
        ("Quant Research Off-Cycle Analyst", LONG),
    ],
)
def test_internship_markers_in_title_or_description_keep_the_offer(title, description):
    assert not_internship_reason(title, description) == ""


def test_missing_or_short_description_is_not_enough_to_reject():
    assert not_internship_reason("Data Scientist", "") == ""
    assert not_internship_reason("Data Scientist", "Court résumé.") == ""


@pytest.mark.parametrize(
    "description",
    [
        "Vous justifiez d'au moins 2 ans d'expérience en data science.",
        "minimum 3 ans d'expérience sur des projets ML",
        "5+ years of experience with Python",
        "3 à 5 ans d'expérience",
        "Expérience de 4 ans minimum en machine learning",
        "At least 3 years experience in NLP",
    ],
)
def test_required_years_of_experience_reject_non_internship_offers(description):
    assert "expérience" in experience_reason("Data Scientist", description)


@pytest.mark.parametrize(
    "title", ["Senior Data Scientist", "Data Scientist confirmé H/F", "Lead Data Scientist"]
)
def test_senior_title_rejects_non_internship_offers(title):
    assert experience_reason(title, "") != ""


@pytest.mark.parametrize(
    "description",
    [
        "Plus de 130 ans d'expérience dans l'industrie automobile.",
        "Notre groupe, 50 ans d'expérience au service de ses clients.",
        "au moins 1 an d'expérience est un plus",
        "0-2 ans d'expérience",
        "Aucune expérience requise.",
    ],
)
def test_company_history_and_junior_wording_do_not_reject(description):
    assert experience_reason("Data Scientist junior", description) == ""


def test_internship_title_is_never_rejected_for_experience():
    assert experience_reason("Stage Senior Data Scientist", "3 ans d'expérience souhaités") == ""


def _score(job):
    from src.matching.scoring_v3 import compute_final_score

    parsed = {"contract_type": "STAGE", "duration_months": 6, "sub_scores": {}}
    return compute_final_score(parsed, job, {"scoring_v3": {}, "companies": {}})


def test_scoring_excludes_offer_that_is_not_an_internship():
    result = _score({"title": "Data Scientist", "description": "Équipe data, poste en CDI." + LONG})
    assert result.excluded
    assert "stage" in result.exclusion_reason


def test_scoring_excludes_offer_requiring_experience():
    result = _score(
        {"title": "Stage Data Scientist", "description": "stage." + LONG}
    )
    assert not result.excluded
    result = _score(
        {"title": "Data Scientist", "description": "Stage possible. 3 ans d'expérience requis." + LONG}
    )
    assert result.excluded
    assert "expérience" in result.exclusion_reason


def test_collection_filter_rejects_full_text_without_internship_and_with_experience():
    from scrapers.base import describe_rejection, screen_rejection
    from scrapers.models import ScraperConfig

    config = ScraperConfig()
    ml = " machine learning deep learning" + LONG
    assert "stage" in describe_rejection("Data Scientist", "Poste." + ml, config)
    assert "expérience" in screen_rejection("Data Scientist", "5+ years of experience." + ml, config)
    assert screen_rejection("Stage Data Scientist", "5+ years of experience." + ml, config) == ""
