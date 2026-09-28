"""Stage Copilot — point d'entrée Streamlit et page « Offres ».

``streamlit run app.py`` démarre le routeur : navigation latérale par sections
(Veille / Pilotage), puis la page demandée. La page « Offres » est définie ici :

* un bandeau KPI compact (volume, offres qualifiées, couverture LLM,
  répartition par plateforme) ;
* un flux en deux volets : liste compacte des offres scorées à gauche, carte
  détaillée (verdict du juge LLM, grille d'évaluation, actions) à droite ;
* des filtres latéraux (recherche plein texte, plateformes, score minimal,
  critères avancés) réinitialisables en un clic.

Aucun emoji décoratif : la hiérarchie visuelle repose sur la typographie, les
badges et des indicateurs d'état discrets. Les thèmes clair et sombre sont
déclarés dans ``.streamlit/config.toml`` et relayés par les jetons CSS de
``utils/styles.py``.
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_config
from src.constants import source_rank
from utils.components import (
    active_filter_count,
    render_header,
    render_kpis,
    render_sidebar_filters,
    render_stream,
)
from utils.data import filter_jobs, get_database, load_jobs
from utils.layout import page_setup


def main() -> None:
    """Page « Offres » : filtres, en-tête, KPI et flux d'offres."""
    page_setup()

    config = load_config()
    db = get_database()
    keywords = tuple(config.get("scoring", {}).get("excellence_keywords", ()))
    llm_model = str(config.get("llm", {}).get("model", "juge LLM"))

    data_version = int(st.session_state.setdefault("data_version", 0))
    jobs = load_jobs(db, data_version)
    sources = sorted({str(job["source"]) for job in jobs if job.get("source")}, key=source_rank)

    filters = render_sidebar_filters(jobs, sources)
    selected = filter_jobs(jobs, filters)
    active = active_filter_count(filters, sources)

    render_header(jobs, config)
    render_kpis(selected, llm_model, len(jobs), bool(active))
    render_stream(db, selected, filters, keywords, base_total=len(jobs), active_filters=active)


def run() -> None:
    """Routeur : configuration de page, navigation, puis page courante."""
    from utils.auth import is_authenticated, render_login, render_logout_button
    from utils.styles import inject_styles

    st.set_page_config(page_title="Stage Copilot", page_icon=":material/radar:", layout="wide")

    if not is_authenticated():
        inject_styles()
        page = st.navigation([st.Page(render_login, title="Connexion", icon=":material/lock:")], position="hidden")
        page.run()
        return

    page = st.navigation(
        {
            "Veille": [
                st.Page(main, title="Offres", icon=":material/work:", url_path="offres", default=True),
                st.Page("pages/kanban.py", title="Candidatures", icon=":material/view_kanban:", url_path="candidatures"),
            ],
            "Pilotage": [
                st.Page("pages/statistiques.py", title="Statistiques", icon=":material/monitoring:", url_path="statistiques"),
                st.Page("pages/pipeline.py", title="Pipeline", icon=":material/play_circle:", url_path="pipeline"),
                st.Page("pages/parametres.py", title="Paramètres", icon=":material/tune:", url_path="parametres"),
            ],
        }
    )
    page.run()
    render_logout_button()


if __name__ == "__main__":
    run()
