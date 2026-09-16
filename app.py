"""Dashboard Streamlit — Stage Copilot.

Console d'ingénierie pour la veille de stages R&D / Data Science :

* un bandeau KPI compact (volume, offres qualifiées, couverture LLM,
  répartition par plateforme) ;
* un flux de cartes d'offres scorées (score R&D, verdict du juge LLM,
  technologies détectées, actions de candidature) ;
* des filtres latéraux denses (recherche plein texte, plateformes, score
  minimal, critères avancés) et la maintenance de la base SQLite.

Aucun emoji décoratif : la hiérarchie visuelle repose sur la typographie, les
badges de métadonnées et des indicateurs d'état discrets (score, verdict,
statut de candidature). Le thème natif Streamlit (clair ou sombre) est respecté
grâce à une palette de jetons CSS générée dynamiquement.
"""
from __future__ import annotations

import html
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from string import Template
from typing import Any, Iterable, Sequence

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_config  # noqa: E402
from src.constants import (  # noqa: E402
    SOURCE_COLORS,
    SOURCE_FALLBACK_COLOR,
    STATUS_APPLIED,
    STATUS_IGNORED,
    STATUS_INTERVIEW,
    STATUS_NEW,
    STATUS_ORDER,
    TIER_1,
    TIER_ESN,
    TIER_LABELS,
    TIER_NEUTRAL,
    VERDICT_EXCELLENT,
    VERDICT_GOOD,
    VERDICT_LABELS,
    VERDICT_MIXED,
    VERDICT_OFF_TOPIC,
    source_label,
    source_rank,
)
from src.storage.database import Database  # noqa: E402

st.set_page_config(
    page_title="Stage Copilot",
    page_icon=":material/insights:",
    layout="wide",
)

# --------------------------------------------------------------------------- #
# Vocabulaire d'affichage (micro-copie d'ingénierie, sans emoji décoratif)
# --------------------------------------------------------------------------- #
DISPLAY_FLAT = "Flux unique (tri par score)"
DISPLAY_GROUPED = "Groupé par plateforme"

PAGE_SIZE_ALL = "Tout"
PAGE_SIZES = ("25", "50", "100", PAGE_SIZE_ALL)

# Durée de vie (s) du cache de lecture SQLite : une écriture externe au dashboard
# (rerank lancé en ligne de commande, collecte manuelle) devient visible sans
# redémarrer le serveur Streamlit.
DATA_CACHE_TTL = 60

QUALIFIED_SCORE = 60.0        # seuil « offre R&D qualifiée »
MAX_TECH_CHIPS = 7            # technologies affichées par carte
EXCERPT_LENGTH = 420          # caractères de fiche affichés avant dépliage

STATUS_LABELS = {
    STATUS_NEW: "Nouveau",
    STATUS_APPLIED: "Postulé",
    STATUS_INTERVIEW: "Entretien",
    STATUS_IGNORED: "Archivé",
}

# Statuts considérés comme « traités » (masquables via la vue focus).
PROCESSED_STATUSES = (STATUS_APPLIED, STATUS_INTERVIEW, STATUS_IGNORED)

STATUS_TONES = {
    STATUS_NEW: "mute",
    STATUS_APPLIED: "positive",
    STATUS_INTERVIEW: "accent",
    STATUS_IGNORED: "warn",
}

VERDICT_TONES = {
    VERDICT_EXCELLENT: "positive",
    VERDICT_GOOD: "accent",
    VERDICT_MIXED: "warn",
    VERDICT_OFF_TOPIC: "alert",
}

# Seuils d'alignement (score effectif) → libellé + tonalité.
ALIGNMENT_LEVELS: tuple[tuple[float, str, str], ...] = (
    (80.0, "Cœur de cible", "positive"),
    (60.0, "Pertinent", "accent"),
    (40.0, "Secondaire", "warn"),
)
ALIGNMENT_FALLBACK: tuple[str, str] = ("Hors périmètre", "mute")

