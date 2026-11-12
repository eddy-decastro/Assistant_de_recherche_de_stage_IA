"""Pont entre la base et une notation v4 hors ligne par agents (Antigravity).

Deux commandes :

    python tools/antigravity_v4.py export [--batch-size 25] [--stale] [--dir DOSSIER] [--force]
        Lecture seule sur la base. Écrit dans data/antigravity/ (ou --dir) :
        - prompt_systeme_v4.txt : prompt v4 actuel, avec les décisions et corrections d'Eddy ;
        - schema_v4.json : schéma JSON attendu pour chaque offre ;
        - in/lot_XXX.json : offres à (re)noter (NOUVEAU, EXCLU, IGNORÉ auto_archive).
        Les offres décidées par Eddy (POSTULÉ, ENTRETIEN, REJETÉ, IGNORÉ à la main) ne sont
        jamais exportées. --stale : seulement les offres sans note v4 ou notées avec une
        autre version de la grille (v4_grid_version). L'export refuse de tourner si out/
        contient des lots pas encore ingérés (sinon les entrées d'une mission en cours
        seraient effacées) ; --force passe outre.

        --verifier-ids FICHIER : ajoute en « vérifier » les offres listées (JSON d'ids), par
        exemple celles que tools/check_closed_offers.py n'a pas pu trancher.

        --verifier-plus-vieux-que 7 : ajoute les offres à trier déjà notées et publiées
        depuis au moins 7 jours, avec ``"mission": "verifier"`` (vivacité seule).

    python tools/antigravity_v4.py ingest [--dry-run] [--dir DOSSIER]
        Lit data/antigravity/out/lot_XXX_out.json (et nouvelles_offres.json), copie la vraie
        base dans data/backups/ puis :
        - recalcule chaque note en code (compute_final_score_v4) et l'enregistre (update_v4) ;
        - offre fermée (vérifiée) : IGNORÉ « offre_fermee » si elle était NOUVEAU, EXCLU ou
          auto-archivée ; offre ouverte auto-archivée : remise en NOUVEAU ;
        - insère les nouvelles offres trouvées (statut NOUVEAU, sans doublon).
        Aucun statut saisi par Eddy n'est modifié. --dry-run travaille sur une copie.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_config  # noqa: E402
from src.constants import (  # noqa: E402
    AUTO_ARCHIVE_REASON,
    CLOSED_OFFER_REASON,
    STATUS_EXCLUDED,
    STATUS_IGNORED,
    STATUS_NEW,
)
from src.matching.scoring_v4 import (  # noqa: E402
    V4JudgeOutput,
    build_calibration_block,
    get_grid_version_v4,
    get_response_schema_v4,
    get_system_prompt_v4,
    v4_result_from_output,
)
from src.storage.database import Database, make_job_id  # noqa: E402

REAL_DB = PROJECT_ROOT / "data" / "stage_copilot.db"
WORK_DIR = PROJECT_ROOT / "data" / "antigravity"
MODEL_LABEL = "antigravity:multi-agent"
LIVENESS_STATES = ("ouverte", "fermee", "inconnue")
INGESTED_MARKER = ".ingere"


class PendingLotsError(RuntimeError):
    """Des lots rendus par les agents n'ont pas encore été ingérés."""


def pending_lots(out_dir: Path) -> list[Path]:
    """Lots ``*_out.json`` plus récents que la dernière ingestion réelle (ou sans ingestion)."""
    lots = sorted(out_dir.glob("lot_*_out.json"))
    marker = out_dir / INGESTED_MARKER
    if not marker.exists():
        return lots
    done_at = marker.stat().st_mtime
    return [p for p in lots if p.stat().st_mtime > done_at]


def is_stale(job: dict[str, Any], grid_version: str) -> bool:
    """Note v4 absente ou faite avec une autre grille."""
    if job.get("v4_display_score") is None:
        return True
    return (job.get("v4_grid_version") or "") != grid_version


def _readonly_copy(db_path: Path) -> Path:
    """Copie cohérente (API backup SQLite) dans un dossier temporaire."""
    tmp = Path(tempfile.mkdtemp(prefix="antigravity_")) / "copie.db"
    src = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(tmp)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    return tmp


