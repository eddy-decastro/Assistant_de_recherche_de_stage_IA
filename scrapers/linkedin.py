"""Scraper public invité LinkedIn (sans compte connecté).

Utilise l'endpoint invité ``seeMoreJobPostings`` et parse le fragment HTML
avec BeautifulSoup (backend lxml). Une pause aléatoire est insérée entre les
pages pour respecter les serveurs ; l'erreur 429 est gérée proprement.

Pagination : l'endpoint invité ne renvoie pas 25 cartes mais **10 par appel**.
Le pas d'avancement est déduit du *nombre réel de cartes reçues* (``start +=
len(cartes)``), ce qui évite de sauter des offres et reste correct si LinkedIn
change la taille de page. Le moteur commun (``BaseScraper._collect_pass``) arrête
la pagination sur page stagnante, plafond de pages, quota ou erreur, et consigne
la raison exacte de chaque arrêt.
"""

from __future__ import annotations

import random
import re
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from bs4 import BeautifulSoup

from .base import BaseScraper, markup_to_text
from .cache import DiskCache
from .models import CardEntry, PageResult, PassPlan, RawJob, ScraperConfig

GUEST_ENDPOINT = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
# Page détail invitée : elle expose la description COMPLÈTE, absente des cartes de
# résultat (vérifié : HTTP 200 et ``div.description__text`` présent).
DETAIL_ENDPOINT = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
CACHE_NAMESPACE = "linkedin"
LOCATION = "France"
SLEEP_RANGE = (2.0, 4.0)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _clean_url(value: str | None) -> str:
    if not value:
        return ""
    return value.split("?")[0].strip()


# Identifiant numérique d'une offre : ``/jobs/view/<slug>-<id>``,
# ``urn:li:jobPosting:<id>`` ou ``?currentJobId=<id>``.
_JOB_ID_PATTERNS = (
    re.compile(r"/jobs/view/(?:[^/?#]*?-)?(\d{6,})"),
    re.compile(r"jobPosting:(\d{6,})"),
    re.compile(r"[?&]currentJobId=(\d{6,})"),
)

# Conteneurs de la description sur la page détail invitée (repli en cascade).
_DESCRIPTION_SELECTORS = (
    "div.show-more-less-html__markup",
    "div.description__text",
    "section.description",
)


def extract_job_id(value: str | None) -> str:
    """Extrait l'identifiant numérique d'une offre depuis une URL (ou un urn)."""
    text = (value or "").strip()
    if text.isdigit():
        return text
    for pattern in _JOB_ID_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1)
    return ""


def parse_description(html_text: str) -> str:
    """Extrait le texte de la description depuis la page détail invitée."""
    if not html_text:
        return ""
    soup = BeautifulSoup(html_text, "lxml")
    for selector in _DESCRIPTION_SELECTORS:
        node = soup.select_one(selector)
        if node is None:
            continue
        text = markup_to_text(node)
        if text:
            return text
    return ""


