"""Tableau Kanban interactif — Suivi des Candidatures (Stage Copilot).

Visualisation par colonnes selon l'avancement dans le recrutement :
* Nouveau (offres qualifiées à étudier)
* Postulé (candidature envoyée)
* Entretien / Relance (en cours d'échange)
* Rejeté (refus ou non retenu)
* Archivé

Chaque carte peut être déplacée vers n'importe quel statut via « Déplacer ».
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.constants import (
    STATUS_APPLIED,
    STATUS_IGNORED,
    STATUS_INTERVIEW,
    STATUS_NEW,
    STATUS_REJECTED,
    TIER_1,
    TIER_ESN,
    TIER_LABELS,
    VERDICT_LABELS,
)
from utils.components import _badge, show_cover_letter_dialog
from utils.data import (
    Filters,
    STATUS_LABELS,
    STATUS_TONES,
    VERDICT_TONES,
    _esc,
    _set_status,
    effective_score,
    filter_jobs,
    get_database,
    is_reranked,
    load_jobs,
    score_alignment,
)
from utils.layout import page_setup, render_page_header

page_setup()

# Vue large : les cinq colonnes ont besoin de toute la largeur de l'écran.
st.markdown(
    "<style>[data-testid=\"stMainBlockContainer\"] { max-width: 100% !important; "
    "padding-left: 2rem !important; padding-right: 2rem !important; }</style>",
    unsafe_allow_html=True,
)

# Ordre des colonnes = parcours de candidature.
KANBAN_COLUMNS = (STATUS_NEW, STATUS_APPLIED, STATUS_INTERVIEW, STATUS_REJECTED, STATUS_IGNORED)
MOVE_ICONS = {
    STATUS_NEW: ":material/inbox:",
    STATUS_APPLIED: ":material/send:",
    STATUS_INTERVIEW: ":material/forum:",
    STATUS_REJECTED: ":material/block:",
    STATUS_IGNORED: ":material/archive:",
}


def kanban_card_html(job: dict[str, Any]) -> str:
    """Contenu de carte Kanban : titre (lien si URL valide), entreprise, score et badges."""
    title = _esc(job.get("title") or "Offre sans titre")
    url = str(job.get("url") or "")
    if url.startswith("http"):
        title = f'<a href="{_esc(url)}" target="_blank" rel="noopener">{title}</a>'
    meta = [f"<b>{_esc(job.get('company') or 'Entreprise inconnue')}</b>"]
    if job.get("location"):
        meta.append(_esc(job["location"]))
    score = effective_score(job)
    align_label, tone = score_alignment(score)
    tags = [_badge(f"{score:.0f}/100 · {align_label}", tone)]
    if is_reranked(job) and job.get("verdict"):
        verdict = job["verdict"]
        tags.append(_badge(VERDICT_LABELS.get(verdict, verdict), VERDICT_TONES.get(verdict, "mute")))
    tier = job.get("company_tier")
    if tier in (TIER_1, TIER_ESN):
        tags.append(_badge(TIER_LABELS.get(tier, str(tier)), "positive" if tier == TIER_1 else "alert"))
    return (
        '<div class="sc-kanban-card">'
        f'<div class="sc-list-title">{title}</div>'
        f'<div class="sc-list-meta">{" · ".join(meta)}</div>'
        f'<div class="sc-list-tags">{"".join(tags)}</div>'
        "</div>"
    )


def _render_kanban_card(job: dict[str, Any], db: Any) -> None:
    """Carte compacte : en-tête, lettre de motivation et déplacement vers un autre statut."""
    job_id = str(job["id"])
    current = str(job.get("status") or STATUS_NEW)
    with st.container(border=True, key=f"card-kb-{job_id}"):
        st.markdown(kanban_card_html(job), unsafe_allow_html=True)
        with st.container(horizontal=True, gap="small"):
            if st.button("Lettre", key=f"kanban_letter_{job_id}", icon=":material/edit_note:"):
                show_cover_letter_dialog(job)
            with st.popover("Déplacer", help="Changer le statut de cette candidature"):
                for target in KANBAN_COLUMNS:
                    if target == current:
                        continue
                    st.button(
                        STATUS_LABELS.get(target, target),
                        key=f"kb_move_{target}_{job_id}",
                        icon=MOVE_ICONS[target],
                        on_click=_set_status,
                        args=(db, job_id, target),
                        width="stretch",
                    )


def _column_header(status: str, count: int) -> str:
    """En-tête de colonne : libellé du statut et compteur, teintés par sa tonalité."""
    tone = STATUS_TONES.get(status, "mute")
    return (
        f'<div class="sc-kanban-col sc-tone-{tone}"><b>{_esc(STATUS_LABELS.get(status, status))}</b>'
        f'<span class="sc-kanban-count">{count}</span></div>'
    )


def main() -> None:
    render_page_header(
        "Tableau Kanban",
        "Candidatures",
        "Suivez chaque offre de « Nouveau » à « Entretien ». Le bouton « Déplacer » "
        "d'une carte la fait passer à n'importe quel autre statut.",
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

    with st.expander("Filtres", icon=":material/filter_list:", expanded=False):
        c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
        with c1:
            query = st.text_input("Recherche", placeholder="Poste, entreprise, techno…", icon=":material/search:")
        with c2:
            min_score = st.slider("Score min", 0, 100, 0, step=5)
        with c3:
            rerank_only = st.toggle("Verdict LLM uniquement", value=False)
            exclude_dassault = st.toggle("Exclure Dassault", value=False, help="Masque les offres Dassault Systèmes et Dassault Aviation.")
        with c4:
            kanban_limit_choice = st.selectbox(
                "Affichage par colonne",
                options=["30", "50", "100", "Tout"],
                index=0,
                help="« Tout » affiche l'intégralité des offres de chaque colonne.",
            )

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

    display_limit = None if kanban_limit_choice == "Tout" else int(kanban_limit_choice)

    filtered = filter_jobs(
        jobs,
        Filters(
            query=query,
            min_score=min_score,
            llm_only=rerank_only,
            statuses=KANBAN_COLUMNS,
            exclude_dassault=exclude_dassault,
            exclude_companies=tuple(exclude_companies),
            selected_companies=tuple(selected_companies),
            limit=None,
        ),
    )

    jobs_by_status: dict[str, list[dict[str, Any]]] = {code: [] for code in KANBAN_COLUMNS}
    for job in filtered:
        status = str(job.get("status") or STATUS_NEW)
        # Les offres écartées automatiquement par le filtre métier (motif renseigné)
        # restent hors du tableau : la colonne « Rejeté » suit vos refus à vous.
        if status == STATUS_REJECTED and job.get("rejection_reason"):
            continue
        jobs_by_status.get(status, jobs_by_status[STATUS_NEW]).append(job)

    cols = st.columns(len(KANBAN_COLUMNS), gap="small")
    for col, status in zip(cols, KANBAN_COLUMNS):
        column_jobs = jobs_by_status[status]
        with col:
            st.markdown(_column_header(status, len(column_jobs)), unsafe_allow_html=True)
            if not column_jobs:
                st.markdown('<div class="sc-kanban-empty">Aucune offre</div>', unsafe_allow_html=True)
                continue
            visible = column_jobs if display_limit is None else column_jobs[:display_limit]
            for job in visible:
                _render_kanban_card(job, db)
            hidden = column_jobs[len(visible):]
            if hidden:
                with st.expander(f"Voir les {len(hidden)} autres offres"):
                    for job in hidden:
                        _render_kanban_card(job, db)


if __name__ == "__main__":
    main()
