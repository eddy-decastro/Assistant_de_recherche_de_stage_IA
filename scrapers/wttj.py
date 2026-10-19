"""Scraper Welcome to the Jungle via l'API publique Algolia (aucun parsing HTML).

Interroge l'index ``wttj_jobs_production`` au moyen de l'endpoint multi-query
d'Algolia, avec le filtre ``contract_type:internship``. Les champs sont extraits
de façon défensive : plusieurs formes de réponse sont acceptées car la structure
de l'index peut évoluer.
"""

from __future__ import annotations

import html
import os
import re
from datetime import datetime, timezone
from collections.abc import Sequence
from typing import Any
from urllib.parse import quote

from .base import BaseScraper
from .known import KnownIndex
from .models import CardEntry, PageResult, PassPlan, PassReport, RawJob, ScrapeResult, ScraperConfig

ALGOLIA_APP_ID = "CSEKHVMS53"
ALGOLIA_API_KEY = "4bd8f6215d0cc52b26430765769e65a0"
ALGOLIA_INDEX = "wttj_jobs_production_fr"
ALGOLIA_HOST = f"https://{ALGOLIA_APP_ID.lower()}-dsn.algolia.net"
ALGOLIA_QUERIES_ENDPOINT = f"{ALGOLIA_HOST}/1/indexes/*/queries"

