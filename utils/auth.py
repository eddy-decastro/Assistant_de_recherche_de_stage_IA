"""Module d'authentification et de contrôle d'accès pour Stage Copilot.

Protège l'application Streamlit lorsqu'elle est déployée sur le Web (Render) :
- Si la variable d'environnement ``APP_PASSWORD`` est définie : exige la saisie
  du mot de passe avant d'accéder au dashboard, aux données et au Kanban.
- Si ``APP_PASSWORD`` est vide ou absente : l'accès est libre (mode développement local).
"""
from __future__ import annotations

import hmac
import os
import sys
import streamlit as st


def is_auth_enabled() -> bool:
    """Indique si une protection par mot de passe est configurée."""
    if ("pytest" in sys.modules or os.getenv("PYTEST_CURRENT_TEST")) and not os.getenv("TESTING_AUTH"):
        return False
    try:
        from src.storage.cloud_storage import _load_env
        _load_env()
    except Exception:
        pass
    return bool(os.getenv("APP_PASSWORD", "").strip())


def check_password(password_attempt: str) -> bool:
    """Compare le mot de passe soumis avec la variable d'environnement en temps constant."""
    try:
        from src.storage.cloud_storage import _load_env
        _load_env()
    except Exception:
        pass
    expected = os.getenv("APP_PASSWORD", "").strip()
    if not expected:
        return True
    return hmac.compare_digest(password_attempt.strip().encode("utf-8"), expected.encode("utf-8"))


def is_authenticated() -> bool:
    """Vrai si l'accès est libre ou si la session courante est déverrouillée."""
    return not is_auth_enabled() or bool(st.session_state.get("authenticated", False))


def render_login() -> None:
    """Écran de connexion : formulaire validable avec la touche Entrée."""
    st.markdown(
        '<div class="sc-auth">'
        '<div class="sc-auth-mark">SC</div>'
        '<div class="sc-title">Stage Copilot</div>'
        '<div class="sc-subtitle">Veille de stages R&amp;D protégée. '
        "Saisissez le mot de passe d'accès pour continuer.</div>"
        "</div>",
        unsafe_allow_html=True,
    )
    _, col, _ = st.columns([1, 1.2, 1])
    with col:
        with st.form("login", border=True):
            password = st.text_input(
                "Mot de passe d'accès",
                type="password",
                placeholder="Mot de passe",
                key="auth_master_password_input",
            )
            submitted = st.form_submit_button(
                "Déverrouiller", type="primary", width="stretch", icon=":material/lock_open:"
            )
        if submitted:
            if check_password(password):
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("Mot de passe incorrect.", icon=":material/error:")


def require_auth() -> None:
    """Garde d'accès Streamlit : affiche la connexion et interrompt la page si besoin."""
    if is_authenticated():
        return
    render_login()
    st.stop()


def render_logout_button() -> None:
    """Affiche un bouton de déconnexion discret dans la barre latérale si l'authentification est active."""
    if not is_auth_enabled():
        return

    if st.session_state.get("authenticated", False):
        st.sidebar.divider()
        if st.sidebar.button("Déconnexion", width="stretch", icon=":material/logout:"):
            st.session_state["authenticated"] = False
            st.rerun()

