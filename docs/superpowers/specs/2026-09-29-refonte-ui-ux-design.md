# Refonte UI/UX — Stage Copilot

Date : 2026-09-29
Statut : design validé, en attente de relecture de la spec

## 1. Contexte et problèmes constatés

Audit des 5 pages (Streamlit 1.63, viewport 1440 px) :

- **Flux (app.py)** : boutons d'action tronqués (« Postul… », « Le », « Arch… ») car la
  barre d'actions utilise 5 `st.columns` fixes dans un panneau détail d'environ 650 px ;
  le conteneur principal est plafonné à `max-width: 1160px` (`utils/styles.py:124`).
  Les cartes « compactes » de la liste font ~350 px (titre 22 px sur 3 lignes, sous-scores,
  technos) : 2 offres visibles. L'offre sélectionnée est rendue deux fois (liste + détail)
  avec le même contenu.
- **Navigation** : dossier `pages/` historique, libellés bruts (« app », « parametres »),
  aucune icône ni section. Chaque page répète `set_page_config`, `inject_styles`,
  `require_auth` et des manipulations de `sys.path`.
- **Incohérences visuelles** : emojis dans les sous-scores, boutons et onglets alors que
  l'app se veut sans emoji décoratif ; titres en capitales dans Statistiques ; H1 surdimensionné
  dans Kanban ; Pipeline sans hiérarchie ; CV brut dans un textarea géant dans Paramètres.
- **Kanban** : jusqu'à 1000 cartes rendues dans la colonne « Nouveau ».
- **Dette** : ~700 lignes de CSS surchargeant des `data-testid` internes (fragile aux montées
  de version), `use_container_width` déprécié, `importlib.reload` résiduel dans `app.py`.

## 2. Objectifs et non-objectifs

Objectifs :
- Trier une offre (lire, décider, agir) sans scroll parasite ni troncature.
- Navigation claire et cohérente entre les 5 pages.
- Une seule grammaire visuelle, sans emoji, respectant le thème clair/sombre.
- Réduire le CSS custom à ce que le thème natif ne couvre pas.

Non-objectifs :
- Aucune modification du scoring, du scraping, de la base ou du juge LLM.
- Aucune modification des calculs de Statistiques (présentation uniquement).
- Pas de framework frontend ni d'étape de build.

## 3. Architecture

### 3.1 Navigation (`st.navigation`)

- `app.py` devient le point d'entrée unique : `set_page_config`, `require_auth`,
  `inject_styles`, puis `st.navigation(...).run()`.
- Les pages migrent de `pages/` vers `app_pages/` (scripts directs, pas de fonction `main`) :

| Section  | Page          | Fichier                       | Icône                   |
|----------|---------------|-------------------------------|-------------------------|
| Veille   | Flux          | `app_pages/flux.py`           | `:material/view_list:`  |
| Veille   | Candidatures  | `app_pages/kanban.py`         | `:material/view_kanban:`|
| Analyse  | Statistiques  | `app_pages/statistiques.py`   | `:material/monitoring:` |
| Système  | Pipeline      | `app_pages/pipeline.py`       | `:material/sync:`       |
| Système  | Paramètres    | `app_pages/parametres.py`     | `:material/tune:`       |

- Les pages ne rappellent plus `set_page_config`, `require_auth`, `inject_styles` ni
  `sys.path.insert`. Le bloc `importlib.reload` de `app.py` est supprimé.
- Sidebar : filtres uniquement sur Flux ; badge de tâche et « Déconnexion » en bas de sidebar.

### 3.2 Composant `job_feed` (CCv2 inline, JS vanilla)

Emplacement :

```
components/
  __init__.py
  job_feed/
    __init__.py      # enregistrement CCv2 + wrapper Python + sérialisation
    feed.html
    feed.css
    feed.js
```

- Enregistrement unique à l'import : `st.components.v2.component("job_feed", html=..., css=..., js=...)`,
  contenus lus depuis les fichiers (chaînes multi-lignes, pas de chemins).
- API Python : `job_feed(jobs: list[dict], *, key: str = "job_feed") -> FeedEvent | None`.
- Isolation de style (shadow DOM, `isolate_styles=True`) ; couleurs, rayons et polices via les
  variables `--st-*` pour hériter du thème clair/sombre.
