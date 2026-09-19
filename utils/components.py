from __future__ import annotations
import streamlit as st
from typing import Any, Sequence, Mapping
import html
from utils.data import *
from utils.data import _esc, _set_status

from src.constants import *
from src.storage.database import Database
from src.matching.cover_letter import CoverLetterGenerator

# Fragments HTML (typographie et badges, aucun emoji décoratif)
# --------------------------------------------------------------------------- #

def _badge(label: str, tone: str | None = None, dot: str | None = None, extra_cls: str = "") -> str:
    """Pill badge de métadonnée, avec pastille colorée optionnelle."""
    classes = f"sc-badge {tone_class(tone)}" if tone else "sc-badge"
    if extra_cls:
        classes += f" {extra_cls}"
    marker = f'<i class="sc-dot" style="background:{dot}"></i>' if dot else ""
    return f'<span class="{classes}">{marker}{_esc(label)}</span>'


def score_html(job: dict[str, Any]) -> str:
    """Jauge de score épurée : « 84 / 100 » + libellé d'alignement."""
    score = effective_score(job)
    label, tone = score_alignment(score)
    origin = "rerank LLM" if is_reranked(job) else "score hybride"
    return (
        f'<div class="sc-score-box {tone_class(tone)}">'
        f'<span class="sc-score" title="Score R&D ({origin})"><b>{score:.0f}</b><span>/100</span></span>'
        f'<span class="sc-align">{_esc(label)}</span>'
        f"</div>"
    )


def _meta_html(job: dict[str, Any]) -> str:
    """Sous-titre : entreprise · ville · date relative · statut de candidature."""
    status = job.get("status")
    status_cls = f"sc-status-{status.lower()}" if status else ""
    parts = [f'<span class="sc-company">{_esc(job.get("company"))}</span>']
    if job.get("location"):
        parts.append(f'<span class="sc-meta-item">{_esc(job["location"])}</span>')
    parts.append(f'<span class="sc-meta-item">{_esc(relative_date(job.get("created_at")))}</span>')
    parts.append(
        f'<span class="sc-status {status_cls}">'
        f'<i class="sc-dot"></i>{_esc(STATUS_LABELS.get(status, status or "Inconnu"))}</span>'
    )
    separator = '<span class="sc-sep">·</span>'
    return f'<div class="sc-card-meta">{separator.join(parts)}</div>'


def _badges_html(job: dict[str, Any]) -> str:
    """Ligne de badges : plateforme, contrat, typologie d'entreprise, verdict LLM."""
    badges = [_badge(source_label(job.get("source")), dot=source_color(job.get("source")))]
    contract = contract_label(job)
    if contract:
        badges.append(_badge(contract))
    tier = job.get("company_tier")
    if tier in (TIER_1, TIER_ESN):
        badges.append(_badge(TIER_LABELS.get(tier, str(tier)), "positive" if tier == TIER_1 else "alert"))
    if is_reranked(job):
        verdict = job.get("verdict")
        extra = f"sc-badge-verdict sc-badge-{verdict.lower()}" if verdict else ""
        badges.append(_badge(VERDICT_LABELS.get(verdict, verdict or "Évalué"), VERDICT_TONES.get(verdict, "mute"), extra_cls=extra))
    return f'<div class="sc-badges">{"".join(badges)}</div>'


def _chips_html(technologies: Sequence[str]) -> str:
    """Chips de technologies détectées (police monospace compacte)."""
    if not technologies:
        return ""
    chips = "".join(f'<span class="sc-chip">{_esc(item)}</span>' for item in technologies)
    return f'<div class="sc-chips">{chips}</div>'


def clean_text(value: Any) -> str:
    """Compacte les espaces et sauts de ligne d'une fiche de poste."""
    text = re.sub(r"[\t\r ]+", " ", str(value or ""))
    return re.sub(r"\n{2,}", "\n", text).strip()


def excerpt(text: str, length: int = EXCERPT_LENGTH) -> tuple[str, bool]:
    """Extrait tronqué sur un mot + indicateur de troncature."""
    if len(text) <= length:
        return text, False
    cut = text[:length]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,;:.") + " …", True


