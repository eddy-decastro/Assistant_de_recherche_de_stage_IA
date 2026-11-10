"""Rotation des sauvegardes de la base dans data/backups/.

    python tools/rotate_backups.py            # affiche ce qui serait supprimé (défaut)
    python tools/rotate_backups.py --apply    # supprime pour de bon

Garde les N sauvegardes les plus récentes (défaut 10), plus la plus récente de chaque
mois plus ancien. Supprime aussi les fichiers vides (0 octet) et les ``-shm`` / ``-wal``
orphelins. Ne touche qu'aux fichiers ``*.db`` (et leurs annexes) directement dans le
dossier : les sous-dossiers (ex. ``archive_20261104``) sont laissés tels quels.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKUP_DIR = PROJECT_ROOT / "data" / "backups"


def plan_rotation(backup_dir: Path, keep: int = 10) -> list[Path]:
    """Fichiers à supprimer, triés par nom."""
    to_delete: set[Path] = set()
    dbs = [p for p in backup_dir.glob("*.db") if p.is_file()]
    for p in dbs:
        if p.stat().st_size == 0:
            to_delete.add(p)
    live = sorted((p for p in dbs if p not in to_delete), key=lambda p: p.stat().st_mtime, reverse=True)
    kept = set(live[:keep])
    months_seen: set[str] = set()
    for p in live:
        month = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m")
        if p in kept:
            months_seen.add(month)
        elif month not in months_seen:
            months_seen.add(month)
            kept.add(p)
        else:
            to_delete.add(p)
    for side in list(backup_dir.glob("*.db-shm")) + list(backup_dir.glob("*.db-wal")):
        main = side.with_name(side.name.rsplit("-", 1)[0])
        if not main.exists() or main in to_delete:
            to_delete.add(side)
    return sorted(to_delete)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Supprimer réellement les fichiers.")
    parser.add_argument("--keep", type=int, default=10, help="Sauvegardes récentes à garder (défaut 10).")
    parser.add_argument("--dir", type=Path, default=BACKUP_DIR)
    args = parser.parse_args(argv)
    victims = plan_rotation(args.dir, keep=args.keep)
    freed = sum(p.stat().st_size for p in victims)
    for p in victims:
        print(f"  {'supprimé' if args.apply else 'à supprimer'} : {p.name} ({p.stat().st_size / 1e6:.1f} Mo)")
        if args.apply:
            p.unlink()
    verb = "libérés" if args.apply else "libérables (rien supprimé, ajouter --apply)"
    print(f"{len(victims)} fichier(s), {freed / 1e6:.0f} Mo {verb}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