# --------------------------------------------------------------------------- #
# Palette : jetons CSS dérivés du thème natif (dark / light), repli « auto »
# --------------------------------------------------------------------------- #
# Tonalités fonctionnelles : (texte, fond, bordure) par thème. Le thème « auto »
# n'est utilisé que si Streamlit n'expose pas son type de thème (mode bare).
_TONE_PALETTE: dict[str, dict[str, tuple[str, str, str]]] = {
    "positive": {
        "dark": ("#34d399", "rgba(16, 185, 129, 0.14)", "rgba(16, 185, 129, 0.34)"),
        "light": ("#047857", "rgba(5, 150, 105, 0.10)", "rgba(5, 150, 105, 0.30)"),
        "auto": ("#10b981", "rgba(16, 185, 129, 0.14)", "rgba(16, 185, 129, 0.34)"),
    },
    "accent": {
        "dark": ("#a5b4fc", "rgba(99, 102, 241, 0.16)", "rgba(99, 102, 241, 0.36)"),
        "light": ("#4338ca", "rgba(99, 102, 241, 0.12)", "rgba(99, 102, 241, 0.32)"),
        "auto": ("#818cf8", "rgba(99, 102, 241, 0.16)", "rgba(99, 102, 241, 0.36)"),
    },
    "warn": {
        "dark": ("#fcd34d", "rgba(245, 158, 11, 0.14)", "rgba(245, 158, 11, 0.34)"),
        "light": ("#b45309", "rgba(245, 158, 11, 0.12)", "rgba(245, 158, 11, 0.32)"),
        "auto": ("#fbbf24", "rgba(245, 158, 11, 0.14)", "rgba(245, 158, 11, 0.34)"),
    },
    "alert": {
        "dark": ("#fda4af", "rgba(244, 63, 94, 0.14)", "rgba(244, 63, 94, 0.34)"),
        "light": ("#be123c", "rgba(244, 63, 94, 0.10)", "rgba(244, 63, 94, 0.28)"),
        "auto": ("#fb7185", "rgba(244, 63, 94, 0.14)", "rgba(244, 63, 94, 0.34)"),
    },
    "mute": {
        "dark": ("rgba(226, 232, 240, 0.78)", "rgba(148, 163, 184, 0.12)", "rgba(148, 163, 184, 0.26)"),
        "light": ("rgba(30, 41, 59, 0.80)", "rgba(100, 116, 139, 0.10)", "rgba(100, 116, 139, 0.24)"),
        "auto": ("rgba(148, 163, 184, 0.9)", "rgba(148, 163, 184, 0.12)", "rgba(148, 163, 184, 0.26)"),
    },
}

_BASE_PALETTE: dict[str, dict[str, str]] = {
    "dark": {
        "border": "rgba(255, 255, 255, 0.08)",
        "border_hover": "rgba(255, 255, 255, 0.20)",
        "surface": "rgba(255, 255, 255, 0.020)",
        "surface_strong": "rgba(255, 255, 255, 0.055)",
        "muted": "rgba(226, 232, 240, 0.66)",
        "faint": "rgba(226, 232, 240, 0.42)",
    },
    "light": {
        "border": "rgba(15, 23, 42, 0.10)",
        "border_hover": "rgba(15, 23, 42, 0.24)",
        "surface": "rgba(15, 23, 42, 0.016)",
        "surface_strong": "rgba(15, 23, 42, 0.045)",
        "muted": "rgba(30, 41, 59, 0.70)",
        "faint": "rgba(30, 41, 59, 0.46)",
    },
    "auto": {
        "border": "color-mix(in srgb, currentColor 14%, transparent)",
        "border_hover": "color-mix(in srgb, currentColor 28%, transparent)",
        "surface": "color-mix(in srgb, currentColor 3%, transparent)",
        "surface_strong": "color-mix(in srgb, currentColor 7%, transparent)",
        "muted": "color-mix(in srgb, currentColor 66%, transparent)",
        "faint": "color-mix(in srgb, currentColor 44%, transparent)",
    },
}

# --------------------------------------------------------------------------- #
# Feuille de style injectée (Template : les accolades CSS restent littérales)
# --------------------------------------------------------------------------- #
_CSS_TOKENS = Template(
    """:root {
  --sc-border: $border;
  --sc-border-strong: $border_hover;
  --sc-surface: $surface;
  --sc-surface-strong: $surface_strong;
  --sc-muted: $muted;
  --sc-faint: $faint;
  --sc-positive-fg: $positive_fg;
  --sc-positive-bg: $positive_bg;
  --sc-positive-bd: $positive_bd;
  --sc-accent-fg: $accent_fg;
  --sc-accent-bg: $accent_bg;
  --sc-accent-bd: $accent_bd;
  --sc-warn-fg: $warn_fg;
  --sc-warn-bg: $warn_bg;
  --sc-warn-bd: $warn_bd;
  --sc-alert-fg: $alert_fg;
  --sc-alert-bg: $alert_bg;
  --sc-alert-bd: $alert_bd;
  --sc-mute-fg: $mute_fg;
  --sc-mute-bg: $mute_bg;
  --sc-mute-bd: $mute_bd;
  --sc-mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  --sc-radius: 10px;
}
.sc-tone-positive { --sc-tone-fg: var(--sc-positive-fg); --sc-tone-bg: var(--sc-positive-bg); --sc-tone-bd: var(--sc-positive-bd); }
.sc-tone-accent { --sc-tone-fg: var(--sc-accent-fg); --sc-tone-bg: var(--sc-accent-bg); --sc-tone-bd: var(--sc-accent-bd); }
.sc-tone-warn { --sc-tone-fg: var(--sc-warn-fg); --sc-tone-bg: var(--sc-warn-bg); --sc-tone-bd: var(--sc-warn-bd); }
.sc-tone-alert { --sc-tone-fg: var(--sc-alert-fg); --sc-tone-bg: var(--sc-alert-bg); --sc-tone-bd: var(--sc-alert-bd); }
.sc-tone-mute { --sc-tone-fg: var(--sc-mute-fg); --sc-tone-bg: var(--sc-mute-bg); --sc-tone-bd: var(--sc-mute-bd); }
"""
)

