"""Rattrapage des descriptions manquantes (pages détail LinkedIn / JobTeaser).

**Constat mesuré** : les pages de LISTE n'exposent pas la description (cartes
LinkedIn invité et cartes JobTeaser) — 100 % des offres en base n'avaient qu'un
titre. Or la description conditionne tout l'aval : filtre anti-BI, similarité
sémantique (étape 1) et jugement LLM (étape 2).

Ce script visite la page DÉTAIL de chaque offre concernée, met le texte en cache
disque (``data/cache/<source>/``) et met à jour la colonne ``description``.

    python backfill_descriptions.py --limit 5 --dry-run   # test sans écriture
    python backfill_descriptions.py                       # rattrapage complet
    python backfill_descriptions.py --source linkedin --sleep 3
    python backfill_descriptions.py --refresh             # relit tout

Le rattrapage est **reprenable** : chaque description est écrite immédiatement,
un arrêt (429, Ctrl+C, coupure réseau) ne fait donc perdre que l'offre en cours ;
le cache évite de refaire les appels déjà réussis.
"""
from __future__ import annotations

import argparse
import logging
import random
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import httpx  # noqa: E402

from scrapers.cache import DEFAULT_CACHE_DIR, DiskCache  # noqa: E402
from scrapers.jobteaser import JobTeaserScraper  # noqa: E402
from scrapers.linkedin import LinkedInGuestScraper  # noqa: E402
from scrapers.models import ScraperConfig  # noqa: E402
from src.config import load_config  # noqa: E402
from src.storage.database import Database  # noqa: E402

logger = logging.getLogger("backfill")

# Sources dont la description est récupérable depuis une page détail publique.
SUPPORTED_SOURCES = ("linkedin", "jobteaser")

# En dessous de ce seuil, l'extraction est jugée ratée (page vide, mur de
# connexion, changement de structure) : mieux vaut ne rien écrire qu'un résidu.
MIN_DESCRIPTION_LENGTH = 150

# Garde-fou réseau : on interrompt une source après N échecs d'affilée
# (panne, blocage) au lieu de marteler le site.
MAX_CONSECUTIVE_FAILURES = 5


@dataclass
class BackfillReport:
    """Bilan d'un rattrapage (compteurs globaux + détail par plateforme)."""

    targets: int = 0
    enriched: int = 0
    written: int = 0
    failed: int = 0
    skipped: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    per_source: dict[str, dict[str, int]] = field(default_factory=dict)
    rate_limited: list[str] = field(default_factory=list)


