"""Chargement de la configuration YAML (config.yaml + surcouche privée config.local.yaml).

Les données personnelles (coordonnées du candidat, CV) ne sont jamais versionnées :

* ``config.yaml`` (versionné) contient la configuration partagée ;
* ``config.local.yaml`` (ignoré par Git) contient les sections privées
  (``candidate``) et peut surcharger n'importe quelle clé de ``config.yaml`` ;
* sur Streamlit Cloud, la section ``[candidate]`` et la clé ``CV_TEXT`` des
  *secrets* jouent le même rôle (aucun fichier privé dans le dépôt).
"""
from __future__ import annotations

import copy
import os
import sys
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"
LOCAL_CONFIG_PATH = PROJECT_ROOT / "config.local.yaml"
DEFAULT_CV_PATH = "data/cv_eddy.txt"

# Sections écrites dans config.local.yaml (jamais dans config.yaml versionné).
PRIVATE_SECTIONS: tuple[str, ...] = ("candidate",)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data if isinstance(data, dict) else {}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Fusionne récursivement ``override`` dans une copie de ``base``."""
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _streamlit_secret(key: str) -> Any:
    """Lit un secret Streamlit, uniquement si l'application Streamlit est chargée."""
    if "streamlit" not in sys.modules:
        return None
    try:
        import streamlit as st

        # Sans fichier de secrets, un accès direct afficherait une erreur dans l'UI.
        if not st.secrets.load_if_toml_exists():
            return None
        return st.secrets.get(key)
    except Exception:
        return None


def _write_yaml_atomic(data: dict[str, Any], path: Path) -> None:
    """Écrit ``data`` dans ``path`` via un fichier temporaire renommé (atomique)."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@lru_cache(maxsize=1)
def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    """Charge config.yaml, surchargé par config.local.yaml et les secrets Streamlit.

    Le résultat est mis en cache pour éviter de relire le fichier à chaque appel.
    La surcouche privée ne s'applique qu'à la configuration par défaut.
    """
    config_path = Path(path)
    data = _read_yaml(config_path)
    if config_path.resolve() != DEFAULT_CONFIG_PATH.resolve():
        return data

    data = _deep_merge(data, _read_yaml(LOCAL_CONFIG_PATH))
    secret_candidate = _streamlit_secret("candidate")
    if secret_candidate and not data.get("candidate"):
        data["candidate"] = dict(secret_candidate)
    return data


def save_config(data: dict[str, Any], path: str | Path = DEFAULT_CONFIG_PATH) -> None:
    """Enregistre la configuration et invalide le cache.

    Pour la configuration par défaut, les sections privées (``PRIVATE_SECTIONS``)
    sont écrites dans config.local.yaml (ignoré par Git) et retirées de
    config.yaml. L'écriture de chaque fichier est atomique.
    """
    config_path = Path(path)
    if config_path.resolve() != DEFAULT_CONFIG_PATH.resolve():
        _write_yaml_atomic(data, config_path)
        load_config.cache_clear()
        return

    public = {k: v for k, v in data.items() if k not in PRIVATE_SECTIONS}
    private = {k: data[k] for k in PRIVATE_SECTIONS if k in data}
    _write_yaml_atomic(public, config_path)
    if private:
        _write_yaml_atomic(_deep_merge(_read_yaml(LOCAL_CONFIG_PATH), private), LOCAL_CONFIG_PATH)
    load_config.cache_clear()


def resolve_cv_path(config: dict[str, Any] | None = None) -> Path:
    """Chemin du CV (``scoring.cv_path``), relatif à la racine du projet."""
    cfg = config if config is not None else load_config()
    raw = (cfg.get("scoring") or {}).get("cv_path") or DEFAULT_CV_PATH
    cv_path = Path(raw)
    return cv_path if cv_path.is_absolute() else PROJECT_ROOT / cv_path


def load_cv_text(config: dict[str, Any] | None = None) -> str:
    """Texte du CV : fichier local, sinon variable d'environnement / secret ``CV_TEXT``."""
    cv_path = resolve_cv_path(config)
    if cv_path.exists():
        return cv_path.read_text(encoding="utf-8")
    return os.environ.get("CV_TEXT") or str(_streamlit_secret("CV_TEXT") or "")
