from __future__ import annotations

from collections import Counter
from typing import Any, Sequence, Mapping

import altair as alt
import pandas as pd
import streamlit as st

from utils.data import (
    get_database,
    parse_timestamp,
    _esc,
    tone_class,
    source_label,
    source_color,
    load_jobs,
    effective_score,
    is_reranked,
)
from utils.styles import inject_styles
from utils.task_manager import render_sidebar_task_badge
from src.constants import *
from src.config import load_config
from src.storage.database import Database

st.set_page_config(page_title="Statistiques & Télémétrie", page_icon=":material/monitoring:", layout="wide")

inject_styles()
render_sidebar_task_badge()
db = get_database()
if not hasattr(db, "get_rejected_seen_jobs"):
    import importlib
    import src.storage.database
    importlib.reload(src.storage.database)
    get_database.clear()
    db = get_database()
    if not hasattr(db, "get_rejected_seen_jobs"):
        db.get_rejected_seen_jobs = src.storage.database.Database.get_rejected_seen_jobs.__get__(db, db.__class__)

# Panneau « Télémétrie des collectes » (tables scrape_runs / scrape_query_stats)
# --------------------------------------------------------------------------- #
RUN_TONES = {
    RUN_OK: "positive",
    RUN_PARTIAL: "warn",
    RUN_ERROR: "alert",
    RUN_INTERRUPTED: "alert",
    RUN_RUNNING: "accent",
}


def _fmt_stamp(value: Any) -> str:
    """Horodatage compact (``16/09 23:21``) d'une colonne de télémétrie."""
    stamp = parse_timestamp(value)
    return stamp.strftime("%d/%m %H:%M") if stamp else "—"


def _telemetry_runs_table(runs: Sequence[dict[str, Any]]) -> str:
    """Tableau des derniers runs de collecte (santé des sources)."""
    if not runs:
        return ""
    rows = "".join(
        "<tr>"
        f'<td class="sc-num">{_esc(_fmt_stamp(run.get("started_at")))}</td>'
        f'<td><span class="sc-status {tone_class(RUN_TONES.get(str(run.get("status")), "mute"))}">'
        f'<i class="sc-dot"></i>'
        f'{_esc(RUN_LABELS.get(str(run.get("status")), str(run.get("status") or "?")))}</span></td>'
        f'<td>{_esc(str(run.get("sources") or "—"))}</td>'
        f'<td class="sc-num">{int(run.get("total_found") or 0)}</td>'
        f'<td class="sc-num sc-strong">{int(run.get("total_validated") or 0)}</td>'
        f'<td class="sc-num">{int(run.get("total_inserted") or 0)}</td>'
        f'<td class="sc-num">{int(run.get("total_duplicates") or 0)}</td>'
        f'<td>{_esc(str(run.get("notes") or "—"))}</td>'
        "</tr>"
        for run in runs
    )
    return (
        '<table class="sc-table"><thead><tr>'
        "<th>Début</th><th>État</th><th>Sources</th><th>Vues</th>"
        "<th>Retenues</th><th>Nouvelles</th><th>Doublons</th><th>Motifs d'arrêt</th>"
        "</tr></thead><tbody>" + rows + "</tbody></table>"
    )


def _objective_cell(kept: Any, target: Any) -> str:
    """Cellule « objectif » d'une passe : ``retenues/objectif`` avec la tonalité qui va bien."""
    target_value = int(target or 0)
    if not target_value:
        return '<td class="sc-num">—</td>'
    kept_value = int(kept or 0)
    tone = "positive" if kept_value >= target_value else "warn"
    return (
        f'<td><span class="sc-badge {tone_class(tone)}">'
        f"{kept_value}/{target_value}</span></td>"
    )


def _counters_strip(totals: Mapping[str, int]) -> str:
    """Bandeau des quatre compteurs du dernier run : cherchées, refusées, déjà vues, acceptées.

    C'est la réponse chiffrée à « que fait la collecte ? » : le volume parcouru, ce
    qu'elle écarte, ce qu'elle reconnaît et ce qu'elle retient.
    """
    cards = (
        ("Cherchées", int(totals.get("cards_seen") or 0), "cartes de flux parcourues"),
        ("Refusées", int(totals.get("refused") or 0), "hors sujet + hors fenêtre"),
        ("Déjà vues", int(totals.get("already_seen") or 0), "en base ou déjà croisées"),
        ("Acceptées", int(totals.get("jobs_kept") or 0), "retenues pour la base"),
    )
    cells = "".join(
        '<div class="sc-kpi">'
        f'<div class="sc-kpi-label">{_esc(label)}</div>'
        f'<div class="sc-kpi-value">{value}</div>'
        f'<div class="sc-kpi-label">{_esc(note)}</div>'
        "</div>"
        for label, value, note in cards
    )
    return f'<div class="sc-kpis">{cells}</div>'


def _run_stamp(stamps: Mapping[str, str], run_id: Any) -> str:
    """Horodatage compact du run, ou son identifiant court en repli."""
    return stamps.get(str(run_id)) or f"run {str(run_id)[:8]}"


