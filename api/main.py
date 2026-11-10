"""Serveur FastAPI exposant la base SQLite, le scoring v3/v4 et le Pack Claude.

Lancez avec :
    uvicorn api.main:app --reload --port 8000
ou via :
    python run_web.py
"""
from __future__ import annotations

import copy
import dataclasses
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from components.job_feed.serialize import serialize_job
from src.config import PROJECT_ROOT, load_config
from sqlalchemy import text

from src.constants import (
    CLOSED_OFFER_REASON,
    IGNORE_COMMENT_MAX_LENGTH,
    IGNORE_REASON_LABELS,
    IGNORE_REASONS,
    SOURCE_ORDER,
    STATUS_IGNORED,
    STATUS_NEW,
    USER_GRADE_COMMENT_MAX_LENGTH,
    USER_GRADE_SCORE_MAX,
    USER_GRADE_SCORE_MIN,
    V4_CLASSES,
    V4_CLASS_LABELS,
    V4_CLASS_SHORT_LABELS,
    V4_TAGS,
    V4_TAG_LABELS,
    VALID_STATUSES,
    source_label,
)
from src.matching import apply_queue
from src.matching.claude_pack import build_claude_pack
from src.matching.cover_letter import CoverLetterGenerator, check_letter
from src.matching.pdf_exporter import generate_cover_letter_pdf
from src.matching.scoring_v4 import LLMJudgeV4
from src.storage.database import Database
from utils.data import (
    Filters,
    effective_score,
    exclusion_info,
    filter_jobs,
    is_contract_to_verify,
    job_age_days,
    main_score,
    parse_timestamp,
    relative_date,
    source_color,
)

# Tris acceptés par /api/jobs : « score » (défaut, note v4), alias « v3 »/« v4 »,
# « v4_class », « v3_legacy » (ancien ordre v3) et « freshness » (date de publication).
DEFAULT_SORT = "score"

# Suffixes juridiques / génériques retirés pour regrouper les offres d'une même entreprise.
_COMPANY_NOISE = frozenset({
    "sa", "sas", "sasu", "sarl", "se", "inc", "ltd", "llc", "gmbh", "ag", "bv", "nv",
    "plc", "corp", "corporation", "company", "co", "group", "groupe", "france", "the",
})


def company_key(name: Any) -> str:
    """Clé de regroupement d'une entreprise : minuscules, sans accents ni suffixe juridique.

    « HGH Infrared Systems SAS » et « HGH INFRARED SYSTEMS » donnent la même clé ;
    les précisions entre crochets ou parenthèses sont ignorées.
    Chaîne vide si l'entreprise est inconnue.
    """
    raw = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode()
    raw = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", raw)  # « Naval Group [FR] », « Thales (TRT) »
    words = [w for w in re.split(r"[^a-z0-9]+", raw.lower()) if w]
    kept = [w for w in words if w not in _COMPANY_NOISE]
    return " ".join(kept or words)


def _iso(value: Any) -> str:
    """Horodatage ISO 8601 (``""`` si absent ou illisible)."""
    stamp = parse_timestamp(value)
    return stamp.isoformat(timespec="seconds") if stamp is not None else ""


def _publication(job: dict[str, Any]) -> tuple[Any, bool]:
    """(date de référence, date_estimee) : la publication, sinon la collecte (estimée)."""
    if parse_timestamp(job.get("published_at")) is not None:
        return job.get("published_at"), False
    return job.get("created_at"), True


class StatusUpdatePayload(BaseModel):
    status: str
    rejection_reason: str | None = None


class BulkStatusPayload(BaseModel):
    ids: list[str] = Field(min_length=1)
    status: str
    rejection_reason: str | None = None
    # Raison d'ignorer en un clic (N3) : seulement avec status IGNORÉ.
    reason: str | None = None
    comment: str | None = Field(default=None, max_length=IGNORE_COMMENT_MAX_LENGTH)


class IgnorePayload(BaseModel):
    reason: str
    comment: str | None = Field(default=None, max_length=IGNORE_COMMENT_MAX_LENGTH)


