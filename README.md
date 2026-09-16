# stage_copilot 🎯

Pipeline automatisé de **scraping**, **scoring multicritère** et **tableau de bord
Streamlit** pour postuler massivement et efficacement à des stages de fin d'études
en Data Science / ML / Data Engineering en Île-de-France.

## Architecture

```
Assistant_recherche_de_stage/
├── config.yaml              # Filtres durs, listes d'entreprises, coefficients
├── requirements.txt
├── run_scrapers.py          # Scrapers unifiés → ingestion SQLite (+ --trigger-scoring)
├── run_pipeline.py          # Ingestion historique → scoring → base SQLite + récap
├── app.py                   # Dashboard Streamlit (KPI, cartes scorées, filtres, pipeline)
├── AUDIT.md                 # Audit technique : constats, méthode de review, plan d'amélioration
├── data/
│   ├── cv_eddy.txt          # CV au format texte (à remplacer par le vrai CV)
│   └── stage_copilot.db     # Base SQLite (générée)
├── scrapers/                # Module de scraping unifié (WTTJ, LinkedIn, JobTeaser)
│   ├── models.py            # RawJob + ScraperConfig + passes (PassConfig/PassPlan) + télémétrie
│   ├── known.py             # KnownIndex : mémoire de collecte (arrêt anticipé, dédup transverse)
│   ├── base.py              # BaseScraper : moteur de collecte hybride + filtre anti-BI
│   ├── wttj.py              # Welcome to the Jungle (API Algolia)
│   ├── linkedin.py          # LinkedIn invité (BeautifulSoup) + filtre temporel f_TPR
│   ├── jobteaser.py         # JobTeaser intranet (curl_cffi + cookies)
│   └── manager.py           # Orchestrateur + déduplication URL + fusion de la télémétrie
├── tools/
│   └── probe_sources.py     # Sonde lecture seule (tri/filtre temporel/pagination des sources)
└── src/
    ├── config.py            # Chargement YAML
    ├── constants.py         # Statuts, tiers, libellés, motifs d'arrêt
    ├── ingestion/
    │   ├── wttj.py          # ⚠️ OBSOLÈTE (API v1 supprimée → 404) — non utilisé
    │   ├── bridge.py        # Pont RawJob → table SQLite (idempotent, URL canonique)
    │   └── known_index.py   # Implémentation SQLite de la mémoire de collecte
    ├── matching/scorer.py   # Scoring hybride 0-100
    └── storage/database.py  # ORM SQLAlchemy (jobs, seen_jobs, scrape_runs, scrape_query_stats)
```

## Collecte hybride (Fraîcheur + Pertinence)

Chaque requête cible est collectée **deux fois**, avec deux intentions opposées :

| Passe | Tri | Fenêtre | Arrêt | Objectif |
|---|---|---|---|---|
| **Fraîcheur** | date (`sortBy=DD`, `f_TPR` LinkedIn) | 7 j (ou 1 j) | **arrêt anticipé** dès N offres consécutives déjà connues | capter 100 % des nouveautés pour postuler immédiatement |
| **Rattrapage** | pertinence (classement de la plateforme) | aucune | quota ou épuisement de la pagination, **jamais** d'arrêt anticipé | récupérer les offres à fort matching, même anciennes |

Invariants garantis par le moteur (`scrapers/base.py`), identiques pour toutes les sources :

1. **Déduplication transverse** : une offre collectée en « Fraîcheur » n'est ni
   retraitée ni comptée en « Rattrapage » (ensemble de clés propre au run + mémoire de collecte) ;
2. **Les offres connues ne consomment pas le quota** ; elles alimentent au contraire
   le compteur d'arrêt anticipé (N connues **consécutives**, la moindre inconnue le remet à zéro) ;
3. **Le plafond de source est appliqué pendant les passes** (`max_offers_per_source`) : il
   borne au plus juste le nombre de requêtes HTTP ;
4. **Garde-fou d'ordre** : l'arrêt anticipé n'est activé que si la source garantit un flux
   trié. Mesuré par sonde le 16/09/2026 : LinkedIn ne le garantit pas (7 inversions de date
   sur 2 pages, `sortBy=DD` sans filtre temporel renvoie le même ordre que la pertinence) →
   le seuil est neutralisé et **consigné** ; la fraîcheur est alors garantie par le filtre
   temporel **serveur** `f_TPR` (vérifié : 10/10 cartes publiées le jour même avec `r86400`).

### Pourquoi une mémoire de collecte (`seen_jobs`) ?

Une offre écartée par le filtre anti-BI n'est jamais écrite dans `jobs`. Sans mémoire, la
passe Fraîcheur la redécouvrirait à chaque run et n'atteindrait jamais N offres
**consécutives** connues : l'arrêt anticipé serait inopérant. `seen_jobs` enregistre donc
**toutes** les cartes croisées (retenues, rejetées BI, hors fenêtre, déjà connues) avec leur
décision. La colonne `decision` est **purement informative** : elle ne filtre jamais
l'ingestion.