def _verdict_block(job: dict[str, Any]) -> str:
    """Synthèse du juge LLM : verdict, points forts, points d'attention."""
    if not is_reranked(job):
        return (
            '<div class="sc-block"><div class="sc-section">Verdict du juge LLM</div>'
            '<p class="sc-excerpt">Offre non évaluée à ce stade : lancez <code>python run_pipeline.py</code> '
            "pour déclencher le reranking (Top-N configuré dans <code>config.yaml</code>).</p></div>"
        )
    verdict = job.get("verdict")
    tone = VERDICT_TONES.get(verdict, "mute")
    badges = (
        f'<div class="sc-badges {tone_class(tone)}">'
        f'{_badge(VERDICT_LABELS.get(verdict, verdict or "Évalué"), tone)}'
        f'{_badge(f"Rerank {effective_score(job):.0f}/100")}'
        f"</div>"
    )
    strengths = [str(item) for item in (job.get("match_reasons") or [])]
    flags = [str(item) for item in (job.get("red_flags") or [])]
    strong_list = (
        f'<ul class="sc-list {tone_class("positive")}">'
        + "".join(f"<li>{_esc(item)}</li>" for item in strengths)
        + "</ul>"
        if strengths
        else '<p class="sc-excerpt">—</p>'
    )
    flag_list = (
        f'<ul class="sc-list {tone_class("alert")}">'
        + "".join(f"<li>{_esc(item)}</li>" for item in flags)
        + "</ul>"
        if flags
        else '<p class="sc-excerpt">Aucun point de vigilance signalé.</p>'
    )
    return (
        '<div class="sc-block"><div class="sc-section">Verdict du juge LLM</div>'
        f"{badges}"
        '<div class="sc-section sc-section--sub">Points forts</div>'
        f"{strong_list}"
        '<div class="sc-section sc-section--sub">Points d\'attention</div>'
        f"{flag_list}</div>"
    )


# Icônes de la grille d'évaluation (sous-scores) — l'emoji est confiné à l'UI.
SUB_SCORE_ICONS = {
    "modeling_depth": "📐",
    "mentorship_team": "👥",
    "engineering_practice": "⚙️",
    "option_value": "🎓",
    "logistics": "📅",
    # Rétro-compatibilité :
    "career_leverage": "🚀",
    "pfe_compatibility": "📅",
}


def _subscore_tone(value: int) -> str:
    """Tonalité d'un sous-score (5 = excellent → 1 = bloquant)."""
    return {5: "positive", 4: "accent", 3: "mute", 2: "warn"}.get(value, "alert")


def _hard_cap_banner(job: dict[str, Any]) -> str:
    """Bandeau d'alerte bien visible lorsqu'un verrou bloquant a été déclenché.

    Placé en tête de carte (juste sous l'en-tête) : le score plafonné par un hard
    cap doit se voir immédiatement, sans ouvrir l'accordéon.
    """
    reason = (job.get("hard_cap_triggered") or "").strip()
    if not reason:
        return ""
    return (
        f'<div class="sc-alert {tone_class("alert")}">'
        '<span class="sc-alert-icon">⚠️</span>'
        f"<span><b>Verrou bloquant</b> — {_esc(reason)} : score plafonné, "
        "candidature à écarter ou à vérifier avant tout effort.</span></div>"
    )


def _subscores_strip(job: dict[str, Any]) -> str:
    """Mini-indicateurs compacts des sous-scores, visibles SANS ouvrir l'accordéon."""
    if not is_reranked(job):
        return ""
    sub = job.get("sub_scores") or {}
    if not sub:
        return ""
    keys_to_show = [k for k in SUB_SCORE_KEYS if k in sub] + [
        k for k in sub if k not in SUB_SCORE_KEYS and k in SUB_SCORE_LABELS
    ]
    if not keys_to_show:
        keys_to_show = list(SUB_SCORE_KEYS)
    separator = '<span class="sc-sep">|</span>'
    items = [
        f'<span class="sc-subscore-item {tone_class(_subscore_tone(coerce_sub_score(sub.get(key))))}">'
        f"{SUB_SCORE_ICONS.get(key, '•')} {_esc(SUB_SCORE_SHORT_LABELS.get(key, key))} : "
        f"<b>{coerce_sub_score(sub.get(key))}/5</b></span>"
        for key in keys_to_show
    ]
    return f'<div class="sc-subscore-strip">{separator.join(items)}</div>'


