"""Point d'entrée Stage Copilot : authentification, styles globaux et navigation."""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.auth import render_logout_button, require_auth
from utils.styles import inject_styles
from utils.task_manager import render_sidebar_task_badge

st.set_page_config(
    page_title="Stage Copilot",
    page_icon=":material/insights:",
    layout="wide",
)

require_auth()
inject_styles()

navigation = st.navigation(
    {
        "Veille": [
            st.Page("app_pages/flux.py", title="Flux", icon=":material/view_list:", default=True),
            st.Page("app_pages/kanban.py", title="Candidatures", icon=":material/view_kanban:"),
        ],
        "Analyse": [
            st.Page("app_pages/statistiques.py", title="Statistiques", icon=":material/monitoring:"),
        ],
        "Système": [
            st.Page("app_pages/pipeline.py", title="Pipeline", icon=":material/sync:"),
            st.Page("app_pages/parametres.py", title="Paramètres", icon=":material/tune:"),
        ],
    }
)
navigation.run()

# Après la page : filtres de la page d'abord, badge de tâche et déconnexion en bas de sidebar.
render_sidebar_task_badge()
render_logout_button()
