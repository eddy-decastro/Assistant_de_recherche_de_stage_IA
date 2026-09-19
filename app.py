"""Dashboard Streamlit — Stage Copilot.

Console d'ingénierie pour la veille de stages R&D / Data Science :

* un bandeau KPI compact (volume, offres qualifiées, couverture LLM,
  répartition par plateforme) ;
* un flux de cartes d'offres scorées (score R&D, verdict du juge LLM,
  technologies détectées, actions de candidature) ;
* des filtres latéraux denses (recherche plein texte, plateformes, score
  minimal, critères avancés) et la maintenance de la base SQLite.

Aucun emoji décoratif : la hiérarchie visuelle repose sur la typographie, les
badges de métadonnées et des indicateurs d'état discrets (score, verdict,
statut de candidature). Le thème natif Streamlit (clair ou sombre) est respecté
grâce à une palette de jetons CSS générée dynamiquement.
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_config
from utils.data import get_database, load_jobs, filter_jobs
from utils.styles import inject_styles
from utils.components import (
    render_header,
    render_kpis,
    render_stream,
    render_sidebar_filters
)

st.set_page_config(
    page_title="Stage Copilot",
    page_icon=":material/insights:",
    layout="wide",
)

def main() -> None:
    """Assemble le dashboard : styles, filtres, en-tête, KPI, flux et télémétrie."""
    inject_styles()
    config = load_config()
    db = get_database()
    keywords = tuple(config.get("scoring", {}).get("excellence_keywords", ()))
    llm_model = str(config.get("llm", {}).get("model", "juge LLM"))

    data_version = int(st.session_state.setdefault("data_version", 0))
    jobs = load_jobs(db, data_version)
    
    # rank is needed here or from utils.data
    from src.constants import source_rank
    sources = sorted({str(job["source"]) for job in jobs if job.get("source")}, key=source_rank)

    filters = render_sidebar_filters(jobs, sources)
    selected = filter_jobs(jobs, filters)

    render_header(jobs, config)
    render_kpis(selected, llm_model, len(jobs), not filters.is_default())

    # La télémétrie est extraite vers pages/statistiques.py
    # La maintenance pipeline vers pages/pipeline.py
    render_stream(db, selected, filters, keywords)


if __name__ == "__main__":
    main()
