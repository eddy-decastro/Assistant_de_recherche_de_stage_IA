from __future__ import annotations
import streamlit as st
from typing import Any, Sequence
import json
from utils.data import *
from utils.data import _esc

from src.constants import *
from src.matching.cover_letter import CoverLetterGenerator
from src.matching.pdf_exporter import generate_cover_letter_pdf

# Préfixe des lettres de secours générées en erreur (séquence échappée : aucun emoji dans le source).
_WARN_PREFIX = "\u26a0\ufe0f"

# Fragments HTML (typographie et badges, aucun emoji décoratif)
# --------------------------------------------------------------------------- #

def _badge(label: str, tone: str | None = None, dot: str | None = None, extra_cls: str = "") -> str:
    """Pill badge de métadonnée, avec pastille colorée optionnelle."""
    classes = f"sc-badge {tone_class(tone)}" if tone else "sc-badge"
    if extra_cls:
        classes += f" {extra_cls}"
    marker = f'<i class="sc-dot" style="background:{dot}"></i>' if dot else ""
    return f'<span class="{classes}">{marker}{_esc(label)}</span>'


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


@st.dialog("Lettre de motivation personnalisée", width="large")
def show_cover_letter_dialog(job: dict[str, Any]) -> None:
    """Boîte de dialogue modale affichant la lettre de motivation rédigée par Gemini."""
    job_id = str(job.get("id"))
    title = str(job.get("title") or "Offre sans titre")
    company = str(job.get("company") or "Entreprise")
    url = str(job.get("url") or "")

    if url.startswith("http"):
        col_title, col_apply = st.columns([2.8, 1.2], vertical_alignment="center")
        with col_title:
            st.markdown(f"**Poste :** {_esc(title)} — **{_esc(company)}**")
            st.caption("Rédigée sur mesure à partir de votre CV (page Paramètres).")
        with col_apply:
            st.link_button("Postuler à l'offre ↗", url, type="primary", width="stretch")
    else:
        st.markdown(f"**Poste :** {_esc(title)} — **{_esc(company)}**")
        st.caption("Rédigée sur mesure à partir de votre CV (page Paramètres).")

    session_key = f"cover_letter_{job_id}"
    source_key = f"cover_letter_source_{job_id}"
    version_key = f"cover_letter_version_{job_id}"
    version = int(st.session_state.get(version_key, 0))
    cached_letter = str(st.session_state.get(session_key, ""))
    editor_key = f"editor_{session_key}_{version}"

    # Si la lettre n'a pas été générée ou si le cache contient une ancienne erreur
    if (
        not cached_letter
        or cached_letter.startswith(_WARN_PREFIX)
        or "no longer available" in cached_letter
    ):
        with st.spinner("Rédaction de la lettre en cours..."):
            generator = CoverLetterGenerator()
            new_letter = generator.generate(job)
            st.session_state[session_key] = new_letter
            st.session_state[source_key] = generator.last_source
            version += 1
            st.session_state[version_key] = version
            editor_key = f"editor_{session_key}_{version}"

    letter_text = st.session_state.get(session_key, "")

    # Nettoyage préventif de l'état du widget éditeur s'il contenait l'erreur
    if editor_key in st.session_state and (
        _WARN_PREFIX in str(st.session_state[editor_key])
        or "no longer available" in str(st.session_state[editor_key])
    ):
        version += 1
        st.session_state[version_key] = version
        editor_key = f"editor_{session_key}_{version}"

    # Si la lettre a été produite via DeepSeek ou le moteur de secours algorithmique
    source = st.session_state.get(source_key)
    if source == "deepseek":
        st.info(
            "**IA de secours DeepSeek V3 active** : Votre lettre a été rédigée avec succès par DeepSeek "
            "(relais automatique suite à une saturation temporaire de Google Gemini). "
            "Vous pouvez la copier, la modifier ou la télécharger en PDF."
        )
    elif source == "fallback":
        st.info(
            "**Mode de secours algorithmique actif** : Rédigée sur-mesure à partir de votre profil et de l'offre. "
            "Vous pouvez la copier, la modifier ou la télécharger en PDF, ou tenter une génération IA ci-dessous."
        )

    edited = st.text_area(
        "Brouillon de la lettre (éditable directement avant envoi) :",
        value=letter_text,
        height=380,
        key=editor_key,
    )

    words_count = len(edited.split())
    chars_count = len(edited)
    approx_pages = max(1.0, round(words_count / 420.0, 1))
    st.caption(f"**{words_count} mots** · {chars_count:,} caractères (environ {approx_pages} page{'s' if approx_pages > 1.1 else ''} standard)")

    custom_inst = st.text_input(
        "Consigne spécifique pour orienter la rédaction (optionnel) :",
        placeholder="ex : Insiste sur les Transformers et la vision par ordinateur, mets en valeur le projet MedStay-CI...",
        key=f"inst_{session_key}",
    )

    clean_company = "".join(c for c in company if c.isalnum() or c in ("-", "_", " ")).strip()
    if clean_company and clean_company.lower() not in ("entreprise", "inconnue", "none"):
        pdf_filename = f"Lettre de motivation Eddy De Castro - {clean_company}.pdf"
    else:
        pdf_filename = "Lettre de motivation Eddy De Castro.pdf"

    pdf_bytes = generate_cover_letter_pdf(edited, job=job)
    # Fix: escape single quotes for insertion in HTML inline script
    escaped_json = json.dumps(edited).replace("'", "&#39;")
    copy_btn_id = f"copy_btn_{job_id}"

    c1, c2, c3, c4 = st.columns([1.1, 1.1, 1.1, 1.1], gap="small")
    with c1:
        st.html(
            f"""
            <div style="display: flex; width: 100%;">
              <button
                id="{copy_btn_id}"
                type="button"
                onclick='(function(btn) {{
                  let textToCopy = {escaped_json};
                  const area = document.querySelector("textarea[aria-label*=\\"Brouillon\\"]");
                  if (area && area.value) {{
                    textToCopy = area.value;
                  }}
                  if (navigator.clipboard && navigator.clipboard.writeText) {{
                    navigator.clipboard.writeText(textToCopy).then(() => {{
                      btn.innerText = "✓ Copié dans le presse-papier !";
                      btn.style.backgroundColor = "#059669";
                      btn.style.borderColor = "#059669";
                      btn.style.color = "#FFFFFF";
                      setTimeout(() => {{
                        btn.innerText = "Copier la lettre";
                        btn.style.backgroundColor = "";
                        btn.style.borderColor = "";
                        btn.style.color = "";
                      }}, 2500);
                    }}).catch(() => fallbackCopy(textToCopy, btn));
                  }} else {{
                    fallbackCopy(textToCopy, btn);
                  }}
                  function fallbackCopy(str, b) {{
                    const el = document.createElement("textarea");
                    el.value = str;
                    el.setAttribute("readonly", "");
                    el.style.position = "absolute";
                    el.style.left = "-9999px";
                    document.body.appendChild(el);
                    el.select();
                    document.execCommand("copy");
                    document.body.removeChild(el);
                    b.innerText = "✓ Copié dans le presse-papier !";
                    b.style.backgroundColor = "#059669";
                    b.style.borderColor = "#059669";
                    b.style.color = "#FFFFFF";
                    setTimeout(() => {{
                      b.innerText = "Copier la lettre";
                      b.style.backgroundColor = "";
                      b.style.borderColor = "";
                      b.style.color = "";
                    }}, 2500);
                  }}
                }})(this)'
                style="
                  width: 100%;
                  min-height: 38px;
                  padding: 0.4rem 0.75rem;
                  font-family: inherit;
                  font-size: 13px;
                  font-weight: 500;
                  color: inherit;
                  background-color: transparent;
                  border: 1px solid rgba(128, 128, 128, 0.35);
                  border-radius: 8px;
                  cursor: pointer;
                  display: inline-flex;
                  align-items: center;
                  justify-content: center;
                  gap: 6px;
                  transition: all 0.2s ease-in-out;
                "
                onmouseover="this.style.borderColor='#1E3A8A'; this.style.backgroundColor='rgba(30, 58, 138, 0.08)';"
                onmouseout="if(!this.innerText.includes('Copié')) {{ this.style.borderColor='rgba(128, 128, 128, 0.35)'; this.style.backgroundColor='transparent'; }}"
              >
                Copier la lettre
              </button>
            </div>
            """
        )
    with c2:
        st.download_button(
            "Télécharger (.pdf)",
            data=pdf_bytes,
            file_name=pdf_filename,
            mime="application/pdf",
            width="stretch",
            icon=":material/picture_as_pdf:",
        )
    with c3:
        if st.button("Réessayer Gemini", key=f"regen_gemini_{job_id}", width="stretch", icon=":material/refresh:"):
            with st.spinner("Appel à Gemini en cours..."):
                generator = CoverLetterGenerator()
                new_l = generator.generate(job, custom_instruction=custom_inst, allow_fallback=True)
                st.session_state[session_key] = new_l
                st.session_state[source_key] = generator.last_source
                st.session_state[version_key] = version + 1
                if generator.last_source == "gemini":
                    st.toast("✓ Lettre rédigée avec succès par Gemini !")
                elif generator.last_source == "deepseek":
                    st.toast("Gemini saturé : relayé avec succès par DeepSeek V3 !")
                else:
                    st.toast("Version de secours algorithmique générée.")
                st.rerun()
    with c4:
        if st.button("Rédiger DeepSeek", key=f"force_deepseek_{job_id}", width="stretch", icon=":material/smart_toy:"):
            with st.spinner("Rédaction par DeepSeek V3..."):
                generator = CoverLetterGenerator()
                new_l = generator.generate_with_deepseek(job, custom_instruction=custom_inst)
                if new_l and not new_l.startswith(_WARN_PREFIX):
                    st.session_state[session_key] = new_l
                    st.session_state[source_key] = "deepseek"
                    st.session_state[version_key] = version + 1
                    st.toast("✓ Lettre rédigée avec succès par DeepSeek V3 !")
                else:
                    st.toast("DeepSeek non disponible, génération de secours activée.")
                    new_l = generator.generate_fallback(job, custom_instruction=custom_inst)
                    st.session_state[session_key] = new_l
                    st.session_state[source_key] = "fallback"
                    st.session_state[version_key] = version + 1
                st.rerun()

    # Section de candidature directe depuis la lettre de motivation
    if url.startswith("http"):
        st.markdown('<hr style="margin: 18px 0 14px; border-color: var(--border, #E4DED3);">', unsafe_allow_html=True)
        col_act1, col_act2 = st.columns([1.6, 1.2], vertical_alignment="center", gap="small")
        with col_act1:
            st.link_button(
                "Postuler directement à l'offre ↗",
                url,
                type="primary",
                width="stretch",
            )
        with col_act2:
            current_st = job.get("status")
            if current_st != STATUS_APPLIED:
                if st.button(
                    "✓ Marquer comme postulé",
                    key=f"dialog_applied_{job_id}",
                    width="stretch",
                    icon=":material/check_circle:",
                ):
                    db = get_database()
                    db.update_status(job_id, STATUS_APPLIED)
                    job["status"] = STATUS_APPLIED
                    bump_data_version()
                    st.toast("✓ Statut mis à jour : Candidature marquée comme envoyée !")
                    st.rerun()
            else:
                st.caption("Candidature déjà enregistrée comme postulée.")


