"""Chargement de la configuration YAML (config.yaml)."""
from __future__ import annotations

import logging
import os
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"
logger = logging.getLogger(__name__)


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


def save_scraper_defaults(
    queries: list[str],
    sources: list[str],
    max_offers: int,
    path: str | Path = DEFAULT_CONFIG_PATH,
) -> None:
    """Écrit requêtes, sources et plafond dans la section ``scrapers`` de config.yaml.

    ruamel.yaml préserve les commentaires et l'ordre du fichier ; sans lui (ou en cas
    d'échec), repli sur ``save_config`` qui réécrit le fichier sans commentaires.
    """
    config_file = Path(path)
    try:
        from ruamel.yaml import YAML

        yaml_rt = YAML()
        yaml_rt.preserve_quotes = True
        with config_file.open("r", encoding="utf-8") as fh:
            data = yaml_rt.load(fh)
        section = data.setdefault("scrapers", {})
        section["enabled_sources"] = sources
        section["target_queries"] = queries
        section["max_offers_per_source"] = max_offers
        tmp = config_file.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            yaml_rt.dump(data, fh)
        tmp.replace(config_file)
        load_config.cache_clear()
    except Exception:
        logger.exception("Sauvegarde ruamel.yaml impossible, repli sur PyYAML")
        cfg = dict(load_config(config_file))
        section = cfg.setdefault("scrapers", {})
        section["enabled_sources"] = sources
        section["target_queries"] = queries
        section["max_offers_per_source"] = max_offers
        save_config(cfg, config_file)