def is_exportable(job: dict[str, Any]) -> bool:
    """Offres que l'IA peut (re)noter : jamais une décision d'Eddy."""
    status = job.get("status")
    if status in (STATUS_NEW, STATUS_EXCLUDED):
        return True
    return status == STATUS_IGNORED and (job.get("rejection_reason") or "") == AUTO_ARCHIVE_REASON


def job_age_days(job: dict[str, Any], now: datetime | None = None) -> float | None:
    """Âge de l'offre en jours (date de publication, sinon date de collecte)."""
    raw = job.get("published_at") or job.get("created_at")
    if not raw:
        return None
    when = raw if isinstance(raw, datetime) else None
    if when is None:
        try:
            when = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return None
    if when.tzinfo is not None:
        when = when.replace(tzinfo=None)
    return ((now or datetime.now()) - when).total_seconds() / 86400


def needs_liveness_check(job: dict[str, Any], min_age_days: float) -> bool:
    """Offre encore « à trier » (ou auto-archivée) assez ancienne pour être revérifiée."""
    status = job.get("status")
    auto_archived = status == STATUS_IGNORED and (job.get("rejection_reason") or "") == AUTO_ARCHIVE_REASON
    if status != STATUS_NEW and not auto_archived:
        return False
    age = job_age_days(job)
    return age is not None and age >= min_age_days


def _export_record(job: dict[str, Any], mission: str = "noter") -> dict[str, Any]:
    published = job.get("published_at") or job.get("created_at") or ""
    return {
        "id": job["id"],
        # « noter » : évaluation complète + vivacité ; « verifier » : vivacité seule
        # (l'offre a déjà une note à jour, on veut seulement savoir si elle est fermée).
        "mission": mission,
        "title": job.get("title") or "",
        "company": job.get("company") or "",
        "location": job.get("location") or "",
        "url": job.get("url") or "",
        "source": job.get("source") or "",
        "published_at": str(published),
        "description": (job.get("description") or "").strip()[:12000],
        "etat_actuel": {
            "status": job.get("status"),
            "rejection_reason": job.get("rejection_reason") or "",
            "v4_class": job.get("v4_class") or "",
            "v4_display_score": job.get("v4_display_score"),
            "exclusion": job.get("exclusion_reason") or "",
        },
    }


