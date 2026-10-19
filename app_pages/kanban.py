"""Page Candidatures : tableau Kanban de suivi, une colonne par statut."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import streamlit as st

from src.config import load_config
from src.constants import (
    REFUSAL_REASON,
    SOURCE_MANUAL,
    STATUS_APPLIED,
    STATUS_IGNORED,
    STATUS_INTERVIEW,
    STATUS_NEW,
    STATUS_REJECTED,
    VERDICT_LABELS,
)
from src.matching.cover_letter import CoverLetterGenerator
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


def _reject_status(db: Any, job_id: str) -> None:
    db.reject_job(job_id, REFUSAL_REASON)
    bump_data_version()


def _calculate_days_applied(applied_at_raw: Any) -> int:
    if not applied_at_raw:
        return 0
    if isinstance(applied_at_raw, str):
        try:
            dt = datetime.fromisoformat(applied_at_raw.replace("Z", "+00:00"))
        except Exception:
            return 0
    elif isinstance(applied_at_raw, datetime):
        dt = applied_at_raw
    else:
        return 0

    if dt.tzinfo is not None:
        now = datetime.now(dt.tzinfo)
    else:
        now = datetime.utcnow()
    return max(0, (now - dt).days)


@st.dialog("Préparation d'entretien & Relance", width="large")
def show_interview_and_followup_dialog(job: dict[str, Any]) -> None:
    """Modale de préparation d'entretien, de prise de notes et de génération de relance."""
    job_id = str(job.get("id"))
    title = str(job.get("title") or "Offre sans titre")
    company = str(job.get("company") or "Entreprise inconnue")

    st.markdown(f"### {_md(title)}")
    st.caption(f"**Entreprise :** {_md(company)}")

    tab_notes, tab_questions, tab_relance = st.tabs([
        "Notes personnelles",
        "Questions d'entretien",
        "Message de relance / LinkedIn",
    ])

    with tab_notes:
        current_notes = str(job.get("user_notes") or "")
        notes_val = st.text_area(
            "Notes sur l'entreprise, les contacts ou l'entretien",
            value=current_notes,
            height=160,
            key=f"user_notes_area_{job_id}",
            placeholder="Ex : Contact RH sur LinkedIn, questions posées lors du screening, points de vigilance...",
        )
        if st.button("Enregistrer les notes", type="primary", key=f"save_notes_btn_{job_id}", icon=":material/save:"):
            db = get_database()
            db.update_user_notes(job_id, notes_val)
            bump_data_version()
            st.toast("Notes enregistrées !")
            st.rerun()

    with tab_questions:
        st.markdown("#### Questions clés pour l'entretien")
        questions = job.get("questions_entretien") or []
        if isinstance(questions, str):
            try:
                import json
                questions = json.loads(questions)
            except Exception:
                questions = [questions]

        if questions:
            for q in questions:
                st.markdown(f"- **{_md(q)}**")
        else:
            techs = job.get("tech_stack") or []
            tech_str = ", ".join(techs[:3]) if techs else "la stack technique"
            st.info("Aucune question spécifique n'a été extraite par le juge LLM. Voici 3 questions d'ingénierie recommandées :")
            st.markdown(
                f"- *Comment structurez-vous vos pipelines de données et l'entraînement sous {tech_str} en production ?*\n"
                f"- *Quelles métriques de fiabilité et de robustesse suivez-vous pour valider vos modèles avant déploiement ?*\n"
                f"- *Comment est structurée l'équipe de recherche/ML et quel accompagnement technique est prévu pour le stagiaire PFE ?*"
            )

        if job.get("reasoning"):
            with st.expander("Raisonnement & analyse du juge", expanded=False):
                st.write(job.get("reasoning"))

        if job.get("match_reasons"):
            st.markdown("#### Signaux positifs identifiés")
            for reason in job.get("match_reasons"):
                st.markdown(f"- :green[✓] {_md(reason)}")

    with tab_relance:
        st.markdown("#### Générateur de relance & d'approche")
        channel_label = st.radio(
            "Canal de communication :",
            options=["Email de relance (J+14)", "Note d'approche LinkedIn (< 300 car.)"],
            index=0,
            key=f"relance_channel_{job_id}",
        )
        channel = "linkedin" if "LinkedIn" in channel_label else "email"

        applied_at = job.get("applied_at")
        days_applied = _calculate_days_applied(applied_at) if applied_at else 14
        if days_applied <= 0:
            days_applied = 14

        generator = CoverLetterGenerator()
        msg = generator.generate_followup_message(job, days_since_applied=days_applied, channel=channel)

        st.text_area("Texte prêt à envoyer / copier :", value=msg, height=220, key=f"followup_msg_txt_{job_id}")
        st.caption("Vous pouvez copier le bloc ci-dessous en un clic :")
        st.code(msg, language=None)


def _render_card(job: dict[str, Any], db: Any, current_status: str) -> None:
    job_id = str(job["id"])
    score = effective_score(job)
    align_label, tone = score_alignment(score)
    url = str(job.get("url") or "")
    location = str(job.get("location") or "")

    with st.container(border=True):
        st.markdown(f"**{_md(job.get('title') or 'Offre sans titre')}**")
        st.caption(_md(job.get("company") or "Entreprise inconnue") + (f" · {_md(location)}" if location else ""))

        user_notes = (job.get("user_notes") or "").strip()
        if user_notes:
            short_notes = user_notes[:80] + ("…" if len(user_notes) > 80 else "")
            st.caption(f"Note : {_md(short_notes)}")

        badges = [f":{_BADGE_COLORS[tone]}-badge[{score:.0f} · {align_label}]"]
        verdict = job.get("verdict")
        if is_reranked(job) and verdict:
            color = _BADGE_COLORS.get(VERDICT_TONES.get(verdict, "mute"), "gray")
            badges.append(f":{color}-badge[{VERDICT_LABELS.get(verdict, verdict)}]")

        applied_at = job.get("applied_at")
        if applied_at and current_status in (STATUS_APPLIED, STATUS_INTERVIEW):
            days_applied = _calculate_days_applied(applied_at)
            cfg_auto = load_config().get("automations", {})
            followup_active = bool(cfg_auto.get("followup_reminders_enabled", True))
            followup_thresh = int(cfg_auto.get("followup_days_threshold", 14))
            if current_status == STATUS_APPLIED and followup_active and days_applied >= followup_thresh:
                badges.append(f":orange-badge[À relancer · J+{days_applied}]")
            else:
                badges.append(f":gray-badge[Postulé il y a {days_applied} j]")

        st.markdown(" ".join(badges))

        with st.popover("Actions", icon=":material/more_horiz:", width="stretch"):
            if url.startswith(("http://", "https://")):
                st.link_button("Ouvrir l'offre", url, icon=":material/open_in_new:", width="stretch")
            if st.button("Lettre de motivation", key=f"kb_letter_{job_id}", icon=":material/edit_note:", width="stretch"):
                show_cover_letter_dialog(job)
            if st.button("Entretien & Relance", key=f"kb_prep_{job_id}", icon=":material/contact_support:", width="stretch"):
                show_interview_and_followup_dialog(job)
            for status, label in KANBAN_COLUMNS:
                if status == current_status:
                    continue
                if status == STATUS_REJECTED:
                    st.button(
                        f"Déplacer vers {label.lower()}",
                        key=f"kb_move_{status}_{job_id}",
                        on_click=_reject_status,
                        args=(db, job_id),
                        width="stretch",
                    )
                else:
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