class LinkedInGuestScraper(BaseScraper):
    """Récupère les cartes d'offres publiques LinkedIn (mode invité).

    Capacités mesurées par sonde (``tools/probe_sources.py``, 16/09/2026) :

    * le filtre temporel serveur ``f_TPR`` **fonctionne** (``r86400`` ⇒ 10/10
      cartes publiées le jour même ; ``r604800`` ⇒ 10/10 dans la semaine) : c'est
      lui qui garantit la fraîcheur ;
    * l'ordre renvoyé n'est **pas** chronologique (``sortBy=DD`` sans ``f_TPR``
      renvoie exactement le même ordre que la pertinence, et 7 inversions de date
      ont été mesurées sur 2 pages) : l'arrêt anticipé est donc désactivé par
      défaut (``DATE_ORDER_RELIABLE = False``) et le coût de la passe « Fraîcheur »
      est borné par la fenêtre serveur, le quota et le plafond de pages.
    """

    source = "linkedin"
    #: Mesuré : NON (voir le docstring ci-dessus).
    DATE_ORDER_RELIABLE = False
    #: Mesuré : OUI (``f_TPR``).
    SERVER_WINDOW_FILTER = True

    def __init__(self, config: ScraperConfig | None = None) -> None:
        super().__init__(config)
        self.client.headers.update(
            {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8",
            }
        )

    # ------------------------------------------------------------------ #
    # Paramètres de recherche par mode
    # ------------------------------------------------------------------ #
    def _search_params(self, mode: str, plan: PassPlan) -> dict[str, Any]:
        """Paramètres LinkedIn d'une passe : tri et filtre temporel serveur.

        * mode « Fraîcheur » : ``sortBy=DD`` (tri par date demandé) et, si la
          fenêtre est active, ``f_TPR=r<secondes>`` — c'est ce filtre qui borne le
          vivier à la fenêtre, indépendamment de l'ordre réellement renvoyé ;
        * mode « Rattrapage » : aucun ``sortBy``, la plateforme applique son
          classement par pertinence (vérifié : ordre différent de ``sortBy=DD``
          dès qu'un filtre temporel est présent).
        """
        params: dict[str, Any] = {}
        if plan.sort == "date":
            params["sortBy"] = "DD"
        if plan.use_server_window_filter and plan.window_days:
            seconds = int(plan.window_days * 86400)
            params["f_TPR"] = f"r{seconds}"
        return params

    def _iter_pages(
        self, query: str, mode: str, cursor: Any, plan: PassPlan
    ) -> PageResult:
        """Une page de cartes LinkedIn (10 cartes par appel en mode invité)."""
        start = int(cursor or 0)
        if start > 0:  # respect des serveurs : pause uniquement entre deux pages
            time.sleep(random.uniform(*SLEEP_RANGE))
        params = {
            "keywords": query,
            "location": LOCATION,
            "f_JT": "I",  # stage / internship
            "start": start,
            **self._search_params(mode, plan),
        }
        response = self.client.get(GUEST_ENDPOINT, params=params)
        response.raise_for_status()
        jobs, card_keys = self._parse_cards_page(response.text)
        # Les cartes inexploitables doivent compter dans l'avancement de la
        # pagination : la clé est associée à sa carte quand elle existe.
        by_key = {(job.id_externe or job.url): job for job in jobs}
        entries = [CardEntry(key=key, job=by_key.get(key)) for key in card_keys]
        return PageResult(
            entries=entries,
            # Pas réel = nombre de cartes reçues (plus de saut d'offres si
            # LinkedIn change la taille de page).
            next_cursor=start + len(card_keys) if card_keys else None,
            exhausted=not card_keys,
            http_calls=1,
        )

    @staticmethod
    def _parse_cards(html_text: str) -> list[RawJob]:
        """Extrait les offres d'un fragment HTML (compatibilité historique)."""
        jobs, _ = LinkedInGuestScraper._parse_cards_page(html_text)
        return jobs


    @staticmethod
    def _parse_cards_page(html_text: str) -> tuple[list[RawJob], list[str]]:
        """Parse un fragment HTML et retourne ``(offres, clés des cartes)``.

        La clé d'une carte est son ``data-entity-urn`` (repli : l'URL nettoyée).
        Elle est collectée même si la carte est inexploitable, ce qui permet de
        détecter une page déjà vue sans jamais boucler indéfiniment.
        """
        soup = BeautifulSoup(html_text, "lxml")
        jobs: list[RawJob] = []
        card_keys: list[str] = []

        for card in soup.select("li"):
            title_el = card.select_one(".base-search-card__title")
            company_el = card.select_one(".base-search-card__subtitle") or card.select_one(
                ".hidden-nested-link"
            )
            location_el = card.select_one(".job-search-card__location")
            link_el = card.select_one("a.base-card__full-link")
            time_el = card.select_one("time")
            urn_el = card.select_one("[data-entity-urn]")

            title = title_el.get_text(strip=True) if title_el else ""
            company = company_el.get_text(strip=True) if company_el else ""
            url = _clean_url(link_el.get("href") if link_el else "")

            # ``data-entity-urn`` est porté par la div interne de la carte
            # (format ``urn:li:jobPosting:<id>``), et non par le <li> lui-même.
            entity_urn = (urn_el.get("data-entity-urn") if urn_el else "") or ""
            id_externe = entity_urn.split(":")[-1] if entity_urn else url
            card_key = id_externe or url
            if card_key:
                card_keys.append(card_key)

            if not title or not company:
                continue

            location = location_el.get_text(strip=True) if location_el else ""
            published_at = _parse_datetime(time_el.get("datetime") if time_el else None)

            jobs.append(
                RawJob(
                    id_externe=id_externe or url,
                    source="linkedin",
                    title=title,
                    company=company,
                    location=location,
                    url=url,
                    # Les cartes invitées n'exposent pas la description complète ;
                    # le filtrage anti-BI s'appuiera donc principalement sur le titre.
                    description="",
                    published_at=published_at,
                    is_internship=True,
                )
            )
        return jobs, card_keys

    def fetch_description(self, url_or_id: str, cache: DiskCache | None = None) -> str:
        """Récupère la description complète d'une offre (page détail invitée).

        Le texte n'est mis en cache que s'il est récupéré (un échec de parsing ne
        « gèle » pas une réponse vide). Les erreurs HTTP sont propagées sous forme
        de ``httpx.HTTPStatusError`` afin que l'appelant distingue un 429 d'une
        description simplement absente.
        """
        job_id = extract_job_id(url_or_id)
        if not job_id:
            self._logger.warning("LinkedIn : identifiant d'offre introuvable dans %r.", url_or_id)
            return ""
        if cache is not None:
            cached = cache.get(CACHE_NAMESPACE, job_id)
            if cached is not None:
                return cached

        response = self.client.get(DETAIL_ENDPOINT.format(job_id=job_id))
        response.raise_for_status()
        description = parse_description(response.text)
        if description and cache is not None:
            cache.set(CACHE_NAMESPACE, job_id, description)
        return description
