"""Page Flux : KPI, filtres latéraux et flux d'offres interactif."""
from __future__ import annotations

import streamlit as st

from components.job_feed import job_feed
from src.config import load_config
from src.constants import source_rank
from utils.components import (
    render_header,
    render_kpis,
    render_sidebar_filters,
    show_cover_letter_dialog,
)
from utils.data import filter_jobs, get_database, load_jobs

config = load_config()
db = get_database()
keywords = tuple(config.get("scoring", {}).get("excellence_keywords", ()))
llm_model = str(config.get("llm", {}).get("model", "juge LLM"))

data_version = int(st.session_state.setdefault("data_version", 0))
jobs = load_jobs(db, data_version)
sources = sorted({str(job["source"]) for job in jobs if job.get("source")}, key=source_rank)

filters = render_sidebar_filters(jobs, sources)
selected = filter_jobs(jobs, filters)

render_header(jobs, config)
render_kpis(selected, llm_model, len(jobs), not filters.is_default())

if not jobs:
    st.info("La base est vide : lancez la collecte depuis la page Pipeline.", icon=":material/database:")
else:
    # Monté même sans résultat : l'état vide et l'annulation du dernier changement vivent dans le composant.
    event = job_feed(
        selected,
        keywords,
        hide_processed=filters.hide_processed,
        grouped=filters.group_by_source,
    )
    if event and event["type"] == "letter":
        job = next((j for j in selected if str(j["id"]) == event["id"]), None)
        if job is not None:
            show_cover_letter_dialog(job)