def _subscores_block(job: dict[str, Any]) -> str:
    """Grille d'évaluation détaillée (accordéon) : sous-scores /5 + verrou bloquant."""
    if not is_reranked(job):
        return ""
    sub = job.get("sub_scores") or {}
    if not sub:
        return ""
    keys_to_show = [k for k in SUB_SCORE_KEYS if k in sub] + [
        k for k in sub if k not in SUB_SCORE_KEYS and k in SUB_SCORE_LABELS
    ]
    if not keys_to_show:
        keys_to_show = list(SUB_SCORE_KEYS)
    badges = "".join(
        f'<span class="sc-badge sc-subscore {tone_class(_subscore_tone(coerce_sub_score(sub.get(key))))}">'
        f"{SUB_SCORE_ICONS.get(key, '•')} {_esc(SUB_SCORE_LABELS.get(key, key))} {coerce_sub_score(sub.get(key))}/5</span>"
        for key in keys_to_show
    )
    hard_cap = (job.get("hard_cap_triggered") or "").strip()
    cap_html = (
        f'<div class="sc-badges"><span class="sc-badge {tone_class("alert")}">'
        f"Verrou bloquant : {_esc(hard_cap)}</span></div>"
        if hard_cap
        else ""
    )
    return (
        '<div class="sc-block"><div class="sc-section">Grille d\'évaluation</div>'
        f'<div class="sc-badges">{badges}</div>{cap_html}</div>'
    )


def _reasoning_block(job: dict[str, Any]) -> str:
    """Raisonnement du juge, produit AVANT le score et conservé en base.

    C'est la justification de la décision : elle rend le score auditable (on voit
    ce qui a été constaté sur le calendrier, la mission réelle et l'encadrement).
    """
    text = clean_text(job.get("reasoning"))
    if not is_reranked(job) or not text:
        return ""
    return (
        '<div class="sc-block"><div class="sc-section">Analyse du juge (raisonnement)</div>'
        f'<p class="sc-excerpt">{_esc(text)}</p></div>'
    )


def _scores_block(job: dict[str, Any]) -> str:
    """Détail des scores internes (score R&D, typologie)."""
    origin = "Gemini" if is_reranked(job) else "historique"
    entries = (
        ("Score R&D", f"{effective_score(job):.0f}/100 ({origin})"),
        ("Typologie", TIER_LABELS.get(job.get("company_tier"), "—")),
    )
    cells = "".join(f"<div>{_esc(label)} <b>{_esc(value)}</b></div>" for label, value in entries)
    return f'<div class="sc-block"><div class="sc-section">Scores</div><div class="sc-kv">{cells}</div></div>'


def _description_block(job: dict[str, Any]) -> str:
    """Extrait de la fiche de poste, avec lecture complète à la demande."""
    text = clean_text(job.get("description"))
    header = '<div class="sc-block"><div class="sc-section">Fiche de poste</div>'
    if not text:
        return header + '<p class="sc-excerpt">Fiche non fournie par la plateforme d\'origine.</p></div>'
    short, truncated = excerpt(text)
    more = (
        '<details class="sc-more"><summary>Lire la fiche complète</summary>'
        f'<p class="sc-excerpt">{_esc(text)}</p></details>'
        if truncated
        else ""
    )
    return f'{header}<p class="sc-excerpt">{_esc(short)}</p>{more}</div>'


def _rejection_block(job: dict[str, Any]) -> str:
    """Motif d'exclusion métier, affiché uniquement pour les offres écartées."""
    reason = (job.get("rejection_reason") or "").strip()
    if not reason:
        return ""
    return (
        '<div class="sc-block"><div class="sc-section">Écartée par le filtre métier</div>'
        f'<p class="sc-excerpt">{_esc(reason)}</p></div>'
    )


def job_card_html(job: dict[str, Any], keywords: Sequence[str]) -> str:
    """Carte d'offre autonome : en-tête, jauge de score, badges, accordéon, CTA."""
    url = str(job.get("url") or "")
    technologies = detected_technologies(job, keywords)
    head = (
        '<div class="sc-card">'
        '<div class="sc-card-head">'
        f'<div><h3 class="sc-card-title">{_esc(job.get("title"))}</h3>{_meta_html(job)}</div>'
        f"{score_html(job)}"
        "</div>"
        f"{_hard_cap_banner(job)}"
        f"{_badges_html(job)}"
        f"{_subscores_strip(job)}"
        f"{_chips_html(technologies)}"
        '<details class="sc-details">'
        '<summary><span class="sc-chev">›</span>Détails &amp; évaluation</summary>'
        f'<div class="sc-details-body">{_rejection_block(job)}{_verdict_block(job)}{_reasoning_block(job)}{_subscores_block(job)}{_scores_block(job)}{_description_block(job)}</div>'
        "</details>"
        "</div>"
    )
    return head

