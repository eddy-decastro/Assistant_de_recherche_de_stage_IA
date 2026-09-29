"""Design system de Stage Copilot : jetons CSS par thème et feuille de style.

Une seule source de vérité : ``_PALETTE[theme]`` alimente l'espace de noms
``--sc-*`` (fonds, textes, bordures, accent indigo, tonalités fonctionnelles).
Le thème natif Streamlit (clair / sombre) est détecté à chaque rendu ; hors
exécution de script (tests, mode bare), la palette « auto » dérive les couleurs
de ``currentColor``.
"""
import streamlit as st
from string import Template

# --------------------------------------------------------------------------- #
# Palette : jetons par thème
# --------------------------------------------------------------------------- #
_PALETTE: dict[str, dict[str, str]] = {
    "light": {
        "bg": "#F7F7F8",
        "surface": "#FFFFFF",
        "surface_2": "#F4F4F5",
        "border": "#E4E4E7",
        "border_strong": "#D4D4D8",
        "text": "#18181B",
        "text_2": "#3F3F46",
        "text_3": "#71717A",
        "accent": "#4F46E5",
        "accent_hover": "#4338CA",
        "accent_soft": "rgba(79, 70, 229, 0.08)",
        "focus": "rgba(79, 70, 229, 0.35)",
        "shadow": "0 1px 2px rgba(24, 24, 27, 0.04), 0 1px 3px rgba(24, 24, 27, 0.05)",
        "shadow_hover": "0 6px 16px rgba(24, 24, 27, 0.08)",
    },
    "dark": {
        "bg": "#0E0F13",
        "surface": "#16181D",
        "surface_2": "#1D2027",
        "border": "#262A33",
        "border_strong": "#363B46",
        "text": "#ECECEF",
        "text_2": "#C4C4CC",
        "text_3": "#8B8D98",
        "accent": "#6366F1",
        "accent_hover": "#818CF8",
        "accent_soft": "rgba(129, 140, 248, 0.12)",
        "focus": "rgba(129, 140, 248, 0.45)",
        "shadow": "0 1px 2px rgba(0, 0, 0, 0.30)",
        "shadow_hover": "0 8px 20px rgba(0, 0, 0, 0.40)",
    },
    "auto": {
        "bg": "transparent",
        "surface": "color-mix(in srgb, currentColor 3%, transparent)",
        "surface_2": "color-mix(in srgb, currentColor 6%, transparent)",
        "border": "color-mix(in srgb, currentColor 14%, transparent)",
        "border_strong": "color-mix(in srgb, currentColor 26%, transparent)",
        "text": "currentColor",
        "text_2": "color-mix(in srgb, currentColor 80%, transparent)",
        "text_3": "color-mix(in srgb, currentColor 58%, transparent)",
        "accent": "#6366F1",
        "accent_hover": "#4F46E5",
        "accent_soft": "rgba(99, 102, 241, 0.10)",
        "focus": "rgba(99, 102, 241, 0.40)",
        "shadow": "none",
        "shadow_hover": "none",
    },
}

# Tonalités fonctionnelles : (texte, fond, bordure) par thème.
_TONE_PALETTE: dict[str, dict[str, tuple[str, str, str]]] = {
    "positive": {
        "light": ("#047857", "#ECFDF5", "#A7F3D0"),
        "dark": ("#34D399", "rgba(16, 185, 129, 0.12)", "rgba(16, 185, 129, 0.30)"),
        "auto": ("#10B981", "rgba(16, 185, 129, 0.12)", "rgba(16, 185, 129, 0.30)"),
    },
    "accent": {
        "light": ("#4338CA", "#EEF2FF", "#C7D2FE"),
        "dark": ("#A5B4FC", "rgba(99, 102, 241, 0.14)", "rgba(99, 102, 241, 0.34)"),
        "auto": ("#818CF8", "rgba(99, 102, 241, 0.14)", "rgba(99, 102, 241, 0.34)"),
    },
    "warn": {
        "light": ("#B45309", "#FFFBEB", "#FDE68A"),
        "dark": ("#FCD34D", "rgba(245, 158, 11, 0.12)", "rgba(245, 158, 11, 0.30)"),
        "auto": ("#F59E0B", "rgba(245, 158, 11, 0.12)", "rgba(245, 158, 11, 0.30)"),
    },
    "alert": {
        "light": ("#BE123C", "#FFF1F2", "#FECDD3"),
        "dark": ("#FDA4AF", "rgba(244, 63, 94, 0.12)", "rgba(244, 63, 94, 0.30)"),
        "auto": ("#F43F5E", "rgba(244, 63, 94, 0.12)", "rgba(244, 63, 94, 0.30)"),
    },
    "mute": {
        "light": ("#52525B", "#F4F4F5", "#E4E4E7"),
        "dark": ("#A1A1AA", "rgba(161, 161, 170, 0.10)", "rgba(161, 161, 170, 0.22)"),
        "auto": ("#71717A", "rgba(113, 113, 122, 0.10)", "rgba(113, 113, 122, 0.24)"),
    },
}

