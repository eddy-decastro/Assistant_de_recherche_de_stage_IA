# AUDIT — stage_copilot

> Audit technique du dépôt `Assistant_recherche_de_stage` : ce qu'il fait, comment il le fait,
> méthode de review employée, constats et plan d'amélioration.
> Périmètre : 31 fichiers (hors `.venv`), 4 scripts d'entrée, 4 modules métier.

---

## 1. Méthode de review employée

| Étape | Technique | But |
|---|---|---|
| 1 | **Lecture statique exhaustive** de tous les fichiers du projet (hors `.venv`) | reconstituer le flux complet et repérer les incohérences entre modules |
| 2 | **Traçage des flux de bout en bout** : collecte → filtrage anti-BI → ingestion → scoring → rerank → UI | vérifier que chaque champ produit en amont est bien consommé en aval |
| 3 | **Sondes réseau en lecture seule** (4 scripts jetables en `%TEMP%`, supprimés ensuite) | vérifier les *hypothèses implicites* du code sur les sources externes (taille de page, champs HTML, paramètres de filtre, bouton de candidature) |
| 4 | **Inspection directe de la base réelle** (requêtes SQL `SELECT` uniquement) | connaître l'état réel des données (`GROUP BY source`, volumétrie) |
| 5 | **Tests automatisés** : nouveaux cas unitaires + `AppTest` Streamlit | verrouiller les comportements corrigés (non-régression) |
| 6 | **Validation live** du correctif sur l'endpoint réel | prouver le comportement en production, pas seulement en laboratoire |

Principes retenus : *ne pas croire le code sur parole* (chaque hypothèse externe a été mesurée),
*ne jamais modifier les serveurs tiers* (uniquement des `GET` publics, espacés),
et *toute correction est accompagnée d'un test qui échoue avant et passe après*.

> Remarque : le dépôt n'est **pas** versionné (`git` : « not a git repository »).
> C'est le premier point de fragilité : aucune possibilité de `diff`/`revert` automatique.

---

## 2. Ce que fait le projet (vue d'ensemble)

Pipeline d'assistance à la recherche de stage Data Science / ML :

```
┌──────────────┐   ┌───────────────┐   ┌────────────────────┐   ┌──────────────────┐
│ COLLECTE     │   │ FILTRAGE      │   │ INGESTION          │   │ RANKING          │
│ scrapers/    │──▶│ anti-BI + DS  │──▶│ SQLite `jobs`      │──▶│ 1. Bi-Encoder    │
│ wttj         │   │ is_valid_job  │   │ idempotente        │   │ 2. Juge LLM      │
│ linkedin     │   │ (mots-clés)   │   │ (id + URL)         │   │  (DeepSeek)      │
│ jobteaser    │   └───────────────┘   └────────────────────┘   └────────┬─────────┘
└──────────────┘                                                         │
        ▲                                                                ▼
        │                                                    ┌──────────────────────┐
        │                                                    │ DASHBOARD Streamlit  │
        └────────────────────────────────────────────────────│ filtres + statuts    │
                        run_scrapers.py                      └──────────────────────┘
```

### 2.1 Modules et responsabilités