class UserGradePayload(BaseModel):
    cls: str | None = None
    score: float | str | None = None
    comment: str | None = Field(default=None, max_length=USER_GRADE_COMMENT_MAX_LENGTH)


class QuickArchiveOldPayload(BaseModel):
    """Contrôle « offre fermée » des offres NOUVEAU d'au moins ``days`` jours (S4).

    L'archivage par âge seul est abandonné : seules les offres dont la page prouve
    la fermeture passent en IGNORÉ (raison ``offre_fermee``). ``action`` est gardé
    pour compatibilité mais ignoré.
    """

    days: int = Field(default=14, ge=1, le=365)
    action: str = Field(default="archive", pattern="^(archive|trash)$")
    max_checks: int = Field(default=20, ge=1, le=100)


class QueueAddPayload(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=50)
    auto_submit: bool = False


class QueueReportPayload(BaseModel):
    outcome: Literal["applied", "failed"]
    report: str


class NotesPayload(BaseModel):
    user_notes: str


class CoverLetterPayload(BaseModel):
    regenerate: bool = False
    custom_instruction: str = ""
    edited_text: str | None = None


class ClaudePackPayload(BaseModel):
    ids: list[str] = Field(min_length=1)
    auto_submit: bool = False


class GradeBatchPayload(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=50)