# --------------------------------------------------------------------------- #
# Feuille de style injectée (Template : les accolades CSS restent littérales)
# --------------------------------------------------------------------------- #
_CSS_TOKENS = Template(
    """@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap');

:root {
  --sc-bg: $bg;
  --sc-surface: $surface;
  --sc-surface-2: $surface_2;
  --sc-border: $border;
  --sc-border-strong: $border_strong;
  --sc-text: $text;
  --sc-text-2: $text_2;
  --sc-text-3: $text_3;
  --sc-accent: $accent;
  --sc-accent-hover: $accent_hover;
  --sc-accent-soft: $accent_soft;
  --sc-on-accent: #FFFFFF;
  --sc-focus: $focus;
  --sc-shadow: $shadow;
  --sc-shadow-hover: $shadow_hover;
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
  --sc-font: 'Inter', ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
  --sc-mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  --sc-radius: 12px;
  --sc-radius-sm: 8px;
  --sc-ease: cubic-bezier(0.2, 0.7, 0.2, 1);
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
/* ---------- Typographie & chrome applicatif ---------- */
.stApp h1, .stApp h2, .stApp h3, .stApp h4 { letter-spacing: -.015em; color: var(--sc-text); }
.stApp h3 { font-size: 1.15rem; font-weight: 650; }
.stApp h4 { font-size: 1rem; font-weight: 600; }
.stApp code { font-family: var(--sc-mono); }

[data-testid="stMainBlockContainer"] {
  padding-top: 3.2rem;
  padding-bottom: 5rem;
  max-width: 1280px;
}
[data-testid="stSidebarUserContent"] { padding-top: .4rem; }
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p {
  font-size: 12px;
  font-weight: 600;
  color: var(--sc-text-2) !important;
}
[data-testid="stSidebar"] hr { margin: 1rem 0 .8rem; border-color: var(--sc-border); }
[data-testid="stExpander"] details {
  border: 1px solid var(--sc-border) !important;
  border-radius: var(--sc-radius-sm) !important;
  background: var(--sc-surface);
}
[data-testid="stExpander"] summary p { font-size: 13px; font-weight: 600; color: var(--sc-text-2); }

/* Navigation latérale (st.navigation) */
[data-testid="stSidebarNav"] a, [data-testid="stSidebarNavLink"] { border-radius: var(--sc-radius-sm) !important; }
[data-testid="stSidebarNav"] a[aria-current="page"],
[data-testid="stSidebarNavLink"][aria-current="page"] {
  background: var(--sc-accent-soft) !important;
}
[data-testid="stSidebarNav"] a[aria-current="page"] span,
[data-testid="stSidebarNavLink"][aria-current="page"] span {
  color: var(--sc-accent-fg) !important;
  font-weight: 600 !important;
}
[data-testid="stNavSectionHeader"] {
  font-size: 11px !important;
  font-weight: 600 !important;
  letter-spacing: .08em;
  text-transform: uppercase;
  color: var(--sc-text-3) !important;
}

/* Chips multiselect */
[data-testid="stMultiSelect"] [data-baseweb="tag"] {
  background: var(--sc-surface-2) !important;
  color: var(--sc-text) !important;
  border: 1px solid var(--sc-border) !important;
  border-radius: 6px !important;
}
[data-testid="stMultiSelect"] [data-baseweb="tag"] span { color: var(--sc-text) !important; }

/* Onglets */
[data-baseweb="tab-list"] { gap: 4px; border-bottom: 1px solid var(--sc-border); }
[data-baseweb="tab"] p { font-size: 13.5px; font-weight: 550; }
[data-baseweb="tab"][aria-selected="true"] p { color: var(--sc-text) !important; font-weight: 650; }
[data-baseweb="tab-highlight"] { background-color: var(--sc-accent) !important; }

/* Métriques natives */
[data-testid="stMetric"] {
  padding: 12px 14px;
  border: 1px solid var(--sc-border);
  border-radius: var(--sc-radius-sm);
  background: var(--sc-surface);
}
[data-testid="stMetricLabel"] p { font-size: 12px; font-weight: 600; color: var(--sc-text-3) !important; }
[data-testid="stMetricValue"] { font-variant-numeric: tabular-nums; font-weight: 650; letter-spacing: -.02em; }

/* ---------- Boutons : thème natif (config.toml), libellés jamais tronqués ---------- */
[data-testid="stButton"] button p,
[data-testid="stLinkButton"] a p,
[data-testid="stDownloadButton"] button p,
[data-testid="stPopover"] button p { font-size: 13px; font-weight: 550; }

[data-testid="stCode"] pre { font-size: 12px; line-height: 1.5; }
"""
)