class DescriptionBackfill:
    """Télécharge les descriptions des offres sélectionnées en base."""

    def __init__(
        self,
        config: dict[str, Any],
        db: Database,
        cache: DiskCache | None = None,
        *,
        sleep: float = 2.0,
        dry_run: bool = False,
        min_length: int = MIN_DESCRIPTION_LENGTH,
        max_consecutive_failures: int = MAX_CONSECUTIVE_FAILURES,
    ) -> None:
        self.config = config
        self.db = db
        self.cache = cache if cache is not None else DiskCache()
        self.sleep = max(0.0, float(sleep))
        self.dry_run = dry_run
        self.min_length = max(0, int(min_length))
        self.max_consecutive_failures = max(1, int(max_consecutive_failures))
        self.report = BackfillReport()
        self.scrapers: dict[str, Any] = self._build_scrapers()

    def _build_scrapers(self) -> dict[str, Any]:
        """Instancie les scrapers utilisés pour le rattrapage (clé = source)."""
        scraper_config = ScraperConfig.from_config(self.config)
        return {
            "linkedin": LinkedInGuestScraper(scraper_config),
            "jobteaser": JobTeaserScraper(scraper_config),
        }

    def close(self) -> None:
        """Ferme les clients HTTP des scrapers."""
        for scraper in self.scrapers.values():
            scraper.close()

    # ------------------------------------------------------------------ #
    # Sélection puis traitement
    # ------------------------------------------------------------------ #
    def _select_jobs(self, source: str, limit: int | None, refresh: bool) -> list[dict[str, Any]]:
        """Offres à traiter : sans description, ou toutes si ``refresh``."""
        if refresh:
            return self.db.get_jobs(sources=[source], limit=limit)
        return self.db.get_jobs_missing_description(sources=[source], limit=limit)

    def _pause(self) -> None:
        """Pause anti-flood entre deux appels réseau (avec un peu d'aléa)."""
        if self.sleep:
            time.sleep(self.sleep + random.uniform(0.0, 0.5))

    def run(
        self, sources: list[str] | None = None, limit: int | None = None, refresh: bool = False
    ) -> BackfillReport:
        """Enrichit les offres des sources demandées et retourne le bilan."""
        self.report = BackfillReport()
        selected = list(sources) if sources else list(SUPPORTED_SOURCES)

        for source in selected:
            jobs = self._select_jobs(source, limit, refresh)
            self.report.per_source[source] = {"targets": len(jobs), "enriched": 0, "failed": 0}
            self.report.targets += len(jobs)
            if not jobs:
                logger.info(" %s : aucune offre à enrichir.", source)
                continue
            if source not in self.scrapers:
                logger.warning(" %s : source non supportée par le rattrapage (ignorée).", source)
                self.report.skipped += len(jobs)
                continue
            logger.info(" %s : %d offre(s) à enrichir…", source, len(jobs))
            self._process_source(source, jobs)

        self.report.cache_hits = self.cache.hits
        self.report.cache_misses = self.cache.misses
        return self.report

    def _process_source(self, source: str, jobs: list[dict[str, Any]]) -> None:
        """Traite une plateforme (arrêt propre sur 429 ou échecs répétés)."""
        report = self.report
        stats = report.per_source[source]
        scraper = self.scrapers[source]
        total = len(jobs)
        consecutive_failures = 0

        for index, job in enumerate(jobs, start=1):
            label = (job.get("title") or job.get("url") or "")[:52]
            text = ""
            failure = ""
            status = 0
            try:
                text = scraper.fetch_description(job["url"], cache=self.cache)
            except httpx.HTTPStatusError as exc:
                status = int(exc.response.status_code)
                failure = f"HTTP {status}"
            except (httpx.HTTPError, OSError) as exc:
                failure = f"erreur réseau ({exc})"
            except Exception as exc:  # noqa: BLE001 - transport curl_cffi / inattendu
                failure = f"erreur inattendue ({exc})"

            if failure:
                consecutive_failures += 1
                stats["failed"] += 1
                report.failed += 1
                logger.warning(" %s %d/%d : %s — %s", source, index, total, failure, label)
                if status == 429:
                    report.rate_limited.append(source)
                    logger.warning(
                        " %s : rate limit (429) — source interrompue ; relancer plus tard "
                        "(cache et base rendent le rattrapage reprenable).",
                        source,
                    )
                    return
                if consecutive_failures >= self.max_consecutive_failures:
                    logger.warning(
                        " %s : %d échecs consécutifs — source interrompue.",
                        source,
                        consecutive_failures,
                    )
                    return
                self._pause()
                continue

            consecutive_failures = 0
            if len(text) < self.min_length:
                stats["failed"] += 1
                report.failed += 1
                logger.warning(
                    " %s %d/%d : description trop courte (%d car.) — ignorée — %s",
                    source,
                    index,
                    total,
                    len(text),
                    label,
                )
                self._pause()
                continue

            if not self.dry_run and not self.db.update_description(job["id"], text):
                stats["failed"] += 1
                report.failed += 1
                logger.warning(" %s : offre introuvable en base (%s).", source, job["id"][:12])
                self._pause()
                continue

            stats["enriched"] += 1
            report.enriched += 1
            if not self.dry_run:
                report.written += 1
            logger.info(
                " %s %d/%d : %d caractères%s — %s",
                source,
                index,
                total,
                len(text),
                " (simulation)" if self.dry_run else " (enregistrée)",
                label,
            )
            if index < total:
                self._pause()

