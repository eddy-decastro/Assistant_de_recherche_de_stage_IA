import streamlit as st
from string import Template

# Palette : jetons CSS dérivés du thème natif (dark / light), repli « auto »
# --------------------------------------------------------------------------- #
# Tonalités fonctionnelles : (texte, fond, bordure) par thème. Le thème « auto »
# n'est utilisé que si Streamlit n'expose pas son type de thème (mode bare).
_TONE_PALETTE: dict[str, dict[str, tuple[str, str, str]]] = {
    "positive": {
        "dark": ("#34d399", "rgba(16, 185, 129, 0.14)", "rgba(16, 185, 129, 0.34)"),
        "light": ("#3F5B34", "#E3EBDD", "#C8D8BF"),
        "auto": ("#10b981", "rgba(16, 185, 129, 0.14)", "rgba(16, 185, 129, 0.34)"),
    },
    "accent": {
        "dark": ("#a5b4fc", "rgba(99, 102, 241, 0.16)", "rgba(99, 102, 241, 0.36)"),
        "light": ("#B5482A", "rgba(181, 72, 42, 0.08)", "rgba(181, 72, 42, 0.25)"),
        "auto": ("#818cf8", "rgba(99, 102, 241, 0.16)", "rgba(99, 102, 241, 0.36)"),
    },
    "warn": {
        "dark": ("#fcd34d", "rgba(245, 158, 11, 0.14)", "rgba(245, 158, 11, 0.34)"),
        "light": ("#8C5815", "rgba(224, 159, 62, 0.12)", "rgba(224, 159, 62, 0.28)"),
        "auto": ("#fbbf24", "rgba(245, 158, 11, 0.14)", "rgba(245, 158, 11, 0.34)"),
    },
    "alert": {
        "dark": ("#fda4af", "rgba(244, 63, 94, 0.14)", "rgba(244, 63, 94, 0.34)"),
        "light": ("#9E2A2B", "rgba(158, 42, 43, 0.08)", "rgba(158, 42, 43, 0.25)"),
        "auto": ("#fb7185", "rgba(244, 63, 94, 0.14)", "rgba(244, 63, 94, 0.34)"),
    },
    "mute": {
        "dark": ("rgba(226, 232, 240, 0.78)", "rgba(148, 163, 184, 0.12)", "rgba(148, 163, 184, 0.26)"),
        "light": ("#5E5A52", "rgba(228, 222, 211, 0.40)", "#E4DED3"),
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
        "border": "#E4DED3",
        "border_hover": "#C8BFB0",
        "surface": "#FFFDF9",
        "surface_strong": "#EFEAE1",
        "muted": "#5E5A52",
        "faint": "#8C877D",
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
    """@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,600;1,6..72,400&display=swap');

:root {
  --bg: #F6F3EE;
  --surface: #FFFDF9;
  --border: #E4DED3;
  --border-2: #D9D1C2;
  --text: #1C1B19;
  --text-2: #4A463F;
  --text-3: #6E695F;
  --chip: #EFEAE0;
  --accent: #B5482A;
  --accent-h: #983A20;
  --ok: #4F6B3A;
  --ok-bg: #E3EBDD;
  --warn: #A8761F;
  --warn-bg: #F3E8CF;

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
  --sc-mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  --sc-serif: 'Newsreader', Georgia, serif;
  --sc-radius: 8px;
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
  padding-top: 2.4rem;
  padding-bottom: 5rem;
  max-width: 1160px;
}
[data-testid="stSidebarUserContent"] { padding-top: .4rem; }
[data-testid="stSidebar"] {
  background-color: var(--bg, #F6F3EE) !important;
}
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: .08em;
  text-transform: uppercase;
  color: var(--text-3, #6E695F) !important;
}
[data-testid="stSidebar"] hr { margin: 1.1rem 0 .9rem; border-color: var(--border, #E4DED3); }
[data-testid="stSidebar"] [data-testid="stExpander"] {
  border: 1px solid var(--border, #E4DED3);
  border-radius: 6px;
  background: var(--surface, #FFFDF9);
}
[data-testid="stSidebar"] [data-testid="stExpander"] summary {
  font-size: 11.5px;
  font-weight: 600;
  letter-spacing: .06em;
  text-transform: uppercase;
  color: var(--text-2, #4A463F);
}

/* Chips multiselect sidebar */
[data-testid="stMultiSelect"] [data-baseweb="tag"] {
  background: var(--chip, #EFEAE0) !important;
  color: var(--text, #1C1B19) !important;
  border: 1px solid var(--border-2, #D9D1C2) !important;
  border-radius: 4px !important;
}
[data-testid="stMultiSelect"] [data-baseweb="tag"] span {
  color: var(--text, #1C1B19) !important;
}

/* Curseur / slider dans la sidebar : couleurs neutres (pas de terracotta) */
[data-testid="stSlider"] div[data-baseweb="slider"] > div {
  background: var(--border-2, #D9D1C2) !important;
}
[data-testid="stSlider"] div[data-baseweb="slider"] > div > div:first-child {
  background: var(--text, #1C1B19) !important;
}
[data-testid="stSlider"] [role="slider"] {
  background-color: var(--text, #1C1B19) !important;
  border-color: var(--text, #1C1B19) !important;
  box-shadow: none !important;
}

/* ---------- Boutons secondaires (Lettre, Archiver, statuts) ---------- */
[data-testid="stButton"] button,
[data-testid="stLinkButton"] a {
  border: 1px solid var(--border-2, #D9D1C2) !important;
  border-radius: 6px !important;
  background: transparent !important;
  color: var(--text-2, #4A463F) !important;
  box-shadow: none !important;
  font-size: 12.5px;
  font-weight: 550;
  height: 36px !important;
  min-height: 36px !important;
  padding: 0 12px !important;
  text-decoration: none !important;
  display: inline-flex !important;
  align-items: center !important;
  justify-content: center !important;
  box-sizing: border-box !important;
  max-width: 100% !important;
  overflow: hidden !important;
  transition: background-color .15s ease, border-color .15s ease, color .15s ease !important;
}
[data-testid="stButton"] button p,
[data-testid="stLinkButton"] a p {
  font-size: 12.5px;
  font-weight: 550;
  color: inherit !important;
  overflow: hidden !important;
  text-overflow: ellipsis !important;
  white-space: nowrap !important;
}
[data-testid="stButton"] button:hover,
[data-testid="stLinkButton"] a:hover {
  border-color: var(--border-2, #D9D1C2) !important;
  background: var(--chip, #EFEAE0) !important;
  color: var(--text, #1C1B19) !important;
}
[data-testid="stButton"] button:hover p,
[data-testid="stLinkButton"] a:hover p { color: var(--text, #1C1B19) !important; }

/* Bouton primaire (Postuler ↗) : réservé à la terracotta */
[data-testid="stLinkButton"] a[kind="primary"],
[data-testid="stButton"] button[kind="primary"],
[data-testid="stLinkButton"] a[data-testid="baseButton-primary"],
[data-testid="stButton"] button[data-testid="baseButton-primary"] {
  background: var(--accent, #B5482A) !important;
  border: 1px solid var(--accent, #B5482A) !important;
  border-radius: 6px !important;
  color: #ffffff !important;
  font-weight: 600 !important;
  font-size: 12.5px !important;
  height: 36px !important;
  min-height: 36px !important;
  padding: 0 12px !important;
  box-sizing: border-box !important;
  max-width: 100% !important;
  overflow: hidden !important;
  box-shadow: none !important;
  display: inline-flex !important;
  align-items: center !important;
  justify-content: center !important;
  transition: background-color .15s ease, border-color .15s ease !important;
}
[data-testid="stLinkButton"] a[kind="primary"]:hover,
[data-testid="stButton"] button[kind="primary"]:hover,
[data-testid="stLinkButton"] a[data-testid="baseButton-primary"]:hover,
[data-testid="stButton"] button[data-testid="baseButton-primary"]:hover {
  background: var(--accent-h, #983A20) !important;
  border-color: var(--accent-h, #983A20) !important;
  color: #ffffff !important;
  box-shadow: none !important;
  transform: none !important;
}
[data-testid="stLinkButton"] a[kind="primary"] p,
[data-testid="stButton"] button[kind="primary"] p,
[data-testid="stLinkButton"] a[data-testid="baseButton-primary"] p,
[data-testid="stButton"] button[data-testid="baseButton-primary"] p {
  color: #ffffff !important;
  font-size: 12.5px !important;
  font-weight: 600 !important;
  overflow: hidden !important;
  text-overflow: ellipsis !important;
  white-space: nowrap !important;
}

/* Accent terracotta sur les onglets et éléments actifs de la sidebar */
[data-testid="stSidebarNav"] a[aria-current="page"],
[data-testid="stSidebar"] [data-testid="stSidebarNavLink"][aria-current="page"] {
  background: var(--chip, #EFEAE0) !important;
  color: var(--text, #1C1B19) !important;
  font-weight: 600 !important;
  border-left: 3px solid var(--accent, #B5482A) !important;
  border-radius: 0 6px 6px 0 !important;
}
[data-testid="stSidebarNav"] a[aria-current="page"] span,
[data-testid="stSidebar"] [data-testid="stSidebarNavLink"][aria-current="page"] span {
  color: var(--text, #1C1B19) !important;
  font-weight: 600 !important;
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
  letter-spacing: .12em;
  text-transform: uppercase;
  color: var(--text-3, #6E695F);
}
.sc-title {
  margin: 6px 0 0;
  font-family: var(--sc-serif, 'Newsreader', Georgia, serif);
  font-size: 28px;
  font-weight: 600;
  letter-spacing: -.02em;
  line-height: 1.15;
  color: var(--text, #1C1B19);
}
.sc-subtitle { margin-top: 6px; font-size: 13px; line-height: 1.6; color: var(--text-3, #6E695F); }
.sc-subtitle code, .sc-empty code {
  font-family: var(--sc-mono);
  font-size: 11.5px;
  padding: 1px 5px;
  border: 1px solid var(--border-2, #D9D1C2);
  border-radius: 4px;
  background: var(--chip, #EFEAE0);
  color: var(--text-2, #4A463F);
}
.sc-rule { height: 1px; margin: 20px 0 16px; background: var(--border, #E4DED3); border: 0; }

/* ---------- Bandeau KPI ---------- */
.sc-kpis {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  border: 1px solid var(--border, #E4DED3);
  border-radius: var(--sc-radius);
  overflow: hidden;
  background: var(--surface, #FFFDF9);
}
.sc-kpi { padding: 14px 16px; min-width: 0; border-right: 1px solid var(--border, #E4DED3); }
.sc-kpi:last-child { border-right: 0; }
.sc-kpi-label {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: .08em;
  text-transform: uppercase;
  color: var(--text-3, #6E695F);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sc-kpi-value {
  margin-top: 6px;
  font-family: var(--sc-serif, 'Newsreader', Georgia, serif);
  font-size: 28px;
  font-weight: 600;
  line-height: 1;
  letter-spacing: -.02em;
  color: var(--text, #1C1B19);
  font-variant-numeric: tabular-nums;
}
.sc-kpi-value span { margin-left: 6px; font-family: system-ui, -apple-system, sans-serif; font-size: 12px; font-weight: 500; color: var(--text-3, #6E695F); letter-spacing: 0; }
.sc-kpi-hint { margin-top: 6px; font-size: 11.5px; color: var(--text-3, #6E695F); }
.sc-dist { display: flex; height: 5px; margin-top: 10px; border-radius: 999px; overflow: hidden; background: var(--chip, #EFEAE0); }
.sc-dist i { display: block; height: 100%; }
.sc-legend { display: flex; flex-wrap: wrap; gap: 4px 12px; margin-top: 8px; font-size: 11.5px; color: var(--text-3, #6E695F); }
.sc-legend i { display: inline-block; width: 6px; height: 6px; margin-right: 5px; border-radius: 999px; vertical-align: middle; }
.sc-legend b { font-weight: 600; color: var(--text, #1C1B19); font-variant-numeric: tabular-nums; }

/* ---------- En-tête de flux, groupes, état vide ---------- */
.sc-stream { display: flex; align-items: baseline; justify-content: space-between; gap: 14px; margin: 4px 0 12px; }
.sc-stream-count { font-size: 13.5px; font-weight: 600; color: var(--text, #1C1B19); font-variant-numeric: tabular-nums; }
.sc-stream-note { font-size: 12px; color: var(--text-3, #6E695F); }
.sc-group { display: flex; align-items: baseline; gap: 9px; margin: 22px 0 8px; padding-bottom: 6px; border-bottom: 1px solid var(--border, #E4DED3); }
.sc-group-name { font-size: 12px; font-weight: 650; letter-spacing: .08em; text-transform: uppercase; color: var(--text, #1C1B19); }
.sc-group-count { font-size: 12px; color: var(--text-3, #6E695F); font-variant-numeric: tabular-nums; }
.sc-empty {
  padding: 24px 18px;
  border: 1px dashed var(--border-2, #D9D1C2);
  border-radius: var(--sc-radius);
  text-align: center;
  font-size: 13px;
  line-height: 1.7;
  color: var(--text-2, #4A463F);
}
"""
)

_CSS_CARD = Template(
    """
/* ---------- Conteneur unifié de carte (st.container avec bordure) ---------- */
div[data-testid="stVerticalBlockBorderWrapper"] {
  background: var(--surface, #FFFDF9);
  border: 1px solid var(--border, #E4DED3) !important;
  border-radius: 8px !important;
  box-shadow: none !important;
  padding: 10px 14px 14px;
  margin-bottom: 0.9rem;
  transition: border-color .15s ease;
}
div[data-testid="stVerticalBlockBorderWrapper"]:hover {
  border-color: var(--border-2, #D9D1C2) !important;
  box-shadow: none !important;
}

/* Carte intérieure (contenu HTML) */
.sc-card {
  padding: 2px 0;
  border: none;
  border-radius: 0;
  background: transparent;
}
.sc-card:hover {
  background: transparent;
}

/* Séparateur discret avant la barre d'actions */
.sc-card-actions-divider {
  height: 1px;
  margin: 12px 0 10px;
  background: var(--border, #E4DED3);
}

.sc-card-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; }
.sc-card-title {
  margin: 0;
  font-family: var(--sc-serif, 'Newsreader', Georgia, serif);
  font-size: 22px;
  font-weight: 600;
  line-height: 1.25;
  letter-spacing: -.01em;
  color: var(--text, #1C1B19);
}
.sc-card-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 6px; font-size: 13px; color: var(--text-3, #6E695F); }
.sc-card-meta .sc-sep { color: var(--border-2, #D9D1C2); }
.sc-company { font-weight: 600; color: var(--text, #1C1B19); }
.sc-meta-item { color: var(--text-3, #6E695F); }
.sc-score-box { display: flex; flex: 0 0 auto; flex-direction: column; align-items: flex-end; gap: 5px; }
.sc-score {
  display: inline-flex;
  align-items: baseline;
  gap: 3px;
  padding: 4px 10px;
  border: 1px solid #CBD9C2;
  border-radius: 6px;
  background: var(--ok-bg, #E3EBDD);
  color: var(--ok, #4F6B3A);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.sc-score b { font-size: 16px; font-weight: 650; letter-spacing: -.01em; color: var(--ok, #4F6B3A); }
.sc-score span { font-size: 11px; opacity: .8; color: var(--ok, #4F6B3A); }
.sc-align {
  font-size: 10.5px;
  font-weight: 600;
  letter-spacing: .08em;
  text-transform: uppercase;
  color: var(--text-3, #6E695F);
}

/* Tonalité d'alignement warn pour les scores secondaires */
.sc-score-box.sc-tone-warn .sc-score {
  background: var(--warn-bg, #F3E8CF);
  color: var(--warn, #A8761F);
  border-color: #E2D3B3;
}
.sc-score-box.sc-tone-warn .sc-score b,
.sc-score-box.sc-tone-warn .sc-score span {
  color: var(--warn, #A8761F);
}
.sc-score-box.sc-tone-warn .sc-align {
  color: var(--warn, #A8761F);
}

.sc-badges { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 10px; }
/* Masquer le badge Excellent redondant avec le score */
.sc-badges .sc-badge-excellent { display: none !important; }

.sc-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 8px;
  border: 1px solid var(--border-2, #D9D1C2);
  border-radius: 6px;
  background: var(--chip, #EFEAE0);
  color: var(--text-2, #4A463F);
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;
}
.sc-dot { display: inline-block; flex: 0 0 auto; width: 6px; height: 6px; border-radius: 999px; }
.sc-status {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-weight: 550;
  font-size: 12px;
}
.sc-status-postule, .sc-status.sc-status-postule { color: var(--ok, #4F6B3A) !important; }
.sc-status-postule .sc-dot { background: var(--ok, #4F6B3A) !important; }
.sc-status-nouveau, .sc-status.sc-status-nouveau { color: var(--warn, #A8761F) !important; }
.sc-status-nouveau .sc-dot { background: var(--warn, #A8761F) !important; }

/* Tags de technologies détectées */
.sc-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 9px; }
.sc-chip {
  padding: 2px 8px;
  border: 1px solid var(--border-2, #D9D1C2);
  border-radius: 4px;
  background: var(--chip, #EFEAE0);
  color: var(--text-2, #4A463F);
  font-family: var(--sc-mono);
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;
}
.sc-card-actions { margin-top: 12px; }

/* ---------- Grille d'évaluation (sous-scores) & verrou bloquant ---------- */
.sc-subscore-strip {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 12px;
  margin-top: 10px;
  padding: 6px 12px;
  border: 1px solid var(--border, #E4DED3);
  border-radius: 6px;
  background: var(--chip, #EFEAE0);
  font-size: 12px;
  color: var(--text-3, #6E695F);
  font-variant-numeric: tabular-nums;
}
.sc-subscore-strip .sc-subscore-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--text-3, #6E695F);
  filter: grayscale(100%);
  opacity: 0.85;
}
.sc-subscore-strip .sc-subscore-item:hover { opacity: 1; }
.sc-subscore-strip .sc-subscore-item b { color: var(--text, #1C1B19); font-weight: 600; }
.sc-subscore-strip .sc-subscore-item.sc-tone-mute b,
.sc-subscore-strip .sc-subscore-item.sc-tone-warn b,
.sc-subscore-strip .sc-subscore-item.sc-tone-alert b {
  color: var(--warn, #A8761F);
}
.sc-subscore-strip .sc-sep { color: var(--border-2, #D9D1C2); }

.sc-alert {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
  padding: 8px 12px;
  border: 1px solid var(--sc-tone-bd, var(--border));
  border-left-width: 3px;
  border-radius: 6px;
  background: var(--sc-tone-bg, var(--surface-strong));
  color: var(--sc-tone-fg, var(--text));
  font-size: 12px;
  font-weight: 550;
  line-height: 1.45;
}
.sc-alert .sc-alert-icon { font-size: 13px; flex: 0 0 auto; }
.sc-telemetry { margin-top: 14px; }
.sc-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.sc-table th {
  text-align: left;
  font-size: 10.5px;
  font-weight: 600;
  letter-spacing: .08em;
  text-transform: uppercase;
  color: var(--text-3, #6E695F);
  padding: 6px 8px;
  border-bottom: 1px solid var(--border, #E4DED3);
  white-space: nowrap;
}
.sc-table td {
  padding: 6px 8px;
  border-bottom: 1px solid var(--border, #E4DED3);
  color: var(--text-2, #4A463F);
  vertical-align: top;
}
.sc-table tr:last-child td { border-bottom: none; }
.sc-table td.sc-num { font-variant-numeric: tabular-nums; white-space: nowrap; }
.sc-table .sc-strong { color: var(--text, #1C1B19); font-weight: 600; }

/* ---------- Accordéon « Détails & évaluation » ---------- */
.sc-details { margin-top: 10px; border-top: 1px solid var(--border, #E4DED3); }
.sc-details > summary {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 0 2px;
  list-style: none;
  cursor: pointer;
  font-size: 12.5px;
  font-weight: 550;
  color: var(--text-2, #4A463F);
  text-decoration: underline;
  text-underline-offset: 3px;
  text-decoration-color: var(--border-2, #D9D1C2);
  transition: color .15s ease, text-decoration-color .15s ease;
}
.sc-details > summary::-webkit-details-marker { display: none; }
.sc-details > summary:hover {
  color: var(--text, #1C1B19);
  text-decoration-color: var(--text-2, #4A463F);
}
.sc-chev {
  display: inline-block;
  font-size: 13px;
  line-height: 1;
  color: var(--text-3, #6E695F);
  text-decoration: none !important;
  transition: transform .15s ease;
}
.sc-details[open] > summary .sc-chev { transform: rotate(90deg); }
.sc-details-body { padding-top: 10px; }
.sc-section {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: .1em;
  text-transform: uppercase;
  color: var(--text-3, #6E695F);
  margin-bottom: 6px;
}
.sc-section--sub { margin-top: 12px; }
.sc-block + .sc-block { margin-top: 14px; }
.sc-list { display: flex; flex-direction: column; gap: 4px; margin: 0; padding: 0; list-style: none; }
.sc-list li { position: relative; padding-left: 14px; font-size: 12.5px; line-height: 1.5; color: var(--text-2, #4A463F); }
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
.sc-kv div { font-size: 12px; color: var(--text-3, #6E695F); }
.sc-kv b {
  font-family: var(--sc-mono);
  font-size: 12px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--text-2, #4A463F);
}
.sc-excerpt { margin: 0; font-size: 12.5px; line-height: 1.6; color: var(--text-2, #4A463F); white-space: pre-line; }
.sc-more { margin-top: 8px; }
.sc-more > summary { cursor: pointer; font-size: 11.5px; color: var(--text-3, #6E695F); text-decoration: underline; text-underline-offset: 2px; }
.sc-more > summary:hover { color: var(--text-2, #4A463F); }
.sc-more[open] > summary { margin-bottom: 6px; }
.sc-cta {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 10px;
  border: 1px solid var(--border-2, #D9D1C2);
  border-radius: 6px;
  background: transparent;
  color: var(--text-2, #4A463F);
  font-size: 12.5px;
  font-weight: 550;
  text-decoration: none;
}
.sc-cta:hover { border-color: var(--border-2, #D9D1C2); background: var(--chip, #EFEAE0); color: var(--text, #1C1B19); text-decoration: none; }

/* ---------- Adaptations ---------- */
@media (max-width: 1100px) {
  .sc-kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .sc-kpi:nth-child(2) { border-right: 0; }
  .sc-kpi:nth-child(-n+2) { border-bottom: 1px solid var(--border, #E4DED3); }
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


# --------------------------------------------------------------------------- #