def _collection_counters_table(
    counters: Sequence[dict[str, Any]], stamps: Mapping[str, str]
) -> str:
    """Compteurs de collecte par (run, source) : cherchées, refusées, déjà vues, acceptées.

    Chaque colonne est décomposée dans son motif : « refusées » distingue le filtre
    métier (hors sujet) du hors-fenêtre, « déjà vues » distingue la mémoire de
    collecte des doublons internes au run. C'est ce qui permet de dire, sans lire un
    journal, si une collecte maigre vient du bruit ou d'un flux déjà parcouru.
    """
    if not counters:
        return ""
    rows = "".join(
        "<tr>"
        f'<td class="sc-num">{_esc(_run_stamp(stamps, row.get("run_id")))}</td>'
        f'<td>{_esc(str(row.get("source") or "?"))}</td>'
        f'<td class="sc-num">{int(row.get("cards_seen") or 0)}</td>'
        f'<td class="sc-num">{int(row.get("refused") or 0)}'
        f'<br><span class="sc-kpi-label">{int(row.get("jobs_rejected") or 0)} hors sujet · '
        f'{int(row.get("jobs_out_of_window") or 0)} hors fenêtre</span></td>'
        f'<td class="sc-num">{int(row.get("already_seen") or 0)}'
        f'<br><span class="sc-kpi-label">{int(row.get("jobs_known") or 0)} en base · '
        f'{int(row.get("jobs_duplicate") or 0)} doublons du run</span></td>'
        f'<td class="sc-num sc-strong">{int(row.get("jobs_kept") or 0)}</td>'
        "</tr>"
        for row in counters
    )
    return (
        '<table class="sc-table"><thead><tr>'
        "<th>Run</th><th>Source</th><th>Cherchées</th><th>Refusées</th>"
        "<th>Déjà vues</th><th>Acceptées</th>"
        "</tr></thead><tbody>" + rows + "</tbody></table>"
    )