_CSS_CHROME = Template(
    """
/* ---------- Chrome applicatif ---------- */
[data-testid="stMainBlockContainer"] {
  padding-top: 2.6rem;
  padding-bottom: 5rem;
  max-width: 1160px;
}
[data-testid="stSidebarUserContent"] { padding-top: .4rem; }
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: .1em;
  text-transform: uppercase;
  color: var(--sc-faint);
}
[data-testid="stSidebar"] hr { margin: 1.1rem 0 .9rem; border-color: var(--sc-border); }
[data-testid="stSidebar"] [data-testid="stExpander"] {
  border: 1px solid var(--sc-border);
  border-radius: 8px;
  background: var(--sc-surface);
}
[data-testid="stSidebar"] [data-testid="stExpander"] summary {
  font-size: 11.5px;
  font-weight: 600;
  letter-spacing: .06em;
  text-transform: uppercase;
  color: var(--sc-muted);
}

/* ---------- Boutons et liens (ghost) ---------- */
[data-testid="stButton"] button,
[data-testid="stLinkButton"] a {
  border: 1px solid var(--sc-border);
  border-radius: 7px;
  background: transparent;
  color: inherit;
  box-shadow: none;
  font-size: 12.5px;
  font-weight: 550;
  padding: .25rem .7rem;
  min-height: 0;
  text-decoration: none;
  transition: border-color .14s ease, background .14s ease;
}
[data-testid="stButton"] button p,
[data-testid="stLinkButton"] a p { font-size: 12.5px; font-weight: 550; }
[data-testid="stButton"] button:hover,
[data-testid="stLinkButton"] a:hover {
  border-color: var(--sc-border-strong);
  background: var(--sc-surface-strong);
  color: inherit;
}
[data-testid="stCode"] pre { font-size: 11.5px; line-height: 1.45; }
"""
)

_CSS_HEADER_KPI = Template(
    """
/* ---------- En-tête ---------- */
.sc-eyebrow {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: .16em;
  text-transform: uppercase;
  color: var(--sc-faint);
}
.sc-title { margin: 7px 0 0; font-size: 24px; font-weight: 650; letter-spacing: -.018em; line-height: 1.15; }
.sc-subtitle { margin-top: 7px; font-size: 12.5px; line-height: 1.6; color: var(--sc-muted); }
.sc-subtitle code, .sc-empty code {
  font-family: var(--sc-mono);
  font-size: 11.5px;
  padding: 1px 5px;
  border: 1px solid var(--sc-border);
  border-radius: 4px;
  background: var(--sc-surface-strong);
  color: var(--sc-muted);
}
.sc-rule { height: 1px; margin: 20px 0 16px; background: var(--sc-border); border: 0; }

/* ---------- Bandeau KPI ---------- */
.sc-kpis {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  border: 1px solid var(--sc-border);
  border-radius: var(--sc-radius);
  overflow: hidden;
}
.sc-kpi { padding: 12px 14px; min-width: 0; border-right: 1px solid var(--sc-border); }
.sc-kpi:last-child { border-right: 0; }
.sc-kpi-label {
  font-size: 10.5px;
  font-weight: 600;
  letter-spacing: .11em;
  text-transform: uppercase;
  color: var(--sc-faint);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sc-kpi-value {
  margin-top: 7px;
  font-size: 21px;
  font-weight: 650;
  line-height: 1;
  letter-spacing: -.02em;
  font-variant-numeric: tabular-nums;
}
.sc-kpi-value span { margin-left: 5px; font-size: 11.5px; font-weight: 500; color: var(--sc-faint); letter-spacing: 0; }
.sc-kpi-hint { margin-top: 6px; font-size: 11px; color: var(--sc-faint); }
.sc-dist { display: flex; height: 5px; margin-top: 9px; border-radius: 999px; overflow: hidden; background: var(--sc-surface-strong); }
.sc-dist i { display: block; height: 100%; }
.sc-legend { display: flex; flex-wrap: wrap; gap: 3px 12px; margin-top: 7px; font-size: 11px; color: var(--sc-muted); }
.sc-legend i { display: inline-block; width: 6px; height: 6px; margin-right: 5px; border-radius: 999px; vertical-align: middle; }
.sc-legend b { font-weight: 600; font-variant-numeric: tabular-nums; }

/* ---------- En-tête de flux, groupes, état vide ---------- */
.sc-stream { display: flex; align-items: baseline; justify-content: space-between; gap: 14px; margin: 2px 0 10px; }
.sc-stream-count { font-size: 13px; font-weight: 600; font-variant-numeric: tabular-nums; }
.sc-stream-note { font-size: 11.5px; color: var(--sc-faint); }
.sc-group { display: flex; align-items: baseline; gap: 9px; margin: 22px 0 8px; padding-bottom: 6px; border-bottom: 1px solid var(--sc-border); }
.sc-group-name { font-size: 11.5px; font-weight: 650; letter-spacing: .11em; text-transform: uppercase; }
.sc-group-count { font-size: 11.5px; color: var(--sc-faint); font-variant-numeric: tabular-nums; }
.sc-empty {
  padding: 24px 18px;
  border: 1px dashed var(--sc-border);
  border-radius: var(--sc-radius);
  text-align: center;
  font-size: 13px;
  line-height: 1.7;
  color: var(--sc-muted);
}
"""
)

