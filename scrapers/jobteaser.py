"""Client JobTeaser (intranet école) — page HTML ``/fr/job-offers``.

Cloudflare protège l'intranet : une requête ``httpx`` classique reçoit un
challenge HTTP 403 (empreinte TLS). Le scraper utilise donc ``curl_cffi`` avec
impersonation d'un navigateur (``impersonate="chrome"``) lorsqu'il est installé,
et retombe sur ``httpx`` sinon (avec un avertissement). Les cartes d'offres sont
parsées via leurs attributs ``data-testid="jobad-card*"`` (rendu SSR), avec des
replis (JSON embarqué puis liens HTML).

Variables d'environnement (.env) :
  JOBTEASER_BASE_URL          (défaut: https://emse.jobteaser.com)
  JOBTEASER_OFFERS_PATH       (défaut: /fr/job-offers)
  JOBTEASER_COOKIES           chaîne cookie complète (recommandé)
  JOBTEASER_SESSION           cookie ``jobteaser_session`` (repli)
  JOBTEASER_CF_CLEARANCE      cookie ``cf_clearance`` (repli Cloudflare)
  JOBTEASER_TOKEN             jeton Bearer optionnel
  JOBTEASER_IMPERSONATE       profil TLS curl_cffi (défaut: chrome)
  JOBTEASER_LOCATION          (défaut: France)
  JOBTEASER_LAT / _LNG / _RADIUS / _CONTRACT_DURATION / _WORK_EXPERIENCE_CODE
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from .base import BaseScraper, load_env_file, markup_to_text
from .cache import DiskCache
from .models import RawJob, ScrapeResult, ScraperConfig

try:  # Transport optionnel : impersonation TLS (contourne le challenge Cloudflare).
    from curl_cffi import requests as curl_requests
except ImportError:  # pragma: no cover - dépendance optionnelle
    curl_requests = None  # type: ignore[assignment]

DEFAULT_BASE_URL = "https://emse.jobteaser.com"
DEFAULT_OFFERS_PATH = "/fr/job-offers"
DEFAULT_IMPERSONATE = "chrome"
CACHE_NAMESPACE = "jobteaser"
NAV_ACCEPT = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,"
    "image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
)

_UUID_RE = re.compile(
    r"/job-offers/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)

# Marqueurs explicites de contrats NON-stage (l'offre est déjà filtrée côté requête).
_NON_INTERNSHIP = (
    "alternance",
    "apprentissage",
    "apprentice",
    "cdi",
    "cdd",
    "permanent",
    "freelance",
)

_TITLE_KEYS = ("title", "jobtitle", "job_title", "position", "name", "label")
_COMPANY_KEYS = (
    "companyname",
    "company_name",
    "company",
    "organizationname",
    "organization",
    "employer",
    "recruiter",
)
_ID_KEYS = ("id", "uuid", "reference", "slug", "url", "weburl", "web_url", "link", "shareurl")

# Conteneurs de la description sur la page détail (repli en cascade).
# ``jobad-DetailView__Description`` est vérifié sur une page réelle (sans cookies).
_DESCRIPTION_SELECTORS = (
    '[data-testid="jobad-DetailView__Description"]',
    "article[class*='Description-module']",
    "div[class*='Description-module'][class*='content']",
)


def _first_str(*values: Any) -> str:
    """Retourne la première valeur non vide convertible en chaîne."""
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)
    return ""


def _parse_datetime(value: Any) -> datetime | None:
    """Parse une date ISO 8601 ou un timestamp Unix en datetime UTC."""
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    if isinstance(value, (int, float)):
        seconds = value / 1000.0 if value > 10_000_000_000 else value
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    return None


def _name_of(value: Any) -> str:
    """Extrait un nom depuis une chaîne ou un objet imbriqué."""
    if isinstance(value, dict):
        return _first_str(
            value.get("name"), value.get("label"), value.get("title"), value.get("display_name")
        )
    return _first_str(value)


def _location_of(value: Any) -> str:
    """Extrait une localisation lisible depuis une chaîne ou un objet imbriqué."""
    if isinstance(value, dict):
        city = _first_str(value.get("city"), value.get("label"), value.get("name"))
        country = _first_str(value.get("country"))
        return ", ".join(part for part in (city, country) if part)
    return _first_str(value)


class JobTeaserScraper(BaseScraper):
    """Interroge la page d'offres de l'intranet JobTeaser de l'école."""

    source = "jobteaser"

    def __init__(self, config: ScraperConfig | None = None) -> None:
        load_env_file()
        super().__init__(config)

        self.base_url = os.getenv("JOBTEASER_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        self.offers_path = os.getenv("JOBTEASER_OFFERS_PATH", DEFAULT_OFFERS_PATH)
        self.offers_url = f"{self.base_url}{self.offers_path}"
        self.token = os.getenv("JOBTEASER_TOKEN", "").strip()
        self.cookie_domain = urlparse(self.base_url).hostname or "emse.jobteaser.com"
        self.cookies_raw = os.getenv("JOBTEASER_COOKIES", "").strip()
        self.session_cookie = os.getenv("JOBTEASER_SESSION", "").strip()
        self.cf_clearance = os.getenv("JOBTEASER_CF_CLEARANCE", "").strip()
        self.impersonate = (
            os.getenv("JOBTEASER_IMPERSONATE", DEFAULT_IMPERSONATE).strip() or DEFAULT_IMPERSONATE
        )

        # Entêtes de navigation (page HTML, comme le navigateur).
        self.client.headers.update(
            {
                "Accept": NAV_ACCEPT,
                "Accept-Language": "fr,fr-FR;q=0.9,en;q=0.8",
                "Referer": self.offers_url,
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
            }
        )
        if self.token:
            self.client.headers["Authorization"] = f"Bearer {self.token}"

        # Cookies (Cloudflare + session JobTeaser).
        self._cookie_pairs = self._parse_cookie_string(self.cookies_raw)
        self._cookies = self._build_cookie_dict()
        for name, value in self._cookie_pairs:
            self.client.cookies.set(name, value, domain=self.cookie_domain)
        if self.session_cookie:
            self.client.cookies.set("jobteaser_session", self.session_cookie, domain=self.cookie_domain)
        if self.cf_clearance:
            self.client.cookies.set("cf_clearance", self.cf_clearance, domain=self.cookie_domain)

        # Session curl_cffi (impersonation TLS) si disponible.
        self._curl = self._build_curl_session()

        # Paramètres de recherche de base (surchargeables par variables d'env).
        self.base_params: dict[str, str] = {
            "contract": "internship",
            "location": os.getenv("JOBTEASER_LOCATION", "France").strip() or "France",
        }
        for env_key, param in (
            ("JOBTEASER_LAT", "lat"),
            ("JOBTEASER_LNG", "lng"),
            ("JOBTEASER_RADIUS", "radius"),
            ("JOBTEASER_CONTRACT_DURATION", "contract_duration"),
            ("JOBTEASER_WORK_EXPERIENCE_CODE", "work_experience_code"),
        ):
            value = os.getenv(env_key, "").strip()
            if value:
                self.base_params[param] = value

    # ------------------------------------------------------------------ #
    # Cookies / transport
    # ------------------------------------------------------------------ #
    @staticmethod
    def _parse_cookie_string(raw: str) -> list[tuple[str, str]]:
        """Découpe une chaîne cookie navigateur (``a=1; b=2``) en paires nom/valeur."""
        pairs: list[tuple[str, str]] = []
        for part in raw.split(";"):
            name, sep, value = part.strip().partition("=")
            if sep and name.strip():
                pairs.append((name.strip(), value.strip()))
        return pairs

    def _build_cookie_dict(self) -> dict[str, str]:
        """Construit le dictionnaire de cookies envoyé au serveur."""
        cookies = dict(self._cookie_pairs)
        if self.session_cookie:
            cookies["jobteaser_session"] = self.session_cookie
        if self.cf_clearance:
            cookies["cf_clearance"] = self.cf_clearance
        return cookies

    def _build_curl_session(self) -> Any:
        """Crée une session curl_cffi avec impersonation TLS, si disponible."""
        if curl_requests is None:
            self._logger.warning(
                "curl_cffi absent : repli sur httpx (la page JobTeaser répond souvent 403 Cloudflare). "
                "Installez 'curl_cffi' pour activer l'impersonation TLS."
            )
            return None
        session = curl_requests.Session(impersonate=self.impersonate)
        headers = {
            key: value
            for key, value in self.client.headers.items()
            if key.lower() not in ("cookie", "host")
        }
        session.headers.update(headers)
        return session

    @property
    def _has_auth(self) -> bool:
        """Vrai si un cookie de session ou un jeton est disponible."""
        return bool(self.token or self.cookies_raw or self.session_cookie)

    def _fetch_html(self, url: str, params: dict[str, str]) -> tuple[int, str]:
        """Récupère la page HTML via curl_cffi (impersonation) ou httpx (repli)."""
        if self._curl is not None:
            response = self._curl.get(
                url,
                params=params,
                cookies=self._cookies,
                timeout=self.config.request_timeout_seconds,
            )
            return int(response.status_code), response.text
        response = self.client.get(url, params=params)
        return int(response.status_code), response.text

    def close(self) -> None:
        """Libère les clients HTTP (httpx et curl_cffi)."""
        if self._curl is not None:
            try:
                self._curl.close()
            except Exception:  # noqa: BLE001 - fermeture best-effort
                pass
        super().close()


    # ------------------------------------------------------------------ #
    # Extraction des offres (cartes SSR -> JSON embarqué -> liens)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _extract_cards_from_html(html_text: str) -> list[dict[str, Any]]:
        """Extrait les offres depuis les cartes SSR (``data-testid="jobad-card*"``)."""
        soup = BeautifulSoup(html_text, "lxml")
        cards: list[dict[str, Any]] = []
        for card in soup.select('[data-testid="jobad-card"]'):
            link = card.select_one('h3 a[href*="/job-offers/"]') or card.select_one(
                'a[href*="/job-offers/"]'
            )
            if link is None:
                continue
            href = (link.get("href") or "").strip()
            title = link.get_text(" ", strip=True)
            if not href or not title:
                continue
            company_el = card.select_one('[data-testid="jobad-card-company-name"]')
            location_el = card.select_one('[data-testid="jobad-card-location"]')
            contract_el = card.select_one('[data-testid="jobad-card-contract"]')
            cards.append(
                {
                    "title": title,
                    "company": company_el.get_text(" ", strip=True) if company_el else "",
                    "location": location_el.get_text(" ", strip=True) if location_el else "",
                    "contract": contract_el.get_text(" ", strip=True) if contract_el else "",
                    "url": href,
                }
            )
        return cards

    @staticmethod
    def _extract_links_from_html(html_text: str) -> list[dict[str, Any]]:
        """Repli : extrait les offres depuis les liens HTML des cartes."""
        soup = BeautifulSoup(html_text, "lxml")
        results: list[dict[str, Any]] = []
        for link in soup.select('a[href*="/job-offers/"]'):
            href = (link.get("href") or "").strip()
            title = link.get_text(" ", strip=True)
            if not href or not title:
                continue
            results.append({"title": title, "url": href})
        return results

    @classmethod
    def _looks_like_job(cls, node: dict[str, Any]) -> bool:
        """Heuristique : le dictionnaire ressemble-t-il à une offre d'emploi ?"""
        lowered = {str(key).casefold(): key for key in node}
        title_key = next((lowered[key] for key in _TITLE_KEYS if key in lowered), None)
        if title_key is None:
            return False
        title = node.get(title_key)
        if not (isinstance(title, str) and title.strip()):
            return False
        has_company = any(key in lowered for key in _COMPANY_KEYS)
        has_identity = any(key in lowered for key in _ID_KEYS)
        return has_company or has_identity

    @classmethod
    def _extract_jobs(cls, data: Any) -> list[dict[str, Any]]:
        """Parcourt l'arbre JSON et collecte les dictionnaires ressemblant à des offres."""
        found: list[dict[str, Any]] = []
        visited: set[int] = set()

        def visit(node: Any, depth: int) -> None:
            if depth > 12:
                return
            if isinstance(node, list):
                for item in node:
                    visit(item, depth + 1)
                return
            if isinstance(node, dict):
                if id(node) in visited:
                    return
                visited.add(id(node))
                if cls._looks_like_job(node):
                    found.append(node)
                    return
                for value in node.values():
                    visit(value, depth + 1)

        visit(data, 0)
        return found

    @staticmethod
    def _iter_json_payloads(html_text: str) -> Iterator[Any]:
        """Itère sur les payloads JSON embarqués (``__NEXT_DATA__`` et scripts JSON)."""
        soup = BeautifulSoup(html_text, "lxml")
        for script in soup.find_all("script"):
            script_id = script.get("id") or ""
            script_type = script.get("type") or ""
            if script_id != "__NEXT_DATA__" and script_type != "application/json":
                continue
            raw = script.string or script.get_text()
            if not raw:
                continue
            try:
                yield json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue

    @classmethod
    def _extract_jobs_from_html(cls, html_text: str) -> list[dict[str, Any]]:
        """Extrait les offres : cartes SSR, puis JSON embarqué, puis liens HTML."""
        cards = cls._extract_cards_from_html(html_text)
        if cards:
            return cards
        for payload in cls._iter_json_payloads(html_text):
            jobs = cls._extract_jobs(payload)
            if jobs:
                return jobs
        return cls._extract_links_from_html(html_text)


    # ------------------------------------------------------------------ #
    # Normalisation
    # ------------------------------------------------------------------ #
    def _to_raw_job(self, raw: dict[str, Any]) -> RawJob | None:
        title = _first_str(
            raw.get("title"),
            raw.get("jobTitle"),
            raw.get("job_title"),
            raw.get("position"),
            raw.get("name"),
            raw.get("label"),
        )
        if not title:
            return None

        company = _first_str(
            raw.get("companyName"),
            raw.get("company_name"),
            _name_of(raw.get("company")),
            _name_of(raw.get("organization")),
            _name_of(raw.get("employer")),
            _name_of(raw.get("recruiter")),
        )

        location = _first_str(
            raw.get("location_text"),
            raw.get("locationText"),
            raw.get("city"),
            _location_of(raw.get("location")),
            _location_of(raw.get("address")),
            _location_of(raw.get("place")),
        )

        contract = _first_str(
            raw.get("contract_type"),
            raw.get("contractType"),
            raw.get("contract"),
            raw.get("type"),
        ).casefold()
        is_internship = not any(marker in contract for marker in _NON_INTERNSHIP)

        description = _first_str(
            raw.get("description"),
            raw.get("description_text"),
            raw.get("descriptionText"),
            raw.get("mission"),
            raw.get("summary"),
        )

        url = _first_str(
            raw.get("url"),
            raw.get("webUrl"),
            raw.get("web_url"),
            raw.get("link"),
            raw.get("shareUrl"),
        )
        if url.startswith("/"):
            url = f"{self.base_url}{url}"

        match = _UUID_RE.search(url)
        identifier = _first_str(
            match.group(1) if match else None,
            raw.get("id"),
            raw.get("uuid"),
            raw.get("reference"),
            raw.get("slug"),
        )
        if not url and identifier:
            url = f"{self.offers_url}/{identifier}"
        if not identifier:
            identifier = url

        return RawJob(
            id_externe=identifier,
            source="jobteaser",
            title=title,
            company=company,
            location=location,
            url=url,
            description=description,
            published_at=_parse_datetime(
                raw.get("published_at")
                or raw.get("publishedAt")
                or raw.get("created_at")
                or raw.get("createdAt")
            ),
            is_internship=is_internship,
        )

    # ------------------------------------------------------------------ #
    # Description complète (page détail)
    # ------------------------------------------------------------------ #
    @staticmethod
    def parse_description(html_text: str) -> str:
        """Extrait la description depuis une page détail JobTeaser.

        Sélecteur principal : ``data-testid="jobad-DetailView__Description"``
        (structure vérifiée sur une page réelle) ; replis sur les classes du design
        system, puis sur les paragraphes longs si la structure évolue.
        """
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
        # Dernier repli : au moins 3 paragraphes longs ⇒ page de détail probable.
        paragraphs = [
            text
            for text in (markup_to_text(node) for node in soup.find_all(["p", "li"]))
            if len(text) > 120
        ]
        return "\n".join(paragraphs) if len(paragraphs) >= 3 else ""

    def fetch_description(self, url: str, cache: DiskCache | None = None) -> str:
        """Récupère la description complète d'une offre depuis sa page détail.

        Fonctionne sans cookie de session (le détail est public derrière
        Cloudflare : ``curl_cffi`` suffit), mais un 403 reste possible si
        l'impersonation expire → l'appelant en est informé par l'exception.
        """
        key = (url or "").split("?")[0].rstrip("/")
        if not key:
            return ""
        if cache is not None:
            cached = cache.get(CACHE_NAMESPACE, key)
            if cached is not None:
                return cached

        status, html_text = self._fetch_html(key, {})
        if status >= 400:
            request = httpx.Request("GET", key)
            raise httpx.HTTPStatusError(
                f"HTTP {status} sur {key}",
                request=request,
                response=httpx.Response(status, request=request),
            )
        description = self.parse_description(html_text)
        if description and cache is not None:
            cache.set(CACHE_NAMESPACE, key, description)
        return description

    # ------------------------------------------------------------------ #
    # Orchestration
    # ------------------------------------------------------------------ #
    def fetch(self) -> ScrapeResult:
        if not self._has_auth:
            self._logger.warning(
                "Cookies JobTeaser absents (JOBTEASER_COOKIES/JOBTEASER_SESSION) — "
                "scraper JobTeaser désactivé pour ce run, sans bloquer le pipeline."
            )
            return ScrapeResult(jobs=[], found=0)

        jobs: list[RawJob] = []
        found = 0
        quota = self.config.per_query_quota
        for query in self.config.target_queries:
            if len(jobs) >= self.config.max_offers_per_source:
                break
            params = dict(self.base_params)
            params["q"] = query

            try:
                status, html_text = self._fetch_html(self.offers_url, params)
            except httpx.RequestError as exc:
                self._logger.warning("JobTeaser : erreur réseau httpx : %s", exc)
                break
            except Exception as exc:  # noqa: BLE001 - transport curl_cffi / autres
                self._logger.warning("JobTeaser : erreur de transport : %s", exc)
                break

            if status == 403:
                self._logger.warning(
                    "JobTeaser : 403 Cloudflare (impersonation '%s' ou cookies expirés). "
                    "Rafraîchissez JOBTEASER_COOKIES depuis le navigateur.",
                    self.impersonate,
                )
                break
            if status >= 400:
                self._logger.warning("JobTeaser : réponse HTTP %d (%s).", status, self.offers_url)
                break

            raw_jobs = self._extract_jobs_from_html(html_text)
            if not raw_jobs:
                self._logger.warning("JobTeaser : aucune offre extraite (query=%r).", query)
                continue
            found += len(raw_jobs)
            # Quota PAR REQUÊTE : chaque requête cible contribue à la collecte
            # (l'ancienne boucle s'arrêtait dès le plafond global atteint, ce qui
            # privait les requêtes suivantes de toute chance).
            added_for_query = 0
            for raw in raw_jobs:
                if added_for_query >= quota or len(jobs) >= self.config.max_offers_per_source:
                    break
                job = self._to_raw_job(raw)
                if job:
                    jobs.append(job)
                    added_for_query += 1

        return ScrapeResult(jobs=jobs, found=found)



