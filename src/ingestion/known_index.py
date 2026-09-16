"""Index de collecte persistant : mémoire ``seen_jobs`` + overlay du run courant.

Ce module est le **seul point de contact** entre le moteur de collecte (qui ne
connaît ni SQLite ni SQLAlchemy) et la persistance. Il fournit :

* ``DatabaseKnownIndex`` — préchargement de la mémoire des runs précédents
  (``seen_jobs``) puis enrichissement en mémoire pendant le run ;
* ``entries_to_rows`` — conversion des observations du run en lignes de
  ``seen_jobs`` (décision, motif de rejet, rattachement à la fiche ``jobs``).
"""
from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping

from scrapers.known import InMemoryKnownIndex
from scrapers.models import SeenEntry, canonical_url
from src.storage.database import Database, make_job_id

logger = logging.getLogger("src.ingestion.known_index")


def entries_to_rows(
    entries: Iterable[SeenEntry],
    job_ids_by_url: Mapping[str, str] | None = None,
) -> list[dict[str, object]]:
    """Convertit des observations de collecte en lignes de ``seen_jobs``.

    ``job_ids_by_url`` associe une URL canonique à l'identifiant de la fiche
    ``jobs`` correspondante (utile pour rattacher une offre retenue à sa fiche, et
    donc ne jamais l'oublier lors de l'élagage de la mémoire).
    """
    rows: list[dict[str, object]] = []
    lookup = {str(key): str(value) for key, value in (job_ids_by_url or {}).items()}
    for entry in entries:
        canonical = entry.canonical_url or canonical_url("")
        rows.append(
            {
                "source": entry.source,
                "external_key": entry.external_key,
                "canonical_url": canonical,
                "title": entry.title,
                "decision": entry.decision,
                "rejection_reason": entry.rejection_reason,
                "job_id": lookup.get(canonical),
            }
        )
    return rows


class DatabaseKnownIndex(InMemoryKnownIndex):
    """Mémoire de collecte : préchargée depuis SQLite, enrichie pendant le run.

    * le **préchargement** (une requête ``SELECT``) apporte la connaissance des
      runs précédents, y compris les offres rejetées par le filtre métier — c'est
      ce qui rend l'arrêt anticipé efficace malgré le bruit ;
    * ``remember`` met à jour les deux ensembles en mémoire : une offre vue par la
      passe « Fraîcheur » est immédiatement « connue » pour la passe « Rattrapage »
      du même run (invariant de déduplication transverse) ;
    * ``persist`` écrit enfin les observations du run en lot (upsert idempotent).
    """

    def __init__(self, db: Database, sources: Iterable[str] | None = None) -> None:
        pairs, urls = db.load_seen_index(sources)
        super().__init__(pairs, urls)
        self.db = db
        logger.info(
            "Mémoire de collecte préchargée : %d identifiant(s) plateforme et %d URL(s) canonique(s).",
            len(pairs),
            len(urls),
        )

    def persist(
        self,
        entries: Iterable[SeenEntry],
        job_ids_by_url: Mapping[str, str] | None = None,
    ) -> int:
        """Écrit les observations du run dans ``seen_jobs`` (upsert en lot)."""
        rows = entries_to_rows(entries, job_ids_by_url)
        written = self.db.upsert_seen_jobs(rows)
        logger.info("Mémoire de collecte mise à jour : %d offre(s) consignée(s).", written)
        return written


def job_ids_for_jobs(jobs: Iterable[object]) -> dict[str, str]:
    """Associe chaque URL canonique d'offre retenue à son identifiant de fiche.

    Permet de rattacher les lignes de ``seen_jobs`` aux fiches ``jobs``, afin que
    l'élagage de la mémoire ne puisse jamais oublier une offre réellement en base.
    """
    mapping: dict[str, str] = {}
    for job in jobs:
        title = str(getattr(job, "title", "") or "")
        company = str(getattr(job, "company", "") or "")
        url = str(getattr(job, "url", "") or "")
        canonical = canonical_url(url)
        if canonical:
            mapping[canonical] = make_job_id(title, company, url)
    return mapping
