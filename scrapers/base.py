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
from bs4 import BeautifulSoup

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


def markup_to_text(node: Any, separator: str = "\n") -> str:
    """Convertit un fragment HTML (chaîne ou balise BeautifulSoup) en texte lisible.

    Les ``<br>`` deviennent des sauts de ligne et les blocs (``<p>``, ``<li>``,
    ``<div>``…) sont séparés par ``separator`` ; les lignes vides consécutives
    sont supprimées. Indispensable en aval : la description part telle quelle
    vers le modèle d'embedding puis vers le juge LLM, elle doit rester structurée.
    """
    parsed = BeautifulSoup(node, "lxml") if isinstance(node, str) else node
    for br in parsed.find_all("br"):
        br.replace_with("\n")
    raw = parsed.get_text(separator, strip=True)
    lines = [line.strip() for line in raw.splitlines()]
    return separator.join(line for line in lines if line)


# --- Re-validation métier sur texte COMPLET (titre + description) ------------ #
# Marqueurs explicites de contrat NON-stage. Deux niveaux :
#   « durs »  : la mention suffit à écarter l'offre (freelance, alternance exclusive…) ;
#   « doux »  : la mention n'écarte que si le TITRE n'annonce pas déjà un stage
#               (une annonce « Stage … possibilité de CDI à l'issue » reste un stage).
NON_INTERNSHIP_HARD_MARKERS: tuple[str, ...] = (
    "freelance",
    "alternance uniquement",
    "uniquement en alternance",
    "alternance exclusivement",
    "apprentissage uniquement",
    "rythme 3j/2j",
    "rythme 2j/3j",
    "rythme 3 jours / 2 jours",
    "contrat cdi",
    "poste en cdi",
    "cdi uniquement",
)
NON_INTERNSHIP_SOFT_MARKERS: tuple[str, ...] = (
    "cdi",
    "cdd",
    "alternance",
    "apprentissage",
)
# Signaux de stage recherchés dans le TITRE (ce que le candidat lit en premier).
INTERNSHIP_TITLE_CUES: tuple[str, ...] = (
    "stage",
    "stagiaire",
    "intern",
    "internship",
    "pfmp",
)


def describe_rejection(title: str, description: str, config: ScraperConfig) -> str:
    """Motif d'exclusion d'une offre lue en entier (``""`` ⇒ offre retenue).

    Complète :func:`BaseScraper.is_valid_job` (qui ne voit, à la collecte, que le
    titre et le résumé de la page de liste) en appliquant les règles sur le texte
    COMPLET récupéré depuis les pages détail :

      1. contrat incompatible explicitement mentionné (freelance, CDI, alternance
         exclusive, rythme 3j/2j…) ;
      2. orientation BI / reporting détectée dans le corps de l'offre ;
      3. absence de tout signal Data Science / ML.
    """
    title_low = (title or "").casefold()
    combined = f"{title_low}\n{(description or '').casefold()}"
    announced_as_internship = any(cue in title_low for cue in INTERNSHIP_TITLE_CUES)

    for marker in NON_INTERNSHIP_HARD_MARKERS:
        if marker in combined:
            return f"contrat incompatible (« {marker} »)"

    if not announced_as_internship:
        for marker in NON_INTERNSHIP_SOFT_MARKERS:
            if _contains_keyword(combined, marker):
                return f"contrat incompatible (« {marker} »), non annoncé comme un stage"

    for keyword in config.exclusion_keywords:
        if _contains_keyword(combined, keyword):
            return f"orientation BI / reporting (« {keyword} »)"

    if not any(_contains_keyword(combined, keyword) for keyword in config.positive_ds_ml_keywords):
        return "aucun signal Data Science / ML dans la fiche"

    return ""


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
