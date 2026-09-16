"""Pont d'ingestion : offres ``RawJob`` (module ``scrapers``) -> table SQLite ``jobs``.

L'ingestion est **idempotente** : une offre déjà présente en base (même identifiant
calculé ``make_job_id`` OU même URL) est ignorée et comptabilisée comme doublon.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any, TypedDict

from sqlalchemy import select

from scrapers.models import RawJob
from src.config import load_config
from src.constants import STATUS_NEW, TIER_NEUTRAL
from src.storage.database import Database, Job, make_job_id

logger = logging.getLogger("src.ingestion.bridge")

DEFAULT_DB_PATH = "data/stage_copilot.db"


class IngestStats(TypedDict):
    """Statistiques retournées par :func:`ingest_raw_jobs`."""

    total_scraped: int
    new_inserted: int
    duplicates_skipped: int


def raw_job_to_dict(job: RawJob) -> dict[str, Any]:
    """Convertit un ``RawJob`` en dictionnaire compatible avec la table ``jobs``.

    Les scores sont neutralisés (``0.0``) : ils seront calculés par l'étape de
    scoring Bi-Encoder si elle est déclenchée.
    """
    return {
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "url": job.url,
        "description": job.description,
        "source": job.source,
        "company_tier": TIER_NEUTRAL,
        "semantic_score": 0.0,
        "final_score": 0.0,
        "status": STATUS_NEW,
    }


def resolve_database(db_session_or_path: Database | str | Path | None = None) -> Database:
    """Résout l'argument ``db_session_or_path`` en une instance ``Database``.

    Accepte une instance ``Database`` existante, un chemin (``str``/``Path``) ou
    ``None`` (auquel cas le chemin est lu depuis ``config.yaml``).
    """
    if isinstance(db_session_or_path, Database):
        return db_session_or_path
    if db_session_or_path is None:
        config = load_config()
        path = config.get("database", {}).get("path", DEFAULT_DB_PATH)
        return Database(path)
    return Database(db_session_or_path)


def _partition_new_jobs(jobs: Iterable[RawJob], db: Database) -> tuple[list[RawJob], int]:
    """Sépare les offres inédites des doublons (déjà en base ou dans le lot).

    Returns:
        ``(nouvelles_offres, nombre_de_doublons)``.
    """
    prepared: list[tuple[RawJob, str, dict[str, Any]]] = []
    seen_urls: set[str] = set()
    duplicates = 0

    for job in jobs:
        record = raw_job_to_dict(job)
        job_id = make_job_id(record["title"], record["company"], record["url"])
        url_key = record["url"].strip().casefold()
        if url_key and url_key in seen_urls:
            duplicates += 1
            continue
        if url_key:
            seen_urls.add(url_key)
        prepared.append((job, job_id, record))

    if not prepared:
        return [], duplicates

    ids = [job_id for _, job_id, _ in prepared]
    urls = [record["url"] for _, _, record in prepared]
    with db.SessionLocal() as session:
        existing_ids = set(session.execute(select(Job.id).where(Job.id.in_(ids))).scalars())
        existing_urls = {
            str(value).strip().casefold()
            for value in session.execute(select(Job.url).where(Job.url.in_(urls))).scalars()
        }

    new_jobs: list[RawJob] = []
    for job, job_id, record in prepared:
        url_key = record["url"].strip().casefold()
        if job_id in existing_ids or (url_key and url_key in existing_urls):
            duplicates += 1
            continue
        new_jobs.append(job)
        existing_ids.add(job_id)
        if url_key:
            existing_urls.add(url_key)

    return new_jobs, duplicates


def find_new_raw_jobs(
    jobs: Iterable[RawJob], db_session_or_path: Database | str | Path | None = None
) -> list[RawJob]:
    """Retourne les offres absentes de la base (utile pour le scoring ciblé)."""
    db = resolve_database(db_session_or_path)
    new_jobs, _ = _partition_new_jobs(list(jobs), db)
    return new_jobs


def ingest_raw_jobs(
    jobs: Iterable[RawJob], db_session_or_path: Database | str | Path | None = None
) -> IngestStats:
    """Insère les offres ``RawJob`` en base en ignorant les doublons (idempotent).

    Returns:
        ``{"total_scraped": X, "new_inserted": Y, "duplicates_skipped": Z}``.
    """
    db = resolve_database(db_session_or_path)
    job_list = list(jobs)
    total = len(job_list)
    if total == 0:
        logger.info("Aucune offre à ingérer.")
        return IngestStats(total_scraped=0, new_inserted=0, duplicates_skipped=0)

    new_jobs, duplicates = _partition_new_jobs(job_list, db)
    if new_jobs:
        with db.SessionLocal() as session:
            session.add_all(
                [
                    Job(id=make_job_id(job.title, job.company, job.url), **raw_job_to_dict(job))
                    for job in new_jobs
                ]
            )
            session.commit()

    stats = IngestStats(
        total_scraped=total,
        new_inserted=len(new_jobs),
        duplicates_skipped=duplicates,
    )
    logger.info(
        "Ingestion SQLite : %d collectée(s), %d nouvelle(s), %d doublon(s) ignoré(s).",
        stats["total_scraped"],
        stats["new_inserted"],
        stats["duplicates_skipped"],
    )
    return stats
