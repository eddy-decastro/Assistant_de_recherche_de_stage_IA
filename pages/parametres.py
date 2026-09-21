"""Page de Paramètres & Profil — Stage Copilot.

Permet de :
1. Consulter, déposer (PDF/TXT) et modifier son CV sans toucher au code.
2. Configurer les requêtes cibles et les passes de scraping (Fraîcheur & Pertinence).
3. Déclencher la re-notation ciblée des offres avec le nouveau CV via Gemini.
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pypdf
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_config, save_config
from utils.data import bump_data_version, get_database, load_jobs, _esc
from utils.styles import inject_styles
from utils.task_manager import (
    get_active_task,
    render_sidebar_task_badge,
    render_task_monitor,
    start_background_task,
)

CV_PATH = PROJECT_ROOT / "data" / "cv_eddy.txt"

st.set_page_config(
    page_title="Paramètres & Profil",
    page_icon=":material/settings:",
    layout="wide",
)

inject_styles()
render_sidebar_task_badge()

from utils.auth import require_auth, render_logout_button
require_auth()
render_logout_button()

st.markdown("<h1>Paramètres &amp; Profil</h1>", unsafe_allow_html=True)
st.caption("Personnalisez votre CV, vos critères de scraping et pilotez la ré-évaluation par Gemini.")

tab_cv, tab_scraping, tab_rerank = st.tabs([
    "📄 Mon CV & Profil",
    "🔍 Recherches & Scraping",
    "🔄 Re-notation des offres",
])


# =========================================================================== #
# ONGLET 1 : MON CV & PROFIL
# =========================================================================== #
with tab_cv:
    st.markdown("### Profil du candidat &amp; CV actif", unsafe_allow_html=True)
    st.write(
        "Ce texte sert de référence pour le calcul de pertinence des offres et pour la rédaction "
        "automatique de vos lettres de motivation personnalisées."
    )

    # Lecture du CV actuel
    current_cv = ""
    if CV_PATH.exists():
        current_cv = CV_PATH.read_text(encoding="utf-8")

    # Zone d'importation de fichier
    st.markdown("#### 1. Importer un nouveau document")
    uploaded_file = st.file_uploader(
        "Déposez votre CV au format PDF ou texte (.txt)",
        type=["pdf", "txt"],
        help="Le texte du document sera extrait et placé dans l'éditeur ci-dessous pour validation.",
    )

    extracted_text: str | None = None
    if uploaded_file is not None:
        try:
            if uploaded_file.name.lower().endswith(".pdf"):
                reader = pypdf.PdfReader(uploaded_file)
                pages_text = [page.extract_text() or "" for page in reader.pages]
                extracted_text = "\n\n".join(t.strip() for t in pages_text if t.strip())
            else:
                raw_bytes = uploaded_file.read()
                try:
                    extracted_text = raw_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    extracted_text = raw_bytes.decode("latin-1", errors="replace")

            if extracted_text:
                st.success(f"Document `{uploaded_file.name}` extrait avec succès ({len(extracted_text)} caractères).")
                if st.button("Transférer le texte extrait dans l'éditeur ci-dessous", icon=":material/arrow_downward:"):
                    st.session_state["cv_editor_content"] = extracted_text
                    st.rerun()
            else:
                st.warning("Aucun texte lisible n'a pu être extrait de ce document.")
        except Exception as exc:
            st.error(f"Erreur lors de la lecture du fichier : {exc}")

    # Zone d'édition
    st.markdown("#### 2. Consulter et ajuster le profil")
    editor_default = st.session_state.get("cv_editor_content", current_cv)

    edited_cv = st.text_area(
        "Texte du CV utilisé par le juge LLM et le générateur de lettre :",
        value=editor_default,
        height=450,
        help="Vous pouvez éditer directement ce texte. N'oubliez pas de cliquer sur 'Enregistrer le profil'.",
    )

    c1, c2, _ = st.columns([1.5, 1.5, 3], gap="small")
    with c1:
        if st.button("Enregistrer le profil", type="primary", use_container_width=True, icon=":material/save:"):
            CV_PATH.parent.mkdir(parents=True, exist_ok=True)
            CV_PATH.write_text(edited_cv, encoding="utf-8")
            st.session_state["cv_editor_content"] = edited_cv
            bump_data_version()
            st.success("Profil candidat enregistré avec succès dans `data/cv_eddy.txt` !")

    with c2:
        st.download_button(
            "Télécharger en .txt",
            data=edited_cv,
            file_name="CV_Actif.txt",
            mime="text/plain",
            use_container_width=True,
            icon=":material/download:",
        )


# =========================================================================== #
# ONGLET 2 : RECHERCHES & SCRAPING
# =========================================================================== #
with tab_scraping:
    st.markdown("### Paramètres des collecteurs (LinkedIn &amp; JobTeaser)", unsafe_allow_html=True)
    st.write(
        "Ajustez ici les requêtes de recherche et les stratégies de collecte. "
        "Les modifications sont écrites directement dans `config.yaml`."
    )

    cfg = load_config()
    scrapers_cfg = cfg.get("scrapers", {})
    passes_cfg = scrapers_cfg.get("passes", {})
    fresh_cfg = passes_cfg.get("freshness", {})
    rel_cfg = passes_cfg.get("relevance", {})

    with st.form("scraping_config_form"):
        st.markdown("#### 1. Mots-clés &amp; Requêtes cibles", unsafe_allow_html=True)
        current_queries = scrapers_cfg.get("target_queries", [])
        queries_text = st.text_area(
            "Requêtes recherchées (une par ligne) :",
            value="\n".join(current_queries),
            height=130,
            help="Chaque requête est lancée sur les plateformes. Conservez 'Stage' devant pour cibler les stages.",
        )

        st.markdown("#### 2. Plafond global de collecte")
        max_offers = st.number_input(
            "Plafond maximal d'offres par source :",
            min_value=10,
            max_value=1000,
            value=int(scrapers_cfg.get("max_offers_per_source", 200)),
            step=25,
            help="Dès que ce nombre d'offres est atteint pour une source, les requêtes suivantes sont interrompues.",
        )

        st.markdown("#### 3. Passe « Fraîcheur » (Tri par date)")
        f_col1, f_col2, f_col3 = st.columns(3)
        with f_col1:
            fresh_enabled = st.toggle("Activer la passe Fraîcheur", value=bool(fresh_cfg.get("enabled", True)))
            fresh_target = st.number_input(
                "Objectif d'offres (source) :",
                min_value=5,
                max_value=500,
                value=int(fresh_cfg.get("target_new_per_source", 150)),
                step=10,
            )
        with f_col2:
            fresh_window = st.number_input(
                "Fenêtre temporelle (en jours) :",
                min_value=1,
                max_value=90,
                value=int(fresh_cfg.get("window_days") or 7),
                step=1,
                help="Ne cherche que les offres publiées depuis moins de N jours.",
            )
            fresh_per_query = st.number_input(
                "Quota max par requête :",
                min_value=5,
                max_value=500,
                value=int(fresh_cfg.get("max_offers_per_query", 150)),
                step=10,
            )
        with f_col3:
            fresh_stop_known = st.number_input(
                "Arrêt anticipé (offres déjà vues d'affilée) :",
                min_value=1,
                max_value=50,
                value=int(fresh_cfg.get("early_stop_after_known", 10)),
                help="Dès que N offres déjà en base se succèdent, la recherche s'arrête (flux déjà parcouru).",
            )
            fresh_pages = st.number_input(
                "Plafond de pages par requête :",
                min_value=1,
                max_value=50,
                value=int(fresh_cfg.get("max_pages_per_query", 20)),
            )

        st.markdown("#### 4. Passe « Pertinence » (Classement algorithmique)")
        r_col1, r_col2, r_col3 = st.columns(3)
        with r_col1:
            rel_enabled = st.toggle("Activer la passe Pertinence", value=bool(rel_cfg.get("enabled", True)))
            rel_target = st.number_input(
                "Objectif d'offres (source) :",
                min_value=5,
                max_value=500,
                value=int(rel_cfg.get("target_new_per_source", 50)),
                step=10,
            )
        with r_col2:
            rel_per_query = st.number_input(
                "Quota max par requête :",
                min_value=5,
                max_value=500,
                value=int(rel_cfg.get("max_offers_per_query", 50)),
                step=10,
            )
        with r_col3:
            rel_pages = st.number_input(
                "Plafond de pages par requête :",
                min_value=1,
                max_value=50,
                value=int(rel_cfg.get("max_pages_per_query", 20)),
                key="rel_pages_input",
            )

        save_btn = st.form_submit_button("Enregistrer les réglages de collecte", type="primary", icon=":material/save:")

    if save_btn:
        new_queries = [q.strip() for q in queries_text.splitlines() if q.strip()]
        if not new_queries:
            st.error("Veuillez renseigner au moins une requête cible.")
        else:
            cfg.setdefault("scrapers", {})
            cfg["scrapers"]["target_queries"] = new_queries
            cfg["scrapers"]["max_offers_per_source"] = int(max_offers)

            cfg["scrapers"].setdefault("passes", {})
            cfg["scrapers"]["passes"].setdefault("freshness", {})
            cfg["scrapers"]["passes"]["freshness"]["enabled"] = bool(fresh_enabled)
            cfg["scrapers"]["passes"]["freshness"]["target_new_per_source"] = int(fresh_target)
            cfg["scrapers"]["passes"]["freshness"]["max_offers_per_query"] = int(fresh_per_query)
            cfg["scrapers"]["passes"]["freshness"]["window_days"] = int(fresh_window)
            cfg["scrapers"]["passes"]["freshness"]["early_stop_after_known"] = int(fresh_stop_known)
            cfg["scrapers"]["passes"]["freshness"]["max_pages_per_query"] = int(fresh_pages)

            cfg["scrapers"]["passes"].setdefault("relevance", {})
            cfg["scrapers"]["passes"]["relevance"]["enabled"] = bool(rel_enabled)
            cfg["scrapers"]["passes"]["relevance"]["target_new_per_source"] = int(rel_target)
            cfg["scrapers"]["passes"]["relevance"]["max_offers_per_query"] = int(rel_per_query)
            cfg["scrapers"]["passes"]["relevance"]["max_pages_per_query"] = int(rel_pages)

            save_config(cfg)
            bump_data_version()
            st.success("Paramètres enregistrés avec succès dans `config.yaml` !")


# =========================================================================== #
# ONGLET 3 : RE-NOTATION & NOTATION DES OFFRES
# =========================================================================== #
with tab_rerank:
    st.markdown("### Notation &amp; Ré-évaluation des offres par Gemini", unsafe_allow_html=True)
    st.write(
        "Pilotez l'évaluation LLM : notez vos offres récemment collectées ou réévaluez les offres existantes "
        "suite à une mise à jour de votre profil ou de votre CV."
    )

    db = get_database()
    stats = db.get_scoring_stats() if hasattr(db, "get_scoring_stats") else {
        "total": len(load_jobs(db, int(st.session_state.get("data_version", 0)))),
        "rated": 0,
        "unrated": 0,
        "unrated_with_desc": 0,
    }

    col_s1, col_s2, col_s3, col_s4 = st.columns(4)
    with col_s1:
        st.metric("Total en base", stats["total"])
    with col_s2:
        st.metric("Déjà notées", stats["rated"])
    with col_s3:
        st.metric("En attente de notation", stats["unrated"])
    with col_s4:
        st.metric("Prêtes avec description", stats["unrated_with_desc"])

    st.markdown("---")
    st.markdown("#### Paramètres d'évaluation")

    mode = st.radio(
        "Action à réaliser :",
        options=[
            "Évaluer les offres non notées (prend les nouvelles offres en attente)",
            "Réévaluer les offres déjà notées (re-notation globale avec le nouveau CV)",
        ],
        index=0 if stats["unrated"] > 0 else 1,
        help=(
            "« Évaluer les offres non notées » cible uniquement les fiches sans note valide.\n"
            "« Réévaluer les offres déjà notées » réinitialise les meilleures offres pour recalculer leur alignement."
        ),
    )
    is_unrated_mode = mode.startswith("Évaluer les offres non notées")

    cfg = load_config()
    llm_cfg = cfg.get("llm", {})
    llm_tier = str(llm_cfg.get("tier", "free")).casefold()
    llm_rpm = int(llm_cfg.get("rate_limit_rpm", 14 if llm_tier == "free" else 120))

    c_vol, c_opts = st.columns([2, 2], gap="medium")
    with c_vol:
        re_limit = st.select_slider(
            "Nombre d'offres à traiter :",
            options=[10, 20, 50, 100, 200, "Toutes les offres"],
            value=50 if is_unrated_mode and stats["unrated"] >= 50 else (20 if stats["unrated"] >= 20 else 10),
            help="Nombre maximal d'offres envoyées à Gemini lors de cette session.",
        )
        limit_int = 10000 if re_limit == "Toutes les offres" else int(re_limit)
        actual_count = min(limit_int, stats["unrated"] if is_unrated_mode else stats["total"])
        est_sec = int(actual_count * (60.0 / max(1, llm_rpm)))
        est_str = f"{est_sec // 60} min {est_sec % 60:02d} s" if est_sec >= 60 else f"{est_sec} s"
        tier_badge = "Gratuit (15 req/min max)" if llm_tier == "free" else "Payant (rapide)"
        st.caption(f"⏱️ Durée estimée pour **{actual_count} offre(s)** : **~{est_str}** *(Forfait {tier_badge})*")

    with c_opts:
        st.write("")
        do_backfill = st.checkbox(
            "Enrichir automatiquement la description si manquante (Backfill)",
            value=True,
            help="Visite la page détail de l'offre si sa description est vide avant de l'envoyer au LLM.",
        )
        tier_choice = st.selectbox(
            "Forfait Google Gemini :",
            options=["Forfait Gratuit (14 req/min - sans surcoût)", "Forfait Payant (Pay-as-you-go - rapide)"],
            index=0 if llm_tier == "free" else 1,
            help="Le forfait gratuit de Google AI Studio impose une limite stricte de 15 requêtes/minute.",
        )
        new_tier = "free" if "Gratuit" in tier_choice else "paid"
        if new_tier != llm_tier:
            cfg.setdefault("llm", {})["tier"] = new_tier
            cfg["llm"]["rate_limit_rpm"] = 14 if new_tier == "free" else 120
            cfg["llm"]["concurrency"] = 1 if new_tier == "free" else 5
            save_config(cfg)
            bump_data_version()
            st.rerun()
    button_label = "Noter les offres non notées" if is_unrated_mode else "Lancer la re-notation par Gemini"

    active_task = get_active_task()
    is_task_running = bool(active_task and active_task.get("status") == "running")

    # Affichage du moniteur interactif (barre d'avancement, ETA, logs et bouton d'arrêt)
    render_task_monitor()

    if is_task_running:
        st.info("⏳ Un traitement est actuellement en cours. Vous pouvez suivre sa progression ci-dessus ou naviguer librement sans interrompre le calcul.")

    if st.button(
        button_label,
        type="primary",
        icon=":material/play_arrow:",
        disabled=is_task_running,
    ):
        args: list[str] = ["--no-collect"]
        if not is_unrated_mode:
            args.extend(["--reset-rerank", str(limit_int)])
        args.extend(["--trigger-rerank", "--top-rerank", str(limit_int)])
        if do_backfill:
            args.append("--backfill-missing")

        command = [sys.executable, str(PROJECT_ROOT / "run_scrapers.py"), *args]
        task_name = f"Notation LLM ({actual_count} offres)" if is_unrated_mode else f"Re-notation globale ({actual_count} offres)"
        task_desc = f"{button_label} ({re_limit} offres, backfill={do_backfill})"

        ok, msg = start_background_task(
            key="rerank",
            name=task_name,
            command=command,
            description=task_desc,
        )
        if ok:
            st.toast("Tâche lancée en arrière-plan !")
            st.rerun()
        else:
            st.error(msg)