# --------------------------------------------------------------------------- #
# En-tête et KPI (éléments natifs)
# --------------------------------------------------------------------------- #
def _inline_relative(value: Any) -> str:
    """Date relative insérable au fil d'une phrase (« il y a 3 h »)."""
    label = relative_date(value)
    return label[0].lower() + label[1:] if label else label


def render_header(jobs: list[dict[str, Any]], config: dict[str, Any]) -> None:
    """En-tête : titre, volumétrie, dernière collecte et chaîne de traitement."""
    from utils.layout import page_header

    llm_model = config.get("llm", {}).get("model", "Gemini 2.0 Flash")
    stamps = [stamp for stamp in (parse_timestamp(job.get("created_at")) for job in jobs) if stamp]
    last = _inline_relative(max(stamps)) if stamps else "inconnue"
    page_header(
        "Flux d'offres",
        f"{len(jobs)} offres en base · dernière collecte {last} · scoring et reranking LLM ({llm_model})",
    )


def render_kpis(
    jobs: list[dict[str, Any]],
    llm_model: str,
    base_total: int,
    filters_active: bool,
) -> None:
    """KPI : offres actives, qualifiées, rerankées et répartition par plateforme."""
    from utils.layout import kpi_row

    active = [job for job in jobs if job.get("status") not in (STATUS_IGNORED, STATUS_REJECTED)]
    qualified = sum(1 for job in active if effective_score(job) >= QUALIFIED_SCORE)
    ranked = sum(1 for job in active if is_reranked(job))
    base = max(len(active), 1)
    scope = f"sur {base_total} en base" if filters_active else "hors offres archivées"
    kpi_row(
        [
            ("Offres actives", str(len(active)), f"{scope} · {qualified / base * 100:.0f} % qualifiées R&D"),
            ("Qualifiées R&D", str(qualified), f"Score effectif ≥ {QUALIFIED_SCORE:.0f} · {qualified} sur {len(active)} offres actives"),
            ("Rerankées par le LLM", f"{ranked / base * 100:.0f} %", f"{ranked} offres évaluées par {llm_model}"),
        ]
    )
    distribution = source_distribution(active)
    if distribution:
        st.caption(" · ".join(f"{label} {count}" for label, count, _ in distribution))


