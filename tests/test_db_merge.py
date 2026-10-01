"""Fusion à trois points des copies SQLite et téléversement conditionnel vers R2."""
from __future__ import annotations

import io
import os
import shutil
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.storage import cloud_storage  # noqa: E402
from src.storage.db_merge import USER_COLUMNS, merge_remote_changes  # noqa: E402

SCHEMA = """
CREATE TABLE jobs (id TEXT PRIMARY KEY, title TEXT, status TEXT, applied_at TEXT,
                   rejection_reason TEXT, rerank_score REAL);
CREATE TABLE scrape_runs (id TEXT PRIMARY KEY, total_inserted INTEGER);
"""


def make_db(path: Path, jobs: list[tuple], runs: list[tuple] = ()) -> Path:
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.executemany("INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?)", jobs)
    conn.executemany("INSERT INTO scrape_runs VALUES (?, ?)", runs)
    conn.commit()
    conn.close()
    return path


def jobs_of(path: Path) -> dict[str, tuple]:
    conn = sqlite3.connect(path)
    rows = {r[0]: r[1:] for r in conn.execute("SELECT * FROM jobs")}
    conn.close()
    return rows


def execute(path: Path, sql: str, *params) -> None:
    conn = sqlite3.connect(path)
    conn.execute(sql, params)
    conn.commit()
    conn.close()


BASE_JOBS = [
    ("a", "Stage A", "NOUVEAU", None, None, 50.0),
    ("b", "Stage B", "NOUVEAU", None, None, 40.0),
]


def three_copies(tmp_path: Path) -> tuple[Path, Path, Path]:
    base = make_db(tmp_path / "base.db", BASE_JOBS)
    local = tmp_path / "local.db"
    remote = tmp_path / "remote.db"
    shutil.copyfile(base, local)
    shutil.copyfile(base, remote)
    return local, remote, base


def test_ligne_ajoutee_a_distance_est_inseree(tmp_path: Path) -> None:
    local, remote, base = three_copies(tmp_path)
    execute(remote, "INSERT INTO jobs VALUES ('c', 'Stage C', 'NOUVEAU', NULL, NULL, 70.0)")
    stats = merge_remote_changes(local, remote, base)
    assert stats.inserted == 1
    assert jobs_of(local)["c"][0] == "Stage C"


def test_changements_independants_sont_combines(tmp_path: Path) -> None:
    local, remote, base = three_copies(tmp_path)
    execute(local, "UPDATE jobs SET rerank_score = 90 WHERE id = 'a'")  # le cron note
    execute(remote, "UPDATE jobs SET status = 'POSTULÉ', applied_at = '2026-10-01' WHERE id = 'a'")  # l'app
    merge_remote_changes(local, remote, base)
    assert jobs_of(local)["a"] == ("Stage A", "POSTULÉ", "2026-10-01", None, 90.0)


def test_conflit_local_gagne_par_defaut(tmp_path: Path) -> None:
    local, remote, base = three_copies(tmp_path)
    execute(local, "UPDATE jobs SET status = 'IGNORÉ' WHERE id = 'a'")
    execute(remote, "UPDATE jobs SET status = 'POSTULÉ' WHERE id = 'a'")
    stats = merge_remote_changes(local, remote, base)
    assert stats.conflicts == 1
    assert jobs_of(local)["a"][1] == "IGNORÉ"


def test_conflit_sur_colonne_utilisateur_le_distant_gagne_pour_le_pipeline(tmp_path: Path) -> None:
    local, remote, base = three_copies(tmp_path)
    execute(local, "UPDATE jobs SET status = 'REJETÉ', rejection_reason = 'revalidation' WHERE id = 'a'")
    execute(remote, "UPDATE jobs SET status = 'POSTULÉ' WHERE id = 'a'")
    merge_remote_changes(local, remote, base, prefer_remote=USER_COLUMNS)
    assert jobs_of(local)["a"][1] == "POSTULÉ"


def test_suppressions(tmp_path: Path) -> None:
    local, remote, base = three_copies(tmp_path)
    execute(remote, "DELETE FROM jobs WHERE id = 'a'")  # distant supprime une ligne intacte
    execute(remote, "DELETE FROM jobs WHERE id = 'b'")  # ...et une ligne modifiée localement
    execute(local, "UPDATE jobs SET rerank_score = 10 WHERE id = 'b'")
    stats = merge_remote_changes(local, remote, base)
    assert stats.deleted == 1
    assert set(jobs_of(local)) == {"b"}


def test_ligne_supprimee_localement_reste_supprimee(tmp_path: Path) -> None:
    local, remote, base = three_copies(tmp_path)
    execute(local, "DELETE FROM jobs WHERE id = 'a'")  # dédoublonnage du cron
    execute(remote, "UPDATE jobs SET rerank_score = 99 WHERE id = 'a'")
    merge_remote_changes(local, remote, base)
    assert "a" not in jobs_of(local)


def test_sans_base_union_des_copies(tmp_path: Path) -> None:
    local = make_db(tmp_path / "local.db", [("a", "Stage A", "NOUVEAU", None, None, 80.0)])
    remote = make_db(tmp_path / "remote.db", [
        ("a", "Stage A", "POSTULÉ", "2026-10-01", None, 50.0),
        ("z", "Stage Z", "NOUVEAU", None, None, 30.0),
    ])
    merge_remote_changes(local, remote, None, prefer_remote=USER_COLUMNS)
    rows = jobs_of(local)
    assert rows["a"] == ("Stage A", "POSTULÉ", "2026-10-01", None, 80.0)
    assert rows["z"][0] == "Stage Z"


