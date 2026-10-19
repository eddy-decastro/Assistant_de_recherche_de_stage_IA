"""Scraper d'offres en direct sur les API publiques ATS (Lever, Greenhouse, Ashby, SmartRecruiters).

Interroge les job boards publics des scale-ups et licornes tech françaises/européennes
(Mistral, Owkin, Hugging Face, Dataiku, Dust, Bioptimus, etc.) sans passer par un agrégateur.
Toutes les fiches fournissent directement leur texte complet (LIST_HAS_DESCRIPTION = True).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from bs4 import BeautifulSoup

from .base import BaseScraper
from .known import KnownIndex
from .models import CardEntry, PageResult, PassPlan, PassReport, RawJob, ScrapeResult, ScraperConfig, Source

logger = logging.getLogger("scrapers.ats")

DEFAULT_ATS_BOARDS: dict[str, list[str]] = {
    "lever": ["mistral", "owkin", "qonto", "shift-technology", "backmarket"],
    "greenhouse": ["datadog", "huggingface", "dataiku", "alan", "criteo"],
    "ashby": ["dust", "bioptimus"],
    "smartrecruiters": ["sanofi", "ubisoft"],
}

_FRANCE_LOC_RE = re.compile(
    r"\b(?:france|paris|lyon|grenoble|toulouse|sophia|bordeaux|nantes|lille|saclay|rennes|strasbourg|marseille|nice|montpellier|idf|ile-de-france|fr)\b",
    re.IGNORECASE,
)

INTERNSHIP_KEYWORDS: tuple[str, ...] = (
    "intern",
    "internship",
    "stage",
    "stagiaire",
    "pfe",
    "end of studies",
    "master thesis",
    "praktikant",
    "werkstudent",
)

COMPANY_DISPLAY_NAMES: dict[str, str] = {
    "mistral": "Mistral AI",
    "owkin": "Owkin",
    "qonto": "Qonto",
    "shift-technology": "Shift Technology",
    "backmarket": "Back Market",
    "datadog": "Datadog",
    "huggingface": "Hugging Face",
    "dataiku": "Dataiku",
    "alan": "Alan",
    "criteo": "Criteo",
    "dust": "Dust",
    "bioptimus": "Bioptimus",
    "sanofi": "Sanofi",
    "ubisoft": "Ubisoft",
}


def _clean_html(html_text: str | None) -> str:
    if not html_text:
        return ""
    try:
        soup = BeautifulSoup(html_text, "html.parser")
        return soup.get_text("\n", strip=True)
    except Exception:
        return re.sub(r"<[^>]+>", " ", html_text).strip()


def _is_france_location(location: str | None) -> bool:
    if not location or not location.strip():
        # Sans localisation explicite, on conserve par précaution pour le filtre aval
        return True
    return bool(_FRANCE_LOC_RE.search(location))


def _is_internship(title: str, contract_info: str = "") -> bool:
    combined = f"{title} {contract_info}".casefold()
    return any(keyword in combined for keyword in INTERNSHIP_KEYWORDS)


def _company_name(slug: str) -> str:
    return COMPANY_DISPLAY_NAMES.get(slug.lower(), slug.replace("-", " ").replace("_", " ").title())


class DirectATSScraper(BaseScraper):
    """Scraper interrogeant directement les APIs publiques des ATS d'entreprises cibles."""

    source: Source = "ats"
    DATE_ORDER_RELIABLE: bool = False
    SERVER_WINDOW_FILTER: bool = False
    LIST_HAS_DESCRIPTION: bool = True

    def __init__(self, config: ScraperConfig | None = None) -> None:
        super().__init__(config)
        self.boards = self.config.ats_boards if self.config.ats_boards else DEFAULT_ATS_BOARDS
        self._cached_jobs: list[RawJob] | None = None

    # ------------------------------------------------------------------ #
    # Connecteurs ATS
    # ------------------------------------------------------------------ #
    def _fetch_lever(self, slug: str) -> list[RawJob]:
        """Récupère les offres d'un board Lever."""
        url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
        company = _company_name(slug)
        jobs: list[RawJob] = []
        try:
            resp = self.client.get(url)
            if resp.status_code == 404:
                self._logger.debug("Lever : board introuvable pour %s (404)", slug)
                return []
            resp.raise_for_status()
            items = resp.json()
            if not isinstance(items, list):
                return []
            for item in items:
                title = str(item.get("text") or "").strip()
                categories = item.get("categories") or {}
                location = str(categories.get("location") or "")
                commitment = str(categories.get("commitment") or "")
                if not _is_france_location(location):
                    continue
                if not _is_internship(title, commitment):
                    continue

                desc_plain = str(item.get("descriptionPlain") or "")
                lists_content = []
                for lst in item.get("lists") or []:
                    header = lst.get("text") or ""
                    content = lst.get("content") or ""
                    clean_content = _clean_html(content)
                    lists_content.append(f"{header}\n{clean_content}")
                full_desc = "\n\n".join(part for part in [desc_plain, *lists_content] if part).strip()

                ext_id = str(item.get("id") or "")
                hosted_url = str(item.get("hostedUrl") or "")
                created_at_ms = item.get("createdAt")
                published_at = (
                    datetime.fromtimestamp(created_at_ms / 1000.0, tz=timezone.utc)
                    if isinstance(created_at_ms, (int, float))
                    else None
                )

                jobs.append(
                    RawJob(
                        id_externe=f"lever_{slug}_{ext_id}",
                        source=self.source,
                        title=title,
                        company=company,
                        location=location or "France",
                        url=hosted_url or f"https://jobs.lever.co/{slug}/{ext_id}",
                        description=full_desc,
                        published_at=published_at,
                        is_internship=True,
                    )
                )
        except Exception as exc:
            self._logger.warning("Lever : échec récupération pour %s (%s)", slug, exc)
        return jobs

    def _fetch_greenhouse(self, slug: str) -> list[RawJob]:
        """Récupère les offres d'un board Greenhouse."""
        url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
        company = _company_name(slug)
        jobs: list[RawJob] = []
        try:
            resp = self.client.get(url)
            if resp.status_code == 404:
                self._logger.debug("Greenhouse : board introuvable pour %s (404)", slug)
                return []
            resp.raise_for_status()
            data = resp.json()
            items = data.get("jobs") or []
            for item in items:
                title = str(item.get("title") or "").strip()
                location_obj = item.get("location") or {}
                location = str(location_obj.get("name") or "")
                if not _is_france_location(location):
                    continue
                if not _is_internship(title):
                    continue

                content_html = item.get("content") or ""
                description = _clean_html(content_html)
                ext_id = str(item.get("id") or "")
                abs_url = str(item.get("absolute_url") or "")
                updated_at_str = item.get("updated_at")
                published_at = None
                if updated_at_str:
                    try:
                        published_at = datetime.fromisoformat(updated_at_str.replace("Z", "+00:00"))
                    except Exception:
                        pass

                jobs.append(
                    RawJob(
                        id_externe=f"gh_{slug}_{ext_id}",
                        source=self.source,
                        title=title,
                        company=company,
                        location=location or "France",
                        url=abs_url or f"https://boards.greenhouse.io/{slug}/jobs/{ext_id}",
                        description=description,
                        published_at=published_at,
                        is_internship=True,
                    )
                )
        except Exception as exc:
            self._logger.warning("Greenhouse : échec récupération pour %s (%s)", slug, exc)
        return jobs

    def _fetch_ashby(self, slug: str) -> list[RawJob]:
        """Récupère les offres d'un board Ashby."""
        url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
        company = _company_name(slug)
        jobs: list[RawJob] = []
        try:
            resp = self.client.get(url)
            if resp.status_code == 404:
                self._logger.debug("Ashby : board introuvable pour %s (404)", slug)
                return []
            resp.raise_for_status()
            data = resp.json()
            items = data.get("jobs") or []
            for item in items:
                title = str(item.get("title") or "").strip()
                location = str(item.get("location") or item.get("locationName") or "")
                employment_type = str(item.get("employmentType") or "")
                if not _is_france_location(location):
                    continue
                if not _is_internship(title, employment_type):
                    continue

                desc = str(item.get("descriptionPlain") or _clean_html(item.get("descriptionHtml")))
                ext_id = str(item.get("id") or "")
                job_url = str(item.get("jobUrl") or "")
                published_at_str = item.get("publishedAt")
                published_at = None
                if published_at_str:
                    try:
                        published_at = datetime.fromisoformat(published_at_str.replace("Z", "+00:00"))
                    except Exception:
                        pass

                jobs.append(
                    RawJob(
                        id_externe=f"ashby_{slug}_{ext_id}",
                        source=self.source,
                        title=title,
                        company=company,
                        location=location or "France",
                        url=job_url or f"https://jobs.ashbyhq.com/{slug}/{ext_id}",
                        description=desc,
                        published_at=published_at,
                        is_internship=True,
                    )
                )
        except Exception as exc:
            self._logger.warning("Ashby : échec récupération pour %s (%s)", slug, exc)
        return jobs

    def _fetch_smartrecruiters(self, slug: str) -> list[RawJob]:
        """Récupère les offres d'un board SmartRecruiters."""
        url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
        company = _company_name(slug)
        jobs: list[RawJob] = []
        try:
            resp = self.client.get(url)
            if resp.status_code == 404:
                self._logger.debug("SmartRecruiters : board introuvable pour %s (404)", slug)
                return []
            resp.raise_for_status()
            data = resp.json()
            items = data.get("content") or []
            for item in items:
                title = str(item.get("name") or "").strip()
                loc_obj = item.get("location") or {}
                city = str(loc_obj.get("city") or "")
                country = str(loc_obj.get("country") or "")
                location = ", ".join(p for p in (city, country) if p)
                type_obj = item.get("typeOfEmployment") or {}
                contract_type = str(type_obj.get("label") or type_obj.get("id") or "")
                if not _is_france_location(location):
                    continue
                if not _is_internship(title, contract_type):
                    continue

                ext_id = str(item.get("id") or "")
                posting_url = f"https://jobs.smartrecruiters.com/{slug}/{ext_id}"
                released_date_str = item.get("releasedDate")
                published_at = None
                if released_date_str:
                    try:
                        published_at = datetime.fromisoformat(released_date_str.replace("Z", "+00:00"))
                    except Exception:
                        pass

                jobs.append(
                    RawJob(
                        id_externe=f"sr_{slug}_{ext_id}",
                        source=self.source,
                        title=title,
                        company=company,
                        location=location or "France",
                        url=posting_url,
                        description="",  # Description sera récupérée ou complétée si besoin
                        published_at=published_at,
                        is_internship=True,
                    )
                )
        except Exception as exc:
            self._logger.warning("SmartRecruiters : échec récupération pour %s (%s)", slug, exc)
        return jobs

    def _fetch_all_boards(self) -> list[RawJob]:
        """Interroge l'ensemble des ATS configurés."""
        all_jobs: list[RawJob] = []
        for slug in self.boards.get("lever", []):
            all_jobs.extend(self._fetch_lever(slug))
        for slug in self.boards.get("greenhouse", []):
            all_jobs.extend(self._fetch_greenhouse(slug))
        for slug in self.boards.get("ashby", []):
            all_jobs.extend(self._fetch_ashby(slug))
        for slug in self.boards.get("smartrecruiters", []):
            all_jobs.extend(self._fetch_smartrecruiters(slug))
        self._logger.info("Direct ATS : %d offre(s) de stage en France extraite(s) des boards.", len(all_jobs))
        return all_jobs

    # ------------------------------------------------------------------ #
    # Pagination & Orchestration
    # ------------------------------------------------------------------ #
    def _iter_pages(self, query: str, mode: str, cursor: Any, plan: PassPlan) -> PageResult:
        """Sert les offres ATS.

        Comme les APIs ATS renvoient le vivier complet en un seul appel,
        la collecte n'a lieu qu'au premier curseur. Les curseurs suivants indiquent la fin.
        """
        if self._cached_jobs is None:
            self._cached_jobs = self._fetch_all_boards()

        offset = int(cursor or 0)
        page_size = 50
        page_jobs = self._cached_jobs[offset : offset + page_size]
        entries = [
            CardEntry(key=job.id_externe or job.url, job=job)
            for job in page_jobs
        ]
        next_cursor = offset + page_size if (offset + page_size) < len(self._cached_jobs) else None
        return PageResult(
            entries=entries,
            next_cursor=next_cursor,
            exhausted=next_cursor is None,
            http_calls=1 if offset == 0 else 0,
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
        """Exécute une passe unique pour les boards ATS."""
        selected_modes = list(modes) if modes else self.config.enabled_modes()
        # Ne lancer que sur une requête virtuelle ATS pour éviter de répéter N fois
        first_mode = selected_modes[0] if selected_modes else "freshness"

        result = super().collect(
            known_index=known_index,
            validate_jobs=validate_jobs,
            modes=[first_mode],
            queries=["ATS Direct"],
            on_batch_collected=on_batch_collected,
        )

        # Si relevance était aussi demandé, ajouter un rapport unsupported pour cohérence
        if len(selected_modes) > 1 and "relevance" in selected_modes:
            now_utc = datetime.now(timezone.utc)
            report = PassReport(
                source=self.source,
                query="ATS Direct",
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
                target_new=0,
                stop_reason="unsupported",
                stop_detail="API ATS Direct : vivier complet extrait en une passe",
            )
            result.query_reports.append(report)

        return result
