from __future__ import annotations
import streamlit as st
from typing import Any, Sequence, Mapping
import html
import json
from utils.data import *
from utils.data import _esc, _set_status_and_advance
from utils.layout import page_header_html

from src.constants import *
from src.storage.database import Database
from src.matching.cover_letter import CoverLetterGenerator
from src.matching.pdf_exporter import generate_cover_letter_pdf

# Fragments HTML (typographie et badges, aucun emoji décoratif)
# --------------------------------------------------------------------------- #

def _badge(label: str, tone: str | None = None, dot: str | None = None, extra_cls: str = "") -> str:
    """Pill badge de métadonnée, avec pastille colorée optionnelle."""
    classes = f"sc-badge {tone_class(tone)}" if tone else "sc-badge"
    if extra_cls:
        classes += f" {extra_cls}"
    marker = f'<i class="sc-dot" style="background:{dot}"></i>' if dot else ""
    return f'<span class="{classes}">{marker}{_esc(label)}</span>'


def score_html(job: dict[str, Any], small: bool = False) -> str:
    """Jauge de score : pastille « 84/100 », mini-barre 0–100 et palier d'alignement."""
    score = effective_score(job)
    label, tone = score_alignment(score)
    origin = "rerank LLM" if is_reranked(job) else "score hybride"
    width = max(0.0, min(100.0, score))
    size = " sc-score-sm" if small else ""
    return (
        f'<div class="sc-score-box{size} {tone_class(tone)}">'
        f'<span class="sc-score" title="Score R&amp;D ({origin})"><b>{score:.0f}</b><span>/100</span></span>'
        f'<span class="sc-score-bar"><i style="width:{width:.0f}%"></i></span>'
        f'<span class="sc-align">{_esc(label)}</span>'
        f"</div>"
    )


def _status_html(status: str | None) -> str:
    """Statut de candidature : pastille colorée + libellé."""
    tone = STATUS_TONES.get(status, "mute")
    return (
        f'<span class="sc-status {tone_class(tone)}"><i class="sc-dot"></i>'
        f'{_esc(STATUS_LABELS.get(status, status or "Inconnu"))}</span>'
    )


def _meta_html(job: dict[str, Any]) -> str:
    """Sous-titre : entreprise · ville · date relative · statut de candidature."""
    parts = [f'<span class="sc-company">{_esc(job.get("company"))}</span>']
    if job.get("location"):
        parts.append(f'<span class="sc-meta-item">{_esc(job["location"])}</span>')
    parts.append(f'<span class="sc-meta-item">{_esc(relative_date(job.get("created_at")))}</span>')
    parts.append(_status_html(job.get("status")))
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


def _subscore_tone(value: int) -> str:
    """Tonalité d'un sous-score (5 = excellent → 1 = bloquant)."""
    return {5: "positive", 4: "accent", 3: "mute", 2: "warn"}.get(value, "alert")


def _meter_html(value: int) -> str:
    """Jauge à 5 segments suivie de la note « n/5 »."""
    segments = "".join('<i class="on"></i>' if index < value else "<i></i>" for index in range(5))
    return f'<span class="sc-meter"><span class="sc-meter-seg">{segments}</span><b>{value}/5</b></span>'


def _subscore_keys(sub: Mapping[str, Any]) -> list[str]:
    """Sous-critères à afficher : grille courante d'abord, puis clés historiques connues."""
    keys = [k for k in SUB_SCORE_KEYS if k in sub] + [
        k for k in sub if k not in SUB_SCORE_KEYS and k in SUB_SCORE_LABELS
    ]
    return keys or list(SUB_SCORE_KEYS)


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
        '<span class="sc-alert-icon">!</span>'
        f"<span><b>Verrou bloquant</b> — {_esc(reason)} : score plafonné, "
        "candidature à écarter ou à vérifier avant tout effort.</span></div>"
    )


def _subscore_items(job: dict[str, Any], labels: Mapping[str, str]) -> str:
    """Lignes « libellé + jauge » des sous-scores, avec le libellé choisi."""
    sub = job.get("sub_scores") or {}
    items = []
    for key in _subscore_keys(sub):
        value = coerce_sub_score(sub.get(key))
        items.append(
            f'<div class="sc-subscore-item {tone_class(_subscore_tone(value))}">'
            f'<span>{_esc(labels.get(key, key))}</span>{_meter_html(value)}</div>'
        )
    return "".join(items)


