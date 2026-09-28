from __future__ import annotations

import logging
import os
import re
import sys
import subprocess
import streamlit as st
from pathlib import Path
from typing import Any
from dataclasses import dataclass

from utils.data import get_database, load_jobs, bump_data_version, source_distribution, _esc
from utils.layout import page_setup, render_page_header
from utils.task_manager import (
    get_active_task,
    render_task_monitor,
    start_background_task,
)
from src.config import load_config, DEFAULT_CONFIG_PATH

PROJECT_ROOT = Path(__file__).resolve().parent.parent
logger = logging.getLogger(__name__)

page_setup()

from src.storage.cloud_storage import (
    is_cloud_storage_configured,
    get_remote_metadata,
    download_database,
    upload_database,
)

# Exécution du pipeline depuis le dashboard
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class PipelineAction:
    """Une action de maintenance lançable depuis le dashboard (script + arguments)."""
    key: str
    label: str
    script: str
    args: tuple[str, ...]
    help: str
    status: str
    icon: str = ":material/play_arrow:"

PIPELINE_ACTIONS: tuple[PipelineAction, ...] = (
    PipelineAction(
        key="full_pipeline",
        label="Pipeline complet",
        script="run_pipeline.py",
        args=(),
        help="Collecte → enrichissement des fiches → scoring par le juge LLM, en une seule tâche.",
        status="Pipeline unifié en cours…",
        icon=":material/rocket_launch:",
    ),
    PipelineAction(
        key="collect",
        label="Collecte seule",
        script="run_scrapers.py",
        args=("--no-scoring",),
        help="Collecte hybride (passe Fraîcheur puis Rattrapage), ingestion SQLite, mémoire de collecte et télémétrie.",
        status="Collecte en cours (fraîcheur → rattrapage → ingestion)…",
        icon=":material/travel_explore:",
    ),
    PipelineAction(
        key="descriptions",
        label="Enrichir les fiches",
        script="backfill_descriptions.py",
        args=(),
        help="Récupère les descriptions manquantes depuis les pages détail (LinkedIn / JobTeaser).",
        status="Enrichissement des fiches en cours (pages détail)…",
        icon=":material/description:",
    ),
    PipelineAction(
        key="rerank",
        label="Juge LLM seul",
        script="run_scrapers.py",
        args=("--no-collect", "--trigger-rerank", "--top-rerank", "1000"),
        help="Sans nouvelle collecte : analyse par le juge LLM des offres non évaluées (rythme adapté au forfait).",
        status="Juge LLM en cours (évaluation des offres non notées)…",
        icon=":material/gavel:",
    ),
)


def run_pipeline(action: PipelineAction) -> None:
    """Exécute une action du pipeline en arrière-plan sans bloquer l'interface."""
    command = [sys.executable, str(PROJECT_ROOT / action.script), *action.args]
    ok, msg = start_background_task(
        key=f"pipeline_{action.key}",
        name=action.label,
        command=command,
        description=action.help,
    )
    if ok:
        st.toast(f"Tâche lancée : {action.label}")
        st.rerun()
    else:
        st.warning(msg)


def save_default_settings(queries: list[str], sources: list[str], max_offers: int) -> None:
    """Met à jour config.yaml de manière atomique en préservant les commentaires."""
    from ruamel.yaml import YAML
    yaml = YAML()
    yaml.preserve_quotes = True

    config_file = Path(DEFAULT_CONFIG_PATH)
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            data = yaml.load(f)

        if 'scraping' not in data:
            data['scraping'] = {}
        
        data['scraping']['enabled_sources'] = sources
        data['scraping']['target_queries'] = queries
        data['scraping']['max_offers_per_source'] = max_offers

        tmp = config_file.with_suffix(".tmp")
        with open(tmp, 'w', encoding='utf-8') as f:
            yaml.dump(data, f)
            
        tmp.replace(config_file)
        load_config.cache_clear()
    except Exception as e:
        logger.error(f"Échec de la sauvegarde des paramètres via ruamel.yaml: {e}")
        from src.config import save_config
        cfg = load_config()
        cfg.setdefault("scraping", {})
        cfg["scraping"]["enabled_sources"] = sources
        cfg["scraping"]["target_queries"] = queries
        cfg["scraping"]["max_offers_per_source"] = max_offers
        save_config(cfg)