def log_report(report: BackfillReport, db: Database, *, dry_run: bool) -> None:
    """Affiche le bilan du run et l'état de complétion de la base."""
    total = db.count_jobs()
    with_description = db.count_with_description()
    logger.info("=" * 60)
    logger.info(" RAPPORT DE RATTRAPAGE DES DESCRIPTIONS%s", " (SIMULATION)" if dry_run else "")
    logger.info("=" * 60)
    for source, stats in report.per_source.items():
        logger.info(
            " %-10s : %3d cible(s) — %3d enrichie(s), %3d échec(s)",
            source,
            stats["targets"],
            stats["enriched"],
            stats["failed"],
        )
    if report.skipped:
        logger.info(" %-10s : %3d offre(s) ignorée(s) (source non supportée)", "—", report.skipped)
    logger.info(
        " Cache disque                  : %d appel(s) évité(s), %d appel(s) réseau",
        report.cache_hits,
        report.cache_misses,
    )
    if report.rate_limited:
        logger.warning(
            " Rate limit (429)              : %s — relancer le script plus tard.",
            ", ".join(sorted(set(report.rate_limited))),
        )
    logger.info("-" * 60)
    logger.info(
        " RÉSULTAT : %d/%d offres enrichies avec succès%s",
        report.enriched,
        report.targets,
        " (simulation : aucune écriture en base)" if dry_run else "",
    )
    logger.info(" Complétion de la base         : %d/%d offres avec description", with_description, total)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Analyse les arguments de la ligne de commande."""
    parser = argparse.ArgumentParser(
        description="Rattrape les descriptions manquantes depuis les pages détail."
    )
    parser.add_argument(
        "--source",
        choices=[*SUPPORTED_SOURCES, "all"],
        default="all",
        help="Plateforme à traiter (défaut : toutes).",
    )
    parser.add_argument("--limit", type=int, default=None, help="Plafond d'offres par plateforme.")
    parser.add_argument(
        "--sleep",
        type=float,
        default=2.0,
        help="Pause (s) entre deux appels réseau (défaut : 2.0).",
    )
    parser.add_argument(
        "--min-length",
        type=int,
        default=MIN_DESCRIPTION_LENGTH,
        help=f"Longueur minimale acceptée pour une description (défaut : {MIN_DESCRIPTION_LENGTH}).",
    )
    parser.add_argument(
        "--cache-dir", default=DEFAULT_CACHE_DIR, help="Répertoire du cache disque."
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Retraite aussi les offres qui possèdent déjà une description.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Aucune écriture en base (test des extracteurs)."
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée : sélectionne, télécharge, enregistre puis affiche le bilan."""
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("httpx", "httpcore", "urllib3", "curl_cffi"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    config = load_config()
    db = Database(config["database"]["path"])
    cache = DiskCache(args.cache_dir)
    sources = list(SUPPORTED_SOURCES) if args.source == "all" else [args.source]

    runner = DescriptionBackfill(
        config,
        db,
        cache,
        sleep=args.sleep,
        dry_run=args.dry_run,
        min_length=args.min_length,
    )
    logger.info(
        " Rattrapage des descriptions    : %s | %s | pause %.1f s",
        ", ".join(sources),
        "simulation (aucune écriture)" if args.dry_run else "écriture en base",
        args.sleep,
    )
    report = runner.report
    try:
        report = runner.run(sources, limit=args.limit, refresh=args.refresh)
    except KeyboardInterrupt:
        logger.warning(
            " Interruption manuelle — les %d description(s) déjà récupérée(s) sont enregistrées.",
            runner.report.enriched,
        )
        report = runner.report
    finally:
        runner.close()
        log_report(report, db, dry_run=args.dry_run)
        db.engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