_CSS_HEADER_KPI = Template(
    """
/* ---------- En-tête de page ---------- */
.sc-page-head { margin-bottom: 18px; }
.sc-eyebrow {
  font-size: 11.5px;
  font-weight: 600;
  letter-spacing: .08em;
  text-transform: uppercase;
  color: var(--sc-accent-fg);
}
.sc-title {
  margin: 4px 0 0;
  font-family: var(--sc-font);
  font-size: 30px;
  font-weight: 700;
  letter-spacing: -.025em;
  line-height: 1.15;
  color: var(--sc-text);
}
.sc-subtitle { margin-top: 6px; font-size: 14px; line-height: 1.6; color: var(--sc-text-3); max-width: 780px; }
.sc-subtitle code, .sc-empty code, .sc-excerpt code {
  font-family: var(--sc-mono);
  font-size: 12px;
  padding: 1px 6px;
  border: 1px solid var(--sc-border);
  border-radius: 6px;
  background: var(--sc-surface-2);
  color: var(--sc-text-2);
}
.sc-rule { height: 1px; margin: 22px 0 18px; background: var(--sc-border); border: 0; }
.sc-section-title { margin: 22px 0 10px; font-size: 15px; font-weight: 650; letter-spacing: -.01em; color: var(--sc-text); }
.sc-section-title small { margin-left: 8px; font-size: 12.5px; font-weight: 500; color: var(--sc-text-3); }

/* ---------- Bandeau KPI ---------- */
.sc-kpis {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 22px;
}
.sc-kpi {
  min-width: 0;
  padding: 14px 16px;
  border: 1px solid var(--sc-border);
  border-radius: var(--sc-radius);
  background: var(--sc-surface);
  box-shadow: var(--sc-shadow);
}
.sc-kpi-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--sc-text-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sc-kpi-value {
  margin-top: 8px;
  font-size: 28px;
  font-weight: 700;
  line-height: 1;
  letter-spacing: -.03em;
  color: var(--sc-text);
  font-variant-numeric: tabular-nums;
}
.sc-kpi-value span { margin-left: 6px; font-size: 12px; font-weight: 500; color: var(--sc-text-3); letter-spacing: 0; }
.sc-kpi-hint { margin-top: 8px; font-size: 12px; color: var(--sc-text-3); }
.sc-dist { display: flex; gap: 2px; height: 6px; margin-top: 12px; border-radius: 999px; overflow: hidden; background: var(--sc-surface-2); }
.sc-dist i { display: block; height: 100%; }
.sc-legend { display: flex; flex-wrap: wrap; gap: 4px 12px; margin-top: 8px; font-size: 12px; color: var(--sc-text-3); }
.sc-legend i { display: inline-block; width: 7px; height: 7px; margin-right: 5px; border-radius: 999px; vertical-align: middle; }
.sc-legend b { font-weight: 600; color: var(--sc-text); font-variant-numeric: tabular-nums; }

/* ---------- En-tête de flux, groupes, état vide ---------- */
.sc-stream { display: flex; align-items: baseline; justify-content: space-between; gap: 14px; margin: 4px 0 12px; }
.sc-stream-count { font-size: 14px; font-weight: 650; color: var(--sc-text); font-variant-numeric: tabular-nums; }
.sc-stream-note { font-size: 12.5px; color: var(--sc-text-3); }
.sc-group { display: flex; align-items: baseline; gap: 8px; margin: 16px 0 8px; padding-bottom: 6px; border-bottom: 1px solid var(--sc-border); }
.sc-group-name { font-size: 12px; font-weight: 650; letter-spacing: .06em; text-transform: uppercase; color: var(--sc-text-2); }
.sc-group-count { font-size: 12px; color: var(--sc-text-3); font-variant-numeric: tabular-nums; }
.sc-empty {
  padding: 32px 20px;
  border: 1px dashed var(--sc-border-strong);
  border-radius: var(--sc-radius);
  background: var(--sc-surface);
  text-align: center;
  font-size: 14px;
  line-height: 1.7;
  color: var(--sc-text-2);
}
.sc-empty b { display: block; margin-bottom: 4px; font-size: 15px; color: var(--sc-text); }
.sc-filter-state { display: flex; align-items: center; gap: 6px; margin: 2px 0 6px; font-size: 12.5px; color: var(--sc-text-2); }
.sc-filter-state b { color: var(--sc-accent-fg); font-variant-numeric: tabular-nums; }
"""
)

