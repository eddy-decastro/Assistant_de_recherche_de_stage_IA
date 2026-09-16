"""Tests de l'hygiène de base : dédoublonnage et re-validation métier complète.

Aucun accès réseau ni base externe : les fonctions de dédoublonnage sont pures et
la re-validation ne fait que lire un texte.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scrapers.base import describe_rejection
from scrapers.models import ScraperConfig
from src.constants import STATUS_REJECTED
from src.storage.cleanup import (
    canonical_url,
    choose_keeper,
    completeness,
    find_duplicate_groups,
    normalize_text,
    title_similarity,
)
from src.storage.database import Database


def _job(title: str, company: str, url: str, description: str = "", **extra: object) -> dict:
    """Fiche d'offre minimale pour les tests de dédoublonnage."""
    job = {
        "id": f"{company}-{title}-{url}",
        "title": title,
        "company": company,
        "url": url,
        "description": description,
        "final_score": 0.0,
        "rerank_score": None,
    }
    job.update(extra)
    return job


def test_normalisation_et_similarite() -> None:
    """Normalisation (accents/casse/ponctuation) et similarité de titres."""
    assert normalize_text("STAGE - Ingénieur R&D (F/H)") == "stage ingenieur r d f h"
    # Identiques après normalisation : espaces multiples, suffixe (F/H) <-> H/F, et
    # préfixe de contrat (« Stage ») neutralisé.
    assert title_similarity("Stage Data Scientist", "stage  data   scientist") == 1.0
    assert title_similarity("STAGE - Data Scientist (F/H)", "Stage Data Scientist H/F") == 1.0
    assert title_similarity("Data Scientist", "Stage Data Scientist") == 1.0
    # Calibration du seuil sur des cas RÉELS : même offre (titre amputé de son
    # suffixe) vs offres distinctes de la même entreprise.
    same_offer = ("STAGE - Ingénieur.e Recherche Deep Learning (F/H)",
                  "STAGE - Ingénieur.e Recherche Deep Learning")
    other_offer = ("STAGE - Ingénieur.e Recherche Deep Learning (F/H)",
                   "STAGE - Ingénieur.e Recherche Machine learning (F/H)")
    assert title_similarity(*same_offer) > 0.95, title_similarity(*same_offer)
    assert title_similarity(*other_offer) < 0.95, title_similarity(*other_offer)
    # Le niveau (junior/senior) reste discriminant : pas de fusion abusive.
    assert title_similarity("Data Scientist", "Stage Data Scientist Junior") < 0.95
    assert title_similarity("Stage Data Scientist", "Alternance Marketing") < 0.5
    assert title_similarity("", "Stage") == 0.0
    print("  dédoublonnage : normalisation et similarité de titres OK")


def test_url_canonique() -> None:
    """L'URL canonique écarte hôte ``www.``, query, fragment et slash final."""
    assert (
        canonical_url("https://www.LinkedIn.com/jobs/view/stage-123/?refId=x#top")
        == "linkedin.com/jobs/view/stage-123"
    )
    assert canonical_url("") == ""
    print("  dédoublonnage : URL canonique OK")


def test_groupes_de_doublons() -> None:
    """Même URL canonique OU même entreprise + titre proche ⇒ un seul groupe."""
    jobs = [
        _job("Stage Data Scientist (F/H)", "Doctolib", "https://x.com/1", "Description A" * 40),
        _job("Stage Data Scientist (H/F)", "Doctolib", "https://x.com/2", "court"),
        _job("STAGE Data Scientist F/H", "Doctolib", "https://x.com/3", ""),
        _job("Stage NLP et Computer Vision", "Doctolib", "https://x.com/4", ""),
        _job("Stage Data Scientist (F/H)", "Capgemini", "https://x.com/5", ""),
        _job("Stage Data Scientist (F/H)", "Doctolib", "https://x.com/1/?utm_source=linkedin", ""),
    ]
    groups = find_duplicate_groups(jobs)

    assert len(groups) == 1, [len(group) for group in groups]
    assert len(groups[0]) == 4, [job["title"] for job in groups[0]]
    keeper, duplicates = choose_keeper(groups[0])
    assert keeper["description"].startswith("Description A"), "La fiche la plus complète est conservée."
    assert len(duplicates) == 3
    print("  dédoublonnage : groupes (URL + entreprise/titre) et fiche conservée OK")