def create_app(db_instance: Database | None = None, *, liveness_checker: Any = None) -> FastAPI:
    """Application FastAPI. ``liveness_checker`` (tests) remplace la sonde réseau « offre fermée »."""
    app = FastAPI(
        title="Stage Copilot API (v3 + v4)",
        version="4.0.0",
        description="Backend FastAPI ultra-rapide pour Stage Copilot (Flux v3/v4, Kanban, Pack Claude).",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    state: dict[str, Any] = {"db": db_instance, "liveness_checker": liveness_checker}

    def get_db() -> Database:
        if state["db"] is None:
            cfg = load_config()
            db_path = Path(cfg.get("database", {}).get("path", "data/stage_copilot.db"))
            if not db_path.is_absolute():
                db_path = PROJECT_ROOT / db_path
            state["db"] = Database(str(db_path))
        return state["db"]

    # Cache des offres : relire 2 000+ lignes coûte ~350 ms, on ne le fait que si la base a changé.
    cache: dict[str, Any] = {"key": None, "jobs": [], "queue": {}}

    def _db_stamp(db: Database) -> tuple[Any, ...]:
        """Empreinte du fichier SQLite et de son WAL : change dès qu'un process (pipeline, Streamlit) écrit."""
        path = Path(str(getattr(db, "db_path", "")))
        stamp: list[Any] = [id(db)]
        for file in (path, path.with_name(path.name + "-wal")):
            try:
                info = file.stat()
                stamp += [info.st_mtime_ns, info.st_size]
            except OSError:
                stamp += [0, 0]
        return tuple(stamp)

    def _queue_map(db: Database) -> dict[str, dict[str, Any]]:
        """Résumé de la file de candidature par offre (lecture seule ; vide si pas de file)."""
        engine = getattr(db, "engine", None)
        if engine is None:
            return {}
        try:
            with engine.connect() as conn:
                exists = conn.execute(
                    text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='apply_queue'")
                ).fetchone()
            if not exists:
                return {}
            return {str(item["job_id"]): item for item in apply_queue.get_queue(db)["items"]}
        except Exception:  # la file ne doit jamais casser la liste des offres
            return {}

    def _refresh(db: Database) -> None:
        key = _db_stamp(db)
        if cache["key"] != key:
            cache["jobs"] = db.get_jobs(limit=10000)
            cache["queue"] = _queue_map(db)
            cache["key"] = key

    def all_jobs(db: Database) -> list[dict[str, Any]]:
        _refresh(db)
        # Copies superficielles : filtres et routes ne modifient jamais le cache partagé.
        return [dict(job) for job in cache["jobs"]]

    def queue_items(db: Database) -> dict[str, dict[str, Any]]:
        _refresh(db)
        return cache["queue"]

    def invalidate() -> None:
        cache["key"] = None

    def get_job_or_404(db: Database, job_id: str) -> dict[str, Any]:
        job = db.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Offre introuvable")
        return job

    def _application_report(item: dict[str, Any] | None) -> dict[str, Any] | None:
        """Compte rendu de candidature (file Claude in Chrome) attaché à l'offre."""
        if not item:
            return None
        return {
            "state": item.get("state"),
            "report": item.get("report") or "",
            "method": item.get("method") or "inconnu",
            "method_label": item.get("method_label") or "",
            "needs_account": bool(item.get("needs_account")),
            "to_finish": bool(item.get("to_finish")),
            "updated_at": item.get("updated_at") or "",
        }

    def _serialize_full_job(
        job: dict[str, Any],
        keywords: tuple[str, ...],
        *,
        queue: dict[str, dict[str, Any]] | None = None,
        company_counts: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        base = serialize_job(job, keywords)
        reference, estimated = _publication(job)
        key = company_key(job.get("company"))
        base["cover_letter"] = str(job.get("cover_letter") or "")
        base["cover_letter_version"] = job.get("cover_letter_version")
        base["created_at"] = _iso(job.get("created_at"))
        # U3 : vraie date de publication ; repli sur la date de collecte, signalé.
        base["published_at"] = _iso(reference)
        base["date_estimee"] = estimated
        base["age_days"] = job_age_days(job)
        base["date_label"] = relative_date(reference)
        base["applied_at"] = _iso(job.get("applied_at"))
        base["v4_model"] = str(job.get("v4_model") or "")
        base["v4_graded_at"] = _iso(job.get("v4_graded_at"))
        base["source"] = str(job.get("source") or "")
        # U2 : regroupement par entreprise (compté sur tout le résultat filtré).
        base["company_key"] = key
        base["company_count"] = (company_counts or {}).get(key, 1) if key else 1
        base["application_report"] = _application_report((queue or {}).get(str(job.get("id"))))
        return base

    def _ignore_reasons() -> list[dict[str, str]]:
        return [{"key": code, "label": IGNORE_REASON_LABELS.get(code, code)} for code in IGNORE_REASONS]

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    # Les deux routes de lecture renvoient JSONResponse : sans cela FastAPI valide puis
    # ré-encode ~1 Mo de réponse via le type de retour, soit ~1 s par appel.
    @app.get("/api/stats", response_class=JSONResponse)
    def get_stats(db: Database = Depends(get_db)) -> JSONResponse:
        jobs = all_jobs(db)
        by_status: dict[str, int] = {}
        by_v4_class: dict[str, int] = {c: 0 for c in V4_CLASSES}
        by_source: dict[str, int] = {}
        old_new_14d = 0
        v4_graded = 0
        v3_graded = 0
        excluded = 0
        contract_to_verify = 0
        unscored_v4 = 0

        for j in jobs:
            st_code = str(j.get("status") or STATUS_NEW)
            by_status[st_code] = by_status.get(st_code, 0) + 1
            cls = str(j.get("v4_class") or "").upper()
            if cls in by_v4_class:
                by_v4_class[cls] += 1
            src = str(j.get("source") or "")
            if src:
                by_source[src] = by_source.get(src, 0) + 1
            if j.get("v4_display_score") is not None:
                v4_graded += 1
            if j.get("rerank_score") is not None:
                v3_graded += 1
            is_exclu = exclusion_info(j)[0]
            if is_exclu:
                excluded += 1
            elif st_code == STATUS_NEW:
                if is_contract_to_verify(j):
                    contract_to_verify += 1
                if j.get("v4_display_score") is None:
                    unscored_v4 += 1
                if (job_age_days(j) or 0) >= 14:
                    old_new_14d += 1

        return JSONResponse({
            "total": len(jobs),
            "by_status": by_status,
            "by_v4_class": by_v4_class,
            "by_source": [
                {"key": k, "label": source_label(k), "color": source_color(k), "count": v}
                for k, v in sorted(by_source.items(), key=lambda x: -x[1])
            ],
            # Catalogue complet des sources connues (même sans offre), dans l'ordre d'affichage.
            "sources": [
                {"key": k, "label": source_label(k), "color": source_color(k), "count": by_source.get(k, 0)}
                for k in SOURCE_ORDER
            ],
            "old_new_14d": old_new_14d,
            "v3_graded": v3_graded,
            "v4_graded": v4_graded,
            "unscored_v4": unscored_v4,
            "excluded": excluded,
            "contract_to_verify": contract_to_verify,
            "v4_classes": [
                {"key": c, "label": V4_CLASS_LABELS[c], "short_label": V4_CLASS_SHORT_LABELS.get(c, c)}
                for c in V4_CLASSES
            ],
            "v4_tags": [{"key": t, "label": V4_TAG_LABELS.get(t, t)} for t in V4_TAGS],
            "ignore_reasons": _ignore_reasons(),
            "user_grade": {
                "score_min": USER_GRADE_SCORE_MIN,
                "score_max": USER_GRADE_SCORE_MAX,
                "comment_max_length": USER_GRADE_COMMENT_MAX_LENGTH,
            },
        })

    @app.get("/api/ignore-reasons")
    def get_ignore_reasons() -> dict[str, Any]:
        return {"reasons": _ignore_reasons(), "comment_max_length": IGNORE_COMMENT_MAX_LENGTH}

    @app.get("/api/jobs", response_class=JSONResponse)
    def list_jobs(
        q: str = "",
        statuses: str = "",
        sources: str = "",
        v4_classes: str = "",
        v4_tags: str = "",
        exclude_v4_tags: str = "",
        company_key_filter: str = Query(default="", alias="company_key"),
        min_score: float = 0.0,
        max_score: float | None = None,
        hide_processed: bool = False,
        hide_excluded: bool = True,
        only_excluded: bool = False,
        hide_defense: bool = False,
        only_older_than_days: int | None = None,
        sort_by: str = DEFAULT_SORT,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=250, ge=1, le=2000),
        db: Database = Depends(get_db),
    ) -> JSONResponse:
        cfg = load_config()
        keywords = tuple(cfg.get("scoring", {}).get("excellence_keywords", ()))
        jobs = all_jobs(db)

        st_tuple = tuple(s.strip() for s in statuses.split(",") if s.strip())
        src_tuple = tuple(s.strip() for s in sources.split(",") if s.strip())
        cls_tuple = tuple(c.strip().upper() for c in v4_classes.split(",") if c.strip())
        tag_tuple = tuple(t.strip().lower() for t in v4_tags.split(",") if t.strip())
        ex_tag_tuple = tuple(t.strip().lower() for t in exclude_v4_tags.split(",") if t.strip())

        flt = Filters(
            query=q,
            sources=src_tuple,
            statuses=st_tuple,
            min_score=min_score,
            # Onglet Exclues : les offres exclues portent le statut EXCLU, « à trier » les masquerait.
            hide_processed=hide_processed and not only_excluded,
            hide_excluded=hide_excluded,
            only_excluded=only_excluded,
            hide_defense_ethics=hide_defense,
            v4_classes=cls_tuple,
            v4_tags=tag_tuple,
            exclude_v4_tags=ex_tag_tuple,
            sort_by=sort_by,
            limit=None,
        )
        filtered = filter_jobs(jobs, flt)
        if max_score is not None:
            # Borne haute exclusive : l'onglet « 60 à 79 » ne recompte pas le cœur de cible.
            filtered = [j for j in filtered if main_score(j) < max_score]

        if only_older_than_days is not None:
            filtered = [
                j for j in filtered if (job_age_days(j) or 0) >= only_older_than_days
            ]
        wanted_company = company_key(company_key_filter) if company_key_filter.strip() else ""
        if wanted_company:
            filtered = [j for j in filtered if company_key(j.get("company")) == wanted_company]

        if sort_by == "v3_legacy":
            # Ancien ordre v3 (comparaison explicite) : la base renvoie déjà l'ordre v4.
            filtered.sort(key=effective_score, reverse=True)
        elif sort_by == "freshness":
            filtered.sort(
                key=lambda j: parse_timestamp(_publication(j)[0]) or datetime.min,
                reverse=True,
            )

        # Compteurs (N2) : offres « contrat à vérifier » du résultat, et exclues des mêmes filtres.
        contract_count = sum(1 for j in filtered if is_contract_to_verify(j))
        if only_excluded:
            excluded_count = len(filtered)
        else:
            excluded_count = len(filter_jobs(jobs, dataclasses.replace(flt, only_excluded=True, hide_processed=False)))

        company_counts: dict[str, int] = {}
        for j in filtered:
            key = company_key(j.get("company"))
            if key:
                company_counts[key] = company_counts.get(key, 0) + 1

        total_matching = len(filtered)
        sliced = filtered[offset: offset + limit]
        queue = queue_items(db)
        return JSONResponse({
            "total": total_matching,
            "offset": offset,
            "limit": limit,
            "has_more": offset + len(sliced) < total_matching,
            "excluded_count": excluded_count,
            "contract_to_verify_count": contract_count,
            "jobs": [
                _serialize_full_job(j, keywords, queue=queue, company_counts=company_counts)
                for j in sliced
            ],
        })

    @app.get("/api/jobs/{job_id}", response_class=JSONResponse)
    def get_job_detail(job_id: str, db: Database = Depends(get_db)) -> JSONResponse:
        cfg = load_config()
        keywords = tuple(cfg.get("scoring", {}).get("excellence_keywords", ()))
        job = get_job_or_404(db, job_id)
        key = company_key(job.get("company"))
        count = sum(1 for j in all_jobs(db) if key and company_key(j.get("company")) == key)
        return JSONResponse(
            _serialize_full_job(job, keywords, queue=queue_items(db), company_counts={key: count or 1})
        )

    @app.post("/api/jobs/{job_id}/status")
    def update_job_status(
        job_id: str,
        payload: StatusUpdatePayload,
        db: Database = Depends(get_db),
    ) -> dict[str, Any]:
        if payload.status not in VALID_STATUSES:
            raise HTTPException(status_code=400, detail=f"Statut invalide : {payload.status}")
        ok = db.update_status(job_id, payload.status, rejection_reason=payload.rejection_reason)
        invalidate()
        if not ok:
            raise HTTPException(status_code=404, detail="Offre introuvable")
        return {"ok": True, "id": job_id, "status": payload.status}

    @app.post("/api/jobs/bulk-status")
    def bulk_update_job_status(
        payload: BulkStatusPayload,
        db: Database = Depends(get_db),
    ) -> dict[str, Any]:
        if payload.status not in VALID_STATUSES:
            raise HTTPException(status_code=400, detail=f"Statut invalide : {payload.status}")
        if payload.reason:
            # Ignorer avec une raison en un clic (N3) : passe par record_ignore.
            if payload.status != STATUS_IGNORED:
                raise HTTPException(
                    status_code=400, detail="Une raison d'ignorer n'est possible qu'avec le statut IGNORÉ."
                )
            if payload.reason not in IGNORE_REASONS:
                raise HTTPException(status_code=400, detail=f"Raison d'ignorer invalide : {payload.reason}")
            count = sum(
                1 for job_id in dict.fromkeys(payload.ids)
                if db.record_ignore(job_id, payload.reason, payload.comment)
            )
        else:
            count = db.bulk_update_status(
                payload.ids, payload.status, rejection_reason=payload.rejection_reason
            )
        invalidate()
        return {"ok": True, "updated": count, "status": payload.status}

    @app.post("/api/jobs/{job_id}/ignore")
    def ignore_job(
        job_id: str,
        payload: IgnorePayload,
        db: Database = Depends(get_db),
    ) -> dict[str, Any]:
        try:
            ok = db.record_ignore(job_id, payload.reason, payload.comment)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        invalidate()
        if not ok:
            raise HTTPException(status_code=404, detail="Offre introuvable")
        return {
            "ok": True,
            "id": job_id,
            "status": STATUS_IGNORED,
            "ignore_reason": payload.reason,
            "ignore_reason_label": IGNORE_REASON_LABELS.get(payload.reason, payload.reason),
            "ignore_comment": str(payload.comment or "").strip(),
        }

    @app.post("/api/jobs/{job_id}/user-grade")
    def set_user_grade(
        job_id: str,
        payload: UserGradePayload,
        db: Database = Depends(get_db),
    ) -> dict[str, Any]:
        try:
            ok = db.set_user_grade(job_id, payload.cls, payload.score, payload.comment)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        invalidate()
        if not ok:
            raise HTTPException(status_code=404, detail="Offre introuvable")
        job = db.get_job(job_id) or {}
        user_at = job.get("user_v4_at")
        return {
            "ok": True,
            "id": job_id,
            "user_v4_class": str(job.get("user_v4_class") or "").strip().upper(),
            "user_v4_score": int(job["user_v4_score"]) if job.get("user_v4_score") is not None else None,
            "user_v4_comment": str(job.get("user_v4_comment") or "").strip(),
            "user_v4_at": _iso(user_at),
        }

    @app.get("/api/user-grades")
    def list_user_grades(db: Database = Depends(get_db)) -> dict[str, Any]:
        grades = db.get_user_grades()
        for grade in grades:
            for field in ("user_v4_at", "v4_graded_at"):
                grade[field] = _iso(grade.get(field))
        return {"grades": grades}

    @app.post("/api/jobs/quick-archive-old")
    def quick_archive_old_offers(
        payload: QuickArchiveOldPayload,
        db: Database = Depends(get_db),
    ) -> dict[str, Any]:
        """Archive les offres NOUVEAU d'au moins ``days`` jours dont la page est fermée (S4).

        Remplace l'ancien archivage par âge : une offre encore ouverte, ou dont la page
        n'a pas pu être lue (429, anti-robot, connexion requise), reste NOUVEAU.
        """
        from src.notifications import archive_expired_offers

        cfg = copy.deepcopy(load_config())
        automations = cfg.setdefault("automations", {})
        automations["check_expired_offers_enabled"] = True
        automations["expired_offers_age_days"] = payload.days
        before = {str(j["id"]) for j in db.get_jobs(statuses=STATUS_NEW)}
        archived = archive_expired_offers(
            db, cfg, max_checks=payload.max_checks, checker=state.get("liveness_checker")
        )
        closed_ids = sorted(
            job_id for job_id in before
            if (job := db.get_job(job_id))
            and job.get("status") == STATUS_IGNORED
            and job.get("rejection_reason") == CLOSED_OFFER_REASON
        )
        invalidate()
        return {
            "ok": True,
            "updated": archived,
            "status": STATUS_IGNORED,
            "reason": CLOSED_OFFER_REASON,
            "ids": closed_ids,
        }

    @app.post("/api/jobs/{job_id}/notes")
    def update_job_notes(
        job_id: str,
        payload: NotesPayload,
        db: Database = Depends(get_db),
    ) -> dict[str, Any]:
        ok = db.update_user_notes(job_id, payload.user_notes)
        invalidate()
        if not ok:
            raise HTTPException(status_code=404, detail="Offre introuvable")
        return {"ok": True, "id": job_id, "user_notes": payload.user_notes}

    @app.post("/api/jobs/{job_id}/cover-letter")
    def generate_or_save_cover_letter(
        job_id: str,
        payload: CoverLetterPayload,
        db: Database = Depends(get_db),
    ) -> dict[str, Any]:
        job = get_job_or_404(db, job_id)

        warnings: list[str] = []
        if payload.edited_text is not None and not payload.regenerate:
            letter = payload.edited_text
            source = "edited"
            warnings = check_letter(letter, job=job)
        else:
            existing = str(job.get("cover_letter") or "").strip()
            if existing and not payload.regenerate:
                letter = existing
                source = "cached"
                warnings = check_letter(letter, job=job)
            else:
                gen = CoverLetterGenerator()
                letter = gen.generate(
                    job,
                    custom_instruction=payload.custom_instruction,
                    allow_fallback=False,
                )
                if not letter or letter.startswith("⚠️"):
                    raise HTTPException(
                        status_code=503,
                        detail=letter
                        or gen.last_error
                        or "Échec de génération de la lettre (DeepSeek et Gemini indisponibles).",
                    )
                source = gen.last_source
                warnings = list(gen.last_warnings)

        # Simple relecture d'une lettre déjà en base : rien à réécrire.
        if source != "cached":
            db.update_cover_letter(job_id, letter, version=2)
            invalidate()
        return {
            "ok": True,
            "id": job_id,
            "cover_letter": letter,
            "cover_letter_version": 2,
            "source": source,
            "warnings": warnings,
        }

    @app.get("/api/jobs/{job_id}/cover-letter.pdf")
    def download_cover_letter_pdf(
        job_id: str,
        db: Database = Depends(get_db),
    ) -> Response:
        job = get_job_or_404(db, job_id)
        letter = str(job.get("cover_letter") or "").strip()
        if not letter:
            gen = CoverLetterGenerator()
            letter = gen.generate(job, allow_fallback=False)
            if not letter or letter.startswith("⚠️"):
                raise HTTPException(
                    status_code=503,
                    detail=letter
                    or gen.last_error
                    or "Échec de génération de la lettre (DeepSeek et Gemini indisponibles).",
                )
            db.update_cover_letter(job_id, letter, version=2)
            invalidate()
        pdf_bytes = generate_cover_letter_pdf(letter, job=job)
        company = "".join(
            c for c in str(job.get("company") or "Entreprise") if c.isalnum() or c in ("-", "_", " ")
        ).strip()
        filename = f"Lettre de motivation Eddy De Castro - {company or 'Stage'}.pdf"
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    def _pack_out_dir(db: Database) -> Path | None:
        """Dossier des PDF du pack : le défaut pour la vraie base, à côté de la base sinon (tests)."""
        real_db_path = (PROJECT_ROOT / "data" / "stage_copilot.db").resolve()
        current = Path(str(getattr(db, "db_path", ""))).resolve() if getattr(db, "db_path", None) else real_db_path
        return None if current == real_db_path else (current.parent / "ready_to_apply")

    @app.post("/api/claude-pack")
    def create_claude_pack(
        payload: ClaudePackPayload,
        db: Database = Depends(get_db),
    ) -> dict[str, Any]:
        id_set = set(payload.ids)
        selected = [j for j in all_jobs(db) if str(j["id"]) in id_set]
        if not selected:
            raise HTTPException(status_code=404, detail="Aucune offre trouvée pour ces identifiants")
        try:
            pack = build_claude_pack(
                selected,
                db=db,
                output_dir=_pack_out_dir(db),
                save_pdfs=True,
                auto_submit=payload.auto_submit,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        finally:
            invalidate()  # le pack enregistre les lettres générées
        return pack

    @app.post("/api/recompute-v4")
    def recompute_v4_endpoint(db: Database = Depends(get_db)) -> dict[str, Any]:
        cfg = load_config()
        count = db.recompute_scores_v4(config=cfg)
        invalidate()
        return {"ok": True, "recomputed": count}

    @app.post("/api/jobs/grade-v4")
    def grade_jobs_v4(
        payload: GradeBatchPayload,
        db: Database = Depends(get_db),
    ) -> dict[str, Any]:
        cfg = load_config()
        id_set = set(payload.ids)
        targets = [j for j in all_jobs(db) if str(j["id"]) in id_set]
        if not targets:
            raise HTTPException(status_code=404, detail="Aucune offre trouvée")
        # db= : le juge reçoit les corrections et décisions d'Eddy (N3, N6) en calibration.
        judge = LLMJudgeV4(config=cfg, db=db)
        results = []
        for job in targets:
            res = judge.evaluate_single_job_v4(job)
            # Une exclusion est aussi une note : sans l'enregistrer, l'offre serait renotée à chaque appel.
            if res.get("v4_status") in ("OK", "EXCLU"):
                db.update_v4(str(job["id"]), res)
            results.append(
                {
                    "id": str(job["id"]),
                    "status": res.get("v4_status"),
                    "v4_class": res.get("v4_class"),
                    "v4_display_score": res.get("v4_display_score"),
                    "v4_class_score": res.get("v4_class_score"),
                    "excluded": bool(res.get("excluded")) or res.get("v4_status") == "EXCLU",
                    "contract_to_verify": bool(res.get("contract_to_verify")),
                }
            )
        invalidate()
        return {"ok": True, "results": results}

    # ------------------------------------------------------------------ #
    # File de candidature Claude in Chrome (C1-C3)
    # ------------------------------------------------------------------ #
    @app.get("/api/queue")
    def get_apply_queue(include_applied: bool = True, db: Database = Depends(get_db)) -> dict[str, Any]:
        return apply_queue.get_queue(db, include_applied=include_applied)

    @app.get("/api/queue/current")
    def get_apply_queue_current(db: Database = Depends(get_db)) -> dict[str, Any]:
        """Offre courante de la file ; ``current`` vaut ``null`` quand la file est vide."""
        return {"url": apply_queue.QUEUE_URL, "current": apply_queue.get_current(db, config=load_config())}

    @app.get("/api/queue/{job_id}")
    def get_apply_queue_item(job_id: str, db: Database = Depends(get_db)) -> dict[str, Any]:
        item = apply_queue.get_queue_item(db, job_id, config=load_config())
        if item is None:
            raise HTTPException(status_code=404, detail="Offre absente de la file")
        return item

    @app.post("/api/queue")
    def add_to_apply_queue(payload: QueueAddPayload, db: Database = Depends(get_db)) -> dict[str, Any]:
        try:
            result = apply_queue.add_ids_to_queue(
                db,
                payload.ids,
                output_dir=_pack_out_dir(db),
                auto_submit=payload.auto_submit,
            )
        finally:
            invalidate()  # dossiers et lettres générés
        if result.get("missing") and not (result.get("added") or result.get("requeued") or result.get("skipped")):
            raise HTTPException(status_code=404, detail="Aucune offre trouvée pour ces identifiants")
        return {"ok": True, **result}

    @app.post("/api/queue/{job_id}/report")
    def report_apply_queue(
        job_id: str,
        payload: QueueReportPayload,
        db: Database = Depends(get_db),
    ) -> dict[str, Any]:
        try:
            item = apply_queue.record_result(db, job_id, payload.outcome, payload.report)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        finally:
            invalidate()  # statut POSTULÉ possible
        return {"ok": True, "item": item}

    @app.delete("/api/queue/{job_id}")
    def remove_from_apply_queue(job_id: str, db: Database = Depends(get_db)) -> dict[str, Any]:
        removed = apply_queue.remove_from_queue(db, job_id)
        invalidate()
        if not removed:
            raise HTTPException(status_code=404, detail="Offre absente de la file")
        return {"ok": True, "id": job_id}

    # ------------------------------------------------------------------ #
    # Santé des sources (S1, S2, S4)
    # ------------------------------------------------------------------ #
    @app.get("/api/sources/health")
    def get_sources_health(db: Database = Depends(get_db)) -> dict[str, Any]:
        """État de chaque source et de ses boards au dernier run de collecte."""
        from scrapers.health import summarize_source_health

        run = db.get_last_run()
        if run is None:
            return {"run": None, "sources": []}
        rows = db.get_recent_query_stats(limit=500, run_id=str(run["id"]))
        sources = summarize_source_health(rows)
        for entry in sources:
            entry["label"] = source_label(entry["source"])
            entry["color"] = source_color(entry["source"])
        return {
            "run": {
                "id": str(run["id"]),
                "status": str(run.get("status") or ""),
                "started_at": _iso(run.get("started_at")),
                "finished_at": _iso(run.get("finished_at")),
            },
            "sources": sources,
        }

    return app


app = create_app()
