"""Serveur FastAPI exposant la base SQLite, le scoring v3/v4 et le Pack Claude.

Lancez avec :
    uvicorn api.main:app --reload --port 8000
ou via :
    python run_web.py
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

from components.job_feed.serialize import serialize_job
from src.config import PROJECT_ROOT, load_config
from src.constants import (
    STATUS_EXCLUDED,
    STATUS_IGNORED,
    STATUS_NEW,
    STATUS_REJECTED,
    V4_CLASSES,
    V4_CLASS_LABELS,
    V4_TAGS,
    V4_TAG_LABELS,
    VALID_STATUSES,
    source_label,
)
from src.matching.claude_pack import build_claude_pack
from src.matching.cover_letter import CoverLetterGenerator
from src.matching.pdf_exporter import generate_cover_letter_pdf
from src.matching.scoring_v4 import LLMJudgeV4
from src.storage.database import Database
from utils.data import Filters, filter_jobs, job_age_days


class StatusUpdatePayload(BaseModel):
    status: str
    rejection_reason: str | None = None


class BulkStatusPayload(BaseModel):
    ids: list[str] = Field(min_length=1)
    status: str
    rejection_reason: str | None = None


class QuickArchiveOldPayload(BaseModel):
    days: int = Field(default=14, ge=1, le=365)
    action: str = Field(default="archive", pattern="^(archive|trash)$")


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



def create_app(db_instance: Database | None = None) -> FastAPI:
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

    state: dict[str, Any] = {"db": db_instance}

    def get_db() -> Database:
        if state["db"] is None:
            cfg = load_config()
            db_path = Path(cfg.get("database", {}).get("path", "data/stage_copilot.db"))
            if not db_path.is_absolute():
                db_path = PROJECT_ROOT / db_path
            state["db"] = Database(str(db_path))
        return state["db"]

    def _serialize_full_job(job: dict[str, Any], keywords: tuple[str, ...]) -> dict[str, Any]:
        base = serialize_job(job, keywords)
        base["cover_letter"] = str(job.get("cover_letter") or "")
        base["created_at"] = str(job.get("created_at") or "")
        base["published_at"] = str(job.get("published_at") or "")
        base["applied_at"] = str(job.get("applied_at") or "")
        base["v4_model"] = str(job.get("v4_model") or "")
        base["v4_graded_at"] = str(job.get("v4_graded_at") or "")
        return base

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/stats")
    def get_stats(db: Database = Depends(get_db)) -> dict[str, Any]:
        jobs = db.get_jobs(limit=10000)
        by_status: dict[str, int] = {}
        by_v4_class: dict[str, int] = {c: 0 for c in V4_CLASSES}
        by_source: dict[str, int] = {}
        old_new_14d = 0
        v4_graded = 0
        v3_graded = 0

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
            is_exclu = (
                st_code == STATUS_EXCLUDED
                or j.get("verdict") == "EXCLU"
                or bool(j.get("exclusion_reason"))
            )
            if st_code == STATUS_NEW and not is_exclu and (job_age_days(j) or 0) >= 14:
                old_new_14d += 1

        return {
            "total": len(jobs),
            "by_status": by_status,
            "by_v4_class": by_v4_class,
            "by_source": [
                {"key": k, "label": source_label(k), "count": v}
                for k, v in sorted(by_source.items(), key=lambda x: -x[1])
            ],
            "old_new_14d": old_new_14d,
            "v3_graded": v3_graded,
            "v4_graded": v4_graded,
            "v4_classes": [{"key": c, "label": V4_CLASS_LABELS[c]} for c in V4_CLASSES],
            "v4_tags": [{"key": t, "label": V4_TAG_LABELS.get(t, t)} for t in V4_TAGS],
        }

    @app.get("/api/jobs")
    def list_jobs(
        q: str = "",
        statuses: str = "",
        sources: str = "",
        v4_classes: str = "",
        v4_tags: str = "",
        exclude_v4_tags: str = "",
        min_score: float = 0.0,
        hide_processed: bool = False,
        hide_excluded: bool = True,
        hide_defense: bool = False,
        only_older_than_days: int | None = None,
        sort_by: str = "v3",
        limit: int = Query(default=250, ge=1, le=2000),
        db: Database = Depends(get_db),
    ) -> dict[str, Any]:
        cfg = load_config()
        keywords = tuple(cfg.get("scoring", {}).get("excellence_keywords", ()))
        all_jobs = db.get_jobs(limit=10000)

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
            hide_processed=hide_processed,
            hide_excluded=hide_excluded,
            hide_defense_ethics=hide_defense,
            v4_classes=cls_tuple,
            v4_tags=tag_tuple,
            exclude_v4_tags=ex_tag_tuple,
            sort_by=sort_by,
            limit=None,
        )
        filtered = filter_jobs(all_jobs, flt)

        if only_older_than_days is not None:
            filtered = [
                j for j in filtered if (job_age_days(j) or 0) >= only_older_than_days
            ]

        if sort_by == "freshness":
            filtered.sort(
                key=lambda j: str(j.get("published_at") or j.get("created_at") or ""),
                reverse=True,
            )

        total_matching = len(filtered)
        sliced = filtered[:limit]
        return {
            "total": total_matching,
            "jobs": [_serialize_full_job(j, keywords) for j in sliced],
        }

    @app.post("/api/jobs/{job_id}/status")
    def update_job_status(
        job_id: str,
        payload: StatusUpdatePayload,
        db: Database = Depends(get_db),
    ) -> dict[str, Any]:
        if payload.status not in VALID_STATUSES:
            raise HTTPException(status_code=400, detail=f"Statut invalide : {payload.status}")
        ok = db.update_status(job_id, payload.status, rejection_reason=payload.rejection_reason)
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
        count = db.bulk_update_status(
            payload.ids, payload.status, rejection_reason=payload.rejection_reason
        )
        return {"ok": True, "updated": count, "status": payload.status}

    @app.post("/api/jobs/quick-archive-old")
    def quick_archive_old_offers(
        payload: QuickArchiveOldPayload,
        db: Database = Depends(get_db),
    ) -> dict[str, Any]:
        all_jobs = db.get_jobs(limit=10000)
        old_ids = [
            str(j["id"])
            for j in all_jobs
            if j.get("status") == STATUS_NEW
            and not (
                j.get("verdict") == "EXCLU"
                or bool(j.get("exclusion_reason"))
            )
            and (job_age_days(j) or 0) >= payload.days
        ]
        target_status = STATUS_IGNORED if payload.action == "archive" else STATUS_REJECTED
        reason = f"quick_{payload.action}_{payload.days}d"
        updated = db.bulk_update_status(old_ids, target_status, rejection_reason=reason)
        return {"ok": True, "updated": updated, "status": target_status, "ids": old_ids}

    @app.post("/api/jobs/{job_id}/notes")
    def update_job_notes(
        job_id: str,
        payload: NotesPayload,
        db: Database = Depends(get_db),
    ) -> dict[str, Any]:
        ok = db.update_user_notes(job_id, payload.user_notes)
        if not ok:
            raise HTTPException(status_code=404, detail="Offre introuvable")
        return {"ok": True, "id": job_id, "user_notes": payload.user_notes}

    @app.post("/api/jobs/{job_id}/cover-letter")
    def generate_or_save_cover_letter(
        job_id: str,
        payload: CoverLetterPayload,
        db: Database = Depends(get_db),
    ) -> dict[str, Any]:
        jobs = db.get_jobs(limit=10000)
        job = next((j for j in jobs if str(j["id"]) == job_id), None)
        if job is None:
            raise HTTPException(status_code=404, detail="Offre introuvable")

        if payload.edited_text is not None and not payload.regenerate:
            letter = payload.edited_text
            source = "edited"
        else:
            existing = str(job.get("cover_letter") or "").strip()
            if existing and not payload.regenerate:
                letter = existing
                source = "cached"
            else:
                gen = CoverLetterGenerator()
                letter = gen.generate(
                    job,
                    custom_instruction=payload.custom_instruction,
                    allow_fallback=True,
                )
                source = gen.last_source

        db.update_cover_letter(job_id, letter)
        return {"ok": True, "id": job_id, "cover_letter": letter, "source": source}

    @app.get("/api/jobs/{job_id}/cover-letter.pdf")
    def download_cover_letter_pdf(
        job_id: str,
        db: Database = Depends(get_db),
    ) -> Response:
        jobs = db.get_jobs(limit=10000)
        job = next((j for j in jobs if str(j["id"]) == job_id), None)
        if job is None:
            raise HTTPException(status_code=404, detail="Offre introuvable")
        letter = str(job.get("cover_letter") or "").strip()
        if not letter:
            gen = CoverLetterGenerator()
            letter = gen.generate(job, allow_fallback=True)
            db.update_cover_letter(job_id, letter)
        pdf_bytes = generate_cover_letter_pdf(letter, job=job)
        company = "".join(
            c for c in str(job.get("company") or "Entreprise") if c.isalnum() or c in ("-", "_", " ")
        ).strip()
        filename = f"Lettre_Eddy_De_Castro_{company or 'Stage'}.pdf"
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.post("/api/claude-pack")
    def create_claude_pack(
        payload: ClaudePackPayload,
        db: Database = Depends(get_db),
    ) -> dict[str, Any]:
        jobs = db.get_jobs(limit=10000)
        id_set = set(payload.ids)
        selected = [j for j in jobs if str(j["id"]) in id_set]
        if not selected:
            raise HTTPException(status_code=404, detail="Aucune offre trouvée pour ces identifiants")
        pack = build_claude_pack(
            selected,
            db=db,
            save_pdfs=True,
            auto_submit=payload.auto_submit,
        )
        return pack

    @app.post("/api/recompute-v4")
    def recompute_v4_endpoint(db: Database = Depends(get_db)) -> dict[str, Any]:
        cfg = load_config()
        count = db.recompute_scores_v4(config=cfg)
        return {"ok": True, "recomputed": count}

    @app.post("/api/jobs/grade-v4")
    def grade_jobs_v4(
        payload: GradeBatchPayload,
        db: Database = Depends(get_db),
    ) -> dict[str, Any]:
        cfg = load_config()
        jobs = db.get_jobs(limit=10000)
        id_set = set(payload.ids)
        targets = [j for j in jobs if str(j["id"]) in id_set]
        if not targets:
            raise HTTPException(status_code=404, detail="Aucune offre trouvée")
        judge = LLMJudgeV4(config=cfg)
        results = []
        for job in targets:
            res = judge.evaluate_single_job_v4(job)
            if res.get("v4_status") == "OK":
                db.update_v4(str(job["id"]), res)
            results.append(
                {
                    "id": str(job["id"]),
                    "status": res.get("v4_status"),
                    "v4_class": res.get("v4_class"),
                    "v4_display_score": res.get("v4_display_score"),
                    "v4_class_score": res.get("v4_class_score"),
                }
            )
        return {"ok": True, "results": results}

    return app


app = create_app()
