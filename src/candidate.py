"""Profil du candidat (CV et coordonnées), tenu hors du dépôt git.

Le dépôt est public : le CV et les coordonnées privées (téléphone, email) ne sont
pas versionnés. Chaque valeur est résolue dans cet ordre, la dernière source
renseignée l'emportant :

1. section ``candidate`` de ``config.yaml`` (champs publics : nom, titre, liens) ;
2. variables d'environnement ``CANDIDATE_<CHAMP>`` : ``.env`` en local, secrets
   GitHub Actions pour le cron, secrets Streamlit pour l'application en ligne ;
3. ``data/candidate.local.yaml`` (ignoré par git, écrit par la page Paramètres) :
   une saisie dans l'interface prime sur les secrets. Sur un hébergement au
   disque éphémère, elle est perdue au redémarrage et les secrets reprennent la main.

Le CV suit la même logique : le fichier désigné par ``scoring.cv_path`` (ignoré
par git, écrit par la page Paramètres) s'il existe, sinon ``CANDIDATE_CV``.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from src.config import PROJECT_ROOT, load_config

CANDIDATE_FIELDS: tuple[str, ...] = (
    "name",
    "title",
    "phone",
    "email",
    "linkedin",
    "github",
    "location",
)
LOCAL_CANDIDATE_PATH = PROJECT_ROOT / "data" / "candidate.local.yaml"
DEFAULT_CV_PATH = "data/cv_eddy.txt"
CV_ENV_VAR = "CANDIDATE_CV"


def _load_env() -> None:
    """Charge ``.env`` et ``st.secrets`` dans ``os.environ`` (sans écraser l'existant)."""
    from src.matching.llm_judge import load_env_file

    load_env_file()


def _env_var(field: str) -> str:
    return f"CANDIDATE_{field.upper()}"


def _clean(raw: Any) -> dict[str, str]:
    """Ne garde que les champs connus et renseignés."""
    if not isinstance(raw, dict):
        return {}
    return {
        k: str(v).strip()
        for k, v in raw.items()
        if k in CANDIDATE_FIELDS and v is not None and str(v).strip()
    }


def load_local_candidate(path: Path | None = None) -> dict[str, str]:
    """Lit les coordonnées locales (fichier absent ou illisible : dictionnaire vide)."""
    path = path or LOCAL_CANDIDATE_PATH
    if not path.exists():
        return {}
    try:
        return _clean(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError):
        return {}


def save_local_candidate(info: dict[str, Any], path: Path | None = None) -> None:
    """Enregistre les coordonnées dans le fichier local ignoré par git."""
    path = path or LOCAL_CANDIDATE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {k: str(info.get(k) or "").strip() for k in CANDIDATE_FIELDS}
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def get_candidate_info(
    config: dict[str, Any] | None = None,
    local_path: Path | None = None,
) -> dict[str, str]:
    """Coordonnées du candidat : config.yaml, puis environnement, puis fichier local."""
    cfg = config or load_config()
    info = {field: "" for field in CANDIDATE_FIELDS}
    info.update(_clean(cfg.get("candidate")))
    _load_env()
    for field in CANDIDATE_FIELDS:
        value = os.environ.get(_env_var(field), "").strip()
        if value:
            info[field] = value
    info.update(load_local_candidate(local_path))
    return info


def get_cv_path(config: dict[str, Any] | None = None) -> Path:
    """Chemin du fichier CV (``scoring.cv_path``), relatif à la racine du projet."""
    cfg = config or load_config()
    path = Path(cfg.get("scoring", {}).get("cv_path") or DEFAULT_CV_PATH)
    return path if path.is_absolute() else PROJECT_ROOT / path


def get_cv_text(config: dict[str, Any] | None = None) -> str:
    """Texte du CV : fichier local s'il existe, sinon variable ``CANDIDATE_CV`` (vide si aucun)."""
    path = get_cv_path(config)
    if path.exists():
        return path.read_text(encoding="utf-8")
    _load_env()
    return os.environ.get(CV_ENV_VAR, "")
