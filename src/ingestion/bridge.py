"""Pont d'ingestion : offres ``RawJob`` (module ``scrapers``) -> table SQLite ``jobs``.

L'ingestion est **idempotente** : une offre déjà présente en base (même identifiant
calculé ``make_job_id`` OU même URL) est ignorée et comptabilisée comme doublon.

Elle déduplique aussi **entre plateformes** : la même annonce publiée sur LinkedIn,
Welcome to the Jungle et JobTeaser a trois URLs différentes, mais la même entreprise
(ou marque mère) et un titre équivalent (voir ``src.storage.cleanup``). Une seule
fiche est conservée — la plus documentée — ce qui évite de consommer trois fois le
quota du juge LLM pour la même offre.
"""

from __future__ import annotations

import difflib
import logging
import re
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict

from sqlalchemy import func, select

from scrapers.models import RawJob, canonical_url
from src.config import load_config
from src.constants import AUTO_ARCHIVE_REASON, CLOSED_OFFER_REASON, STATUS_NEW, TIER_NEUTRAL
from src.storage.cleanup import (
    TITLE_SIMILARITY_THRESHOLD,
    companies_match,
    normalize_company,
    title_similarity,
)
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
    scoring Bi-Encoder si elle est déclenchée. Les champs de traçabilité de la
    collecte (``id_externe``, ``canonical_url``, ``published_at``) sont renseignés
    pour que la déduplication, l'arrêt anticipé et la fenêtre temporelle disposent
    des mêmes identités que le scraper au run suivant.
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
        "id_externe": (job.id_externe or None),
        "canonical_url": canonical_url(job.url),
        "published_at": _naive_utc(job.published_at),
    }


def _naive_utc(value: datetime | None) -> datetime | None:
    """Convertit une date en UTC naïf (convention de stockage de la base)."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


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


#: Similarité minimale des descriptions COMPLÈTES pour qu'une offre de la MÊME
#: plateforme soit une republication. Le texte entier, pas son début : les grands
#: groupes (Thales sur Workday) publient plusieurs sujets distincts sous le même
#: titre avec le même paragraphe de présentation en tête.
REPOST_DESCRIPTION_SIMILARITY = 0.92
_REPOST_PREFIX_CHARS = 8000


def normalized_text(text: str | None) -> str:
    return re.sub(r"\W+", " ", (text or "").casefold()).strip()


def description_similarity(a: str | None, b: str | None) -> float:
    """Similarité (0 à 1) des débuts de description normalisés ; 0 si l'une est vide."""
    na, nb = normalized_text(a)[:_REPOST_PREFIX_CHARS], normalized_text(b)[:_REPOST_PREFIX_CHARS]
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return difflib.SequenceMatcher(None, na, nb, autojunk=False).ratio()


def is_repost(
    company_a: str, title_a: str, description_a: str | None,
    company_b: str, title_b: str, description_b: str | None,
    location_a: str | None = None, location_b: str | None = None,
) -> bool:
    """Même offre republiée (même plateforme ou non) : entreprise, titre ET texte."""
    return (
        companies_match(company_a, company_b)
        and normalized_text(title_a) == normalized_text(title_b)
        and same_city(location_a, location_b)
        and description_similarity(description_a, description_b) >= REPOST_DESCRIPTION_SIMILARITY
    )


def same_city(a: str | None, b: str | None) -> bool:
    """Même ville (premier élément du lieu), ou lieu inconnu d'un côté."""
    ca, cb = normalized_text((a or "").split(",")[0]), normalized_text((b or "").split(",")[0])
    return not ca or not cb or ca == cb


def _company_bucket(company: str) -> str:
    """Clé de regroupement : premier mot de l'entreprise normalisée.

    ``companies_match`` accepte l'égalité ou l'inclusion de marque par préfixe de
    mots : deux entreprises compatibles partagent donc toujours leur premier mot.
    """
    normalized = normalize_company(company)
    return normalized.split()[0] if normalized else ""


def _same_offer(
    source_a: str, company_a: str, title_a: str, source_b: str, company_b: str, title_b: str
) -> bool:
    """Même annonce publiée sur deux plateformes différentes ?

    Limité aux paires **inter-sources** : sur une même plateforme, deux
    identifiants distincts désignent en général deux offres distinctes (deux équipes
    recrutant un « Data Scientist »), que l'on ne veut pas fusionner.
    """
    return (
        source_a != source_b
        and companies_match(company_a, company_b)
        and title_similarity(title_a, title_b) >= TITLE_SIMILARITY_THRESHOLD
    )


def _load_cross_source_candidates(
    db: Database, buckets: set[str]
) -> dict[str, list[dict[str, Any]]]:
    """Fiches en base susceptibles d'être la même annonce, par clé d'entreprise."""
    by_bucket: dict[str, list[dict[str, Any]]] = {}
    if not buckets:
        return by_bucket
    with db.SessionLocal() as session:
        rows = session.execute(
            select(
                Job.id,
                Job.source,
                Job.company,
                Job.title,
                func.length(func.coalesce(Job.description, "")),
                Job.description,
                Job.rejection_reason,
                Job.location,
            )
        ).all()
    for job_id, source, company, title, description_length, description, reason, location in rows:
        bucket = _company_bucket(str(company or ""))
        if bucket in buckets:
            by_bucket.setdefault(bucket, []).append(
                {
                    "id": job_id,
                    "source": str(source or ""),
                    "company": str(company or ""),
                    "title": str(title or ""),
                    "description_length": int(description_length or 0),
                    "description": str(description or ""),
                    "location": str(location or ""),
                    # Une offre fermée ou archivée qui réapparaît est de nouveau ouverte :
                    # sa republication n'est pas un doublon.
                    "closed": str(reason or "") in (AUTO_ARCHIVE_REASON, CLOSED_OFFER_REASON),
                }
            )
    return by_bucket


