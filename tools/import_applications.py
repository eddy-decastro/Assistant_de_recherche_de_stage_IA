"""Importe des candidatures faites hors scraping (mails, saisie en lot) dans Stage Copilot.

Entrée : un fichier JSON (liste d'objets) ou CSV avec les champs
    company, title, status (postule | entretien | refus), date (AAAA-MM-JJ),
    et en option url, location, source (manuel | gmail), external_id, note.

Usage :
    python tools/import_applications.py candidatures.json            # aperçu, n'écrit rien
    python tools/import_applications.py candidatures.json --apply    # écrit dans la base
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.constants import (  # noqa: E402
    SOURCE_MANUAL,
    STATUS_APPLIED,
    STATUS_INTERVIEW,
    STATUS_REJECTED,
)
from src.storage.database import Database  # noqa: E402

STATUS_ALIASES = {
    "postule": STATUS_APPLIED,
    "postulé": STATUS_APPLIED,
    STATUS_APPLIED.lower(): STATUS_APPLIED,
    "entretien": STATUS_INTERVIEW,
    STATUS_INTERVIEW.lower(): STATUS_INTERVIEW,
    "refus": STATUS_REJECTED,
    "refusé": STATUS_REJECTED,
    STATUS_REJECTED.lower(): STATUS_REJECTED,
}

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load_rows(path: Path) -> list[dict[str, Any]]:
    """Lit la liste des candidatures depuis un fichier JSON ou CSV."""
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Le JSON doit contenir une liste de candidatures.")
    return data


def parse_row(row: dict[str, Any]) -> dict[str, Any]:
    """Valide une ligne et la convertit en arguments de ``Database.record_application``."""
    status = STATUS_ALIASES.get(str(row.get("status") or "postule").strip().lower())
    if status is None:
        raise ValueError(f"statut inconnu : {row.get('status')!r}")
    date = str(row.get("date") or "").strip()
    return {
        "company": str(row.get("company") or "").strip(),
        "title": str(row.get("title") or "").strip(),
        "status": status,
        "applied_at": datetime.strptime(date[:10], "%Y-%m-%d") if date else None,
        "url": (str(row.get("url") or "").strip() or None),
        "location": (str(row.get("location") or "").strip() or None),
        "source": (str(row.get("source") or "").strip() or SOURCE_MANUAL),
        "external_id": (str(row.get("external_id") or "").strip() or None),
        "note": (str(row.get("note") or "").strip() or None),
    }


class ImportResult:
    """Bilan d'un import : compteurs, erreurs et refus rencontrés."""

    def __init__(self) -> None:
        self.created = 0
        self.updated = 0
        self.errors: list[str] = []
        self.refusals: list[dict[str, Any]] = []


def import_rows(db: Database, rows: list[dict[str, Any]], apply: bool, echo=print) -> ImportResult:
    """Importe (ou prévisualise) les lignes. Un statut n'est jamais rétrogradé (voir ``Database.record_application``)."""
    result = ImportResult()
    for index, row in enumerate(rows, start=1):
        try:
            fields = parse_row(row)
            label = f"{fields['company']} — {fields['title']} → {fields['status']}"
            if not apply:
                known = db.find_application(fields["company"], fields["title"], fields["external_id"])
                action = f"mise à jour (actuel : {known['status']})" if known else "création"
                echo(f"[aperçu] {label} : {action}")
                continue
            _, is_new = db.record_application(**fields)
            result.created += int(is_new)
            result.updated += int(not is_new)
            if fields["status"] == STATUS_REJECTED:
                result.refusals.append({k: fields[k] for k in ("company", "title", "external_id")})
            echo(f"{'créée ' if is_new else 'màj   '} {label}")
        except (ValueError, KeyError) as exc:
            message = f"ligne {index} ignorée : {exc}"
            result.errors.append(message)
            echo(message)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("fichier", type=Path, help="JSON ou CSV des candidatures")
    parser.add_argument("--apply", action="store_true", help="écrit dans la base (sinon simple aperçu)")
    parser.add_argument("--db", type=Path, default=PROJECT_ROOT / "data" / "stage_copilot.db")
    args = parser.parse_args(argv)

    result = import_rows(Database(args.db), load_rows(args.fichier), args.apply)
    if args.apply:
        print(f"\n{result.created} créée(s), {result.updated} mise(s) à jour, {len(result.errors)} erreur(s).")
        print("Si la base est synchronisée dans le cloud : python scripts/sync_db.py --push")
    else:
        print("\nAperçu seulement. Relancer avec --apply pour écrire dans la base.")
    return 1 if result.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
