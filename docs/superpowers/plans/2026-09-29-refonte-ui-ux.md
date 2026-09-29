# Refonte UI/UX — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refondre l'UI/UX de Stage Copilot : navigation `st.navigation`, flux d'offres en composant CCv2 (liste dense + détail), et une grammaire visuelle commune aux 5 pages.

**Architecture:** `app.py` devient un routeur (auth, styles, navigation) ; les pages migrent de `pages/` vers `app_pages/`. Le flux est un composant Streamlit v2 inline en JS vanilla (`components/job_feed/`) alimenté par une sérialisation pure `job -> dict` ; la sélection est purement côté client, les actions de statut reviennent par un trigger validé et traité dans un callback. Les autres pages restent natives et sont harmonisées.

**Tech Stack:** Streamlit 1.63 (`st.navigation`, `st.components.v2`, `st.metric`, `st.popover`), JS vanilla (ES module inline), pytest + `streamlit.testing.v1.AppTest`, Node (tests de la logique JS pure, ignorés si absent).

**Spec:** `docs/superpowers/specs/2026-09-29-refonte-ui-ux-design.md`

## Global Constraints

- Streamlit `>=1.63` (CCv2 récent) — épinglé dans `requirements.txt` et `requirements-render.txt`.
- CCv2 uniquement : jamais `st.components.v1`, `components.declare_component`, `Streamlit.setComponentValue`, `window.Streamlit`, `window.parent.postMessage`.
- Le JS n'injecte **jamais** de donnée d'offre via `innerHTML` : `createElement` + `textContent` uniquement.
- Aucun emoji dans l'interface (libellés, onglets, boutons, toasts) ; icônes Material `:material/nom:` uniquement. Les symboles typographiques `↗ · — …` sont autorisés.
- `use_container_width` interdit : `width="stretch"` / `width="content"`.
- Libellés en français, casse phrase (« Marquer postulé », pas « Marquer Postulé »).
- Aucune modification du scoring, du scraping, du schéma SQLite ni du juge LLM ; aucun calcul de Statistiques modifié.
- `page_title`, `set_page_config`, `require_auth`, `inject_styles` : appelés une seule fois, dans `app.py`.
- Les pages restent des scripts directs (pas de `main()` ajouté) ; les tests importent les helpers de page par `app_pages.<page>`.
- Commandes Python : `.venv/Scripts/python.exe` (Windows, Git Bash). Ne pas committer `.claude/launch.json` ni les fichiers non suivis déjà présents (`PROMPT_ANTIGRAVITY_grille_v2.md`, `implementation_plan.md`, `run_app.py`, `task.md`, `data/prompt_rerank_v1_backup.txt`).
- Un commit par tâche (messages `feat(ui):` / `refactor(ui):` / `test(ui):` / `docs:`), avec la ligne d'attribution exigée par la session.
- La base SQLite locale contient les vraies candidatures de l'utilisateur : toute vérification manuelle qui change un statut doit être annulée (Annuler / Rétablir) avant de continuer.

## Review Focus

- Offre dont l'URL est `javascript:…`, `data:…` ou vide : aucun lien « Postuler » cliquable, aucune exception (tests Task 3 et Task 5).
- Titre / fiche contenant du HTML (`<img src=x onerror=alert(1)>`) : affiché littéralement, jamais interprété ; la donnée sérialisée n'est PAS échappée (double échappement sinon) (Task 3 + démo hostile Task 5).
- Action de statut forgée (statut inconnu, id vide, type inconnu, charge non-dict) : ignorée sans écriture en base (Task 4).
- Dernière offre visible archivée avec « Masquer les offres traitées » actif, ou filtres donnant zéro résultat : état vide propre, pas d'erreur JS ni de `st.info` cassé (Task 5 + Task 6).
- Grosses listes (mode « Tout », plus de 1000 offres à fiches longues) et statuts hors norme venus de la base (`REJETÉ`, `EXCLU`, `None`) : payload borné, actions cohérentes (Task 3 + Task 5).

---

## File Structure

| Fichier | Rôle |
|---|---|
| `app.py` (réécrit) | Routeur : `set_page_config`, auth, styles, `st.navigation`, badge de tâche + déconnexion en bas de sidebar |
| `app_pages/flux.py` (créé) | Page Flux : filtres sidebar, en-tête, KPI natifs, `job_feed` |
| `app_pages/kanban.py`, `statistiques.py`, `pipeline.py`, `parametres.py` (déplacés depuis `pages/`) | Pages existantes, sans boilerplate global |
| `components/__init__.py`, `components/job_feed/__init__.py` | Enregistrement CCv2 + wrapper `job_feed(...)` |
| `components/job_feed/serialize.py` | `serialize_job`, `serialize_feed` (pures, testables) |
| `components/job_feed/actions.py` | `parse_action`, `apply_status_action` (pures, `db` injectée) |
| `components/job_feed/feed.html` / `feed.css` / `feed.js` | Front du composant |
| `utils/layout.py` (créé) | `page_header(title, caption)`, `kpi_row(items)` communs à toutes les pages |
| `utils/components.py` (allégé) | Garde filtres sidebar, `show_cover_letter_dialog`, `_badge`, `score_alignment`, KPI natifs ; perd tout le rendu de cartes |
| `utils/styles.py` (réduit en Task 11) | Uniquement les classes encore utilisées |
| `tools/feed_demo.py` (créé) | Page de démonstration du composant avec offres factices (dont hostiles) |
| `tests/test_job_feed.py`, `tests/test_job_feed_js.py`, `tests/test_ui_conventions.py` (créés) | Sérialisation/actions, logique JS sous Node, garde-fous « pas d'emoji / pas de `use_container_width` » |
| `tests/test_app.py`, `tests/test_database.py` (adaptés) | Nouveaux chemins de pages, fin des tests de cartes HTML |

---

### Task 0: Baseline

**Files:** aucun.

- [ ] **Step 1: Vérifier l'état de départ**

Run: `git status --short && git branch --show-current`
Expected: branche `feat/refonte-ui-ux`, seuls les fichiers non suivis listés dans Global Constraints (+ `.claude/`).

- [ ] **Step 2: Lancer la suite existante**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`
Expected: tout vert (`tests/test_app.py` : 13 passed). Noter tout échec préexistant ailleurs : il n'est pas à corriger ici.

---

## Livraison 1 — Navigation et chrome

### Task 1: Navigation `st.navigation` et déplacement des pages

**Files:**
- Rename: `pages/` -> `app_pages/` (`git mv`)
- Create: `app_pages/flux.py`
- Modify (réécriture complète): `app.py`
- Modify: `app_pages/kanban.py`, `app_pages/statistiques.py`, `app_pages/pipeline.py`, `app_pages/parametres.py` (retrait du boilerplate)
- Modify: `utils/components.py` (`render_sidebar_filters` : retirer le badge de tâche)
- Modify: `tests/test_app.py`, `tests/test_database.py`, `README.md`

**Interfaces:**
- Produces: pages `app_pages/<nom>.py` exécutables comme scripts directs sous `st.navigation` ; `app_pages.pipeline.PIPELINE_ACTIONS` et `app_pages.statistiques.*` importables par les tests.

- [ ] **Step 1: Déplacer les pages**

```bash
git mv pages app_pages
```

- [ ] **Step 2: Créer `app_pages/flux.py` (contenu de l'ancienne `main()` d'`app.py`, encore avec l'ancien rendu de cartes)**

```python
"""Page Flux : KPI, filtres latéraux et flux d'offres scorées."""
from __future__ import annotations

import streamlit as st

from src.config import load_config
from src.constants import source_rank
from utils.components import (
    render_header,
    render_kpis,
    render_sidebar_filters,
    render_stream,
)
from utils.data import filter_jobs, get_database, load_jobs

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
render_kpis(selected, llm_model, len(jobs), not filters.is_default())
render_stream(db, selected, filters, keywords)
```

- [ ] **Step 3: Réécrire `app.py` en routeur**

```python
"""Point d'entrée Stage Copilot : authentification, styles globaux et navigation."""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.auth import render_logout_button, require_auth
from utils.styles import inject_styles
from utils.task_manager import render_sidebar_task_badge

st.set_page_config(
    page_title="Stage Copilot",
    page_icon=":material/insights:",
    layout="wide",
)

require_auth()
inject_styles()

navigation = st.navigation(
    {
        "Veille": [
            st.Page("app_pages/flux.py", title="Flux", icon=":material/view_list:", default=True),
            st.Page("app_pages/kanban.py", title="Candidatures", icon=":material/view_kanban:"),
        ],
        "Analyse": [
            st.Page("app_pages/statistiques.py", title="Statistiques", icon=":material/monitoring:"),
        ],
        "Système": [
            st.Page("app_pages/pipeline.py", title="Pipeline", icon=":material/sync:"),
            st.Page("app_pages/parametres.py", title="Paramètres", icon=":material/tune:"),
        ],
    }
)
navigation.run()

# Après la page : filtres de la page d'abord, badge de tâche et déconnexion en bas de sidebar.
render_sidebar_task_badge()
render_logout_button()
```

- [ ] **Step 4: Retirer le boilerplate de chaque page**

Dans `app_pages/kanban.py`, `statistiques.py`, `pipeline.py`, `parametres.py`, supprimer :
- l'appel `st.set_page_config(...)` ;
- `inject_styles()` et son import `from utils.styles import inject_styles` ;
- `render_sidebar_task_badge()` (et l'import correspondant s'il n'est plus utilisé) ;
- `from utils.auth import require_auth, render_logout_button` + `require_auth()` + `render_logout_button()` ;
- les blocs « Rechargement défensif » (`importlib.reload`) de `kanban.py` et `statistiques.py` (et le bloc `hasattr(db, "get_rejected_seen_jobs")` de `statistiques.py` : le garder tel quel s'il dépend d'une vraie méthode manquante — vérifier avec `grep -n get_rejected_seen_jobs src/storage/database.py`, si la méthode existe le bloc est mort et se supprime) ;
- dans `kanban.py`, le bloc `st.markdown("<style>…max-width: 98%…</style>")` (le `max-width` global disparaît en Task 2).

Conserver les `sys.path.insert` de tête de fichier (inoffensifs) et le reste du contenu.

- [ ] **Step 5: Retirer le badge de tâche des filtres**

Dans `utils/components.py`, fonction `render_sidebar_filters`, supprimer les deux lignes :

```python
        from utils.task_manager import render_sidebar_task_badge
        render_sidebar_task_badge()
