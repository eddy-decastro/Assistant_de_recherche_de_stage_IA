"""Couche d'accès aux données SQLite via SQLAlchemy.

Expose le schéma de la table ``jobs`` ainsi qu'une classe ``Database``
fournissant les opérations CRUD nécessaires au pipeline et au dashboard.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
    event,

    func,
    select,
)
from sqlalchemy.engine import Engine

from sqlalchemy.orm import Session, declarative_base, sessionmaker

from src.constants import STATUS_NEW, TIER_ESN, VALID_STATUSES

Base = declarative_base()

# Colonnes stockant du JSON sérialisé (listes de chaînes).
_JSON_COLUMNS = ("match_reasons", "red_flags", "tech_stack")
# --- Robustesse SQLite ------------------------------------------------------ #
# WAL : le dashboard peut LIRE pendant qu'un scraper ÉCRIT (fin des
# ``database is locked`` quand les deux tournent en même temps).
# busy_timeout : on patiente 5 s au lieu d'échouer immédiatement sur un verrou.
SQLITE_BUSY_TIMEOUT_MS = 5000


def configure_sqlite_engine(engine: Engine) -> None:
    """Applique les PRAGMA de robustesse à chaque connexion SQLite ouverte.

    Sans effet sur un moteur non SQLite (le projet ne cible que SQLite, mais la
    garde évite de casser d'éventuels usages sur un autre backend).
    """
    if engine.dialect.name != "sqlite":
        return

    @event.listens_for(engine, "connect")
    def _apply_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()




def _decode_json_list(value: Any) -> list[str]:
    """Décode une colonne JSON en liste de chaînes (tolérant aux erreurs)."""
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(v) for v in parsed]
    except (json.JSONDecodeError, TypeError):
        pass
    return [str(value)]


def _encode_json_list(value: Any) -> str:
    """Sérialise une liste en JSON (idempotent si la valeur est déjà une chaîne)."""
    if value is None:
        return "[]"
    if isinstance(value, str):
        return value
    return json.dumps(list(value), ensure_ascii=False)


class Job(Base):
    """Une offre de stage collectée et scorée."""

    __tablename__ = "jobs"

    id = Column(String(64), primary_key=True)
    title = Column(String(500), nullable=False)
    company = Column(String(300), nullable=False, index=True)
    location = Column(String(300), nullable=True)
    url = Column(String(2000), nullable=False)
    description = Column(Text, nullable=True)
    source = Column(String(100), nullable=True)
    company_tier = Column(Integer, default=2, nullable=False)
    semantic_score = Column(Float, default=0.0, nullable=False)
    final_score = Column(Float, default=0.0, nullable=False, index=True)
    status = Column(String(30), default=STATUS_NEW, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # --- Étape 2 : reranking par juge LLM (NULL tant que non évalué) ---
    rerank_score = Column(Float, nullable=True)
    verdict = Column(String(30), nullable=True)
    match_reasons = Column(Text, nullable=True)   # JSON : liste de chaînes
    red_flags = Column(Text, nullable=True)       # JSON : liste de chaînes
    tech_stack = Column(Text, nullable=True)      # JSON : liste de chaînes

    def to_dict(self) -> dict[str, Any]:
        data = {column.name: getattr(self, column.name) for column in self.__table__.columns}
        for name in _JSON_COLUMNS:
            data[name] = _decode_json_list(data.get(name))
        return data


def make_job_id(title: str, company: str, url: str) -> str:
    """Calcule un identifiant unique (hash SHA256) pour une offre."""
    raw = f"{title}|{company}|{url}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class Database:
    """Gestionnaire de la base SQLite (session SQLAlchemy)."""

    def __init__(self, db_path: str | Path = "data/stage_copilot.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(f"sqlite:///{self.db_path}", future=True)
        configure_sqlite_engine(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, future=True, expire_on_commit=False)
        Base.metadata.create_all(self.engine)
        self._migrate()

    def _migrate(self) -> None:
        """Migration légère : ajoute les colonnes manquantes sur une base existante."""
        expected = {
            "rerank_score": "FLOAT",
            "verdict": "VARCHAR(30)",
            "match_reasons": "TEXT",
            "red_flags": "TEXT",
            "tech_stack": "TEXT",
        }
        with self.engine.begin() as conn:
            existing = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(jobs)")}
            for column, sql_type in expected.items():
                if column not in existing:
                    conn.exec_driver_sql(f"ALTER TABLE jobs ADD COLUMN {column} {sql_type}")

    def upsert_job(self, job: dict[str, Any]) -> Job:
        """Insère une offre sans doublon (clé = id) ou met à jour ses scores."""
        job_id = job.get("id") or make_job_id(job["title"], job["company"], job["url"])
        fields: dict[str, Any] = {}
        for key, value in job.items():
            if key == "id" or not hasattr(Job, key):
                continue
            fields[key] = _encode_json_list(value) if key in _JSON_COLUMNS else value
        with self.SessionLocal() as session:
            record = session.get(Job, job_id)
            if record is None:
                record = Job(id=job_id, **fields)
                session.add(record)
            else:
                for key, value in fields.items():
                    setattr(record, key, value)
            session.commit()
            return record

    def update_status(self, job_id: str, status: str) -> bool:
        """Met à jour le statut d'une offre. Retourne False si l'offre n'existe pas."""
        if status not in VALID_STATUSES:
            raise ValueError(f"Statut invalide: {status!r} (attendu: {sorted(VALID_STATUSES)})")
        with self.SessionLocal() as session:
            record = session.get(Job, job_id)
            if record is None:
                return False
            record.status = status
            session.commit()
            return True

    def get_jobs(
        self,
        min_score: float = 0.0,
        statuses: str | Iterable[str] | None = None,
        exclude_esn: bool = False,
        tiers: Iterable[int] | None = None,
        sources: Iterable[str] | None = None,
        limit: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Récupère les offres filtrées, triées par score décroissant.

        ``sources`` restreint la sélection aux plateformes d'origine
        (``jobs.source`` : ``linkedin``, ``wttj``/``welcome_to_the_jungle``,
        ``jobteaser``). ``None`` ou liste vide = toutes les plateformes.
        """
        with self.SessionLocal() as session:
            stmt = select(Job)
            if min_score:
                stmt = stmt.where(Job.final_score >= min_score)
            if statuses:
                if isinstance(statuses, str):
                    statuses = [statuses]
                stmt = stmt.where(Job.status.in_(list(statuses)))
            if exclude_esn:
                stmt = stmt.where(Job.company_tier != TIER_ESN)
            if tiers:
                stmt = stmt.where(Job.company_tier.in_(list(tiers)))
            if sources:
                stmt = stmt.where(Job.source.in_(list(sources)))
            # Tri : privilégier le score de reranking LLM, sinon le score initial.
            stmt = stmt.order_by(func.coalesce(Job.rerank_score, Job.final_score).desc())
            if limit:
                stmt = stmt.limit(limit)
            rows = session.execute(stmt).scalars().all()
            return [row.to_dict() for row in rows]

    def get_source_counts(self) -> list[tuple[str, int]]:
        """Compte les offres par plateforme source (ordre décroissant).

        Returns:
            Liste de tuples ``(source, nombre_d_offres)``. Les offres sans source
            renseignée sont regroupées sous une chaîne vide.
        """
        with self.SessionLocal() as session:
            stmt = (
                select(Job.source, func.count())
                .group_by(Job.source)
                .order_by(func.count().desc())
            )
            return [
                (str(source) if source else "", int(count))
                for source, count in session.execute(stmt).all()
            ]

    def update_rerank(
        self,
        job_id: str,
        rerank_score: float,
        verdict: str | None = None,
        match_reasons: Iterable[str] | None = None,
        red_flags: Iterable[str] | None = None,
        tech_stack: Iterable[str] | None = None,
    ) -> bool:
        """Enregistre l'analyse fine (étape 2) d'une offre. False si introuvable."""
        with self.SessionLocal() as session:
            record = session.get(Job, job_id)
            if record is None:
                return False
            record.rerank_score = float(rerank_score)
            record.verdict = verdict
            record.match_reasons = _encode_json_list(match_reasons)
            record.red_flags = _encode_json_list(red_flags)
            record.tech_stack = _encode_json_list(tech_stack)
            session.commit()
            return True

    def get_unranked_jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        """Top des offres NON encore évaluées par le juge LLM (tri par score initial)."""
        with self.SessionLocal() as session:
            stmt = (
                select(Job)
                .where(Job.rerank_score.is_(None))
                .order_by(Job.final_score.desc())
                .limit(limit)
            )
            return [row.to_dict() for row in session.execute(stmt).scalars().all()]

    def update_description(self, job_id: str, description: str) -> bool:
        """Enregistre la description complète d'une offre. False si introuvable."""
        with self.SessionLocal() as session:
            record = session.get(Job, job_id)
            if record is None:
                return False
            record.description = description
            session.commit()
            return True

    def get_jobs_missing_description(
        self, sources: Iterable[str] | None = None, limit: Optional[int] = None
    ) -> list[dict[str, Any]]:
        """Offres sans description exploitable (cibles du rattrapage).

        Tri par score effectif décroissant : les offres qui comptent le plus sont
        enrichies en premier si le rattrapage est interrompu (rate limit).
        """
        with self.SessionLocal() as session:
            stmt = select(Job).where(func.coalesce(func.trim(Job.description), "") == "")
            if sources:
                stmt = stmt.where(Job.source.in_(list(sources)))
            stmt = stmt.order_by(func.coalesce(Job.rerank_score, Job.final_score).desc())
            if limit:
                stmt = stmt.limit(limit)
            return [row.to_dict() for row in session.execute(stmt).scalars().all()]

    def count_with_description(self) -> int:
        """Nombre d'offres disposant d'une description non vide."""
        with self.SessionLocal() as session:
            stmt = (
                select(func.count())
                .select_from(Job)
                .where(func.coalesce(func.trim(Job.description), "") != "")
            )
            return int(session.execute(stmt).scalar_one())

    def clear_rerank(self, limit: int) -> list[str]:
        """Remet à zéro l'analyse LLM des ``limit`` meilleures offres.

        Sert à la ré-évaluation forcée : des offres déjà jugées — par exemple sur
        un titre seul, faute de description — redeviennent candidates au Top-N.
        Les valeurs précédentes sont récupérables via un snapshot
        (``compare_scores.py --snapshot``). Retourne les identifiants réinitialisés.
        """
        with self.SessionLocal() as session:
            stmt = (
                select(Job)
                .where(Job.rerank_score.is_not(None))
                .order_by(func.coalesce(Job.rerank_score, Job.final_score).desc())
                .limit(max(0, int(limit)))
            )
            records = session.execute(stmt).scalars().all()
            ids: list[str] = []
            for record in records:
                record.rerank_score = None
                record.verdict = None
                record.match_reasons = None
                record.red_flags = None
                record.tech_stack = None
                ids.append(record.id)
            session.commit()
            return ids

    def count_jobs(self, status: Optional[str] = None) -> int:
        """Compte le nombre d'offres (optionnellement filtrées par statut)."""
        with self.SessionLocal() as session:
            stmt = select(func.count()).select_from(Job)
            if status:
                stmt = stmt.where(Job.status == status)
            return int(session.execute(stmt).scalar_one())

    def count_ranked(self) -> int:
        """Nombre d'offres déjà évaluées par le juge LLM (étape 2)."""
        with self.SessionLocal() as session:
            stmt = select(func.count()).select_from(Job).where(Job.rerank_score.is_not(None))
            return int(session.execute(stmt).scalar_one())
