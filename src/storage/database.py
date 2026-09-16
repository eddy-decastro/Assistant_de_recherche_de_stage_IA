"""Couche d'accès aux données SQLite via SQLAlchemy.

Expose le schéma de la table ``jobs`` ainsi qu'une classe ``Database``
fournissant les opérations CRUD nécessaires au pipeline et au dashboard.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional
from uuid import uuid4

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
    delete,
    event,

    func,
    select,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine

from sqlalchemy.orm import Session, declarative_base, sessionmaker

from src.constants import (
    DEFAULT_SUB_SCORE,
    RUN_INTERRUPTED,
    RUN_RUNNING,
    SEEN_VALIDATED,
    STATUS_NEW,
    STATUS_REJECTED,
    SUB_SCORE_KEYS,
    TIER_ESN,
    VALID_STATUSES,
    coerce_sub_score,
)

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


def _decode_sub_scores(value: Any) -> dict[str, int]:
    """Décode la colonne ``sub_scores`` (JSON) en dict complet ``{clé: 1-5}``."""
    if value is None or value == "":
        return {}
    raw = value if isinstance(value, dict) else None
    if raw is None:
        try:
            parsed = json.loads(value)
            raw = parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {key: coerce_sub_score(raw.get(key, DEFAULT_SUB_SCORE)) for key in SUB_SCORE_KEYS}


def _encode_sub_scores(value: Any) -> str | None:
    """Sérialise un dict de sous-scores en JSON (``None`` ⇒ ``None``)."""
    if not value:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        normalized = {
            key: coerce_sub_score(value.get(key, DEFAULT_SUB_SCORE)) for key in SUB_SCORE_KEYS
        }
        return json.dumps(normalized, ensure_ascii=False)
    return None


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
    # Motif d'exclusion métier (« contrat incompatible », « orientation BI »…) :
    # renseigné par la re-validation sur texte complet, NULL sinon.
    rejection_reason = Column(String(300), nullable=True)
    # --- Traçabilité de la collecte hybride (date + pertinence) ---
    # ``id_externe`` : identifiant métier de la plateforme (id LinkedIn, UUID
    # JobTeaser…), base de la déduplication ``(source, id_externe)`` et de la
    # détection « offre déjà connue » qui déclenche l'arrêt anticipé.
    id_externe = Column(String(300), nullable=True, index=True)
    # ``canonical_url`` : URL normalisée (sans query string, sans fragment, sans
    # slash final), utilisée par la déduplication transverse entre passes.
    canonical_url = Column(String(2000), nullable=True, index=True)
    # Date de PUBLICATION annoncée par la plateforme (≠ ``created_at`` = date de
    # collecte) : c'est elle qui pilote la fenêtre temporelle de la passe
    # « Fraîcheur » et l'affichage de la récence dans le dashboard.
    published_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # --- Étape 2 : reranking par juge LLM (NULL tant que non évalué) ---
    rerank_score = Column(Float, nullable=True)
    verdict = Column(String(30), nullable=True)
    match_reasons = Column(Text, nullable=True)   # JSON : liste de chaînes
    red_flags = Column(Text, nullable=True)       # JSON : liste de chaînes
    tech_stack = Column(Text, nullable=True)      # JSON : liste de chaînes
    sub_scores = Column(Text, nullable=True)      # JSON : dict des 4 sous-scores (1-5)
    hard_cap_triggered = Column(String(200), nullable=True)  # verrou bloquant déclenché

    def to_dict(self) -> dict[str, Any]:
        data = {column.name: getattr(self, column.name) for column in self.__table__.columns}
        for name in _JSON_COLUMNS:
            data[name] = _decode_json_list(data.get(name))
        data["sub_scores"] = _decode_sub_scores(data.get("sub_scores"))
        return data


def make_job_id(title: str, company: str, url: str) -> str:
    """Calcule un identifiant unique (hash SHA256) pour une offre."""
    raw = f"{title}|{company}|{url}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


# --------------------------------------------------------------------------- #
# Télémétrie de collecte : mémoire des offres vues, runs, passes
# --------------------------------------------------------------------------- #
class SeenJob(Base):
    """Mémoire de collecte : TOUTE carte croisée, même écartée par le filtre métier.

    C'est la référence de l'arrêt anticipé de la passe « Fraîcheur ». Une offre
    rejetée (BI, contrat incompatible, hors fenêtre) n'est jamais écrite dans
    ``jobs`` ; sans cette table, le scraper la redécouvrirait à chaque run et
    l'arrêt anticipé serait neutralisé par le bruit.

    ``decision`` est **purement informative** : elle ne filtre jamais l'ingestion
    (une offre rejetée hier puis acceptée après ajustement des mots-clés doit
    rester collectable).
    """

    __tablename__ = "seen_jobs"

    source = Column(String(50), primary_key=True)
    external_key = Column(String(300), primary_key=True)
    canonical_url = Column(String(2000), nullable=False, index=True)
    title = Column(String(500), nullable=True)
    first_seen_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    decision = Column(String(30), default=SEEN_VALIDATED, nullable=False)
    rejection_reason = Column(String(300), nullable=True)
    # Identifiant de la fiche ``jobs`` correspondante (NULL si l'offre n'y est pas).
    job_id = Column(String(64), nullable=True)

    def to_dict(self) -> dict[str, Any]:
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class ScrapeRun(Base):
    """Un run de collecte : cadre de rattachement de toutes les passes."""

    __tablename__ = "scrape_runs"

    id = Column(String(36), primary_key=True, default=lambda: uuid4().hex)
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    finished_at = Column(DateTime, nullable=True)
    status = Column(String(20), default=RUN_RUNNING, nullable=False, index=True)
    sources = Column(String(200), nullable=True)
    total_found = Column(Integer, default=0, nullable=False)
    total_validated = Column(Integer, default=0, nullable=False)
    total_rejected = Column(Integer, default=0, nullable=False)
    total_inserted = Column(Integer, default=0, nullable=False)
    total_duplicates = Column(Integer, default=0, nullable=False)
    notes = Column(Text, nullable=True)

    def to_dict(self) -> dict[str, Any]:
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class ScrapeQueryStat(Base):
    """Télémétrie d'UNE passe : (run, source, requête, mode) → raison exacte d'arrêt.

    Une ligne par (source × requête cible × mode). C'est ce qui permet de savoir
    si une requête s'est arrêtée parce que le vivier était épuisé (``stream_end``,
    ``window_end``), parce qu'un quota a tronqué le flux (``quota``,
    ``max_pages``) ou parce que la plateforme a coupé (``rate_limit``…).
    """

    __tablename__ = "scrape_query_stats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(36), nullable=False, index=True)
    source = Column(String(50), nullable=False, index=True)
    query = Column(String(300), nullable=False)
    mode = Column(String(20), nullable=False, index=True)
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    finished_at = Column(DateTime, nullable=True)
    duration_seconds = Column(Float, nullable=True)
    pages_fetched = Column(Integer, default=0, nullable=False)
    http_requests = Column(Integer, default=0, nullable=False)
    cards_seen = Column(Integer, default=0, nullable=False)
    jobs_kept = Column(Integer, default=0, nullable=False)
    jobs_known = Column(Integer, default=0, nullable=False)
    jobs_duplicate = Column(Integer, default=0, nullable=False)
    jobs_rejected = Column(Integer, default=0, nullable=False)
    jobs_out_of_window = Column(Integer, default=0, nullable=False)
    stop_reason = Column(String(30), nullable=False, index=True)
    stop_detail = Column(String(300), nullable=True)
    stop_page = Column(Integer, nullable=True)
    newest_published_at = Column(DateTime, nullable=True)
    oldest_published_at = Column(DateTime, nullable=True)
    error = Column(String(300), nullable=True)

    def to_dict(self) -> dict[str, Any]:
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


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
        """Migration légère : ajoute les colonnes manquantes sur une base existante.

        Les nouvelles tables (``seen_jobs``, ``scrape_runs``, ``scrape_query_stats``)
        sont créées par ``Base.metadata.create_all`` ; seules les colonnes ajoutées
        à ``jobs`` nécessitent un ``ALTER TABLE`` explicite.
        """
        expected = {
            "rerank_score": "FLOAT",
            "verdict": "VARCHAR(30)",
            "match_reasons": "TEXT",
            "red_flags": "TEXT",
            "tech_stack": "TEXT",
            "rejection_reason": "VARCHAR(300)",
            "sub_scores": "TEXT",
            "hard_cap_triggered": "VARCHAR(200)",
            # Collecte hybride : identifiant plateforme, URL canonique, publication.
            "id_externe": "VARCHAR(300)",
            "canonical_url": "VARCHAR(2000)",
            "published_at": "DATETIME",
        }
        with self.engine.begin() as conn:
            existing = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(jobs)")}
            for column, sql_type in expected.items():
                if column not in existing:
                    conn.exec_driver_sql(f"ALTER TABLE jobs ADD COLUMN {column} {sql_type}")
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_jobs_canonical_url ON jobs (canonical_url)"
            )
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_jobs_id_externe ON jobs (id_externe)"
            )
        self._backfill_collection_memory()

    def _backfill_collection_memory(self) -> None:
        """Renseigne ``canonical_url`` sur l'historique puis alimente ``seen_jobs``.

        Sans ce rattrapage, toutes les offres déjà en base seraient considérées
        comme inconnues au premier run hybride : l'arrêt anticipé ne pourrait pas
        s'enclencher et le scraper repaginerait tout le vivier une fois de plus.

        ``id_externe`` n'est **pas** inventé pour l'historique (l'identifiant
        plateforme n'est pas déductible de façon fiable) : ces lignes restent
        reconnaissables par leur ``canonical_url``, que l'index interroge aussi.
        """
        from src.storage.cleanup import canonical_url  # import local : évite un cycle

        with self.engine.begin() as conn:
            missing = conn.exec_driver_sql(
                "SELECT id, url FROM jobs WHERE canonical_url IS NULL OR canonical_url = ''"
            ).fetchall()
            if missing:
                conn.exec_driver_sql(
                    "UPDATE jobs SET canonical_url = ? WHERE id = ?",
                    [(canonical_url(str(url or "")), job_id) for job_id, url in missing],
                )
            # Mémoire de collecte : une entrée par offre déjà connue (idempotent :
            # les lignes existantes ne sont jamais écrasées).
            conn.exec_driver_sql(
                """
                INSERT OR IGNORE INTO seen_jobs (
                    source, external_key, canonical_url, title,
                    first_seen_at, last_seen_at, decision, job_id
                )
                SELECT
                    COALESCE(NULLIF(source, ''), 'inconnu'),
                    COALESCE(NULLIF(id_externe, ''), canonical_url),
                    canonical_url,
                    title,
                    created_at,
                    created_at,
                    'VALIDATED',
                    id
                FROM jobs
                WHERE canonical_url IS NOT NULL AND canonical_url <> ''
                """
            )

    def upsert_job(self, job: dict[str, Any]) -> Job:
        """Insère une offre sans doublon (clé = id) ou met à jour ses scores."""
        job_id = job.get("id") or make_job_id(job["title"], job["company"], job["url"])
        fields: dict[str, Any] = {}
        for key, value in job.items():
            if key == "id" or not hasattr(Job, key):
                continue
            if key in _JSON_COLUMNS:
                fields[key] = _encode_json_list(value)
            elif key == "sub_scores":
                fields[key] = _encode_sub_scores(value)
            else:
                fields[key] = value
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

    def reject_job(self, job_id: str, reason: str) -> bool:
        """Écarte une offre (statut REJETÉ) et conserve le motif d'exclusion."""
        with self.SessionLocal() as session:
            record = session.get(Job, job_id)
            if record is None:
                return False
            record.status = STATUS_REJECTED
            record.rejection_reason = (reason or "")[:300] or None
            session.commit()
            return True

    def delete_jobs(self, job_ids: Iterable[str]) -> int:
        """Supprime définitivement des offres (doublons fusionnés). Nombre supprimé."""
        ids = [job_id for job_id in job_ids if job_id]
        if not ids:
            return 0
        with self.SessionLocal() as session:
            records = session.execute(select(Job).where(Job.id.in_(ids))).scalars().all()
            for record in records:
                session.delete(record)
            session.commit()
            return len(records)

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
        sub_scores: Mapping[str, int] | None = None,
        hard_cap_triggered: str | None = None,
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
            record.sub_scores = _encode_sub_scores(sub_scores)
            record.hard_cap_triggered = hard_cap_triggered or None
            session.commit()
            return True

    def get_unranked_jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        """Top des offres NON encore évaluées par le juge LLM (tri par score initial).

        Les offres écartées par la re-validation métier sont exclues : inutile de
        dépenser des tokens du juge sur une offre déjà disqualifiée.
        """
        with self.SessionLocal() as session:
            stmt = (
                select(Job)
                .where(Job.rerank_score.is_(None), Job.status != STATUS_REJECTED)
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
                record.sub_scores = None
                record.hard_cap_triggered = None
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

    # ------------------------------------------------------------------ #
    # Mémoire de collecte (seen_jobs) : référence de l'arrêt anticipé
    # ------------------------------------------------------------------ #
    def load_seen_index(
        self, sources: Iterable[str] | None = None
    ) -> tuple[set[tuple[str, str]], set[str]]:
        """Charge la mémoire de collecte : ``({(source, clé)}, {urls canoniques})``.

        Les deux ensembles sont renvoyés ensemble car l'index de collecte doit
        répondre indifféremment par identifiant plateforme
        (``(source, id_externe)``) ou par URL canonique (offres historiques,
        antérieures à l'ajout de la colonne ``id_externe``).
        """
        with self.SessionLocal() as session:
            stmt = select(SeenJob.source, SeenJob.external_key, SeenJob.canonical_url)
            source_list = [str(item) for item in sources] if sources else []
            if source_list:
                stmt = stmt.where(SeenJob.source.in_(source_list))
            pairs: set[tuple[str, str]] = set()
            urls: set[str] = set()
            for source, external_key, url in session.execute(stmt).all():
                if source and external_key:
                    pairs.add((str(source), str(external_key)))
                if url:
                    urls.add(str(url))
            return pairs, urls

    def upsert_seen_jobs(self, entries: Iterable[dict[str, Any]]) -> int:
        """Insère / met à jour des lignes de mémoire de collecte. Nombre de lignes.

        Upsert en lot sur la clé ``(source, external_key)`` : ``first_seen_at``
        est préservé, tandis que ``last_seen_at``, ``decision`` et le motif de
        rejet reflètent la dernière observation (vérité courante).
        """
        rows = [entry for entry in entries if entry.get("source") and entry.get("external_key")]
        if not rows:
            return 0
        now = datetime.utcnow()
        payload: list[dict[str, Any]] = []
        for entry in rows:
            payload.append(
                {
                    "source": str(entry["source"])[:50],
                    "external_key": str(entry["external_key"])[:300],
                    "canonical_url": str(entry.get("canonical_url") or "")[:2000],
                    "title": (str(entry["title"])[:500] if entry.get("title") else None),
                    "first_seen_at": entry.get("first_seen_at") or now,
                    "last_seen_at": entry.get("last_seen_at") or now,
                    "decision": str(entry.get("decision") or SEEN_VALIDATED)[:30],
                    "rejection_reason": (
                        str(entry["rejection_reason"])[:300]
                        if entry.get("rejection_reason")
                        else None
                    ),
                    "job_id": entry.get("job_id"),
                }
            )
        with self.SessionLocal() as session:
            statement = sqlite_insert(SeenJob).values(payload)
            statement = statement.on_conflict_do_update(
                index_elements=[SeenJob.source, SeenJob.external_key],
                set_={
                    "canonical_url": statement.excluded.canonical_url,
                    "title": statement.excluded.title,
                    "last_seen_at": statement.excluded.last_seen_at,
                    "decision": statement.excluded.decision,
                    "rejection_reason": statement.excluded.rejection_reason,
                    # Ne jamais perdre le rattachement à une fiche ``jobs`` : une
                    # ré-observation sans identifiant de fiche (décision ``KNOWN``,
                    # par exemple) ne doit pas écraser un lien existant.
                    "job_id": func.coalesce(statement.excluded.job_id, SeenJob.job_id),
                },
            )
            session.execute(statement)
            session.commit()
        return len(payload)

    def count_seen_jobs(self, decision: str | None = None) -> int:
        """Nombre d'offres en mémoire de collecte (optionnellement par décision)."""
        with self.SessionLocal() as session:
            stmt = select(func.count()).select_from(SeenJob)
            if decision:
                stmt = stmt.where(SeenJob.decision == decision)
            return int(session.execute(stmt).scalar_one())

    # ------------------------------------------------------------------ #
    # Télémétrie : runs de collecte
    # ------------------------------------------------------------------ #
    def start_run(self, sources: Iterable[str] | None = None) -> str:
        """Ouvre un run de collecte et retourne son identifiant.

        Les runs laissés ouverts par un précédent processus (arrêt brutal, coupure
        de session) sont marqués ``INTERRUPTED`` : un run inachevé signifie que la
        collecte a pu être tronquée, information à ne pas perdre.
        """
        run_id = uuid4().hex
        with self.SessionLocal() as session:
            stale = (
                session.execute(select(ScrapeRun).where(ScrapeRun.status == RUN_RUNNING))
                .scalars()
                .all()
            )
            for record in stale:
                record.status = RUN_INTERRUPTED
                record.finished_at = record.finished_at or datetime.utcnow()
                record.notes = record.notes or "run interrompu (processus arrêté avant la clôture)"
            session.add(
                ScrapeRun(
                    id=run_id,
                    status=RUN_RUNNING,
                    sources=",".join(str(item) for item in (sources or ())) or None,
                )
            )
            session.commit()
        return run_id

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        total_found: int = 0,
        total_validated: int = 0,
        total_rejected: int = 0,
        total_inserted: int = 0,
        total_duplicates: int = 0,
        notes: str | None = None,
    ) -> bool:
        """Clôt un run : statut final, compteurs agrégés et note libre. False si inconnu."""
        with self.SessionLocal() as session:
            record = session.get(ScrapeRun, run_id)
            if record is None:
                return False
            record.finished_at = datetime.utcnow()
            record.status = status
            record.total_found = int(total_found)
            record.total_validated = int(total_validated)
            record.total_rejected = int(total_rejected)
            record.total_inserted = int(total_inserted)
            record.total_duplicates = int(total_duplicates)
            record.notes = notes
            session.commit()
            return True

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """Un run de collecte par identifiant (``None`` s'il est inconnu)."""
        with self.SessionLocal() as session:
            record = session.get(ScrapeRun, run_id)
            return record.to_dict() if record is not None else None

    def get_last_run(self) -> dict[str, Any] | None:
        """Dernier run de collecte enregistré (``None`` si la télémétrie est vide)."""
        with self.SessionLocal() as session:
            stmt = select(ScrapeRun).order_by(ScrapeRun.started_at.desc()).limit(1)
            record = session.execute(stmt).scalars().first()
            return record.to_dict() if record is not None else None

    def count_runs(self) -> int:
        """Nombre de runs de collecte enregistrés."""
        with self.SessionLocal() as session:
            return int(session.execute(select(func.count()).select_from(ScrapeRun)).scalar_one())

    # ------------------------------------------------------------------ #
    # Télémétrie : passes (source × requête × mode)
    # ------------------------------------------------------------------ #
    _QUERY_STAT_INT_FIELDS = (
        "pages_fetched",
        "http_requests",
        "cards_seen",
        "jobs_kept",
        "jobs_known",
        "jobs_duplicate",
        "jobs_rejected",
        "jobs_out_of_window",
    )

    def record_query_stats(self, run_id: str, stats: Iterable[Mapping[str, Any]]) -> int:
        """Enregistre une ligne de télémétrie par passe. Nombre de lignes écrites.

        Les clés inconnues sont ignorées : l'appelant peut transmettre directement
        le dictionnaire d'un ``PassReport`` sans avoir à connaître le schéma.
        """
        columns = {column.name for column in ScrapeQueryStat.__table__.columns}
        rows: list[ScrapeQueryStat] = []
        for stat in stats:
            payload = {
                key: value for key, value in dict(stat).items() if key in columns and key != "id"
            }
            payload["run_id"] = run_id
            payload["stop_reason"] = str(payload.get("stop_reason") or "error")[:30]
            if not payload.get("started_at"):
                payload["started_at"] = datetime.utcnow()
            for field in self._QUERY_STAT_INT_FIELDS:
                payload[field] = int(payload.get(field) or 0)
            if payload.get("duration_seconds") is not None:
                payload["duration_seconds"] = float(payload["duration_seconds"])
            if payload.get("stop_detail"):
                payload["stop_detail"] = str(payload["stop_detail"])[:300]
            if payload.get("error"):
                payload["error"] = str(payload["error"])[:300]
            rows.append(ScrapeQueryStat(**payload))
        if not rows:
            return 0
        with self.SessionLocal() as session:
            session.add_all(rows)
            session.commit()
        return len(rows)

    def get_run_query_stats(self, run_id: str) -> list[dict[str, Any]]:
        """Passes d'un run, dans leur ordre de collecte (source, requête, mode)."""
        with self.SessionLocal() as session:
            stmt = (
                select(ScrapeQueryStat)
                .where(ScrapeQueryStat.run_id == run_id)
                .order_by(ScrapeQueryStat.id.asc())
            )
            return [row.to_dict() for row in session.execute(stmt).scalars().all()]

    def get_recent_query_stats(
        self, limit: int = 50, run_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Dernières passes enregistrées (toutes sources confondues)."""
        with self.SessionLocal() as session:
            stmt = select(ScrapeQueryStat)
            if run_id:
                stmt = stmt.where(ScrapeQueryStat.run_id == run_id)
            stmt = (
                stmt.order_by(ScrapeQueryStat.started_at.desc(), ScrapeQueryStat.id.desc())
                .limit(max(0, int(limit)))
            )
            return [row.to_dict() for row in session.execute(stmt).scalars().all()]

    # ------------------------------------------------------------------ #
    # Rétention : la télémétrie ne doit pas croître indéfiniment
    # ------------------------------------------------------------------ #
    def prune_telemetry(self, days: int = 180) -> dict[str, int]:
        """Purge la télémétrie antérieure à ``days`` jours (runs + passes)."""
        threshold = datetime.utcnow() - timedelta(days=max(1, int(days)))
        with self.SessionLocal() as session:
            removed_stats = session.execute(
                delete(ScrapeQueryStat).where(ScrapeQueryStat.started_at < threshold)
            ).rowcount
            removed_runs = session.execute(
                delete(ScrapeRun).where(ScrapeRun.started_at < threshold)
            ).rowcount
            session.commit()
        return {"runs": int(removed_runs or 0), "query_stats": int(removed_stats or 0)}

    def prune_seen_jobs(self, days: int = 180) -> int:
        """Oublie les offres vues mais **jamais retenues** depuis plus de ``days`` jours.

        Les clés rattachées à une fiche ``jobs`` sont toujours conservées : elles
        restent la référence de déduplication, quel que soit leur âge. Le bruit
        (annonces BI, contrats incompatibles) est, lui, élagué — il est de toute
        façon plus ancien que la fenêtre temporelle de la passe « Fraîcheur ».
        """
        threshold = datetime.utcnow() - timedelta(days=max(1, int(days)))
        with self.SessionLocal() as session:
            known_ids = set(session.execute(select(Job.id)).scalars())
            stale = (
                session.execute(select(SeenJob).where(SeenJob.last_seen_at < threshold))
                .scalars()
                .all()
            )
            removed = 0
            for record in stale:
                if record.job_id and record.job_id in known_ids:
                    continue
                session.delete(record)
                removed += 1
            session.commit()
            return removed