def render_custom_collection_form(is_task_running: bool = False) -> None:
    """Formulaire de paramétrage fin et d'exécution personnalisée de la collecte."""
    config = load_config()
    scrapers_cfg = config.get("scrapers", {})
    default_queries = scrapers_cfg.get("target_queries", [
        "Stage Data Scientist",
        "Stage Machine Learning",
        "Stage Recherche IA",
    ])
    default_sources = scrapers_cfg.get("enabled_sources", ["linkedin", "jobteaser"])
    default_max = int(scrapers_cfg.get("max_offers_per_source", 200))

    st.markdown(
        '<div class="sc-section-title">Collecte personnalisée'
        "<small>requêtes, plateformes et volumes pour ce run, ou enregistrés par défaut</small></div>",
        unsafe_allow_html=True,
    )

    queries_input = st.text_area(
        "Requêtes de recherche cibles (une par ligne)",
        value="\n".join(default_queries),
        height=110,
        help="Requêtes envoyées aux moteurs des plateformes d'emploi.",
    )

    col1, col2 = st.columns([1.2, 1.8], gap="medium")
    with col1:
        sources_selected = st.multiselect(
            "Plateformes cibles",
            options=["linkedin", "jobteaser"],
            default=[s for s in default_sources if s in ("linkedin", "jobteaser")] or ["linkedin", "jobteaser"],
            format_func=lambda s: "LinkedIn" if s == "linkedin" else "JobTeaser",
            help="Sélectionnez les sources à interroger lors de ce scrape.",
        )
    with col2:
        max_offers = st.slider(
            "Plafond d'offres par source",
            min_value=10,
            max_value=500,
            value=min(max(default_max, 10), 500),
            step=10,
            help="Nombre maximal d'offres à collecter par plateforme.",
        )

    passes_selected = st.multiselect(
        "Passes de collecte hybride",
        options=["freshness", "relevance"],
        default=["freshness", "relevance"],
        format_func=lambda p: (
            "Fraîcheur (tri chronologique, fenêtre 7 j, arrêt anticipé)"
            if p == "freshness"
            else "Rattrapage (classement pertinence, sans arrêt anticipé)"
        ),
        help="Combinez la veille quotidienne et le filet de rattrapage exhaustif.",
    )

    btn_col1, btn_col2 = st.columns([1.2, 1], gap="medium")
    with btn_col1:
        launch_clicked = st.button(
            "Lancer la collecte personnalisée",
            type="primary",
            icon=":material/play_arrow:",
            width="stretch",
            help="Lance immédiatement la collecte en tâche de fond avec les paramètres ci-dessus sans modifier config.yaml.",
            disabled=is_task_running,
        )
    with btn_col2:
        save_clicked = st.button(
            "Enregistrer comme paramètres par défaut",
            icon=":material/save:",
            width="stretch",
            help="Met à jour durablement la section scrapers de config.yaml avec ces valeurs.",
        )

    parsed_queries = [q.strip() for q in queries_input.splitlines() if q.strip()]

    if launch_clicked:
        if not parsed_queries:
            st.error("Veuillez renseigner au moins une requête cible.")
        elif not sources_selected:
            st.error("Veuillez activer au moins une plateforme de collecte.")
        else:
            args = ["--no-scoring"]
            args.extend(["--queries", ";".join(parsed_queries)])
            args.extend(["--sources", ",".join(sources_selected)])
            args.extend(["--max-offers", str(max_offers)])
            if passes_selected:
                args.extend(["--passes", ",".join(passes_selected)])

            action = PipelineAction(
                key="custom_collect",
                label="Collecte personnalisée",
                script="run_scrapers.py",
                args=tuple(args),
                help="Collecte avec paramètres sur-mesure",
                status="Collecte personnalisée en cours…",
            )
            run_pipeline(action)

    if save_clicked:
        if not parsed_queries:
            st.error("Veuillez renseigner au moins une requête cible avant d'enregistrer.")
        elif not sources_selected:
            st.error("Veuillez activer au moins une plateforme avant d'enregistrer.")
        else:
            save_default_settings(parsed_queries, sources_selected, max_offers)
            st.success("Paramètres enregistrés comme nouveaux défauts dans config.yaml !")
            st.toast("Configuration mise à jour !")


def _section(title: str, note: str = "") -> None:
    """Titre de section de la page."""
    small = f"<small>{note}</small>" if note else ""
    st.markdown(f'<div class="sc-section-title">{title}{small}</div>', unsafe_allow_html=True)


def render_actions(actions: tuple[PipelineAction, ...], is_task_running: bool, key_prefix: str) -> None:
    """Actions prédéfinies en grille de cartes (titre, description, bouton)."""
    cols = st.columns(2, gap="small")
    for index, action in enumerate(actions):
        with cols[index % 2]:
            with st.container(border=True, key=f"card-action-{key_prefix}-{action.key}"):
                st.markdown(f"**{action.label}**")
                st.caption(action.help)
                if st.button(
                    "Lancer",
                    key=f"{key_prefix}-{action.key}",
                    icon=action.icon,
                    type="primary" if action.key == "full_pipeline" else "secondary",
                    disabled=is_task_running,
                ):
                    run_pipeline(action)


