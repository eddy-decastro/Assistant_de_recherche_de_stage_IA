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
from utils.layout import page_setup, render_page_header
from utils.task_manager import (
    get_active_task,
    render_task_monitor,
    start_background_task,
)

CV_PATH = PROJECT_ROOT / "data" / "cv_eddy.txt"

page_setup()

render_page_header(
    "Pilotage",
    "Paramètres & Profil",
    "Personnalisez votre CV, vos critères de collecte et pilotez la ré-évaluation des offres par Gemini.",
)

tab_cv, tab_scraping, tab_v3, tab_rerank = st.tabs([
    ":material/person: Mon CV & profil",
    ":material/travel_explore: Recherches & collecte",
    ":material/balance: Grille de scoring v3",
    ":material/autorenew: Re-notation des offres",
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
        if st.button("Enregistrer le profil", type="primary", width="stretch", icon=":material/save:"):
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
            width="stretch",
            icon=":material/download:",
        )

    st.markdown("---")
    st.markdown("#### 3. Coordonnées de contact (Lettres de motivation & Export PDF)")
    st.caption("Ces coordonnées sont injectées automatiquement dans vos lettres pour un copier-coller immédiat sans aucun placeholder.")

    cfg_main = load_config()
    candidate_cfg = cfg_main.get("candidate", {})

    col_nom, col_tel = st.columns(2)
    with col_nom:
        c_name = st.text_input("Nom & Prénom", value=candidate_cfg.get("name", "Eddy DE CASTRO"), key="c_name_input")
    with col_tel:
        c_phone = st.text_input("Téléphone", value=candidate_cfg.get("phone", "06 98 82 44 85"), key="c_phone_input")

    col_mail, col_loc = st.columns(2)
    with col_mail:
        c_email = st.text_input("Email", value=candidate_cfg.get("email", "eddyprepa123@gmail.com"), key="c_email_input")
    with col_loc:
        c_location = st.text_input("Ville / Localisation", value=candidate_cfg.get("location", "Paris, France"), key="c_location_input")

    col_li, col_gh = st.columns(2)
    with col_li:
        c_linkedin = st.text_input("Profil LinkedIn", value=candidate_cfg.get("linkedin", "https://www.linkedin.com/in/eddy-de-castro/"), key="c_linkedin_input")
    with col_gh:
        c_github = st.text_input("Profil GitHub", value=candidate_cfg.get("github", "https://github.com/eddy-decastro"), key="c_github_input")

    c_title = st.text_input("Titre / Formation", value=candidate_cfg.get("title", "Élève-ingénieur Mines de Saint-Étienne — Double diplôme M2 Mathématiques en Action"), key="c_title_input")

    if st.button("Enregistrer les coordonnées", type="primary", icon=":material/badge:", key="save_candidate_btn"):
        cfg_main["candidate"] = {
            "name": c_name.strip(),
            "title": c_title.strip(),
            "phone": c_phone.strip(),
            "email": c_email.strip(),
            "linkedin": c_linkedin.strip(),
            "github": c_github.strip(),
            "location": c_location.strip(),
        }
        save_config(cfg_main)
        bump_data_version()
        st.success("Coordonnées enregistrées avec succès dans `config.yaml` !")



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


def save_scoring_v3_settings(
    min_duration: int,
    scaleup_floor: int,
    rd_floor: int,
    labo_floor: int,
    min_tech_depth: int,
    trust_scaleup: bool,
    encadrant_bonus: int,
    donnees_bonus: int,
    suite_bonus: int,
    bonus_cap: int,
    benchmark_penalty: int,
    hard_caps: dict[str, int],
) -> None:
    """Met à jour les paramètres de scoring v3 dans config.yaml en préservant les commentaires."""
    from ruamel.yaml import YAML
    from src.config import DEFAULT_CONFIG_PATH
    yaml = YAML()
    yaml.preserve_quotes = True
    config_file = Path(DEFAULT_CONFIG_PATH)
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            data = yaml.load(f)
        if 'scoring_v3' not in data:
            data['scoring_v3'] = {}
        data['scoring_v3']['min_duration_months'] = int(min_duration)
        if 'floors' not in data['scoring_v3']:
            data['scoring_v3']['floors'] = {}
        data['scoring_v3']['floors']['scaleup'] = int(scaleup_floor)
        data['scoring_v3']['floors']['rd'] = int(rd_floor)
        data['scoring_v3']['floors']['labo_public'] = int(labo_floor)
        data['scoring_v3']['floors']['min_technical_depth'] = int(min_tech_depth)
        data['scoring_v3']['floors']['trust_llm_scaleup'] = bool(trust_scaleup)

        if 'bonuses' not in data['scoring_v3']:
            data['scoring_v3']['bonuses'] = {}
        data['scoring_v3']['bonuses']['encadrant'] = int(encadrant_bonus)
        data['scoring_v3']['bonuses']['donnees'] = int(donnees_bonus)
        data['scoring_v3']['bonuses']['suite'] = int(suite_bonus)
        data['scoring_v3']['bonuses']['bonus_cap'] = int(bonus_cap)
        data['scoring_v3']['bonuses']['benchmark_penalty'] = int(benchmark_penalty)

        if 'hard_caps' not in data['scoring_v3']:
            data['scoring_v3']['hard_caps'] = {}
        for k, v in hard_caps.items():
            data['scoring_v3']['hard_caps'][k] = int(v)

        tmp = config_file.with_suffix(".tmp")
        with open(tmp, 'w', encoding='utf-8') as f:
            yaml.dump(data, f)
        tmp.replace(config_file)
        load_config.cache_clear()
    except Exception as e:
        cfg = load_config()
        cfg.setdefault('scoring_v3', {})
        cfg['scoring_v3']['min_duration_months'] = int(min_duration)
        cfg['scoring_v3'].setdefault('floors', {})
        cfg['scoring_v3']['floors']['scaleup'] = int(scaleup_floor)
        cfg['scoring_v3']['floors']['rd'] = int(rd_floor)
        cfg['scoring_v3']['floors']['labo_public'] = int(labo_floor)
        cfg['scoring_v3']['floors']['min_technical_depth'] = int(min_tech_depth)
        cfg['scoring_v3']['floors']['trust_llm_scaleup'] = bool(trust_scaleup)
        cfg['scoring_v3'].setdefault('bonuses', {})
        cfg['scoring_v3']['bonuses']['encadrant'] = int(encadrant_bonus)
        cfg['scoring_v3']['bonuses']['donnees'] = int(donnees_bonus)
        cfg['scoring_v3']['bonuses']['suite'] = int(suite_bonus)
        cfg['scoring_v3']['bonuses']['bonus_cap'] = int(bonus_cap)
        cfg['scoring_v3']['bonuses']['benchmark_penalty'] = int(benchmark_penalty)
        cfg['scoring_v3'].setdefault('hard_caps', {})
        for k, v in hard_caps.items():
            cfg['scoring_v3']['hard_caps'][k] = int(v)
        save_config(cfg)


# =========================================================================== #
# ONGLET 3 : GRILLE DE SCORING V3
# =========================================================================== #
with tab_v3:
    st.markdown("### Configuration de la grille de notation v3", unsafe_allow_html=True)
    st.caption("Ajustez les planchers par catégorie, les bonus de signaux et les plafonds éthiques stricts.")

    cfg_v3 = load_config().get("scoring_v3", {})
    floors_v3 = cfg_v3.get("floors", {})
    bonuses_v3 = cfg_v3.get("bonuses", {})
    caps_v3 = cfg_v3.get("hard_caps", {})

    with st.form("form_scoring_v3"):
        st.markdown("#### 1. Planchers par Catégorie (Floor)")
        st.caption("Les planchers ne s'appliquent qu'aux offres ayant une profondeur technique ≥ seuil.")
        col_f1, col_f2, col_f3 = st.columns(3)
        with col_f1:
            val_scaleup_floor = st.number_input("Plancher Scale-up (Next40/FT120) :", min_value=50, max_value=90, value=int(floors_v3.get("scaleup", 70)))
            val_min_tech = st.number_input("Profondeur technique minimale pour plancher :", min_value=1, max_value=5, value=int(floors_v3.get("min_technical_depth", 3)))
        with col_f2:
            val_rd_floor = st.number_input("Plancher Grand Groupe R&D / Labo privé :", min_value=40, max_value=80, value=int(floors_v3.get("rd", 60)))
            val_trust_scaleup = st.checkbox("Faire confiance au LLM pour le plancher Scale-up hors liste", value=bool(floors_v3.get("trust_llm_scaleup", False)))
        with col_f3:
            val_labo_floor = st.number_input("Plancher Laboratoire Public :", min_value=30, max_value=70, value=int(floors_v3.get("labo_public", 50)))
            val_min_duration = st.number_input("Durée minimale requise (mois) :", min_value=1, max_value=12, value=int(cfg_v3.get("min_duration_months", 4)))

        st.markdown("---")
        st.markdown("#### 2. Bonus & Pénalités Qualitatifs")
        st.caption("Les bonus ne s'appliquent que si la citation extraite est rigoureusement vérifiée dans le texte de l'offre.")
        col_b1, col_b2, col_b3 = st.columns(3)
        with col_b1:
            val_encadrant = st.number_input("Bonus Encadrant explicite :", min_value=0, max_value=15, value=int(bonuses_v3.get("encadrant", 6)))
            val_bonus_cap = st.number_input("Plafond total des bonus cumulés :", min_value=0, max_value=20, value=int(bonuses_v3.get("bonus_cap", 10)))
        with col_b2:
            val_donnees = st.number_input("Bonus Données réelles explicites :", min_value=0, max_value=10, value=int(bonuses_v3.get("donnees", 3)))
            val_penalty = st.number_input("Pénalité Données de benchmark seul :", min_value=0, max_value=15, value=int(bonuses_v3.get("benchmark_penalty", 5)))
        with col_b3:
            val_suite = st.number_input("Bonus Débouché / Thèse explicite :", min_value=0, max_value=10, value=int(bonuses_v3.get("suite", 3)))

        st.markdown("---")
        st.markdown("#### 3. Plafonds Éthiques Stricts (Hard Caps)")
        st.caption("Ces plafonds s'appliquent APRÈS les planchers et l'emportent toujours (priorité éthique).")
        col_c1, col_c2, col_c3 = st.columns(3)
        with col_c1:
            cap_defense = st.number_input("Plafond Défense / Armement :", min_value=0, max_value=50, value=int(caps_v3.get("DEFENSE", 10)))
            cap_trading = st.number_input("Plafond Trading / Finance :", min_value=0, max_value=50, value=int(caps_v3.get("TRADING", 25)))
        with col_c2:
            cap_bi = st.number_input("Plafond BI / Reporting :", min_value=0, max_value=50, value=int(caps_v3.get("BI_REPORTING", 30)))
            cap_esn = st.number_input("Plafond ESN en régie :", min_value=0, max_value=50, value=int(caps_v3.get("ESN_REGIE", 35)))
        with col_c3:
            cap_supervision = st.number_input("Plafond Encadrement absent :", min_value=0, max_value=50, value=int(caps_v3.get("ENCADREMENT_ABSENT", 35)))
            cap_shallow = st.number_input("Plafond IA superficielle :", min_value=0, max_value=60, value=int(caps_v3.get("SHALLOW_AI", 40)))

        submit_v3 = st.form_submit_button("Enregistrer les réglages de la grille v3", type="primary", icon=":material/save:")

    if submit_v3:
        hard_caps_dict = {
            "DEFENSE": int(cap_defense),
            "TRADING": int(cap_trading),
            "BI_REPORTING": int(cap_bi),
            "ESN_REGIE": int(cap_esn),
            "ENCADREMENT_ABSENT": int(cap_supervision),
            "SHALLOW_AI": int(cap_shallow),
        }
        save_scoring_v3_settings(
            min_duration=int(val_min_duration),
            scaleup_floor=int(val_scaleup_floor),
            rd_floor=int(val_rd_floor),
            labo_floor=int(val_labo_floor),
            min_tech_depth=int(val_min_tech),
            trust_scaleup=bool(val_trust_scaleup),
            encadrant_bonus=int(val_encadrant),
            donnees_bonus=int(val_donnees),
            suite_bonus=int(val_suite),
            bonus_cap=int(val_bonus_cap),
            benchmark_penalty=int(val_penalty),
            hard_caps=hard_caps_dict,
        )
        bump_data_version()
        st.success("Paramètres de notation v3 sauvegardés avec succès dans `config.yaml` !")


# =========================================================================== #
# ONGLET 4 : RE-NOTATION & NOTATION DES OFFRES
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
        st.caption(f"Durée estimée pour **{actual_count} offre(s)** : **~{est_str}** *(Forfait {tier_badge})*")

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
        st.info("Un traitement est actuellement en cours. Vous pouvez suivre sa progression ci-dessus ou naviguer librement sans interrompre le calcul.")

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
