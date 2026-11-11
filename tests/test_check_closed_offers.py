"""Vérification en masse des offres fermées : sans réseau (sonde simulée), base dans tmp_path."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.constants import (  # noqa: E402
    AUTO_ARCHIVE_REASON,
    CLOSED_OFFER_REASON,
    STATUS_APPLIED,
    STATUS_IGNORED,
    STATUS_NEW,
)
from src.storage.database import Database  # noqa: E402
from tools import check_closed_offers as cc  # noqa: E402


@dataclass
class _Verdict:
    status: str
    reason: str = ""

    @property
    def is_closed(self) -> bool:
        return self.status == "ferme"


class _FakeChecker:
    def __init__(self, verdicts: dict[str, str]) -> None:
        self.verdicts = verdicts
        self.seen: list[str] = []

    def check(self, url: str, source: str | None = None) -> _Verdict:
        self.seen.append(url)
        return _Verdict(self.verdicts.get(url, "inconnu"), "test")

    def close(self) -> None:
        pass


def _add(db: Database, key: str, status: str, days: int, reason: str | None = None) -> str:
    db.upsert_job({
        "title": f"Stage ML {key}", "company": f"Acme {key}", "location": "Paris",
        "url": f"https://exemple.fr/{key}", "description": "Stage de recherche.",
        "source": "linkedin", "status": status, "rejection_reason": reason,
        "published_at": datetime.now() - timedelta(days=days),
    })
    return next(j["id"] for j in db.get_jobs(limit=100) if j["url"].endswith(f"/{key}"))


def test_archive_les_vieilles_fermees_sans_toucher_aux_decisions(tmp_path: Path) -> None:
    db_path = tmp_path / "b.db"
    db = Database(str(db_path))
    old_closed = _add(db, "vieille_fermee", STATUS_NEW, 20)
    old_open = _add(db, "vieille_ouverte", STATUS_NEW, 20)
    recent = _add(db, "recente", STATUS_NEW, 2)
    applied = _add(db, "postulee", STATUS_APPLIED, 30)
    archived = _add(db, "archivee", STATUS_IGNORED, 40, AUTO_ARCHIVE_REASON)
    checker = _FakeChecker({
        "https://exemple.fr/vieille_fermee": "ferme",
        "https://exemple.fr/vieille_ouverte": "ouvert",
        "https://exemple.fr/postulee": "ferme",
        "https://exemple.fr/archivee": "ouvert",
    })

    stats = cc.run(True, db_path=db_path, reopen=True, checker=checker)

    db = Database(str(db_path))
    assert stats["fermees"] == 1 and stats["rouvertes"] == 1
    closed = db.get_job(old_closed)
    assert closed["status"] == STATUS_IGNORED and closed["rejection_reason"] == CLOSED_OFFER_REASON
    assert db.get_job(old_open)["status"] == STATUS_NEW
    assert db.get_job(recent)["status"] == STATUS_NEW
    assert "https://exemple.fr/recente" not in checker.seen  # trop récente : pas sondée
    assert db.get_job(applied)["status"] == STATUS_APPLIED  # décision d'Eddy : jamais sondée
    assert db.get_job(archived)["status"] == STATUS_NEW
    assert list((tmp_path / "backups").glob("*.db"))


def test_essai_a_blanc_n_ecrit_rien(tmp_path: Path) -> None:
    db_path = tmp_path / "b.db"
    db = Database(str(db_path))
    job_id = _add(db, "vieille", STATUS_NEW, 20)
    cc.run(False, db_path=db_path, checker=_FakeChecker({"https://exemple.fr/vieille": "ferme"}))
    assert Database(str(db_path)).get_job(job_id)["status"] == STATUS_NEW