_CSS_CARD = Template(
    """
/* ---------- Cartes Streamlit (st.container(border=True, key="card-…")) ---------- */
div[class*="st-key-card-"] {
  background: var(--sc-surface);
  border-radius: var(--sc-radius) !important;
  box-shadow: var(--sc-shadow);
  transition: border-color .15s var(--sc-ease), box-shadow .15s var(--sc-ease);
}
div[class*="st-key-card-item-"]:hover { border-color: var(--sc-border-strong) !important; box-shadow: var(--sc-shadow-hover); }
div[class*="st-key-card-sel-"] {
  border-color: var(--sc-accent) !important;
  box-shadow: 0 0 0 3px var(--sc-accent-soft) !important;
}
div[class*="st-key-card-kb-"] { padding: 12px !important; gap: 10px; }
div[class*="st-key-card-kb-"] [data-testid="stButton"] button,
div[class*="st-key-card-kb-"] [data-testid="stPopover"] button { min-height: 32px; padding: 0 8px; }

/* ---------- Élément de liste compact (colonne de gauche) ---------- */
.sc-list-item { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.sc-list-main { min-width: 0; }
.sc-list-title {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  font-size: 14.5px;
  font-weight: 600;
  line-height: 1.35;
  color: var(--sc-text);
}
.sc-list-meta { margin-top: 4px; font-size: 12.5px; color: var(--sc-text-3); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sc-list-meta b { font-weight: 600; color: var(--sc-text-2); }
.sc-list-tags { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }

/* ---------- Carte détaillée ---------- */
.sc-card { padding: 2px 0; }
.sc-card-actions-divider { height: 1px; margin: 14px 0 12px; background: var(--sc-border); }
.sc-card-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; }
.sc-card-title {
  margin: 0;
  font-family: var(--sc-font);
  font-size: 21px;
  font-weight: 700;
  line-height: 1.3;
  letter-spacing: -.02em;
  color: var(--sc-text);
}
.sc-card-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 6px; font-size: 13px; color: var(--sc-text-3); }
.sc-card-meta .sc-sep { color: var(--sc-border-strong); }
.sc-company { font-weight: 600; color: var(--sc-text); }
.sc-meta-item { color: var(--sc-text-3); }

/* Jauge de score : pastille + mini-barre 0–100 */
.sc-score-box { display: flex; flex: 0 0 auto; flex-direction: column; align-items: flex-end; gap: 5px; min-width: 76px; }
.sc-score {
  display: inline-flex;
  align-items: baseline;
  gap: 2px;
  padding: 3px 10px;
  border: 1px solid var(--sc-tone-bd, var(--sc-border));
  border-radius: 999px;
  background: var(--sc-tone-bg, var(--sc-surface-2));
  color: var(--sc-tone-fg, var(--sc-text));
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.sc-score b { font-size: 15px; font-weight: 700; letter-spacing: -.01em; color: inherit; }
.sc-score span { font-size: 11px; opacity: .75; color: inherit; }
.sc-score-bar { width: 100%; height: 4px; border-radius: 999px; background: var(--sc-surface-2); overflow: hidden; }
.sc-score-bar i { display: block; height: 100%; border-radius: 999px; background: var(--sc-tone-fg, var(--sc-accent)); }
.sc-align { font-size: 11px; font-weight: 600; color: var(--sc-tone-fg, var(--sc-text-3)); white-space: nowrap; }
.sc-quality-sub { display: block; margin-top: 2px; font-size: 11px; color: var(--sc-text-3); font-variant-numeric: tabular-nums; }
.sc-score-sm .sc-score { padding: 2px 8px; }
.sc-score-sm .sc-score b { font-size: 13.5px; }

.sc-badges { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 12px; }
.sc-badges .sc-badge-excellent { display: none !important; }
.sc-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 9px;
  border: 1px solid var(--sc-tone-bd, var(--sc-border));
  border-radius: 999px;
  background: var(--sc-tone-bg, var(--sc-surface-2));
  color: var(--sc-tone-fg, var(--sc-text-2));
  font-size: 12px;
  font-weight: 550;
  line-height: 1.6;
  white-space: nowrap;
}
.sc-dot { display: inline-block; flex: 0 0 auto; width: 7px; height: 7px; border-radius: 999px; background: currentColor; }
.sc-status { display: inline-flex; align-items: center; gap: 5px; font-weight: 550; font-size: 12.5px; color: var(--sc-tone-fg, var(--sc-text-3)); }

/* Tags de technologies détectées */
.sc-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
.sc-chip {
  padding: 1px 8px;
  border: 1px solid var(--sc-tone-bd, var(--sc-border));
  border-radius: 6px;
  background: var(--sc-tone-bg, var(--sc-surface));
  color: var(--sc-tone-fg, var(--sc-text-2));
  font-family: var(--sc-mono);
  font-size: 11.5px;
  font-weight: 500;
  line-height: 1.7;
  white-space: nowrap;
}

/* ---------- Grille d'évaluation : jauges à 5 segments ---------- */
.sc-subscore-strip {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 8px 16px;
  margin-top: 12px;
  padding: 10px 12px;
  border: 1px solid var(--sc-border);
  border-radius: var(--sc-radius-sm);
  background: var(--sc-surface-2);
}
.sc-subscore-item { display: flex; align-items: center; justify-content: space-between; gap: 8px; min-width: 0; font-size: 12px; color: var(--sc-text-2); }
.sc-subscore-item > span:first-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sc-subscore-item b { font-weight: 650; color: var(--sc-text); font-variant-numeric: tabular-nums; }
.sc-meter { display: inline-flex; flex: 0 0 auto; align-items: center; gap: 6px; }
.sc-meter-seg { display: inline-flex; gap: 2px; }
.sc-meter-seg i { display: block; width: 10px; height: 6px; border-radius: 2px; background: var(--sc-border-strong); }
.sc-meter-seg i.on { background: var(--sc-tone-fg, var(--sc-accent)); }
.sc-subscore-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 8px 18px; }

.sc-alert {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-top: 12px;
  padding: 9px 12px;
  border: 1px solid var(--sc-tone-bd, var(--sc-border));
  border-left-width: 3px;
  border-radius: var(--sc-radius-sm);
  background: var(--sc-tone-bg, var(--sc-surface-2));
  color: var(--sc-tone-fg, var(--sc-text));
  font-size: 12.5px;
  font-weight: 500;
  line-height: 1.5;
}
.sc-alert b { font-weight: 650; }
.sc-alert .sc-alert-icon { flex: 0 0 auto; font-weight: 700; }

/* ---------- Tableaux ---------- */
.sc-telemetry { margin-top: 14px; }
.sc-table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.sc-table th {
  text-align: left;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: .06em;
  text-transform: uppercase;
  color: var(--sc-text-3);
  padding: 8px;
  border-bottom: 1px solid var(--sc-border);
  white-space: nowrap;
}
.sc-table td { padding: 8px; border-bottom: 1px solid var(--sc-border); color: var(--sc-text-2); vertical-align: top; }
.sc-table tr:last-child td { border-bottom: none; }
.sc-table tbody tr:hover td { background: var(--sc-surface-2); }
.sc-table td.sc-num { font-variant-numeric: tabular-nums; white-space: nowrap; }
.sc-table .sc-strong { color: var(--sc-text); font-weight: 600; }
.sc-link { color: var(--sc-text); font-weight: 550; text-decoration: underline; text-decoration-color: var(--sc-border-strong); text-underline-offset: 3px; }
.sc-link:hover { color: var(--sc-accent-fg); text-decoration-color: currentColor; }
.sc-link-accent { font-size: 12px; font-weight: 600; white-space: nowrap; color: var(--sc-accent-fg); text-decoration: none; }
.sc-faint { color: var(--sc-text-3); }

/* ---------- Accordéon « Détails & évaluation » ---------- */
.sc-details { margin-top: 12px; border-top: 1px solid var(--sc-border); }
.sc-details > summary {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 10px 0 2px;
  list-style: none;
  cursor: pointer;
  font-size: 13px;
  font-weight: 600;
  color: var(--sc-accent-fg);
}
.sc-details > summary::-webkit-details-marker { display: none; }
.sc-details > summary:hover { color: var(--sc-text); }
.sc-chev { display: inline-block; font-size: 10px; line-height: 1; transition: transform .15s var(--sc-ease); }
.sc-details[open] > summary .sc-chev { transform: rotate(90deg); }
.sc-details-body { padding-top: 12px; }
.sc-section { font-size: 11.5px; font-weight: 650; letter-spacing: .06em; text-transform: uppercase; color: var(--sc-text-3); margin-bottom: 8px; }
.sc-section--sub { margin-top: 14px; }
.sc-block + .sc-block { margin-top: 18px; padding-top: 16px; border-top: 1px dashed var(--sc-border); }
.sc-list { display: flex; flex-direction: column; gap: 5px; margin: 0; padding: 0; list-style: none; }
.sc-list li { position: relative; padding-left: 16px; font-size: 13px; line-height: 1.55; color: var(--sc-text-2); }
.sc-list li::before {
  content: "";
  position: absolute;
  left: 3px;
  top: 8px;
  width: 6px;
  height: 6px;
  border-radius: 999px;
  background: var(--sc-tone-fg, currentColor);
}
.sc-kv { display: flex; flex-wrap: wrap; gap: 4px 20px; }
.sc-kv div { font-size: 12.5px; color: var(--sc-text-3); }
.sc-kv b { font-family: var(--sc-mono); font-size: 12px; font-weight: 600; font-variant-numeric: tabular-nums; color: var(--sc-text-2); }
.sc-excerpt { margin: 0; font-size: 13px; line-height: 1.65; color: var(--sc-text-2); white-space: pre-line; }
.sc-more { margin-top: 8px; }
.sc-more > summary { cursor: pointer; font-size: 12.5px; font-weight: 550; color: var(--sc-accent-fg); }
.sc-more > summary:hover { color: var(--sc-text); }
.sc-more[open] > summary { margin-bottom: 6px; }

/* ---------- Kanban ---------- */
.sc-kanban-col {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 10px;
  padding: 10px 12px;
  border: 1px solid var(--sc-border);
  border-top: 3px solid var(--sc-tone-fg, var(--sc-border-strong));
  border-radius: var(--sc-radius-sm);
  background: var(--sc-surface);
}
.sc-kanban-col b { font-size: 13.5px; font-weight: 650; color: var(--sc-text); }
.sc-kanban-count {
  min-width: 24px;
  padding: 0 7px;
  border-radius: 999px;
  background: var(--sc-tone-bg, var(--sc-surface-2));
  color: var(--sc-tone-fg, var(--sc-text-2));
  font-size: 12px;
  font-weight: 650;
  line-height: 20px;
  text-align: center;
  font-variant-numeric: tabular-nums;
}
.sc-kanban-card { padding-bottom: 12px; }
.sc-kanban-card .sc-list-title { font-size: 13.5px; -webkit-line-clamp: 3; }
.sc-kanban-card .sc-list-title a { color: inherit; text-decoration: none; }
.sc-kanban-card .sc-list-title a:hover { color: var(--sc-accent-fg); text-decoration: underline; text-underline-offset: 3px; }
.sc-kanban-empty { padding: 14px 8px; border: 1px dashed var(--sc-border); border-radius: var(--sc-radius-sm); text-align: center; font-size: 12.5px; color: var(--sc-text-3); }

/* ---------- Dialog lettre ---------- */
.sc-letter-head { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 10px; }
.sc-letter-head .sc-card-title { font-size: 18px; }
.sc-copy-btn {
  width: 100%;
  min-height: 40px;
  padding: 0 12px;
  border: 1px solid var(--sc-border-strong);
  border-radius: var(--sc-radius-sm);
  background: var(--sc-surface);
  color: var(--sc-text-2);
  font-family: var(--sc-font);
  font-size: 13px;
  font-weight: 550;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  transition: background-color .15s var(--sc-ease), border-color .15s var(--sc-ease), color .15s var(--sc-ease);
}
.sc-copy-btn:hover { border-color: var(--sc-text-3); background: var(--sc-surface-2); color: var(--sc-text); }
.sc-copy-btn.is-done { border-color: var(--sc-positive-bd); background: var(--sc-positive-bg); color: var(--sc-positive-fg); }

/* ---------- Écran de connexion ---------- */
.sc-auth { margin: 8vh auto 18px; text-align: center; }
.sc-auth-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  margin-bottom: 14px;
  border-radius: 12px;
  background: var(--sc-accent);
  color: var(--sc-on-accent);
  font-size: 18px;
  font-weight: 700;
  letter-spacing: -.03em;
}
.sc-auth .sc-title { font-size: 24px; }
.sc-auth .sc-subtitle { margin: 6px auto 0; }

/* ---------- Adaptations ---------- */
@media (max-width: 1100px) {
  .sc-kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 640px) {
  .sc-kpis { gap: 8px; }
  .sc-kpi { padding: 12px; }
  .sc-kpi-value { font-size: 24px; }
  .sc-kpi-value span { display: block; margin: 6px 0 0; }
  .sc-kpi:last-child { grid-column: 1 / -1; }
  .sc-title { font-size: 24px; }
  .sc-card-head { flex-direction: column; gap: 10px; }
  .sc-score-box { align-items: flex-start; }
  .sc-stream { flex-direction: column; gap: 2px; }
}
@media (prefers-reduced-motion: reduce) {
  .stApp * { transition: none !important; }
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
# Style injecté & accès au thème
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
    tokens = dict(_PALETTE[theme])
    for tone, variants in _TONE_PALETTE.items():
        fg, bg, bd = variants[theme]
        tokens[f"{tone}_fg"], tokens[f"{tone}_bg"], tokens[f"{tone}_bd"] = fg, bg, bd
    return tokens


def inject_styles() -> None:
    """Injecte la feuille de style, calibrée sur le thème natif courant."""
    st.markdown(_CSS_TEMPLATE.substitute(_token_context(_theme_type())), unsafe_allow_html=True)