@st.dialog("Lettre de motivation personnalisée", width="large")
def show_cover_letter_dialog(job: dict[str, Any]) -> None:
    """Boîte de dialogue modale affichant la lettre de motivation rédigée par Gemini."""
    job_id = str(job.get("id"))
    title = str(job.get("title") or "Offre sans titre")
    company = str(job.get("company") or "Entreprise")

    st.markdown(f"**Poste :** {_esc(title)} — **{_esc(company)}**")
    st.caption("Rédigée sur mesure par Gemini à partir de votre profil `data/cv_eddy.txt`.")

    session_key = f"cover_letter_{job_id}"
    if session_key not in st.session_state:
        with st.spinner("Rédaction de la lettre en cours par Gemini..."):
            generator = CoverLetterGenerator()
            st.session_state[session_key] = generator.generate(job)

    letter_text = st.session_state.get(session_key, "")

    edited = st.text_area(
        "Brouillon de la lettre (éditable directement avant envoi) :",
        value=letter_text,
        height=380,
        key=f"editor_{session_key}",
    )

    safe_company = "".join(c for c in company if c.isalnum() or c in ("-", "_")).strip() or "Entreprise"
    c1, c2, c3 = st.columns([1, 1, 1], gap="small")
    with c1:
        st.download_button(
            "Télécharger (.txt)",
            data=edited,
            file_name=f"Lettre_Motivation_{safe_company}.txt",
            mime="text/plain",
            use_container_width=True,
            icon=":material/download:",
        )
    with c2:
        st.download_button(
            "Télécharger (.md)",
            data=edited,
            file_name=f"Lettre_Motivation_{safe_company}.md",
            mime="text/markdown",
            use_container_width=True,
            icon=":material/download:",
        )
    with c3:
        if st.button("Régénérer", key=f"regen_{job_id}", use_container_width=True, icon=":material/refresh:"):
            with st.spinner("Nouvelle rédaction en cours..."):
                generator = CoverLetterGenerator()
                st.session_state[session_key] = generator.generate(job)
                st.rerun()


def render_job_card(db: Database, job: dict[str, Any], keywords: Sequence[str]) -> None:
    """Rend une carte d'offre unifiée suivie de sa barre d'actions de candidature."""
    with st.container(border=True):
        st.markdown(job_card_html(job, keywords), unsafe_allow_html=True)
        st.markdown('<div class="sc-card-actions-divider"></div>', unsafe_allow_html=True)
        status = job.get("status")
        job_id = str(job.get("id"))
        url = str(job.get("url") or "")

        if status == STATUS_APPLIED:
            actions: tuple[tuple[str, str], ...] = (
                ("Entretien obtenu", STATUS_INTERVIEW),
                ("Archiver", STATUS_IGNORED),
            )
        elif status in (STATUS_INTERVIEW, STATUS_IGNORED):
            actions = (("Rétablir au flux", STATUS_NEW),)
        else:
            actions = (("Marquer postulé", STATUS_APPLIED), ("Archiver", STATUS_IGNORED))

        if len(actions) == 2:
            cols = st.columns([1.3, 2.5, 1.1, 1.4, 1.1], gap="small")
            with cols[0]:
                if url.startswith("http"):
                    st.link_button("Postuler ↗", url, type="primary", use_container_width=True)
            # cols[1] sert d'espaceur pour pousser les boutons secondaires à droite
            with cols[2]:
                if st.button("Lettre", key=f"letter-{job_id}", use_container_width=True, icon=":material/edit_note:"):
                    show_cover_letter_dialog(job)
            with cols[3]:
                st.button(
                    actions[0][0],
                    key=f"status-{actions[0][1]}-{job_id}",
                    on_click=_set_status,
                    args=(db, job_id, actions[0][1]),
                    use_container_width=True,
                )
            with cols[4]:
                st.button(
                    actions[1][0],
                    key=f"status-{actions[1][1]}-{job_id}",
                    on_click=_set_status,
                    args=(db, job_id, actions[1][1]),
                    use_container_width=True,
                )
        else:
            cols = st.columns([1.3, 3.8, 1.1, 1.4], gap="small")
            with cols[0]:
                if url.startswith("http"):
                    st.link_button("Postuler ↗", url, type="primary", use_container_width=True)
            with cols[2]:
                if st.button("Lettre", key=f"letter-{job_id}", use_container_width=True, icon=":material/edit_note:"):
                    show_cover_letter_dialog(job)
            with cols[3]:
                st.button(
                    actions[0][0],
                    key=f"status-{actions[0][1]}-{job_id}",
                    on_click=_set_status,
                    args=(db, job_id, actions[0][1]),
                    use_container_width=True,
                )


