"""Outil CLI de synchronisation de la base SQLite avec le Cloud (R2 / S3).

Usage:
  python scripts/sync_db.py --pull      # Télécharge la base depuis le bucket
  python scripts/sync_db.py --push      # Envoie la base locale vers le bucket
  python scripts/sync_db.py --status    # Affiche l'état de synchronisation
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# Assure que la racine du projet est dans sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.storage.cloud_storage import (
    DEFAULT_LOCAL_DB,
    download_database,
    get_cloud_config,
    get_remote_metadata,
    is_cloud_storage_configured,
    upload_database,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

logger = logging.getLogger("sync_db")


def format_size(bytes_val: int) -> str:
    """Formate une taille en octets en Mo lisible."""
    return f"{bytes_val / (1024 * 1024):.2f} Mo"


def show_status() -> None:
    """Affiche un diagnostic clair de l'état local vs distant."""
    print("\n" + "=" * 60)
    print(" STATUT DE SYNCHRONISATION CLOUD (R2 / S3)")
    print("=" * 60)

    cfg = get_cloud_config()
    configured = is_cloud_storage_configured()

    print(f"* Configuration active : {'[ACTIF]' if configured else '[INACTIF]'}")
    if not configured:
        print("  [!] Variables manquantes : renseignez R2_ACCOUNT_ID (ou S3_ENDPOINT_URL),")
        print("      R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, et R2_BUCKET_NAME.")
        print("=" * 60 + "\n")
        return

    print(f"* Endpoint : {cfg['endpoint_url']}")
    print(f"* Bucket   : {cfg['bucket_name']}")
    print(f"* Objet    : {cfg['remote_key']}")

    print("\n[ETAT LOCAL]")
    if DEFAULT_LOCAL_DB.exists():
        stat = DEFAULT_LOCAL_DB.stat()
        local_dt = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        print(f"  - Fichier : {DEFAULT_LOCAL_DB}")
        print(f"  - Taille  : {format_size(stat.st_size)}")
        print(f"  - Modifié : {local_dt.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    else:
        print("  - Fichier : Introuvable (aucune base locale)")

    print("\n[ETAT DISTANT]")
    meta = get_remote_metadata()
    if meta:
        print(f"  - Taille  : {format_size(meta['size_bytes'])}")
        remote_dt = meta["last_modified"]
        if remote_dt:
            print(f"  - Modifié : {remote_dt.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print(f"  - ETag    : {meta['etag']}")
    else:
        print("  - Objet distant introuvable ou inaccessible.")

    print("=" * 60 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Synchronisation de la base SQLite avec le Cloud")
    parser.add_argument("--pull", "-p", action="store_true", help="Télécharge la base depuis le bucket vers le local")
    parser.add_argument("--push", "-u", action="store_true", help="Téléverse la base locale vers le bucket distant")
    parser.add_argument("--force", "-f", action="store_true", help="Force le téléchargement même si local plus récent")
    parser.add_argument("--status", "-s", action="store_true", help="Affiche l'état de synchronisation local vs cloud")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.status or (not args.pull and not args.push):
        show_status()
        if not args.pull and not args.push:
            return

    if args.pull:
        print(">> Téléchargement de la base distante...")
        ok = download_database(force=args.force)
        if ok:
            print("[OK] Base locale mise à jour avec succès !")
        else:
            print("[INFO] Aucune mise à jour nécessaire ou téléchargement ignoré.")

    if args.push:
        print(">> Téléversement de la base locale vers le cloud...")
        ok = upload_database()
        if ok:
            print("[OK] Base distante synchronisée avec succès !")
        else:
            print("[ERREUR] Échec de l'envoi de la base.")
            sys.exit(1)


if __name__ == "__main__":
    main()

