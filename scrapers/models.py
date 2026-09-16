"""Modèles de données et configuration du module de scraping unifié.

Ce module définit :
  - le modèle Pydantic ``RawJob`` (offre brute normalisée, indépendante de la source) ;
  - la configuration ``ScraperConfig`` (filtrage métier + paramètres réseau) ;
  - les listes de mots-clés d'exclusion / positifs et les requêtes cibles.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Identifiant de source normalisé (wttj | linkedin | jobteaser).
Source = Literal["wttj", "linkedin", "jobteaser"]

# --------------------------------------------------------------------------- #
# Filtrage métier
# --------------------------------------------------------------------------- #
# Mots-clés rédhibitoires : toute offre orientée BI / reporting / analyste
# classique doit être écartée (test effectué sur le titre ET le résumé).
EXCLUSION_KEYWORDS: list[str] = [
    "power bi",
    "tableau",
    "qlik",
    "vba",
    "excel reporting",
    "data analyst",
    "business intelligence",
    "bi analyst",
    "chargé de reporting",
    "stage bi",
]

# Requêtes principales envoyées aux sources.
TARGET_QUERIES: list[str] = [
    "Stage Data Scientist",
    "Stage Machine Learning",
    "Stage Recherche IA",
]

# Signaux positifs exigés pour considérer une offre comme "Data Science / ML".
POSITIVE_DS_ML_KEYWORDS: list[str] = [
    "data scientist",
    "data science",
    "machine learning",
    "deep learning",
    "ml engineer",
    "mlops",
    "ml ops",
    "intelligence artificielle",
    "artificial intelligence",
    "nlp",
    "computer vision",
    "vision par ordinateur",
    "pytorch",
    "tensorflow",
    "scikit-learn",
    "neural network",
    "réseau de neurones",
    "llm",
    "rag",
    "recherche",
    "research",
    "r&d",
    "data engineer",
    "moteur de recommandation",
]

# User-Agent moderne partagé par tous les scrapers.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class RawJob(BaseModel):
    """Offre brute normalisée, indépendante de la source de collecte."""

    model_config = ConfigDict(extra="ignore")

    id_externe: str
    source: Source
    title: str
    company: str
    location: str
    url: str
    description: str
    published_at: datetime | None = None
    is_internship: bool = True


class ScraperConfig(BaseModel):
    """Configuration du module de scraping (filtrage métier + réseau)."""

    model_config = ConfigDict(extra="ignore")

    exclusion_keywords: list[str] = Field(default_factory=lambda: list(EXCLUSION_KEYWORDS))
    target_queries: list[str] = Field(default_factory=lambda: list(TARGET_QUERIES))
    positive_ds_ml_keywords: list[str] = Field(
        default_factory=lambda: list(POSITIVE_DS_ML_KEYWORDS)
    )
    request_timeout_seconds: float = 30.0
    user_agent: str = DEFAULT_USER_AGENT
    enabled_sources: list[Source] = Field(
        default_factory=lambda: ["wttj", "linkedin", "jobteaser"]
    )
    max_offers_per_source: int = 50


class ScrapeResult(BaseModel):
    """Résultat d'un scraping : offres validées + compteurs pour le résumé."""

    jobs: list[RawJob] = Field(default_factory=list)
    found: int = 0          # offres brutes récupérées (avant filtrage)
    rejected_bi: int = 0    # offres rejetées par le filtre anti-BI / DS-ML