def _partition_new_jobs(
    jobs: Iterable[RawJob], db: Database
) -> tuple[list[RawJob], int, list[tuple[str, str]]]:
    """Sépare les offres inédites des doublons (déjà en base ou dans le lot).

    Trois niveaux de déduplication :

    1. URL canonique et identifiant calculé (même plateforme, liens variables) ;
    2. même annonce sur une **autre plateforme** déjà en base (entreprise et titre
       équivalents), ou **republication** sur la même plateforme (même entreprise,
       même titre et texte similaire, voir ``is_repost``) : l'offre est ignorée,
       sauf si la fiche en base est fermée ou archivée ; si la fiche en base n'a pas
       de description et que la nouvelle en a une, la description est récupérée ;
    3. même annonce sur plusieurs plateformes **dans le lot** : seule la version
       à la description la plus longue est conservée.

    Returns:
        ``(nouvelles_offres, nombre_de_doublons, descriptions_à_reporter)`` où le
        dernier élément liste des couples ``(id_fiche_existante, description)``.
    """
    prepared: list[tuple[RawJob, str, dict[str, Any]]] = []
    seen_urls: set[str] = set()
    duplicates = 0

    for job in jobs:
        record = raw_job_to_dict(job)
        job_id = make_job_id(record["title"], record["company"], record["url"])
        # Déduplication par URL CANONIQUE (schéma, ``www.``, query string, fragment
        # et slash final neutralisés) : deux entrées de la même offre issues de
        # chemins différents étaient auparavant considérées comme distinctes.
        url_key = record["canonical_url"] or canonical_url(record["url"])
        if url_key and url_key in seen_urls:
            duplicates += 1
            continue
        if url_key:
            seen_urls.add(url_key)
        prepared.append((job, job_id, record))

    if not prepared:
        return [], duplicates, []

    ids = [job_id for _, job_id, _ in prepared]
    urls = [record["canonical_url"] for _, _, record in prepared]
    with db.SessionLocal() as session:
        existing_ids: set[str] = set(session.execute(select(Job.id).where(Job.id.in_(ids))).scalars())
        existing_urls = {
            str(value)
            for value in session.execute(
                select(Job.canonical_url).where(Job.canonical_url.in_(urls))
            ).scalars()
            if value
        }

    candidates: list[RawJob] = []
    for job, job_id, record in prepared:
        url_key = record["canonical_url"] or ""
        if job_id in existing_ids or (url_key and url_key in existing_urls):
            duplicates += 1
            continue
        candidates.append(job)
        existing_ids.add(job_id)
        if url_key:
            existing_urls.add(url_key)

    # --- Déduplication inter-plateformes -------------------------------------
    buckets = {bucket for job in candidates if (bucket := _company_bucket(job.company))}
    in_database = _load_cross_source_candidates(db, buckets)
    description_updates: dict[str, str] = {}
    kept_by_bucket: dict[str, list[int]] = {}
    new_jobs: list[RawJob] = []

    for job in candidates:
        bucket = _company_bucket(job.company)
        description = (job.description or "").strip()
        existing = next(
            (
                row
                for row in in_database.get(bucket, [])
                if _same_offer(
                    job.source, job.company, job.title, row["source"], row["company"], row["title"]
                )
                or (
                    not row["closed"]
                    and is_repost(
                        job.company, job.title, description,
                        row["company"], row["title"], row["description"],
                        job.location, row["location"],
                    )
                )
            ),
            None,
        )
        if existing is not None:
            duplicates += 1
            if description and existing["description_length"] == 0:
                description_updates[existing["id"]] = description
                existing["description_length"] = len(description)
            logger.debug(
                "Doublon inter-plateformes ignoré : %s (%s) = fiche %s (%s).",
                job.title,
                job.source,
                existing["id"],
                existing["source"],
            )
            continue

        rival_index = next(
            (
                index
                for index in kept_by_bucket.get(bucket, [])
                if _same_offer(
                    job.source,
                    job.company,
                    job.title,
                    new_jobs[index].source,
                    new_jobs[index].company,
                    new_jobs[index].title,
                )
            ),
            None,
        )
        if rival_index is not None:
            duplicates += 1
            if len(description) > len((new_jobs[rival_index].description or "").strip()):
                new_jobs[rival_index] = job  # la version la plus documentée l'emporte
            continue

        if bucket:
            kept_by_bucket.setdefault(bucket, []).append(len(new_jobs))
        new_jobs.append(job)

    return new_jobs, duplicates, list(description_updates.items())


def find_new_raw_jobs(
    jobs: Iterable[RawJob], db_session_or_path: Database | str | Path | None = None
) -> list[RawJob]:
    """Retourne les offres absentes de la base (utile pour le scoring ciblé)."""
    db = resolve_database(db_session_or_path)
    new_jobs, _, _ = _partition_new_jobs(list(jobs), db)
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

    new_jobs, duplicates, description_updates = _partition_new_jobs(job_list, db)
    for existing_id, description in description_updates:
        db.update_description(existing_id, description)
    if description_updates:
        logger.info(
            "Ingestion : %d description(s) récupérée(s) depuis une autre plateforme.",
            len(description_updates),
        )
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
