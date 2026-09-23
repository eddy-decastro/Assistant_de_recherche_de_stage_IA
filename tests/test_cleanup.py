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
    companies_match,
    completeness,
    find_duplicate_groups,
    normalize_company,
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


def test_normalize_company() -> None:
    """Normalisation avancée du nom d'entreprise et rattachement des filiales."""
    # Casse, accents et ponctuation
    assert normalize_company("CRÉDIT AGRICOLE CIB!") == "credit agricole"
    assert normalize_company("Groupe Crédit Agricole") == "credit agricole"
    assert normalize_company("Crédit Agricole CIB") == "credit agricole"
    assert normalize_company("Crédit Agricole Corporate and Investment Bank") == "credit agricole"

    # Thales et filiales
    assert normalize_company("Thales") == "thales"
    assert normalize_company("Thales DIS") == "thales"
    assert normalize_company("Thales DIS France SAS") == "thales"
    assert normalize_company("Groupe Thales") == "thales"
    assert normalize_company("Thales Alenia Space") == "thales"

    # Airbus et filiales
    assert normalize_company("Airbus") == "airbus"
    assert normalize_company("Airbus Helicopters") == "airbus"
    assert normalize_company("Airbus Helicopters France") == "airbus"
    assert normalize_company("Airbus Defence and Space") == "airbus"
    assert normalize_company("Airbus Group") == "airbus"

    # Autres groupes et formes juridiques
    assert normalize_company("Dassault Systèmes") == "dassault"
    assert normalize_company("Capgemini Technology Services") == "capgemini"
    assert normalize_company("BNP Paribas CIB") == "bnp paribas"
    assert normalize_company("Société Générale CIB") == "societe generale"
    assert normalize_company("TotalEnergies") == "total"
    assert normalize_company("Total Energies") == "total"
    assert normalize_company("SNCF Connect") == "sncf"

    # Replis et cas limites (préservation si uniquement générique ou chiffres)
    assert normalize_company("Services") == "services"
    assert normalize_company("Solutions 30") == "solutions 30"
    assert normalize_company("") == ""
    print("  dédoublonnage : normalisation d'entreprise et filiales OK")


def test_companies_match() -> None:
    """Vérification de la correspondance d'entreprises (exacte ou inclusion de marque)."""
    # Correspondance après normalisation
    assert companies_match("Crédit Agricole CIB", "Groupe Crédit Agricole") is True
    assert companies_match("Thales DIS", "Thales") is True
    assert companies_match("Airbus Helicopters", "Airbus") is True

    # Inclusion de marque (division non répertoriée formant un préfixe valide)
    assert companies_match("Airbus", "Airbus Atlantic") is True
    assert companies_match("Thales", "Thales Avionics") is True
    assert companies_match("Orange", "Orange Bank") is True

    # Non-correspondance (entreprises différentes ou collisions évitées)
    assert companies_match("Doctolib", "Alan") is False
    assert companies_match("Apple", "Pineapple") is False
    assert companies_match("Car", "Carrefour") is False
    assert companies_match("", "Thales") is False
    print("  dédoublonnage : correspondance d'entreprises (exacte / inclusion) OK")


def test_tokens_generiques_enrichis() -> None:
    """Prise en compte des variantes fréquentes (PFE, fin d'études, césure, master, bac+5, etc.)."""
    assert title_similarity("Stage PFE Data Scientist", "Data Scientist") == 1.0
    assert title_similarity("Stage de fin d'études - Data Scientist", "Stage Data Scientist") == 1.0
    assert title_similarity("Stage Césure - Data Scientist (6 mois)", "Data Scientist M/W/D") == 1.0
    assert title_similarity("Stage Bac+5 Data Scientist", "Stage Data Scientist") == 1.0
    assert title_similarity("Stage Bac5 Data Scientist", "Data Scientist") == 1.0
    assert title_similarity("Stage Master Data Scientist", "Data Scientist") == 1.0
    assert title_similarity("Internship Data Scientist", "Stage Data Scientist") == 1.0
    assert title_similarity("Apprentissage Data Scientist", "Alternance Data Scientist") == 1.0

    # Tolérance des variations de titre avec seuil assoupli à 0.70
    sim_ia = title_similarity("Stage Data Scientist - NLP & LLM (F/H)", "Data Scientist - LLM (H/F)")
    assert sim_ia >= 0.70, sim_ia
    sim_dl = title_similarity(
        "Stage R&D - Machine Learning & Deep Learning (F/H)",
        "Stage Ingénieur R&D - Machine Learning",
    )
    assert sim_dl >= 0.70, sim_dl
    print("  dédoublonnage : tokens génériques enrichis et seuil assoupli OK")


