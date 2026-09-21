"""Tableau Kanban interactif — Suivi des Candidatures (Stage Copilot).

Visualisation par colonnes selon l'avancement dans le recrutement :
* Nouveau (offres qualifiées à étudier)
* Postulé (candidature envoyée)
* Entretien / Relance (en cours d'échange)
* Refusé / Non retenu
* Ignoré (archivé)

Chaque carte permet le changement de statut en 1 clic et réagit immédiatement.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_config
from src.constants import (
    STATUS_NEW,
    STATUS_APPLIED,
    STATUS_INTERVIEW,
    STATUS_REJECTED,
    STATUS_IGNORED,
    TIER_1,
    TIER_ESN,
    TIER_LABELS,
    VERDICT_LABELS,
)
from utils.components import _badge, score_alignment, show_cover_letter_dialog
from utils.data import (
    Filters,
    bump_data_version,
    effective_score,
    filter_jobs,
    get_database,
    is_reranked,
    load_jobs,
    STATUS_LABELS,
    VERDICT_TONES,
    tone_class,
)
from utils.styles import inject_styles
from utils.task_manager import render_sidebar_task_badge

st.set_page_config(
    page_title="Kanban Candidatures",
    page_icon=":material/view_kanban:",
    layout="wide",
)

inject_styles()
render_sidebar_task_badge()

from utils.auth import require_auth, render_logout_button
require_auth()
render_logout_button()

# Rechargement défensif si Streamlit a conservé une ancienne version en cache mémoire
if not hasattr(Filters, "__dataclass_fields__") or "exclude_companies" not in Filters.__dataclass_fields__:
    import importlib
    import utils.data
    importlib.reload(utils.data)
    from utils.data import Filters, filter_jobs

KANBAN_COLUMNS = [
    (STATUS_NEW, "🆕 Nouveau", "sc-tone-accent"),
    (STATUS_APPLIED, "📤 Postulé", "sc-tone-positive"),
    (STATUS_INTERVIEW, "💬 Entretien", "sc-tone-warn"),
    (STATUS_REJECTED, "❌ Refusé", "sc-tone-alert"),
    (STATUS_IGNORED, "🙈 Ignoré", "sc-tone-mute"),
]


def _render_kanban_card(job: dict[str, Any], db: Any) -> None:
    """Rend une carte compacte dans la colonne Kanban correspondante."""
    job_id = str(job["id"])
    score = effective_score(job)
    align_label, tone = score_alignment(score)
    company = str(job.get("company") or "Entreprise inconnue")
    title = str(job.get("title") or "Offre sans titre")
    location = str(job.get("location") or "")
    url = str(job.get("url") or "#")
    current_status = str(job.get("status") or STATUS_NEW)

    card_container = st.container(border=True)
    with card_container:
        # En-tête de carte : titre cliquable & score
        st.markdown(
            f'<div style="font-weight: 600; font-size: 0.95rem; line-height: 1.25;">'
            f'<a href="{url}" target="_blank" style="text-decoration: none; color: inherit;">{title}</a></div>'
            f'<div style="color: var(--sc-fg-muted); font-size: 0.8rem; margin-top: 2px;">{company}'
            f'{" · " + location if location else ""}</div>',
            unsafe_allow_html=True,
        )

        # Badges : Score, Tier, Verdict
        badges_html = [
            f'<span class="sc-badge {tone_class(tone)}"><b>{score:.0f}</b>/100 · {align_label}</span>'
        ]

        if is_reranked(job):
            verdict = job.get("verdict")
            if verdict:
                badges_html.append(
                    _badge(
                        VERDICT_LABELS.get(verdict, verdict),
                        VERDICT_TONES.get(verdict, "mute"),
                    )
                )

        tier = job.get("company_tier")
        if tier in (TIER_1, TIER_ESN):
            badges_html.append(
                _badge(
                    TIER_LABELS.get(tier, str(tier)),
                    "positive" if tier == TIER_1 else "alert",
                )
            )

        st.markdown(
            f'<div style="margin-top: 8px; margin-bottom: 8px;">{" ".join(badges_html)}</div>',
            unsafe_allow_html=True,
        )

        # Bouton d'action et transition de statut
        col_btn1, col_btn2 = st.columns([1, 1], gap="small")
        with col_btn1:
            if url.startswith("http"):
                st.link_button("🚀 Postuler ↗", url, type="primary", use_container_width=True)
        with col_btn2:
            if st.button("✍️ Lettre", key=f"kanban_letter_{job_id}", use_container_width=True):
                show_cover_letter_dialog(job)

        options = [status_code for status_code, _, _ in KANBAN_COLUMNS]
        idx = options.index(current_status) if current_status in options else 0
        new_status = st.selectbox(
            "Changer statut",
            options=options,
            format_func=lambda s: STATUS_LABELS.get(s, s),
            index=idx,
            key=f"kanban_status_{job_id}",
            label_visibility="collapsed",
        )
        if new_status != current_status:
            db.update_status(job_id, new_status)
            bump_data_version()
            st.rerun()


def main() -> None:
    st.markdown("<h1>Tableau Kanban — Suivi des Candidatures</h1>", unsafe_allow_html=True)
    st.caption(
        "Faites évoluer le statut de vos candidatures d'une colonne à une autre en 1 clic."
    )

    db = get_database()
    data_version = int(st.session_state.setdefault("data_version", 0))
    jobs = load_jobs(db, data_version)

    company_counts: dict[str, int] = {}
    for job in jobs:
        comp = (job.get("company") or "").strip()
        if comp:
            company_counts[comp] = company_counts.get(comp, 0) + 1
    sorted_companies = sorted(company_counts.keys(), key=lambda c: (-company_counts[c], c.lower()))

    # Zone de filtres compacts
    with st.expander("🎛️ Filtres du Tableau Kanban", expanded=False):
        c1, c2, c3 = st.columns([2, 1, 1])
        with c1:
            query = st.text_input("Recherche", placeholder="Rechercher par poste ou entreprise…")
        with c2:
            min_score = st.slider("Score min", 0, 100, 0, step=5)
        with c3:
            rerank_only = st.toggle("Verdict LLM uniquement", value=False)
            exclude_dassault = st.toggle("Exclure Dassault", value=False, help="Masque les offres Dassault Systèmes et Dassault Aviation.")

        col_ex, col_sel = st.columns(2)
        with col_ex:
            exclude_companies = st.multiselect(
                "Exclure des entreprises",
                options=sorted_companies,
                default=[],
                format_func=lambda c: f"{c} ({company_counts.get(c, 0)})",
            )
        with col_sel:
            selected_companies = st.multiselect(
                "Cibler des entreprises",
                options=sorted_companies,
                default=[],
                format_func=lambda c: f"{c} ({company_counts.get(c, 0)})",
            )

    filtered = filter_jobs(
        jobs,
        Filters(
            query=query,
            min_score=min_score,
            llm_only=rerank_only,
            exclude_dassault=exclude_dassault,
            exclude_companies=tuple(exclude_companies),
            selected_companies=tuple(selected_companies),
            limit=500,
        ),
    )

    # Répartition par statut
    jobs_by_status: dict[str, list[dict[str, Any]]] = {
        code: [] for code, _, _ in KANBAN_COLUMNS
    }
    for job in filtered:
        st_code = str(job.get("status") or STATUS_NEW)
        if st_code in jobs_by_status:
            jobs_by_status[st_code].append(job)
        else:
            jobs_by_status[STATUS_NEW].append(job)

    # Colonnes Kanban
    cols = st.columns(len(KANBAN_COLUMNS))

    for idx, (status_code, label, tone) in enumerate(KANBAN_COLUMNS):
        column_jobs = jobs_by_status[status_code]
        with cols[idx]:
            st.markdown(
                f'<div style="background: var(--sc-bg-card); padding: 8px 12px; border-radius: 6px; '
                f'border-top: 3px solid var(--sc-border-active); margin-bottom: 12px;">'
                f'<b style="font-size: 0.95rem;">{label}</b> '
                f'<span class="sc-badge {tone}" style="margin-left: 6px;">{len(column_jobs)}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

            if not column_jobs:
                st.caption("Aucune offre")
            else:
                for job in column_jobs:
                    _render_kanban_card(job, db)


if __name__ == "__main__":
    main()
