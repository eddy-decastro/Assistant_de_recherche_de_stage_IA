"""Scraper Welcome to the Jungle (httpx) — **OBSOLÈTE, NON UTILISÉ**.

⚠️ DEPRECATED : ce module n'est plus importé par le pipeline. L'API publique v1
qu'il vise (``/api/v1/jobs``) a été retirée par WTTJ et répond désormais **404**
(constaté lors d'un run réel le 2026-09-14). La collecte WTTJ passe par
``scrapers/wttj.py`` (index Algolia public, filtre ``contract_type:internship``).
Fichier conservé à titre documentaire — voir ``AUDIT.md`` (action P2-L : suppression).

NOTE : l'API publique historique de WTTJ (``/api/v1/jobs``) a été retirée fin
2024 lors du passage à la recherche par "matching". Ce scraper vise l'endpoint
configurable dans ``config.yaml`` (``scraping.api_base``) et extrait les champs
de façon défensive (plusieurs formes de réponse acceptées).

En cas de 403 (protection anti-bot) ou de 404 (route déplacée), le scraper
échoue proprement avec un message explicite : ajustez ``api_base`` dans
``config.yaml`` ou adaptez les entêtes ``DEFAULT_HEADERS``.
"""
from __future__ import annotations

import html
import re
from typing import Any, Optional

import httpx

from src.config import load_config

SITE_BASE = "https://www.welcometothejungle.com"
SOURCE_NAME = "welcome_to_the_jungle"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

_ACCENTS = str.maketrans(
    {
        "é": "e", "è": "e", "ê": "e", "ë": "e",
        "à": "a", "â": "a", "ä": "a",
        "î": "i", "ï": "i",
        "ô": "o", "ö": "o",
        "ù": "u", "û": "u", "ü": "u",
        "ç": "c", "œ": "oe", "æ": "ae",
    }
)


def strip_html(value: str | None) -> str:
    """Supprime les balises HTML et normalise les espaces."""
    if not value:
        return ""
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", value))).strip()


def slugify(value: str) -> str:
    """Transforme une valeur en slug compatible API (ex: 'Île-de-France' -> 'ile-de-france')."""
    value = value.lower().translate(_ACCENTS)
    return _NON_ALNUM_RE.sub("-", value).strip("-")


class WelcomeToTheJungleScraper:
    """Récupère les offres de stage selon les critères de config.yaml."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.config = config or load_config()
        self.scraping = self.config.get("scraping", {})
        self.api_base = self.scraping.get("api_base", "https://api.welcometothejungle.com/api/v1").rstrip("/")
        self.max_offers = int(self.scraping.get("max_offers", 150))
        timeout = float(self.scraping.get("request_timeout_seconds", 30))
        self.client = client or httpx.Client(
            headers=DEFAULT_HEADERS, timeout=timeout, follow_redirects=True
        )

    # ------------------------------------------------------------------ #
    # Normalisation
    # ------------------------------------------------------------------ #
    @staticmethod
    def _first(*values: Any) -> str:
        """Retourne la première valeur non vide parmi les candidats (str ou dict)."""
        for value in values:
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                for key in ("name", "title", "location", "city", "label", "value"):
                    candidate = value.get(key)
                    if isinstance(candidate, str) and candidate.strip():
                        return candidate.strip()
        return ""

    def _normalize_job(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        title = self._first(raw.get("name"), raw.get("title"))
        company_obj = raw.get("company") or {}
        company = self._first(company_obj.get("name"), raw.get("company_name"))
        job_slug = raw.get("slug") or ""
        company_slug = company_obj.get("slug") or raw.get("company_slug") or ""

        office = raw.get("office") or {}
        location = self._first(office.get("location"), office.get("city"), raw.get("location"))

        description = strip_html(
            self._first(raw.get("description"), raw.get("content"), raw.get("descriptions"))
        )

        if not title or not company:
            return None

        url = raw.get("website_url") or raw.get("apply_url") or ""
        if not url and job_slug:
            url = f"{SITE_BASE}/fr/companies/{company_slug}/jobs/{job_slug}"

        return {
            "title": title,
            "company": company,
            "location": location,
            "url": url,
            "description": description,
            "source": SOURCE_NAME,
        }

    # ------------------------------------------------------------------ #
    # Requêtes
    # ------------------------------------------------------------------ #
    def search(self, keyword: str, location: str | None = None, page: int = 1) -> list[dict[str, Any]]:
        """Interroge l'API de recherche et renvoie les offres normalisées d'une page."""
        params: dict[str, Any] = {
            "query": keyword,
            "contract_type": self.scraping.get("contract_filter", "internship"),
            "page": page,
        }
        if location:
            params["office_location"] = slugify(location)

        response = self.client.get(f"{self.api_base}/jobs", params=params)
        if response.status_code == 403:
            raise RuntimeError(
                "WTTJ a bloqué la requête (403 anti-bot). Réessayez depuis un réseau résidentiel "
                "ou ajustez les entêtes DEFAULT_HEADERS dans src/ingestion/wttj.py."
            )
        if response.status_code == 404:
            raise RuntimeError(
                "L'endpoint WTTJ est introuvable (404). Vérifiez 'scraping.api_base' dans config.yaml."
            )
        response.raise_for_status()
        data = response.json()

        jobs = (
            data.get("jobs")
            or data.get("data", {}).get("jobs")
            or data.get("data", {}).get("jobs", {}).get("jobs")
            or []
        )
        if isinstance(jobs, dict):
            jobs = jobs.get("jobs") or []

        results = []
        for raw in jobs:
            normalized = self._normalize_job(raw)
            if normalized:
                results.append(normalized)
        return results

    # ------------------------------------------------------------------ #
    # Orchestration
    # ------------------------------------------------------------------ #
    def run(self) -> list[dict[str, Any]]:
        """Parcourt mots-clés × localisations et dédoublonne les offres."""
        keywords = self.scraping.get("search_keywords", [])
        locations = self.scraping.get("location_filters", []) or [None]
        seen: set[tuple[str, str]] = set()
        collected: list[dict[str, Any]] = []

        for keyword in keywords:
            for location in locations:
                page = 1
                while len(collected) < self.max_offers:
                    try:
                        batch = self.search(keyword, location, page)
                    except httpx.HTTPError as exc:
                        print(f"[wttj] Erreur HTTP ({keyword}/{location}/p{page}) : {exc}")
                        break
                    except RuntimeError as exc:
                        print(f"[wttj] {exc}")
                        break
                    if not batch:
                        break
                    new_jobs = 0
                    for job in batch:
                        key = (job["title"].casefold(), job["company"].casefold())
                        if key in seen:
                            continue
                        seen.add(key)
                        collected.append(job)
                        new_jobs += 1
                        if len(collected) >= self.max_offers:
                            break
                    if new_jobs == 0:
                        break
                    page += 1

        print(f"[wttj] {len(collected)} offre(s) collectée(s).")
        return collected

    def close(self) -> None:
        self.client.close()


def scrape_jobs(config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Point d'entrée : lance le scraping et retourne les offres normalisées."""
    scraper = WelcomeToTheJungleScraper(config)
    try:
        return scraper.run()
    finally:
        scraper.close()
