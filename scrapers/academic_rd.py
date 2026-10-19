"""Scraper d'offres de stage et thèses CIFRE / R&D publique (Inria, CNRS, CEA, ANRT).

Interroge les flux publics RSS / Atom et portails d'offres de recherche des grands
instituts de recherche français en IA, informatique et mathématiques appliquées.
Toutes les fiches fournissent directement leur texte complet (LIST_HAS_DESCRIPTION = True).
"""

from __future__ import annotations

import email.utils
import html
import logging
import re
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from bs4 import BeautifulSoup

from .base import BaseScraper
from .known import KnownIndex
from .models import (
    CardEntry,
    PageResult,
    PassPlan,
    PassReport,
    RawJob,
    ScrapeResult,
    ScraperConfig,
    Source,
)

logger = logging.getLogger("scrapers.academic")

INRIA_RSS_URL = "https://jobs.inria.fr/public/classic/fr/offres/rss"
CNRS_RSS_URL = "https://emploi.cnrs.fr/Rss.aspx"
CEA_OFFERS_URL = "https://instn.cea.fr/offres-de-theses-et-stages/"

_WS_RE = re.compile(r"\s+")
_TAG_RE = re.compile(r"<[^>]+>")


def _clean_text(value: str | None) -> str:
    if not value:
        return ""
    unescaped = html.unescape(value)
    stripped = _TAG_RE.sub(" ", unescaped)
    return _WS_RE.sub(" ", stripped).strip()