def export(
    batch_size: int,
    db_path: Path = REAL_DB,
    out_dir: Path = WORK_DIR,
    stale_only: bool = False,
    force: bool = False,
    verify_older_than_days: float | None = None,
    verify_ids: set[str] | None = None,
) -> int:
    pending = pending_lots(out_dir / "out")
    if pending and not force:
        raise PendingLotsError(
            f"{len(pending)} lot(s) rendu(s) pas encore ingéré(s) dans {out_dir / 'out'} "
            f"(ex. {pending[0].name}). Lance d'abord « ingest », ou utilise --dir pour "
            "une nouvelle vague, ou --force pour écraser les entrées."
        )
    copy = _readonly_copy(db_path)
    db = Database(str(copy))
    config = load_config()
    exportable = [j for j in db.get_jobs(limit=100000) if is_exportable(j)]
    # À noter : offres vivantes seulement. Les auto-archivées (plus de 30 jours sans être
    # revues au scraping) ne sont pas notées ; si elles reviennent, elles le seront.
    jobs = [
        j for j in exportable
        if not (j.get("status") == STATUS_IGNORED and (j.get("rejection_reason") or "") == AUTO_ARCHIVE_REASON)
    ]
    if stale_only:
        current = get_grid_version_v4()
        jobs = [j for j in jobs if is_stale(j, current)]
    jobs.sort(key=lambda j: str(j["id"]))
    to_grade = {str(j["id"]) for j in jobs}
    to_verify: list[dict[str, Any]] = []
    if verify_older_than_days is not None:
        to_verify = sorted(
            (j for j in exportable
             if str(j["id"]) not in to_grade and needs_liveness_check(j, verify_older_than_days)),
            # Meilleures notes d'abord : si le temps manque, les offres qui comptent sont vérifiées.
            key=lambda j: (-(j.get("v4_display_score") or 0.0), str(j["id"])),
        )
    if verify_ids:
        # Offres que la vérification par code n'a pas su trancher (check_closed_offers).
        already = to_grade | {str(j["id"]) for j in to_verify}
        to_verify += [
            j for j in exportable
            if str(j["id"]) in verify_ids and str(j["id"]) not in already
        ]
    records = [_export_record(j) for j in jobs] + [_export_record(j, "verifier") for j in to_verify]

    in_dir = out_dir / "in"
    in_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "out").mkdir(parents=True, exist_ok=True)
    for old in in_dir.glob("lot_*.json"):
        old.unlink()

    prompt = get_system_prompt_v4(build_calibration_block(db, config))
    (out_dir / "prompt_systeme_v4.txt").write_text(prompt, encoding="utf-8")
    (out_dir / "schema_v4.json").write_text(
        json.dumps(get_response_schema_v4(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    n_lots = 0
    for start in range(0, len(records), batch_size):
        n_lots += 1
        lot = records[start:start + batch_size]
        (in_dir / f"lot_{n_lots:03d}.json").write_text(
            json.dumps(lot, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(
        f"{len(records)} offres exportées en {n_lots} lots dans {in_dir} "
        f"({len(jobs)} à noter, {len(to_verify)} à vérifier seulement)"
    )
    return len(records)


def _apply_liveness(db: Database, job: dict[str, Any], liveness: dict[str, Any]) -> str:
    """Applique le verdict « offre ouverte / fermée ». Renvoie l'action faite."""
    state = str(liveness.get("etat") or "inconnue").lower()
    if state not in LIVENESS_STATES or state == "inconnue":
        return "inchange"
    if not liveness.get("verifie"):
        return "inchange"
    status = job.get("status")
    auto_archived = status == STATUS_IGNORED and job.get("rejection_reason") == AUTO_ARCHIVE_REASON
    if state == "fermee" and (status in (STATUS_NEW, STATUS_EXCLUDED) or auto_archived):
        motif = str(liveness.get("preuve") or "page fermée")[:200]
        db.update_status(job["id"], STATUS_IGNORED, rejection_reason=CLOSED_OFFER_REASON)
        _append_note(db, job, f"[Antigravity] Fermée : {motif}")
        return "fermee"
    if state == "ouverte" and auto_archived:
        db.update_status(job["id"], STATUS_NEW, rejection_reason="")
        return "reouverte"
    return "inchange"


def _append_note(db: Database, job: dict[str, Any], note: str) -> None:
    current = (job.get("user_notes") or "").strip()
    db.update_user_notes(job["id"], f"{current}\n{note}".strip() if current else note)


def _insert_new_offers(db: Database, path: Path, known_urls: set[str]) -> int:
    if not path.exists():
        return 0
    offers = json.loads(path.read_text(encoding="utf-8"))
    added = 0
    for offer in offers:
        url = str(offer.get("url") or "").strip()
        title = str(offer.get("title") or "").strip()
        company = str(offer.get("company") or "").strip()
        if not (url and title and company) or url in known_urls:
            continue
        job_id = make_job_id(title, company, url)
        if db.get_job(job_id) is not None:
            continue
        db.upsert_job({
            "id": job_id,
            "title": title,
            "company": company,
            "location": str(offer.get("location") or ""),
            "url": url,
            "source": "manuel",
            "description": str(offer.get("description") or ""),
            "status": STATUS_NEW,
        })
        known_urls.add(url)
        added += 1
    return added


def ingest(dry_run: bool, db_path: Path = REAL_DB, work_dir: Path = WORK_DIR) -> dict[str, int]:
    out_dir = work_dir / "out"
    if dry_run:
        target = _readonly_copy(db_path)
        print(f"Essai à blanc sur une copie : {target}")
    else:
        backup_dir = db_path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / f"stage_copilot_avant_antigravity_{datetime.now():%Y%m%d_%H%M%S}.db"
        src = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        dst = sqlite3.connect(backup)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
        print(f"Sauvegarde : {backup}")
        target = db_path

    db = Database(str(target))
    config = load_config()
    jobs = {j["id"]: j for j in db.get_jobs(limit=100000)}
    stats = {"notees": 0, "verifiees_seulement": 0, "invalides": 0, "inconnues": 0,
             "ignorees_decision_eddy": 0, "fermees": 0, "reouvertes": 0, "nouvelles_offres": 0}

    for path in sorted(out_dir.glob("lot_*_out.json")):
        results = json.loads(path.read_text(encoding="utf-8"))
        for job_id, entry in results.items():
            job = jobs.get(job_id)
            if job is None:
                stats["inconnues"] += 1
                continue
            if not is_exportable(job):
                stats["ignorees_decision_eddy"] += 1
                continue
            if "evaluation" not in entry:
                # Mission « verifier » : la note reste, seule la vivacité s'applique.
                if not entry.get("vivacite"):
                    stats["invalides"] += 1
                    print(f"  {path.name} {job_id[:10]} invalide : ni évaluation ni vivacité")
                    continue
                stats["verifiees_seulement"] += 1
                action = _apply_liveness(db, job, entry["vivacite"])
                if action == "fermee":
                    stats["fermees"] += 1
                elif action == "reouverte":
                    stats["reouvertes"] += 1
                continue
            try:
                validated = V4JudgeOutput.model_validate(entry["evaluation"])
            except Exception as exc:  # sortie d'agent mal formée : on la saute, on la signale
                stats["invalides"] += 1
                print(f"  {path.name} {job_id[:10]} invalide : {exc}")
                continue
            result = v4_result_from_output(
                validated, dict(entry["evaluation"]), job, config, MODEL_LABEL
            )
            db.update_v4(job_id, result)
            stats["notees"] += 1
            fresh = db.get_job(job_id) or job
            action = _apply_liveness(db, fresh, entry.get("vivacite") or {})
            if action == "fermee":
                stats["fermees"] += 1
            elif action == "reouverte":
                stats["reouvertes"] += 1

    known_urls = {str(j.get("url") or "") for j in jobs.values()}
    stats["nouvelles_offres"] = _insert_new_offers(db, out_dir / "nouvelles_offres.json", known_urls)
    if not dry_run:
        (out_dir / INGESTED_MARKER).write_text(datetime.now().isoformat(), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_exp = sub.add_parser("export")
    p_exp.add_argument("--batch-size", type=int, default=25)
    p_exp.add_argument("--stale", action="store_true",
                       help="Seulement les offres sans note v4 ou notées avec une autre grille.")
    p_exp.add_argument("--force", action="store_true",
                       help="Exporter même si des lots rendus n'ont pas été ingérés.")
    p_exp.add_argument("--verifier-plus-vieux-que", type=float, default=None, metavar="JOURS",
                       dest="verify_days",
                       help="Ajouter, en « vérifier seulement », les offres à trier déjà notées "
                            "et publiées il y a au moins JOURS jours.")
    p_exp.add_argument("--verifier-ids", type=Path, default=None, dest="verify_ids",
                       help="Fichier JSON d'identifiants à vérifier seulement (ex. "
                            "data/antigravity/vivacite_inconnues.json écrit par check_closed_offers).")
    p_ing = sub.add_parser("ingest")
    p_ing.add_argument("--dry-run", action="store_true")
    for p in (p_exp, p_ing):
        p.add_argument("--dir", type=Path, default=WORK_DIR,
                       help="Dossier de la vague (défaut : data/antigravity).")
    args = parser.parse_args(argv)
    if args.cmd == "export":
        try:
            ids = (set(json.loads(args.verify_ids.read_text(encoding="utf-8")))
                   if args.verify_ids else None)
            export(args.batch_size, out_dir=args.dir, stale_only=args.stale, force=args.force,
                   verify_older_than_days=args.verify_days, verify_ids=ids)
        except PendingLotsError as exc:
            print(f"Export refusé : {exc}")
            return 1
    else:
        ingest(args.dry_run, work_dir=args.dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