- Interdits (v1) : `components.v1.*`, `Streamlit.setComponentValue`, `window.parent.postMessage`.

**Données Python → JS (`data`)** — produites par une fonction pure
`serialize_job(job, keywords) -> dict` :

`id, title, company, location, date_label, source_label, source_color, contract, structure_label,
structure_tone, score, score_origin, align_label, align_tone, verdict_label, verdict_tone,
status, status_label, is_new, hard_cap, badges[{label,tone}], sub_scores[{key,label,value,tone}],
signals[{label,tone,evidence}], technologies[], strengths[], red_flags[], reasoning,
description, rejection_reason, url`

Seules les offres de la page courante (limite 25/50/100 déjà appliquée par les filtres) sont
envoyées, ~4 Ko par offre.

**JS → Python** :
- État `selected_id` (`setStateValue`) : persiste la sélection entre reruns. Si l'id n'est plus
  dans la liste, le JS sélectionne la première offre.
- Déclencheur `action` (`setTriggerValue`) : `{type: "status", id, status}` ou
  `{type: "letter", id}`.
- Côté Python, après montage : `status` → `_set_status(db, id, status)` (bump `data_version`)
  puis `st.toast` avec bouton d'annulation (restaure le statut précédent) ; `letter` →
  `show_cover_letter_dialog(job)` existant, inchangé.
- « Postuler » : lien `<a target="_blank" rel="noopener">` dans le composant.

### 3.3 Code supprimé / conservé

Supprimé de `utils/components.py` : `job_card_html`, `render_job_card`,
`render_compact_card_with_select`, `render_job_detail_pane`, `render_stream` et les fragments
HTML qui ne servent qu'à eux ; CSS `.sc-card*`, `.sc-subscore*`, `.sc-details*` correspondant.

Conservé : `_badge`, `score_alignment` (importés par le kanban), `show_cover_letter_dialog`,
`render_sidebar_filters`, `render_header`, `render_kpis` (réécrit en natif).

## 4. UX du flux

```
┌─ 50 offres · tri score ─────────┬─────────────────────────────────────────────┐
│ [Tous] [Cœur de cible] [Bon]    │ STAGE - R&D Generative IA Surfacique   84  │
├─────────────────────────────────┤ Dassault Systèmes · Aix · il y a 10 j /100 │
│▌84  STAGE - R&D Generative IA…  │ JobTeaser · Stage · Grand groupe            │
│     Dassault Systèmes · Aix · 10j│ [Postuler ↗] [Lettre] [Postulé] [Archiver] │
│ 84  ML Researcher / Engineer    │ ─ Grille ────────────────────────────────── │
│     Pathway · Paris · 13 h   ●  │ Modélisation ▰▰▰▰▰ 5  Équipe ▰▰▰▰▱ 4 …     │
│ 79  Data Scientist NLP          │ ─ Verdict du juge ───────────────────────── │
│     Criteo · Paris · 2 j        │ + points forts / − vigilance                │
│ …                               │ ─ Raisonnement · Fiche de poste (repliable) │
└─────────────────────────────────┴─────────────────────────────────────────────┘
```

**Liste (≈38 %)** : lignes de 64 px — pastille de score teintée par l'alignement, titre sur une
ligne (ellipse, titre complet en `title`), « entreprise · ville · date ». Pastille terracotta si
statut Nouveau, icône cadenas si verrou bloquant. Sélection : barre gauche terracotta + fond
`--st-secondary-background-color`. Segments client en tête : Tous / Cœur de cible / Bon /
Non évalué (filtrage local, sans rerun).

