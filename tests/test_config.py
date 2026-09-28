"""Tests de la configuration : surcouche privée config.local.yaml et chargement du CV."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import src.config as config_module
from src.config import load_config, load_cv_text, save_config


@pytest.fixture
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Redirige config.yaml et config.local.yaml vers un répertoire temporaire."""
    public = tmp_path / "config.yaml"
    local = tmp_path / "config.local.yaml"
    public.write_text(yaml.safe_dump({"llm": {"model": "m"}, "scoring": {"weights": {"semantic": 0.6}}}), encoding="utf-8")
    monkeypatch.setattr(config_module, "DEFAULT_CONFIG_PATH", public)
    monkeypatch.setattr(config_module, "LOCAL_CONFIG_PATH", local)
    load_config.cache_clear()
    yield public, local
    load_config.cache_clear()


def test_local_config_overrides_public(isolated_config: tuple[Path, Path]) -> None:
    public, local = isolated_config
    local.write_text(
        yaml.safe_dump({"candidate": {"phone": "01"}, "scoring": {"weights": {"keywords": 0.2}}}),
        encoding="utf-8",
    )
    cfg = load_config(public)
    assert cfg["candidate"]["phone"] == "01"
    assert cfg["scoring"]["weights"] == {"semantic": 0.6, "keywords": 0.2}
    assert cfg["llm"]["model"] == "m"


def test_save_config_keeps_private_sections_out_of_public_file(isolated_config: tuple[Path, Path]) -> None:
    public, local = isolated_config
    cfg = dict(load_config(public))
    cfg["candidate"] = {"phone": "01 23 45 67 89", "email": "a@example.com"}
    save_config(cfg, public)

    assert "candidate" not in yaml.safe_load(public.read_text(encoding="utf-8"))
    assert yaml.safe_load(local.read_text(encoding="utf-8"))["candidate"]["phone"] == "01 23 45 67 89"
    assert load_config(public)["candidate"]["email"] == "a@example.com"


def test_load_cv_text_from_file_then_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cv_file = tmp_path / "cv.txt"
    cfg = {"scoring": {"cv_path": str(cv_file)}}

    monkeypatch.delenv("CV_TEXT", raising=False)
    assert load_cv_text(cfg) == ""

    monkeypatch.setenv("CV_TEXT", "CV depuis un secret")
    assert load_cv_text(cfg) == "CV depuis un secret"

    cv_file.write_text("CV local", encoding="utf-8")
    assert load_cv_text(cfg) == "CV local"