### Observabilité : savoir si du flux a été perdu

Chaque passe produit une ligne de télémétrie avec sa **raison exacte d'arrêt** :

| Motif | Interprétation |
|---|---|
| `early_stop` | jonction avec le scrape précédent (aucune perte, requêtes économisées) |
| `window_end` / `stream_end` | vivier épuisé (fenêtre temporelle ou fin de résultats) |
| `quota` | quota de la passe ou plafond de source atteint (le flux continue au-delà) |
| `duplicate_page` | page sans carte inédite (pagination stagnante) |
| `rate_limit`, `http_error`, `network_error`, `max_pages` | **flux potentiellement perdu** → alerte explicite dans le journal |
| `auth_missing`, `disabled`, `unsupported` | passe non exécutée (cookies absents, pass désactivée…) |

Les tables `scrape_runs` (un run) et `scrape_query_stats` (une passe) conservent l'historique
(180 j par défaut), consultable par `--top-telemetry N`.

### Pilotage (`config.yaml → scrapers.passes`)

```yaml
passes:
  freshness:
    sort: "date"            # tri par date
    max_offers_per_query: 40
    window_days: 7          # 1 = 24 h ; null = aucune fenêtre
    early_stop_after_known: 5   # N connues consécutives ⇒ arrêt
    early_stop_min_pages: 1
    max_pages_per_query: 12
    use_server_window_filter: true   # LinkedIn : f_TPR=r<secondes>
  relevance:
    sort: "relevance"
    max_offers_per_query: 20
    early_stop_after_known: 0   # jamais d'arrêt anticipé
```