```

- [ ] **Step 6: Adapter les tests aux nouveaux chemins**

Dans `tests/test_app.py` :
- `from pages.pipeline import PIPELINE_ACTIONS` -> `from app_pages.pipeline import PIPELINE_ACTIONS`
- `from pages.statistiques import (` -> `from app_pages.statistiques import (`
- supprimer la ligne `import app` (inutilisée : vérifier `grep -n "\bapp\." tests/test_app.py` ne montre rien hors `AppTest`)
- `PROJECT_ROOT / "pages" / "statistiques.py"` -> `PROJECT_ROOT / "app_pages" / "statistiques.py"` (idem `kanban.py`, `parametres.py`)

Dans `tests/test_database.py` : `from pages.statistiques import normalize_region` -> `from app_pages.statistiques import normalize_region`.

Dans `README.md` : remplacer `pages/kanban.py`, `pages/statistiques.py`, `pages/pipeline.py`, `pages/parametres.py` par `app_pages/…` (lignes du tableau des fonctionnalités).

- [ ] **Step 7: Lancer les tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_app.py tests/test_database.py -q -p no:cacheprovider`
Expected: tout vert. Si `test_interface_streamlit` échoue parce qu'`AppTest.from_file(app.py)` n'exécute pas la page par défaut, remplacer dans ce test `PROJECT_ROOT / "app.py"` par `PROJECT_ROOT / "app_pages" / "flux.py"` (la page est un script direct, testable seule) et le noter dans le message de commit.

- [ ] **Step 8: Vérification navigateur**

Le serveur `stage-copilot` (`.claude/launch.json`, port 8511) recharge à chaud ; sinon le relancer avec `preview_start`. Ouvrir `http://localhost:8511/`, vérifier : menu en 3 sections (Veille / Analyse / Système) avec icônes, chaque page se charge sans erreur rouge, filtres visibles uniquement sur Flux, « Déconnexion » absente si `APP_PASSWORD` vide.

- [ ] **Step 9: Commit**

```bash
git add -A app.py app_pages tests README.md utils/components.py
git status --short   # ne doit lister ni .claude/ ni les fichiers non suivis d'origine
git commit -m "feat(ui): navigation st.navigation en 3 sections et pages deplacees dans app_pages"
```

---

### Task 2: Thème natif et largeur pleine

**Files:**
- Modify: `.streamlit/config.toml`
- Modify: `utils/styles.py` (`_CSS_CHROME`, bloc `stMainBlockContainer`)

**Interfaces:** aucune.

- [ ] **Step 1: Vérifier les clés de thème supportées**

Run: `.venv/Scripts/python.exe -m streamlit config show | grep -i -E "theme\.(borderColor|baseRadius|buttonRadius|sidebar)"`
Expected: les clés `borderColor`, `baseRadius`, `buttonRadius` et la section `theme.sidebar` apparaissent. Si l'une manque, ne pas l'ajouter.

- [ ] **Step 2: Compléter `.streamlit/config.toml`**

```toml
[theme]
base = "light"
primaryColor = "#B5482A"
backgroundColor = "#F6F3EE"
secondaryBackgroundColor = "#FFFDF9"
textColor = "#1C1B19"
font = "sans serif"
borderColor = "#E4DED3"
baseRadius = "8px"
buttonRadius = "6px"

[theme.sidebar]
backgroundColor = "#F6F3EE"
secondaryBackgroundColor = "#EFEAE0"

[browser]
gatherUsageStats = false

[server]
headless = true
```

- [ ] **Step 3: Supprimer le plafond de largeur**

Dans `utils/styles.py`, bloc `[data-testid="stMainBlockContainer"]` de `_CSS_CHROME`, retirer la ligne `max-width: 1160px;` :

```css
[data-testid="stMainBlockContainer"] {
  padding-top: 2.4rem;
  padding-bottom: 5rem;
}
```

- [ ] **Step 4: Tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_app.py -q -p no:cacheprovider`
Expected: vert.

- [ ] **Step 5: Vérification navigateur**

Recharger `http://localhost:8511/` à 1440 px : le contenu occupe toute la largeur, sidebar de la même teinte que l'ancienne, bordures cohérentes.

- [ ] **Step 6: Commit**

```bash
git add .streamlit/config.toml utils/styles.py
git commit -m "feat(ui): theme natif complete (bordures, rayons, sidebar) et contenu pleine largeur"
```

---

## Livraison 2 — Composant `job_feed` et page Flux

### Task 3: Sérialisation `job -> dict` (TDD)

**Files:**
- Create: `components/__init__.py` (vide), `components/job_feed/__init__.py` (vide pour l'instant)
- Create: `components/job_feed/serialize.py`
- Test: `tests/test_job_feed.py`

**Interfaces:**
- Produces:
  - `serialize_job(job: dict, keywords: Sequence[str], group: str = "") -> dict`
  - `serialize_feed(jobs: list[dict], keywords: Sequence[str], *, grouped: bool = False) -> list[dict]`
  - `DESCRIPTION_LIMIT: int = 4000`
  - Clés du dict : `id, title, company, location, date_label, source_label, source_color, score, score_origin, quality, align_label, align_tone, reranked, verdict, verdict_label, verdict_tone, status, status_label, is_new, hard_cap, badges[{label,tone}], sub_scores[{key,label,short,value,tone}], signals[{label,tone,evidence}], technologies[], strengths[], red_flags[], reasoning, description, rejection_reason, url, group`

- [ ] **Step 1: Écrire les tests qui échouent**

`tests/test_job_feed.py` :

```python
"""Tests du composant job_feed : sérialisation des offres et actions de statut."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from components.job_feed.serialize import DESCRIPTION_LIMIT, serialize_feed, serialize_job
from src.constants import (
    STATUS_APPLIED,
    STATUS_NEW,
    TIER_1,
    VERDICT_EXCELLENT,
)

_NOW = datetime.now(timezone.utc).replace(tzinfo=None)

JOB = {
    "id": "offre-1",
    "title": "STAGE - Ingénieur.e Recherche Machine Learning (F/H)",
    "company": "Dassault Systèmes",
    "location": "Valbonne, France",
    "url": "https://example.com/offre?a=1&b=2",
    "description": None,
    "source": "jobteaser",
    "company_tier": 2,
    "semantic_score": 52.26,
    "final_score": 46.36,
    "status": STATUS_NEW,
    "created_at": _NOW - timedelta(hours=5),
    "rerank_score": None,
    "verdict": None,
    "match_reasons": [],
    "red_flags": [],
    "tech_stack": [],
}

RERANKED = {
    **JOB,
    "id": "offre-2",
    "company": "Mistral AI",
    "company_tier": TIER_1,
    "rerank_score": 88.0,
    "quality_score": 71.0,
    "verdict": VERDICT_EXCELLENT,
    "match_reasons": ["Modélisation PyTorch avancée"],
    "red_flags": ["Périmètre susceptible d'évoluer"],
    "tech_stack": ["PyTorch", "GNN"],
    "description": "Mission de recherche.\n\n\nPyTorch et GNN.",
    "status": STATUS_APPLIED,
    "reasoning": "Calendrier aligné, mission de modélisation réelle.",
    "sub_scores": {"modeling_depth": 5, "mentorship_team": 4, "career_leverage": 4, "pfe_compatibility": 5},
    "signals": {
        "encadrant_explicite": {"present": True, "evidence": "encadré par un chercheur senior"},
        "donnees_benchmark_seulement": {"present": False},
    },
}


def test_serialize_offre_non_evaluee() -> None:
    data = serialize_job(JOB, ())
    assert data["id"] == "offre-1"
    assert data["reranked"] is False and data["verdict"] == "" and data["verdict_label"] == ""
    assert data["score"] == 46 and data["align_label"] == "Secondaire" and data["align_tone"] == "warn"
    assert data["score_origin"] == "hybride" and data["quality"] is None
    assert data["sub_scores"] == [] and data["signals"] == [] and data["strengths"] == []
    assert data["status"] == STATUS_NEW and data["is_new"] is True and data["status_label"] == "Nouveau"
    assert data["source_label"] == "JobTeaser" and data["source_color"].startswith("#")
    assert data["description"] == "" and data["hard_cap"] == "" and data["group"] == ""


def test_serialize_offre_evaluee_complete() -> None:
    data = serialize_job(RERANKED, ("PyTorch",))
    assert data["reranked"] is True and data["score"] == 88 and data["quality"] == 71.0
    assert data["align_label"] == "Cœur de cible" and data["align_tone"] == "positive"
    assert data["verdict"] == VERDICT_EXCELLENT and data["verdict_label"] == "Excellent"
    assert data["technologies"] == ["PyTorch", "GNN"]
    assert data["strengths"] == ["Modélisation PyTorch avancée"]
    assert data["red_flags"] == ["Périmètre susceptible d'évoluer"]
    assert data["reasoning"].startswith("Calendrier aligné")
    assert data["is_new"] is False and data["status_label"] == "Postulé"
    # Sous-scores : ordre stable, libellés, tonalité selon la valeur
    subs = {item["key"]: item for item in data["sub_scores"]}
    assert set(subs) == {"modeling_depth", "mentorship_team", "career_leverage", "pfe_compatibility"}
    assert subs["modeling_depth"]["value"] == 5 and subs["modeling_depth"]["tone"] == "positive"
    assert subs["mentorship_team"]["value"] == 4 and subs["mentorship_team"]["tone"] == "accent"
    assert subs["modeling_depth"]["label"] == "Modélisation" and subs["modeling_depth"]["short"]
    # Seuls les signaux présents sont exposés, avec leur citation
    assert [s["evidence"] for s in data["signals"]] == ["encadré par un chercheur senior"]
    assert data["signals"][0]["tone"] == "positive"
    # Les sauts de ligne multiples de la fiche sont compactés
    assert data["description"] == "Mission de recherche.\nPyTorch et GNN."


def test_serialize_verrou_bloquant_et_plafond() -> None:
    capped = {**RERANKED, "hard_cap_triggered": " Reporting / dashboards BI "}
    data = serialize_job(capped, ())
    assert data["hard_cap"] == "Reporting / dashboards BI"
    assert any(b["tone"] == "alert" for b in data["badges"])
    assert serialize_job(RERANKED, ())["hard_cap"] == ""


def test_serialize_badges_structure_flags_et_exclusion() -> None:
    job = {
        **JOB,
        "structure_type": "SCALEUP_IA",
        "floor_reason": "Plancher scale-up 70",
        "scaleup_suggested": True,
        "flags": ["esn"],
        "status": "EXCLU",
        "exclusion_reason": "hors sujet",
    }
    labels = [b["label"] for b in serialize_job(job, ())["badges"]]
    assert "Scale-up IA" in labels and "Plancher scale-up 70" in labels
    assert "scale-up suggérée (à confirmer)" in labels
    assert "EXCLU" in labels


def test_serialize_url_non_http_neutralisee() -> None:
    for bad in ("javascript:alert(1)", "data:text/html,<script>1</script>", "//evil.example", "", None):
        assert serialize_job({**JOB, "url": bad}, ())["url"] == "", bad
    assert serialize_job({**JOB, "url": "http://a.example/x"}, ())["url"] == "http://a.example/x"


def test_serialize_ne_echappe_pas_le_html() -> None:
    hostile = '<img src=x onerror=alert(1)> & "guillemets"'
    data = serialize_job({**JOB, "title": hostile, "company": hostile, "description": hostile}, ())
    # Le composant affiche via textContent : la donnée brute doit rester intacte (pas de double échappement).
    assert data["title"] == hostile and data["company"] == hostile and data["description"] == hostile


def test_serialize_description_tronquee_et_json_serialisable() -> None:
    data = serialize_job({**RERANKED, "description": "x" * (DESCRIPTION_LIMIT * 3)}, ())
    assert len(data["description"]) == DESCRIPTION_LIMIT
    json.dumps(data)  # aucune valeur datetime / non sérialisable


def test_serialize_donnees_degradees() -> None:
    weird = {
        "id": 42,  # entier venu de la base
        "title": None,
        "company": None,
        "url": None,
        "status": None,
        "created_at": "pas-une-date",
        "rerank_score": 60,
        "sub_scores": {"modeling_depth": "4/5", "mentorship_team": None, "inconnu": 9},
        "signals": "pas un dict",
        "flags": None,
        "tech_stack": None,
        "match_reasons": None,
    }
    data = serialize_job(weird, ())
    assert data["id"] == "42" and data["title"] == "Offre sans titre" and data["company"] == "Entreprise inconnue"
    assert data["status"] == STATUS_NEW and data["is_new"] is True
    assert data["date_label"] == "Date inconnue" and data["url"] == ""
    values = {s["key"]: s["value"] for s in data["sub_scores"]}
    assert values["modeling_depth"] == 4 and values["mentorship_team"] == 3 and "inconnu" not in values
    assert data["signals"] == [] and data["technologies"] == [] and data["strengths"] == []
    json.dumps(data)


def test_serialize_feed_groupe_par_plateforme() -> None:
    jobs = [
        {**JOB, "id": "a", "source": "jobteaser"},
        {**JOB, "id": "b", "source": "linkedin"},
        {**JOB, "id": "c", "source": "jobteaser"},
    ]
    flat = serialize_feed(jobs, (), grouped=False)
    assert [j["id"] for j in flat] == ["a", "b", "c"] and {j["group"] for j in flat} == {""}
    grouped = serialize_feed(jobs, (), grouped=True)
    assert [(j["group"], j["id"]) for j in grouped] == [("LinkedIn", "b"), ("JobTeaser", "a"), ("JobTeaser", "c")]
    assert serialize_feed([], (), grouped=True) == []


def test_serialize_feed_volume_borne() -> None:
    jobs = [{**RERANKED, "id": f"j{i}", "description": "mot " * 5000} for i in range(1000)]
    payload = json.dumps(serialize_feed(jobs, ()))
    assert len(payload.encode("utf-8")) < 8_000_000, "Le mode « Tout » doit rester sous 8 Mo de charge utile."
```

- [ ] **Step 2: Vérifier l'échec**

Run: `.venv/Scripts/python.exe -m pytest tests/test_job_feed.py -q -p no:cacheprovider`
Expected: ERROR à la collecte (`ModuleNotFoundError: components`).

- [ ] **Step 3: Créer les paquets et l'implémentation**

`components/__init__.py` et `components/job_feed/__init__.py` : fichiers vides.

`components/job_feed/serialize.py` :

```python
"""Sérialisation des offres pour le composant ``job_feed`` (fonctions pures).

Le composant affiche les valeurs via ``textContent`` : les chaînes sont donc
transmises brutes (jamais échappées en HTML).
"""
from __future__ import annotations

import re
from typing import Any, Sequence

from src.constants import (
    FLAG_LABELS,
    FLAG_TONES,
    HARD_CAP_LABELS,
    STATUS_EXCLUDED,
    STATUS_NEW,
    STRUCTURE_TYPE_LABELS,
    STRUCTURE_TYPE_TONES,
    SUB_SCORE_KEYS,
    SUB_SCORE_LABELS,
    SUB_SCORE_SHORT_LABELS,
    TIER_1,
    TIER_ESN,
    TIER_LABELS,
    VERDICT_LABELS,
    coerce_sub_score,
    source_label,
)
from utils.data import (
    STATUS_LABELS,
    VERDICT_TONES,
    contract_label,
    detected_technologies,
    effective_score,
    group_jobs_by_source,
    is_reranked,
    relative_date,
    score_alignment,
    source_color,
)

DESCRIPTION_LIMIT = 4000

# (clé du signal, libellé, tonalité) — mêmes signaux que la grille v3.
SIGNAL_DEFS = (
    ("encadrant_explicite", "Encadrant explicite (+6)", "positive"),
    ("donnees_reelles_explicites", "Données réelles (+3)", "positive"),
    ("suite_explicite", "Débouché / Thèse (+3)", "positive"),
    ("donnees_benchmark_seulement", "Benchmark seul (-5)", "alert"),
)

_SUBSCORE_TONES = {5: "positive", 4: "accent", 3: "mute", 2: "warn"}


def _one_line(value: Any) -> str:
    """Compacte tous les espaces (titres, entreprises, lieux)."""
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _multiline(value: Any) -> str:
    """Compacte les espaces mais conserve les sauts de ligne (fiche de poste)."""
    text = re.sub(r"[\t\r ]+", " ", str(value or ""))
    return re.sub(r"\n{2,}", "\n", text).strip()


def _texts(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    return [str(item) for item in values if str(item).strip()]


def _http_url(value: Any) -> str:
    url = str(value or "").strip()
    return url if re.match(r"^https?://", url, re.IGNORECASE) else ""


def _badges(job: dict[str, Any]) -> list[dict[str, str]]:
    badges: list[dict[str, str]] = []
    contract = contract_label(job)
    if contract:
        badges.append({"label": contract, "tone": "mute"})

    structure = job.get("structure_type")
    if structure:
        badges.append(
            {
                "label": STRUCTURE_TYPE_LABELS.get(structure, str(structure)),
                "tone": STRUCTURE_TYPE_TONES.get(structure, "mute"),
            }
        )
    else:
        tier = job.get("company_tier")
        if tier in (TIER_1, TIER_ESN):
            badges.append(
                {"label": TIER_LABELS.get(tier, str(tier)), "tone": "positive" if tier == TIER_1 else "alert"}
            )

    if job.get("floor_reason"):
        badges.append({"label": str(job["floor_reason"]), "tone": "positive"})
    elif job.get("floor_value"):
        badges.append({"label": f"plancher {job['floor_value']}", "tone": "positive"})

    cap = job.get("cap_applied") or job.get("hard_cap_triggered")
    if cap:
        cap_key = str(cap).strip().upper()
        badges.append({"label": HARD_CAP_LABELS.get(cap_key, f"plafond {cap_key}"), "tone": "alert"})

    if job.get("scaleup_suggested"):
        badges.append({"label": "scale-up suggérée (à confirmer)", "tone": "warn"})

    for flag in job.get("flags") or []:
        key = str(flag).upper()
        badges.append({"label": FLAG_LABELS.get(key, key), "tone": FLAG_TONES.get(key, "warn")})

    if not is_reranked(job) and (job.get("status") == STATUS_EXCLUDED or job.get("exclusion_reason")):
        badges.append({"label": "EXCLU", "tone": "alert"})
    return badges


def _sub_scores(job: dict[str, Any]) -> list[dict[str, Any]]:
    if not is_reranked(job):
        return []
    raw = job.get("sub_scores")
    if not isinstance(raw, dict) or not raw:
        return []
    keys = [k for k in SUB_SCORE_KEYS if k in raw] + [
        k for k in raw if k not in SUB_SCORE_KEYS and k in SUB_SCORE_LABELS
    ]
    if not keys:
        keys = list(SUB_SCORE_KEYS)
    items = []
    for key in keys:
        value = coerce_sub_score(raw.get(key))
        items.append(
            {
                "key": key,
                "label": SUB_SCORE_LABELS.get(key, key),
                "short": SUB_SCORE_SHORT_LABELS.get(key, SUB_SCORE_LABELS.get(key, key)),
                "value": value,
                "tone": _SUBSCORE_TONES.get(value, "alert"),
            }
        )
    return items


def _signals(job: dict[str, Any]) -> list[dict[str, str]]:
    raw = job.get("signals")
    if not isinstance(raw, dict):
        return []
    items = []
    for key, label, tone in SIGNAL_DEFS:
        data = raw.get(key)
        if isinstance(data, dict) and data.get("present"):
            evidence = str(data.get("evidence") or data.get("citation") or "").strip()
            items.append({"label": label, "tone": tone, "evidence": evidence})
    return items


def serialize_job(job: dict[str, Any], keywords: Sequence[str], group: str = "") -> dict[str, Any]:
    """Convertit une offre de la base en dictionnaire JSON pour le composant."""
    reranked = is_reranked(job)
    score = effective_score(job)
    align_label, align_tone = score_alignment(score)
    status = job.get("status") or STATUS_NEW
    verdict = (job.get("verdict") or "") if reranked else ""
    quality = job.get("quality_score")
    return {
        "id": str(job.get("id")),
        "title": _one_line(job.get("title")) or "Offre sans titre",
        "company": _one_line(job.get("company")) or "Entreprise inconnue",
        "location": _one_line(job.get("location")),
        "date_label": relative_date(job.get("created_at")),
        "source_label": source_label(job.get("source")),
        "source_color": source_color(job.get("source")),
        "score": int(round(score)),
        "score_origin": "rerank" if reranked else "hybride",
        "quality": float(quality) if reranked and quality is not None else None,
        "align_label": align_label,
        "align_tone": align_tone,
        "reranked": reranked,
        "verdict": verdict,
        "verdict_label": VERDICT_LABELS.get(verdict, verdict) if verdict else "",
        "verdict_tone": VERDICT_TONES.get(verdict, "mute") if verdict else "mute",
        "status": status,
        "status_label": STATUS_LABELS.get(status, status),
        "is_new": status == STATUS_NEW,
        "hard_cap": (job.get("hard_cap_triggered") or "").strip(),
        "badges": _badges(job),
        "sub_scores": _sub_scores(job),
        "signals": _signals(job),
        "technologies": detected_technologies(job, keywords),
        "strengths": _texts(job.get("match_reasons")),
        "red_flags": _texts(job.get("red_flags")),
        "reasoning": _multiline(job.get("reasoning")) if reranked else "",
        "description": _multiline(job.get("description"))[:DESCRIPTION_LIMIT],
        "rejection_reason": (job.get("rejection_reason") or "").strip(),
        "url": _http_url(job.get("url")),
        "group": group,
    }


def serialize_feed(
    jobs: list[dict[str, Any]], keywords: Sequence[str], *, grouped: bool = False
) -> list[dict[str, Any]]:
    """Sérialise le flux ; en mode groupé, l'ordre suit les groupes de plateforme."""
    if grouped:
        return [
            serialize_job(job, keywords, group=label)
            for label, members in group_jobs_by_source(jobs)
            for job in members
        ]
    return [serialize_job(job, keywords) for job in jobs]
```

- [ ] **Step 4: Vérifier le succès**

Run: `.venv/Scripts/python.exe -m pytest tests/test_job_feed.py -q -p no:cacheprovider`
Expected: 10 passed. Si `test_serialize_donnees_degradees` échoue sur `status_label` ou `date_label`, corriger `serialize_job` (pas le test) : `relative_date("pas-une-date")` doit renvoyer « Date inconnue ».

- [ ] **Step 5: Commit**

```bash
git add components tests/test_job_feed.py
git commit -m "feat(ui): serialisation des offres pour le composant job_feed"
```

---

### Task 4: Validation et application des actions (TDD)

**Files:**
- Create: `components/job_feed/actions.py`
- Modify: `tests/test_job_feed.py` (ajout)

**Interfaces:**
- Consumes: `utils.data._set_status(db, job_id, status)`.
- Produces:
  - `ALLOWED_STATUSES: frozenset[str]`
  - `parse_action(raw: Any) -> dict | None` (retourne `{"type": "status", "id", "status"}` ou `{"type": "letter", "id"}`)
  - `apply_status_action(db, raw: Any, set_status=_set_status) -> str` -> `"applied"` | `"ignored"` | `"failed"`

- [ ] **Step 1: Ajouter les tests qui échouent** (fin de `tests/test_job_feed.py`)

```python
from components.job_feed.actions import ALLOWED_STATUSES, apply_status_action, parse_action
from src.constants import STATUS_IGNORED, STATUS_INTERVIEW, STATUS_REJECTED


def test_parse_action_valide() -> None:
    assert parse_action({"type": "status", "id": "a1", "status": STATUS_APPLIED}) == {
        "type": "status", "id": "a1", "status": STATUS_APPLIED,
    }
    assert parse_action({"type": "letter", "id": "a1", "extra": "ignoré"}) == {"type": "letter", "id": "a1"}
    assert ALLOWED_STATUSES == {STATUS_NEW, STATUS_APPLIED, STATUS_INTERVIEW, STATUS_IGNORED}


def test_parse_action_forgee_ignoree() -> None:
    forged = [
        None, "status", 42, [], {},
        {"type": "status", "id": "", "status": STATUS_APPLIED},
        {"type": "status", "id": None, "status": STATUS_APPLIED},
        {"type": "status", "id": 5, "status": STATUS_APPLIED},
        {"type": "status", "id": "a1", "status": "DROP TABLE jobs"},
        {"type": "status", "id": "a1", "status": STATUS_REJECTED},
        {"type": "status", "id": "a1"},
        {"type": "delete", "id": "a1"},
        {"type": "letter", "id": ""},
    ]
    for raw in forged:
        assert parse_action(raw) is None, raw


def test_apply_status_action() -> None:
    calls: list[tuple] = []

    def fake_set_status(db, job_id, status):
        calls.append((db, job_id, status))

    db = object()
    assert apply_status_action(db, {"type": "status", "id": "a1", "status": STATUS_APPLIED}, fake_set_status) == "applied"
    assert calls == [(db, "a1", STATUS_APPLIED)]

    # Lettre, action forgée ou absente : aucune écriture.
    assert apply_status_action(db, {"type": "letter", "id": "a1"}, fake_set_status) == "ignored"
    assert apply_status_action(db, {"type": "status", "id": "a1", "status": "X"}, fake_set_status) == "ignored"
    assert apply_status_action(db, None, fake_set_status) == "ignored"
    assert len(calls) == 1

    def boom(db, job_id, status):
        raise RuntimeError("base verrouillée")

    assert apply_status_action(db, {"type": "status", "id": "a1", "status": STATUS_NEW}, boom) == "failed"
```

- [ ] **Step 2: Vérifier l'échec**

Run: `.venv/Scripts/python.exe -m pytest tests/test_job_feed.py -q -p no:cacheprovider`
Expected: ERROR d'import (`components.job_feed.actions`).

- [ ] **Step 3: Implémenter**

`components/job_feed/actions.py` :

```python
"""Validation et application des actions émises par le composant ``job_feed``."""
from __future__ import annotations

import logging
from typing import Any, Callable

from src.constants import STATUS_APPLIED, STATUS_IGNORED, STATUS_INTERVIEW, STATUS_NEW
from utils.data import _set_status

logger = logging.getLogger(__name__)

ALLOWED_STATUSES = frozenset({STATUS_NEW, STATUS_APPLIED, STATUS_INTERVIEW, STATUS_IGNORED})


def parse_action(raw: Any) -> dict[str, str] | None:
    """Valide la charge d'un trigger ; ``None`` si elle est absente ou forgée."""
    if not isinstance(raw, dict):
        return None
    job_id = raw.get("id")
    if not isinstance(job_id, str) or not job_id:
        return None
    kind = raw.get("type")
    if kind == "letter":
        return {"type": "letter", "id": job_id}
    status = raw.get("status")
    if kind == "status" and isinstance(status, str) and status in ALLOWED_STATUSES:
        return {"type": "status", "id": job_id, "status": status}
    return None


def apply_status_action(
    db: Any, raw: Any, set_status: Callable[[Any, str, str], None] | None = None
) -> str:
    """Persiste un changement de statut valide.

    Retourne ``"applied"`` (écrit), ``"ignored"`` (rien à faire ou charge invalide)
    ou ``"failed"`` (l'écriture a levé une exception).
    ``set_status`` est résolu à l'appel (et non à la définition) pour rester substituable.
    """
    set_status = set_status or _set_status
    action = parse_action(raw)
    if action is None or action["type"] != "status":
        return "ignored"
    try:
        set_status(db, action["id"], action["status"])
    except Exception:  # noqa: BLE001 - l'UI signale l'échec, jamais de crash de rerun
        logger.exception("Échec du changement de statut %s -> %s", action["id"], action["status"])
        return "failed"
    return "applied"
```

- [ ] **Step 4: Vérifier le succès**

Run: `.venv/Scripts/python.exe -m pytest tests/test_job_feed.py -q -p no:cacheprovider`
Expected: 13 passed.

- [ ] **Step 5: Commit**

```bash
git add components/job_feed/actions.py tests/test_job_feed.py
git commit -m "feat(ui): validation des actions du composant job_feed"
```

---

### Task 5: Front du composant, wrapper CCv2 et page de démonstration

**Files:**
- Create: `components/job_feed/feed.html`, `feed.css`, `feed.js`
- Modify: `components/job_feed/__init__.py`
- Create: `tools/feed_demo.py`
- Modify: `.claude/launch.json` (ajout d'une entrée `feed-demo`, non committé)
- Test: `tests/test_job_feed_js.py`, `tests/test_job_feed.py` (test de montage)

**Interfaces:**
- Consumes: `serialize_feed`, `parse_action`, `apply_status_action` (Tasks 3-4), `utils.data.get_database`, `STATUS_LABELS`.
- Produces: `job_feed(jobs, keywords, *, hide_processed, grouped, key="job_feed") -> dict | None` (action `letter` ou `status` validée de la dernière interaction, sinon `None`). Le changement de statut est **déjà appliqué** (callback) quand la fonction retourne.
- Logique JS pure exposée pour les tests Node par ajout d'une ligne `export {…}` : `SEGMENTS, keyToCommand, excerpt, visibleJobs, pickAfterRemoval, moveSelection, actionsFor, safeUrl`.

- [ ] **Step 1: Test de la logique JS pure (échoue tant que `feed.js` n'existe pas)**

`tests/test_job_feed_js.py` :

```python
"""Tests de la logique JS pure de feed.js sous Node (ignorés si Node est absent)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FEED_JS = PROJECT_ROOT / "components" / "job_feed" / "feed.js"
NODE = shutil.which("node")
EXPORTS = (
    "\nexport { SEGMENTS, keyToCommand, excerpt, visibleJobs, pickAfterRemoval,"
    " moveSelection, actionsFor, safeUrl }\n"
)

pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js absent")


def _run(tmp_path: Path, body: str) -> subprocess.CompletedProcess[str]:
    (tmp_path / "feed_under_test.mjs").write_text(FEED_JS.read_text(encoding="utf-8") + EXPORTS, encoding="utf-8")
    check = tmp_path / "check.mjs"
    check.write_text(
        'import assert from "node:assert/strict"\n'
        'import * as feed from "./feed_under_test.mjs"\n' + body,
        encoding="utf-8",
    )
    return subprocess.run([NODE, str(check)], capture_output=True, text=True, timeout=30)


def _assert_ok(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 0, result.stdout + result.stderr


def test_syntaxe_et_chargement(tmp_path: Path) -> None:
    _assert_ok(_run(tmp_path, "assert.equal(typeof feed.keyToCommand, 'function')"))


def test_raccourcis_clavier(tmp_path: Path) -> None:
    _assert_ok(_run(tmp_path, """
assert.equal(feed.keyToCommand('j'), 'next')
assert.equal(feed.keyToCommand('ArrowDown'), 'next')
assert.equal(feed.keyToCommand('k'), 'prev')
assert.equal(feed.keyToCommand('ArrowUp'), 'prev')
assert.equal(feed.keyToCommand('p'), 'applied')
assert.equal(feed.keyToCommand('x'), 'archive')
assert.equal(feed.keyToCommand('?'), 'help')
assert.equal(feed.keyToCommand('z'), null)
assert.equal(feed.keyToCommand('constructor'), null)
assert.equal(feed.keyToCommand('toString'), null)
"""))


def test_segments_et_filtre_traitees(tmp_path: Path) -> None:
    _assert_ok(_run(tmp_path, """
const S = { new: 'NOUVEAU', applied: 'POSTULÉ', interview: 'ENTRETIEN', ignored: 'IGNORÉ' }
const jobs = [
  { id: 'a', align_tone: 'positive', reranked: true, status: 'NOUVEAU' },
  { id: 'b', align_tone: 'accent', reranked: true, status: 'NOUVEAU' },
  { id: 'c', align_tone: 'warn', reranked: false, status: 'POSTULÉ' },
]
const base = { segment: 'all', hideProcessed: false, overrides: {}, statuses: S }
const ids = (o) => feed.visibleJobs(jobs, { ...base, ...o }).map((j) => j.id)
assert.deepEqual(ids({}), ['a', 'b', 'c'])
assert.deepEqual(ids({ segment: 'core' }), ['a'])
assert.deepEqual(ids({ segment: 'good' }), ['b'])
assert.deepEqual(ids({ segment: 'unrated' }), ['c'])
assert.deepEqual(ids({ segment: 'inconnu' }), ['a', 'b', 'c'])
assert.deepEqual(ids({ hideProcessed: true }), ['a', 'b'])
// Un changement optimiste retire immédiatement l'offre quand les traitées sont masquées.
assert.deepEqual(ids({ hideProcessed: true, overrides: { a: 'POSTULÉ' } }), ['b'])
assert.deepEqual(ids({ hideProcessed: true, overrides: { c: 'NOUVEAU' } }), ['a', 'b', 'c'])
assert.deepEqual(feed.visibleJobs([], { ...base }), [])
"""))


def test_selection_apres_retrait_et_navigation(tmp_path: Path) -> None:
    _assert_ok(_run(tmp_path, """
assert.equal(feed.pickAfterRemoval([], 0), null)
assert.equal(feed.pickAfterRemoval(['a', 'b', 'c'], 1), 'b')
assert.equal(feed.pickAfterRemoval(['a', 'b'], 5), 'b')
assert.equal(feed.pickAfterRemoval(['a', 'b'], -1), 'a')
assert.equal(feed.moveSelection([], 'a', 1), null)
assert.equal(feed.moveSelection(['a', 'b', 'c'], 'a', 1), 'b')
assert.equal(feed.moveSelection(['a', 'b', 'c'], 'c', 1), 'c')
assert.equal(feed.moveSelection(['a', 'b', 'c'], 'a', -1), 'a')
assert.equal(feed.moveSelection(['a', 'b', 'c'], 'absent', 1), 'a')
"""))


def test_actions_par_statut(tmp_path: Path) -> None:
    _assert_ok(_run(tmp_path, """
const S = { new: 'NOUVEAU', applied: 'POSTULÉ', interview: 'ENTRETIEN', ignored: 'IGNORÉ' }
const labels = (status) => feed.actionsFor(status, S).map((a) => a.label)
assert.deepEqual(labels('NOUVEAU'), ['Marquer postulé', 'Archiver'])
assert.deepEqual(labels('POSTULÉ'), ['Entretien obtenu', 'Archiver'])
assert.deepEqual(labels('ENTRETIEN'), ['Rétablir au flux'])
assert.deepEqual(labels('IGNORÉ'), ['Rétablir au flux'])
// Statuts hors norme venus de la base : comportement du statut « nouveau ».
assert.deepEqual(labels('REJETÉ'), ['Marquer postulé', 'Archiver'])
assert.deepEqual(labels(undefined), ['Marquer postulé', 'Archiver'])
assert.equal(feed.actionsFor('POSTULÉ', S)[0].status, 'ENTRETIEN')
"""))


def test_extrait_et_url_sure(tmp_path: Path) -> None:
    _assert_ok(_run(tmp_path, """
assert.deepEqual(feed.excerpt('court', 420), { text: 'court', truncated: false })
const long = feed.excerpt('mot '.repeat(300), 50)
assert.equal(long.truncated, true)
assert.ok(long.text.endsWith(' …') && long.text.length <= 55, long.text)
assert.ok(!long.text.slice(0, -2).endsWith(' '), 'coupe sur un mot entier')
assert.equal(feed.safeUrl('https://a.example/x'), 'https://a.example/x')
assert.equal(feed.safeUrl('http://a.example'), 'http://a.example')
for (const bad of ['javascript:alert(1)', 'data:text/html,1', '//evil.example', '', null, undefined]) {
  assert.equal(feed.safeUrl(bad), '', String(bad))
}
"""))
```

- [ ] **Step 2: Vérifier l'échec**

Run: `.venv/Scripts/python.exe -m pytest tests/test_job_feed_js.py -q -p no:cacheprovider`
Expected: FAIL (fichier `feed.js` introuvable).

- [ ] **Step 3: Écrire `components/job_feed/feed.html`**

```html
<div class="root" tabindex="0" role="application" aria-label="Flux d'offres de stage">
  <div class="layout">
    <section class="list-pane" aria-label="Liste des offres">
      <header class="list-head">
        <div class="count" aria-live="polite"></div>
        <div class="segments" role="tablist" aria-label="Filtrer par alignement"></div>
      </header>
      <ul class="list" role="listbox" aria-label="Offres"></ul>
    </section>
    <section class="detail-pane" aria-label="Détail de l'offre" aria-live="polite"></section>
  </div>
  <div class="toast" role="status" hidden></div>
  <div class="help" hidden>
    <b>Raccourcis clavier</b>
    <div><kbd>j</kbd> / <kbd>k</kbd> ou <kbd>↓</kbd> / <kbd>↑</kbd> naviguer</div>
    <div><kbd>o</kbd> ouvrir l'offre · <kbd>l</kbd> lettre de motivation</div>
    <div><kbd>p</kbd> marquer postulé · <kbd>x</kbd> archiver</div>
    <div><kbd>?</kbd> afficher / masquer cette aide · <kbd>Échap</kbd> fermer</div>
  </div>
</div>
```

- [ ] **Step 4: Écrire `components/job_feed/feed.css`**

```css
:host { display: block; }

.root {
  --fg-muted: color-mix(in srgb, var(--st-text-color) 62%, transparent);
  --line: var(--st-border-color, rgba(128, 128, 128, 0.3));
  --panel: var(--st-secondary-background-color, transparent);
  --accent: var(--st-primary-color, #b5482a);
  --radius: var(--st-base-radius, 8px);
  position: relative;
  font-family: var(--st-font, system-ui, sans-serif);
  font-size: 14px;
  color: var(--st-text-color);
  outline: none;
}
.root:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: var(--radius); }

/* Tonalités */
.tone-positive { --tone: #2f855a; }
.tone-accent { --tone: var(--accent); }
.tone-warn { --tone: #b7791f; }
.tone-alert { --tone: #c0392b; }
.tone-mute { --tone: color-mix(in srgb, var(--st-text-color) 60%, transparent); }
.tone-positive, .tone-accent, .tone-warn, .tone-alert, .tone-mute {
  --tone-bg: color-mix(in srgb, var(--tone) 14%, transparent);
  --tone-bd: color-mix(in srgb, var(--tone) 35%, transparent);
}

/* Mise en page */
.layout {
  display: grid;
  grid-template-columns: minmax(300px, 38%) minmax(0, 1fr);
  gap: 16px;
  height: min(78vh, 900px);
  min-height: 480px;
}
.list-pane, .detail-pane {
  min-height: 0;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--panel);
}
.list-pane { display: flex; flex-direction: column; overflow: hidden; }
.detail-pane { overflow-y: auto; padding: 20px 24px; }

/* Liste */
.list-head { display: grid; gap: 8px; padding: 10px 12px; border-bottom: 1px solid var(--line); }
.count { font-size: 13px; color: var(--fg-muted); }
.segments { display: flex; flex-wrap: wrap; gap: 6px; }
.seg {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 3px 10px; border-radius: 999px;
  border: 1px solid var(--line); background: transparent; color: inherit;
  font: inherit; font-size: 12.5px; cursor: pointer;
}
.seg:hover { background: color-mix(in srgb, var(--st-text-color) 6%, transparent); }
.seg.active { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 12%, transparent); font-weight: 600; }
.seg-n { color: var(--fg-muted); font-size: 11.5px; }

.list { flex: 1; min-height: 0; margin: 0; padding: 0; overflow-y: auto; list-style: none; }
.row {
  display: grid; grid-template-columns: 44px minmax(0, 1fr) auto; gap: 12px; align-items: center;
  min-height: 64px; box-sizing: border-box; padding: 10px 12px;
  border-bottom: 1px solid var(--line); border-left: 3px solid transparent; cursor: pointer;
}
.row:hover { background: color-mix(in srgb, var(--st-text-color) 5%, transparent); }
.row.selected { border-left-color: var(--accent); background: color-mix(in srgb, var(--accent) 9%, transparent); }
.score {
  display: grid; place-items: center; width: 44px; height: 44px; border-radius: 10px;
  font-weight: 700; font-size: 16px; color: var(--tone); background: var(--tone-bg); border: 1px solid var(--tone-bd);
}
.row-main { min-width: 0; }
.row-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 600; }
.row-meta { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12.5px; color: var(--fg-muted); }
.row-flags { display: flex; align-items: center; gap: 6px; }
.dot-new { width: 8px; height: 8px; border-radius: 50%; background: var(--accent); }
.group {
  position: sticky; top: 0; z-index: 1; padding: 8px 12px;
  font-size: 11.5px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: var(--fg-muted);
  background: color-mix(in srgb, var(--st-text-color) 5%, var(--st-secondary-background-color, transparent));
  border-bottom: 1px solid var(--line);
}
.empty { padding: 24px 16px; color: var(--fg-muted); text-align: center; list-style: none; }

/* Chips */
.chip {
  display: inline-flex; align-items: center; gap: 6px; padding: 1px 8px; border-radius: 999px;
  border: 1px solid var(--tone-bd, var(--line)); background: var(--tone-bg, transparent);
  color: var(--tone, inherit); font-size: 12px; line-height: 20px; white-space: nowrap;
}
.chip.plain { color: inherit; }
.chip i { width: 7px; height: 7px; border-radius: 50%; display: inline-block; }
.tech { font-family: var(--st-code-font, ui-monospace, monospace); font-size: 12px; color: inherit; border-radius: 6px; }

/* Détail */
.detail-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.detail-titles { min-width: 0; }
.detail-title { margin: 0 0 4px; font-size: 22px; line-height: 1.25; font-weight: 700; overflow-wrap: anywhere; }
.detail-meta { color: var(--fg-muted); font-size: 13.5px; }
.back { display: none; margin-bottom: 12px; }
.score-box { flex: none; text-align: right; color: var(--tone); }
.score-big b { font-size: 30px; } .score-big span { font-size: 13px; color: var(--fg-muted); margin-left: 2px; }
.score-align { font-size: 11.5px; letter-spacing: 0.06em; text-transform: uppercase; }
.score-quality { font-size: 12px; color: var(--fg-muted); }
.chips { display: flex; flex-wrap: wrap; gap: 6px; margin: 12px 0; }

.actions { display: flex; flex-wrap: wrap; gap: 8px; margin: 14px 0 6px; padding-bottom: 14px; border-bottom: 1px solid var(--line); }
.btn {
  display: inline-flex; align-items: center; justify-content: center; box-sizing: border-box;
  height: 36px; padding: 0 14px; border-radius: var(--st-button-radius, 6px);
  border: 1px solid var(--line); background: transparent; color: inherit;
  font: inherit; font-size: 13px; font-weight: 550; text-decoration: none; cursor: pointer; white-space: nowrap;
}
.btn:hover { background: color-mix(in srgb, var(--st-text-color) 7%, transparent); }
.btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
.btn.primary:hover { background: color-mix(in srgb, var(--accent) 88%, #000); }
.btn:focus-visible, .seg:focus-visible, .row:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

.section { margin: 18px 0 0; }
.section h3 { margin: 0 0 8px; font-size: 12px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: var(--fg-muted); }
.alert {
  margin-top: 14px; padding: 10px 12px; border-radius: var(--radius);
  border: 1px solid var(--tone-bd); background: var(--tone-bg); color: var(--tone);
}
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 8px 20px; }
.sub { display: grid; grid-template-columns: 1fr auto auto; gap: 10px; align-items: center; }
.bar { display: inline-flex; gap: 3px; }
.bar i { width: 18px; height: 8px; border-radius: 3px; background: color-mix(in srgb, var(--st-text-color) 15%, transparent); }
.bar i.on { background: var(--tone); }
.sub-value { min-width: 28px; text-align: right; font-weight: 600; color: var(--tone); }
.list-plain { margin: 0; padding-left: 18px; }
.list-plain li { margin: 3px 0; }
.list-plain.pos li::marker { color: #2f855a; content: "+  "; }
.list-plain.neg li::marker { color: #c0392b; content: "−  "; }
.muted { color: var(--fg-muted); }
.pre { margin: 0; white-space: pre-line; overflow-wrap: anywhere; line-height: 1.55; }
details summary { cursor: pointer; color: var(--fg-muted); }
details[open] summary { margin-bottom: 8px; }
.signal { margin: 4px 0; }
.signal em { color: var(--fg-muted); }

/* Snackbar et aide */
.toast {
  position: absolute; left: 50%; bottom: 12px; z-index: 5; transform: translateX(-50%);
  display: flex; align-items: center; gap: 14px; padding: 8px 8px 8px 16px; border-radius: 999px;
  background: var(--st-text-color); color: var(--st-background-color, #fff); box-shadow: 0 6px 20px rgba(0, 0, 0, 0.25);
}
.toast[hidden], .help[hidden] { display: none; }
.toast-undo {
  height: 30px; padding: 0 14px; border-radius: 999px; border: 0; cursor: pointer;
  background: var(--accent); color: #fff; font: inherit; font-weight: 600;
}
.help {
  position: absolute; right: 12px; top: 12px; z-index: 5; padding: 12px 16px; border-radius: var(--radius);
  border: 1px solid var(--line); background: var(--st-background-color, #fff); box-shadow: 0 6px 20px rgba(0, 0, 0, 0.18);
  line-height: 1.8; font-size: 13px;
}
kbd { padding: 1px 6px; border: 1px solid var(--line); border-bottom-width: 2px; border-radius: 4px; font-family: inherit; font-size: 12px; }

@media (max-width: 900px) {
  .layout { grid-template-columns: 1fr; height: auto; min-height: 0; }
  .list-pane { height: 75vh; }
  .detail-pane { min-height: 60vh; }
  .layout:not(.show-detail) .detail-pane { display: none; }
  .layout.show-detail .list-pane { display: none; }
  .back { display: inline-flex; }
}
@media (prefers-reduced-motion: reduce) { * { transition: none !important; animation: none !important; } }
```

- [ ] **Step 5: Écrire `components/job_feed/feed.js`**

```js
// Composant « job_feed » : liste dense + panneau de détail (CCv2, JS vanilla, sans build).
// Toute donnée d'offre est rendue via createElement / textContent : jamais d'innerHTML.

const SEGMENTS = [
  { key: 'all', label: 'Tous', test: () => true },
  { key: 'core', label: 'Cœur de cible', test: (j) => j.align_tone === 'positive' },
  { key: 'good', label: 'Pertinent', test: (j) => j.align_tone === 'accent' },
  { key: 'unrated', label: 'Non évalué', test: (j) => !j.reranked },
]

const KEY_COMMANDS = {
  j: 'next', ArrowDown: 'next', k: 'prev', ArrowUp: 'prev',
  o: 'open', l: 'letter', p: 'applied', x: 'archive', '?': 'help', Escape: 'close',
}

const EXCERPT_LENGTH = 420
const TOAST_MS = 6000
const STORAGE_KEY = 'job_feed:selected'

// ---------- Logique pure (testée sous Node) ----------
function keyToCommand(key) {
  return Object.hasOwn(KEY_COMMANDS, key) ? KEY_COMMANDS[key] : null
}

function excerpt(text, length = EXCERPT_LENGTH) {
  if (text.length <= length) return { text, truncated: false }
  let cut = text.slice(0, length)
  const space = cut.lastIndexOf(' ')
  if (space > 0) cut = cut.slice(0, space)
  return { text: cut.replace(/[ ,;:.]+$/, '') + ' …', truncated: true }
}

function safeUrl(url) {
  return /^https?:\/\//i.test(url || '') ? url : ''
}

function effectiveStatus(job, overrides) {
  return overrides[job.id] ?? job.status
}

function visibleJobs(jobs, options) {
  const { segment, hideProcessed, overrides, statuses } = options
  const seg = SEGMENTS.find((s) => s.key === segment) || SEGMENTS[0]
  return jobs.filter(
    (j) => seg.test(j) && (!hideProcessed || effectiveStatus(j, overrides) === statuses.new),
  )
}

function pickAfterRemoval(ids, removedIndex) {
  if (!ids.length) return null
  return ids[Math.min(Math.max(removedIndex, 0), ids.length - 1)]
}

function moveSelection(ids, currentId, delta) {
  if (!ids.length) return null
  const index = ids.indexOf(currentId)
  if (index === -1) return ids[0]
  return ids[Math.min(Math.max(index + delta, 0), ids.length - 1)]
}

function actionsFor(status, statuses) {
  if (status === statuses.applied) {
    return [
      { label: 'Entretien obtenu', status: statuses.interview },
      { label: 'Archiver', status: statuses.ignored },
    ]
  }
  if (status === statuses.interview || status === statuses.ignored) {
    return [{ label: 'Rétablir au flux', status: statuses.new }]
  }
  return [
    { label: 'Marquer postulé', status: statuses.applied },
    { label: 'Archiver', status: statuses.ignored },
  ]
}

// ---------- Rendu DOM ----------
function h(tag, props, ...kids) {
  const el = document.createElement(tag)
  for (const [name, value] of Object.entries(props || {})) {
    if (value == null || value === false) continue
    if (name === 'class') el.className = value
    else if (name.startsWith('on')) el.addEventListener(name.slice(2), value)
    else el.setAttribute(name, value === true ? '' : String(value))
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)))
  }
  return el
}

const chip = (label, tone, extra) => h('span', { class: `chip ${tone ? 'tone-' + tone : 'plain'} ${extra || ''}`.trim() }, label)

function sourceChip(job) {
  const dot = h('i')
  if (/^#[0-9a-f]{3,8}$/i.test(job.source_color || '')) dot.style.background = job.source_color
  return h('span', { class: 'chip plain' }, dot, job.source_label)
}

function shorten(text, max = 48) {
  return text.length > max ? text.slice(0, max - 1) + '…' : text
}

function section(title, ...content) {
  return h('div', { class: 'section' }, h('h3', {}, title), ...content)
}

function bulletList(items, kind) {
  return h('ul', { class: `list-plain ${kind}` }, items.map((item) => h('li', {}, item)))
}

function createInstance(root) {
  const $ = (selector) => root.querySelector(selector)
  const layoutEl = $('.layout')
  const listEl = $('.list')
  const countEl = $('.count')
  const segmentsEl = $('.segments')
  const detailEl = $('.detail-pane')
  const toastEl = $('.toast')
  const helpEl = $('.help')

  const inst = {
    data: { jobs: [], statuses: {}, status_labels: {}, hide_processed: false, grouped: false },
    selected: null,
    segment: 'all',
    overrides: {},
    mobileDetail: false,
    toastTimer: null,
    lastDetailId: null,
    setTrigger: () => {},
  }
  try { inst.selected = sessionStorage.getItem(STORAGE_KEY) } catch (_) { /* stockage indisponible */ }

  const options = () => ({
    segment: inst.segment,
    hideProcessed: inst.data.hide_processed,
    overrides: inst.overrides,
    statuses: inst.data.statuses,
  })
  const visible = () => visibleJobs(inst.data.jobs, options())

  function render() {
    const list = visible()
    if (!list.some((j) => j.id === inst.selected)) inst.selected = list.length ? list[0].id : null
    if (inst.selected === null) inst.mobileDetail = false
    renderSegments()
    const rows = renderList(list)
    renderDetail(list)
    layoutEl.classList.toggle('show-detail', inst.mobileDetail)
    return rows
  }

  function renderSegments() {
    const base = visibleJobs(inst.data.jobs, { ...options(), segment: 'all' })
    countEl.textContent = `${base.length} offre${base.length > 1 ? 's' : ''}`
    segmentsEl.replaceChildren(
      ...SEGMENTS.map((seg) =>
        h(
          'button',
          {
            type: 'button',
            role: 'tab',
            class: 'seg' + (seg.key === inst.segment ? ' active' : ''),
            'aria-selected': String(seg.key === inst.segment),
            onclick: () => { inst.segment = seg.key; render() },
          },
          seg.label,
          h('span', { class: 'seg-n' }, String(base.filter(seg.test).length)),
        ),
      ),
    )
  }

  function renderList(list) {
    const top = listEl.scrollTop
    if (!list.length) {
      listEl.replaceChildren(h('li', { class: 'empty', role: 'presentation' }, 'Aucune offre ne correspond aux filtres courants.'))
      return new Map()
    }
    const rows = new Map()
    const nodes = []
    let lastGroup = null
    for (const job of list) {
      if (inst.data.grouped && job.group !== lastGroup) {
        lastGroup = job.group
        nodes.push(h('li', { class: 'group', role: 'presentation' }, job.group))
      }
      const status = effectiveStatus(job, inst.overrides)
      const selected = job.id === inst.selected
      const row = h(
        'li',
        {
          class: 'row' + (selected ? ' selected' : ''),
          role: 'option',
          'aria-selected': String(selected),
          onclick: () => select(job.id, true),
        },
        h('div', { class: `score tone-${job.align_tone}`, title: `${job.align_label} (${job.score_origin})` }, String(job.score)),
        h(
          'div',
          { class: 'row-main' },
          h('div', { class: 'row-title', title: job.title }, job.title),
          h('div', { class: 'row-meta' }, [job.company, job.location, job.date_label].filter(Boolean).join(' · ')),
        ),
        h(
          'div',
          { class: 'row-flags' },
          job.hard_cap ? chip('Plafonné', 'alert') : null,
          status === inst.data.statuses.new ? h('i', { class: 'dot-new', title: 'Nouveau' }) : null,
        ),
      )
      rows.set(job.id, row)
      nodes.push(row)
    }
    listEl.replaceChildren(...nodes)
    listEl.scrollTop = top
    return rows
  }

  function renderDetail(list) {
    const base = list.find((j) => j.id === inst.selected)
    if (!base) {
      detailEl.replaceChildren(h('div', { class: 'empty' }, 'Sélectionnez une offre pour afficher son évaluation.'))
      return
    }
    const S = inst.data.statuses
    const status = effectiveStatus(base, inst.overrides)
    const job = { ...base, status, status_label: inst.data.status_labels[status] || status }
    const url = safeUrl(job.url)
    const sameJob = inst.lastDetailId === job.id
    const scroll = detailEl.scrollTop

    const badges = [sourceChip(job), ...job.badges.map((b) => chip(b.label, b.tone))]
    if (job.verdict_label) badges.push(chip(job.verdict_label, job.verdict_tone))

    const parts = [
      h('button', { type: 'button', class: 'btn back', onclick: () => { inst.mobileDetail = false; render() } }, '← Retour à la liste'),
      h(
        'header',
        { class: 'detail-head' },
        h(
          'div',
          { class: 'detail-titles' },
          h('h2', { class: 'detail-title' }, job.title),
          h('div', { class: 'detail-meta' }, [job.company, job.location, job.date_label, job.status_label].filter(Boolean).join(' · ')),
        ),
        h(
          'div',
          { class: `score-box tone-${job.align_tone}`, title: `Score R&D (${job.score_origin})` },
          h('div', { class: 'score-big' }, h('b', {}, String(job.score)), h('span', {}, '/100')),
          h('div', { class: 'score-align' }, job.align_label),
          job.quality != null ? h('div', { class: 'score-quality' }, `qualité : ${Math.round(job.quality)}`) : null,
        ),
      ),
      h('div', { class: 'chips' }, badges),
      h(
        'div',
        { class: 'actions' },
        url ? h('a', { class: 'btn primary', href: url, target: '_blank', rel: 'noopener noreferrer' }, 'Postuler ↗') : null,
        h('button', { type: 'button', class: 'btn', onclick: () => emitLetter(job) }, 'Lettre'),
        actionsFor(status, S).map((a) => h('button', { type: 'button', class: 'btn', onclick: () => changeStatus(job, a.status) }, a.label)),
      ),
    ]

    if (job.hard_cap) {
      parts.push(
        h('div', { class: 'alert tone-alert' }, h('b', {}, 'Verrou bloquant'), ` — ${job.hard_cap} : score plafonné, candidature à écarter ou à vérifier avant tout effort.`),
      )
    }
    if (job.rejection_reason) parts.push(section('Écartée par le filtre métier', h('p', { class: 'pre' }, job.rejection_reason)))

    if (job.sub_scores.length) {
      parts.push(
        section(
          "Grille d'évaluation",
          h(
            'div',
            { class: 'grid' },
            job.sub_scores.map((s) =>
              h(
                'div',
                { class: `sub tone-${s.tone}`, title: s.label },
                h('span', {}, s.short),
                h('span', { class: 'bar' }, [1, 2, 3, 4, 5].map((n) => h('i', { class: n <= s.value ? 'on' : '' }))),
                h('span', { class: 'sub-value' }, `${s.value}/5`),
              ),
            ),
          ),
        ),
      )
    }
    if (job.signals.length) {
      parts.push(
        section(
          'Signaux qualitatifs vérifiés',
          job.signals.map((s) => h('div', { class: 'signal' }, chip(s.label, s.tone), s.evidence ? h('em', {}, ` « ${s.evidence} »`) : null)),
        ),
      )
    }

    if (job.reranked) {
      parts.push(
        section(
          'Verdict du juge',
          job.strengths.length ? bulletList(job.strengths, 'pos') : h('p', { class: 'muted' }, '—'),
          h('h3', { style: 'margin-top:12px' }, "Points d'attention"),
          job.red_flags.length ? bulletList(job.red_flags, 'neg') : h('p', { class: 'muted' }, 'Aucun point de vigilance signalé.'),
        ),
      )
      if (job.reasoning) {
        parts.push(h('div', { class: 'section' }, h('details', {}, h('summary', {}, 'Analyse du juge (raisonnement)'), h('p', { class: 'pre' }, job.reasoning))))
      }
    } else {
      parts.push(
        section('Verdict du juge', h('p', { class: 'muted' }, 'Offre non évaluée à ce stade : lancez `python run_pipeline.py` pour déclencher le reranking.')),
      )
    }

    if (job.technologies.length) parts.push(section('Technologies détectées', h('div', { class: 'chips' }, job.technologies.map((t) => chip(t, null, 'tech')))))

    if (job.description) {
      const short = excerpt(job.description)
      parts.push(
        section(
          'Fiche de poste',
          h('p', { class: 'pre' }, short.text),
          short.truncated ? h('details', {}, h('summary', {}, 'Lire la fiche complète'), h('p', { class: 'pre' }, job.description)) : null,
        ),
      )
    } else {
      parts.push(section('Fiche de poste', h('p', { class: 'muted' }, "Fiche non fournie par la plateforme d'origine.")))
    }
    parts.push(h('p', { class: 'muted', style: 'margin-top:20px;font-size:12px' }, 'Raccourcis : j / k naviguer · o ouvrir · l lettre · p postulé · x archiver · ? aide'))

    detailEl.replaceChildren(...parts)
    detailEl.scrollTop = sameJob ? scroll : 0
    inst.lastDetailId = job.id
  }

  function select(id, openDetail) {
    inst.selected = id
    try { sessionStorage.setItem(STORAGE_KEY, id) } catch (_) { /* stockage indisponible */ }
    if (openDetail) inst.mobileDetail = true
    const rows = render()
    const row = rows.get(id)
    if (row) row.scrollIntoView({ block: 'nearest' })
    root.focus({ preventScroll: true })
  }

  function emitLetter(job) {
    inst.setTrigger('action', { type: 'letter', id: job.id })
  }

  function changeStatus(job, status) {
    const previous = effectiveStatus(job, inst.overrides)
    if (previous === status) return
    const before = visible()
    const index = before.findIndex((j) => j.id === job.id)
    inst.overrides[job.id] = status
    const after = visible()
    if (!after.some((j) => j.id === job.id)) {
      inst.selected = pickAfterRemoval(after.map((j) => j.id), index)
      inst.mobileDetail = false
    }
    inst.setTrigger('action', { type: 'status', id: job.id, status })
    showToast(`${inst.data.status_labels[status] || status} : ${shorten(job.title)}`, () => undo(job.id, previous))
    render()
    root.focus({ preventScroll: true })
  }

  function undo(id, previous) {
    inst.overrides[id] = previous
    inst.selected = id
    inst.setTrigger('action', { type: 'status', id, status: previous })
    hideToast()
    render()
    root.focus({ preventScroll: true })
  }

  function showToast(message, onUndo) {
    clearTimeout(inst.toastTimer)
    toastEl.replaceChildren(h('span', {}, message), h('button', { type: 'button', class: 'toast-undo', onclick: onUndo }, 'Annuler'))
    toastEl.hidden = false
    inst.toastTimer = setTimeout(hideToast, TOAST_MS)
  }

  function hideToast() {
    toastEl.hidden = true
    toastEl.replaceChildren()
  }

  root.addEventListener('keydown', (event) => {
    if (event.ctrlKey || event.metaKey || event.altKey) return
    if (event.target && /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName || '')) return
    const command = keyToCommand(event.key)
    if (!command) return
    const list = visible()
    const current = list.find((j) => j.id === inst.selected)
    const S = inst.data.statuses
    switch (command) {
      case 'next':
      case 'prev':
        event.preventDefault()
        select(moveSelection(list.map((j) => j.id), inst.selected, command === 'next' ? 1 : -1), false)
        break
      case 'open':
        if (current && safeUrl(current.url)) window.open(safeUrl(current.url), '_blank', 'noopener,noreferrer')
        break
      case 'letter':
        if (current) emitLetter(current)
        break
      case 'applied':
        if (current) changeStatus(current, S.applied)
        break
      case 'archive':
        if (current) changeStatus(current, S.ignored)
        break
      case 'help':
        helpEl.hidden = !helpEl.hidden
        break
      case 'close':
        helpEl.hidden = true
        if (inst.mobileDetail) { inst.mobileDetail = false; render() }
        break
    }
  })

  inst.update = (data, setTrigger) => {
    inst.data = data
    inst.setTrigger = setTrigger
    inst.overrides = {} // la donnée serveur fait foi après chaque rerun
    render()
  }
  return inst
}

const instances = new WeakMap()

export default function (component) {
  const { parentElement, data, setTriggerValue } = component
  let inst = instances.get(parentElement)
  if (!inst) {
    const root = parentElement.querySelector('.root')
    if (!root) return
    inst = createInstance(root)
    instances.set(parentElement, inst)
  }
  inst.update(data, setTriggerValue)
}
```

- [ ] **Step 6: Lancer les tests JS**

Run: `.venv/Scripts/python.exe -m pytest tests/test_job_feed_js.py -q -p no:cacheprovider`
Expected: 6 passed. En cas d'échec d'une assertion, corriger `feed.js` (la logique pure) et non le test, sauf si le test contredit la spec.

- [ ] **Step 7: Écrire le wrapper CCv2**

`components/job_feed/__init__.py` :

```python
"""Composant Streamlit « job_feed » : flux d'offres (liste dense + détail), CCv2 inline."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import streamlit as st

from components.job_feed.actions import apply_status_action, parse_action
from components.job_feed.serialize import serialize_feed
from src.constants import STATUS_APPLIED, STATUS_IGNORED, STATUS_INTERVIEW, STATUS_NEW
from utils.data import STATUS_LABELS, get_database

_ASSETS = Path(__file__).parent


def _asset(name: str) -> str:
    return (_ASSETS / name).read_text(encoding="utf-8")


# Enregistré une seule fois à l'import (multi-lignes : traité comme contenu inline).
_FEED = st.components.v2.component(
    "job_feed",
    html=_asset("feed.html"),
    css=_asset("feed.css"),
    js=_asset("feed.js"),
)

_STATUS_CODES = {
    "new": STATUS_NEW,
    "applied": STATUS_APPLIED,
    "interview": STATUS_INTERVIEW,
    "ignored": STATUS_IGNORED,
}


def _on_action_change(key: str) -> None:
    """Callback exécuté avant le corps du script : les données rechargées sont déjà à jour."""
    state = st.session_state.get(key)
    raw = state.get("action") if state is not None else None
    if apply_status_action(get_database(), raw) == "failed":
        st.toast("Le statut n'a pas pu être enregistré.", icon=":material/error:")


def job_feed(
    jobs: list[dict[str, Any]],
    keywords: Sequence[str],
    *,
    hide_processed: bool,
    grouped: bool,
    key: str = "job_feed",
) -> dict[str, str] | None:
    """Affiche le flux et retourne l'action ``letter`` (ou ``status``) validée, sinon ``None``.

    Les changements de statut sont déjà persistés quand cette fonction retourne.
    """
    data = {
        "jobs": serialize_feed(jobs, keywords, grouped=grouped),
        "hide_processed": bool(hide_processed),
        "grouped": bool(grouped),
        "statuses": _STATUS_CODES,
        "status_labels": {code: STATUS_LABELS.get(code, code) for code in _STATUS_CODES.values()},
        "rev": int(st.session_state.get("data_version", 0)),
    }
    result = _FEED(key=key, data=data, on_action_change=lambda: _on_action_change(key))
    return parse_action(result.action)
```

- [ ] **Step 8: Test de montage (AppTest)**

Ajouter à `tests/test_job_feed.py` :

```python
def test_job_feed_se_monte_sans_exception(tmp_path: Path) -> None:
    from streamlit.testing.v1 import AppTest

    script = tmp_path / "mount_feed.py"
    script.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(PROJECT_ROOT)!r})\n"
        "import streamlit as st\n"
        "from components.job_feed import job_feed\n"
        "from tests.test_job_feed import JOB, RERANKED\n"
        "event = job_feed([JOB, RERANKED], (), hide_processed=False, grouped=True)\n"
        "st.write('event', event)\n",
        encoding="utf-8",
    )
    at = AppTest.from_file(str(script), default_timeout=30).run()
    assert not at.exception, at.exception
    assert any("None" in element.value for element in at.markdown)
```

Run: `.venv/Scripts/python.exe -m pytest tests/test_job_feed.py -q -p no:cacheprovider`
Expected: 14 passed. Si `from tests.test_job_feed import …` échoue (pas de paquet `tests`), remplacer par la définition inline de deux petits dicts dans le script généré.

- [ ] **Step 9: Page de démonstration avec offres factices (dont hostiles)**

`tools/feed_demo.py` :

```python
"""Démonstration isolée du composant job_feed avec des offres factices.

Usage : streamlit run tools/feed_demo.py --server.port 8512
Les statuts sont modifiés en mémoire uniquement (aucune base n'est touchée).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import components.job_feed as feed_module
from components.job_feed import job_feed

st.set_page_config(page_title="Démo job_feed", layout="wide")
st.header("Démo du composant job_feed", anchor=False)

NOW = datetime.now(timezone.utc).replace(tzinfo=None)


def _job(i: int, **extra):
    base = {
        "id": f"demo-{i}", "title": f"Stage R&D machine learning n°{i}", "company": f"Société {i}",
        "location": "Paris", "url": f"https://example.com/{i}", "source": "linkedin" if i % 2 else "jobteaser",
        "status": "NOUVEAU", "created_at": NOW - timedelta(hours=i), "final_score": 90 - i * 3,
        "rerank_score": 90 - i * 3 if i % 3 else None, "verdict": "EXCELLENT" if i % 3 else None,
        "match_reasons": ["Modélisation avancée"], "red_flags": [], "tech_stack": ["PyTorch", "Jax"],
        "description": "Mission de recherche appliquée. " * 40,
        "sub_scores": {"modeling_depth": 5, "mentorship_team": 4, "engineering_practice": 3, "option_value": 2},
        "reasoning": "Calendrier aligné et encadrement senior.",
    }
    return {**base, **extra}


HOSTILE = '<img src=x onerror="document.title=\'PWNED\'"> & "guillemets"'
JOBS = [
    _job(1), _job(2), _job(3),
    _job(4, title=HOSTILE, company=HOSTILE, description=HOSTILE, url="javascript:document.title='PWNED'"),
    _job(5, url=""),
    _job(6, id='id"avec]guillemets', hard_cap_triggered="Reporting / dashboards BI"),
    _job(7, status="REJETÉ"),
    _job(8, description=None, rerank_score=None, verdict=None, sub_scores=None),
]

# Les écritures de statut sont détournées vers la mémoire de session (aucune base).
def _memory_status(_db, job_id, status):
    for job in st.session_state["demo_jobs"]:
        if str(job["id"]) == job_id:
            job["status"] = status
    st.session_state["data_version"] = st.session_state.get("data_version", 0) + 1

import components.job_feed.actions as actions_module
actions_module._set_status = _memory_status
feed_module.get_database = lambda: None
st.session_state.setdefault("demo_jobs", JOBS)

hide = st.toggle("Masquer les offres traitées", value=True)
grouped = st.toggle("Grouper par plateforme", value=False)
event = job_feed(st.session_state["demo_jobs"], ("PyTorch",), hide_processed=hide, grouped=grouped)
if event:
    st.caption(f"Dernière action : {event}")
```

La démo substitue `components.job_feed.actions._set_status` (résolu à l'appel dans `apply_status_action`) et `components.job_feed.get_database`, si bien qu'aucune base n'est ouverte ni modifiée.

- [ ] **Step 10: Vérification navigateur (démo)**

Ajouter à `.claude/launch.json` (non committé) une entrée `feed-demo` : `runtimeExecutable ".venv/Scripts/python.exe"`, `runtimeArgs ["-m","streamlit","run","tools/feed_demo.py","--server.port","8512","--server.headless","true"]`, `port 8512`. Puis `preview_start` `feed-demo`, `resize_window` 1440x900 et vérifier, avec `computer`/`read_page`/`get_page_text` :
1. La liste affiche 7 lignes de 64 px (l'offre 7, au statut `REJETÉ`, est masquée par « Masquer les offres traitées » ; désactiver le toggle la fait apparaître avec les actions « Marquer postulé » / « Archiver »).
2. L'offre 4 (hostile) : son titre s'affiche **littéralement** (`<img src=x …>`), `document.title` ne devient jamais « PWNED », aucun bouton « Postuler » (URL `javascript:`), l'offre 5 n'a pas de « Postuler » non plus.
3. Clic sur une ligne puis `j`/`k` : sélection instantanée, la ligne suit (scroll `nearest`).
4. `p` sur l'offre 1 : la ligne disparaît, la suivante est sélectionnée, snackbar « Postulé : … » avec « Annuler » ; « Annuler » ramène l'offre 1 (statut Nouveau) sans erreur console (`read_console_messages`).
5. « Lettre » : la démo affiche `Dernière action : {'type': 'letter', …}`.
6. `?` ouvre/ferme l'aide. « Grouper par plateforme » insère les en-têtes LinkedIn / JobTeaser.
7. Segments : compteurs cohérents, « Cœur de cible » filtre sans rerun (aucune requête réseau ajoutée : `read_network_requests`).
8. Archiver toutes les offres visibles jusqu'à vider la liste : « Aucune offre ne correspond aux filtres courants. » sans erreur console.
9. `resize_window` 375x812 : une seule colonne, clic sur une ligne ouvre le détail, « ← Retour à la liste » revient. Puis `resize_window` preset `desktop`.

Si un point échoue, corriger `feed.js` / `feed.css` puis recharger ; ne pas passer à la suite avant que les 9 points passent.

- [ ] **Step 11: Suite de tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_job_feed.py tests/test_job_feed_js.py -q -p no:cacheprovider`
Expected: tout vert.

- [ ] **Step 12: Commit**

```bash
git add components tools/feed_demo.py tests/test_job_feed.py tests/test_job_feed_js.py
git commit -m "feat(ui): composant job_feed (liste dense, detail, raccourcis, annulation) et page de demo"
```

---

### Task 6: Page Flux — KPI natifs, intégration du composant, suppression de l'ancien rendu

**Files:**
- Create: `utils/layout.py`
- Modify: `app_pages/flux.py`, `utils/components.py`, `tests/test_app.py`
- Create: `tests/test_ui_conventions.py`

**Interfaces:**
- Consumes: `job_feed(...)` (Task 5), `show_cover_letter_dialog(job)` (existant).
- Produces:
  - `utils.layout.page_header(title: str, caption: str = "") -> None`
  - `utils.layout.kpi_row(items: Sequence[tuple[str, str, str | None]]) -> None` (label, valeur, aide en infobulle)
  - `utils.components.render_header(jobs, config)` et `render_kpis(jobs, llm_model, base_total, filters_active)` réécrits en natif (mêmes signatures).

- [ ] **Step 1: Écrire le test de conventions (échoue sur `utils/components.py`)**

`tests/test_ui_conventions.py` :

```python
"""Garde-fous de conventions UI : pas d'emoji ni de use_container_width dans les fichiers migrés."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-⛿✅❌✨⭐️]")

# Fichiers déjà migrés : chaque tâche de la refonte ajoute les siens.
CLEAN_FILES = [
    "app_pages/flux.py",
    "utils/components.py",
    "utils/layout.py",
    "components/job_feed/__init__.py",
    "components/job_feed/feed.js",
    "components/job_feed/feed.html",
    "components/job_feed/feed.css",
]


@pytest.mark.parametrize("relative", CLEAN_FILES)
def test_pas_d_emoji_ni_de_use_container_width(relative: str) -> None:
    text = (ROOT / relative).read_text(encoding="utf-8")
    found = sorted(set(EMOJI.findall(text)))
    assert not found, f"{relative} : emoji décoratifs interdits {found}"
    assert "use_container_width" not in text, f"{relative} : utiliser width=\"stretch\""
```

Run: `.venv/Scripts/python.exe -m pytest tests/test_ui_conventions.py -q -p no:cacheprovider`
Expected: échec sur `utils/components.py` (emojis) et `utils/layout.py` (inexistant).

- [ ] **Step 2: Créer `utils/layout.py`**

```python
"""Éléments de mise en page communs aux pages (en-tête, rangée de KPI)."""
from __future__ import annotations

from typing import Sequence

import streamlit as st


def page_header(title: str, caption: str = "") -> None:
    """En-tête de page : titre court en casse phrase + une ligne de contexte."""
    st.header(title, anchor=False)
    if caption:
        st.caption(caption)


def kpi_row(items: Sequence[tuple[str, str, str | None]]) -> None:
    """Rangée de KPI (grille fixe) : ``(libellé, valeur, aide en infobulle)``."""
    columns = st.columns(len(items), gap="small")
    for column, (label, value, help_text) in zip(columns, items):
        with column:
            st.metric(label, value, help=help_text, border=True)
```

- [ ] **Step 3: Réécrire l'en-tête et les KPI en natif dans `utils/components.py`**

Remplacer les fonctions `render_header` et `render_kpis` (et supprimer `_inline_relative` seulement s'il n'est plus utilisé : `grep -n _inline_relative -r .`) par :

```python
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
```

- [ ] **Step 4: Supprimer l'ancien rendu de cartes et nettoyer les emojis**

Dans `utils/components.py` :
1. Vérifier ce que la boîte de dialogue utilise : `sed -n '/^def show_cover_letter_dialog/,/^def render_job_card/p' utils/components.py | grep -n -o -E "\b(_badge|score_html|_meta_html|_badges_html|_signals_html|_chips_html|clean_text|excerpt|_verdict_block|_subscores_[a-z]+|_reasoning_block|_scores_block|_description_block|_rejection_block|_hard_cap_banner|job_card_html|SUB_SCORE_ICONS)\b" | sort -u`. Toute fonction listée reste ; les autres sont supprimées.
2. Supprimer les fonctions de rendu de carte non utilisées ailleurs : `score_html`, `_meta_html`, `_badges_html`, `_signals_html`, `_chips_html`, `_verdict_block`, `_subscore_tone`, `_hard_cap_banner`, `_subscores_strip`, `_subscores_block`, `_reasoning_block`, `_scores_block`, `_description_block`, `_rejection_block`, `job_card_html`, `SUB_SCORE_ICONS`, `render_job_card`, `render_compact_card_with_select`, `render_job_detail_pane`, `render_stream`. Avant chaque suppression : `grep -rn "<nom>" --include=*.py .` — ne supprimer que si aucun autre usage n'existe (le kanban importe `_badge` et `score_alignment` : les garder).
3. Dans `show_cover_letter_dialog` : remplacer chaque `use_container_width=True` par `width="stretch"` et retirer les emojis des libellés/légendes (par exemple `st.caption("✅ Candidature déjà enregistrée comme postulée")` -> `st.caption("Candidature déjà enregistrée comme postulée.")`) ; utiliser `icon=":material/…:"` quand une icône aide.
4. Vérifier : `.venv/Scripts/python.exe -m pytest tests/test_ui_conventions.py -q -p no:cacheprovider` -> les fichiers déjà listés doivent passer (sauf `flux.py`, qui n'est pas encore réécrit).

- [ ] **Step 5: Réécrire `app_pages/flux.py`**

```python
"""Page Flux : KPI, filtres latéraux et flux d'offres interactif."""
from __future__ import annotations

import streamlit as st

from components.job_feed import job_feed
from src.config import load_config
from src.constants import source_rank
from utils.components import (
    render_header,
    render_kpis,
    render_sidebar_filters,
    show_cover_letter_dialog,
)
from utils.data import filter_jobs, get_database, load_jobs

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
render_kpis(selected, llm_model, len(jobs), not filters.is_default())

if not selected:
    st.info(
        "Aucune offre ne correspond aux filtres courants. Relancez la collecte ou élargissez les critères.",
        icon=":material/search_off:",
    )
else:
    event = job_feed(
        selected,
        keywords,
        hide_processed=filters.hide_processed,
        grouped=filters.group_by_source,
    )
    if event and event["type"] == "letter":
        job = next((j for j in selected if str(j["id"]) == event["id"]), None)
        if job is not None:
            show_cover_letter_dialog(job)
```

- [ ] **Step 6: Adapter `tests/test_app.py`**

1. Supprimer de l'import `from utils.components import (...)` les noms disparus (`job_card_html`) et importer depuis `utils.data` ceux qui y vivent (`score_alignment, clean_text?`) : `clean_text` et `excerpt` restent dans `utils.components` ; `relative_date, parse_timestamp, detected_technologies, contract_label, EXCERPT_LENGTH, score_alignment` viennent de `utils.data`.
2. Supprimer les tests dédiés au HTML des cartes : `test_carte_html` et `test_grille_sous_scores` (couverts par `tests/test_job_feed.py`), ainsi que leur appel dans `main()`, et la constante `_OFFER_CONTENT_PATTERNS` / `ui_labels_only` si plus utilisées.
3. Remplacer `test_interface_streamlit` par :

```python
def test_interface_streamlit(monkeypatch) -> None:
    """La page Flux se rend sans exception, les filtres pilotent réellement le flux."""
    calls: list[tuple[list, dict]] = []
    monkeypatch.setattr(
        "components.job_feed.job_feed",
        lambda jobs, keywords, **kwargs: calls.append((jobs, kwargs)),
    )
    at = AppTest.from_file(str(PROJECT_ROOT / "app_pages" / "flux.py"), default_timeout=240)
    at.run()
    assert not at.exception, f"Exception dans l'app : {at.exception}"

    # 1. Barre latérale : filtres compacts attendus (inchangés).
    assert [widget.label for widget in at.sidebar.text_input] == ["Recherche"]
    assert [widget.label for widget in at.sidebar.slider] == ["Score R&D minimal"]
    assert [widget.label for widget in at.sidebar.selectbox] == ["Mode de flux", "Offres affichées"]
    assert [widget.label for widget in at.sidebar.toggle][:3] == [
        "Verdict LLM uniquement",
        "Masquer les offres traitées",
        "Masquer les offres exclues",
    ]

    scripts = {action.key: action.script for action in PIPELINE_ACTIONS}
    assert scripts["collect"] == "run_scrapers.py"
    collect = next(a for a in PIPELINE_ACTIONS if a.key == "collect")
    assert "--no-scoring" in collect.args, collect

    # 2. En-tête et KPI natifs ; le flux reçoit les offres filtrées (50 par défaut).
    assert any("Flux d'offres" in header.value for header in at.header)
    assert len(at.metric) == 3, "Trois KPI natifs attendus."
    db = _open_database()
    total = db.count_jobs()
    db.engine.dispose()
    assert calls, "Le composant job_feed doit être monté."
    jobs_sent, kwargs = calls[-1]
    assert len(jobs_sent) == min(total, 50), f"{len(jobs_sent)} offre(s) transmise(s) pour {total} en base"
    assert kwargs["hide_processed"] is True and kwargs["grouped"] is False

    # 3. Aucun emoji décoratif dans les libellés d'interface.
    rendered = " ".join(
        [element.value for element in at.markdown]
        + [element.value for element in at.caption]
        + [element.value for element in at.header]
        + [metric.label for metric in at.metric]
    )
    for emoji in BANNED_EMOJI:
        assert emoji not in rendered, f"Emoji décoratif détecté : {emoji}"

    # 4. Recherche infructueuse : message d'état vide, composant non monté.
    calls.clear()
    at.sidebar.text_input[0].set_value("zz-introuvable-zz").run()
    assert not at.exception, at.exception
    assert calls == []
    assert any("Aucune offre ne correspond" in info.value for info in at.info)

    # 5. Vue groupée par plateforme : le mode est transmis au composant.
    at.sidebar.text_input[0].set_value("").run()
    at.sidebar.selectbox[0].select(DISPLAY_GROUPED).run()
    assert not at.exception, at.exception
    assert calls[-1][1]["grouped"] is True

    # 6. Vue focus : le filtre de statut cède la main.
    at.sidebar.toggle[1].set_value(True).run()
    assert not at.exception, at.exception
    assert at.sidebar.multiselect[1].disabled is True
    print("  Interface : page Flux (filtres, KPI natifs, composant) OK")
```

4. `test_palette_sombre_claire_et_repli` reste inchangé pour l'instant (adapté en Task 11).
5. `test_kanban_interface`/`test_parametres_interface` inchangés dans cette tâche.

- [ ] **Step 7: Lancer les tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_app.py tests/test_job_feed.py tests/test_ui_conventions.py -q -p no:cacheprovider`
Expected: tout vert. Si `monkeypatch.setattr("components.job_feed.job_feed", …)` n'est pas pris en compte (la page a déjà importé le nom), vérifier que la page fait `from components.job_feed import job_feed` **à l'exécution du script** (AppTest ré-exécute le script, l'import lit l'attribut patché) ; ne pas déplacer l'import au niveau d'un module tiers.

- [ ] **Step 8: Vérification navigateur (base réelle)**

Sur `http://localhost:8511/` (1440 px) : liste dense avec 50 offres, détail à droite, boutons d'action sur une ligne sans troncature, KPI en 3 cartes. Tester UNE action réelle sur une offre (`p`), constater la disparition puis « Annuler » et vérifier le retour de l'offre ; l'offre doit finir avec son statut d'origine. Tester « Lettre » (le dialogue Streamlit s'ouvre). Vérifier les 3 modes de filtre latéral (recherche, plateforme, score minimal).

- [ ] **Step 9: Commit**

```bash
git add utils/layout.py utils/components.py app_pages/flux.py tests
git commit -m "feat(ui): page Flux avec composant job_feed, KPI natifs et suppression de l'ancien rendu de cartes"
```

---

## Livraison 3 — Candidatures (Kanban)

### Task 7: Page Candidatures

**Files:**
- Modify (réécriture complète): `app_pages/kanban.py`
- Modify: `tests/test_app.py` (`test_kanban_interface`), `tests/test_ui_conventions.py` (`CLEAN_FILES`)

**Interfaces:**
- Consumes: `page_header` (Task 6), `_set_status`, `show_cover_letter_dialog`, `filter_jobs`, `Filters`.
- Produces: page `app_pages/kanban.py` (script direct).

- [ ] **Step 1: Mettre à jour le test (échoue)**

Dans `tests/test_app.py`, remplacer `test_kanban_interface` :

```python
def test_kanban_interface() -> None:
    """La page Candidatures est rendue sans erreur, avec cinq colonnes et un en-tête natif."""
    at = AppTest.from_file(str(PROJECT_ROOT / "app_pages" / "kanban.py"), default_timeout=60).run()
    assert not at.exception, at.exception
    assert any("Candidatures" in header.value for header in at.header)
    column_titles = " ".join(element.value for element in at.markdown)
    for label in ("Nouveau", "Postulé", "Entretien", "Refusé", "Archivé"):
        assert label in column_titles, label
    print("  Interface : page Candidatures OK")
```

Ajouter `"app_pages/kanban.py"` à `CLEAN_FILES` dans `tests/test_ui_conventions.py`.

Run: `.venv/Scripts/python.exe -m pytest tests/test_app.py::test_kanban_interface tests/test_ui_conventions.py -q -p no:cacheprovider`
Expected: FAIL.

- [ ] **Step 2: Réécrire `app_pages/kanban.py`**

```python
"""Page Candidatures : tableau Kanban de suivi, une colonne par statut."""
from __future__ import annotations

import re
from typing import Any

import streamlit as st

from src.constants import (
    STATUS_APPLIED,
    STATUS_IGNORED,
    STATUS_INTERVIEW,
    STATUS_NEW,
    STATUS_REJECTED,
    VERDICT_LABELS,
)
from utils.components import show_cover_letter_dialog
from utils.data import (
    Filters,
    STATUS_LABELS,
    VERDICT_TONES,
    _set_status,
    effective_score,
    filter_jobs,
    get_database,
    is_reranked,
    load_jobs,
    score_alignment,
)
from utils.layout import page_header

KANBAN_COLUMNS = (
    (STATUS_NEW, "Nouveau"),
    (STATUS_APPLIED, "Postulé"),
    (STATUS_INTERVIEW, "Entretien"),
    (STATUS_REJECTED, "Refusé"),
    (STATUS_IGNORED, "Archivé"),
)
PAGE_STEP = 10
_BADGE_COLORS = {"positive": "green", "accent": "blue", "warn": "orange", "alert": "red", "mute": "gray"}


def _md(text: Any) -> str:
    """Neutralise le markdown d'un texte venu d'une plateforme (titres, entreprises)."""
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|~<>$&])", r"\\\1", " ".join(str(text or "").split()))


def _limit_key(status: str) -> str:
    return f"kanban_limit_{status}"


def _show_more(status: str) -> None:
    st.session_state[_limit_key(status)] = st.session_state.get(_limit_key(status), PAGE_STEP) + PAGE_STEP


def _render_card(job: dict[str, Any], db: Any, current_status: str) -> None:
    job_id = str(job["id"])
    score = effective_score(job)
    align_label, tone = score_alignment(score)
    url = str(job.get("url") or "")
    location = str(job.get("location") or "")

    with st.container(border=True):
        st.markdown(f"**{_md(job.get('title') or 'Offre sans titre')}**")
        st.caption(_md(job.get("company") or "Entreprise inconnue") + (f" · {_md(location)}" if location else ""))
        badges = [f":{_BADGE_COLORS[tone]}-badge[{score:.0f} · {align_label}]"]
        verdict = job.get("verdict")
        if is_reranked(job) and verdict:
            badges.append(f":{_BADGE_COLORS.get(VERDICT_TONES.get(verdict, 'mute'), 'gray')}-badge[{VERDICT_LABELS.get(verdict, verdict)}]")
        st.markdown(" ".join(badges))

        with st.popover("Actions", icon=":material/more_horiz:", width="stretch"):
            if url.startswith(("http://", "https://")):
                st.link_button("Ouvrir l'offre", url, icon=":material/open_in_new:", width="stretch")
            if st.button("Lettre de motivation", key=f"kb_letter_{job_id}", icon=":material/edit_note:", width="stretch"):
                show_cover_letter_dialog(job)
            for status, label in KANBAN_COLUMNS:
                if status in (current_status, STATUS_REJECTED):
                    continue
                st.button(
                    f"Déplacer vers {label.lower()}",
                    key=f"kb_move_{status}_{job_id}",
                    on_click=_set_status,
                    args=(db, job_id, status),
                    width="stretch",
                )


page_header("Candidatures", "Faites évoluer le statut de vos candidatures d'une colonne à l'autre.")

db = get_database()
data_version = int(st.session_state.setdefault("data_version", 0))
jobs = load_jobs(db, data_version)

company_counts: dict[str, int] = {}
for job in jobs:
    company = (job.get("company") or "").strip()
    if company:
        company_counts[company] = company_counts.get(company, 0) + 1
sorted_companies = sorted(company_counts, key=lambda c: (-company_counts[c], c.lower()))

with st.expander("Filtres", icon=":material/filter_list:"):
    left, middle, right = st.columns([2, 1, 1])
    with left:
        query = st.text_input("Recherche", placeholder="Poste ou entreprise…", icon=":material/search:")
    with middle:
        min_score = st.slider("Score minimal", 0, 100, 0, step=5)
    with right:
        rerank_only = st.toggle("Verdict LLM uniquement")
        exclude_dassault = st.toggle("Exclure Dassault")
    col_ex, col_sel = st.columns(2)
    with col_ex:
        exclude_companies = st.multiselect(
            "Exclure des entreprises",
            options=sorted_companies,
            format_func=lambda c: f"{c} ({company_counts.get(c, 0)})",
        )
    with col_sel:
        selected_companies = st.multiselect(
            "Cibler des entreprises",
            options=sorted_companies,
            format_func=lambda c: f"{c} ({company_counts.get(c, 0)})",
        )

filtered = filter_jobs(
    jobs,
    Filters(
        query=query,
        min_score=float(min_score),
        llm_only=rerank_only,
        exclude_dassault=exclude_dassault,
        exclude_companies=tuple(exclude_companies),
        selected_companies=tuple(selected_companies),
    ),
)

jobs_by_status: dict[str, list[dict[str, Any]]] = {code: [] for code, _ in KANBAN_COLUMNS}
for job in filtered:
    code = str(job.get("status") or STATUS_NEW)
    jobs_by_status[code if code in jobs_by_status else STATUS_NEW].append(job)

columns = st.columns(len(KANBAN_COLUMNS), gap="small")
for column, (status, label) in zip(columns, KANBAN_COLUMNS):
    members = jobs_by_status[status]
    limit = st.session_state.get(_limit_key(status), PAGE_STEP)
    with column:
        st.markdown(f"##### {label} ({len(members)})")
        if not members:
            st.caption("Aucune offre")
        for job in members[:limit]:
            _render_card(job, db, status)
        remaining = len(members) - limit
        if remaining > 0:
            st.button(
                f"Afficher {min(PAGE_STEP, remaining)} de plus ({remaining} restantes)",
                key=f"kb_more_{status}",
                on_click=_show_more,
                args=(status,),
                width="stretch",
            )
```

Notes : (a) les filtres passés à `Filters` sont exactement ceux de l'ancienne page (défauts de `Filters` inclus : aucun changement de logique de sélection). (b) `STATUS_REJECTED` n'est jamais une cible de déplacement (statut posé par le pipeline) mais reste une colonne d'affichage.

- [ ] **Step 3: Tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_app.py tests/test_ui_conventions.py -q -p no:cacheprovider`
Expected: vert. Si `st.popover(width="stretch")` ou `st.expander(icon=…)` lève une `TypeError`, vérifier avec `.venv/Scripts/python.exe -m streamlit docs st.popover` / `st.expander` et retirer l'argument non supporté.

- [ ] **Step 4: Vérification navigateur**

`http://localhost:8511/kanban` à 1440 px : cinq colonnes lisibles, 10 cartes par colonne maximum, « Afficher 10 de plus (N restantes) » fonctionne, popover « Actions » avec ouverture d'offre / lettre / déplacements. Déplacer une carte de test « Nouveau -> Postulé » puis la remettre « Nouveau ». Titre contenant `[` `]` `*` : aucun rendu markdown parasite.

- [ ] **Step 5: Commit**

```bash
git add app_pages/kanban.py tests/test_app.py tests/test_ui_conventions.py
git commit -m "feat(ui): page Candidatures (cartes limitees par colonne, popover d'actions, sans emoji)"
```

---

## Livraison 4 — Harmonisation

### Task 8: Statistiques

**Files:**
- Modify: `app_pages/statistiques.py`, `tests/test_ui_conventions.py`

**Interfaces:**
- Consumes: `page_header` (Task 6).
- Produces: aucune API nouvelle ; les helpers testés (`_telemetry_runs_table`, `_counters_strip`, `_collection_counters_table`, `_objectives_table`, `_refusals_table`, `normalize_region`) gardent leurs signatures et leur sortie.

- [ ] **Step 1: Ajouter la page aux fichiers gardés (échoue)**

Ajouter `"app_pages/statistiques.py"` à `CLEAN_FILES` (`tests/test_ui_conventions.py`) puis :
Run: `.venv/Scripts/python.exe -m pytest tests/test_ui_conventions.py -q -p no:cacheprovider`
Expected: FAIL (emojis / `use_container_width` dans `statistiques.py`).

- [ ] **Step 2: En-tête et onglets**

Dans `app_pages/statistiques.py` :
- Remplacer le bandeau d'ouverture de page (les `st.markdown('<div class="sc-stream">…Statistiques sur N offres…')` juste avant `st.tabs`, y compris la variante « masquée(s) par le filtre entreprise ») par `page_header("Statistiques", f"{len(jobs)} offres analysées · <complément existant>")` en réutilisant le texte des deux variantes (`from utils.layout import page_header` en tête de fichier).
- Remplacer l'appel `st.tabs([...])` par des libellés sans emoji, avec le même nombre et le même ordre :

```python
tab_personal, tab_geo, tab_rd, tab_telemetry, tab_rejections = st.tabs(
    [
        "Mes candidatures",
        "Cartographie et entreprises",
        "Qualité R&D et technologies",
        "Télémétrie des collectes",
        "Explorateur des rejets",
    ]
)
```

- [ ] **Step 3: Titres de section et emojis**

1. Lister les occurrences : `grep -n -P "[\x{1F300}-\x{1FAFF}\x{2600}-\x{26FF}\x{2705}\x{274C}\x{2728}\x{2B50}\x{FE0F}]" app_pages/statistiques.py`.
2. Pour chaque occurrence : supprimer l'emoji du libellé ; si un titre est en capitales (`.upper()` ou `text-transform`), le passer en casse phrase. Les titres du type `"### 🎯 SUIVI DE VOS CANDIDATURES PERSONNELLES"` deviennent `"#### Suivi de vos candidatures"`.
3. Ne pas modifier les contenus dépendant d'assertions existantes dans `tests/test_app.py` : « Runs de collecte », « Dernières passes (raison d'arrêt) », « Compteurs par source », « Objectifs de collecte », « Ce qui est refusé », « Refusée — hors sujet », « Déjà connue », « Plafond de pages atteint (flux potentiellement tronqué) ». `grep -n "sc-alert-icon" app_pages/statistiques.py` : remplacer l'icône `⚠️` par un texte sans emoji (ex. `!`) et laisser `✓` (autorisé).
4. `sed -i 's/use_container_width=True/width="stretch"/g' app_pages/statistiques.py` puis relire les appels concernés : pour `st.altair_chart`, `st.dataframe`, `st.plotly_chart` etc. vérifier qu'ils acceptent `width="stretch"` (`streamlit docs st.altair_chart`).

- [ ] **Step 4: Tests et vérification navigateur**

Run: `.venv/Scripts/python.exe -m pytest tests/test_app.py tests/test_database.py tests/test_ui_conventions.py -q -p no:cacheprovider`
Expected: vert. Puis `http://localhost:8511/statistiques` : 5 onglets sans emoji, aucun titre en capitales, graphiques présents.

- [ ] **Step 5: Commit**

```bash
git add app_pages/statistiques.py tests/test_ui_conventions.py
git commit -m "refactor(ui): page Statistiques harmonisee (en-tete natif, onglets et titres sans emoji)"
```

---

### Task 9: Pipeline

**Files:**
- Modify: `app_pages/pipeline.py`, `tests/test_ui_conventions.py`

**Interfaces:**
- Consumes: `page_header`, `kpi_row` (Task 6).
- Produces: `PIPELINE_ACTIONS`, `PipelineAction`, `run_pipeline`, `save_default_settings`, `render_custom_collection_form`, `render_base_panel` conservent leurs signatures.

- [ ] **Step 1: Ajouter la page aux fichiers gardés (échoue)**

Ajouter `"app_pages/pipeline.py"` à `CLEAN_FILES` ; `pytest tests/test_ui_conventions.py` -> FAIL.

- [ ] **Step 2: Restructurer `render_base_panel` en blocs bordés**

Dans `app_pages/pipeline.py` :
1. Ajouter `from utils.layout import kpi_row, page_header` et, tout en haut de `render_base_panel`, `page_header("Pipeline", "Base de données, maintenance du scoring, synchronisation cloud et collecte.")` (avant `render_task_monitor()`).
2. Remplacer le bloc « État de la base » (le `st.markdown("### État de la base SQLite…")`, le `<div class="sc-kv">…`, le `st.caption(...)` avec `<b>`) par :

```python
    with st.container(border=True):
        st.subheader("État de la base", anchor=False)
        kpi_row(
            [
                *[(label, str(count), None) for label, count, _ in distribution],
                ("Évaluées v3", str(len(v3_jobs)), "Offres notées avec la grille v3"),
                ("Évaluées v1", str(len(v1_jobs)), "Offres notées en v1, à réévaluer"),
            ]
        )
        st.caption(f"{len(jobs)} offres en base SQLite.")
```

3. Envelopper « Maintenance du scoring v3 » (les deux colonnes + le bouton « Actualiser la vue ») dans `with st.container(border=True):` avec `st.subheader("Maintenance du scoring v3", anchor=False)` à la place de `st.markdown("#### Maintenance du scoring v3")`.
4. Remplacer le séparateur `st.markdown("---")` + `st.markdown("### ☁️ Synchronisation Cloud (R2 / S3)")` par `with st.container(border=True):` + `st.subheader("Synchronisation cloud (R2 / S3)", anchor=False)` et indenter le bloc jusqu'à l'`else` compris ; retirer les emojis des boutons : `"⬇️ Récupérer la dernière base distante"` -> `"Récupérer la base distante"` avec `icon=":material/cloud_download:"` ; `"⬆️ Sauvegarder la base vers le cloud"` -> `"Sauvegarder vers le cloud"` avec `icon=":material/cloud_upload:"`.
5. Bloc cloud/production : `"### 🛡️ Collecte & Pipeline (Mode Cloud)"` -> `st.subheader("Collecte et pipeline (mode cloud)", anchor=False)` ; dans l'avertissement, retirer « 👉 » (garder la phrase). Bloc local : `render_custom_collection_form(...)` puis `st.subheader("Actions prédéfinies", anchor=False)` dans un `st.container(border=True)`.
6. `"⏳ Un traitement est actuellement en cours…"` -> `st.info("Un traitement est en cours : suivez sa progression en direct ci-dessus.", icon=":material/hourglass_top:")`.
7. Bouton principal : dans la boucle des actions prédéfinies, passer `type="primary" if action.key == "full_pipeline" else "secondary"`.
8. `render_custom_collection_form` : `st.markdown("### Personnaliser & Lancer la collecte")` -> `st.subheader("Personnaliser et lancer la collecte", anchor=False)` ; le message de succès `"…défauts dans config.yaml !"` -> sans point d'exclamation superflu si présent (facultatif).
9. `sed -i 's/use_container_width=True/width="stretch"/g' app_pages/pipeline.py`.
10. Les actions destructrices : le seul bouton destructif potentiel est « Récupérer la base distante » (écrase la base locale). Le faire précéder d'une case de confirmation : `confirm = st.checkbox("Je confirme le remplacement de la base locale", key="confirm_download")` et `disabled=not confirm` sur le bouton.

- [ ] **Step 3: Tests et navigateur**

Run: `.venv/Scripts/python.exe -m pytest tests/test_app.py tests/test_ui_conventions.py -q -p no:cacheprovider`
Expected: vert (`PIPELINE_ACTIONS` toujours importable). Navigateur `http://localhost:8511/pipeline` : quatre blocs bordés titrés, aucun emoji, un seul bouton primaire dans les actions prédéfinies, case de confirmation devant le téléchargement. Ne PAS lancer d'action réelle.

- [ ] **Step 4: Commit**

```bash
git add app_pages/pipeline.py tests/test_ui_conventions.py
git commit -m "refactor(ui): page Pipeline en blocs bordes, sans emoji, confirmation avant ecrasement"
```

---

### Task 10: Paramètres

**Files:**
- Modify: `app_pages/parametres.py`, `tests/test_ui_conventions.py`

**Interfaces:**
- Consumes: `page_header` (Task 6).
- Produces: aucune API ; le radio de notation de l'onglet « Re-notation des offres » (option contenant « non notées ») reste présent (test existant).

- [ ] **Step 1: Ajouter la page aux fichiers gardés (échoue)**

Ajouter `"app_pages/parametres.py"` à `CLEAN_FILES` ; `pytest tests/test_ui_conventions.py` -> FAIL.

- [ ] **Step 2: En-tête et onglets**

Dans `app_pages/parametres.py`, remplacer le `st.markdown("<h1>Paramètres &amp; Profil</h1>", unsafe_allow_html=True)` + `st.caption(...)` par `page_header("Paramètres et profil", "Personnalisez votre CV, vos critères de collecte et la ré-évaluation par Gemini.")` (import `from utils.layout import page_header`), et les onglets par :

```python
tab_cv, tab_scraping, tab_v3, tab_rerank = st.tabs([
    "CV et profil",
    "Recherches et collecte",
    "Grille de scoring v3",
    "Re-notation des offres",
])
```

- [ ] **Step 3: CV replié sous un aperçu**

Dans l'onglet CV, remplacer la zone « 2. Consulter et ajuster le profil » (le `st.text_area` de 450 px) par un aperçu + un expander d'édition :

```python
    st.markdown("#### 2. Consulter et ajuster le profil")
    editor_default = st.session_state.get("cv_editor_content", current_cv)

    preview_lines = [line.strip() for line in editor_default.splitlines() if line.strip()][:4]
    with st.container(border=True):
        if preview_lines:
            st.markdown("  \n".join(preview_lines))
            st.caption(f"{len(editor_default)} caractères · aperçu des premières lignes")
        else:
            st.caption("Aucun CV enregistré : importez un document ci-dessus ou saisissez le texte.")

    with st.expander("Modifier le texte du CV", icon=":material/edit:", expanded=not editor_default.strip()):
        edited_cv = st.text_area(
            "Texte du CV utilisé par le juge LLM et le générateur de lettre :",
            value=editor_default,
            height=450,
            help="Éditez directement ce texte, puis cliquez sur « Enregistrer le profil ».",
        )
```

Les boutons « Enregistrer le profil » / « Télécharger en .txt » restent **hors** de l'expander (ils utilisent `edited_cv` ; si le `text_area` est dans l'expander, `edited_cv` reste défini car l'expander exécute son contenu à chaque rerun).

- [ ] **Step 4: Titres, emojis, boutons**

1. `st.markdown("### Profil du candidat &amp; CV actif", unsafe_allow_html=True)` et les titres analogues (`&amp;`, `Mots-clés &amp; Requêtes cibles`, `Notation &amp; Ré-évaluation…`, capitales inutiles) : `st.subheader("…", anchor=False)` / `st.markdown("#### …")` avec `&` littéral et sans `unsafe_allow_html`.
2. Retirer tous les emojis restants : `grep -n -P "[\x{1F300}-\x{1FAFF}\x{2600}-\x{26FF}\x{2705}\x{274C}\x{2728}\x{2B50}\x{FE0F}]" app_pages/parametres.py`.
3. `sed -i 's/use_container_width=True/width="stretch"/g' app_pages/parametres.py`.
4. Ne pas renommer le radio ni ses options (le test cherche « non notées »).

- [ ] **Step 5: Tests et navigateur**

Run: `.venv/Scripts/python.exe -m pytest tests/test_app.py tests/test_ui_conventions.py -q -p no:cacheprovider`
Expected: vert (dont `test_parametres_interface`). Navigateur `http://localhost:8511/parametres` : onglets sans emoji, aperçu du CV, expander « Modifier le texte du CV » replié quand un CV existe. Ne pas cliquer sur « Enregistrer ».

- [ ] **Step 6: Commit**

```bash
git add app_pages/parametres.py tests/test_ui_conventions.py
git commit -m "refactor(ui): page Parametres (en-tete natif, apercu du CV, onglets sans emoji)"
```

---

### Task 11: Réduction du CSS, dernières conventions, dépendances et vérification finale

**Files:**
- Modify: `utils/styles.py`, `utils/auth.py`, `utils/task_manager.py`, `tests/test_app.py`, `tests/test_ui_conventions.py`, `requirements.txt`, `requirements-render.txt`, `README.md`

**Interfaces:** aucune.

- [ ] **Step 1: Auditer les classes CSS orphelines**

Créer `<scratchpad>/css_audit.py` (hors dépôt) :

```python
import pathlib
import re

root = pathlib.Path(".")
css = (root / "utils/styles.py").read_text(encoding="utf-8")
classes = set(re.findall(r"\.(sc-[a-z0-9_-]+)", css))
sources = "\n".join(
    path.read_text(encoding="utf-8")
    for pattern in ("app.py", "app_pages/*.py", "utils/*.py", "components/**/*.py")
    for path in root.glob(pattern)
    if path.name != "styles.py"
)
print("\n".join(sorted(c for c in classes if c not in sources)))
```

Run: `.venv/Scripts/python.exe <scratchpad>/css_audit.py`
Expected: liste des classes `.sc-*` définies mais plus utilisées (attendu : `sc-card*`, `sc-subscore*`, `sc-details*`, `sc-chev`, `sc-group*`, `sc-quality-sub`, … ; **conservées** : `sc-badge`, `sc-kpis`, `sc-kpi*`, `sc-stream*`, `sc-status*`, `sc-alert*`, `sc-empty`, `sc-tone-*`, `sc-eyebrow`… tant que `statistiques.py` les émet).

- [ ] **Step 2: Réduire `utils/styles.py`**

1. Supprimer les règles CSS dont **tous** les sélecteurs portent uniquement sur des classes orphelines (liste de l'étape 1), et le template `_CSS_CARD` s'il devient vide (retirer alors son entrée de `_CSS_TEMPLATE`).
2. Supprimer de `_CSS_CHROME` les surcharges de composants Streamlit devenues inutiles grâce au thème natif : les blocs `[data-testid="stButton"] button…`, `[data-testid="stLinkButton"] a…` (bouton secondaire, primaire, survols), `[data-testid="stSlider"]…`, `[data-testid="stMultiSelect"] [data-baseweb="tag"]…`. Conserver : `stMainBlockContainer` (padding), `stSidebar` (fond, libellés de widget en petites capitales, expanders) et les `prefers-reduced-motion`.
3. Relancer l'audit (Step 1) : la liste doit être vide.

- [ ] **Step 3: Adapter le test de palette**

Dans `tests/test_app.py`, `test_palette_sombre_claire_et_repli` : remplacer l'assertion
`assert ".sc-card" in css and ".sc-badge" in css and ".sc-kpis" in css, theme`
par
`assert ".sc-badge" in css and ".sc-kpis" in css, theme`.

- [ ] **Step 4: Emojis et `use_container_width` restants**

1. `utils/auth.py` : `use_container_width=True` -> `width="stretch"`.
2. `utils/task_manager.py` : retirer l'emoji du badge (`f"**⚡ {task.get('name', 'Tâche')} en cours**"` -> `f"**{task.get('name', 'Tâche')} en cours**"`), et tout autre emoji / `use_container_width` (`grep -n -P "[\x{1F300}-\x{1FAFF}\x{2600}-\x{26FF}\x{2705}\x{274C}\x{2728}\x{2B50}\x{FE0F}]|use_container_width" utils/task_manager.py utils/auth.py`).
3. Ajouter à `CLEAN_FILES` : `"app.py"`, `"utils/auth.py"`, `"utils/task_manager.py"`, `"utils/styles.py"`, `"utils/data.py"`.
4. Run: `.venv/Scripts/python.exe -m pytest tests/test_ui_conventions.py -q -p no:cacheprovider` -> tout vert.

- [ ] **Step 5: Épingler Streamlit et documenter**

```bash
grep -a -n "^streamlit" requirements.txt requirements-render.txt
sed -i 's/^streamlit\(\r\?\)$/streamlit>=1.63\1/' requirements.txt requirements-render.txt
grep -a -n "^streamlit" requirements.txt requirements-render.txt
```
Expected: les deux lignes deviennent `streamlit>=1.63`.

Dans `README.md`, section des pages : décrire la navigation (Veille / Analyse / Système), le composant `components/job_feed/` (liste dense, raccourcis `j k o l p x ?`, annulation) et `tools/feed_demo.py` (démonstration isolée).

- [ ] **Step 6: Suite complète**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`
Expected: tout vert (ou uniquement les échecs préexistants relevés en Task 0).

- [ ] **Step 7: Vérification visuelle finale**

Pour chacune des 5 pages à 1440 px puis Flux à 1100 px et 375 px (`resize_window`), avec `screenshot` :
- aucune troncature de bouton, aucun débordement horizontal ;
- une seule grammaire d'en-tête (titre casse phrase + légende) ;
- sidebar : navigation en sections, filtres uniquement sur Flux ;
- console propre (`read_console_messages` `onlyErrors`).
Puis `resize_window` preset `desktop` et arrêter les serveurs de démonstration (`preview_stop`).

- [ ] **Step 8: Commit**

```bash
git add utils tests requirements.txt requirements-render.txt README.md
git commit -m "refactor(ui): CSS reduit aux classes utilisees, conventions sans emoji, streamlit>=1.63"
```

- [ ] **Step 9: Revue de branche**

Demander une revue de l'ensemble (`superpowers:requesting-code-review`) sur `git diff main...feat/refonte-ui-ux` avant d'ouvrir une PR ; traiter les retours puis `superpowers:finishing-a-development-branch`.

---

## Self-Review (couverture de la spec)

| Section de la spec | Tâche |
|---|---|
| 3.1 Navigation `st.navigation`, `app_pages/`, boilerplate unique, sidebar (badge + déconnexion en bas) | 1 |
| 3.2 Composant CCv2 inline, sérialisation, actions, callback, snackbar d'annulation, pas de `selected_id` serveur | 3, 4, 5 |
| 3.3 Code supprimé / conservé | 6 |
| 4 UX du flux (liste, détail, segments, groupé, clavier, optimiste, responsive, vide, a11y) | 5 (+ 6 pour l'intégration) |
| 5 Grammaire commune (`page_header`, KPI natifs, sans emoji, `width="stretch"`, pleine largeur) | 2, 6, 7, 8, 9, 10, 11 |
| 6 Pages : Flux, Candidatures, Statistiques, Pipeline, Paramètres | 6, 7, 8, 9, 10 |
| 7 Thème et CSS (config + réduction en dernier) | 2, 11 |
| 8 Tests (sérialisation, AppTest par page, suite existante, vérification manuelle) | 3, 4, 5, 6, 7, 8, 9, 10, 11 |
| 9 Livraisons 1-4 | Sections du plan |
| 10 Risques (`streamlit>=1.63`, taille des données, imports de tests, Render) | 3 (volume), 1 (imports), 11 (pin) ; `render.yaml` inchangé (`streamlit run app.py`) |

Écarts assumés par rapport à la spec initiale, déjà reportés dans la spec : snackbar d'annulation rendu par le composant (pas de bouton dans `st.toast`), sélection non synchronisée avec le serveur (un `setStateValue` déclencherait un rerun par touche), réduction du CSS reportée en livraison 4, thème sombre natif hors périmètre.