def _objectives_table(
    objectives: Sequence[dict[str, Any]], stamps: Mapping[str, str]
) -> str:
    """Objectifs de collecte par (run, source, passe) et leur état.

    « Objectif atteint », « vivier épuisé » (rien à regretter) et « flux tronqué »
    (à relancer) sont trois situations distinctes : les confondre reviendrait à
    croire une collecte complète alors qu'un plafond l'a coupée.
    """
    if not objectives:
        return ""
    rows: list[str] = []
    for item in objectives:
        target = int(item.get("target_new") or 0)
        kept = int(item.get("jobs_kept") or 0)
        if not target:
            state, tone = "aucun objectif", "mute"
        elif kept >= target:
            state, tone = f"objectif atteint ({kept}/{target})", "positive"
        elif item.get("incomplete"):
            state, tone = f"{kept}/{target} — flux tronqué (à relancer)", "alert"
        else:
            state, tone = f"{kept}/{target} — vivier épuisé", "warn"
        rows.append(
            "<tr>"
            f'<td class="sc-num">{_esc(_run_stamp(stamps, item.get("run_id")))}</td>'
            f'<td>{_esc(str(item.get("source") or "?"))}</td>'
            f'<td>{_esc(pass_label(item.get("mode")))}</td>'
            f'<td class="sc-num">{int(item.get("pages") or 0)}</td>'
            f'<td class="sc-num">{int(item.get("cards_seen") or 0)}</td>'
            f'<td class="sc-num sc-strong">{kept}</td>'
            f'<td class="sc-num">{target or "—"}</td>'
            f'<td><span class="sc-badge {tone_class(tone)}">{_esc(state)}</span></td>'
            "</tr>"
        )
    return (
        '<table class="sc-table"><thead><tr>'
        "<th>Run</th><th>Source</th><th>Passe</th><th>Pages</th><th>Vues</th>"
        "<th>Retenues</th><th>Objectif</th><th>État</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def _refusals_table(breakdown: Sequence[dict[str, Any]]) -> str:
    """Répartition des décisions de la mémoire de collecte : ce qui est refusé, et pourquoi."""
    if not breakdown:
        return ""
    rows = "".join(
        "<tr>"
        f'<td>{_esc(seen_decision_label(row.get("decision")))}</td>'
        f'<td>{_esc(row.get("rejection_reason") or "—")}</td>'
        f'<td class="sc-num sc-strong">{int(row.get("total") or 0)}</td>'
        "</tr>"
        for row in breakdown
    )
    return (
        '<table class="sc-table"><thead><tr>'
        "<th>Décision</th><th>Motif</th><th>Offres</th>"
        "</tr></thead><tbody>" + rows + "</tbody></table>"
    )


def _telemetry_passes_table(stats: Sequence[dict[str, Any]]) -> str:
    """Tableau des dernières passes : une ligne par (source, requête, mode)."""
    if not stats:
        return ""
    rows = "".join(
        "<tr>"
        f'<td class="sc-num">'
        f'{_esc(_fmt_stamp(stat.get("finished_at") or stat.get("started_at")))}</td>'
        f'<td>{_esc(str(stat.get("source") or "?"))}</td>'
        f'<td>{_esc(str(stat.get("query") or ""))}</td>'
        f'<td>{_esc(pass_label(stat.get("mode")))}</td>'
        f'<td class="sc-num">{int(stat.get("pages_fetched") or 0)}</td>'
        f'<td class="sc-num">{int(stat.get("cards_seen") or 0)}</td>'
        f'<td class="sc-num sc-strong">{int(stat.get("jobs_kept") or 0)}</td>'
        f'<td class="sc-num">{int(stat.get("jobs_known") or 0)}</td>'
        f'{_objective_cell(stat.get("jobs_kept"), stat.get("target_new"))}'
        f'<td><span class="sc-badge '
        f'{tone_class("alert" if is_incomplete_stop(stat.get("stop_reason")) else "mute")}">'
        f'{_esc(stop_reason_label(stat.get("stop_reason")))}</span></td>'
        "</tr>"
        for stat in stats
    )
    return (
        '<table class="sc-table"><thead><tr>'
        "<th>Fin</th><th>Source</th><th>Requête</th><th>Passe</th><th>Pages</th>"
        "<th>Vues</th><th>Retenues</th><th>Connues</th><th>Objectif</th><th>Arrêt</th>"
        "</tr></thead><tbody>" + rows + "</tbody></table>"
    )


def render_telemetry(db: Database, runs_limit: int = 8, passes_limit: int = 20) -> None:
    """Panneau d'observabilité : état de santé des collectes et raisons d'arrêt.

    Répond à une question opérationnelle précise : la collecte s'est-elle arrêtée
    parce que le vivier était épuisé (rien perdu) ou parce qu'un quota, un plafond
    de pages ou un rate limit a tronqué le flux (donnée potentiellement manquée) ?
    """
    runs = db.get_recent_runs(limit=runs_limit)
    passes = db.get_recent_query_stats(limit=passes_limit)
    counters = db.get_collection_counters(limit=runs_limit)
    objectives = db.get_pass_objectives(limit=runs_limit)
    refusals = db.get_seen_decision_breakdown()
    if not runs and not passes:
        st.markdown(
            '<div class="sc-empty">Aucune télémétrie enregistrée.<br>'
            "Lancez une collecte (<code>python run_scrapers.py</code>) : chaque passe y "
            "consignera sa raison d'arrêt.</div>",
            unsafe_allow_html=True,
        )
        return

    last = runs[0] if runs else {}
    losses = [stat for stat in passes if is_incomplete_stop(stat.get("stop_reason"))]
    st.markdown(
        '<div class="sc-stream"><span class="sc-stream-count">'
        f"{len(runs)} run(s) · {len(passes)} passe(s) tracée(s)</span>"
        '<span class="sc-stream-note">'
        f"Dernier run : {_esc(_fmt_stamp(last.get('started_at')))} · "
        f"{_esc(RUN_LABELS.get(str(last.get('status')), 'inconnu'))} · "
        f"mémoire de collecte : {db.count_seen_jobs()} offre(s)</span></div>",
        unsafe_allow_html=True,
    )
    if losses:
        reasons = ", ".join(
            sorted({stop_reason_label(stat.get("stop_reason")) for stat in losses})
        )
        st.markdown(
            f'<div class="sc-alert {tone_class("alert")}"><span class="sc-alert-icon">⚠️</span>'
            f"<span><b>{len(losses)} passe(s) interrompue(s)</b> — du flux a pu être perdu "
            f"({_esc(reasons)}). Vérifiez la source concernée avant de conclure à un "
            "vivier épuisé.</span></div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<div class="sc-alert {tone_class("positive")}"><span class="sc-alert-icon">✓</span>'
            "<span>Aucune passe interrompue : les collectes tracées se sont closes sur un "
            "vivier épuisé ou un arrêt anticipé.</span></div>",
            unsafe_allow_html=True,
        )

    # --- Compteurs du dernier run : cherchées / refusées / déjà vues / acceptées ---
    stamps = {str(run.get("id")): _fmt_stamp(run.get("started_at")) for run in runs}
    last_run_id = str(runs[0].get("id")) if runs else ""
    last_counters = [
        row for row in counters if str(row.get("run_id")) == last_run_id
    ]
    if last_counters:
        totals = {
            key: sum(int(row.get(key) or 0) for row in last_counters)
            for key in ("cards_seen", "refused", "already_seen", "jobs_kept")
        }
        st.markdown(_counters_strip(totals), unsafe_allow_html=True)
        st.caption(
            "Dernier run — cherchées : cartes de flux réellement parcourues · refusées : "
            "écartées par le filtre métier (hors sujet) ou parce qu'antérieures à la fenêtre "
            "· déjà vues : déjà en base ou croisées dans ce run · acceptées : retenues, donc "
            "candidates à l'ingestion."
        )
    if counters:
        st.markdown(
            '<div class="sc-telemetry"><div class="sc-section">'
            "Compteurs par source — cherchées / refusées / déjà vues / acceptées</div>",
            unsafe_allow_html=True,
        )
        st.markdown(_collection_counters_table(counters, stamps), unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)
    if objectives:
        st.markdown(
            '<div class="sc-telemetry"><div class="sc-section">'
            "Objectifs de collecte — 40 dernières, puis 10 plus pertinentes</div>",
            unsafe_allow_html=True,
        )
        st.markdown(_objectives_table(objectives, stamps), unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown(
        '<div class="sc-telemetry"><div class="sc-section">Runs de collecte</div>',
        unsafe_allow_html=True,
    )
    st.markdown(_telemetry_runs_table(runs), unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown(
        '<div class="sc-telemetry"><div class="sc-section">'
        "Dernières passes (raison d'arrêt)</div>",
        unsafe_allow_html=True,
    )
    st.markdown(_telemetry_passes_table(passes), unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

    if refusals:
        st.markdown(
            '<div class="sc-telemetry"><div class="sc-section">'
            "Ce qui est refusé — mémoire de collecte, 30 derniers jours</div>",
            unsafe_allow_html=True,
        )
        st.markdown(_refusals_table(refusals), unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# Normalisation géographique
# --------------------------------------------------------------------------- #
def normalize_region(loc: str | None) -> str:
    """Rapproche une localisation textuelle brute d'un grand bassin d'emploi français."""
    if not loc:
        return "Autres / Non précisé"
    txt = loc.lower()
    idf_kw = [
        "paris", "île-de-france", "ile-de-france", "idf", "vélizy", "velizy",
        "nanterre", "saclay", "massy", "boulogne", "courbevoie", "clichy",
        "guyancourt", "issy", "meudon", "montrouge", "puteaux", "rueil",
        "saint-denis", "st denis", "versailles", "evry", "évry", "palaiseau",
        "fontenay", "châtillon", "chatillon", "antony", "créteil", "creteil",
        "ivry", "montreuil", "cergy", "neuilly", "levallois", "charenton",
        "suresnes", "bagneux", "arcueil", "villejuif", "trappes",
        "roissy", "le plessis", "saint-ouen", "st ouen"
    ]
    if any(k in txt for k in idf_kw):
        return "Paris & Île-de-France"
    paca_kw = [
        "sophia", "valbonne", "nice", "cannes", "antibes", "marseille",
        "aix-en-provence", "aix en provence", "toulon", "paca", "provence"
    ]
    if any(k in txt for k in paca_kw):
        return "PACA & Côte d'Azur"
    aura_kw = [
        "lyon", "villeurbanne", "grenoble", "saint-étienne", "saint etienne",
        "st-etienne", "clermont", "annecy", "chambéry", "chambery", "rhône", "rhone"
    ]
    if any(k in txt for k in aura_kw):
        return "Auvergne-Rhône-Alpes"
    occitanie_kw = ["toulouse", "montpellier", "blagnac", "labège", "labege", "occitanie"]
    if any(k in txt for k in occitanie_kw):
        return "Occitanie"
    na_kw = ["bordeaux", "mérignac", "merignac", "pessac", "pau", "nouvelle-aquitaine"]
    if any(k in txt for k in na_kw):
        return "Nouvelle-Aquitaine"
    ouest_kw = ["nantes", "rennes", "brest", "angers", "bretagne", "pays de la loire"]
    if any(k in txt for k in ouest_kw):
        return "Bretagne & Pays de la Loire"
    nord_est_kw = [
        "lille", "villeneuve-d'ascq", "strasbourg", "nancy", "metz",
        "hauts-de-france", "grand est", "rouen", "reims"
    ]
    if any(k in txt for k in nord_est_kw):
        return "Hauts-de-France & Grand Est"
    return "Autres / Non précisé"


# --------------------------------------------------------------------------- #
# 1. Mes Candidatures (Statistiques Personnelles)
# --------------------------------------------------------------------------- #
def _render_personal_analytics(jobs: list[dict[str, Any]]) -> None:
    """Indicateurs de suivi de vos candidatures et rythme d'envoi."""
    st.markdown(
        '<div class="sc-telemetry"><div class="sc-section" style="font-size:1.3rem;">'
        "🎯 Suivi de vos candidatures personnelles</div></div>",
        unsafe_allow_html=True,
    )
    if not jobs:
        st.info("Aucune donnée disponible.")
        return

    applied_jobs = [j for j in jobs if j.get("status") in (STATUS_APPLIED, STATUS_INTERVIEW)]
    interview_jobs = [j for j in jobs if j.get("status") == STATUS_INTERVIEW]
    archived_jobs = [j for j in jobs if j.get("status") == STATUS_IGNORED]
    new_jobs = [j for j in jobs if j.get("status") == STATUS_NEW]

    nb_applied = len(applied_jobs)
    nb_interview = len(interview_jobs)
    nb_archived = len(archived_jobs)
    rate = (nb_interview / nb_applied * 100.0) if nb_applied > 0 else 0.0

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Candidatures envoyées", f"{nb_applied}")
    col2.metric("Entretiens décrochés", f"{nb_interview}")
    col3.metric("Taux d'entretien", f"{rate:.1f} %")
    col4.metric("Offres archivées", f"{nb_archived}")

    st.markdown('<hr class="sc-rule">', unsafe_allow_html=True)

    col_hist, col_status = st.columns([3, 2], gap="large")

    with col_hist:
        st.markdown(
            '<div class="sc-telemetry"><div class="sc-section" style="font-size:1.1rem;">'
            "Rythme d'envoi des candidatures (par jour)</div></div>",
            unsafe_allow_html=True,
        )
        if not applied_jobs:
            st.info("Aucune candidature envoyée pour l'instant. Dès que vous passez une offre en 'Postulé' ou 'Entretien', son horodatage apparaîtra ici.")
        else:
            dates = []
            for j in applied_jobs:
                dt_val = j.get("applied_at") or j.get("created_at")
                parsed = parse_timestamp(dt_val)
                if parsed:
                    dates.append(parsed.date())
            if dates:
                df_daily = pd.DataFrame({"date": dates})
                counts = df_daily.groupby("date").size().reset_index(name="Candidatures")
                counts["date"] = pd.to_datetime(counts["date"])
                counts = counts.sort_values("date")

                chart_rhythm = (
                    alt.Chart(counts)
                    .mark_bar(color="#2563eb", cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
                    .encode(
                        x=alt.X("date:T", title="Date", axis=alt.Axis(format="%d/%m", labelAngle=0)),
                        y=alt.Y("Candidatures:Q", title="Candidatures envoyées", axis=alt.Axis(tickMinStep=1)),
                        tooltip=[
                            alt.Tooltip("date:T", title="Date", format="%d/%m/%Y"),
                            alt.Tooltip("Candidatures:Q", title="Candidatures envoyées"),
                        ],
                    )
                    .properties(height=260)
                )
                st.altair_chart(chart_rhythm, use_container_width=True)
            else:
                st.info("Horodatage indisponible.")

    with col_status:
        st.markdown(
            '<div class="sc-telemetry"><div class="sc-section" style="font-size:1.1rem;">'
            "Répartition globale des offres par statut</div></div>",
            unsafe_allow_html=True,
        )
        status_data = [
            {"Statut": "Nouveau", "Nombre": len(new_jobs)},
            {"Statut": "Postulé", "Nombre": len(applied_jobs) - len(interview_jobs)},
            {"Statut": "Entretien", "Nombre": len(interview_jobs)},
            {"Statut": "Archivé", "Nombre": len(archived_jobs)},
        ]
        df_status = pd.DataFrame(status_data)
        chart_status = (
            alt.Chart(df_status)
            .mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4)
            .encode(
                y=alt.Y("Statut:N", sort=["Nouveau", "Postulé", "Entretien", "Archivé"], title=None),
                x=alt.X("Nombre:Q", title="Nombre d'offres"),
                color=alt.Color("Statut:N", scale=alt.Scale(
                    domain=["Nouveau", "Postulé", "Entretien", "Archivé"],
                    range=["#64748b", "#2563eb", "#059669", "#d97706"]
                ), legend=None),
                tooltip=["Statut", "Nombre"],
            )
            .properties(height=260)
        )
        st.altair_chart(chart_status, use_container_width=True)


# --------------------------------------------------------------------------- #
# 2. Cartographie & Entreprises
# --------------------------------------------------------------------------- #
def _render_geo_and_companies(jobs: list[dict[str, Any]], db: Database) -> None:
    """Cartographie géographique, classement des entreprises et match LinkedIn vs JobTeaser."""
    if not jobs:
        st.info("Aucune donnée disponible.")
        return

    # A. Cartographie
    st.markdown(
        '<div class="sc-telemetry"><div class="sc-section" style="font-size:1.3rem;">'
        "🗺️ Répartition géographique des opportunités</div></div>",
        unsafe_allow_html=True,
    )
    region_counts = Counter([normalize_region(j.get("location")) for j in jobs])
    total_jobs = len(jobs)
    df_geo = pd.DataFrame([
        {
            "Région": r,
            "Offres": c,
            "Part": f"{c / total_jobs * 100:.1f} %",
            "pct_num": round(c / total_jobs * 100, 1),
        }
        for r, c in region_counts.most_common()
    ])

    chart_geo = (
        alt.Chart(df_geo)
        .mark_bar(color="#3b82f6", cornerRadiusTopRight=4, cornerRadiusBottomRight=4)
        .encode(
            y=alt.Y("Région:N", sort="-x", title=None),
            x=alt.X("Offres:Q", title="Nombre d'offres"),
            tooltip=["Région", "Offres", "Part"],
        )
        .properties(height=250)
    )
    st.altair_chart(chart_geo, use_container_width=True)

    st.markdown('<hr class="sc-rule">', unsafe_allow_html=True)

    # B. Top 10 Recruteurs & Top 10 R&D
    col_vol, col_qual = st.columns(2, gap="large")

    with col_vol:
        st.markdown(
            '<div class="sc-telemetry"><div class="sc-section" style="font-size:1.15rem;">'
            "Top 10 des entreprises qui recrutent le plus (Volume)</div></div>",
            unsafe_allow_html=True,
        )
        comp_counts = Counter([j.get("company").strip() for j in jobs if j.get("company") and j.get("company").strip()])
        top_vol = comp_counts.most_common(10)
        df_vol = pd.DataFrame(top_vol, columns=["Entreprise", "Offres"])

        chart_vol = (
            alt.Chart(df_vol)
            .mark_bar(color="#0284c7", cornerRadiusTopRight=4, cornerRadiusBottomRight=4)
            .encode(
                y=alt.Y("Entreprise:N", sort="-x", title=None),
                x=alt.X("Offres:Q", title="Nombre d'offres publiées"),
                tooltip=["Entreprise", "Offres"],
            )
            .properties(height=280)
        )
        st.altair_chart(chart_vol, use_container_width=True)

    with col_qual:
        st.markdown(
            '<div class="sc-telemetry"><div class="sc-section" style="font-size:1.15rem;">'
            "Top 10 des entreprises les plus cotées en R&D</div></div>",
            unsafe_allow_html=True,
        )
        by_comp: dict[str, list[dict[str, Any]]] = {}
        for j in jobs:
            c = (j.get("company") or "").strip()
            if c:
                by_comp.setdefault(c, []).append(j)

        qual_rows = []
        for c, j_list in by_comp.items():
            if len(j_list) >= 2:
                scores = [effective_score(j) for j in j_list]
                avg = sum(scores) / len(scores)
                t1 = any(j.get("company_tier") == TIER_1 for j in j_list)
                label = f"⭐ {c}" if t1 else c
                qual_rows.append({
                    "Entreprise": label,
                    "Score R&D moyen": round(avg, 1),
                    "Offres": len(j_list),
                })

        qual_rows.sort(key=lambda x: x["Score R&D moyen"], reverse=True)
        df_qual = pd.DataFrame(qual_rows[:10])

        if not df_qual.empty:
            chart_qual = (
                alt.Chart(df_qual)
                .mark_bar(color="#059669", cornerRadiusTopRight=4, cornerRadiusBottomRight=4)
                .encode(
                    y=alt.Y("Entreprise:N", sort="-x", title=None),
                    x=alt.X("Score R&D moyen:Q", scale=alt.Scale(domain=[0, 100]), title="Score moyen / 100"),
                    tooltip=["Entreprise", "Score R&D moyen", "Offres"],
                )
                .properties(height=280)
            )
            st.altair_chart(chart_qual, use_container_width=True)
            st.caption("Calculé sur les entreprises ayant au moins 2 offres pour la représentativité. (⭐ = Scale-up / Lab Tier 1)")
        else:
            st.info("Données insuffisantes.")

    st.markdown('<hr class="sc-rule">', unsafe_allow_html=True)

    # C. Match des Plateformes : LinkedIn vs JobTeaser
    st.markdown(
        '<div class="sc-telemetry"><div class="sc-section" style="font-size:1.2rem;">'
        "⚔️ Comparatif des Plateformes : LinkedIn vs JobTeaser</div></div>",
        unsafe_allow_html=True,
    )

    col_li, col_jt = st.columns(2, gap="large")

    sources_data = {}
    for src in ["linkedin", "jobteaser"]:
        src_jobs = [j for j in jobs if (j.get("source") or "").lower() == src]
        src_reranked = [j for j in src_jobs if is_reranked(j)]
        avg_score = (sum(effective_score(j) for j in src_reranked) / len(src_reranked)) if src_reranked else 0.0
        cœur_cible = [j for j in src_jobs if effective_score(j) >= 80.0]
        # Rejets enregistrés dans seen_jobs
        rejected_seen = db.get_rejected_seen_jobs(limit=10000, source=src) if hasattr(db, "get_rejected_seen_jobs") else []
        sources_data[src] = {
            "total": len(src_jobs),
            "evaluated": len(src_reranked),
            "avg_score": avg_score,
            "top80": len(cœur_cible),
            "rejected": len(rejected_seen),
        }

    with col_li:
        li = sources_data["linkedin"]
        st.markdown("#### 🔵 LinkedIn")
        c1, c2, c3 = st.columns(3)
        c1.metric("Offres retenues", f"{li['total']}")
        c2.metric("Score R&D moyen", f"{li['avg_score']:.1f} / 100")
        c3.metric("Cœur de cible (≥80)", f"{li['top80']}")
        st.caption(f"Cartes éliminées par le filtre métier : {li['rejected']} offres écartées")

    with col_jt:
        jt = sources_data["jobteaser"]
        st.markdown("#### 🟠 JobTeaser")
        c1, c2, c3 = st.columns(3)
        c1.metric("Offres retenues", f"{jt['total']}")
        c2.metric("Score R&D moyen", f"{jt['avg_score']:.1f} / 100")
        c3.metric("Cœur de cible (≥80)", f"{jt['top80']}")
        st.caption(f"Cartes éliminées par le filtre métier : {jt['rejected']} offres écartées")


# --------------------------------------------------------------------------- #
# 3. Qualité R&D & Technologies
# --------------------------------------------------------------------------- #
def _render_rd_and_tech(jobs: list[dict[str, Any]]) -> None:
    """Distribution des scores R&D, jauges des 5 sous-critères et baromètre technologique."""
    if not jobs:
        st.info("Aucune donnée disponible.")
        return

    # A. Distribution des scores & verdicts
    st.markdown(
        '<div class="sc-telemetry"><div class="sc-section" style="font-size:1.3rem;">'
        "🧠 Distribution des scores R&D et verdicts du juge LLM</div></div>",
        unsafe_allow_html=True,
    )

    reranked_jobs = [j for j in jobs if is_reranked(j)]
    scores = [effective_score(j) for j in reranked_jobs] if reranked_jobs else [effective_score(j) for j in jobs]

    col_scores, col_verdicts = st.columns(2, gap="large")

    with col_scores:
        st.markdown(
            '<div class="sc-telemetry"><div class="sc-section" style="font-size:1.1rem;">'
            "Histogramme des scores R&D (0 à 100)</div></div>",
            unsafe_allow_html=True,
        )
        bins = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 101]
        labels = ["0-9", "10-19", "20-29", "30-39", "40-49", "50-59", "60-69", "70-79", "80-89", "90-100"]
        binned = pd.cut(scores, bins=bins, labels=labels, right=False)
        df_scores = pd.DataFrame({"Tranche": labels, "Offres": [int((binned == l).sum()) for l in labels]})

        def get_tier_label(tranche: str) -> str:
            val = int(tranche.split("-")[0])
            if val >= 80: return "Cœur de cible (≥80)"
            if val >= 60: return "Pertinent (60-79)"
            if val >= 40: return "Mitigé (40-59)"
            return "Hors sujet (<40)"

        df_scores["Niveau"] = df_scores["Tranche"].apply(get_tier_label)
        color_scale = alt.Scale(
            domain=["Cœur de cible (≥80)", "Pertinent (60-79)", "Mitigé (40-59)", "Hors sujet (<40)"],
            range=["#059669", "#2563eb", "#d97706", "#dc2626"],
        )
        chart_scores = (
            alt.Chart(df_scores)
            .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
            .encode(
                x=alt.X("Tranche:N", sort=labels, title="Tranche de note"),
                y=alt.Y("Offres:Q", title="Nombre d'offres"),
                color=alt.Color("Niveau:N", scale=color_scale, title="Niveau d'alignement"),
                tooltip=["Tranche", "Offres", "Niveau"],
            )
            .properties(height=260)
        )
        st.altair_chart(chart_scores, use_container_width=True)

    with col_verdicts:
        st.markdown(
            '<div class="sc-telemetry"><div class="sc-section" style="font-size:1.1rem;">'
            "Répartition par verdict du juge</div></div>",
            unsafe_allow_html=True,
        )
        verdict_counts: Counter[str] = Counter()
        for j in reranked_jobs:
            v = (j.get("verdict") or "").upper()
            if "EXCELLENT" in v:
                verdict_counts["EXCELLENT"] += 1
            elif "BON" in v:
                verdict_counts["BON"] += 1
            elif "MITIG" in v:
                verdict_counts["MITIGÉ"] += 1
            elif "HORS" in v or "SUJET" in v:
                verdict_counts["HORS SUJET"] += 1

        v_order = ["EXCELLENT", "BON", "MITIGÉ", "HORS SUJET"]
        df_verdicts = pd.DataFrame([
            {"Verdict": k, "Offres": verdict_counts.get(k, 0)} for k in v_order
        ])
        v_colors = alt.Scale(
            domain=["EXCELLENT", "BON", "MITIGÉ", "HORS SUJET"],
            range=["#059669", "#2563eb", "#d97706", "#dc2626"],
        )
        chart_verdicts = (
            alt.Chart(df_verdicts)
            .mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4)
            .encode(
                y=alt.Y("Verdict:N", sort=v_order, title=None),
                x=alt.X("Offres:Q", title="Nombre d'offres"),
                color=alt.Color("Verdict:N", scale=v_colors, legend=None),
                tooltip=["Verdict", "Offres"],
            )
            .properties(height=260)
        )
        st.altair_chart(chart_verdicts, use_container_width=True)

    st.markdown('<hr class="sc-rule">', unsafe_allow_html=True)

    # B. Jauges des 5 sous-scores moyens
    st.markdown(
        '<div class="sc-telemetry"><div class="sc-section" style="font-size:1.2rem;">'
        "⚖️ Moyennes des 5 sous-scores R&D (sur 5.0)</div></div>",
        unsafe_allow_html=True,
    )
    subscore_defs = [
        ("modeling_depth", "Complexité DL & Théorie"),
        ("mentorship_team", "Encadrement Chercheurs/Seniors"),
        ("engineering_practice", "Pratiques MLOps & Ingénierie"),
        ("option_value", "Débouchés Thèse/CDI & Notoriété"),
        ("logistics", "Logistique & Rémunération"),
    ]
    subscore_jobs = [j for j in reranked_jobs if j.get("sub_scores")]
    n_sub = len(subscore_jobs)

    cols_sub = st.columns(5)
    sub_data = []
    for i, (key, label) in enumerate(subscore_defs):
        if n_sub > 0:
            avg_val = sum(j["sub_scores"].get(key, 3) for j in subscore_jobs) / n_sub
        else:
            avg_val = 0.0
        cols_sub[i].metric(label, f"{avg_val:.2f} / 5")
        sub_data.append({"Critère": label, "Moyenne": round(avg_val, 2)})

    st.markdown('<hr class="sc-rule">', unsafe_allow_html=True)

    # C. Baromètre des Technologies & Domaines de pointe
    st.markdown(
        '<div class="sc-telemetry"><div class="sc-section" style="font-size:1.25rem;">'
        "💻 Baromètre des Technologies & Domaines de pointe</div></div>",
        unsafe_allow_html=True,
    )
    st.caption("Volume de présence dans les offres et score R&D moyen associé par le juge.")

    tech_catalog = {
        "Deep Learning & Frameworks": ["PyTorch", "TensorFlow", "JAX", "Hugging Face", "Scikit-learn"],
        "Domaines de pointe": ["Vision 3D", "IA Générative", "Diffusion", "LLM", "Optimisation", "Médical"],
        "Ingénierie & Déploiement": ["Docker", "Kubernetes", "Slurm", "C++", "MLflow", "ONNX", "CUDA"],
    }

    barometer_rows = []
    for cat, tech_list in tech_catalog.items():
        for tech in tech_list:
            pat = tech.lower()
            matching = []
            for j in jobs:
                declared = [str(t).lower() for t in (j.get("tech_stack") or [])]
                txt = f"{j.get('title') or ''} {j.get('description') or ''}".lower()
                found = False
                if pat in declared or pat in txt:
                    found = True
                elif tech == "Vision 3D" and any(k in txt for k in ["3d", "nerf", "splatting"]):
                    found = True
                elif tech == "IA Générative" and any(k in txt for k in ["ia générative", "generative ai", "genai", "gen ai"]):
                    found = True
                elif tech == "Médical" and any(k in txt for k in ["médical", "medical", "santé", "biomedical"]):
                    found = True
                elif tech == "Slurm" and "slurm" in txt:
                    found = True
                elif tech == "CUDA" and "cuda" in txt:
                    found = True
                if found:
                    matching.append(j)

            if matching:
                scores_m = [effective_score(j) for j in matching]
                avg_m = sum(scores_m) / len(scores_m)
                barometer_rows.append({
                    "Technologie": tech,
                    "Catégorie": cat,
                    "Offres": len(matching),
                    "Score R&D moyen": round(avg_m, 1),
                })

    df_baro = pd.DataFrame(barometer_rows).sort_values("Offres", ascending=False)
    if not df_baro.empty:
        chart_baro = (
            alt.Chart(df_baro)
            .mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4)
            .encode(
                y=alt.Y("Technologie:N", sort="-x", title=None),
                x=alt.X("Offres:Q", title="Nombre d'offres"),
                color=alt.Color(
                    "Score R&D moyen:Q",
                    scale=alt.Scale(scheme="blues"),
                    title="Score R&D moyen / 100",
                ),
                tooltip=["Technologie", "Catégorie", "Offres", "Score R&D moyen"],
            )
            .properties(height=380)
        )
        st.altair_chart(chart_baro, use_container_width=True)
    else:
        st.info("Aucune technologie identifiée.")



def _normalize_job_url(raw_url: str | None) -> str:
    """S'assure qu'une URL d'offre dispose d'un schéma HTTPS valide."""
    url = (raw_url or "").strip()
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        return f"https://{url}"
    return url


def _rejections_table(rejected_jobs: Sequence[dict[str, Any]]) -> str:
    """Tableau HTML des offres écartées par les filtres métier."""
    if not rejected_jobs:
        return ""
    rows = []
    for job in rejected_jobs:
        date_str = _fmt_stamp(job.get("last_seen_at"))
        src = str(job.get("source") or "")
        src_lbl = source_label(src)
        src_col = source_color(src)
        raw_url = str(job.get("canonical_url") or "")
        url = _normalize_job_url(raw_url)
        title = str(job.get("title") or "Offre sans titre")

        reason = str(job.get("rejection_reason") or "Motif non précisé")
        reason_lower = reason.lower()
        if "aucun signal" in reason_lower:
            tone = "alert"
        elif "bi" in reason_lower or "reporting" in reason_lower:
            tone = "warn"
        else:
            tone = "alert"

        title_html = (
            f'<a href="{_esc(url)}" target="_blank" rel="noopener noreferrer" '
            f'style="color:var(--text, #1C1B19);text-decoration:underline;text-underline-offset:3px;font-weight:550;">'
            f'{_esc(title)}</a>'
            if url
            else _esc(title)
        )
        action_html = (
            f'<a href="{_esc(url)}" target="_blank" rel="noopener noreferrer" '
            f'style="white-space:nowrap;font-size:12px;font-weight:600;color:var(--accent, #B5482A);text-decoration:none;">'
            f"Ouvrir l'annonce ↗</a>"
            if url
            else '<span style="color:var(--text-3, #6E695F);">—</span>'
        )

        rows.append(
            "<tr>"
            f'<td class="sc-num">{_esc(date_str)}</td>'
            f'<td><span class="sc-badge"><i class="sc-dot" style="background:{src_col}"></i>{_esc(src_lbl)}</span></td>'
            f'<td>{title_html}</td>'
            f'<td><span class="sc-badge {tone_class(tone)}">{_esc(reason)}</span></td>'
            f'<td style="text-align:right;">{action_html}</td>'
            "</tr>"
        )
    return (
        '<table class="sc-table"><thead><tr>'
        '<th style="width:105px;">Détectée</th>'
        '<th style="width:115px;">Plateforme</th>'
        '<th>Titre de l\'annonce</th>'
        '<th>Motif d\'exclusion</th>'
        '<th style="width:130px;text-align:right;">Action</th>'
        '</tr></thead><tbody>' + "".join(rows) + "</tbody></table>"
    )


def render_rejections_explorer(db: Database) -> None:
    """Explorateur des offres écartées par les filtres de collecte (seen_jobs)."""
    st.markdown(
        '<div class="sc-telemetry"><div class="sc-section" style="font-size:1.4rem;">'
        "Explorateur des offres écartées</div></div>"
        '<div class="sc-stream-note" style="margin-bottom:14px;">'
        "Offres écartées par les filtres de collecte (orientation BI, hors sujet, etc.) "
        "et conservées dans la mémoire de collecte <code>seen_jobs</code>."
        "</div>",
        unsafe_allow_html=True,
    )

    # Récupération dynamique des motifs de rejet
    breakdown = db.get_seen_decision_breakdown(days=180, limit=50)
    reasons = sorted({
        row["rejection_reason"]
        for row in breakdown
        if str(row.get("decision") or "").startswith("REJECTED") and row.get("rejection_reason")
    })
    reason_options = ["Tous les motifs"] + reasons

    # Filtres interactifs sur une ligne
    col_search, col_reason, col_sources, col_limit = st.columns([2.6, 2.4, 2.0, 1.2], gap="small")
    with col_search:
        search_query = st.text_input(
            "Rechercher dans les rejets",
            placeholder="Titre, motif, entreprise…",
            key="rej_query",
        )
    with col_reason:
        selected_reason = st.selectbox(
            "Motif de rejet",
            options=reason_options,
            key="rej_reason",
        )
    with col_sources:
        selected_sources = st.multiselect(
            "Plateformes",
            options=["linkedin", "jobteaser"],
            default=["linkedin", "jobteaser"],
            format_func=lambda s: source_label(s),
            key="rej_sources",
        )
    with col_limit:
        selected_limit = st.selectbox(
            "Offres affichées",
            options=[25, 50, 100, 200],
            index=1,
            key="rej_limit",
        )

    # Interrogation de la base
    limit_val = int(selected_limit)
    query_val = search_query.strip() or None
    reason_val = None if selected_reason == "Tous les motifs" else selected_reason

    if not selected_sources:
        rejected_jobs = []
    elif len(selected_sources) == 1:
        rejected_jobs = db.get_rejected_seen_jobs(
            limit=limit_val,
            source=selected_sources[0],
            reason=reason_val,
            query=query_val,
        )
    else:
        # Les deux sources sélectionnées (ou plus)
        raw = db.get_rejected_seen_jobs(
            limit=limit_val,
            source=None,
            reason=reason_val,
            query=query_val,
        )
        rejected_jobs = [j for j in raw if j.get("source") in selected_sources]

    if not rejected_jobs:
        st.markdown(
            '<div class="sc-stream"><span class="sc-stream-count">0 offre trouvée</span></div>'
            '<div class="sc-empty">Aucune offre écartée ne correspond aux critères sélectionnés.<br>'
            "Modifiez votre recherche ou élargissez les filtres.</div>",
            unsafe_allow_html=True,
        )
        return

    st.markdown(
        f'<div class="sc-stream"><span class="sc-stream-count">{len(rejected_jobs)} offre(s) écartée(s)</span>'
        '<span class="sc-stream-note">mémoire de collecte seen_jobs</span></div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="sc-telemetry">' + _rejections_table(rejected_jobs) + "</div>",
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------- #
# Rendu principal : 5 onglets Streamlit
# --------------------------------------------------------------------------- #
jobs = load_jobs(db, 0)

tab_personal, tab_geo, tab_rd, tab_telemetry, tab_rejections = st.tabs(
    [
        "🎯 Mes Candidatures",
        "🗺️ Cartographie & Entreprises",
        "🧠 Qualité R&D & Technologies",
        "📡 Télémétrie des collectes",
        "🚫 Explorateur des rejets",
    ]
)

with tab_personal:
    _render_personal_analytics(jobs)

with tab_geo:
    _render_geo_and_companies(jobs, db)

with tab_rd:
    _render_rd_and_tech(jobs)

with tab_telemetry:
    render_telemetry(db)

with tab_rejections:
    render_rejections_explorer(db)

