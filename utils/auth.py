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


def require_auth() -> None:
    """Garde d'accès Streamlit : interrompt l'affichage si l'utilisateur n'est pas connecté."""
    if not is_auth_enabled():
        return

    if st.session_state.get("authenticated", False):
        return

    # Interface de connexion épurée
    st.markdown(
        """
        <style>
        .auth-container {
            max-width: 420px;
            margin: 60px auto 20px auto;
            padding: 32px;
            background: var(--background-secondary, rgba(255, 255, 255, 0.05));
            border: 1px solid var(--border-color, rgba(128, 128, 128, 0.2));
            border-radius: 8px;
            text-align: center;
        }
        .auth-title {
            font-size: 1.4rem;
            font-weight: 600;
            margin-bottom: 8px;
            letter-spacing: -0.02em;
        }
        .auth-subtitle {
            font-size: 0.85rem;
            color: gray;
            margin-bottom: 24px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    _, col, _ = st.columns([1, 1.4, 1])
    with col:
        st.markdown(
            """
            <div class="auth-container">
                <div class="auth-title">Stage Copilot</div>
                <div class="auth-subtitle">Console d'ingénierie & veille R&D protégée</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        password = st.text_input(
            "Mot de passe d'accès",
            type="password",
            placeholder="Entrez le mot de passe maître...",
            label_visibility="collapsed",
            key="auth_master_password_input",
        )
        if st.button("Déverrouiller la console", use_container_width=True, type="primary"):
            if check_password(password):
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("Mot de passe incorrect.")

    st.stop()


def render_logout_button() -> None:
    """Affiche un bouton de déconnexion discret dans la barre latérale si l'authentification est active."""
    if not is_auth_enabled():
        return

    if st.session_state.get("authenticated", False):
        st.sidebar.markdown("---")
        if st.sidebar.button("Déconnexion", use_container_width=True, type="secondary"):
            st.session_state["authenticated"] = False
            st.rerun()

