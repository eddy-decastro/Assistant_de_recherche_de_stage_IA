"""Importe automatiquement les JSON hebdomadaires déposés dans data/imports/.

Repère les ``candidatures_gmail_*.json`` absents du registre ``processed.txt``, sauvegarde la base
(data/backups/), importe avec la logique de ``import_applications`` (statuts jamais rétrogradés),
écrit un résumé dans ``import_log.txt`` puis inscrit le fichier au registre. Aucun fichier n'est
supprimé ni déplacé. Un fichier avec erreurs n'est pas inscrit au registre (il sera retenté, l'import
est idempotent) et le code de sortie est non nul.

Usage : python tools/auto_import.py [--imports-dir DIR] [--db FICHIER] [--backup-dir DIR]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.storage.database import Database  # noqa: E402
from tools.import_applications import ImportResult, import_rows, load_rows  # noqa: E402

PATTERN = "candidatures_gmail_*.json"
REGISTRY = "processed.txt"
LOG = "import_log.txt"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def pending_files(imports_dir: Path) -> list[Path]:
    """Fichiers du dossier pas encore inscrits au registre, du plus ancien au plus récent."""
    registry = imports_dir / REGISTRY
    done = set(registry.read_text(encoding="utf-8").split()) if registry.exists() else set()
    return [path for path in sorted(imports_dir.glob(PATTERN)) if path.name not in done]


def backup_database(db_path: Path, backup_dir: Path) -> Path:
    """Copie horodatée de la base (API sqlite : cohérente même en mode WAL)."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"{db_path.stem}_{datetime.now():%Y%m%d_%H%M%S}{db_path.suffix}"
    source, dest = sqlite3.connect(db_path), sqlite3.connect(target)
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()
    return target


def write_log(imports_dir: Path, name: str, result: ImportResult, backup: Path | None) -> None:
    lines = [
        f"=== {datetime.now():%Y-%m-%d %H:%M:%S} — {name} ===",
        f"sauvegarde : {backup}" if backup else "sauvegarde : aucune",
        f"créations : {result.created} | mises à jour : {result.updated} | erreurs : {len(result.errors)}",
    ]
    lines += [f"  erreur : {message}" for message in result.errors]
    lines.append(f"refus importés ({len(result.refusals)}) :")
    lines += [
        f"  - {r['company']} | {r['title'] or '(sans titre)'} | {r['external_id'] or '-'}"
        for r in result.refusals
    ]
    with (imports_dir / LOG).open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--imports-dir", type=Path, default=PROJECT_ROOT / "data" / "imports")
    parser.add_argument("--db", type=Path, default=PROJECT_ROOT / "data" / "stage_copilot.db")
    parser.add_argument("--backup-dir", type=Path, default=PROJECT_ROOT / "data" / "backups")
    args = parser.parse_args(argv)

    failed = False
    for path in pending_files(args.imports_dir):
        backup = None
        try:
            if args.db.exists():
                backup = backup_database(args.db, args.backup_dir)
            result = import_rows(Database(args.db), load_rows(path), apply=True, echo=lambda _: None)
        except Exception as exc:  # fichier illisible, base verrouillée...
            result = ImportResult()
            result.errors.append(f"échec de l'import : {exc}")
        write_log(args.imports_dir, path.name, result, backup)
        print(f"{path.name} : {result.created} créée(s), {result.updated} mise(s) à jour, {len(result.errors)} erreur(s)")
        if result.errors:
            failed = True
            continue
        with (args.imports_dir / REGISTRY).open("a", encoding="utf-8") as handle:
            handle.write(path.name + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