def render_database_section(jobs: list[dict[str, Any]]) -> None:
    """État de la base SQLite, rafraîchissement et synchronisation cloud (R2 / S3)."""
    _section("Base de données", f"{len(jobs)} offres en base SQLite")
    distribution = source_distribution(jobs)
    c_stats, c_refresh = st.columns([3, 1], vertical_alignment="center")
    with c_stats:
        st.markdown(
            '<div class="sc-kv">'
            + "".join(f"<div>{_esc(label)} <b>{count}</b></div>" for label, count, _ in distribution)
            + "</div>",
            unsafe_allow_html=True,
        )
    with c_refresh:
        if st.button("Actualiser", icon=":material/refresh:", help="Relit la base SQLite et invalide le cache de lecture du dashboard.", width="stretch"):
            try:
                db = get_database()
                db.engine.dispose()
            except Exception:
                pass
            st.cache_resource.clear()
            st.cache_data.clear()
            bump_data_version(sync_cloud=False)
            st.rerun()

    _section("Synchronisation cloud", "Cloudflare R2 / S3")
    if not is_cloud_storage_configured():
        st.caption("Synchronisation cloud inactive. Configurez les variables R2/S3 pour lier un bucket.")
        return
    meta = get_remote_metadata()
    if meta:
        size_mb = meta["size_bytes"] / (1024 * 1024)
        date_str = meta["last_modified"].strftime("%d/%m/%Y à %H:%M UTC") if meta.get("last_modified") else "inconnue"
        st.success(f"Stockage distant connecté. Base distante : **{size_mb:.2f} Mo** (modifiée le {date_str}).", icon=":material/cloud_done:")
    else:
        st.info("Stockage distant configuré mais aucune base distante trouvée dans le bucket.", icon=":material/cloud_off:")

    c_sync1, c_sync2 = st.columns(2)
    with c_sync1:
        if st.button("Récupérer la base distante", icon=":material/cloud_download:", width="stretch", help="Force le téléchargement de la base depuis le bucket."):
            with st.spinner("Téléchargement de la base distante en cours…"):
                try:
                    db = get_database()
                    db.engine.dispose()
                except Exception:
                    pass
                if download_database(force=True):
                    st.cache_resource.clear()
                    st.cache_data.clear()
                    bump_data_version(sync_cloud=False)
                    st.toast("Base locale mise à jour depuis le cloud.")
                    st.rerun()
                else:
                    st.warning("Échec du téléchargement ou stockage vide.")
    with c_sync2:
        if st.button("Sauvegarder vers le cloud", icon=":material/cloud_upload:", width="stretch", help="Envoie la base SQLite actuelle vers le bucket."):
            with st.spinner("Envoi vers le cloud en cours…"):
                if upload_database():
                    st.toast("Base sauvegardée sur le cloud.")
                else:
                    st.error("Échec de l'envoi.")


def render_base_panel(jobs: list[dict[str, Any]]) -> None:
    """Page Pipeline : suivi de tâche, lancement des traitements, base et cloud."""
    render_page_header(
        "Pilotage",
        "Pipeline",
        "Lancez la collecte, l'enrichissement des fiches et le scoring en tâche de fond, "
        "puis gérez la base SQLite et sa synchronisation cloud.",
    )
    # Moniteur de tâche interactif (barre, logs, bouton d'arrêt)
    render_task_monitor()

    active_task = get_active_task()
    is_task_running = bool(active_task and active_task.get("status") == "running")
    if is_task_running:
        st.info("Un traitement est en cours : suivez sa progression ci-dessus. "
                "Les autres actions restent désactivées jusqu'à sa fin.", icon=":material/hourglass_top:")

    is_cloud_env = bool(os.getenv("RENDER") or os.getenv("ENVIRONMENT") == "production")
    if is_cloud_env:
        _section("Collecte & pipeline", "mode cloud")
        st.warning(
            "**Collecte automatique désactivée sur le serveur cloud** : LinkedIn et JobTeaser bloquent "
            "fréquemment les requêtes issues de datacenters.\n\n"
            "Pour collecter de nouvelles offres, lancez `python run_pipeline.py` sur votre machine : "
            "elles seront ensuite synchronisées avec ce tableau de bord.",
            icon=":material/shield:",
        )
        with st.expander("Mode avancé : forcer une action sur le serveur"):
            st.caption("Réservé au dépannage ou aux tests.")
            render_actions(PIPELINE_ACTIONS, is_task_running, "cloud-pipeline")
    else:
        _section("Lancer un traitement", "chaque action tourne en tâche de fond et survit à la navigation")
        render_actions(PIPELINE_ACTIONS, is_task_running, "pipeline")
        st.caption("Sans clé GEMINI_API_KEY, l'étape de juge LLM est ignorée proprement.")
        st.markdown('<hr class="sc-rule">', unsafe_allow_html=True)
        render_custom_collection_form(is_task_running)

    st.markdown('<hr class="sc-rule">', unsafe_allow_html=True)
    render_database_section(jobs)


db = get_database()
data_version = int(st.session_state.get("data_version", 0))
jobs = load_jobs(db, data_version)

render_base_panel(jobs)
