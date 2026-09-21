"""Tests unitaires du module de stockage cloud (src.storage.cloud_storage)."""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.storage.cloud_storage import (
    checkpoint_sqlite_wal,
    download_database,
    get_cloud_config,
    is_cloud_storage_configured,
    upload_database,
)


def test_cloud_config_defaults() -> None:
    with patch("src.storage.cloud_storage._load_env"):
        with patch.dict(os.environ, {}, clear=True):
            assert not is_cloud_storage_configured()
            cfg = get_cloud_config()
            assert cfg["endpoint_url"] == ""
            assert cfg["bucket_name"] == "stage-copilot"


def test_cloud_config_r2() -> None:
    env = {
        "R2_ACCOUNT_ID": "abc123def456",
        "R2_ACCESS_KEY_ID": "key_id",
        "R2_SECRET_ACCESS_KEY": "secret_key",
        "R2_BUCKET_NAME": "my-bucket",
    }
    with patch.dict(os.environ, env, clear=True):
        assert is_cloud_storage_configured()
        cfg = get_cloud_config()
        assert cfg["endpoint_url"] == "https://abc123def456.r2.cloudflarestorage.com"
        assert cfg["access_key"] == "key_id"
        assert cfg["secret_key"] == "secret_key"
        assert cfg["bucket_name"] == "my-bucket"


def test_checkpoint_sqlite_wal(tmp_path: Path) -> None:
    db_file = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE dummy (id INTEGER PRIMARY KEY, val TEXT)")
    conn.execute("INSERT INTO dummy (val) VALUES ('hello')")
    conn.commit()
    conn.close()

    # Le checkpoint ne doit lever aucune exception
    checkpoint_sqlite_wal(db_file)
    assert db_file.exists()


def test_download_database_unconfigured() -> None:
    with patch("src.storage.cloud_storage._load_env"):
        with patch.dict(os.environ, {}, clear=True):
            assert download_database() is False


def test_upload_database_unconfigured() -> None:
    with patch("src.storage.cloud_storage._load_env"):
        with patch.dict(os.environ, {}, clear=True):
            assert upload_database() is False


def test_download_database_mocked(tmp_path: Path) -> None:
    env = {
        "R2_ACCOUNT_ID": "abc123",
        "R2_ACCESS_KEY_ID": "key",
        "R2_SECRET_ACCESS_KEY": "secret",
        "R2_BUCKET_NAME": "test-bucket",
    }
    target_db = tmp_path / "target.db"

    mock_s3 = MagicMock()
    # Simuler download_file en créant le fichier temporaire
    def mock_download(bucket: str, key: str, filename: str) -> None:
        Path(filename).write_text("sqlite-mock-content", encoding="utf-8")

    mock_s3.download_file.side_effect = mock_download

    mock_meta = {
        "size_bytes": 100,
        "last_modified": datetime.now(timezone.utc),
        "etag": "etag123",
    }

    with patch.dict(os.environ, env, clear=True):
        with patch("src.storage.cloud_storage.get_s3_client", return_value=mock_s3):
            with patch("src.storage.cloud_storage.get_remote_metadata", return_value=mock_meta):
                res = download_database(target_path=target_db, force=True)
                assert res is True
                assert target_db.exists()
                assert target_db.read_text(encoding="utf-8") == "sqlite-mock-content"


def test_upload_database_mocked(tmp_path: Path) -> None:
    env = {
        "R2_ACCOUNT_ID": "abc123",
        "R2_ACCESS_KEY_ID": "key",
        "R2_SECRET_ACCESS_KEY": "secret",
        "R2_BUCKET_NAME": "test-bucket",
    }
    src_db = tmp_path / "src.db"
    src_db.write_text("sqlite-content", encoding="utf-8")

    mock_s3 = MagicMock()

    with patch.dict(os.environ, env, clear=True):
        with patch("src.storage.cloud_storage.get_s3_client", return_value=mock_s3):
            res = upload_database(db_path=src_db)
            assert res is True
            mock_s3.upload_file.assert_called_once()
