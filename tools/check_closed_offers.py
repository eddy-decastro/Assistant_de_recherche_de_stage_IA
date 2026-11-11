"""Vérifie en masse si les offres anciennes sont encore ouvertes (détecteur du projet).

    python tools/check_closed_offers.py                 # essai à blanc sur une copie (défaut)
    python tools/check_closed_offers.py --apply         # sauvegarde dans data/backups/ puis écrit
    python tools/check_closed_offers.py --apply --rouvrir-archivees

Offres « à trier » publiées (ou collectées) depuis au moins ``--age`` jours (7 par
défaut) : la page est sondée par ``scrapers.liveness`` (marqueurs vérifiés par
source : « Les candidatures ne sont plus acceptées » sur LinkedIn, « Ce job n'est
plus disponible » sur WTTJ, 404 des ATS, API Workday…). Seule une PREUVE de
fermeture archive l'offre (IGNORÉ, raison « offre_fermee ») ; un blocage
anti-robot ou un 429 ne conclut rien.

``--rouvrir-archivees`` : sonde aussi les offres archivées par l'ancien archivage
par âge (raison « auto_archive ») et remet « à trier » celles qui sont ouvertes.

Les verdicts « ouvert » sont mis en cache 3 jours : relancer la commande reprend
là où elle s'était arrêtée sans resonder les offres déjà vues.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.constants import (  # noqa: E402
    AUTO_ARCHIVE_REASON,
    CLOSED_OFFER_REASON,
    STATUS_IGNORED,
    STATUS_NEW,
)
from src.storage.database import Database  # noqa: E402
from tools.antigravity_v4 import job_age_days  # noqa: E402

REAL_DB = PROJECT_ROOT / "data" / "stage_copilot.db"
# Offres que le code n'a pas pu trancher (anti-robot, 429, page ambiguë) : à faire
# vérifier par des agents (tools/antigravity_v4.py export --verifier-ids ce_fichier).
UNKNOWN_FILE = PROJECT_ROOT / "data" / "antigravity" / "vivacite_inconnues.json"


def _copy(db_path: Path, target: Path) -> Path:
    src = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    return target


def select_candidates(jobs: list[dict[str, Any]], age_days: float, reopen: bool) -> list[dict[str, Any]]:
    """Offres à sonder : à trier assez anciennes, plus les auto-archivées si ``reopen``."""
    picked = []
    for job in jobs:
        status = job.get("status")
        auto_archived = status == STATUS_IGNORED and (job.get("rejection_reason") or "") == AUTO_ARCHIVE_REASON
        if status == STATUS_NEW:
            age = job_age_days(job)
            if age is not None and age >= age_days:
                picked.append(job)
        elif reopen and auto_archived:
            picked.append(job)
    # Meilleures notes d'abord : si on s'arrête en route, l'essentiel est fait.
    picked.sort(key=lambda j: -(j.get("v4_display_score") or 0.0))
    return picked


def run(
    apply: bool,
    db_path: Path = REAL_DB,
    age_days: float = 7.0,
    reopen: bool = False,
    max_checks: int | None = None,
    checker: Any = None,
    unknown_out: Path | None = None,
) -> Counter:
    if apply:
        backup_dir = db_path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = _copy(db_path, backup_dir / f"stage_copilot_avant_vivacite_{datetime.now():%Y%m%d_%H%M%S}.db")
        print(f"Sauvegarde : {backup}")
        target = db_path
    else:
        target = _copy(db_path, Path(tempfile.mkdtemp(prefix="vivacite_")) / "copie.db")
        print(f"Essai à blanc sur une copie : {target}")

    db = Database(str(target))
    candidates = select_candidates(db.get_jobs(limit=100000), age_days, reopen)
    if max_checks is not None:
        candidates = candidates[:max_checks]
    print(f"{len(candidates)} offre(s) à vérifier.")

    own_checker = checker is None
    if own_checker:
        from scrapers.cache import DiskCache
        from scrapers.liveness import LivenessChecker

        checker = LivenessChecker(cache=DiskCache())

    stats: Counter = Counter()
    by_source: Counter = Counter()
    unknown_ids: list[str] = []
    start = time.monotonic()
    try:
        for index, job in enumerate(candidates, start=1):
            verdict = checker.check((job.get("url") or "").strip(), job.get("source"))
            by_source[(job.get("source") or "?", verdict.status)] += 1
            current = db.get_job(job["id"]) or {}
            if verdict.is_closed and current.get("status") == STATUS_NEW:
                db.update_status(job["id"], STATUS_IGNORED, rejection_reason=CLOSED_OFFER_REASON)
                note = f"[Auto] Fermée : {verdict.reason}"
                notes = (current.get("user_notes") or "").strip()
                db.update_user_notes(job["id"], f"{notes}\n{note}".strip() if notes else note)
                stats["fermees"] += 1
            elif (
                verdict.status == "ouvert"
                and current.get("status") == STATUS_IGNORED
                and (current.get("rejection_reason") or "") == AUTO_ARCHIVE_REASON
            ):
                db.update_status(job["id"], STATUS_NEW, rejection_reason="")
                stats["rouvertes"] += 1
            else:
                stats[verdict.status] += 1
                if verdict.status == "inconnu":
                    unknown_ids.append(str(job["id"]))
            if index % 50 == 0 or index == len(candidates):
                elapsed = time.monotonic() - start
                print(f"  {index}/{len(candidates)} en {elapsed / 60:.1f} min — {dict(stats)}", flush=True)
    finally:
        if own_checker:
            checker.close()

    if unknown_out is not None:
        unknown_out.parent.mkdir(parents=True, exist_ok=True)
        unknown_out.write_text(json.dumps(unknown_ids, ensure_ascii=False, indent=0), encoding="utf-8")
        print(f"{len(unknown_ids)} offre(s) non tranchée(s) -> {unknown_out} (à faire vérifier par des agents)")
    print("Par source :", {f"{s}/{v}": n for (s, v), n in sorted(by_source.items())})
    print(f"Bilan : {dict(stats)} {'(écrit en base)' if apply else '(rien écrit dans la vraie base)'}")
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Écrire dans la vraie base (après sauvegarde).")
    parser.add_argument("--age", type=float, default=7.0, help="Âge minimal en jours (défaut 7).")
    parser.add_argument("--rouvrir-archivees", action="store_true", dest="reopen",
                        help="Sonder aussi les offres auto-archivées et remettre à trier les ouvertes.")
    parser.add_argument("--max", type=int, default=None, help="Nombre maximal d'offres sondées.")
    parser.add_argument("--db", type=Path, default=REAL_DB)
    parser.add_argument("--liste-inconnues", type=Path, default=UNKNOWN_FILE, dest="unknown_out",
                        help="Fichier JSON des offres non tranchées (défaut : data/antigravity/vivacite_inconnues.json).")
    args = parser.parse_args(argv)
    run(args.apply, db_path=args.db, age_days=args.age, reopen=args.reopen, max_checks=args.max,
        unknown_out=args.unknown_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
