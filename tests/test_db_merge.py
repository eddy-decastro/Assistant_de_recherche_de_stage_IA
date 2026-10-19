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


def test_database_user_columns_and_rerank_evidence(tmp_path: Path) -> None:
    from src.storage.database import Database

    db_path = tmp_path / "test_user_cols.db"
    db = Database(db_path)
    job_data = {
        "id": "job-1",
        "title": "Stage Python",
        "company": "Tech Corp",
        "url": "https://example.com/job-1",
    }
    db.upsert_job(job_data)

    # Test update_cover_letter
    assert db.update_cover_letter("job-1", "Ma super lettre de motivation") is True
    job = db.get_job("job-1")
    assert job is not None
    assert job["cover_letter"] == "Ma super lettre de motivation"
    assert db.update_cover_letter("non-existent", "Lettre") is False

    # Test update_user_notes
    assert db.update_user_notes("job-1", "Contacter le recruteur sur LinkedIn") is True
    job = db.get_job("job-1")
    assert job is not None
    assert job["user_notes"] == "Contacter le recruteur sur LinkedIn"
    assert db.update_user_notes("non-existent", "Notes") is False

    # Test update_rerank avec evidence_json et questions_entretien
    assert db.update_rerank(
        "job-1",
        rerank_score=85.5,
        verdict="OUI",
        evidence_json={"profil": "Bac+5 recherché", "mission": "IA et NLP"},
        questions_entretien=["Parlez-moi de votre expérience NLP", "Connaissez-vous PyTorch ?"],
    ) is True

    job = db.get_job("job-1")
    assert job is not None
    assert job["rerank_score"] == 85.5
    assert job["verdict"] == "OUI"
    assert job["evidence"] == {"profil": "Bac+5 recherché", "mission": "IA et NLP"}
    assert job["questions_entretien"] == [
        "Parlez-moi de votre expérience NLP",
        "Connaissez-vous PyTorch ?",
    ]
    assert job["cover_letter"] == "Ma super lettre de motivation"
    assert job["user_notes"] == "Contacter le recruteur sur LinkedIn"
    assert "evidence_json" not in job
    assert "questions_json" not in job

    # Test clear_rerank resets evidence_json and questions_json, but leaves cover_letter and user_notes intact
    cleared = db.clear_rerank(limit=10)
    assert "job-1" in cleared
    job_after = db.get_job("job-1")
    assert job_after is not None
    assert job_after["rerank_score"] is None
    assert job_after["evidence"] == {}
    assert job_after["questions_entretien"] == []
    assert job_after["cover_letter"] == "Ma super lettre de motivation"
    assert job_after["user_notes"] == "Contacter le recruteur sur LinkedIn"

    # Test clear_rerank_scores
    db.update_rerank(
        "job-1",
        rerank_score=92.0,
        evidence_json={"profil": "Master"},
        questions_entretien=["Question 1"],
    )
    count_cleared = db.clear_rerank_scores()
    assert count_cleared == 1
    job_after_all = db.get_job("job-1")
    assert job_after_all is not None
    assert job_after_all["rerank_score"] is None
    assert job_after_all["evidence"] == {}
    assert job_after_all["cover_letter"] == "Ma super lettre de motivation"


def test_merge_preserves_cover_letter_and_user_notes(tmp_path: Path) -> None:
    from src.storage.db_merge import USER_COLUMNS, merge_sqlite_files

    local_db = tmp_path / "local.db"
    remote_db = tmp_path / "remote.db"
    base_db = tmp_path / "base.db"

    schema_extended = """
    CREATE TABLE jobs (
        id TEXT PRIMARY KEY,
        title TEXT,
        status TEXT,
        applied_at TEXT,
        rejection_reason TEXT,
        rerank_score REAL,
        cover_letter TEXT,
        user_notes TEXT
    );
    """
    for p in (base_db, local_db, remote_db):
        conn = sqlite3.connect(p)
        conn.executescript(schema_extended)
        conn.close()

    # Base commune : job-1 et job-2 sans lettre ni notes
    conn = sqlite3.connect(base_db)
    conn.execute(
        "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("job-1", "Stage 1", "NOUVEAU", None, None, 50.0, None, None),
    )
    conn.execute(
        "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("job-2", "Stage 2", "NOUVEAU", None, None, 60.0, None, None),
    )
    conn.commit()
    conn.close()

    # Local : l'utilisateur a rédigé cover_letter et user_notes sur job-1 (remote est NULL)
    shutil.copyfile(base_db, local_db)
    conn = sqlite3.connect(local_db)
    conn.execute(
        "UPDATE jobs SET cover_letter = 'Lettre locale', user_notes = 'Note locale' WHERE id = 'job-1'"
    )
    conn.commit()
    conn.close()

    # Remote : l'utilisateur sur une autre instance a rédigé cover_letter et user_notes sur job-2 (local est NULL)
    shutil.copyfile(base_db, remote_db)
    conn = sqlite3.connect(remote_db)
    conn.execute(
        "UPDATE jobs SET cover_letter = 'Lettre distante', user_notes = 'Note distante' WHERE id = 'job-2'"
    )
    conn.commit()
    conn.close()

    # Fusion avec USER_COLUMNS
    stats = merge_sqlite_files(local_db, remote_db, base_db, prefer_remote=USER_COLUMNS)
    assert stats.updated >= 1

    conn = sqlite3.connect(local_db)
    rows = {
        r[0]: dict(zip(["cover_letter", "user_notes"], r[1:]))
        for r in conn.execute("SELECT id, cover_letter, user_notes FROM jobs")
    }
    conn.close()

    # Local job-1 : 'Lettre locale' et 'Note locale' préservées (remote inchangé/NULL)
    assert rows["job-1"]["cover_letter"] == "Lettre locale"
    assert rows["job-1"]["user_notes"] == "Note locale"

    # Local job-2 : 'Lettre distante' et 'Note distante' intégrées (local inchangé/NULL)
    assert rows["job-2"]["cover_letter"] == "Lettre distante"
    assert rows["job-2"]["user_notes"] == "Note distante"
