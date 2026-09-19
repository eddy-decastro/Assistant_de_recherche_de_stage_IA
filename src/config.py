"""Chargement de la configuration YAML (config.yaml)."""
from __future__ import annotations

import os
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"


@lru_cache(maxsize=1)
def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    """Charge et retourne le contenu de config.yaml.

    Le résultat est mis en cache pour éviter de relire le fichier à chaque appel.
    """
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data if isinstance(data, dict) else {}


def save_config(data: dict[str, Any], path: str | Path = DEFAULT_CONFIG_PATH) -> None:
    """Enregistre la configuration dans config.yaml et invalide le cache.

    L'écriture est atomique : un fichier temporaire est créé dans le même
    répertoire puis renommé, ce qui garantit qu'un crash en cours d'écriture
    ne laisse pas le fichier cible corrompu.
    """
    config_path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=config_path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)
        os.replace(tmp, config_path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    load_config.cache_clear()
