"""Page Candidatures : tableau Kanban de suivi, une colonne par statut."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import streamlit as st

from src.constants import (
    SOURCE_MANUAL,
    STATUS_APPLIED,
    STATUS_IGNORED,
    STATUS_INTERVIEW,
    STATUS_NEW,
    STATUS_REJECTED,
    VERDICT_LABELS,
)
from utils.components import show_cover_letter_dialog
from utils.data import (
    Filters,
    VERDICT_TONES,
    _set_status,
    bump_data_version,
    effective_score,
    filter_jobs,
    get_database,
    is_reranked,
    load_jobs,
    score_alignment,
)
from utils.layout import page_header

KANBAN_COLUMNS = (
    (STATUS_NEW, "Nouveau"),
    (STATUS_APPLIED, "Postulé"),
    (STATUS_INTERVIEW, "Entretien"),
    (STATUS_REJECTED, "Refusé"),
    (STATUS_IGNORED, "Archivé"),
)
PAGE_STEP = 10
_BADGE_COLORS = {"positive": "green", "accent": "blue", "warn": "orange", "alert": "red", "mute": "gray"}


def _md(text: Any) -> str:
    """Neutralise le markdown d'un texte venu d'une plateforme (titres, entreprises)."""
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|~<>$&])", r"\\\1", " ".join(str(text or "").split()))


def _limit_key(status: str) -> str:
    return f"kanban_limit_{status}"


def _show_more(status: str) -> None:
    st.session_state[_limit_key(status)] = st.session_state.get(_limit_key(status), PAGE_STEP) + PAGE_STEP


def _render_card(job: dict[str, Any], db: Any, current_status: str) -> None:
    job_id = str(job["id"])
    score = effective_score(job)
    align_label, tone = score_alignment(score)
    url = str(job.get("url") or "")
    location = str(job.get("location") or "")

    with st.container(border=True):
        st.markdown(f"**{_md(job.get('title') or 'Offre sans titre')}**")
        st.caption(_md(job.get("company") or "Entreprise inconnue") + (f" · {_md(location)}" if location else ""))
        badges = [f":{_BADGE_COLORS[tone]}-badge[{score:.0f} · {align_label}]"]
        verdict = job.get("verdict")
        if is_reranked(job) and verdict:
            color = _BADGE_COLORS.get(VERDICT_TONES.get(verdict, "mute"), "gray")
            badges.append(f":{color}-badge[{VERDICT_LABELS.get(verdict, verdict)}]")
        st.markdown(" ".join(badges))

        with st.popover("Actions", icon=":material/more_horiz:", width="stretch"):
            if url.startswith(("http://", "https://")):
                st.link_button("Ouvrir l'offre", url, icon=":material/open_in_new:", width="stretch")
            if st.button("Lettre de motivation", key=f"kb_letter_{job_id}", icon=":material/edit_note:", width="stretch"):
                show_cover_letter_dialog(job)
            for status, label in KANBAN_COLUMNS:
                if status in (current_status, STATUS_REJECTED):
                    continue
                st.button(
                    f"Déplacer vers {label.lower()}",
                    key=f"kb_move_{status}_{job_id}",
                    on_click=_set_status,
                    args=(db, job_id, status),
                    width="stretch",
                )


page_header("Candidatures", "Faites évoluer le statut de vos candidatures d'une colonne à l'autre.")

db = get_database()

with st.expander("Ajouter une candidature", icon=":material/add:"):
    with st.form("kb_add_application", clear_on_submit=True, border=False):
        col_company, col_title = st.columns(2)
        company_input = col_company.text_input("Entreprise")
        title_input = col_title.text_input("Intitulé du poste")
        url_input = st.text_input("Lien de l'offre (facultatif)")
        col_date, col_status = st.columns(2)
        date_input = col_date.date_input("Date de candidature", value="today", format="DD/MM/YYYY")
        status_input = col_status.selectbox(
            "Statut",
            options=[STATUS_APPLIED, STATUS_INTERVIEW, STATUS_REJECTED],
            format_func={STATUS_APPLIED: "Postulé", STATUS_INTERVIEW: "Entretien", STATUS_REJECTED: "Refusé"}.get,
        )
        if st.form_submit_button("Ajouter", icon=":material/add:"):
            try:
                _, created = db.record_application(
                    company_input,
                    title_input,
                    status=status_input,
                    applied_at=datetime.combine(date_input, datetime.min.time()),
                    url=url_input.strip() or None,
                    source=SOURCE_MANUAL,
                )
            except ValueError as exc:
                st.error(str(exc))
            else:
                bump_data_version()
                st.toast("Candidature ajoutée." if created else "Offre existante mise à jour.")

data_version = int(st.session_state.setdefault("data_version", 0))
jobs = load_jobs(db, data_version)

company_counts: dict[str, int] = {}
for job in jobs:
    company = (job.get("company") or "").strip()
    if company:
        company_counts[company] = company_counts.get(company, 0) + 1
sorted_companies = sorted(company_counts, key=lambda c: (-company_counts[c], c.lower()))

with st.expander("Filtres", icon=":material/filter_list:"):
    left, middle, right = st.columns([2, 1, 1])
    with left:
        query = st.text_input("Recherche", placeholder="Poste ou entreprise…", icon=":material/search:")
    with middle:
        min_score = st.slider("Score minimal", 0, 100, 0, step=5)
    with right:
        rerank_only = st.toggle("Verdict LLM uniquement")
        exclude_dassault = st.toggle("Exclure Dassault")
    col_ex, col_sel = st.columns(2)
    with col_ex:
        exclude_companies = st.multiselect(
            "Exclure des entreprises",
            options=sorted_companies,
            format_func=lambda c: f"{c} ({company_counts.get(c, 0)})",
        )
    with col_sel:
        selected_companies = st.multiselect(
            "Cibler des entreprises",
            options=sorted_companies,
            format_func=lambda c: f"{c} ({company_counts.get(c, 0)})",
        )

filtered = filter_jobs(
    jobs,
    Filters(
        query=query,
        min_score=float(min_score),
        llm_only=rerank_only,
        exclude_dassault=exclude_dassault,
        exclude_companies=tuple(exclude_companies),
        selected_companies=tuple(selected_companies),
    ),
)

jobs_by_status: dict[str, list[dict[str, Any]]] = {code: [] for code, _ in KANBAN_COLUMNS}
for job in filtered:
    code = str(job.get("status") or STATUS_NEW)
    jobs_by_status[code if code in jobs_by_status else STATUS_NEW].append(job)

columns = st.columns(len(KANBAN_COLUMNS), gap="small")
for column, (status, label) in zip(columns, KANBAN_COLUMNS):
    members = jobs_by_status[status]
    limit = st.session_state.get(_limit_key(status), PAGE_STEP)
    with column:
        st.markdown(f"##### {label} ({len(members)})")
        if not members:
            st.caption("Aucune offre")
        for job in members[:limit]:
            _render_card(job, db, status)
        remaining = len(members) - limit
        if remaining > 0:
            st.button(
                f"Afficher {min(PAGE_STEP, remaining)} de plus ({remaining} restantes)",
                key=f"kb_more_{status}",
                on_click=_show_more,
                args=(status,),
                width="stretch",
            )
