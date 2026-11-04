"""Garde-fou de tests/conftest.py : aucune ``Database`` ouverte sur la vraie base.

Le test ne construit jamais de ``Database`` sur data/stage_copilot.db : si le
garde-fou était cassé, il y écrirait. Il vérifie seulement la détection des
chemins et que le constructeur est bien intercepté.
"""
from __future__ import annotations

from pathlib import Path

from conftest import PROJECT_ROOT, is_real_db_path
from src.storage.database import Database


def test_chemins_de_la_vraie_base_detectes(tmp_path: Path) -> None:
    assert is_real_db_path("data/stage_copilot.db")
    assert is_real_db_path(PROJECT_ROOT / "data" / "jobs.db")
    assert not is_real_db_path(tmp_path / "stage_copilot.db")
    assert not is_real_db_path(PROJECT_ROOT / "data" / "backups" / "stage_copilot.db")


def test_constructeur_de_database_intercepte() -> None:
    assert Database.__init__.__name__ == "guarded_init"