def render_stream(
    db: Database,
    jobs: list[dict[str, Any]],
    filters: Filters,
    keywords: Sequence[str],
) -> None:
    """Rend le flux d'offres, en vue unifiée ou groupée par plateforme."""
    if not jobs:
        st.markdown(
            '<div class="sc-empty">Aucune offre ne correspond aux filtres courants.<br>'
            "Relancez la collecte (<code>python run_pipeline.py</code>) ou élargissez les critères.</div>",
            unsafe_allow_html=True,
        )
        return
    note = "tri par score R&D décroissant"
    note += " · filtres actifs" if not filters.is_default() else " · aucun filtre"
    st.markdown(
        f'<div class="sc-stream"><span class="sc-stream-count">{len(jobs)} offre(s)</span>'
        f'<span class="sc-stream-note">{_esc(note)}</span></div>',
        unsafe_allow_html=True,
    )
    if filters.group_by_source:
        for label, group in group_jobs_by_source(jobs):
            st.markdown(
                f'<div class="sc-group"><span class="sc-group-name">{_esc(label)}</span>'
                f'<span class="sc-group-count">{len(group)} offre(s)</span></div>',
                unsafe_allow_html=True,
            )
            for job in group:
                render_job_card(db, job, keywords)
    else:
        for job in jobs:
            render_job_card(db, job, keywords)


# --------------------------------------------------------------------------- #
# Bandeau KPI & en-tête
# --------------------------------------------------------------------------- #
def _inline_relative(value: Any) -> str:
    """Date relative insérable au fil d'une phrase (« il y a 3 h »)."""
    label = relative_date(value)
    return label[0].lower() + label[1:] if label else label


def render_header(jobs: list[dict[str, Any]], config: dict[str, Any]) -> None:
    """En-tête : identité du poste, volumétrie et chaîne de traitement courante."""
    llm_model = config.get("llm", {}).get("model", "Gemini 2.0 Flash")
    stamps = [stamp for stamp in (parse_timestamp(job.get("created_at")) for job in jobs) if stamp]
    chain = f"scoring & reranking 100% LLM (<code>{_esc(llm_model)}</code>)"
    st.markdown(
        '<div class="sc-eyebrow">Veille stages R&amp;D · Data Science / Machine Learning</div>'
        '<div class="sc-title">Stage Copilot</div>'
        f'<div class="sc-subtitle">{len(jobs)} offres en base · dernière collecte '
        f"{_esc(_inline_relative(max(stamps)) if stamps else 'inconnue')} · {chain}</div>",
        unsafe_allow_html=True,
    )


