"""Scraper public invité LinkedIn (sans compte connecté).

Utilise l'endpoint invité ``seeMoreJobPostings`` et parse le fragment HTML
avec BeautifulSoup (backend lxml). Une pause aléatoire est insérée entre les
pages pour respecter les serveurs ; l'erreur 429 est gérée proprement.

Pagination : l'endpoint invité ne renvoie pas 25 cartes mais **10 par appel**.
Le pas d'avancement est donc déduit du *nombre réel de cartes reçues*
(``start += len(cartes)``) au lieu d'une constante, ce qui évite de sauter des
offres et reste correct si LinkedIn change la taille de page. Une page dont
aucune carte n'est inédite termine la pagination (garde-fou anti-boucle).
"""

from __future__ import annotations

import random
import re
import time
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup

from .base import BaseScraper, markup_to_text
from .cache import DiskCache
from .models import RawJob, ScrapeResult, ScraperConfig

GUEST_ENDPOINT = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
# Page détail invitée : elle expose la description COMPLÈTE, absente des cartes de
# résultat (vérifié : HTTP 200 et ``div.description__text`` présent).
DETAIL_ENDPOINT = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
CACHE_NAMESPACE = "linkedin"
LOCATION = "France"
SLEEP_RANGE = (2.0, 4.0)
# Garde-fou : nombre maximal d'appels HTTP par requête cible (anti-boucle infinie).
MAX_PAGES_PER_QUERY = 60


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
    """Récupère les cartes d'offres publiques LinkedIn (mode invité)."""

    source = "linkedin"

    def __init__(self, config: ScraperConfig | None = None) -> None:
        super().__init__(config)
        self.client.headers.update(
            {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8",
            }
        )

    def _fetch_page(self, query: str, start: int) -> tuple[list[RawJob], list[str]]:
        """Récupère une page de cartes LinkedIn.

        Returns:
            ``(offres exploitables, clés de toutes les cartes reçues)``. Les clés
            servent à mesurer l'avancement de la pagination même lorsque des
            cartes sont inexploitables (titre ou entreprise manquants).
        """
        params = {
            "keywords": query,
            "location": LOCATION,
            "f_JT": "I",  # stage / internship
            "sortBy": "DD",
            "start": start,
        }
        response = self.client.get(GUEST_ENDPOINT, params=params)
        response.raise_for_status()
        return self._parse_cards_page(response.text)

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

    def fetch(self) -> ScrapeResult:
        jobs: list[RawJob] = []
        found = 0
        max_offers = self.config.max_offers_per_source
        quota = self.config.per_query_quota
        seen_ids: set[str] = set()  # déduplication inter-requêtes

        for query in self.config.target_queries:
            # Quota PAR REQUÊTE : une requête qui remplit son quota ne prive plus les
            # suivantes (l'ancien ``break`` global arrêtait la collecte dès que
            # ``max_offers_per_source`` était atteint par la première requête).
            collected_for_query = 0
            start = 0
            seen_page_keys: set[str] = set()  # détection de stagnation (par requête)

            for _page in range(MAX_PAGES_PER_QUERY):
                if collected_for_query >= quota or len(jobs) >= max_offers:
                    break
                try:
                    batch, card_keys = self._fetch_page(query, start)
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 429:
                        self._logger.warning(
                            "LinkedIn : 429 Too Many Requests (query=%r) — repli propre.", query
                        )
                    else:
                        self._logger.warning(
                            "LinkedIn : erreur HTTP %d (query=%r).",
                            exc.response.status_code,
                            query,
                        )
                    break
                except httpx.RequestError as exc:
                    self._logger.warning("LinkedIn : erreur réseau (query=%r) : %s", query, exc)
                    break

                if not card_keys:
                    break

                new_keys = [key for key in card_keys if key not in seen_page_keys]
                seen_page_keys.update(card_keys)
                if not new_keys:
                    self._logger.debug(
                        "LinkedIn : page déjà vue (query=%r, start=%d) — fin de pagination.",
                        query,
                        start,
                    )
                    break

                found += len(batch)
                for job in batch:
                    key = job.id_externe or job.url
                    if key in seen_ids:
                        continue
                    if collected_for_query >= quota or len(jobs) >= max_offers:
                        break
                    seen_ids.add(key)
                    jobs.append(job)
                    collected_for_query += 1

                # Pas réel = nombre de cartes reçues (10 aujourd'hui) : plus de
                # saut d'offres si LinkedIn change la taille de page.
                start += len(card_keys)
                if collected_for_query >= quota or len(jobs) >= max_offers:
                    break
                time.sleep(random.uniform(*SLEEP_RANGE))

        return ScrapeResult(jobs=jobs, found=found)