# --------------------------------------------------------------------------- #
# Téléversement : faux bucket R2 en mémoire, avec ETag et If-Match
# --------------------------------------------------------------------------- #
class FakeClientError(Exception):
    def __init__(self, code: str, status: int) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}


class FakeR2:
    def __init__(self) -> None:
        self.data: bytes | None = None
        self.version = 0

    @property
    def etag(self) -> str:
        return f'"v{self.version}"'

    def head_object(self, Bucket: str, Key: str) -> dict:
        if self.data is None:
            raise FakeClientError("404", 404)
        return {"ETag": self.etag, "ContentLength": len(self.data)}

    def get_object(self, Bucket: str, Key: str) -> dict:
        return {"Body": io.BytesIO(self.data), "ETag": self.etag}

    def put_object(self, Bucket: str, Key: str, Body, IfMatch=None, IfNoneMatch=None, **_) -> dict:
        if IfMatch is not None and IfMatch != self.etag:
            raise FakeClientError("PreconditionFailed", 412)
        if IfNoneMatch == "*" and self.data is not None:
            raise FakeClientError("PreconditionFailed", 412)
        self.data = Body.read()
        self.version += 1
        return {"ETag": self.etag}

    def download_file(self, Bucket: str, Key: str, Filename: str) -> None:
        Path(Filename).write_bytes(self.data)

    def write_to(self, path: Path) -> None:
        path.write_bytes(self.data)


ENV = {
    "R2_ACCOUNT_ID": "abc",
    "R2_ACCESS_KEY_ID": "key",
    "R2_SECRET_ACCESS_KEY": "secret",
    "R2_BUCKET_NAME": "bucket",
}


def download(r2: FakeR2, path: Path) -> None:
    meta = {"size_bytes": len(r2.data), "last_modified": None, "etag": r2.etag.strip('"')}
    with patch("src.storage.cloud_storage.get_remote_metadata", return_value=meta):
        assert cloud_storage.download_database(target_path=path, force=True)


def test_cron_et_app_ne_s_ecrasent_plus(tmp_path: Path) -> None:
    r2 = FakeR2()
    seed = make_db(tmp_path / "seed.db", BASE_JOBS)
    r2.data = seed.read_bytes()
    r2.version = 1

    cron_db = tmp_path / "cron" / "stage.db"
    app_db = tmp_path / "app" / "stage.db"
    cron_db.parent.mkdir()
    app_db.parent.mkdir()

    with patch.dict(os.environ, ENV, clear=True), \
            patch("src.storage.cloud_storage.get_s3_client", return_value=r2):
        download(r2, cron_db)
        download(r2, app_db)

        # L'app marque une candidature et téléverse pendant le run du cron.
        execute(app_db, "UPDATE jobs SET status = 'POSTULÉ' WHERE id = 'a'")
        assert cloud_storage.upload_database(app_db)

        # Le cron insère une offre, en note une autre, puis téléverse.
        execute(cron_db, "INSERT INTO jobs VALUES ('c', 'Stage C', 'NOUVEAU', NULL, NULL, 70.0)")
        execute(cron_db, "UPDATE jobs SET rerank_score = 88 WHERE id = 'b'")
        assert cloud_storage.upload_database(cron_db, prefer_remote_columns=USER_COLUMNS)

        # L'app, restée sur sa vieille copie, change encore un statut et téléverse.
        execute(app_db, "UPDATE jobs SET status = 'IGNORÉ' WHERE id = 'b'")
        assert cloud_storage.upload_database(app_db)

    final = tmp_path / "final.db"
    r2.write_to(final)
    rows = jobs_of(final)
    assert rows["a"][1] == "POSTULÉ"  # statut de l'app conservé par le cron
    assert rows["b"][1] == "IGNORÉ" and rows["b"][4] == 88.0  # note du cron conservée par l'app
    assert "c" in rows  # offre du cron conservée par l'app
    assert jobs_of(app_db) == rows  # l'app voit désormais les offres du cron


def test_televersement_concurrent_refusionne(tmp_path: Path) -> None:
    r2 = FakeR2()
    r2.data = make_db(tmp_path / "seed.db", BASE_JOBS).read_bytes()
    r2.version = 1
    local = tmp_path / "local" / "stage.db"
    local.parent.mkdir()

    real_put = r2.put_object
    calls = {"n": 0}

    def racing_put(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:  # un autre client téléverse juste avant nous
            other = tmp_path / "other.db"
            r2.write_to(other)
            execute(other, "INSERT INTO jobs VALUES ('x', 'Stage X', 'NOUVEAU', NULL, NULL, 1.0)")
            r2.data = other.read_bytes()
            r2.version += 1
        return real_put(**kwargs)

    r2.put_object = racing_put
    with patch.dict(os.environ, ENV, clear=True), \
            patch("src.storage.cloud_storage.get_s3_client", return_value=r2):
        download(r2, local)
        execute(local, "UPDATE jobs SET rerank_score = 77 WHERE id = 'a'")
        assert cloud_storage.upload_database(local)

    assert calls["n"] == 2
    final = tmp_path / "final.db"
    r2.write_to(final)
    rows = jobs_of(final)
    assert "x" in rows and rows["a"][4] == 77.0