def _subscores_strip(job: dict[str, Any]) -> str:
    """Mini-jauges compactes des sous-scores, visibles SANS ouvrir l'accordéon."""
    if not is_reranked(job) or not job.get("sub_scores"):
        return ""
    return f'<div class="sc-subscore-strip">{_subscore_items(job, SUB_SCORE_SHORT_LABELS)}</div>'


def _subscores_block(job: dict[str, Any]) -> str:
    """Grille d'évaluation détaillée (accordéon) : sous-scores /5 + verrou bloquant."""
    if not is_reranked(job) or not job.get("sub_scores"):
        return ""
    hard_cap = (job.get("hard_cap_triggered") or "").strip()
    cap_html = (
        f'<div class="sc-badges"><span class="sc-badge {tone_class("alert")}">'
        f"Verrou bloquant : {_esc(hard_cap)}</span></div>"
        if hard_cap
        else ""
    )
    return (
        '<div class="sc-block"><div class="sc-section">Grille d\'évaluation</div>'
        f'<div class="sc-subscore-grid">{_subscore_items(job, SUB_SCORE_LABELS)}</div>{cap_html}</div>'
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


def job_card_html(job: dict[str, Any], keywords: Sequence[str], compact: bool = False) -> str:
    """Carte d'offre autonome : en-tête, jauge de score, badges, accordéon, CTA."""
    url = str(job.get("url") or "")
    technologies = detected_technologies(job, keywords)
    html = (
        '<div class="sc-card">'
        '<div class="sc-card-head">'
        f'<div><h3 class="sc-card-title">{_esc(job.get("title"))}</h3>{_meta_html(job)}</div>'
        f"{score_html(job)}"
        "</div>"
        f"{_hard_cap_banner(job)}"
        f"{_badges_html(job)}"
        f"{_subscores_strip(job)}"
        f"{_chips_html(technologies)}"
    )
    if not compact:
        html += (
            '<details class="sc-details">'
            '<summary><span class="sc-chev">▶</span>Détails &amp; évaluation</summary>'
            f'<div class="sc-details-body">{_rejection_block(job)}{_verdict_block(job)}{_reasoning_block(job)}{_subscores_block(job)}{_scores_block(job)}{_description_block(job)}</div>'
            "</details>"
        )
    html += "</div>"
    return html

def job_list_item_html(job: dict[str, Any]) -> str:
    """Élément compact de la liste d'offres : titre, contexte, score, verdict."""
    meta = [f"<b>{_esc(job.get('company') or 'Entreprise inconnue')}</b>"]
    if job.get("location"):
        meta.append(_esc(job["location"]))
    meta.append(_esc(relative_date(job.get("created_at"))))
    tags = [_badge(source_label(job.get("source")), dot=source_color(job.get("source")))]
    if is_reranked(job):
        verdict = job.get("verdict")
        tags.append(_badge(VERDICT_LABELS.get(verdict, verdict or "Évalué"), VERDICT_TONES.get(verdict, "mute")))
    if job.get("hard_cap_triggered"):
        tags.append(_badge("Verrou bloquant", "alert"))
    status = job.get("status")
    if status and status != STATUS_NEW:
        tags.append(_badge(STATUS_LABELS.get(status, status), STATUS_TONES.get(status, "mute")))
    return (
        '<div class="sc-list-item">'
        '<div class="sc-list-main">'
        f'<div class="sc-list-title">{_esc(job.get("title") or "Offre sans titre")}</div>'
        f'<div class="sc-list-meta">{" · ".join(meta)}</div>'
        f'<div class="sc-list-tags">{"".join(tags)}</div>'
        "</div>"
        f"{score_html(job, small=True)}"
        "</div>"
    )


# --------------------------------------------------------------------------- #
# Lettre de motivation (boîte de dialogue)
# --------------------------------------------------------------------------- #
LETTER_SOURCES = {
    "gemini": ("Rédigée par Gemini", "accent"),
    "deepseek": ("Relais DeepSeek V3", "warn"),
    "fallback": ("Mode secours algorithmique", "mute"),
}


def _copy_button_html(text: str, button_id: str) -> str:
    """Bouton « Copier » : copie le brouillon affiché (ou, à défaut, la dernière version)."""
    payload = json.dumps(text).replace("</", "<\\/")
    return f"""
<button id="{button_id}" class="sc-copy-btn" type="button">Copier la lettre</button>
<script>
(function () {{
  const btn = document.getElementById("{button_id}");
  if (!btn || btn.dataset.bound) return;
  btn.dataset.bound = "1";
  const saved = {payload};
  const done = () => {{
    btn.classList.add("is-done");
    btn.textContent = "Copiée";
    setTimeout(() => {{ btn.classList.remove("is-done"); btn.textContent = "Copier la lettre"; }}, 2000);
  }};
  btn.addEventListener("click", () => {{
    const area = document.querySelector('textarea[aria-label^="Brouillon"]');
    const text = (area && area.value) || saved;
    const legacy = () => {{
      const el = document.createElement("textarea");
      el.value = text;
      el.setAttribute("readonly", "");
      el.style.position = "fixed";
      el.style.left = "-9999px";
      document.body.appendChild(el);
      el.select();
      document.execCommand("copy");
      document.body.removeChild(el);
      done();
    }};
    if (navigator.clipboard && navigator.clipboard.writeText) {{
      navigator.clipboard.writeText(text).then(done).catch(legacy);
    }} else {{
      legacy();
    }}
  }});
}})();
</script>
"""


def _store_letter(job_id: str, letter: str, source: str | None) -> None:
    """Mémorise une nouvelle version de la lettre (brouillon éditable inclus)."""
    session_key = f"cover_letter_{job_id}"
    st.session_state[session_key] = letter
    st.session_state[f"cover_letter_source_{job_id}"] = source
    st.session_state[f"editor_{session_key}"] = letter


def _is_stale_letter(text: str) -> bool:
    """Vrai si le cache contient une erreur plutôt qu'une lettre."""
    return not text or text.startswith("⚠️") or "no longer available" in text


@st.dialog("Lettre de motivation", width="large")
def show_cover_letter_dialog(job: dict[str, Any]) -> None:
    """Boîte de dialogue : brouillon éditable, copie, export PDF, régénération, candidature."""
    job_id = str(job.get("id"))
    title = str(job.get("title") or "Offre sans titre")
    company = str(job.get("company") or "Entreprise")
    url = str(job.get("url") or "")
    session_key = f"cover_letter_{job_id}"
    editor_key = f"editor_{session_key}"

    if _is_stale_letter(str(st.session_state.get(session_key, ""))):
        with st.spinner("Rédaction de la lettre en cours…"):
            generator = CoverLetterGenerator()
            _store_letter(job_id, generator.generate(job), generator.last_source)
    letter_text = str(st.session_state.get(session_key, ""))
    if editor_key not in st.session_state or _is_stale_letter(str(st.session_state[editor_key])):
        st.session_state[editor_key] = letter_text

    source = st.session_state.get(f"cover_letter_source_{job_id}")
    source_badge = _badge(*LETTER_SOURCES[source]) if source in LETTER_SOURCES else ""
    st.markdown(
        '<div class="sc-letter-head">'
        f'<h3 class="sc-card-title">{_esc(title)}</h3>{source_badge}</div>'
        f'<div class="sc-card-meta"><span class="sc-company">{_esc(company)}</span>'
        '<span class="sc-sep">·</span><span class="sc-meta-item">à partir de votre profil '
        "<code>data/cv_eddy.txt</code></span></div>",
        unsafe_allow_html=True,
    )
    if source == "deepseek":
        st.caption("Gemini était saturé : DeepSeek V3 a pris le relais automatiquement.")
    elif source == "fallback":
        st.caption("Les modèles IA sont indisponibles : version rédigée par le moteur de secours. "
                   "Vous pouvez relancer une génération IA via « Régénérer ».")

    edited = st.text_area("Brouillon (modifiable avant envoi)", height=380, key=editor_key)
    words = len(edited.split())
    pages = max(1.0, round(words / 420.0, 1))
    st.caption(f"{words} mots · {len(edited):,} caractères · ≈ {pages} page{'s' if pages > 1.1 else ''}".replace(",", " "))

    clean_company = "".join(c for c in company if c.isalnum() or c in ("-", "_", " ")).strip()
    if clean_company and clean_company.lower() not in ("entreprise", "inconnue", "none"):
        pdf_filename = f"Lettre de motivation Eddy De Castro - {clean_company}.pdf"
    else:
        pdf_filename = "Lettre de motivation Eddy De Castro.pdf"

    c_copy, c_pdf, c_regen = st.columns(3, gap="small")
    with c_copy:
        st.html(_copy_button_html(edited, f"copy_btn_{job_id}"), unsafe_allow_javascript=True)
    with c_pdf:
        st.download_button(
            "Télécharger en PDF",
            data=generate_cover_letter_pdf(edited, job=job),
            file_name=pdf_filename,
            mime="application/pdf",
            width="stretch",
            icon=":material/picture_as_pdf:",
        )
    with c_regen:
        with st.popover("Régénérer", icon=":material/refresh:", width="stretch"):
            custom_inst = st.text_area(
                "Consigne pour orienter la rédaction (facultatif)",
                placeholder="ex : insiste sur les Transformers et la vision par ordinateur…",
                key=f"inst_{session_key}",
                height=90,
            )
            if st.button("Avec Gemini", key=f"regen_gemini_{job_id}", type="primary", width="stretch"):
                with st.spinner("Appel à Gemini en cours…"):
                    generator = CoverLetterGenerator()
                    letter = generator.generate(job, custom_instruction=custom_inst, allow_fallback=True)
                    _store_letter(job_id, letter, generator.last_source)
                st.toast({
                    "gemini": "Lettre rédigée par Gemini.",
                    "deepseek": "Gemini saturé : lettre rédigée par DeepSeek V3.",
                }.get(generator.last_source, "Version de secours générée."))
                st.rerun(scope="fragment")
            if st.button("Avec DeepSeek V3", key=f"force_deepseek_{job_id}", width="stretch"):
                with st.spinner("Rédaction par DeepSeek V3…"):
                    generator = CoverLetterGenerator()
                    letter = generator.generate_with_deepseek(job, custom_instruction=custom_inst)
                    if letter and not letter.startswith("⚠️"):
                        _store_letter(job_id, letter, "deepseek")
                        st.toast("Lettre rédigée par DeepSeek V3.")
                    else:
                        _store_letter(job_id, generator.generate_fallback(job, custom_instruction=custom_inst), "fallback")
                        st.toast("DeepSeek indisponible : version de secours générée.")
                st.rerun(scope="fragment")

    st.divider()
    c_apply, c_status = st.columns(2, gap="small")
    with c_apply:
        if url.startswith("http"):
            st.link_button("Postuler à l'offre ↗", url, type="primary", width="stretch")
    with c_status:
        if job.get("status") == STATUS_APPLIED:
            st.caption("Candidature déjà marquée comme envoyée.")
        elif st.button("Marquer comme postulé", key=f"dialog_applied_{job_id}", width="stretch", icon=":material/check_circle:"):
            get_database().update_status(job_id, STATUS_APPLIED)
            job["status"] = STATUS_APPLIED
            bump_data_version()
            st.toast("Candidature marquée comme envoyée.")
            st.rerun()


# --------------------------------------------------------------------------- #
# Flux d'offres : liste compacte + panneau de détail
# --------------------------------------------------------------------------- #
LIST_PANE_HEIGHT = 780


def _status_actions(status: str | None) -> tuple[tuple[str, str, str], ...]:
    """Transitions proposées depuis un statut : (libellé, statut cible, icône)."""
    if status == STATUS_APPLIED:
        return (("Entretien obtenu", STATUS_INTERVIEW, ":material/forum:"), ("Archiver", STATUS_IGNORED, ":material/archive:"))
    if status in (STATUS_INTERVIEW, STATUS_IGNORED, STATUS_REJECTED):
        return (("Rétablir au flux", STATUS_NEW, ":material/undo:"),)
    return (("Marquer postulé", STATUS_APPLIED, ":material/send:"), ("Archiver", STATUS_IGNORED, ":material/archive:"))


def render_job_card(db: Database, job: dict[str, Any], keywords: Sequence[str], next_id: str | None = None) -> None:
    """Carte d'offre détaillée suivie de sa barre d'actions de candidature."""
    job_id = str(job.get("id"))
    url = str(job.get("url") or "")
    with st.container(border=True, key=f"card-detail-{job_id}"):
        st.markdown(job_card_html(job, keywords), unsafe_allow_html=True)
        st.markdown('<div class="sc-card-actions-divider"></div>', unsafe_allow_html=True)
        with st.container(horizontal=True, gap="small"):
            if url.startswith("http"):
                st.link_button("Postuler ↗", url, type="primary")
            if st.button("Lettre de motivation", key=f"letter-{job_id}", icon=":material/edit_note:"):
                show_cover_letter_dialog(job)
            for label, target, icon in _status_actions(job.get("status")):
                st.button(
                    label,
                    key=f"status-{target}-{job_id}",
                    icon=icon,
                    on_click=_set_status_and_advance,
                    args=(db, job_id, target, next_id),
                )


def render_compact_card_with_select(db: Database, job: dict[str, Any], keywords: Sequence[str]) -> None:
    """Élément compact de la liste de gauche, avec son bouton de sélection."""
    job_id = str(job.get("id"))
    is_selected = st.session_state.get("selected_job_id") == job_id
    with st.container(border=True, key=f"card-{'sel' if is_selected else 'item'}-{job_id}"):
        st.markdown(job_list_item_html(job), unsafe_allow_html=True)
        if st.button(
            "Affichée" if is_selected else "Voir le détail",
            key=f"select_{job_id}",
            type="tertiary",
            icon=":material/check:" if is_selected else ":material/arrow_forward:",
            icon_position="left" if is_selected else "right",
            disabled=is_selected,
        ):
            st.session_state.selected_job_id = job_id
            st.rerun()


def render_job_detail_pane(db: Database, job: dict[str, Any], keywords: Sequence[str], next_id: str | None = None) -> None:
    """Panneau de détail (colonne de droite)."""
    render_job_card(db, job, keywords, next_id)


def _empty_state(title: str, body: str) -> None:
    """Encadré d'état vide (titre + explication)."""
    st.markdown(f'<div class="sc-empty"><b>{title}</b>{body}</div>', unsafe_allow_html=True)


def render_stream(
    db: Database,
    jobs: list[dict[str, Any]],
    filters: Filters,
    keywords: Sequence[str],
    base_total: int | None = None,
    active_filters: int = 0,
) -> None:
    """Flux d'offres en deux volets : liste compacte à gauche, détail à droite."""
    if not jobs:
        if base_total == 0:
            _empty_state(
                "La base ne contient encore aucune offre.",
                "Lancez une première collecte depuis la page Pipeline, ou en local avec "
                "<code>python run_pipeline.py</code>.",
            )
            try:
                st.page_link("pages/pipeline.py", label="Ouvrir le pipeline", icon=":material/play_circle:")
            except Exception:  # noqa: BLE001 - page exécutée hors du routeur (tests)
                pass
        else:
            _empty_state(
                "Aucune offre ne correspond aux filtres courants.",
                "Élargissez les critères ou relancez la collecte "
                "(<code>python run_pipeline.py</code>).",
            )
            st.button("Réinitialiser les filtres", key="reset-filters-empty", icon=":material/filter_alt_off:", on_click=reset_filters)
        return

    note = "tri par score R&amp;D décroissant"
    note += " · filtres actifs" if active_filters else " · aucun filtre"
    st.markdown(
        f'<div class="sc-stream"><span class="sc-stream-count">{len(jobs)} offre(s)</span>'
        f'<span class="sc-stream-note">{note}</span></div>',
        unsafe_allow_html=True,
    )

    ids = [str(j["id"]) for j in jobs]
    if st.session_state.get("selected_job_id") not in ids:
        st.session_state.selected_job_id = ids[0]
    selected_id = st.session_state.selected_job_id
    position = ids.index(selected_id)
    next_id = ids[position + 1] if position + 1 < len(ids) else (ids[position - 1] if position else None)

    col_list, col_detail = st.columns([1, 1.45], gap="medium")
    with col_list:
        with st.container(height=LIST_PANE_HEIGHT, border=False):
            if filters.group_by_source:
                for label, group in group_jobs_by_source(jobs):
                    st.markdown(
                        f'<div class="sc-group"><span class="sc-group-name">{_esc(label)}</span>'
                        f'<span class="sc-group-count">{len(group)} offre(s)</span></div>',
                        unsafe_allow_html=True,
                    )
                    for job in group:
                        render_compact_card_with_select(db, job, keywords)
            else:
                for job in jobs:
                    render_compact_card_with_select(db, job, keywords)

    with col_detail:
        with st.container(height=LIST_PANE_HEIGHT, border=False):
            selected_job = next(j for j in jobs if str(j["id"]) == selected_id)
            render_job_detail_pane(db, selected_job, keywords, next_id)



# --------------------------------------------------------------------------- #
# Bandeau KPI & en-tête
# --------------------------------------------------------------------------- #
def _inline_relative(value: Any) -> str:
    """Date relative insérable au fil d'une phrase (« il y a 3 h »)."""
    label = relative_date(value)
    return label[0].lower() + label[1:] if label else label


def render_header(jobs: list[dict[str, Any]], config: dict[str, Any]) -> None:
    """En-tête : identité du produit, volumétrie et chaîne de traitement courante."""
    llm_model = config.get("llm", {}).get("model", "Gemini")
    stamps = [stamp for stamp in (parse_timestamp(job.get("created_at")) for job in jobs) if stamp]
    last = _esc(_inline_relative(max(stamps))) if stamps else "inconnue"
    st.markdown(
        page_header_html(
            "Veille stages R&D · Data Science / Machine Learning",
            "Stage Copilot",
            f"{len(jobs)} offres en base · dernière collecte {last} · "
            f"scoring &amp; reranking par <code>{_esc(llm_model)}</code>",
        ),
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
# Barre latérale : filtres compacts, réinitialisation, affichage
# --------------------------------------------------------------------------- #
def filter_defaults(sources: Sequence[str]) -> dict[str, Any]:
    """Valeurs par défaut des filtres du flux (toutes plateformes, vue focus active)."""
    return {
        "flt_query": "",
        "flt_sources": list(sources),
        "flt_min_score": 0,
        "flt_llm_only": False,
        "flt_hide_processed": True,
        "flt_statuses": list(STATUS_ORDER),
        "flt_tiers": list(TIER_LABELS.keys()),
        "flt_exclude_esn": False,
        "flt_exclude_dassault": False,
        "flt_exclude_companies": [],
        "flt_selected_companies": [],
    }


def reset_filters() -> None:
    """Callback : rétablit tous les filtres du flux à leur valeur par défaut.

    Les valeurs sont réécrites (et non supprimées) pour que les widgets affichés
    se remettent à jour côté navigateur.
    """
    defaults = st.session_state.get("_flt_defaults") or {}
    for key, value in defaults.items():
        st.session_state[key] = list(value) if isinstance(value, list) else value


def active_filter_count(filters: Filters, all_sources: Sequence[str] = ()) -> int:
    """Nombre de critères qui restreignent réellement le flux.

    Contrairement à ``Filters.is_default``, ignore la pagination et la vue focus
    (activée par défaut) et considère « toutes les plateformes cochées » comme
    l'absence de filtre.
    """
    checks = (
        bool(filters.query),
        bool(filters.sources) and bool(all_sources) and set(filters.sources) != set(all_sources),
        bool(filters.min_score),
        filters.llm_only,
        not filters.hide_processed and bool(filters.statuses) and set(filters.statuses) != set(STATUS_ORDER),
        bool(filters.tiers) and len(filters.tiers) != len(TIER_LABELS),
        filters.exclude_esn,
        filters.exclude_dassault,
        bool(filters.exclude_companies),
        bool(filters.selected_companies),
    )
    return sum(1 for check in checks if check)


def render_sidebar_filters(jobs: list[dict[str, Any]], sources: Sequence[str]) -> Filters:
    """Barre latérale de filtres compacts ; retourne les critères courants."""
    counts: dict[str, int] = {}
    company_counts: dict[str, int] = {}
    for job in jobs:
        source = job.get("source")
        if source:
            counts[source] = counts.get(source, 0) + 1
        comp = (job.get("company") or "").strip()
        if comp:
            company_counts[comp] = company_counts.get(comp, 0) + 1

    sorted_companies = sorted(company_counts.keys(), key=lambda c: (-company_counts[c], c.lower()))

    # Filtres pilotés par session_state : défauts posés une fois, puis nettoyés des
    # options disparues (la base a pu changer depuis le dernier rendu).
    defaults = filter_defaults(sources)
    st.session_state["_flt_defaults"] = defaults
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)
    for key, options in (
        ("flt_sources", sources),
        ("flt_exclude_companies", sorted_companies),
        ("flt_selected_companies", sorted_companies),
    ):
        allowed = set(options)
        st.session_state[key] = [value for value in st.session_state[key] if value in allowed]

    with st.sidebar:
        st.markdown('<div class="sc-eyebrow">Filtres</div>', unsafe_allow_html=True)
        state_slot = st.container()
        query = st.text_input(
            "Recherche",
            placeholder="Titre, entreprise, techno…",
            icon=":material/search:",
            key="flt_query",
            help="Plein texte (ET logique) sur le titre, l'entreprise, la ville, "
            "la fiche de poste et les technologies. Validez avec Entrée.",
        )
        selected_sources = st.multiselect(
            "Plateformes",
            options=list(sources),
            key="flt_sources",
            format_func=lambda source: f"{source_label(source)} ({counts.get(source, 0)})",
        )
        min_score = st.slider(
            "Score R&D minimal",
            min_value=0,
            max_value=100,
            step=5,
            key="flt_min_score",
            help="Score effectif : rerank du juge LLM s'il existe, sinon score hybride du bi-encoder.",
        )
        llm_only = st.toggle(
            "Verdict LLM uniquement",
            key="flt_llm_only",
            help="Ne conserver que les offres déjà évaluées par le juge LLM (étape 2).",
        )
        hide_processed = st.toggle(
            "Masquer les offres traitées",
            key="flt_hide_processed",
            help="Prioritaire sur le filtre de statut : ne conserve que les offres au statut NOUVEAU.",
        )

        with st.expander("Critères avancés", icon=":material/tune:"):
            statuses = st.multiselect(
                "Statut de candidature",
                options=STATUS_OPTIONS,
                key="flt_statuses",
                format_func=lambda status: STATUS_LABELS.get(status, status),
                disabled=hide_processed,
            )
            tiers = st.multiselect(
                "Typologie d'entreprise",
                options=list(TIER_LABELS.keys()),
                key="flt_tiers",
                format_func=lambda tier: TIER_LABELS[tier],
            )
            exclude_esn = st.toggle(
                "Exclure les ESN",
                key="flt_exclude_esn",
                help="Retire les ESN / SSII du flux (filtre également appliqué en amont si configuré).",
            )
            exclude_dassault = st.toggle(
                "Exclure Dassault",
                key="flt_exclude_dassault",
                help="Masque les offres Dassault Systèmes et Dassault Aviation.",
            )
            exclude_companies = st.multiselect(
                "Exclure des entreprises",
                options=sorted_companies,
                key="flt_exclude_companies",
                format_func=lambda c: f"{c} ({company_counts.get(c, 0)})",
                help="Sélectionnez une ou plusieurs entreprises à masquer du flux.",
            )
            selected_companies = st.multiselect(
                "Cibler des entreprises",
                options=sorted_companies,
                key="flt_selected_companies",
                format_func=lambda c: f"{c} ({company_counts.get(c, 0)})",
                help="Ne conserver que les offres des entreprises sélectionnées.",
            )

        with st.expander("Affichage", icon=":material/view_agenda:"):
            display_mode = st.selectbox("Mode de flux", options=[DISPLAY_FLAT, DISPLAY_GROUPED])
            page_size = st.selectbox(
                "Offres affichées",
                options=list(PAGE_SIZES),
                index=1,
                help="Limite le nombre d'offres rendues pour garder l'interface fluide.",
            )

    limit = None if page_size == PAGE_SIZE_ALL else int(page_size)
    filters = Filters(
        query=query or "",
        sources=tuple(selected_sources),
        statuses=tuple(statuses),
        tiers=tuple(tiers),
        min_score=float(min_score),
        llm_only=llm_only,
        hide_processed=hide_processed,
        exclude_esn=exclude_esn,
        exclude_dassault=exclude_dassault,
        exclude_companies=tuple(exclude_companies),
        selected_companies=tuple(selected_companies),
        limit=limit,
        group_by_source=display_mode == DISPLAY_GROUPED,
    )

    active = active_filter_count(filters, sources)
    if active:
        with state_slot:
            c_state, c_reset = st.columns([1.4, 1], vertical_alignment="center")
            c_state.markdown(
                f'<div class="sc-filter-state"><b>{active}</b> filtre{"s" if active > 1 else ""} actif{"s" if active > 1 else ""}</div>',
                unsafe_allow_html=True,
            )
            c_reset.button(
                "Réinitialiser",
                key="reset-filters",
                type="tertiary",
                icon=":material/filter_alt_off:",
                on_click=reset_filters,
            )
    return filters
