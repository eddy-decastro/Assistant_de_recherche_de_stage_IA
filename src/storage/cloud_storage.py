"""Client de persistance et synchronisation Cloud (Cloudflare R2 / AWS S3).

Permet de synchroniser la base SQLite locale (``data/stage_copilot.db``) avec un
bucket de stockage d'objets compatible S3 (Cloudflare R2, AWS S3, etc.).
Garantit que les modifications d'état (statut Kanban « Postulé », notes, archivages)
effectuées depuis le dashboard hébergé sur Render ne sont jamais perdues lors des
redémarrages ou mises en veille du conteneur.
"""
from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config import PROJECT_ROOT

logger = logging.getLogger("src.storage.cloud_storage")

DEFAULT_LOCAL_DB = PROJECT_ROOT / "data" / "stage_copilot.db"
DEFAULT_REMOTE_KEY = "stage_copilot.db"

# Verrou pour éviter les téléversements/téléchargements concurrents
_SYNC_LOCK = threading.Lock()
_LAST_UPLOAD_TIMESTAMP: float = 0.0
_DEBOUNCE_DELAY_SECONDS: float = 5.0
_PENDING_TIMER: threading.Timer | None = None


def _load_env() -> None:
    """Charge les variables du fichier .env si présent (sans écraser os.environ)."""
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_file)
        except Exception:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip() not in os.environ:
                        os.environ[k.strip()] = v.strip()


def get_cloud_config() -> dict[str, str]:
    """Extrait et normalise les paramètres de connexion S3/Cloudflare R2."""
    _load_env()
    account_id = os.getenv("R2_ACCOUNT_ID", "").strip()
    endpoint_url = os.getenv("S3_ENDPOINT_URL", "").strip()
    if account_id and not endpoint_url:
        endpoint_url = f"https://{account_id}.r2.cloudflarestorage.com"

    access_key = (
        os.getenv("R2_ACCESS_KEY_ID")
        or os.getenv("AWS_ACCESS_KEY_ID")
        or ""
    ).strip()
    secret_key = (
        os.getenv("R2_SECRET_ACCESS_KEY")
        or os.getenv("AWS_SECRET_ACCESS_KEY")
        or ""
    ).strip()
    bucket_name = (
        os.getenv("R2_BUCKET_NAME")
        or os.getenv("S3_BUCKET_NAME")
        or "stage-copilot"
    ).strip()
    region_name = (
        os.getenv("R2_REGION_NAME")
        or os.getenv("AWS_REGION")
        or "auto"
    ).strip()
    remote_key = os.getenv("DB_REMOTE_KEY", DEFAULT_REMOTE_KEY).strip()

    return {
        "endpoint_url": endpoint_url,
        "access_key": access_key,
        "secret_key": secret_key,
        "bucket_name": bucket_name,
        "region_name": region_name,
        "remote_key": remote_key,
    }


def is_cloud_storage_configured() -> bool:
    """Indique si les variables nécessaires au stockage distant sont renseignées."""
    cfg = get_cloud_config()
    return bool(cfg["endpoint_url"] and cfg["access_key"] and cfg["secret_key"] and cfg["bucket_name"])


def get_s3_client() -> Any:
    """Instancie un client boto3 S3 configuré pour R2/S3."""
    import boto3
    from botocore.config import Config

    cfg = get_cloud_config()
    if not is_cloud_storage_configured():
        raise RuntimeError("Stockage distant non configuré (variables R2/S3 manquantes).")

    session = boto3.session.Session()
    client = session.client(
        service_name="s3",
        endpoint_url=cfg["endpoint_url"],
        aws_access_key_id=cfg["access_key"],
        aws_secret_access_key=cfg["secret_key"],
        region_name=cfg["region_name"],
        config=Config(
            signature_version="s3v4",
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=10,
            read_timeout=30,
        ),
    )
    return client


def checkpoint_sqlite_wal(db_path: Path) -> None:
    """Applique un checkpoint TRUNCATE sur la base SQLite pour intégrer le WAL dans le .db."""
    if not db_path.exists():
        return
    try:
        conn = sqlite3.connect(str(db_path), timeout=10.0)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()
        logger.debug("WAL checkpoint effectué avec succès sur %s", db_path)
    except Exception as exc:
        logger.warning("Échec checkpoint WAL sur %s (non bloquant) : %s", db_path, exc)