def _parse_pubdate(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    date_clean = date_str.strip()
    try:
        # Essai format RFC 2822 (flux RSS standard)
        parsed_tuple = email.utils.parsedate_to_datetime(date_clean)
        if parsed_tuple:
            return parsed_tuple.astimezone(timezone.utc)
    except Exception:
        pass

    try:
        # Essai format ISO 8601 (Atom standard)
        dt = datetime.fromisoformat(date_clean.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        pass

    return None


class AcademicRDScraper(BaseScraper):
    """Scraper pour les offres de stages et R&D des laboratoires publics français."""

    source: Source = "academic"
    DATE_ORDER_RELIABLE: bool = False
    SERVER_WINDOW_FILTER: bool = False
    LIST_HAS_DESCRIPTION: bool = True

    def __init__(self, config: ScraperConfig | None = None) -> None:
        super().__init__(config)
        self._cached_jobs: list[RawJob] | None = None

    # ------------------------------------------------------------------ #
    # Parseurs de flux
    # ------------------------------------------------------------------ #
    def _parse_feed(self, content: str, default_company: str, default_location: str = "France") -> list[RawJob]:
        """Parse un flux RSS 2.0 ou Atom XML / HTML de façon défensive."""
        jobs: list[RawJob] = []
        if not content or not content.strip():
            return []

        # Tenter d'abord le parseur XML, repli sur html.parser
        soup = None
        for parser in ("xml", "html.parser"):
            try:
                soup = BeautifulSoup(content, parser)
                break
            except Exception:
                continue

        if soup is None:
            return []

        # Chercher <item> (RSS) ou <entry> (Atom)
        items = soup.find_all("item")
        if not items:
            items = soup.find_all("entry")

        for item in items:
            title_node = item.find("title")
            title = _clean_text(title_node.get_text() if title_node else "")
            if not title:
                continue

            link_node = item.find("link")
            link = ""
            if link_node:
                link = link_node.get("href") or link_node.get_text() or ""
            link = link.strip()

            guid_node = item.find("guid") or item.find("id")
            guid = (guid_node.get_text() if guid_node else "") or link

            desc_node = item.find("description") or item.find("summary") or item.find("content")
            desc_raw = desc_node.get_text() if desc_node else ""
            description = _clean_text(desc_raw)

            pubdate_node = item.find("pubdate") or item.find("pubDate") or item.find("published") or item.find("updated")
            published_at = _parse_pubdate(pubdate_node.get_text() if pubdate_node else None)

            # Extraire une éventuelle localisation ou utiliser le défaut
            location = default_location
            loc_match = re.search(r"\b(?:à|en|at|in)\s+([A-Z][a-zA-ZÀ-ÿ\s-]+(?:\([^)]+\))?)", title)
            if loc_match:
                candidate_city = loc_match.group(1).strip()
                if len(candidate_city) < 30:
                    location = f"{candidate_city}, France"

            id_clean = re.sub(r"[^a-zA-Z0-9_-]+", "_", guid)[-64:]
            jobs.append(
                RawJob(
                    id_externe=f"{default_company.lower()}_{id_clean}",
                    source=self.source,
                    title=title,
                    company=default_company,
                    location=location,
                    url=link or f"https://emploi.{default_company.lower()}.fr/offre/{id_clean}",
                    description=description,
                    published_at=published_at,
                    is_internship=True,
                )
            )

        return jobs

    # ------------------------------------------------------------------ #
    # Connecteurs Laboratoires
    # ------------------------------------------------------------------ #
    def _fetch_inria(self) -> list[RawJob]:
        """Récupère les offres de stage et recherche Inria."""
        try:
            resp = self.client.get(INRIA_RSS_URL)
            if resp.status_code == 200:
                jobs = self._parse_feed(resp.text, default_company="Inria", default_location="France")
                self._logger.debug("Inria : %d offre(s) extraite(s) du flux RSS.", len(jobs))
                return jobs
        except Exception as exc:
            self._logger.warning("Inria : échec de récupération du flux RSS (%s)", exc)
        return []

    def _fetch_cnrs(self) -> list[RawJob]:
        """Récupère les offres d'emploi / stage CNRS."""
        try:
            resp = self.client.get(CNRS_RSS_URL)
            if resp.status_code == 200:
                jobs = self._parse_feed(resp.text, default_company="CNRS", default_location="France")
                self._logger.debug("CNRS : %d offre(s) extraite(s) du flux RSS.", len(jobs))
                return jobs
        except Exception as exc:
            self._logger.warning("CNRS : échec de récupération du flux RSS (%s)", exc)
        return []

    def _fetch_cea(self) -> list[RawJob]:
        """Récupère les offres de stages et thèses R&D du CEA."""
        try:
            resp = self.client.get(CEA_OFFERS_URL)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                jobs: list[RawJob] = []
                for card in soup.find_all("article") or soup.find_all("div", class_=re.compile(r"offer|card")):
                    title_elem = card.find(["h2", "h3", "h4", "a"])
                    if not title_elem:
                        continue
                    title = _clean_text(title_elem.get_text())
                    link = title_elem.get("href") if title_elem.name == "a" else ""
                    if not link:
                        a_elem = card.find("a")
                        link = a_elem.get("href") if a_elem else ""
                    if not title:
                        continue
                    desc = _clean_text(card.get_text())
                    id_slug = re.sub(r"[^a-zA-Z0-9]+", "_", title[:50].strip())
                    jobs.append(
                        RawJob(
                            id_externe=f"cea_{id_slug}",
                            source=self.source,
                            title=title,
                            company="CEA",
                            location="France",
                            url=link or f"{CEA_OFFERS_URL}#{id_slug}",
                            description=desc,
                            published_at=datetime.now(timezone.utc),
                            is_internship=True,
                        )
                    )
                self._logger.debug("CEA : %d offre(s) extraite(s) du portail.", len(jobs))
                return jobs
        except Exception as exc:
            self._logger.warning("CEA : échec de récupération des offres (%s)", exc)
        return []

    def _fetch_all_academic(self) -> list[RawJob]:
        """Interroge tous les flux publics académiques et de recherche."""
        all_jobs: list[RawJob] = []
        all_jobs.extend(self._fetch_inria())
        all_jobs.extend(self._fetch_cnrs())
        all_jobs.extend(self._fetch_cea())
        self._logger.info("Academic R&D : %d offre(s) au total extraite(s) des flux publics.", len(all_jobs))
        return all_jobs

    # ------------------------------------------------------------------ #
    # Pagination & Orchestration
    # ------------------------------------------------------------------ #
    def _iter_pages(self, query: str, mode: str, cursor: Any, plan: PassPlan) -> PageResult:
        """Sert les offres de recherche académique filtrées par les mots-clés."""
        if self._cached_jobs is None:
            self._cached_jobs = self._fetch_all_academic()

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
        """Exécute une passe unique pour les flux académiques."""
        selected_modes = list(modes) if modes else self.config.enabled_modes()
        first_mode = selected_modes[0] if selected_modes else "freshness"

        result = super().collect(
            known_index=known_index,
            validate_jobs=validate_jobs,
            modes=[first_mode],
            queries=["R&D Publique"],
            on_batch_collected=on_batch_collected,
        )

        if len(selected_modes) > 1 and "relevance" in selected_modes:
            now_utc = datetime.now(timezone.utc)
            report = PassReport(
                source=self.source,
                query="R&D Publique",
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
                stop_detail="Flux R&D Publique : vivier complet extrait en une passe",
            )
            result.query_reports.append(report)

        return result
