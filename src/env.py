"""Chargement des variables d'environnement (.env et secrets Streamlit)."""
from __future__ import annotations

import os
from pathlib import Path

from src.config import PROJECT_ROOT


def load_env_file(path: str | Path | None = None) -> None:
    """Charge les variables d'un fichier .env ou de st.secrets dans os.environ (sans écraser l'existant)."""
    try:
        import streamlit as st
        if hasattr(st, "secrets"):
            for k, v in st.secrets.items():
                if isinstance(v, str) and k not in os.environ:
                    os.environ[k] = v
    except Exception:
        pass

    env_path = Path(path) if path else (PROJECT_ROOT / ".env")
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