def get_remote_metadata() -> dict[str, Any] | None:
    """Récupère les métadonnées de la base stockée sur le bucket (taille, date)."""
    if not is_cloud_storage_configured():
        return None
    cfg = get_cloud_config()
    try:
        s3 = get_s3_client()
        resp = s3.head_object(Bucket=cfg["bucket_name"], Key=cfg["remote_key"])
        return {
            "size_bytes": resp.get("ContentLength", 0),
            "last_modified": resp.get("LastModified"),
            "etag": resp.get("ETag", "").strip('"'),
        }
    except Exception as exc:
        logger.debug("Objet distant non trouvé ou erreur de métadonnées : %s", exc)
        return None


def download_database(target_path: Path | str | None = None, force: bool = False) -> bool:
    """Télécharge la base depuis le bucket vers target_path de façon atomique.

    Args:
        target_path: Chemin du fichier local (défaut: data/stage_copilot.db).
        force: Si True, écrase la base locale même si elle semble plus récente.

    Returns:
        True si la base a été téléchargée/mise à jour, False sinon.
    """
    if not is_cloud_storage_configured():
        logger.info("Stockage distant non configuré : téléchargement ignoré.")
        return False

    dest = Path(target_path) if target_path else DEFAULT_LOCAL_DB
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp_dest = dest.with_suffix(".tmp_download")

    with _SYNC_LOCK:
        cfg = get_cloud_config()
        try:
            meta = get_remote_metadata()
            if not meta:
                logger.info("Aucune base distante trouvée dans le bucket %s.", cfg["bucket_name"])
                return False

            remote_time = meta["last_modified"]
            if not force and dest.exists() and remote_time:
                local_mtime = datetime.fromtimestamp(dest.stat().st_mtime, tz=timezone.utc)
                if local_mtime > remote_time:
                    logger.info(
                        "Base locale (%s) plus récente que distante (%s) : téléchargement évité.",
                        local_mtime,
                        remote_time,
                    )
                    return False

            s3 = get_s3_client()
            logger.info("Téléchargement de %s depuis le bucket %s...", cfg["remote_key"], cfg["bucket_name"])
            s3.download_file(cfg["bucket_name"], cfg["remote_key"], str(temp_dest))

            # Remplacement atomique
            if temp_dest.exists():
                shutil.move(str(temp_dest), str(dest))
                # Supprimer les reliquats de WAL/SHM locaux pour éviter toute désynchronisation
                for suffix in ("-wal", "-shm"):
                    extra = dest.with_name(dest.name + suffix)
                    if extra.exists():
                        extra.unlink()
                logger.info("Base distante synchronisée avec succès vers %s (%.2f Mo).", dest, dest.stat().st_size / (1024 * 1024))
                return True
        except Exception as exc:
            logger.error("Erreur lors du téléchargement de la base distante : %s", exc)
            if temp_dest.exists():
                temp_dest.unlink()
            return False

    return False


def upload_database(db_path: Path | str | None = None) -> bool:
    """Téléverse la base SQLite locale vers le bucket distant après checkpoint WAL.

    Returns:
        True si le téléversement a réussi, False sinon.
    """
    global _LAST_UPLOAD_TIMESTAMP
    if not is_cloud_storage_configured():
        logger.info("Stockage distant non configuré : téléversement ignoré.")
        return False

    src = Path(db_path) if db_path else DEFAULT_LOCAL_DB
    if not src.exists():
        logger.warning("Fichier SQLite introuvable pour upload : %s", src)
        return False

    with _SYNC_LOCK:
        checkpoint_sqlite_wal(src)
        cfg = get_cloud_config()
        try:
            s3 = get_s3_client()
            size_mb = src.stat().st_size / (1024 * 1024)
            logger.info("Téléversement de %s vers %s/%s (%.2f Mo)...", src.name, cfg["bucket_name"], cfg["remote_key"], size_mb)
            s3.upload_file(
                Filename=str(src),
                Bucket=cfg["bucket_name"],
                Key=cfg["remote_key"],
                ExtraArgs={"ContentType": "application/x-sqlite3"},
            )
            _LAST_UPLOAD_TIMESTAMP = time.time()
            logger.info("Téléversement réussi !")
            return True
        except Exception as exc:
            logger.error("Échec du téléversement de la base vers le stockage distant : %s", exc)
            return False


def trigger_debounced_upload(db_path: Path | str | None = None) -> None:
    """Déclenche un téléversement en arrière-plan avec debounce pour regrouper les écritures rapprochées."""
    global _PENDING_TIMER
    if not is_cloud_storage_configured():
        return

    def _task() -> None:
        upload_database(db_path)

    with _SYNC_LOCK:
        if _PENDING_TIMER and _PENDING_TIMER.is_alive():
            _PENDING_TIMER.cancel()
        _PENDING_TIMER = threading.Timer(_DEBOUNCE_DELAY_SECONDS, _task)
        _PENDING_TIMER.daemon = True
        _PENDING_TIMER.start()

