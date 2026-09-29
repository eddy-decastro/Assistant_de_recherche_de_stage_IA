"""Composant Streamlit « job_feed » : flux d'offres (liste dense + détail), CCv2 inline."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import streamlit as st

from components.job_feed.actions import apply_status_action, parse_action
from components.job_feed.serialize import serialize_feed
from src.constants import STATUS_APPLIED, STATUS_IGNORED, STATUS_INTERVIEW, STATUS_NEW
from utils.data import STATUS_LABELS, get_database

_ASSETS = Path(__file__).parent


def _asset(name: str) -> str:
    return (_ASSETS / name).read_text(encoding="utf-8")


# Enregistré une seule fois à l'import (multi-lignes : traité comme contenu inline).
_FEED = st.components.v2.component(
    "job_feed",
    html=_asset("feed.html"),
    css=_asset("feed.css"),
    js=_asset("feed.js"),
)

_STATUS_CODES = {
    "new": STATUS_NEW,
    "applied": STATUS_APPLIED,
    "interview": STATUS_INTERVIEW,
    "ignored": STATUS_IGNORED,
}


def _on_action_change(key: str) -> None:
    """Callback exécuté avant le corps du script : les données rechargées sont déjà à jour."""
    state = st.session_state.get(key)
    raw = state.get("action") if state is not None else None
    if apply_status_action(get_database(), raw) == "failed":
        st.toast("Le statut n'a pas pu être enregistré.", icon=":material/error:")


def job_feed(
    jobs: list[dict[str, Any]],
    keywords: Sequence[str],
    *,
    hide_processed: bool,
    grouped: bool,
    key: str = "job_feed",
) -> dict[str, str] | None:
    """Affiche le flux et retourne l'action ``letter`` (ou ``status``) validée, sinon ``None``.

    Les changements de statut sont déjà persistés quand cette fonction retourne.
    """
    data = {
        "jobs": serialize_feed(jobs, keywords, grouped=grouped),
        "hide_processed": bool(hide_processed),
        "grouped": bool(grouped),
        "statuses": _STATUS_CODES,
        "status_labels": {code: STATUS_LABELS.get(code, code) for code in _STATUS_CODES.values()},
        "rev": int(st.session_state.get("data_version", 0)),
    }
    result = _FEED(key=key, data=data, on_action_change=lambda: _on_action_change(key))
    return parse_action(result.action)
