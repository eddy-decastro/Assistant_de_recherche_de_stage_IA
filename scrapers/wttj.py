"""Scraper Welcome to the Jungle via l'API publique Algolia (aucun parsing HTML).

Interroge l'index ``wttj_jobs_production`` au moyen de l'endpoint multi-query
d'Algolia, avec le filtre ``contract_type:internship``. Les champs sont extraits
de façon défensive : plusieurs formes de réponse sont acceptées car la structure
de l'index peut évoluer.
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx

from .base import BaseScraper
from .models import RawJob, ScrapeResult, ScraperConfig

ALGOLIA_HOST = "https://v3splegsrc-dsn.algolia.net"
ALGOLIA_QUERIES_ENDPOINT = f"{ALGOLIA_HOST}/1/indexes/*/queries"
ALGOLIA_APP_ID = "V3SPLEGSRC"
ALGOLIA_API_KEY = "603859ff57d97eabec2058e5f560e224"
ALGOLIA_INDEX = "wttj_jobs_production"

HITS_PER_PAGE = 50
CONTRACT_FILTER = "internship"
SITE_BASE = "https://www.welcometothejungle.com"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(value: str | None) -> str:
    """Supprime les balises HTML et normalise les espaces."""
    if not value:
        return ""
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", value))).strip()


def _nested_get(data: dict[str, Any], *path: str) -> Any:
    """Descend dans un dictionnaire imbriqué (``None`` si le chemin est absent)."""
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _first_str(*values: Any) -> str:
    """Retourne la première valeur non vide convertible en chaîne."""
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)
    return ""


def _parse_datetime(value: Any) -> datetime | None:
    """Parse une date (ISO 8601 ou timestamp Unix en s/ms) en datetime UTC."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000.0 if value > 10_000_000_000 else value
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


class WelcomeToTheJungleScraper(BaseScraper):
    """Récupère les offres de stage WTTJ via l'index Algolia public."""

    source = "wttj"

    def __init__(self, config: ScraperConfig | None = None) -> None:
        super().__init__(config)
        self.client.headers.update(
            {
                "X-Algolia-Application-Id": ALGOLIA_APP_ID,
                "X-Algolia-API-Key": ALGOLIA_API_KEY,
                "Content-Type": "application/json",
            }
        )

    # ------------------------------------------------------------------ #
    # Requête Algolia
    # ------------------------------------------------------------------ #
    def _search(self, query: str, page: int) -> list[dict[str, Any]]:
        params = (
            f"query={quote(query)}"
            f"&filters=contract_type:{CONTRACT_FILTER}"
            f"&hitsPerPage={HITS_PER_PAGE}"
            f"&page={page}"
        )
        payload = {"requests": [{"indexName": ALGOLIA_INDEX, "params": params}]}
        response = self.client.post(ALGOLIA_QUERIES_ENDPOINT, json=payload)
        response.raise_for_status()
        data = response.json()
        results = data.get("results") or []
        if not results:
            return []
        return results[0].get("hits") or []

    # ------------------------------------------------------------------ #
    # Normalisation
    # ------------------------------------------------------------------ #
    def _to_raw_job(self, hit: dict[str, Any]) -> RawJob | None:
        title = _first_str(hit.get("name"), hit.get("title"))
        company = _first_str(_nested_get(hit, "company", "name"), hit.get("company_name"))
        if not title or not company:
            return None

        city = _first_str(
            _nested_get(hit, "office", "city"),
            _nested_get(hit, "office", "location"),
        )
        country = _first_str(_nested_get(hit, "office", "country"))
        location = ", ".join(part for part in (city, country) if part)

        company_slug = _first_str(_nested_get(hit, "company", "slug"))
        job_slug = _first_str(hit.get("slug"))
        if company_slug and job_slug:
            url = f"{SITE_BASE}/fr/companies/{company_slug}/jobs/{job_slug}"
        else:
            url = _first_str(hit.get("website_url"), hit.get("url"))

        contract_type = _first_str(
            hit.get("contract_type"),
            _nested_get(hit, "contract", "name"),
        ).casefold()
        is_internship = (
            contract_type == CONTRACT_FILTER
            or "stage" in contract_type
            or "intern" in contract_type
        )

        description = strip_html(
            _first_str(
                hit.get("description"),
                hit.get("description_plain"),
                hit.get("content"),
            )
        )

        return RawJob(
            id_externe=_first_str(hit.get("objectID"), hit.get("id"), url),
            source=self.source,
            title=title,
            company=company,
            location=location,
            url=url,
            description=description,
            published_at=_parse_datetime(hit.get("published_at") or hit.get("created_at")),
            is_internship=is_internship,
        )

    # ------------------------------------------------------------------ #
    # Orchestration
    # ------------------------------------------------------------------ #
    def fetch(self) -> ScrapeResult:
        jobs: list[RawJob] = []
        found = 0
        max_offers = self.config.max_offers_per_source

        for query in self.config.target_queries:
            if len(jobs) >= max_offers:
                break
            page = 0
            while len(jobs) < max_offers:
                try:
                    hits = self._search(query, page)
                except httpx.HTTPStatusError as exc:
                    self._logger.warning(
                        "WTTJ : erreur HTTP (query=%r, page=%d) : %s", query, page, exc
                    )
                    break
                except httpx.RequestError as exc:
                    self._logger.warning(
                        "WTTJ : erreur réseau (query=%r, page=%d) : %s", query, page, exc
                    )
                    break
                if not hits:
                    break
                found += len(hits)
                for hit in hits:
                    job = self._to_raw_job(hit)
                    if job:
                        jobs.append(job)
                page += 1

        return ScrapeResult(jobs=jobs, found=found)

