"""Chargement de la configuration YAML (config.yaml)."""
from __future__ import annotations

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
