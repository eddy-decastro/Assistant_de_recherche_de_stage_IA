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
    _read_sync_etag,
    _record_sync_point,
    backup_remote_database,
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
    missing = Exception("absent")
    missing.response = {"Error": {"Code": "404"}}
    mock_s3.head_object.side_effect = missing
    mock_s3.put_object.return_value = {"ETag": '"new-etag"'}

    with patch.dict(os.environ, env, clear=True):
        with patch("src.storage.cloud_storage.get_s3_client", return_value=mock_s3):
            res = upload_database(db_path=src_db)
            assert res is True
            mock_s3.put_object.assert_called_once()
            assert mock_s3.put_object.call_args.kwargs["IfNoneMatch"] == "*"


def test_download_database_etag_skip(tmp_path: Path) -> None:
    env = {
        "R2_ACCOUNT_ID": "abc123",
        "R2_ACCESS_KEY_ID": "key",
        "R2_SECRET_ACCESS_KEY": "secret",
        "R2_BUCKET_NAME": "test-bucket",
    }
    target_db = tmp_path / "target.db"
    target_db.write_text("existing-content", encoding="utf-8")
    _record_sync_point(target_db, "matching_etag")

    mock_meta = {
        "size_bytes": 100,
        "last_modified": datetime.now(timezone.utc),
        "etag": "matching_etag",
    }

    with patch.dict(os.environ, env, clear=True):
        with patch("src.storage.cloud_storage.get_s3_client"):
            with patch("src.storage.cloud_storage.get_remote_metadata", return_value=mock_meta):
                res = download_database(target_path=target_db, force=False)
                assert res is False
                assert target_db.read_text(encoding="utf-8") == "existing-content"


def test_download_database_etag_merge(tmp_path: Path) -> None:
    env = {
        "R2_ACCOUNT_ID": "abc123",
        "R2_ACCESS_KEY_ID": "key",
        "R2_SECRET_ACCESS_KEY": "secret",
        "R2_BUCKET_NAME": "test-bucket",
    }
    target_db = tmp_path / "target.db"
    target_db.write_text("local-content", encoding="utf-8")
    _record_sync_point(target_db, "old_etag")

    mock_meta = {
        "size_bytes": 200,
        "last_modified": datetime.now(timezone.utc),
        "etag": "new_remote_etag",
    }

    mock_s3 = MagicMock()

    with patch.dict(os.environ, env, clear=True):
        with patch("src.storage.cloud_storage.get_s3_client", return_value=mock_s3):
            with patch("src.storage.cloud_storage.get_remote_metadata", return_value=mock_meta):
                with patch(
                    "src.storage.cloud_storage._merge_from_remote",
                    return_value="new_remote_etag",
                ) as mock_merge:
                    res = download_database(target_path=target_db, force=False)
                    assert res is True
                    mock_merge.assert_called_once()
                    assert _read_sync_etag(target_db) == "new_remote_etag"


def test_backup_remote_database_unconfigured() -> None:
    with patch("src.storage.cloud_storage._load_env"):
        with patch.dict(os.environ, {}, clear=True):
            assert backup_remote_database() is False


def test_backup_remote_database_mocked() -> None:
    env = {
        "R2_ACCOUNT_ID": "abc123",
        "R2_ACCESS_KEY_ID": "key",
        "R2_SECRET_ACCESS_KEY": "secret",
        "R2_BUCKET_NAME": "test-bucket",
    }

    mock_s3 = MagicMock()
    # Cas 1 : base distante inexistante
    missing = Exception("404")
    missing.response = {"Error": {"Code": "404"}}
    mock_s3.head_object.side_effect = missing

    with patch.dict(os.environ, env, clear=True):
        with patch("src.storage.cloud_storage.get_s3_client", return_value=mock_s3):
            assert backup_remote_database() is False

    # Cas 2 : base distante existante, rotation max_backups = 2
    mock_s3.reset_mock()
    mock_s3.head_object.side_effect = None
    mock_s3.head_object.return_value = {"ContentLength": 5000, "ETag": '"etag123"'}
    mock_s3.list_objects_v2.return_value = {
        "Contents": [
            {"Key": "backups/stage_copilot_2026-10-10.db"},
            {"Key": "backups/stage_copilot_2026-10-11.db"},
            {"Key": "backups/stage_copilot_2026-10-12.db"},
        ]
    }

    with patch.dict(os.environ, env, clear=True):
        with patch("src.storage.cloud_storage.get_s3_client", return_value=mock_s3):
            res = backup_remote_database(max_backups=2)
            assert res is True
            mock_s3.copy_object.assert_called_once()
            call_kwargs = mock_s3.copy_object.call_args.kwargs
            assert call_kwargs["Bucket"] == "test-bucket"
            assert call_kwargs["CopySource"] == {"Bucket": "test-bucket", "Key": "stage_copilot.db"}
            assert call_kwargs["Key"].startswith("backups/stage_copilot_")

            # 3 sauvegardes présentes, max_backups=2 -> la plus ancienne (2026-10-10) doit être supprimée
            mock_s3.delete_object.assert_called_once_with(
                Bucket="test-bucket",
                Key="backups/stage_copilot_2026-10-10.db",
            )