_CSS_CARD = Template(
    """
/* ---------- Carte d'offre ---------- */
.sc-card {
  padding: 13px 15px 11px;
  border: 1px solid var(--sc-border);
  border-radius: var(--sc-radius);
  background: var(--sc-surface);
  transition: border-color .15s ease, background .15s ease;
}
.sc-card:hover { border-color: var(--sc-border-strong); background: var(--sc-surface-strong); }
/* Resserre l'écart entre la carte et sa barre d'actions lorsque le DOM le permet. */
div[data-testid="stVerticalBlock"]:has(> div[data-testid="stElementContainer"] .sc-card) { gap: .5rem; }
.sc-card-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; }
.sc-card-title { margin: 0; font-size: 15.5px; font-weight: 630; line-height: 1.32; letter-spacing: -.012em; }
.sc-card-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 5px; font-size: 12.5px; color: var(--sc-muted); }
.sc-card-meta .sc-sep { color: var(--sc-faint); }
.sc-company { font-weight: 600; }
.sc-score-box { display: flex; flex: 0 0 auto; flex-direction: column; align-items: flex-end; gap: 5px; }
.sc-score {
  display: inline-flex;
  align-items: baseline;
  gap: 3px;
  padding: 3px 9px;
  border: 1px solid var(--sc-tone-bd, var(--sc-border));
  border-radius: 7px;
  background: var(--sc-tone-bg, transparent);
  color: var(--sc-tone-fg, inherit);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.sc-score b { font-size: 15px; font-weight: 650; letter-spacing: -.01em; }
.sc-score span { font-size: 11px; opacity: .7; }
.sc-align {
  font-size: 10.5px;
  font-weight: 600;
  letter-spacing: .09em;
  text-transform: uppercase;
  color: var(--sc-tone-fg, var(--sc-faint));
}
.sc-badges { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 10px; }
.sc-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 8px;
  border: 1px solid var(--sc-tone-bd, var(--sc-border));
  border-radius: 6px;
  background: var(--sc-tone-bg, var(--sc-surface-strong));
  color: var(--sc-tone-fg, var(--sc-muted));
  font-size: 11.5px;
  font-weight: 550;
  white-space: nowrap;
}
.sc-dot { display: inline-block; flex: 0 0 auto; width: 6px; height: 6px; border-radius: 999px; }
.sc-status {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-weight: 550;
  color: var(--sc-tone-fg, var(--sc-muted));
}
.sc-status .sc-dot { background: var(--sc-tone-fg, currentColor); }
.sc-chips { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 9px; }
.sc-chip {
  padding: 1px 7px;
  border: 1px solid var(--sc-border);
  border-radius: 999px;
  font-family: var(--sc-mono);
  font-size: 11px;
  color: var(--sc-muted);
  white-space: nowrap;
}
.sc-card-actions { margin-top: 12px; }

/* ---------- Accordéon « Détails & évaluation » ---------- */
.sc-details { margin-top: 12px; border-top: 1px solid var(--sc-border); }
.sc-details > summary {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 8px 0 0;
  list-style: none;
  cursor: pointer;
  font-size: 11.5px;
  font-weight: 600;
  letter-spacing: .07em;
  text-transform: uppercase;
  color: var(--sc-faint);
}
.sc-details > summary::-webkit-details-marker { display: none; }
.sc-details > summary:hover { color: var(--sc-muted); }
.sc-chev { display: inline-block; font-size: 13px; line-height: 1; transition: transform .15s ease; }
.sc-details[open] > summary .sc-chev { transform: rotate(90deg); }
.sc-details-body { padding-top: 10px; }
.sc-section {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: .1em;
  text-transform: uppercase;
  color: var(--sc-faint);
  margin-bottom: 6px;
}
.sc-section--sub { margin-top: 12px; }
.sc-block + .sc-block { margin-top: 14px; }
.sc-list { display: flex; flex-direction: column; gap: 4px; margin: 0; padding: 0; list-style: none; }
.sc-list li { position: relative; padding-left: 14px; font-size: 12.5px; line-height: 1.5; color: var(--sc-muted); }
.sc-list li::before {
  content: "";
  position: absolute;
  left: 3px;
  top: 7px;
  width: 4px;
  height: 4px;
  border-radius: 999px;
  background: var(--sc-tone-fg, currentColor);
  opacity: .8;
}
.sc-kv { display: flex; flex-wrap: wrap; gap: 3px 18px; }
.sc-kv div { font-size: 11.5px; color: var(--sc-faint); }
.sc-kv b {
  font-family: var(--sc-mono);
  font-size: 12px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--sc-muted);
}
.sc-excerpt { margin: 0; font-size: 12.5px; line-height: 1.6; color: var(--sc-muted); white-space: pre-line; }
.sc-more { margin-top: 8px; }
.sc-more > summary { cursor: pointer; font-size: 11.5px; color: var(--sc-faint); }
.sc-more > summary:hover { color: var(--sc-muted); }
.sc-more[open] > summary { margin-bottom: 6px; }
.sc-cta {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 10px;
  border: 1px solid var(--sc-tone-bd, var(--sc-border));
  border-radius: 7px;
  background: var(--sc-tone-bg, transparent);
  color: var(--sc-tone-fg, inherit);
  font-size: 12.5px;
  font-weight: 550;
  text-decoration: none;
}
.sc-cta:hover { border-color: var(--sc-border-strong); text-decoration: none; }

/* ---------- Adaptations ---------- */
@media (max-width: 1100px) {
  .sc-kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .sc-kpi:nth-child(2) { border-right: 0; }
  .sc-kpi:nth-child(-n+2) { border-bottom: 1px solid var(--sc-border); }
}
@media (prefers-reduced-motion: reduce) {
  .sc-card, .sc-chev, [data-testid="stButton"] button, [data-testid="stLinkButton"] a { transition: none; }
}
"""
)

