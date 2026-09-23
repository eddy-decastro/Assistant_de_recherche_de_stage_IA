"""Orchestrateur central : lance les scrapers, déduplique et agrège les résultats.

L'orchestrateur porte l'objet le plus important de la collecte hybride : la
**mémoire de collecte** (``KnownIndex``) partagée par toutes les sources. C'est
elle qui garantit la déduplication transverse — « une offre collectée lors de la
passe Date n'est pas retraitée ni comptée dans la passe Pertinence » — et qui sert
de référence à l'arrêt anticipé.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from .base import BaseScraper
from .jobteaser import JobTeaserScraper
from .known import KnownIndex, NullKnownIndex
from .linkedin import LinkedInGuestScraper
from .models import (
    SEEN_DUPLICATE,
    SEEN_KNOWN,
    SEEN_OUT_OF_WINDOW,
    SEEN_REJECTED_BI,
    SEEN_REJECTED_CONTRACT,
    SEEN_VALIDATED,
    RawJob,
    ScrapeResult,
    ScraperConfig,
    SeenEntry,
    Source,
    canonical_url,
)
from .wttj import WelcomeToTheJungleScraper

logger = logging.getLogger("scrapers.manager")

SCRAPER_REGISTRY: dict[Source, type[BaseScraper]] = {
    "wttj": WelcomeToTheJungleScraper,
    "linkedin": LinkedInGuestScraper,
    "jobteaser": JobTeaserScraper,
}

#: Priorité des décisions lorsqu'une même offre est croisée plusieurs fois dans le
#: même run (plusieurs requêtes, plusieurs passes) : la décision la plus
#: informative est conservée. ``DUPLICATE`` n'en porte aucune (l'offre est déjà
#: mémorisée par ailleurs dans le même run) et est donc écartée.
_DECISION_PRIORITY: dict[str, int] = {
    SEEN_VALIDATED: 5,
    SEEN_REJECTED_BI: 4,
    SEEN_REJECTED_CONTRACT: 4,
    SEEN_OUT_OF_WINDOW: 3,
    SEEN_KNOWN: 2,
    SEEN_DUPLICATE: 0,
}


class ScraperManager:
    """Instancie et exécute les scrapers activés, puis fusionne leurs offres."""

    def __init__(
        self,
        config: ScraperConfig | None = None,
        known_index: KnownIndex | None = None,
    ) -> None:
        self.config = config or ScraperConfig()
        #: Mémoire de collecte partagée par toutes les sources (arrêt anticipé et
        #: déduplication transverse). ``NullKnownIndex`` = mode dégradé sans mémoire.
        self.known_index: KnownIndex = known_index or NullKnownIndex()

    @staticmethod
    def _normalize_url(url: str) -> str:
        """URL normalisée pour la déduplication intra-run.

        Alignée sur ``models.canonical_url`` (sans schéma, sans ``www.``, sans
        query/fragment ni slash final) pour éviter que ``http://`` et ``https://``
        soient traitées comme des URLs distinctes lors du merge.
        """
        return canonical_url(url)

    @staticmethod
    def _merge_seen(entries: Sequence[SeenEntry]) -> list[SeenEntry]:
        """Fusionne la mémoire de collecte du run : une décision par offre.

        Évite d'écrire plusieurs lignes pour la même offre (elle est croisée par
        plusieurs requêtes et par les deux passes) tout en conservant la décision
        la plus informative.
        """
        best: dict[tuple[str, str], SeenEntry] = {}
        for entry in entries:
            priority = _DECISION_PRIORITY.get(entry.decision, 1)
            if priority == 0:
                continue  # déjà mémorisée par ailleurs dans ce run
            key = (entry.source, entry.external_key)
            current = best.get(key)
            if current is None or priority > _DECISION_PRIORITY.get(current.decision, 1):
                best[key] = entry
        return list(best.values())

    def run(
        self,
        modes: Sequence[str] | None = None,
        on_batch_collected: Any = None,
    ) -> ScrapeResult:
        """Lance tous les scrapers activés et retourne les offres dédupliquées.

        La déduplication s'effectue sur l'URL canonique normalisée ; seules les
        offres ayant passé le filtre métier (``BaseScraper.run``) sont conservées.
        Chaque passe produit en plus une ligne de télémétrie (raison d'arrêt) et
        les cartes croisées alimentent la mémoire de collecte.
        """
        seen_urls: set[str] = set()
        merged: list[RawJob] = []
        reports = []
        seen_entries: list[SeenEntry] = []
        total_found = 0
        total_rejected = 0

        for source in self.config.enabled_sources:
            scraper_cls = SCRAPER_REGISTRY.get(source)
            if scraper_cls is None:
                logger.warning("Source inconnue ignorée : %s", source)
                continue

            scraper = scraper_cls(self.config)
            try:
                result = scraper.run(
                    self.known_index,
                    modes=modes,
                    on_batch_collected=on_batch_collected,
                )
            except Exception:  # noqa: BLE001 — un scraper en erreur ne doit pas tuer le run
                logger.exception(
                    "Source %s : erreur inattendue — les sources suivantes seront quand même exécutées.",
                    source,
                )
                continue
            finally:
                scraper.close()

            total_found += result.found
            total_rejected += result.rejected_bi
            reports.extend(result.query_reports)
            seen_entries.extend(result.seen)
            for job in result.jobs:
                url_key = self._normalize_url(job.url)
                if url_key and url_key in seen_urls:
                    continue
                if url_key:
                    seen_urls.add(url_key)
                merged.append(job)

        logger.info(
            "Fusion terminée : %d offre(s) unique(s) après déduplication "
            "(%d rejetée(s), %d passe(s) tracée(s), %d offre(s) en mémoire de collecte).",
            len(merged),
            total_rejected,
            len(reports),
            len(seen_entries),
        )
        return ScrapeResult(
            jobs=merged,
            found=total_found,
            rejected_bi=total_rejected,
            query_reports=reports,
            seen=self._merge_seen(seen_entries),
        )

