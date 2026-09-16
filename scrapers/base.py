"""Classe de base des scrapers : client HTTP partagé + filtre anti-BI.

Fournit :
  - ``BaseScraper`` (classe abstraite) avec un ``httpx.Client`` pré-configuré
    (timeout, User-Agent moderne, gestion des redirections) ;
  - la méthode concrète ``is_valid_job`` qui implémente le filtrage métier
    (exclusion stricte des offres BI/reporting + exigence d'un signal DS/ML).
"""

from __future__ import annotations

import logging
import os
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar

import httpx

from .models import RawJob, ScrapeResult, ScraperConfig, Source


def load_env_file(path: str | Path | None = None) -> None:
    """Charge les variables d'un fichier ``.env`` dans ``os.environ`` (sans écraser).

    Implémentation minimale et autonome (pas de dépendance à python-dotenv).
    """
    env_path = Path(path) if path else Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def _contains_keyword(text: str, keyword: str) -> bool:
    """Recherche un mot-clé de façon tolérante (frontières de mot pour les tokens simples).

    Les expressions multi-mots sont cherchées en sous-chaîne ; les tokens simples
    (``vba``, ``qlik``, ``tableau``…) utilisent une frontière de mot pour éviter
    les faux positifs (ex. ``vba`` dans un mot comme ``advbance``).
    """
    kw = keyword.casefold()
    lowered = text.casefold()
    if " " in kw:
        return kw in lowered
    return re.search(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])", lowered) is not None


class BaseScraper(ABC):
    """Contrat commun à tous les scrapers de sources d'offres."""

    source: ClassVar[Source]

    def __init__(self, config: ScraperConfig | None = None) -> None:
        self.config = config or ScraperConfig()
        self._logger = logging.getLogger(f"scrapers.{self.source}")
        self.client = httpx.Client(
            headers={
                "User-Agent": self.config.user_agent,
                "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
                "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
            },
            timeout=self.config.request_timeout_seconds,
            follow_redirects=True,
        )

    # ------------------------------------------------------------------ #
    # Filtrage métier
    # ------------------------------------------------------------------ #
    def is_valid_job(self, title: str, description: str) -> bool:
        """Retourne ``False`` si l'offre est BI/reporting, ``True`` si DS/ML.

        Règles :
          1. rejet immédiat si un mot-clé d'exclusion est présent dans le titre
             OU le résumé (BI, Power BI, data analyst, reporting…) ;
          2. acceptation seulement si au moins un signal positif Data Science /
             Machine Learning est détecté dans le titre ou le résumé.
        """
        title_low = (title or "").casefold()
        description_low = (description or "").casefold()

        for keyword in self.config.exclusion_keywords:
            if _contains_keyword(title_low, keyword) or _contains_keyword(description_low, keyword):
                return False

        combined = f"{title_low} {description_low}"
        return any(
            _contains_keyword(combined, keyword)
            for keyword in self.config.positive_ds_ml_keywords
        )

    def validate(self, job: RawJob) -> bool:
        """Filtre complet : offre de stage + anti-BI + signal DS/ML."""
        return job.is_internship and self.is_valid_job(job.title, job.description)

    # ------------------------------------------------------------------ #
    # Cycle de vie
    # ------------------------------------------------------------------ #
    @abstractmethod
    def fetch(self) -> ScrapeResult:
        """Récupère et normalise les offres brutes de la source."""

    def run(self) -> ScrapeResult:
        """Lance le scraping et applique le filtre métier sur les offres récupérées."""
        result = self.fetch()
        validated: list[RawJob] = []
        rejected = 0
        for job in result.jobs:
            if self.validate(job):
                validated.append(job)
            else:
                rejected += 1
                self._logger.debug("Offre rejetée (anti-BI/DS-ML) : %s", job.title)
        result.jobs = validated
        result.rejected_bi += rejected
        self._logger.info(
            "%s : %d offre(s) récupérée(s) -> %d validée(s), %d rejetée(s).",
            self.source,
            result.found,
            len(validated),
            rejected,
        )
        return result

    def close(self) -> None:
        """Libère le client HTTP sous-jacent."""
        self.client.close()

    def __enter__(self) -> "BaseScraper":
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.close()
