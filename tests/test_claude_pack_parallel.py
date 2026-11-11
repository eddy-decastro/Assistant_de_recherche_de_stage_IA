"""Pack Claude : préparation des dossiers en parallèle (sans appel réseau ni vraie base)."""
from __future__ import annotations

import time

import pytest

from src.matching import claude_pack


class _FakeDb:
    def __init__(self) -> None:
        self.saved: dict[str, str] = {}

    def update_cover_letter(self, job_id: str, letter: str, **_: object) -> None:
        self.saved[job_id] = letter


def _fake_prepare(job, **kwargs):
    assert kwargs["db"] is None  # aucune écriture SQLite depuis les fils
    time.sleep(0.2 if job["id"] == "a" else 0.0)  # « a » finit après « b »
    if job["id"] == "boom":
        raise RuntimeError("Gemini indisponible")
    return {"id": job["id"], "cover_letter": job.get("cover_letter") or f"lettre {job['id']}"}


def test_order_kept_and_new_letters_saved(monkeypatch) -> None:
    monkeypatch.setattr(claude_pack, "prepare_job_application_bundle", _fake_prepare)
    db = _FakeDb()
    jobs = [{"id": "a"}, {"id": "b", "cover_letter": "déjà écrite"}]
    out = claude_pack._prepare_bundles_parallel(
        jobs, db=db, config={"llm": {"cover_letter_concurrency": 2}}, output_dir=None,
        generate_missing_letters=True, export_pdf=False,
    )
    assert [b["id"] for b in out] == ["a", "b"]
    assert db.saved == {"a": "lettre a"}  # la lettre existante n'est pas réécrite


def test_failure_raises_after_saving_successes(monkeypatch) -> None:
    monkeypatch.setattr(claude_pack, "prepare_job_application_bundle", _fake_prepare)
    db = _FakeDb()
    with pytest.raises(RuntimeError, match="Gemini indisponible"):
        claude_pack._prepare_bundles_parallel(
            [{"id": "a", "company": "A"}, {"id": "boom", "company": "B"}], db=db, config={},
            output_dir=None, generate_missing_letters=True, export_pdf=False,
        )
    assert db.saved == {"a": "lettre a"}