_CSS_TEMPLATE = Template(
    "<style>"
    + _CSS_TOKENS.template
    + _CSS_CHROME.template
    + _CSS_HEADER_KPI.template
    + _CSS_CARD.template
    + "</style>"
)


# --------------------------------------------------------------------------- #
# Style injecté & accès aux données
# --------------------------------------------------------------------------- #
def _theme_type() -> str:
    """Type du thème natif Streamlit (« dark » / « light »), repli « auto ».

    ``st.context.theme`` n'est pas disponible hors exécution de script
    (mode bare, tests unitaires) : la palette « auto » prend alors le relais
    avec des couleurs dérivées de ``currentColor``.
    """
    try:
        value = getattr(st.context.theme, "type", None)
    except Exception:  # noqa: BLE001 - contexte absent : on retombe sur « auto »
        return "auto"
    return value if value in {"dark", "light"} else "auto"


def _token_context(theme: str) -> dict[str, str]:
    """Assemble les jetons CSS à substituer dans la feuille de style."""
    tokens = dict(_BASE_PALETTE[theme])
    for tone, variants in _TONE_PALETTE.items():
        fg, bg, bd = variants[theme]
        tokens[f"{tone}_fg"], tokens[f"{tone}_bg"], tokens[f"{tone}_bd"] = fg, bg, bd
    return tokens


def inject_styles() -> None:
    """Injecte la feuille de style, calibrée sur le thème natif courant."""
    st.markdown(_CSS_TEMPLATE.substitute(_token_context(_theme_type())), unsafe_allow_html=True)


@st.cache_resource
def get_database() -> Database:
    """Connexion SQLite partagée (créée une seule fois par session serveur)."""
    config = load_config()
    return Database(config["database"]["path"])


@st.cache_data(ttl=DATA_CACHE_TTL, show_spinner=False)
def load_jobs(_db: Database, data_version: int) -> list[dict[str, Any]]:
    """Charge toutes les offres, triées par score effectif décroissant.

    ``data_version`` (session_state) invalide le cache dès qu'un statut change
    ou que le pipeline est relancé : aucune requête n'est refaite sans raison.
    Le ``ttl`` couvre le cas d'une écriture externe (rerank lancé en ligne de
    commande) : la vue se rafraîchit d'elle-même au plus tard après ``DATA_CACHE_TTL``.
    """
    return _db.get_jobs()


def bump_data_version() -> None:
    """Invalide les données mises en cache (statut modifié, base rafraîchie)."""
    st.session_state["data_version"] = st.session_state.get("data_version", 0) + 1


def _set_status(db: Database, job_id: str, status: str) -> None:
    """Callback de bouton : persiste un statut de candidature puis invalide le cache."""
    db.update_status(job_id, status)
    bump_data_version()


