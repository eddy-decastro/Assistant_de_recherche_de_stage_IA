"""Coquille commune des pages : styles, garde d'accès et en-tête unifié."""
from __future__ import annotations

import html

import streamlit as st

from utils.styles import inject_styles


def page_setup() -> None:
    """Prépare une page : feuille de style, garde d'accès, suivi de tâche.

    Appelée en tête de chaque page, y compris lorsqu'elle est exécutée seule
    (tests ``AppTest``). Le bouton de déconnexion est rendu par le routeur
    ``app.py`` après la page, pour rester en bas de la barre latérale.
    """
    from utils.auth import require_auth
    from utils.task_manager import render_sidebar_task_badge

    inject_styles()
    require_auth()
    render_sidebar_task_badge()


def page_header_html(eyebrow: str, title: str, subtitle: str | None = None) -> str:
    """Fragment HTML de l'en-tête de page (le sous-titre accepte du HTML contrôlé)."""
    sub = f'<div class="sc-subtitle">{subtitle}</div>' if subtitle else ""
    return (
        '<div class="sc-page-head">'
        f'<div class="sc-eyebrow">{html.escape(eyebrow)}</div>'
        f'<div class="sc-title">{html.escape(title)}</div>'
        f"{sub}</div>"
    )


def render_page_header(eyebrow: str, title: str, subtitle: str | None = None) -> None:
    """En-tête commun à toutes les pages : sur-titre, titre, sous-titre."""
    st.markdown(page_header_html(eyebrow, title, subtitle), unsafe_allow_html=True)
