"""Orchestrateur central : lance les scrapers, déduplique et agrège les résultats."""

from __future__ import annotations

import logging

from .base import BaseScraper
from .jobteaser import JobTeaserScraper
from .linkedin import LinkedInGuestScraper
from .models import RawJob, ScrapeResult, ScraperConfig, Source
from .wttj import WelcomeToTheJungleScraper

logger = logging.getLogger("scrapers.manager")

SCRAPER_REGISTRY: dict[Source, type[BaseScraper]] = {
    "wttj": WelcomeToTheJungleScraper,
    "linkedin": LinkedInGuestScraper,
    "jobteaser": JobTeaserScraper,
}


class ScraperManager:
    """Instancie et exécute les scrapers activés, puis fusionne leurs offres."""

    def __init__(self, config: ScraperConfig | None = None) -> None:
        self.config = config or ScraperConfig()

    @staticmethod
    def _normalize_url(url: str) -> str:
        cleaned = (url or "").strip().casefold()
        # Retire la chaîne de requête et le fragment pour une déduplication robuste.
        return cleaned.split("?")[0].split("#")[0].rstrip("/")

    def run(self) -> ScrapeResult:
        """Lance tous les scrapers activés et retourne les offres dédupliquées.

        La déduplication s'effectue sur l'URL canonique normalisée ; seules les
        offres ayant passé le filtre ``is_valid_job`` (via ``BaseScraper.run``)
        sont conservées.
        """
        seen_urls: set[str] = set()
        merged: list[RawJob] = []
        total_found = 0
        total_rejected = 0

        for source in self.config.enabled_sources:
            scraper_cls = SCRAPER_REGISTRY.get(source)
            if scraper_cls is None:
                logger.warning("Source inconnue ignorée : %s", source)
                continue

            scraper = scraper_cls(self.config)
            try:
                result = scraper.run()
            finally:
                scraper.close()

            total_found += result.found
            total_rejected += result.rejected_bi
            for job in result.jobs:
                url_key = self._normalize_url(job.url)
                if url_key and url_key in seen_urls:
                    continue
                if url_key:
                    seen_urls.add(url_key)
                merged.append(job)

        logger.info(
            "Fusion terminée : %d offre(s) unique(s) après déduplication (%d rejetée(s)).",
            len(merged),
            total_rejected,
        )
        return ScrapeResult(jobs=merged, found=total_found, rejected_bi=total_rejected)