| Module | Rôle | Sortie |
|---|---|---|
| `scrapers/models.py` | modèle `RawJob` (Pydantic), `ScraperConfig`, listes de mots-clés, `ScrapeResult` | objets normalisés indépendants de la source |
| `scrapers/base.py` | `BaseScraper` : client `httpx` partagé, `load_env_file`, filtre métier `is_valid_job` (exclusion BI puis exigence d'un signal DS/ML), `run()` | `ScrapeResult` filtré |
| `scrapers/wttj.py` | API Algolia publique (`wttj_jobs_production`, filtre `contract_type:internship`), pagination `HITS_PER_PAGE=50` | offres WTTJ |
| `scrapers/linkedin.py` | endpoint **invité** `seeMoreJobPostings`, parsage HTML (BeautifulSoup/lxml) | offres LinkedIn (sans description) |
| `scrapers/jobteaser.py` | page intranet école, Cloudflare contourné par `curl_cffi` (impersonation TLS) + cookies `.env` ; replis cartes SSR → JSON embarqué → liens | offres JobTeaser |
| `scrapers/manager.py` | orchestrateur : lance les sources activées, fusionne, déduplique sur l'URL canonique | `ScrapeResult` unifié |
| `src/ingestion/bridge.py` | pont `RawJob` → table `jobs`, idempotent (dédup par `make_job_id` **et** URL) | statistiques d'ingestion |
| `src/ingestion/wttj.py` | scraper WTTJ **historique** (API v1 retirée fin 2024) | offres au format dict |
| `src/matching/scorer.py` | score hybride 0-100 : `0.60·sémantique + 0.25·typologie + 0.15·mots-clés` (`all-MiniLM-L6-v2`) | `semantic_score`, `final_score`, `company_tier` |
| `src/matching/llm_judge.py` | étape 2 : juge DeepSeek (`deepseek-chat`, `response_format=json_object`), verdicts + points forts/alertes, **fallback défensif garanti** | `rerank_score`, `verdict`, `match_reasons`, `red_flags`, `tech_stack` |
| `src/storage/database.py` | ORM SQLAlchemy, table `jobs`, migration légère (`ALTER TABLE`), CRUD, filtres | accès données |
| `src/constants.py` | tiers d'entreprise, statuts, verdicts, **libellés plateformes (nouveau)** | constantes partagées |
| `app.py` | dashboard Streamlit : bandeau KPI, flux de cartes d'offres (score R&D, verdict LLM, technos, actions), filtres compacts (recherche, plateformes, score, statut, typologie), lancement du pipeline | UI |
| `run_scrapers.py` / `run_pipeline.py` | entrées CLI (collecte + ingestion, ou pipeline historique complet) | exécution |

### 2.2 Schéma de données (`jobs`)

`id` (SHA256 title|company|url) · `title` · `company` (index) · `location` · `url` · `description` ·
`source` · `company_tier` · `semantic_score` · `final_score` (index) · `status` (index) · `created_at` ·
`rerank_score` · `verdict` · `match_reasons` (JSON) · `red_flags` (JSON) · `tech_stack` (JSON)

Contenu réel mesuré (lecture seule) : **88 offres** — `jobteaser` 38, `linkedin` 50 (71 offres lors de l'itération 1).

## 3. Constats (avec preuves)

### 3.1 🔴 Critique — perte silencieuse de ~60 % des offres LinkedIn *(corrigé)*

Le code supposait une page de 25 résultats : `RESULTS_PER_PAGE = 25` puis `start += RESULTS_PER_PAGE`.
Or l'endpoint invité renvoie **10 cartes par appel**, ce qu'une sonde réseau a établi :

```
start=  0  status=200  nb_cartes=10  ids=[4464330131, 4464331502, ...]
start= 10  status=200  nb_cartes=10  ids=[4464227633, 4435300130, ...]
start= 25  status=200  nb_cartes=10  ids=[4464544258, 4460482940, ...]
recouvrement 0/10 : 0     recouvrement 0/25 : 0
```

Conséquence : les positions **10→24, 35→49, …** n'étaient jamais demandées.
Avec `max_offers_per_source = 50`, seules 3 pages sur 5 étaient réellement parcourues.

### 3.2 🔴 Critique — `id_externe` LinkedIn jamais renseigné *(corrigé)*

`card.get("data-entity-urn")` était appelé sur le `<li>`… qui ne porte pas cet attribut
(il est sur la `div.base-card` interne — vérifié : `<li>` → `attrs = {}`).
Tous les `id_externe` LinkedIn retombaient donc sur l'URL, neutralisant l'identifiant métier naturel.

### 3.3 🟠 Majeur — biais structurel de scoring contre LinkedIn *(à corriger)*

Les cartes invitées n'exposent pas la description → `description = ""`.
Or :
- `Scorer.semantic_score()` renvoie **0.0** si le texte est vide et `keywords_score()` divise par le
  nombre total de mots-clés → une offre LinkedIn plafonne structurellement autour de
  `0.25·tier + 0.15·(mots-clés du titre)` ;
- le juge LLM reçoit `Description : (vide)` → pénalité systématique.

Résultat : le classement « top opportunités » est biaisé **par la source**, pas par la qualité du poste.
Avec 33 offres LinkedIn en base, l'impact est direct et mesurable.

### 3.4 🟠 Majeur — `config.yaml` sans effet sur le pipeline unifié

`config.yaml → scraping.search_keywords / contract_filter / location_filters / max_offers` n'est lu que
par `src/ingestion/wttj.py` (**scraper historique, API v1 supprimée fin 2024** → 403/404 en pratique).
`run_scrapers.py` instancie `ScraperConfig()` avec ses valeurs **codées en dur**
(`TARGET_QUERIES`, `EXCLUSION_KEYWORDS`, `max_offers_per_source=50`).
L'utilisateur qui édite `config.yaml` pour élargir la recherche ne constate donc aucun changement.

### 3.5 🟠 Majeur — double implémentation et code mort

- Deux scrapers WTTJ coexistent (`scrapers/wttj.py` fonctionnel via Algolia vs `src/ingestion/wttj.py` obsolète) ;
- deux `load_env_file` dupliqués (`scrapers/base.py` et `src/matching/llm_judge.py`) ;
- `requirements.txt` déclare `python-dotenv` et `pandas`, jamais importés.
Ces doublons créent des divergences silencieuses (correctif appliqué d'un côté seulement).

### 3.6 🟡 Mineur — robustesse réseau

- Aucun **backoff** : un `429` interrompt définitivement la requête en cours (pas de `Retry-After`) ;
- aucune rotation de `User-Agent` ni cache disque → un run complet refait tous les appels ;
- `start` reposait sur une constante au lieu de la réponse observée (même famille de bug que 3.1) ;
- déduplication LinkedIn absente **entre requêtes cibles** (une offre « ML » et « IA » créait 2 objets,
  absorbés seulement plus tard par le manager).

### 3.7 🟡 Mineur — données et concurrence

- Deux conventions pour la même plateforme : `wttj` (scrapers unifiés) vs `welcome_to_the_jungle`
  (ingestion historique) — les libellés d'affichage sont désormais unifiés, mais la **valeur en base** reste hétérogène ;
- déduplication **par URL non canonique** à l'ingestion (`bridge` compare la chaîne brute) alors que le
  manager, lui, normalise (query string / fragment / trailing slash) → doublons possibles selon le chemin d'entrée ;
- SQLite en mode journal par défaut : `app.py` (statuts) et `run_scrapers.py` (ingestion) peuvent se
  concurrencer → risque de `database is locked`.

### 3.8 🟡 Mineur — filtrage métier perfectible

- Mots-clés positifs très larges (`recherche`, `research`) → « nous recherchons un stagiaire » suffit à valider une offre ;
- le seuil d'exclusion travaille sur le titre seul pour LinkedIn (conséquence de 3.3) ;
- `is_internship=True` forcé côté LinkedIn (acceptable car `f_JT=I`, mais non vérifié offre par offre).

### 3.9 🟡 Mineur — tests et exploitation

- Tests écrits comme **scripts** (`python tests/test_x.py`), sans `pytest`, sans CI ;
- `test_scorer.py` / `test_reranker.py` dépendent de `torch` (téléchargement du modèle) et/ou du réseau ;
- pas de traçabilité des runs : impossible de savoir *quand* une source a renvoyé 0 offre (panne silencieuse) ;
- pas de versionnage Git.

## 4. Correctifs appliqués dans cette itération

| # | Fichier | Correction |
|---|---|---|
| 1 | `scrapers/linkedin.py` | **Pagination auto-calibrée** : `start += len(cartes reçues)` (au lieu de la constante 25) ; garde-fou `MAX_PAGES_PER_QUERY = 60` ; arrêt sur page déjà vue (stagnation) |
| 2 | `scrapers/linkedin.py` | **`id_externe`** extrait du bon nœud (`[data-entity-urn]`) → identifiant LinkedIn réel |
| 3 | `scrapers/linkedin.py` | **Déduplication inter-requêtes** (`seen_ids`) → plus d'objets en double entre requêtes cibles |
| 4 | `scrapers/linkedin.py` | Import inutilisé retiré, constante morte `RESULTS_PER_PAGE` supprimée, docstring complétée |
| 5 | `src/storage/database.py` | Nouveau filtre `sources=` sur `get_jobs()` + nouvelle méthode `get_source_counts()` |
| 6 | `src/constants.py` | `SOURCE_LABELS`, `SOURCE_COLORS`, `SOURCE_ORDER`, `source_label()`, `source_rank()` (alias `wttj`/`welcome_to_the_jungle` fusionnés) |
| 7 | `app.py` | **Filtre « Plateforme source »**, **badge de plateforme** sur chaque carte, **affichage « Groupé par plateforme »**, répartition en clair, rendu de carte extrait dans `render_job_card()` (code mort `config` retiré) |
| 8 | Tests | 6 cas LinkedIn (parsing, carte inexploitable, pas de pagination, stagnation, déduplication, repli 429) ; filtre source + `get_source_counts` ; `AppTest` vérifie la sidebar et l'affichage groupé |

### Preuves d'exécution

```
tests/test_scrapers.py  → TOUS LES TESTS PASSENT (EXIT=0)
   LinkedIn : parsing des cartes OK (urn, url, date, société)
   LinkedIn : carte inexploitable comptée pour la pagination OK
   LinkedIn : pas = taille réelle de page OK (aucune offre sautée)
   LinkedIn : arrêt sur page déjà vue OK
   LinkedIn : déduplication inter-requêtes OK
   LinkedIn : repli propre sur 429 OK
tests/test_database.py  → [OK] (dont filtre source)
tests/test_bridge.py    → [OK]
tests/test_app.py       → [OK] rendu + [OK] affichage groupé (71 offres, 0 exception)
```

Validation **live** (endpoint réel, `max_offers_per_source=20`) :

```
offsets demandés : [0, 10]          ← pas de 10, plus aucun saut d'offres
offres récupérées : 20 (found = 20)
ids uniques : 20
  - [4464330131] Stage - Data Scientist / Data Analyst (H/F) @ Sézane | Paris
  - [4464331502] STAGE - ENOVIA Brand Data Scientist (F/H) @ Dassault Systèmes | Vélizy-Villacoublay
  - [4390810974] Product Data Scientist Intern @ Criteo | Paris
```

### Ce qui est désormais possible dans le dashboard

- filtrer sur **une ou plusieurs plateformes** (LinkedIn / Welcome to the Jungle / JobTeaser) ;
- choisir **« Groupé par plateforme »** pour séparer visuellement les offres par site (sections + compteurs)
  tout en conservant, dans chaque section, le tri par score décroissant ;
- identifier la plateforme d'origine d'une offre via un **badge coloré** sur la carte ;
- visualiser la **répartition des offres en base** sous les métriques.

## 5. Améliorations proposées (priorisées)

### P1 — impact fort, effort modéré

| # | Proposition | Bénéfice | Effort |
|---|---|---|---|
| 1 | **Récupérer les descriptions LinkedIn** via l'endpoint détail invité `GET /jobs-guest/jobs/api/jobPosting/{id}` (vérifié : HTTP 200, `.description__text` présent) avec cache disque, plafond et délai | débloque le filtre anti-BI **et** le scoring sémantique/LLM sur les offres LinkedIn (corrige 3.3) | ~1 j |
| 2 | **Neutraliser le biais de scoring des offres sans description** : score sémantique de repli sur le titre seul, ou normalisation par source, ou `keywords_score` à courbe saturante (`min(n/5, 1)`) | classement comparable entre plateformes | ~0,5 j |
| 3 | **Brancher `config.yaml` sur `ScraperConfig`** (requêtes cibles, mots-clés d'exclusion/positifs, `max_offers_per_source`, timeout) | la configuration redevient réellement pilotable (corrige 3.4) | ~0,5 j |
| 4 | **Backoff exponentiel + `Retry-After`** sur 429/5xx, jitter, rotation de `User-Agent`, cache HTTP disque | augmente le taux de succès des runs longs sans agresser les serveurs | ~1 j |
| 5 | **Traçabilité des runs** : table `scrape_runs(source, started_at, found, inserted, status)` + log structuré | détecte immédiatement une source « morte » (ex. 0 offre LinkedIn) | ~0,5 j |

### P2 — qualité, robustesse, exploitation

| # | Proposition | Bénéfice |
|---|---|---|
| 6 | **Déduplication canonique** : normaliser l'URL à l'ingestion, ajouter une colonne `canonical_url` (index unique) et dédupliquer aussi par `(source, id_externe)` | supprime les doublons résiduels (corrige 3.7) |
| 7 | **Migration de normalisation** : `UPDATE jobs SET source='wttj' WHERE source='welcome_to_the_jungle'` + contrainte de domaine | une seule convention de source en base |
| 8 | **SQLite en WAL** (`PRAGMA journal_mode=WAL`, `busy_timeout=5000`) | supprime les `database is locked` quand dashboard et scrapers tournent ensemble |
| 9 | **Supprimer le code mort** : `src/ingestion/wttj.py` (API v1 disparue), `load_env_file` dupliqué (module unique), dépendances inutilisées du `requirements.txt` | maintenance et lisibilité |
| 10 | **`pytest` + CI** (GitHub Actions) avec marqueurs `slow`/`network`, `httpx.MockTransport` pour les appels LLM | exécution fiable en une commande, garde-fous de régression |
| 11 | **Initialiser Git** (`.gitignore` déjà présent pour `.env`, `.venv`, la base) | historique, revue, retour arrière |
| 12 | **Affiner les mots-clés** : retirer/pondérer `recherche` et `research`, exiger un signal *technique* (et non « nous recherchons ») | réduit les faux positifs (corrige 3.8) |

### P3 — produit / confort utilisateur

| # | Proposition | Bénéfice |
|---|---|---|
| 13 | Bouton **« Actualiser les offres »** dans le dashboard (déclenche `run_scrapers.py` en tâche de fond) | boucle d'usage complète sans ligne de commande |
| 14 | **Recherche plein texte** (FTS5 SQLite) + pagination/`limit` dans l'UI + **export CSV** | exploitation à plusieurs centaines d'offres |
| 15 | **Suivi d'activité** : compteurs par plateforme dans les métriques, « dernière collecte par source », graphiques de répartition | pilotage de la recherche |
| 16 | Stocker le **mode de candidature** (`Easy Apply` ↔ externe) via `public_jobs_apply-link-onsite` / `-offsite` de l'endpoint détail | distingue les candidatures LinkedIn des candidatures externes |

> ⚠️ La **plateforme ATS réelle** (Workday, Greenhouse, Lever…) n'est **pas** exposée en mode invité :
> vérifié, aucune URL externe ni `href` de candidature n'apparaît dans le HTML, et le lien est masqué
> derrière le mur de connexion. Seule la distinction *on-site / off-site* est accessible techniquement.

### P4 — conformité

| # | Proposition |
|---|---|
| 17 | Documenter explicitement l'usage **personnel** et le respect des CGU/`robots.txt` de chaque source |
| 18 | Privilégier les sources autorisées : intranet JobTeaser de l'école, index Algolia public WTTJ, API partenaires LinkedIn ; conserver un *rate limit* bas et un `User-Agent` identifiable |
| 19 | Ne pas stocker de données personnelles (recruteurs) — uniquement des données d'offres |

---

## 6. Commandes de vérification

```bash
# Tests rapides (sans réseau, sans torch)
python tests/test_scrapers.py     # scrapers : anti-BI, parsing, pagination, dédup, 429
python tests/test_database.py     # SQLite : upsert, filtres (dont sources), migration
python tests/test_bridge.py       # ingestion idempotente RawJob -> SQLite
python tests/test_app.py          # rendu Streamlit + affichage groupé par plateforme

# Tests lourds (téléchargement du modèle / réseau)
python tests/test_scorer.py
python tests/test_reranker.py

# Pipeline
python run_scrapers.py                    # collecte + ingestion SQLite
python run_scrapers.py --trigger-scoring  # + scoring Bi-Encoder des nouvelles offres
streamlit run app.py                      # dashboard
```

---

## 7. Synthèse

- Le projet est **fonctionnel et bien structuré** : séparation claire collecte / filtrage / ingestion /
  ranking / UI, typage Pydantic, repli défensif sur les erreurs réseau et LLM, tests présents.
- Les deux défauts **critiques** (pagination LinkedIn et `id_externe`) sont corrigés et **vérifiés en
  conditions réelles** : plus aucune offre n'est sautée.
- La séparation par plateforme est désormais disponible (filtre + affichage groupé + badges), ce qui
  rend la base exploitable par site comme demandé.
- Le risque principal restant est le **biais de scoring des offres sans description** (P1.1/P1.2) : c'est
  la prochaine action à plus fort rendement, car elle conditionne la qualité du classement des offres LinkedIn.

---

# 8. Itération 2 — exécution réelle de bout en bout & constats complémentaires

## 8.1 Ce qui a été exécuté

```bash
python tests/test_database.py     # OK
python tests/test_bridge.py       # OK
python tests/test_scrapers.py     # OK (16 cas dont 6 nouveaux LinkedIn)
python tests/test_reranker.py     # OK (7 cas, httpx.MockTransport : aucun appel réseau réel)
python tests/test_app.py          # OK (rendu + affichage groupé)
python tests/test_scorer.py       # OK (modèle réel : final=59.7, semantic=52.5)
python tests/test_cli.py          # OK (nouveau : options du pipeline)
python run_scrapers.py --rescore-all --trigger-rerank
python run_pipeline.py            # (avant correction : 404 sans aucune offre)
```

## 8.2 Preuve : `run_pipeline.py` était cassé (404)

```
[1/4] Ingestion des offres (Welcome to the Jungle)...
[wttj] L'endpoint WTTJ est introuvable (404). Vérifiez 'scraping.api_base' dans config.yaml.
[wttj] 0 offre(s) collectée(s).
       Aucune offre récupérée. Vérifiez la connexion ou config.yaml.
```

Conséquences : le **point d'entrée principal du README ne produisait rien**, et l'**étape 2 (reranking LLM)
ne pouvait jamais s'exécuter** (elle était appelée après un `return` anticipé).

## 8.3 Preuve : exécution réelle après correction

```
linkedin : 50 offre(s) récupérée(s) -> 35 validée(s), 15 rejetée(s)   ← 5 pages : start 0,10,20,30,40
jobteaser : scraper désactivé (cookies absents) — sans bloquer le pipeline
Fusion : 35 offre(s) unique(s) après déduplication
Ingestion SQLite : 35 collectée(s), 17 nouvelle(s), 18 doublon(s)
Total en base SQLite : 88        (linkedin 50 · jobteaser 38)
Scoring Bi-Encoder (global) : 88 offre(s) scorée(s)
Reranking ignoré : DEEPSEEK_API_KEY absente (.env)
Top 5 en base : [46.4] STAGE - Ingénieur.e Recherche Machine Learning (jobteaser)
                [44.6] STAGE - Ingénieur.e Recherche Deep Learning  (jobteaser)
                [44.0] Stage septembre 2026 - Data Science et ML   (linkedin)
```

➡️ Le correctif de pagination tient en production : **50 offres LinkedIn sur 5 pages** (contre 3 pages
avant, avec 15 offres sautées par page), et **88/88 offres sont désormais notées**.

## 8.4 Constats complémentaires (nouveaux)

| # | Constat | Gravité | Preuve |
|---|---|---|---|
| 20 | **`run_pipeline.py` cassé (404)** → README inopérant et étape 2 du ranking inatteignable | 🔴 | sortie ci-dessus |
| 21 | **Aucune description en base** : 71/71 puis 88/88 offres ont `description` vide — ce n'est pas propre à LinkedIn, les cartes JobTeaser n'en fournissent pas non plus | 🟠 | `select count(*) from jobs where description is null or description=''` → 71 avant run, 88 après |
| 22 | **Reranking jamais exécuté** (0 offre rerankée) faute de clé API **et** faute de point d'entrée fonctionnel | 🟠 | `rerank NOTNULL = 0` |
| 23 | **Historique non noté** : 66/71 offres à `final_score = 0` avant cette itération ; aucune option pour rattraper | 🟠 | `final_score > 0 : 5` |
| 24 | **WTTJ injoignable dans cet environnement** : `[Errno 11001] getaddrinfo failed` (DNS) sur `v3splegsrc-dsn.algolia.net`, alors que LinkedIn répond | 🟡 (env.) | logs du run |
| 25 | **Logs trop verbeux** : `logging.basicConfig(level=INFO)` laisse passer les logs `httpx`/HF (300+ lignes par run) | 🟡 | sortie du run |
| 26 | `keywords_score` saturé bas : 19 mots-clés → 3 présents = **15,8 %** (max jamais atteint) | 🟡 | `test_scorer` : `haut=15.8` |

## 8.5 Correctifs de l'itération 2

| # | Fichier | Correction |
|---|---|---|
| 9 | `run_scrapers.py` | **`--rescore-all`** : re-score de **toutes** les offres (rattrapage d'historique non noté) |
| 10 | `run_scrapers.py` | **`--trigger-rerank`** + **`--top-rerank N`** : l'étape 2 (juge LLM) devient accessible ; sans clé, avertissement clair et poursuite du pipeline |
| 11 | `run_scrapers.py` | Résumé enrichi : **répartition par plateforme** + **Top 5 en base** (score effectif, `rerank_score` prioritaire) |
| 12 | `run_pipeline.py` | Réécrit en **raccourci sans duplication** : `run_scrapers.main(["--trigger-scoring", "--trigger-rerank"])` — l'ancien scraper WTTJ v1 (404) est abandonné |
| 13 | `tests/test_cli.py` | **Nouveau** : options par défaut, options activées, et vérification que `run_pipeline` délègue bien (garde-fou anti-régression) |
| 14 | `README.md` | Commandes mises à jour (pipeline complet + les 4 options de ranking + `test_cli.py`) |

## 8.6 Améliorations concrètes, priorisées après exécution

### P1 — à faire en premier (impact direct sur la qualité du classement)

| # | Action concrète | Où | Effort |
|---|---|---|---|
| A | **Enrichir les descriptions** : LinkedIn via `GET /jobs-guest/jobs/api/jobPosting/{id}` (vérifié : HTTP 200, `.description__text` présent) et JobTeaser via la page détail `/fr/job-offers/{uuid}` (SSR). Cache disque `data/cache/`, 1 requête/offre, pause 1-2 s, plafond `enrich_max_offers`. | nouveau `scrapers/enrich.py` + appel dans `ScraperManager` | ~1 j |
| B | **Débiaiser le score sans description** : sémantique de repli sur `titre + entreprise + lieu` **et** `keywords_score` à courbe saturante (`min(n/5, 1) × 100` au lieu de `n/19 × 100`). Aujourd'hui « 3 mots-clés sur 19 » plafonne à 15,8 %. | `src/matching/scorer.py` | ~0,5 j |
| C | **Activer réellement l'étape 2** : `cp .env.example .env`, renseigner `DEEPSEEK_API_KEY`, puis `python run_scrapers.py --trigger-rerank --top-rerank 30` (coût DeepSeek ≈ quelques centimes pour 30 offres). | `.env` + commande | 5 min |
| D | **Piloter la collecte depuis `config.yaml`** : `ScraperConfig(**load_config().get("scrapers", {}))` avec `target_queries`, `max_offers_per_source`, `exclusion_keywords`, `positive_ds_ml_keywords` (l'édition du YAML n'a aujourd'hui aucun effet). | `run_scrapers.py` / `scrapers/models.py` | ~0,5 j |
| E | **Diagnostic réseau WTTJ** : pré-contrôle de connectivité + message d'erreur distinguant « DNS/hôte injoignable » (cas observé : `getaddrinfo failed`) d'une erreur Algolia, et hôte configurable. | `scrapers/wttj.py` | ~1 h |

### P2 — fiabilité et exploitation

| # | Action concrète | Effort |
|---|---|---|
| F | **Logs lisibles** : `logging.getLogger("httpx").setLevel(logging.WARNING)`, `TRANSFORMERS_VERBOSITY=error`, `HF_HUB_DISABLE_PROGRESS_BARS=1` (le run courant produit 300+ lignes de bruit). | 15 min |
| G | **Table `scrape_runs`** (`source, started_at, found, inserted, rejected, status, error`) + alerte si une source renvoie 0 → détecte immédiatement une panne silencieuse (WTTJ, cookies JobTeaser expirés). | ~0,5 j |
| H | **Backoff exponentiel + `Retry-After`** sur 429/5xx et cache HTTP disque (le run relance tous les appels à chaque fois). | ~1 j |
| I | **Déduplication canonique** : normaliser l'URL **avant** insertion + index unique sur `canonical_url`, dédup aussi sur `(source, id_externe)`. | ~0,5 j |
| J | **SQLite en WAL** (`PRAGMA journal_mode=WAL`, `busy_timeout=5000`) + migration `UPDATE jobs SET source='wttj' WHERE source='welcome_to_the_jungle'`. | ~0,5 j |
| K | **`pytest` + CI** (GitHub Actions) avec marqueurs `slow`/`network`, et **init Git** (le dépôt n'est pas versionné). | ~0,5 j |
| L | **Retirer le code mort** : `src/ingestion/wttj.py` (404), `load_env_file` dupliqué, dépendances inutilisées (`python-dotenv`, `pandas`). | ~1 h |

### P3 — produit

| # | Action concrète | Effort |
|---|---|---|
| M | Bouton **« Actualiser les offres »** dans le dashboard (lance `run_scrapers.main` en tâche de fond) → plus besoin de la ligne de commande. | ~0,5 j |
| N | **Recherche plein texte** (FTS5) + **export CSV** + pagination (`limit`) dans l'UI. | ~0,5 j |
| O | **Colonne `score_confidence`** (`haute` si description présente, `basse` sinon) affichée sur la carte → l'utilisateur voit si le score est fiable. | ~2 h |
| P | **Mode de candidature** (`Easy Apply` ↔ externe) via `public_jobs_apply-link-onsite` / `-offsite` de l'endpoint détail (la plateforme ATS réelle reste inaccessible). | ~0,5 j |

### P4 — conformité

| # | Action concrète |
|---|---|
| Q | Documenter l'usage **personnel**, le respect des CGU/`robots.txt`, un *rate limit* bas et un `User-Agent` identifiable ; privilégier les sources autorisées (intranet JobTeaser de l'école, index Algolia public WTTJ). |

### Effet attendu du lot P1 (ordre de grandeur)

- **Avant** : 88 offres, toutes avec `final_score` calculé *sans description* (sémantique ≈ score de titre),
  15/50 offres LinkedIn écartées par le filtre anti-BI **sur le titre seul**, 0 analyse LLM.
- **Après P1** : filtre anti-BI appliqué sur le texte réel (moins de faux rejets **et** moins de faux
  positifs « nous recherchons un stagiaire »), scores comparables entre plateformes, et un Top-N trié
  par un juge LLM — c'est-à-dire un classement réellement exploitable pour postuler.

---

# 9. Itération 3 — refonte de l'interface du dashboard (`app.py`)

## 9.1 Objectif

L'interface souffrait du syndrome « code généré » : emojis décoratifs sur chaque libellé
(`🔍`, `📄`, `🏢`, `🔗`, `🚀`), blocs Streamlit bruts, hiérarchie visuelle plate. La refonte vise un
outil d'ingénierie dense et sobre (références : Linear / Vercel / GitHub).

## 9.2 Ce qui a changé

| Élément | Avant | Après |
|---|---|---|
| Libellés | `🏢 Entreprise`, `🔗 Lien`, « Analyse du copilote » | typographie seule : entreprise en graisse 600, « Synthèse LLM », « Alignement vectoriel » |
| Métriques | 5 × `st.metric` + `st.caption` | bandeau KPI unique (4 cellules) : offres actives · R&D qualifiées (≥ 60) · rerankées LLM · répartition par plateforme (barre empilée + légende) |
| Cartes | colonnes + `st.container(border=True)` + 2 `st.expander` | carte HTML maîtrisée : titre 15,5 px, badge de score `84 / 100` + libellé d'alignement (*Cœur de cible*, *Pertinent*, *Secondaire*, *Hors périmètre*), badges de métadonnées, chips de technos (monospace), accordéon natif « Détails & évaluation » |
| Accordéon | 2 expanders (« Analyse du copilote », « Description ») | 1 `<details>` : verdict du juge LLM (points forts / points d'attention), scores internes, extrait de fiche + lecture complète |
| Actions | `selectbox` de statut | « Consulter l'offre ↗ » (lien direct dans la carte) + « Marquer postulé » / « Archiver » (`on_click`, persistance SQLite immédiate) |
| Filtres | score, statut, ESN, typologie, plateforme, mode | recherche plein texte, plateformes, score R&D minimal, verdict LLM uniquement, masquer les offres traitées (+ critères avancés : statut, typologie, ESN ; affichage : mode, limite) |
| Base | — | panneau « Base & pipeline » : répartition, « Actualiser la vue », « Relancer collecte & scoring » (journal diffusé en continu) → **P3.M réalisé** |

## 9.3 Décisions techniques

1. **Aucune variable CSS de thème disponible** : Streamlit 1.63 n'expose plus `--text-color`,
   `--background-color` ni `--secondary-background-color` (vérifié dans `static/css/index.*.css` et
   les bundles JS). La palette est donc générée en Python : `st.context.theme.type` (`dark` / `light`)
   sélectionne un jeu de jetons `--sc-*` (fond, bordures, texte atténué, tonalités `positive` /
   `accent` / `warn` / `alert` / `mute`), avec un repli `auto` (`color-mix(in srgb, currentColor …)`)
   si le type est indisponible (mode bare, tests).
2. **Feuille de style assemblée par `string.Template`** (`_CSS_TOKENS` + `_CSS_CHROME` +
   `_CSS_HEADER_KPI` + `_CSS_CARD`) : aucun échappement d'accolades, jetons substitués une seule fois.
3. **Carte = HTML autonome** : le contenu est injecté en un seul bloc (aucune ligne vide, sinon
   markdown-it clôt le bloc HTML) ; toutes les valeurs sont échappées (`html.escape`) et l'accordéon
   utilise `<details>` (accessible au clavier, sans widget supplémentaire).
4. **Lecture unique et cache maîtrisé** : `load_jobs(_db, data_version)` (`st.cache_data`,
   `ttl=DATA_CACHE_TTL` = 60 s) récupère la base complète triée par
   `COALESCE(rerank_score, final_score)` ; le filtrage est fait en Python (`filter_jobs`) et un
   compteur `data_version` en `session_state` invalide le cache après chaque changement de statut
   ou relance du pipeline → aucune requête inutile, sans jamais afficher un snapshot antérieur à une
   écriture externe (rerank en ligne de commande) au-delà d'une minute.
5. **Aucun emoji décoratif** : seuls des indicateurs fonctionnels subsistent (pastille de statut,
   pastille colorée par plateforme, flèche `↗` du lien). `VERDICT_EMOJI`, devenu inutile, est supprimé
   de `src/constants.py`.

## 9.4 Preuves d'exécution

```
python tests/test_app.py
  Helpers : score effectif et paliers d'alignement OK
  Helpers : texte, dates relatives, technologies et contrat OK
  Filtres : score, statuts, typologie, plateformes, recherche et répartition OK
  Carte : en-tête, jauge de score, badges, accordéon et CTA OK
  Palette : jetons complets pour les thèmes sombre, clair et « auto » OK
  Palette : détection du thème natif Streamlit OK
  Interface : 88 offre(s) en base, 50 carte(s) rendue(s) OK
  Interface : aucun emoji décoratif dans les libellés OK
  Interface : recherche sans résultat -> état vide OK
  Interface : recherche « nieur » -> 19 carte(s) cohérente(s) OK
  Interface : affichage groupé par plateforme OK
  Interface : bascule « Masquer les offres traitées » OK
TOUS LES TESTS PASSENT
```

`test_cli.py`, `test_bridge.py`, `test_database.py`, `test_scrapers.py` et `test_reranker.py`
passent également : **aucune régression** (le schéma et la couche d'accès aux données sont inchangés).

## 9.5 Limites connues (assumées)

- La recherche plein texte s'applique à la validation (`Entrée`) : Streamlit ne remonte pas la frappe
  caractère par caractère sur `st.text_input`.
- Les chips de technologies proviennent de `tech_stack` (rempli par le juge LLM) ; tant que l'étape 2
  n'a pas tourné, elles retombent sur la détection déterministe des `excellence_keywords` de
  `config.yaml` (**0 offre rerankée dans la base actuelle → chips issues des titres uniquement**).
- Aucune fiche de poste en base (`description` vide pour 88/88 offres, constat §8.4 #21) : la carte
  affiche « Fiche non fournie par la plateforme d'origine » au lieu d'un faux extrait.
- Le resserrement de l'écart entre une carte et sa barre d'actions s'appuie sur `:has()` ; si le
  navigateur ne le supporte pas, la mise en page reste correcte (espacement par défaut de Streamlit).