# --------------------------------------------------------------------------- #
# Helpers métier (purs, donc directement testables)
# --------------------------------------------------------------------------- #
def effective_score(job: dict[str, Any]) -> float:
    """Score R&D effectif : rerank du juge LLM s'il existe, sinon score bi-encoder."""
    rerank = job.get("rerank_score")
    if rerank is not None:
        return float(rerank)
    return float(job.get("final_score") or 0.0)


def is_reranked(job: dict[str, Any]) -> bool:
    """Indique si l'offre a été évaluée par le juge LLM (étape 2)."""
    return job.get("rerank_score") is not None


def score_alignment(score: float) -> tuple[str, str]:
    """Traduit un score 0-100 en libellé d'alignement + tonalité fonctionnelle."""
    for threshold, label, tone in ALIGNMENT_LEVELS:
        if score >= threshold:
            return label, tone
    return ALIGNMENT_FALLBACK


def tone_class(tone: str) -> str:
    """Classe CSS portant les jetons de couleur d'une tonalité."""
    return f"sc-tone-{tone}"


def _esc(value: Any) -> str:
    """Échappe une valeur destinée à être injectée dans du HTML."""
    return "" if value is None else html.escape(str(value), quote=True)


def parse_timestamp(value: Any) -> datetime | None:
    """Convertit ``created_at`` (datetime ou chaîne ISO) en datetime naïf UTC."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def relative_date(value: Any, now: datetime | None = None) -> str:
    """Date relative compacte : « Aujourd'hui », « Il y a 5 h », « Il y a 3 j »…"""
    stamp = parse_timestamp(value)
    if stamp is None:
        return "Date inconnue"
    reference = now or datetime.now(timezone.utc).replace(tzinfo=None)
    delta = reference - stamp
    seconds = delta.total_seconds()
    if seconds < 0:
        return "À l'instant"
    if seconds < 3600:
        return "À l'instant" if seconds < 120 else f"Il y a {int(seconds // 60)} min"
    if seconds < 86400:
        return f"Il y a {int(seconds // 3600)} h"
    days = int(seconds // 86400)
    if days == 1:
        return "Hier"
    if days < 31:
        return f"Il y a {days} j"
    months = days // 30
    return f"Il y a {months} mois" if months < 12 else f"Il y a {months // 12} an(s)"


def detected_technologies(job: dict[str, Any], keywords: Sequence[str]) -> list[str]:
    """Technologies clés de l'offre.

    Le juge LLM remplit ``tech_stack`` ; en l'absence de verdict, on retombe sur
    la détection déterministe des mots-clés d'excellence de ``config.yaml``
    (aucune technologie n'est inventée).
    """
    declared = [str(item) for item in (job.get("tech_stack") or []) if str(item).strip()]
    if declared:
        return declared[:MAX_TECH_CHIPS]
    haystack = f"{job.get('title') or ''} {job.get('description') or ''}".lower()
    found = [keyword for keyword in keywords if keyword.lower() in haystack]
    return found[:MAX_TECH_CHIPS]


def contract_label(job: dict[str, Any]) -> str | None:
    """Type de contrat déduit de la fiche (``None`` si non identifiable)."""
    haystack = f"{job.get('title') or ''} {job.get('description') or ''}".lower()
    if "stage" in haystack or "internship" in haystack:
        return "Stage"
    if "alternance" in haystack or "apprentissage" in haystack:
        return "Alternance"
    return None


def search_terms(query: str) -> list[str]:
    """Découpe une recherche plein texte en termes significatifs (ET logique)."""
    return [term for term in query.lower().replace(",", " ").split() if term]


def _search_haystack(job: dict[str, Any]) -> str:
    """Concatène les champs indexés par la recherche plein texte."""
    parts: list[str] = [
        str(job.get("title") or ""),
        str(job.get("company") or ""),
        str(job.get("location") or ""),
        source_label(job.get("source")),
    ]
    parts.extend(str(item) for item in (job.get("tech_stack") or []))
    parts.append(str(job.get("description") or ""))
    return " ".join(parts).lower()


def matches_query(job: dict[str, Any], terms: Iterable[str]) -> bool:
    """Vrai si tous les termes recherchés apparaissent dans l'offre."""
    haystack = _search_haystack(job)
    return all(term in haystack for term in terms)


@dataclass(frozen=True)
class Filters:
    """Critères de sélection courants du flux d'offres."""

    query: str = ""
    sources: tuple[str, ...] = ()
    statuses: tuple[str, ...] = ()
    tiers: tuple[int, ...] = (TIER_1, TIER_NEUTRAL, TIER_ESN)
    min_score: float = 0.0
    llm_only: bool = False
    hide_processed: bool = False
    exclude_esn: bool = False
    group_by_source: bool = False
    limit: int | None = None

    def is_default(self) -> bool:
        """Vrai si aucun critère restrictif n'est appliqué.

        Un tuple vide (``statuses``, ``tiers``, ``sources``) signifie « tous les
        conserver » et ne constitue donc pas un filtre.
        """
        return not (
            self.query
            or self.sources
            or self.min_score
            or self.llm_only
            or self.exclude_esn
            or (self.statuses and len(self.statuses) != len(STATUS_ORDER))
            or (self.tiers and len(self.tiers) != len(TIER_LABELS))
            or self.limit is not None
        )


def filter_jobs(jobs: list[dict[str, Any]], filters: Filters) -> list[dict[str, Any]]:
    """Applique les filtres courants au flux d'offres (ordre conservé)."""
    terms = search_terms(filters.query)
    statuses = {STATUS_NEW} if filters.hide_processed else set(filters.statuses)
    tiers = set(filters.tiers)
    sources = set(filters.sources)
    selected: list[dict[str, Any]] = []
    for job in jobs:
        if filters.min_score and effective_score(job) < filters.min_score:
            continue
        if statuses and job.get("status") not in statuses:
            continue
        if sources and job.get("source") not in sources:
            continue
        if tiers and job.get("company_tier") not in tiers:
            continue
        if filters.exclude_esn and job.get("company_tier") == TIER_ESN:
            continue
        if filters.llm_only and not is_reranked(job):
            continue
        if terms and not matches_query(job, terms):
            continue
        selected.append(job)
    return selected[: filters.limit] if filters.limit else selected


def source_distribution(jobs: list[dict[str, Any]]) -> list[tuple[str, int, str]]:
    """Répartition par plateforme : ``(libellé, nombre, couleur)``.

    Les alias ``wttj`` / ``welcome_to_the_jungle`` sont fusionnés et les
    plateformes les plus représentées passent en premier.
    """
    counts: dict[str, tuple[int, str]] = {}
    for job in jobs:
        label = source_label(job.get("source"))
        count, color = counts.get(label, (0, source_color(job.get("source"))))
        counts[label] = (count + 1, color)
    ordered = sorted(counts.items(), key=lambda item: (-item[1][0], source_rank(item[0])))
    return [(label, stats[0], stats[1]) for label, stats in ordered]


def group_jobs_by_source(jobs: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    """Regroupe les offres par plateforme en conservant le tri par score.

    Les alias ``wttj`` / ``welcome_to_the_jungle`` sont fusionnés sous le même
    libellé et les groupes suivent ``SOURCE_ORDER`` (inconnues en dernier).
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for job in jobs:
        groups.setdefault(source_label(job.get("source")), []).append(job)
    return sorted(groups.items(), key=lambda item: source_rank(item[0]))


# --------------------------------------------------------------------------- #
# Fragments HTML (typographie et badges, aucun emoji décoratif)
# --------------------------------------------------------------------------- #
def source_color(source: str | None) -> str:
    """Couleur identitaire de la plateforme d'origine (repli gris ardoise)."""
    return SOURCE_COLORS.get(source or "", SOURCE_FALLBACK_COLOR)


def _badge(label: str, tone: str | None = None, dot: str | None = None) -> str:
    """Pill badge de métadonnée, avec pastille colorée optionnelle."""
    classes = f"sc-badge {tone_class(tone)}" if tone else "sc-badge"
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
    parts = [f'<span class="sc-company">{_esc(job.get("company"))}</span>']
    if job.get("location"):
        parts.append(_esc(job["location"]))
    parts.append(_esc(relative_date(job.get("created_at"))))
    parts.append(
        f'<span class="sc-status {tone_class(STATUS_TONES.get(status, "mute"))}">'
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
        badges.append(_badge(VERDICT_LABELS.get(verdict, verdict or "Évalué"), VERDICT_TONES.get(verdict, "mute")))
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


def _scores_block(job: dict[str, Any]) -> str:
    """Détail des scores internes (score R&D, alignement vectoriel, typologie)."""
    origin = "rerank LLM" if is_reranked(job) else "hybride"
    entries = (
        ("Score R&D", f"{effective_score(job):.0f}/100 ({origin})"),
        ("Alignement vectoriel", f"{float(job.get('semantic_score') or 0.0):.0f}/100"),
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
        f"{_badges_html(job)}"
        f"{_chips_html(technologies)}"
        '<details class="sc-details">'
        '<summary><span class="sc-chev">›</span>Détails &amp; évaluation</summary>'
        f'<div class="sc-details-body">{_verdict_block(job)}{_scores_block(job)}{_description_block(job)}</div>'
        "</details>"
    )
    cta = (
        f'<div class="sc-card-actions"><a class="sc-cta sc-tone-accent" href="{_esc(url)}"'
        ' target="_blank" rel="noopener noreferrer">Consulter l\'offre ↗</a></div>'
        if url.startswith("http")
        else ""
    )
    return head + cta + "</div>"


def render_job_card(db: Database, job: dict[str, Any], keywords: Sequence[str]) -> None:
    """Rend une carte d'offre suivie de sa barre d'actions de candidature."""
    st.markdown(job_card_html(job, keywords), unsafe_allow_html=True)
    status = job.get("status")
    job_id = str(job.get("id"))
    if status == STATUS_APPLIED:
        actions: tuple[tuple[str, str], ...] = (
            ("Entretien obtenu", STATUS_INTERVIEW),
            ("Archiver", STATUS_IGNORED),
        )
    elif status in (STATUS_INTERVIEW, STATUS_IGNORED):
        actions = (("Rétablir au flux", STATUS_NEW),)
    else:
        actions = (("Marquer postulé", STATUS_APPLIED), ("Archiver", STATUS_IGNORED))
    columns = st.columns([1.2, 1.2, 4], gap="small")
    for column, (label, new_status) in zip(columns, actions):
        with column:
            st.button(
                label,
                key=f"status-{new_status}-{job_id}",
                on_click=_set_status,
                args=(db, job_id, new_status),
                width="stretch",
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
    scoring = config.get("scoring", {})
    weights = scoring.get("weights", {})
    model = scoring.get("model_name", "bi-encoder")
    llm_model = config.get("llm", {}).get("model", "juge LLM")
    stamps = [stamp for stamp in (parse_timestamp(job.get("created_at")) for job in jobs) if stamp]
    chain = (
        f"retrieval <code>{_esc(model)}</code> "
        f"({float(weights.get('semantic', 0)):.2f} alignement · "
        f"{float(weights.get('company', 0)):.2f} typologie · "
        f"{float(weights.get('keywords', 0)):.2f} mots-clés) → reranking <code>{_esc(llm_model)}</code>"
    )
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
    active = [job for job in jobs if job.get("status") != STATUS_IGNORED]
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
                options=STATUS_ORDER,
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

        render_base_panel(jobs)

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


def render_base_panel(jobs: list[dict[str, Any]]) -> None:
    """Panneau latéral : état de la base et maintenance du pipeline."""
    distribution = source_distribution(jobs)
    with st.expander("Base & pipeline"):
        st.markdown(
            '<div class="sc-kv">'
            + "".join(f"<div>{_esc(label)} <b>{count}</b></div>" for label, count, _ in distribution)
            + "</div>",
            unsafe_allow_html=True,
        )
        st.caption(f"{len(jobs)} offres en base SQLite")
        st.button(
            "Actualiser la vue",
            width="stretch",
            help="Relit la base SQLite et invalide le cache de lecture du dashboard.",
            on_click=bump_data_version,
        )
        if st.button(
            "Relancer collecte & scoring",
            width="stretch",
            help="Exécute run_scrapers.py --trigger-scoring --trigger-rerank et affiche le journal.",
        ):
            run_pipeline()
        st.caption("Sans clé DEEPSEEK_API_KEY, l'étape de reranking est ignorée proprement.")


# --------------------------------------------------------------------------- #
# Exécution du pipeline depuis le dashboard
# --------------------------------------------------------------------------- #
PIPELINE_ARGS: tuple[str, ...] = ("--trigger-scoring", "--trigger-rerank")


def run_pipeline() -> None:
    """Exécute collecte + scoring + reranking et diffuse le journal en continu."""
    command = [sys.executable, str(PROJECT_ROOT / "run_scrapers.py"), *PIPELINE_ARGS]
    environment = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    lines: list[str] = []
    with st.status("Pipeline en cours (collecte → scoring → reranking)…", expanded=True) as state:
        output = st.empty()
        process = subprocess.Popen(
            command,
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=environment,
        )
        if process.stdout is not None:
            for line in process.stdout:
                lines.append(line.rstrip())
                output.code("\n".join(lines[-400:]), language="text")
        return_code = process.wait()
        if return_code == 0:
            state.update(label=f"Pipeline terminé · {len(lines)} lignes de journal", state="complete")
        else:
            state.update(label=f"Pipeline interrompu (code {return_code})", state="error")
    bump_data_version()


# --------------------------------------------------------------------------- #
# Point d'entrée
# --------------------------------------------------------------------------- #
def main() -> None:
    """Assemble le dashboard : styles, filtres, en-tête, KPI et flux d'offres."""
    inject_styles()
    config = load_config()
    db = get_database()
    keywords = tuple(config.get("scoring", {}).get("excellence_keywords", ()))
    llm_model = str(config.get("llm", {}).get("model", "juge LLM"))

    data_version = int(st.session_state.setdefault("data_version", 0))
    jobs = load_jobs(db, data_version)
    sources = sorted({str(job["source"]) for job in jobs if job.get("source")}, key=source_rank)

    filters = render_sidebar_filters(jobs, sources)
    selected = filter_jobs(jobs, filters)

    render_header(jobs, config)
    st.markdown('<hr class="sc-rule">', unsafe_allow_html=True)
    render_kpis(selected, llm_model, len(jobs), not filters.is_default())
    render_stream(db, selected, filters, keywords)


if __name__ == "__main__":
    main()



