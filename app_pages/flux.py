"""Page Flux : KPI, filtres latéraux et flux d'offres scorées."""
from __future__ import annotations

import streamlit as st

from src.config import load_config
from src.constants import source_rank
from utils.components import (
    render_header,
    render_kpis,
    render_sidebar_filters,
    render_stream,
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
render_stream(db, selected, filters, keywords)
