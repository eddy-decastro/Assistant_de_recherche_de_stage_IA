"""Tests du profil candidat : CV et coordonnées résolus hors du dépôt git."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src import candidate as candidate_module  # noqa: E402
from src.candidate import (  # noqa: E402
    CANDIDATE_FIELDS,
    get_candidate_info,
    get_cv_text,
    load_local_candidate,
    save_local_candidate,
)

CONFIG = {
    "candidate": {
        "name": "Camille TESTEUR",
        "title": "Élève-ingénieure",
        "linkedin": "https://www.linkedin.com/in/camille-testeur/",
    },
}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch) -> Path:
    """Aucun fichier local ni secret CANDIDATE_* du développeur ; .env ignoré."""
    for field in CANDIDATE_FIELDS:
        monkeypatch.delenv(f"CANDIDATE_{field.upper()}", raising=False)
    monkeypatch.delenv("CANDIDATE_CV", raising=False)
    monkeypatch.setattr(candidate_module, "_load_env", lambda: None)
    local = tmp_path / "candidate.local.yaml"
    monkeypatch.setattr(candidate_module, "LOCAL_CANDIDATE_PATH", local)
    return local


def test_config_seule_laisse_les_coordonnees_privees_vides() -> None:
    info = get_candidate_info(CONFIG)
    assert info["name"] == "Camille TESTEUR"
    assert info["phone"] == ""
    assert info["email"] == ""
    assert set(info) == set(CANDIDATE_FIELDS)


def test_secrets_completent_la_config(monkeypatch) -> None:
    monkeypatch.setenv("CANDIDATE_PHONE", "06 00 00 00 00")
    monkeypatch.setenv("CANDIDATE_EMAIL", "camille@example.com")
    info = get_candidate_info(CONFIG)
    assert info["phone"] == "06 00 00 00 00"
    assert info["email"] == "camille@example.com"
    assert info["name"] == "Camille TESTEUR"


def test_fichier_local_prime_sur_les_secrets(monkeypatch, isolated: Path) -> None:
    monkeypatch.setenv("CANDIDATE_PHONE", "06 00 00 00 00")
    save_local_candidate({"phone": "07 11 11 11 11", "name": ""}, isolated)
    info = get_candidate_info(CONFIG)
    assert info["phone"] == "07 11 11 11 11"
    # Un champ vide dans le fichier local n'efface pas la valeur de la config.
    assert info["name"] == "Camille TESTEUR"


def test_fichier_local_illisible_ignore(isolated: Path) -> None:
    isolated.write_text("phone: [non fermé", encoding="utf-8")
    assert load_local_candidate(isolated) == {}
    assert get_candidate_info(CONFIG)["phone"] == ""


def test_cv_fichier_prioritaire_sur_le_secret(tmp_path, monkeypatch) -> None:
    cv_file = tmp_path / "cv.txt"
    cv_file.write_text("CV du fichier", encoding="utf-8")
    monkeypatch.setenv("CANDIDATE_CV", "CV du secret")
    assert get_cv_text({"scoring": {"cv_path": str(cv_file)}}) == "CV du fichier"


def test_cv_secret_si_fichier_absent(tmp_path, monkeypatch) -> None:
    config = {"scoring": {"cv_path": str(tmp_path / "absent.txt")}}
    assert get_cv_text(config) == ""
    monkeypatch.setenv("CANDIDATE_CV", "Ligne 1\nLigne 2")
    assert get_cv_text(config) == "Ligne 1\nLigne 2"
