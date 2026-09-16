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
import time
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup

from .base import BaseScraper
from .models import RawJob, ScrapeResult, ScraperConfig

GUEST_ENDPOINT = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
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

    def fetch(self) -> ScrapeResult:
        jobs: list[RawJob] = []
        found = 0
        max_offers = self.config.max_offers_per_source
        seen_ids: set[str] = set()  # déduplication inter-requêtes

        for query in self.config.target_queries:
            if len(jobs) >= max_offers:
                break
            start = 0
            seen_page_keys: set[str] = set()  # détection de stagnation (par requête)

            for _page in range(MAX_PAGES_PER_QUERY):
                if len(jobs) >= max_offers:
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
                    seen_ids.add(key)
                    jobs.append(job)

                # Pas réel = nombre de cartes reçues (10 aujourd'hui) : plus de
                # saut d'offres si LinkedIn change la taille de page.
                start += len(card_keys)
                if len(jobs) >= max_offers:
                    break
                time.sleep(random.uniform(*SLEEP_RANGE))

        return ScrapeResult(jobs=jobs, found=found)