def render_kpis(
    jobs: list[dict[str, Any]],
    llm_model: str,
    base_total: int,
    filters_active: bool,
) -> None:
    """Bandeau KPI : volume actif, offres qualifiées, couverture LLM, plateformes."""
    active = [
        job
        for job in jobs
        if job.get("status") not in (STATUS_IGNORED, STATUS_REJECTED)
    ]
    qualified = sum(1 for job in active if effective_score(job) >= QUALIFIED_SCORE)
    ranked = sum(1 for job in active if is_reranked(job))
    base = max(len(active), 1)
    distribution = source_distribution(active)
    bars = "".join(
        f'<i style="width:{count / base * 100:.2f}%;background:{color}"></i>'
        for _, count, color in distribution
    )
    legend = "".join(
        f'<span><i style="background:{color}"></i>{_esc(label)} <b>{count}</b></span>'
        for label, count, color in distribution
    ) or '<span>Aucune offre sur la sélection</span>'
    scope = f"sur {base_total} en base" if filters_active else "hors offres archivées"
    st.markdown(
        '<div class="sc-kpis">'
        '<div class="sc-kpi"><div class="sc-kpi-label">Offres actives</div>'
        f'<div class="sc-kpi-value">{len(active)}<span>{_esc(scope)}</span></div>'
        f'<div class="sc-kpi-hint">{qualified / base * 100:.0f} % qualifiées R&amp;D</div></div>'
        '<div class="sc-kpi"><div class="sc-kpi-label">Offres R&amp;D qualifiées</div>'
        f'<div class="sc-kpi-value">{qualified}<span>score ≥ {QUALIFIED_SCORE:.0f}</span></div>'
        f'<div class="sc-kpi-hint">{qualified} sur {len(active)} offres actives</div></div>'
        '<div class="sc-kpi"><div class="sc-kpi-label">Rerankées par le LLM</div>'
        f'<div class="sc-kpi-value">{ranked}<span>{_esc(llm_model)}</span></div>'
        f'<div class="sc-kpi-hint">{ranked / base * 100:.0f} % du flux couvert</div></div>'
        '<div class="sc-kpi"><div class="sc-kpi-label">Répartition par plateforme</div>'
        f'<div class="sc-dist">{bars}</div>'
        f'<div class="sc-legend">{legend}</div></div>'
        "</div>",
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------- #
# Barre latérale : filtres compacts, affichage, maintenance de la base
# --------------------------------------------------------------------------- #
def render_sidebar_filters(jobs: list[dict[str, Any]], sources: Sequence[str]) -> Filters:
    """Barre latérale de filtres compacts ; retourne les critères courants."""
    counts: dict[str, int] = {}
    for job in jobs:
        source = job.get("source")
        if source:
            counts[source] = counts.get(source, 0) + 1

    with st.sidebar:
        from utils.task_manager import render_sidebar_task_badge
        render_sidebar_task_badge()

        st.markdown('<div class="sc-eyebrow">Filtres</div>', unsafe_allow_html=True)
        query = st.text_input(
            "Recherche",
            placeholder="Titre, entreprise, techno…",
            icon=":material/search:",
            help="Plein texte (ET logique) sur le titre, l'entreprise, la ville, "
            "la fiche de poste et les technologies. Validez avec Entrée.",
        )
        selected_sources = st.multiselect(
            "Plateformes",
            options=list(sources),
            default=list(sources),
            format_func=lambda source: f"{source_label(source)} ({counts.get(source, 0)})",
        )
        min_score = st.slider(
            "Score R&D minimal",
            0,
            100,
            0,
            5,
            help="Score effectif : rerank du juge LLM s'il existe, sinon score hybride du bi-encoder.",
        )
        llm_only = st.toggle(
            "Verdict LLM uniquement",
            help="Ne conserver que les offres déjà évaluées par le juge LLM (étape 2).",
        )
        hide_processed = st.toggle(
            "Masquer les offres traitées",
            help="Prioritaire sur le filtre de statut : ne conserve que les offres au statut NOUVEAU.",
        )

        with st.expander("Critères avancés"):
            statuses = st.multiselect(
                "Statut de candidature",
                options=STATUS_OPTIONS,
                default=STATUS_ORDER,
                format_func=lambda status: STATUS_LABELS.get(status, status),
                disabled=hide_processed,
            )
            tiers = st.multiselect(
                "Typologie d'entreprise",
                options=list(TIER_LABELS.keys()),
                default=list(TIER_LABELS.keys()),
                format_func=lambda tier: TIER_LABELS[tier],
            )
            exclude_esn = st.toggle(
                "Exclure les ESN",
                help="Retire les ESN / SSII du flux (filtre également appliqué en amont si configuré).",
            )

        with st.expander("Affichage"):
            display_mode = st.selectbox("Mode de flux", options=[DISPLAY_FLAT, DISPLAY_GROUPED])
            page_size = st.selectbox(
                "Offres affichées",
                options=list(PAGE_SIZES),
                index=1,
                help="Limite le nombre de cartes rendues pour garder l'interface fluide.",
            )


    limit = None if page_size == PAGE_SIZE_ALL else int(page_size)
    return Filters(
        query=query or "",
        sources=tuple(selected_sources),
        statuses=tuple(statuses),
        tiers=tuple(tiers),
        min_score=float(min_score),
        llm_only=llm_only,
        hide_processed=hide_processed,
        exclude_esn=exclude_esn,
        limit=limit,
        group_by_source=display_mode == DISPLAY_GROUPED,
    )