def test_aucun_doublon() -> None:
    """Aucune fusion abusive : entreprise différente ou titre distinct."""
    jobs = [
        _job("Stage Data Scientist", "Doctolib", "https://x.com/1"),
        _job("Stage Data Scientist", "Alan", "https://x.com/2"),
        _job("Stage NLP et Computer Vision", "Doctolib", "https://x.com/3"),
    ]
    assert find_duplicate_groups(jobs) == [], "Ni l'entreprise ni le titre ne concordent."
    print("  dédoublonnage : aucune fusion abusive OK")


def test_completude() -> None:
    """La complétude privilégie la description, puis le verdict LLM, puis le score."""
    pauvre = _job("A", "X", "u1", "")
    riche = _job("A", "X", "u1", "texte" * 50)
    juge = _job("A", "X", "u1", "texte" * 50, rerank_score=90.0)
    assert completeness(riche) > completeness(pauvre)
    assert completeness(juge) > completeness(riche)
    print("  dédoublonnage : clé de complétude OK")

def test_revalidation_faux_stages() -> None:
    """La re-validation écarte les faux stages détectés dans le corps de la fiche."""
    config = ScraperConfig()

    reason = describe_rejection(
        "Data Scientist - Statistics & Data Science",
        "Freelance assignment, modelling mission with PyTorch and time series.",
        config,
    )
    assert reason.startswith("contrat incompatible"), reason

    assert describe_rejection(
        "Data Scientist IA",
        "Poste en alternance uniquement, rythme 3j/2j, machine learning.",
        config,
    ).startswith("contrat incompatible")

    assert describe_rejection(
        "Data Scientist",
        "Nous recrutons en CDI pour renforcer l'équipe machine learning.",
        config,
    ).startswith("contrat incompatible")

    assert describe_rejection(
        "Data Scientist",
        "Vous produirez des tableaux de bord Power BI et du reporting Excel.",
        config,
    ).startswith("orientation BI")

    assert describe_rejection(
        "Chargé de mission",
        "Gestion de projet, relation client et suivi administratif.",
        config,
    ) == "aucun signal Data Science / ML dans la fiche"
    print("  re-validation : faux stages, alternance exclusive et BI écartés OK")


def test_revalidation_preserve_les_vrais_stages() -> None:
    """Aucun faux positif : une mention de CDI dans une annonce de stage est tolérée."""
    config = ScraperConfig()

    assert describe_rejection(
        "STAGE - Ingénieur.e Recherche Deep Learning (F/H)",
        "Stage de fin d'études de 6 mois, possibilité d'embauche en CDI à l'issue. "
        "Modélisation PyTorch, publications et expérimentations.",
        config,
    ) == ""

    assert describe_rejection(
        "Stage Data Scientist - alternance possible",
        "Nous accueillons des stagiaires en apprentissage ou alternance. Deep learning et NLP.",
        config,
    ) == ""

    assert describe_rejection(
        "STAGE Ingénieur R&D IA (F/H)",
        "Développement de modèles de machine learning, PyTorch et scikit-learn.",
        config,
    ) == ""
    print("  re-validation : vrais stages préservés (aucun faux positif) OK")


def test_base_rejet_et_suppression() -> None:
    """reject_job marque le statut + le motif ; delete_jobs ne touche que le lot."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "cleanup.db")
        for title, url, description in (("Stage A", "https://x/a", "PyTorch"), ("Stage B", "https://x/b", "")):
            db.upsert_job(
                {
                    "title": title,
                    "company": "Doctolib",
                    "url": url,
                    "description": description,
                    "source": "linkedin",
                    "company_tier": 1,
                    "semantic_score": 10.0,
                    "final_score": 10.0,
                }
            )
        rows = {job["title"]: job for job in db.get_jobs()}

        assert db.reject_job(rows["Stage B"]["id"], "contrat incompatible (« freelance »)") is True
        assert db.reject_job("identifiant-inexistant", "x") is False
        assert db.count_jobs(STATUS_REJECTED) == 1
        rejected = db.get_jobs(statuses=[STATUS_REJECTED])
        assert rejected[0]["title"] == "Stage B"
        assert rejected[0]["rejection_reason"].startswith("contrat incompatible")

        assert db.delete_jobs([rows["Stage A"]["id"]]) == 1
        assert db.count_jobs() == 1
        assert db.delete_jobs([]) == 0
        db.engine.dispose()
    print("  base : reject_job (motif conservé) et delete_jobs OK")


if __name__ == "__main__":
    test_normalisation_et_similarite()
    test_url_canonique()
    test_groupes_de_doublons()
    test_aucun_doublon()
    test_completude()
    test_revalidation_faux_stages()
    test_revalidation_preserve_les_vrais_stages()
    test_base_rejet_et_suppression()
    print("TOUS LES TESTS PASSENT")
