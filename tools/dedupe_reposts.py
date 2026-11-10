"""Range les republications déjà en base (même entreprise, même titre, même texte).

    python tools/dedupe_reposts.py            # essai à blanc sur une copie (défaut)
    python tools/dedupe_reposts.py --apply    # sauvegarde dans data/backups/ puis écrit

Pour chaque groupe de republications, on garde une offre « canonique » :
d'abord une offre décidée par Eddy (Postulé, Entretien, Refus, Ignoré à la main),
puis une offre notée, puis la description la plus longue, puis la plus ancienne.
Les autres passent en IGNORÉ avec la raison « doublon » et une note « doublon de … ».

Jamais touchées : les décisions d'Eddy, et les offres d'un groupe dont la seule
canonique possible est fermée ou archivée (la republication est alors l'offre vivante).
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.constants import (  # noqa: E402
    AUTO_IGNORE_REASONS,
    DUPLICATE_REASON,
    STATUS_EXCLUDED,
    STATUS_IGNORED,
    STATUS_NEW,
)
from src.ingestion.bridge import is_repost, normalized_text  # noqa: E402
from src.storage.cleanup import normalize_company  # noqa: E402
from src.storage.database import Database  # noqa: E402

REAL_DB = PROJECT_ROOT / "data" / "stage_copilot.db"


def _copy(db_path: Path, target: Path) -> Path:
    src = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    return target


def is_eddy_decision(job: dict[str, Any]) -> bool:
    status = job.get("status")
    if status in (STATUS_NEW, STATUS_EXCLUDED):
        return False
    if status == STATUS_IGNORED:
        return str(job.get("rejection_reason") or "") not in AUTO_IGNORE_REASONS
    return True  # POSTULÉ, ENTRETIEN, REJETÉ


def _is_closed(job: dict[str, Any]) -> bool:
    return job.get("status") == STATUS_IGNORED and str(job.get("rejection_reason") or "") in AUTO_IGNORE_REASONS


def _canonical_rank(job: dict[str, Any]) -> tuple:
    return (
        0 if is_eddy_decision(job) else 1,
        1 if _is_closed(job) else 0,
        0 if job.get("v4_display_score") is not None else 1,
        -len(job.get("description") or ""),
        str(job.get("created_at") or ""),
    )


def plan_duplicates(jobs: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Couples ``(doublon à ignorer, offre canonique)``."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for job in jobs:
        key = (normalize_company(str(job.get("company") or "")), normalized_text(job.get("title")))
        if key[0] and key[1]:
            groups[key].append(job)

    plan: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        remaining = sorted(members, key=_canonical_rank)
        while remaining:
            canonical, rest = remaining[0], remaining[1:]
            twins = [
                j for j in rest
                if is_repost(
                    canonical.get("company") or "", canonical.get("title") or "", canonical.get("description"),
                    j.get("company") or "", j.get("title") or "", j.get("description"),
                    canonical.get("location"), j.get("location"),
                )
            ]
            if not _is_closed(canonical):
                plan.extend(
                    (twin, canonical) for twin in twins
                    if not is_eddy_decision(twin) and twin.get("status") != STATUS_IGNORED
                )
            remaining = [j for j in rest if j not in twins]
    return plan


def run(apply: bool, db_path: Path = REAL_DB) -> int:
    if apply:
        backup_dir = db_path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = _copy(db_path, backup_dir / f"stage_copilot_avant_doublons_{datetime.now():%Y%m%d_%H%M%S}.db")
        print(f"Sauvegarde : {backup}")
        target = db_path
    else:
        target = _copy(db_path, Path(tempfile.mkdtemp(prefix="doublons_")) / "copie.db")
        print(f"Essai à blanc sur une copie : {target}")

    db = Database(str(target))
    plan = plan_duplicates(db.get_jobs(limit=100000))
    for twin, canonical in plan:
        db.update_status(twin["id"], STATUS_IGNORED, rejection_reason=DUPLICATE_REASON)
        note = f"[doublon] republication de {canonical['id']} ({canonical.get('status')})"
        current = (twin.get("user_notes") or "").strip()
        db.update_user_notes(twin["id"], f"{current}\n{note}".strip() if current else note)
        print(f"  {twin.get('status'):8} {str(twin.get('company'))[:25]:25} {str(twin.get('title'))[:45]}"
              f"  -> doublon de {canonical['id'][:10]}")
    print(f"{len(plan)} doublon(s) {'rangé(s)' if apply else 'à ranger (rien écrit dans la vraie base)'}.")
    return len(plan)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Écrire dans la vraie base (après sauvegarde).")
    parser.add_argument("--db", type=Path, default=REAL_DB)
    args = parser.parse_args(argv)
    run(args.apply, db_path=args.db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