HITS_PER_PAGE = 50
CONTRACT_FILTER = "internship"
#: Pays des bureaux retenus (``WTTJ_COUNTRY``, vide = tous pays). Mesuré le
#: 30/09/2026 : ~30 % des stages de l'index FR sont hors France (scoring LLM gaspillé).
DEFAULT_COUNTRY = "FR"
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
    """Récupère les offres de stage WTTJ via l'index Algolia public.

    Capacités : l'index expose le classement natif d'Algolia (pertinence). Aucun
    tri par date ni filtre temporel serveur n'est garanti. Le scraper reste donc
    conservateur : ``DATE_ORDER_RELIABLE`` et ``SERVER_WINDOW_FILTER`` à ``False``,
    la sélection fine revenant au filtre métier et au scoring en aval.
    """

    source = "wttj"
    DATE_ORDER_RELIABLE = False
    SERVER_WINDOW_FILTER = False

    def __init__(self, config: ScraperConfig | None = None) -> None:
        super().__init__(config)
        self.client.headers.update(
            {
                "X-Algolia-Application-Id": ALGOLIA_APP_ID,
                "X-Algolia-API-Key": ALGOLIA_API_KEY,
                "Content-Type": "application/json",
                "Referer": "https://www.welcometothejungle.com/fr/jobs",
                "Origin": "https://www.welcometothejungle.com",
            }
        )
        self._sort_notice_logged = False
        self._page0_cache: dict[str, list[dict[str, Any]]] = {}

    def _refresh_keys(self) -> bool:
        """Tente de rafraîchir les clés Algolia depuis l'endpoint public /api/env."""
        try:
            from curl_cffi import requests as curl_requests
            r = curl_requests.get("https://www.welcometothejungle.com/api/env", impersonate="chrome", timeout=10)
            if r.status_code == 200:
                import json
                m = re.search(r'window\.env\s*=\s*(\{.*?\});?', r.text)
                if m:
                    env = json.loads(m.group(1))
                    app_id = env.get("PUBLIC_ALGOLIA_APPLICATION_ID")
                    api_key = env.get("PUBLIC_ALGOLIA_API_KEY_CLIENT")
                    if app_id and api_key:
                        self.client.headers["X-Algolia-Application-Id"] = app_id
                        self.client.headers["X-Algolia-API-Key"] = api_key
                        self._logger.info("WTTJ : clés Algolia rafraîchies avec succès depuis /api/env.")
                        return True
        except Exception as exc:
            self._logger.debug("WTTJ : échec du rafraîchissement dynamique des clés : %s", exc)
        return False

    # ------------------------------------------------------------------ #
    # Requête Algolia
    # ------------------------------------------------------------------ #
    @staticmethod
    def _filters() -> str:
        """Filtre Algolia : contrat de stage, et pays des bureaux si configuré."""
        country = os.getenv("WTTJ_COUNTRY", DEFAULT_COUNTRY).strip().upper()
        filters = f"contract_type:{CONTRACT_FILTER}"
        return f"{filters} AND offices.country_code:{country}" if country else filters

    def preload_queries(self, queries: Sequence[str]) -> None:
        """Précharge la page 0 de plusieurs requêtes Algolia en un seul appel multi-query."""
        to_fetch = [q for q in queries if q not in self._page0_cache]
        if not to_fetch:
            return
        payload = {
            "requests": [
                {
                    "indexName": ALGOLIA_INDEX,
                    "params": (
                        f"query={quote(q)}"
                        f"&filters={quote(self._filters())}"
                        f"&hitsPerPage={HITS_PER_PAGE}"
                        f"&page=0"
                    ),
                }
                for q in to_fetch
            ]
        }
        try:
            response = self.client.post(ALGOLIA_QUERIES_ENDPOINT, json=payload)
            if response.status_code in (401, 403) and self._refresh_keys():
                response = self.client.post(ALGOLIA_QUERIES_ENDPOINT, json=payload)
            response.raise_for_status()
            data = response.json()
            results = data.get("results") or []
            for q, res in zip(to_fetch, results):
                self._page0_cache[q] = res.get("hits") or []
        except Exception as exc:
            self._logger.debug("WTTJ : échec du préchargement multi-requêtes : %s", exc)

    def _search(self, query: str, page: int) -> list[dict[str, Any]]:
        if page == 0 and query in self._page0_cache:
            return self._page0_cache[query]
        params = (
            f"query={quote(query)}"
            f"&filters={quote(self._filters())}"
            f"&hitsPerPage={HITS_PER_PAGE}"
            f"&page={page}"
        )
        payload = {"requests": [{"indexName": ALGOLIA_INDEX, "params": params}]}
        response = self.client.post(ALGOLIA_QUERIES_ENDPOINT, json=payload)
        if response.status_code in (401, 403) and self._refresh_keys():
            response = self.client.post(ALGOLIA_QUERIES_ENDPOINT, json=payload)
        response.raise_for_status()
        data = response.json()
        results = data.get("results") or []
        if not results:
            hits = []
        else:
            hits = results[0].get("hits") or []
        if page == 0:
            self._page0_cache[query] = hits
        return hits

    # ------------------------------------------------------------------ #
    # Normalisation
    # ------------------------------------------------------------------ #
    def _to_raw_job(self, hit: dict[str, Any]) -> RawJob | None:
        title = _first_str(hit.get("name"), hit.get("title"))
        company = _first_str(
            _nested_get(hit, "organization", "name"),
            _nested_get(hit, "company", "name"),
            hit.get("company_name"),
        )
        if not title or not company:
            return None

        offices = hit.get("offices") or []
        first_office = offices[0] if (isinstance(offices, list) and offices and isinstance(offices[0], dict)) else {}
        city = _first_str(
            first_office.get("city"),
            first_office.get("local_city"),
            _nested_get(hit, "office", "city"),
            _nested_get(hit, "office", "location"),
        )
        country = _first_str(
            first_office.get("country"),
            _nested_get(hit, "office", "country"),
        )
        location = ", ".join(part for part in (city, country) if part)

        company_slug = _first_str(
            _nested_get(hit, "organization", "slug"),
            _nested_get(hit, "company", "slug"),
        )
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
                hit.get("summary"),
                hit.get("description"),
                hit.get("description_plain"),
                hit.get("content"),
            )
        )

        return RawJob(
            id_externe=_first_str(hit.get("objectID"), hit.get("id"), hit.get("slug"), url),
            source=self.source,
            title=title,
            company=company,
            location=location,
            url=url,
            description=description,
            published_at=_parse_datetime(
                hit.get("published_at")
                or hit.get("published_at_timestamp")
                or hit.get("created_at")
            ),
            is_internship=is_internship,
        )

    # ------------------------------------------------------------------ #
    # Orchestration
    # ------------------------------------------------------------------ #
    def _iter_pages(self, query: str, mode: str, cursor: Any, plan: PassPlan) -> PageResult:
        """Une page de hits Algolia (50 par appel).

        Le tri n'est pas différencié par mode : Algolia renvoie son classement de
        pertinence (le mode « Fraîcheur » ne bénéficie donc d'aucune garantie
        temporelle côté plateforme — l'information est consignée une fois par run).
        """
        if plan.sort == "date" and not self._sort_notice_logged:
            self._sort_notice_logged = True
            self._logger.info(
                "WTTJ : tri par date non exposé par l'index Algolia — classement natif "
                "(pertinence) utilisé pour la passe « Fraîcheur »."
            )
        page = int(cursor or 0)
        hits = self._search(query, page)
        entries: list[CardEntry] = []
        for hit in hits:
            job = self._to_raw_job(hit)
            key = (
                (job.id_externe if job else "")
                or _first_str(hit.get("objectID"), hit.get("id"), hit.get("slug"))
            )
            if key:
                entries.append(CardEntry(key=key, job=job))
        return PageResult(
            entries=entries,
            next_cursor=page + 1,
            exhausted=not hits,
            http_calls=1,
        )

    def collect(
        self,
        known_index: KnownIndex | None = None,
        *,
        validate_jobs: bool = False,
        modes: Sequence[str] | None = None,
        queries: Sequence[str] | None = None,
        on_batch_collected: Any = None,
    ) -> ScrapeResult:
        selected_modes = list(modes) if modes else self.config.enabled_modes()
        selected_queries = list(queries) if queries else list(self.config.target_queries)

        # Si "freshness" et "relevance" sont demandés ensemble, comme l'index Algolia
        # wttj_jobs_production_fr n'accepte pas de tri par date distinct de la pertinence,
        # on exécute uniquement "freshness" et on consigne "relevance" comme unsupported.
        skip_relevance = "freshness" in selected_modes and "relevance" in selected_modes

        if skip_relevance:
            effective_modes = [m for m in selected_modes if m != "relevance"]
        else:
            effective_modes = selected_modes

        self.preload_queries(selected_queries)

        result = super().collect(
            known_index=known_index,
            validate_jobs=validate_jobs,
            modes=effective_modes,
            queries=selected_queries,
            on_batch_collected=on_batch_collected,
        )

        if skip_relevance:
            relevance_plan = self.config.build_plan(
                "relevance",
                date_order_reliable=self.DATE_ORDER_RELIABLE,
                server_window_filter=self.SERVER_WINDOW_FILTER,
            )
            now_utc = datetime.now(timezone.utc)
            for q in selected_queries:
                report = PassReport(
                    source=self.source,
                    query=q,
                    mode="relevance",
                    started_at=now_utc,
                    finished_at=now_utc,
                    duration_seconds=0.0,
                    pages_fetched=0,
                    http_requests=0,
                    cards_seen=0,
                    jobs_kept=0,
                    jobs_known=0,
                    jobs_duplicate=0,
                    jobs_rejected=0,
                    jobs_out_of_window=0,
                    target_new=relevance_plan.target_new,
                    stop_reason="unsupported",
                    stop_detail="Index Algolia unique : fusionné avec la passe freshness pour éviter les requêtes en double",
                )
                result.query_reports.append(report)

        return result

