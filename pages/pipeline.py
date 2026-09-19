from __future__ import annotations

import os
import re
import sys
import subprocess
import streamlit as st
from pathlib import Path
from typing import Any
from dataclasses import dataclass

from utils.data import get_database, load_jobs, bump_data_version, source_distribution, _esc
from utils.styles import inject_styles
from utils.task_manager import (
    get_active_task,
    render_sidebar_task_badge,
    render_task_monitor,
    start_background_task,
)
from src.config import load_config, DEFAULT_CONFIG_PATH

PROJECT_ROOT = Path(__file__).resolve().parent.parent

st.set_page_config(page_title="Pipeline", page_icon=":material/settings:", layout="wide")

inject_styles()
render_sidebar_task_badge()

st.markdown("<h1>Pipeline & Maintenance</h1>", unsafe_allow_html=True)

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

PIPELINE_ACTIONS: tuple[PipelineAction, ...] = (
    PipelineAction(
        key="full_pipeline",
        label="Lancer le pipeline complet (Collecte → Backfill → Scoring Gemini)",
        script="run_pipeline.py",
        args=(),
        help="Exécute l'intégralité de la chaîne de traitement de manière automatisée.",
        status="Pipeline unifié en cours…",
    ),
    PipelineAction(
        key="collect",
        label="Collecter les offres uniquement",
        script="run_scrapers.py",
        args=("--no-scoring",),
        help="Collecte hybride (passe Fraîcheur puis Rattrapage), ingestion SQLite, mémoire de collecte et télémétrie.",
        status="Collecte en cours (fraîcheur → rattrapage → ingestion)…",
    ),
    PipelineAction(
        key="descriptions",
        label="Enrichir les fiches de poste (Backfill)",
        script="backfill_descriptions.py",
        args=(),
        help="Récupère les descriptions manquantes depuis les pages détail (LinkedIn / JobTeaser).",
        status="Enrichissement des fiches en cours (pages détail)…",
    ),
    PipelineAction(
        key="rerank",
        label="Juge LLM seul (Gemini)",
        script="run_scrapers.py",
        args=("--no-collect", "--trigger-rerank", "--top-rerank", "1000"),
        help="Sans nouvelle collecte : analyse par le juge LLM des offres non évaluées (rythme adapté au forfait).",
        status="Juge LLM en cours (évaluation des offres non notées)…",
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
    config_file = Path(DEFAULT_CONFIG_PATH)
    raw = config_file.read_text(encoding="utf-8")
    sources_yaml = "\n".join(f'    - "{s}"' for s in sources)
    queries_yaml = "\n".join(f'    - "{q}"' for q in queries)

    pat_src = r'(enabled_sources:\s*\n)(?:[ \t]*-[ \t]*[^\n]*\n)+'
    pat_queries = r'(target_queries:\s*\n)(?:[ \t]*-[ \t]*[^\n]*\n)+'
    pat_max = r'(max_offers_per_source:\s*)\d+'

    c1, n1 = re.subn(pat_src, rf'\g<1>{sources_yaml}\n', raw)
    c2, n2 = re.subn(pat_queries, rf'\g<1>{queries_yaml}\n', c1)
    c3, n3 = re.subn(pat_max, rf'\g<1>{max_offers}', c2)

    if n1 and n2 and n3:
        tmp = config_file.with_suffix(".tmp")
        tmp.write_text(c3, encoding="utf-8")
        tmp.replace(config_file)
        load_config.cache_clear()
    else:
        from src.config import save_config
        cfg = load_config()
        cfg.setdefault("scrapers", {})
        cfg["scrapers"]["enabled_sources"] = sources
        cfg["scrapers"]["target_queries"] = queries
        cfg["scrapers"]["max_offers_per_source"] = max_offers
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

    st.markdown("### Personnaliser & Lancer la collecte")
    st.caption("Ajustez les requêtes, plateformes cibles et volumes d'offres pour ce run ou enregistrez-les par défaut.")

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
            use_container_width=True,
            help="Lance immédiatement la collecte en tâche de fond avec les paramètres ci-dessus sans modifier config.yaml.",
            disabled=is_task_running,
        )
    with btn_col2:
        save_clicked = st.button(
            "Enregistrer comme paramètres par défaut",
            use_container_width=True,
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


def render_base_panel(jobs: list[dict[str, Any]]) -> None:
    """Panneau : état de la base et maintenance du pipeline."""
    # Affichage du moniteur de tâche interactive (barre, logs, bouton d'arrêt)
    render_task_monitor()

    active_task = get_active_task()
    is_task_running = bool(active_task and active_task.get("status") == "running")

    distribution = source_distribution(jobs)
    
    st.markdown("### État de la base SQLite")
    st.markdown(
        '<div class="sc-kv">'
        + "".join(f"<div>{_esc(label)} <b>{count}</b></div>" for label, count, _ in distribution)
        + "</div>",
        unsafe_allow_html=True,
    )
    st.caption(f"{len(jobs)} offres en base SQLite")
    
    if st.button("Actualiser la vue", help="Relit la base SQLite et invalide le cache de lecture du dashboard.", use_container_width=True):
        bump_data_version()
        st.rerun()
        
    render_custom_collection_form(is_task_running)

    st.markdown("---")
    st.markdown("### Actions de pipeline prédéfinies")
    if is_task_running:
        st.info("⏳ Un traitement est actuellement en cours. Vous pouvez suivre sa progression en direct ci-dessus.")

    for action in PIPELINE_ACTIONS:
        if st.button(
            action.label,
            use_container_width=True,
            help=action.help,
            key=f"pipeline-{action.key}",
            disabled=is_task_running,
        ):
            run_pipeline(action)
    st.caption(
        "Chaque action s'exécute en tâche de fond avec suivi en temps réel et survit à la navigation entre les pages. "
        "Sans clé GEMINI_API_KEY, l'étape de juge LLM est ignorée proprement."
    )


db = get_database()
data_version = int(st.session_state.get("data_version", 0))
jobs = load_jobs(db, data_version)

render_base_panel(jobs)
