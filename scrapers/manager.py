"""Orchestrateur central : lance les scrapers, déduplique et agrège les résultats.

L'orchestrateur porte l'objet le plus important de la collecte hybride : la
**mémoire de collecte** (``KnownIndex``) partagée par toutes les sources. C'est
elle qui garantit la déduplication transverse — « une offre collectée lors de la
passe Date n'est pas retraitée ni comptée dans la passe Pertinence » — et qui sert
de référence à l'arrêt anticipé.
"""

from __future__ import annotations

import concurrent.futures
import logging
import threading
from collections.abc import Sequence
from typing import Any

from .academic_rd import AcademicRDScraper
from .ats import DirectATSScraper
from .base import BaseScraper
from .cache import DiskCache
from .jobteaser import JobTeaserScraper
from .known import InMemoryKnownIndex, KnownIndex, NullKnownIndex
from .linkedin import LinkedInGuestScraper
from .models import (
    SEEN_DUPLICATE,
    SEEN_KNOWN,
    SEEN_OUT_OF_WINDOW,
    SEEN_REJECTED_BI,
    SEEN_REJECTED_CONTRACT,
    SEEN_REJECTED_TITLE,
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

# Rendre InMemoryKnownIndex thread-safe avec RLock et méthodes de consignation
if not hasattr(InMemoryKnownIndex, "_thread_safe_patched"):
    _orig_init = InMemoryKnownIndex.__init__
    _orig_is_known = InMemoryKnownIndex.is_known
    _orig_remember = InMemoryKnownIndex.remember

    def _ts_init(self: Any, *args: Any, **kwargs: Any) -> None:
        self._lock = threading.RLock()
        self._decisions: dict[tuple[str, str], str] = {}
        _orig_init(self, *args, **kwargs)

    def _ts_is_known(self: Any, source: str, external_key: str, url: str = "") -> bool:
        lock = getattr(self, "_lock", None)
        if lock is not None:
            with lock:
                return _orig_is_known(self, source, external_key, url)
        return _orig_is_known(self, source, external_key, url)

    def _ts_remember(self: Any, source: str, external_key: str, url: str = "") -> None:
        lock = getattr(self, "_lock", None)
        if lock is not None:
            with lock:
                _orig_remember(self, source, external_key, url)
        else:
            _orig_remember(self, source, external_key, url)

    def _ts_decision_for(self: Any, source: str, external_key: str, url: str = "") -> str | None:
        lock = getattr(self, "_lock", None)
        if lock is not None:
            with lock:
                return self._decisions.get((str(source), str(external_key)))
        return self._decisions.get((str(source), str(external_key)))

    def _ts_mark_collected(self: Any, source: str, external_key: str, url: str = "") -> None:
        lock = getattr(self, "_lock", None)
        if lock is not None:
            with lock:
                self.remember(source, external_key, url)
                self._decisions[(str(source), str(external_key))] = SEEN_VALIDATED
        else:
            self.remember(source, external_key, url)
            self._decisions[(str(source), str(external_key))] = SEEN_VALIDATED

    def _ts_record_seen(self: Any, entry: SeenEntry) -> None:
        lock = getattr(self, "_lock", None)
        if lock is not None:
            with lock:
                self.remember(entry.source, entry.external_key, entry.canonical_url)
                self._decisions[(str(entry.source), str(entry.external_key))] = entry.decision
        else:
            self.remember(entry.source, entry.external_key, entry.canonical_url)
            self._decisions[(str(entry.source), str(entry.external_key))] = entry.decision

    InMemoryKnownIndex.__init__ = _ts_init  # type: ignore[method-assign]
    InMemoryKnownIndex.is_known = _ts_is_known  # type: ignore[method-assign]
    InMemoryKnownIndex.remember = _ts_remember  # type: ignore[method-assign]
    InMemoryKnownIndex.decision_for = _ts_decision_for  # type: ignore[attr-defined]
    InMemoryKnownIndex.mark_collected = _ts_mark_collected  # type: ignore[attr-defined]
    InMemoryKnownIndex.record_seen = _ts_record_seen  # type: ignore[attr-defined]
    InMemoryKnownIndex._thread_safe_patched = True  # type: ignore[attr-defined]

if not hasattr(NullKnownIndex, "decision_for"):
    NullKnownIndex.decision_for = lambda self, *a, **kw: None  # type: ignore[attr-defined]
    NullKnownIndex.mark_collected = lambda self, *a, **kw: None  # type: ignore[attr-defined]
    NullKnownIndex.record_seen = lambda self, *a, **kw: None  # type: ignore[attr-defined]


SCRAPER_REGISTRY: dict[Source, type[BaseScraper]] = {
    "wttj": WelcomeToTheJungleScraper,
    "linkedin": LinkedInGuestScraper,
    "jobteaser": JobTeaserScraper,
    "ats": DirectATSScraper,
    "academic": AcademicRDScraper,
}

#: Priorité des décisions lorsqu'une même offre est croisée plusieurs fois dans le
#: même run (plusieurs requêtes, plusieurs passes) : la décision la plus
#: informative est conservée. ``DUPLICATE`` n'en porte aucune (l'offre est déjà
#: mémorisée par ailleurs dans le même run) et est donc écartée.
_DECISION_PRIORITY: dict[str, int] = {
    SEEN_VALIDATED: 5,
    SEEN_REJECTED_BI: 4,
    SEEN_REJECTED_CONTRACT: 4,
    SEEN_REJECTED_TITLE: 4,
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
        detail_cache: DiskCache | None = None,
    ) -> None:
        self.config = config or ScraperConfig()
        #: Mémoire de collecte partagée par toutes les sources (arrêt anticipé et
        #: déduplication transverse). ``NullKnownIndex`` = mode dégradé sans mémoire.
        self.known_index: KnownIndex = known_index or NullKnownIndex()
        if not hasattr(self.known_index, "_lock"):
            self.known_index._lock = threading.RLock()  # type: ignore[attr-defined]
        #: Cache disque des fiches détail, partagé avec ``backfill_descriptions``.
        self.detail_cache = detail_cache

    @classmethod
    def from_config(cls, config: dict[str, Any] | None = None, **kwargs: Any) -> ScraperManager:
        """Instancie ScraperManager depuis un dictionnaire de configuration de l'application."""
        cfg = config or {}
        scrapers_cfg = cfg.get("scrapers", {})
        automations_cfg = cfg.get("automations", {})
        parallel_sources = bool(automations_cfg.get("parallel_scraping", True))
        ats_boards = scrapers_cfg.get("ats_boards", {})

        scraper_config = ScraperConfig.from_config(cfg)
        scraper_config.parallel_sources = parallel_sources
        if ats_boards:
            scraper_config.ats_boards = ats_boards

        return cls(config=scraper_config, **kwargs)

    @classmethod
    def from_app_config(cls, config: dict[str, Any] | None = None, **kwargs: Any) -> ScraperManager:
        """Alias pour from_config."""
        return cls.from_config(config, **kwargs)

    @staticmethod
    def _normalize_url(url: str) -> str:
        """URL normalisée pour la déduplication intra-run."""
        return canonical_url(url)

    @staticmethod
    def _merge_seen(entries: Sequence[SeenEntry]) -> list[SeenEntry]:
        """Fusionne la mémoire de collecte du run : une décision par offre."""
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

        callback_lock = threading.Lock()

        def safe_on_batch_collected(batch: list[RawJob]) -> None:
            if on_batch_collected is not None and batch:
                with callback_lock:
                    try:
                        on_batch_collected(batch)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("Erreur dans on_batch_collected : %s", exc)

        def _run_source(source: Source) -> tuple[Source, ScrapeResult | None]:
            scraper_cls = SCRAPER_REGISTRY.get(source)
            if scraper_cls is None:
                logger.warning("Source inconnue ignorée : %s", source)
                return source, None

            scraper = scraper_cls(self.config)
            scraper.detail_cache = self.detail_cache
            try:
                res = scraper.run(
                    self.known_index,
                    modes=modes,
                    on_batch_collected=safe_on_batch_collected if on_batch_collected else None,
                )
                return source, res
            except Exception:  # noqa: BLE001 — un scraper en erreur ne doit pas tuer le run
                logger.exception(
                    "Source %s : erreur inattendue — les sources suivantes seront quand même exécutées.",
                    source,
                )
                return source, None
            finally:
                scraper.close()

        results_by_source: dict[Source, ScrapeResult | None] = {}

        if self.config.parallel_sources and len(self.config.enabled_sources) > 1:
            max_workers = len(self.config.enabled_sources)
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_src = {
                    executor.submit(_run_source, src): src
                    for src in self.config.enabled_sources
                }
                for future in concurrent.futures.as_completed(future_to_src):
                    src, res = future.result()
                    results_by_source[src] = res
        else:
            for src in self.config.enabled_sources:
                _, res = _run_source(src)
                results_by_source[src] = res

        # Agrégation dans l'ordre strict de self.config.enabled_sources
        for source in self.config.enabled_sources:
            result = results_by_source.get(source)
            if result is None:
                continue

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