Une section `passes` déclarée est **explicite** : un mode absent n'est pas exécuté.
La déclaration est entièrement pilotable par YAML (quotas, fenêtres, seuils d'arrêt).

```bash
python run_scrapers.py                          # hybride : fraîcheur puis rattrapage
python run_scrapers.py --passes freshness        # une seule passe (diagnostic)
python run_scrapers.py --only-source linkedin    # une seule source
python run_scrapers.py --top-telemetry 20        # dernières raisons d'arrêt tracées
python tools/probe_sources.py --source linkedin  # sonde lecture seule (paramètres réels)
```


## Installation

```bash
python -m pip install -r requirements.txt
```

> `requirements.txt` déclare l'index PyTorch **CPU** (léger, suffisant pour
> l'inférence de `sentence-transformers`).

## Utilisation

1. **Remplacer le CV** : éditez `data/cv_eddy.txt` avec votre vrai CV.
2. **Ajuster la config** : éditez `config.yaml` (mots-clés, entreprises, poids).
3. **Lancer le pipeline complet** (collecte → scoring → reranking) :
   ```bash
   python run_pipeline.py
   # équivalent explicite (mêmes étapes, options visibles) :
   python run_scrapers.py --trigger-scoring --trigger-rerank
   ```
4. **Ouvrir le dashboard** :
   ```bash
   streamlit run app.py
   ```

## Scrapers unifiés & ingestion SQLite

```bash
python run_scrapers.py                       # collecte + ingestion SQLite (idempotente)
python run_scrapers.py --trigger-scoring     # + scoring Bi-Encoder des nouvelles offres
python run_scrapers.py --rescore-all         # + re-scoring de TOUTES les offres en base
python run_scrapers.py --trigger-rerank      # + juge LLM (DeepSeek) du Top-N non analysé
python run_scrapers.py --rescore-all --trigger-rerank --top-rerank 50   # chaîne complète
```

- `ScraperManager` lance **WTTJ** (Algolia), **LinkedIn** (invité) et **JobTeaser**
  (intranet), applique le **filtre anti-BI** (`BaseScraper.is_valid_job`) puis
  déduplique sur l'URL canonique.
- `src.ingestion.bridge.ingest_raw_jobs` convertit les `RawJob` en lignes de la
  table `jobs` en ignorant les doublons (par identifiant **et** URL) et retourne
  `{"total_scraped", "new_inserted", "duplicates_skipped"}`.
- `--trigger-scoring` calcule `all-MiniLM-L6-v2` **uniquement** sur les offres
  nouvellement insérées (aucune réévaluation des offres déjà en base).

### Pagination LinkedIn (corrigée)

L'endpoint invité renvoie **10 cartes par appel** (et non 25). Le scraper avance désormais du
**nombre réel de cartes reçues** (`start += len(cartes)`) et s'arrête si une page ne contient
que des offres déjà vues : **aucune offre n'est plus sautée**, sans risque de boucle infinie.
L'`id_externe` est lu sur le bon attribut (`data-entity-urn`) et les offres communes à
plusieurs requêtes cibles sont dédupliquées.

### Filtrer / séparer les offres par plateforme

Le dashboard exploite la colonne `jobs.source` :

- filtre **« Plateformes »** dans la sidebar (avec le nombre d'offres par site) ;
- **badge de plateforme** sur chaque carte (LinkedIn / Welcome to the Jungle / JobTeaser) ;
- affichage **« Groupé par plateforme »** : une section par site, triée par score à l'intérieur ;
- **répartition** dans la 4ᵉ cellule du bandeau KPI (barre empilée + légende).

## Interface du dashboard

- **Bandeau KPI** : offres actives · offres R&D qualifiées (score ≥ 60) · offres rerankées par le juge
  LLM · répartition par plateforme.
- **Cartes d'offres** : score R&D (`84 / 100`) avec libellé d'alignement (*Cœur de cible*, *Pertinent*,
  *Secondaire*, *Hors périmètre*), entreprise · ville · date relative, badges (plateforme, contrat,
  typologie, verdict LLM), chips de technologies, accordéon **« Détails & évaluation »** (points forts,
  points d'attention, scores internes, extrait de fiche de poste) et actions (`Consulter l'offre ↗`,
  `Marquer postulé`, `Archiver`).
- **Filtres latéraux** : recherche plein texte, plateformes, score R&D minimal, verdict LLM uniquement,
  masquer les offres traitées, critères avancés (statut, typologie d'entreprise, ESN), mode de flux et
  limite d'offres affichées.
- **Base & pipeline** : « Actualiser la vue » et « Relancer collecte & scoring » (le journal du pipeline
  est diffusé en continu dans la sidebar).
- **Thème** : l'interface suit automatiquement le mode clair ou sombre natif de Streamlit
  (`st.context.theme`), et n'utilise **aucun emoji décoratif**.

> ⚠️ Les cartes LinkedIn invitées n'exposent pas la description : le filtre anti-BI et le score
> sémantique s'appuient alors principalement sur le titre. Voir `AUDIT.md` §3.3 et la
> proposition P1.1 (récupération des descriptions via l'endpoint détail invité).

### JobTeaser (intranet école)
Cloudflare protège `emse.jobteaser.com` : une requête `httpx` reçoit un `403`.
Renseignez vos cookies dans `.env` (voir `.env.example`) ; le scraper utilise
`curl_cffi` (impersonation TLS, `impersonate="chrome"`) pour récupérer la page
`/fr/job-offers` puis parse les cartes `data-testid="jobad-card*"`.

```bash
JOBTEASER_COOKIES=...   # chaîne cookie complète (DevTools > Network > Cookie)
```

Sans `curl_cffi`, il retombe sur `httpx` (souvent `403`) sans bloquer le pipeline.

## Architecture de ranking en 2 étapes

### Étape 1 — Retrieval rapide (Bi-Encoder)
Score hybride 0-100 sur **toutes** les offres :
```
final = 0.60 * similarité_sémantique (all-MiniLM-L6-v2)
      + 0.25 * score_typologie (Tier 1 > Grand groupe > ESN)
      + 0.15 * mots_clés_d'excellence (PyTorch, GNN, Docker, ...)
```

### Étape 2 — Deep Reranking (LLM-as-a-Judge)
Sur le **Top N** (`ranking.top_n_rerank`, défaut 20) des offres non encore
évaluées, un LLM **DeepSeek** (`deepseek-chat`) recalcule un `rerank_score`
(0-100) et produit une synthèse critique :
- `verdict` : EXCELLENT | BON | MITIGÉ | HORS_SUJET
- `match_reasons` (points forts), `red_flags` (alertes), `tech_stack`

Le but est d'**éliminer les faux positifs** (ex. offres de reporting
Excel/PowerBI déguisées en Data Science) que le bi-encoder seul ne détecte pas.

### Clé API
```bash
cp .env.example .env      # puis renseignez DEEPSEEK_API_KEY
```
Sans clé, l'étape 2 est simplement ignorée (l'étape 1 reste pleinement
fonctionnelle) ; en cas d'erreur API, le juge retombe automatiquement sur le
score initial (fallback défensif).

### Tests
```bash
python tests/test_database.py   # couche SQLite + migration
python tests/test_scorer.py     # étape 1 (bi-encoder + tiering)
python tests/test_reranker.py   # étape 2 (juge LLM) + persistance
python tests/test_app.py        # dashboard : rendu, filtres, cartes, palettes (thème clair/sombre)
python tests/test_scrapers.py   # scrapers : filtre anti-BI + parsing JobTeaser
python tests/test_hybrid_collection.py  # collecte hybride : arrêt anticipé, passes, motifs d'arrêt
python tests/test_bridge.py     # pont d'ingestion RawJob → SQLite (idempotence)
python tests/test_cli.py        # options CLI du pipeline (trigger-scoring, rescore-all, rerank, passes)
python tools/validate_hybrid_run.py     # validation bout-en-bout (2 runs réels, base temporaire)
python tools/probe_sources.py --source linkedin   # sonde lecture seule (tri/filtre/pagination)
```

Les poids, listes d'entreprises et paramètres LLM sont entièrement
configurables dans `config.yaml`.