def test_deduplication_multiplateformes() -> None:
    """Regroupement multi-plateformes réaliste (LinkedIn, WTTJ, JobTeaser)."""
    jobs = [
        # Groupe 1 : Crédit Agricole (LinkedIn vs Welcome to the Jungle)
        _job(
            title="Stage Data Scientist - NLP & LLM (F/H)",
            company="Crédit Agricole CIB",
            url="https://www.linkedin.com/jobs/view/1001",
            description="Mission complète en modélisation NLP et LLM." * 10,
            final_score=70.0,
        ),
        _job(
            title="Data Scientist - LLM (H/F)",
            company="Groupe Crédit Agricole",
            url="https://www.welcometothejungle.com/fr/companies/credit-agricole/jobs/2001",
            description="Mission NLP courte.",
            final_score=65.0,
        ),
        # Groupe 2 : Thales (JobTeaser vs LinkedIn avec Deep Learning ajouté)
        _job(
            title="Stage Ingénieur R&D - Machine Learning",
            company="Thales",
            url="https://www.jobteaser.com/fr/job-offers/3001",
            description="Stage R&D Machine Learning et modèles de fondation.",
            final_score=60.0,
            rerank_score=88.0,
        ),
        _job(
            title="Stage R&D - Machine Learning & Deep Learning (F/H)",
            company="Thales DIS",
            url="https://www.linkedin.com/jobs/view/3002",
            description="Stage R&D Machine Learning et modèles de fondation.",
            final_score=75.0,
            rerank_score=None,
        ),
        # Groupe 3 : Airbus (Airbus Helicopters vs Airbus)
        _job(
            title="Stage Deep Learning Computer Vision",
            company="Airbus Helicopters",
            url="https://airbus.com/jobs/4001",
            description="Vision par ordinateur PyTorch.",
            final_score=80.0,
        ),
        _job(
            title="Stage Deep Learning Computer Vision (6 mois)",
            company="Airbus",
            url="https://www.linkedin.com/jobs/view/4002",
            description="Vision par ordinateur PyTorch.",
            final_score=80.0,
        ),
        # Offres distinctes (ne doivent PAS être fusionnées)
        _job(
            title="Stage Data Scientist",
            company="Doctolib",
            url="https://doctolib.com/jobs/5001",
            description="Data Science santé.",
            final_score=85.0,
        ),
        _job(
            title="Stage Juriste Droit des Affaires",
            company="Groupe Crédit Agricole",
            url="https://credit-agricole.com/jobs/6001",
            description="Droit des sociétés.",
            final_score=10.0,
        ),
    ]

    groups = find_duplicate_groups(jobs)
    assert len(groups) == 3, f"Attendu 3 groupes de doublons, obtenu {len(groups)}"

    # Vérification groupe Crédit Agricole
    ca_group = next(g for g in groups if any("Crédit Agricole" in j["company"] for j in g))
    assert len(ca_group) == 2
    keeper_ca, duplicates_ca = choose_keeper(ca_group)
    assert keeper_ca["company"] == "Crédit Agricole CIB"
    assert "complète" in keeper_ca["description"]
    assert len(duplicates_ca) == 1

    # Vérification groupe Thales (priorité à l'évaluation LLM existante)
    thales_group = next(g for g in groups if any("Thales" in j["company"] for j in g))
    assert len(thales_group) == 2
    keeper_thales, duplicates_thales = choose_keeper(thales_group)
    assert keeper_thales["rerank_score"] == 88.0
    assert len(duplicates_thales) == 1

    # Vérification groupe Airbus
    airbus_group = next(g for g in groups if any("Airbus" in j["company"] for j in g))
    assert len(airbus_group) == 2

    print("  dédoublonnage : regroupement multi-plateformes réaliste OK")


def test_choose_keeper_avance() -> None:
    """Choix du keeper avec priorité description, rerank LLM existant et score R&D."""
    # 1. Priorité à la longueur de la description
    court = _job("T", "C", "u1", "court", rerank_score=95.0)
    long_desc = _job("T", "C", "u2", "description très détaillée" * 20, rerank_score=None)
    keeper1, _ = choose_keeper([court, long_desc])
    assert keeper1["url"] == "u2"
    # L'évaluation LLM du doublon court est préservée sur le keeper !
    assert keeper1["rerank_score"] == 95.0

    # 2. À longueur égale, priorité à l'offre avec rerank_score
    non_evalue = _job("T", "C", "u1", "texte", rerank_score=None, final_score=90.0)
    evalue = _job("T", "C", "u2", "texte", rerank_score=85.0, final_score=50.0)
    keeper2, _ = choose_keeper([non_evalue, evalue])
    assert keeper2["url"] == "u2"

    # 3. À longueur égale et toutes deux évaluées, le score R&D le plus haut gagne
    evalue_bas = _job("T", "C", "u1", "texte", rerank_score=65.0)
    evalue_haut = _job("T", "C", "u2", "texte", rerank_score=89.0)
    keeper3, _ = choose_keeper([evalue_bas, evalue_haut])
    assert keeper3["url"] == "u2"

    # 4. À longueur égale et aucune évaluée, final_score départage
    score_bas = _job("T", "C", "u1", "texte", final_score=50.0)
    score_haut = _job("T", "C", "u2", "texte", final_score=82.0)
    keeper4, _ = choose_keeper([score_bas, score_haut])
    assert keeper4["url"] == "u2"

    print("  dédoublonnage : complétude avancée (longueur, rerank LLM, score R&D) OK")


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
    test_normalize_company()
    test_companies_match()
    test_tokens_generiques_enrichis()
    test_deduplication_multiplateformes()
    test_choose_keeper_avance()
    test_url_canonique()
    test_groupes_de_doublons()
    test_aucun_doublon()
    test_completude()
    test_revalidation_faux_stages()
    test_revalidation_preserve_les_vrais_stages()
    test_base_rejet_et_suppression()
    print("TOUS LES TESTS PASSENT")