**Détail (≈62 %)** : en-tête (titre, méta, score /100 + libellé d'alignement), badges, barre
d'actions en flex avec retour à la ligne (jamais de troncature) : Postuler (primaire), Lettre,
Postulé / Entretien obtenu / Rétablir selon le statut, Archiver. Puis sections : bandeau verrou
bloquant si présent, grille des sous-scores (barres segmentées /5), signaux vérifiés avec
citation au survol, verdict (points forts / points d'attention), raisonnement, fiche de poste
(extrait + `<details>` pour la version complète), motif d'exclusion si présent.

**Défilement** : liste et détail défilent indépendamment ; hauteur `calc(100vh - <offset>)`.

**Clavier** (actif quand le focus n'est pas dans un champ ; aide via `?`) :
`j`/`k` ou ↑/↓ naviguer · `o` ouvrir l'offre · `l` lettre · `p` marquer postulé · `x` archiver.

**Actions optimistes** : « Postulé » / « Archiver » retirent la ligne immédiatement quand
« Masquer les offres traitées » est actif, et sélectionnent l'offre suivante ; Python persiste
ensuite. Toast « Annuler » après changement de statut.

**Responsive** : sous 900 px, une colonne ; le détail s'affiche à la place de la liste avec un
bouton « Retour ».

**États vides** : aucun résultat → message + suggestion d'élargir les filtres (dans le
composant) ; offre sans évaluation LLM → section verdict remplacée par une note courte.

**Accessibilité** : liste en `role="listbox"` / `role="option"` + `aria-selected`, focus
visible, contraste AA sur les pastilles de score, `prefers-reduced-motion` respecté.

## 5. Grammaire commune aux pages (natif)

- En-tête : titre court en casse phrase + `st.caption` d'une ligne.
- KPI : `st.metric(border=True)` dans `st.container(horizontal=True)`.
- Regroupements : `st.container(border=True)` ; icônes Material ; aucun emoji.
- `use_container_width` remplacé par `width="stretch"` partout.
- Suppression du `max-width: 1160px` : contenu pleine largeur.

## 6. Pages

- **Flux** : en-tête + rangée de 4 `st.metric` (offres actives, qualifiées, rerankées, répartition
  plateformes en `st.caption`), puis `job_feed`.
- **Candidatures (kanban)** : 5 colonnes pleine largeur ; 10 cartes par colonne par défaut +
  bouton « Afficher plus » (+10) ; actions de carte dans `st.popover` « ⋮ » (déplacer vers…,
  lettre). Titre natif en casse phrase.
- **Statistiques** : onglets et titres sans emoji, casse normale ; graphiques inchangés hormis la
  palette issue du thème. Aucune logique de calcul modifiée.
- **Pipeline** : blocs bordés titrés — État de la base, Maintenance du scoring, Synchronisation
  cloud, Collecte ; action principale en bouton primaire ; actions destructives isolées avec
  confirmation explicite.
- **Paramètres** : onglets conservés ; CV brut dans un expander « Modifier le texte » sous un
  aperçu résumé (nom, formation, disponibilité).

## 7. Thème et CSS

- `.streamlit/config.toml` complété : `borderColor`, `baseRadius`, `font`/`headingFont`,
  `codeFont`, section `[theme.sidebar]`, variante `[theme.dark]` alignée sur la palette.
- `utils/styles.py` réduit à ~150 lignes : typographie de sidebar, classes utilitaires encore
  utilisées par le kanban et les stats. Suppression des surcharges `[data-testid=...]` de
  boutons, sliders, multiselect.

## 8. Tests et vérification

- Unitaires : `serialize_job` (champs obligatoires présents, offre non évaluée, verrou bloquant,
  échappement HTML délégué au JS via `textContent`, sous-scores manquants).
- `st.testing.v1.AppTest` : chaque page se charge sans exception (auth désactivée en test).
- Tests existants (`pytest`) verts.
- Vérification manuelle dans le navigateur à 1440 px, 1100 px et 375 px, thème clair et sombre :
  aucune troncature de bouton, sélection, raccourcis, action optimiste + annulation, lettre.

## 9. Livraisons

Chaque livraison est indépendante, testée et commitée séparément :

1. Navigation + chrome : `st.navigation`, `app_pages/`, thème, suppression du `max-width`,
   CSS réduit, sidebar réorganisée.
2. Composant `job_feed` + page Flux (KPI natifs, suppression de l'ancien rendu de cartes).
3. Kanban (limite par colonne, popover d'actions).
4. Harmonisation Statistiques, Pipeline, Paramètres.

## 10. Risques

- **CCv2 récent** : API vérifiée sur Streamlit 1.63 installé ; épingler `streamlit>=1.63` dans
  `requirements*.txt`.
- **Taille de `data`** : bornée par la limite d'affichage (100 offres max hors « Tout ») ;
  avec « Tout », descriptions tronquées à 4000 caractères côté sérialisation.
- **Tests existants important `utils.components`** : vérifier les imports avant suppression.
- **Déploiement Render** : le point d'entrée reste `app.py`, pas de changement de commande.