# --------------------------------------------------------------------------- #
# Barre latérale : filtres compacts, affichage, maintenance de la base
# --------------------------------------------------------------------------- #
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

    with st.sidebar:
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
            value=True,
            help="Prioritaire sur le filtre de statut : ne conserve que les offres au statut NOUVEAU.",
        )
        hide_excluded = st.toggle(
            "Masquer les offres exclues",
            value=True,
            help="Masque les offres qui ne sont pas des stages ou ne respectent pas les critères minimaux (contrat, durée).",
        )
        hide_defense_ethics = st.toggle(
            "Masquer Défense et Éthique",
            value=True,
            help="Masque les offres relevant du secteur Défense/Armement ou nécessitant un examen éthique.",
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
            structure_types = st.multiselect(
                "Catégorie d'entreprise",
                options=list(STRUCTURE_TYPES),
                default=list(STRUCTURE_TYPES),
                format_func=lambda s: STRUCTURE_TYPE_LABELS.get(s, s),
            )
            flags = st.multiselect(
                "Signaux d'attention / Flags",
                options=list(FLAGS),
                default=[],
                format_func=lambda f: FLAG_LABELS.get(f, f),
            )
            exclude_esn = st.toggle(
                "Exclure les ESN",
                help="Retire les ESN / SSII du flux (filtre également appliqué en amont si configuré).",
            )
            exclude_dassault = st.toggle(
                "Exclure Dassault",
                help="Masque les offres Dassault Systèmes et Dassault Aviation.",
            )
            exclude_companies = st.multiselect(
                "Exclure des entreprises",
                options=sorted_companies,
                default=[],
                format_func=lambda c: f"{c} ({company_counts.get(c, 0)})",
                help="Sélectionnez une ou plusieurs entreprises à masquer du flux.",
            )
            selected_companies = st.multiselect(
                "Cibler des entreprises",
                options=sorted_companies,
                default=[],
                format_func=lambda c: f"{c} ({company_counts.get(c, 0)})",
                help="Ne conserver que les offres des entreprises sélectionnées.",
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
        exclude_dassault=exclude_dassault,
        exclude_companies=tuple(exclude_companies),
        selected_companies=tuple(selected_companies),
        limit=limit,
        group_by_source=display_mode == DISPLAY_GROUPED,
        structure_types=tuple(structure_types),
        flags=tuple(flags),
        hide_excluded=hide_excluded,
        hide_defense_ethics=hide_defense_ethics,
    )


