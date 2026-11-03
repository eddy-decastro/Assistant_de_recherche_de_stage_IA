"""Page Flux : KPI, filtres latéraux et flux d'offres interactif."""
from __future__ import annotations

import streamlit as st

from components.job_feed import job_feed
from src.config import load_config
from src.constants import STATUS_IGNORED, STATUS_NEW, STATUS_REJECTED, source_rank
from utils.components import (
    render_header,
    render_kpis,
    render_sidebar_filters,
    show_claude_pack_dialog,
    show_cover_letter_dialog,
)
from utils.data import bump_data_version, filter_jobs, get_database, job_age_days, load_jobs

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

old_new_jobs = [
    j for j in selected
    if j.get("status") == STATUS_NEW and (job_age_days(j) or 0) >= 14
]
if old_new_jobs:
    col_msg, col_arch, col_trash = st.columns([3.0, 1.3, 1.3], vertical_alignment="center")
    with col_msg:
        st.caption(
            f"**{len(old_new_jobs)} offre(s) NOUVEAU** ont plus de 14 jours "
            "(archivage rapide facultatif)."
        )
    with col_arch:
        if st.button(
            f"Archiver > 14j ({len(old_new_jobs)})",
            key="quick_archive_14d",
            width="stretch",
            icon=":material/inventory_2:",
        ):
            db.bulk_update_status(
                [str(j["id"]) for j in old_new_jobs],
                STATUS_IGNORED,
                rejection_reason="quick_archive_14d",
            )
            bump_data_version()
            st.rerun()
    with col_trash:
        if st.button(
            f"Corbeille > 14j ({len(old_new_jobs)})",
            key="quick_trash_14d",
            width="stretch",
            icon=":material/delete:",
        ):
            db.bulk_update_status(
                [str(j["id"]) for j in old_new_jobs],
                STATUS_REJECTED,
                rejection_reason="quick_trash_14d",
            )
            bump_data_version()
            st.rerun()

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
    elif event and event["type"] == "claude_pack":
        ids_set = set(event.get("ids") or [])
        pack_jobs = [j for j in selected if str(j["id"]) in ids_set]
        if pack_jobs:
            show_claude_pack_dialog(pack_jobs)
