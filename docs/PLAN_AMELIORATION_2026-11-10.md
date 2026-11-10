# Plan d'amélioration — 10/11/2026

> Mise à jour du 10/11 au soir : le run GitHub quotidien est abandonné (collecte à la main et par Antigravity). Le §4 ne s'applique plus pour l'étape 0 ; le coût DeepSeek est estimé au §6.

Suite de [AUDIT.md](../AUDIT.md). Ce document part de l'état réel du 10/11/2026 :
base locale (copie lue), journaux GitHub Actions, configuration et code.
Statut : ⏳ à faire · ✅ fait · 🧑 action d'Eddy (secrets, décision, commande sur la vraie base).

## 1. État des lieux chiffré

| Mesure | Valeur |
|---|---|
| Offres en base (`data/stage_copilot.db`) | 2 788 : 757 à trier, 952 exclues, 1 021 ignorées (dont 981 par l'ancien archivage par âge), 44 postulées, 2 entretiens, 12 refus |
| Offres « à trier » sans note v4 | 444 sur 757 |
| Doublons titre + entreprise | 184 (151 groupes) |
| Offres sans date de publication | 395 |
| Runs réellement enregistrés | 27/10 et 10/11, lancés à la main. Les runs GitHub quotidiens n'ont rien rapporté |
| Mission Antigravity en cours | 87 lots sur 91 rendus. Essai à blanc : 2 170 notes valides, 0 invalide, 412 offres fermées confirmées, 126 offres auto-archivées encore ouvertes, 11 nouvelles offres des grands groupes |
| Tests / lint | 518 tests verts, `ruff` propre |

## 2. Architecture proposée

On garde l'architecture actuelle, qui est saine. Les changements portent sur trois coutures : la CI, la version de la grille et la double voie de notation.

```
                 ┌──────────────── GitHub Actions (06:00 UTC) ────────────────┐
                 │ 1. pull R2 (obligatoire, sinon échec du job)               │
 Sources ──────► │ 2. collecte : LinkedIn, WTTJ, ATS, academic, Workday       │
 (scrapers/)     │ 3. filtres durs (contrat, mots-clés, durée) → EXCLU        │
                 │ 4. dédoublonnage (URL, id plateforme, puis entreprise+titre)│
                 │ 5. notation v4 en ligne (DeepSeek) de TOUTES les nouvelles │
                 │ 6. vivacité : offres fermées → IGNORÉ « offre_fermee »     │
                 │ 7. push R2 (fusion 3 voies, colonnes d'Eddy protégées)     │
                 └──────────────────────────┬─────────────────────────────────┘
                                            │ R2 (source de vérité partagée)
                 ┌──────────────────────────┴─────────────────────────────────┐
                 │ PC d'Eddy : python run_web.py                               │
                 │  FastAPI :8000 ◄──► Next.js :3000 (tri, notes, file /file)  │
                 │  sync R2 au démarrage et après chaque décision              │
                 └──────────────────────────┬─────────────────────────────────┘
                                            │ quand la grille change
                 ┌──────────────────────────┴─────────────────────────────────┐
                 │ Notation hors ligne Antigravity (tools/antigravity_v4.py)   │
                 │  export (lecture seule) → agents → ingest (sauvegarde+code) │
                 └────────────────────────────────────────────────────────────┘
```

Principes :

1. **R2 est la source de vérité.** La CI et le PC fusionnent toujours, sans jamais écraser (déjà en place : `db_merge.py` fusionne toutes les tables en 3 voies et protège les colonnes d'Eddy).
2. **Deux voies de notation, un seul format.** DeepSeek note au fil de l'eau ; Antigravity renote en masse quand la grille change. Les deux produisent le même `evaluation` (schéma v4), et la note finale est toujours recalculée en code (`compute_final_score_v4`).
3. **Nouveau : version de grille.** Chaque note porte `v4_grid_version` (hash court du prompt système sans le bloc de calibration). « Renoter » veut dire noter les offres dont la version diffère de la grille courante. Plus besoin de tout renoter à l'aveugle.
4. **Un échec doit se voir.** Un run sans R2 ou sans clé LLM échoue en rouge au lieu d'afficher « success ».

## 3. Problèmes et solutions

### CI et synchronisation

| # | Problème | Solution | Statut |
|---|---|---|---|
| P1 | La CI tournait sur le code du 03/11 (35 commits non poussés) : LinkedIn + WTTJ seulement, archivage par âge encore actif | `git push` | ✅ poussé le 10/11 |
| P2 | Secrets GitHub absents (R2, DeepSeek, Gemini) : chaque run part d'une base vide, ne note rien et jette son résultat | Ajouter les 6 secrets (commandes en §5) après avoir poussé la base locale sur R2 | ❌ abandonné le 10/11 (Eddy lance la collecte à la main et avec Antigravity) |
| P3 | Le job affiche « success » alors qu'il n'a rien sauvegardé | Dans `run_pipeline.py` : si `GITHUB_ACTIONS` est défini et que R2 n'est pas configuré ou que le pull échoue → `sys.exit(1)`. Même chose si aucune clé LLM n'est disponible | ❌ abandonné (run GitHub hors sujet) |
| P4 | `requirements.txt` installe Streamlit et toute l'UI en CI (environ 20 s, plus de surface) | Créer `requirements-pipeline.txt` (scrapers, LLM, boto3) utilisé par le workflow | ❌ abandonné |

### Notation

| # | Problème | Solution | Statut |
|---|---|---|---|
| N7 | 444 offres à trier sans note : `ranking.top_n_rerank: 20` par run, pour environ 400 nouvelles | Noter toutes les nouvelles offres qui passent les filtres durs : `ranking.top_n_rerank` passé de 20 à 500 (plafond de sécurité) | ✅ |
| N8 | Impossible de savoir quelles notes sont périmées quand la grille change | Colonne `v4_grid_version` (migration), remplie par `update_v4` ; `antigravity_v4.py export --stale` n'exporte que les offres à version différente ou sans note | ✅ |
| N9 | Le retard actuel (offres arrivées après l'export du 04/11) et la fin de la mission en cours | Finir la mission en cours, ingérer, puis vague 2 sur les offres manquantes (prompt : [PROMPT_ANTIGRAVITY_renotation_complete.md](PROMPT_ANTIGRAVITY_renotation_complete.md)) | ⏳ / 🧑 |
| N10 | `antigravity_v4.py export` supprime `in/lot_*.json` : relancer l'export pendant une mission détruit ses entrées | Option `--dir` (dossier de vague, ex. `data/antigravity/vague2`), refus d'exporter si le dossier contient des lots non ingérés (`--force` pour passer outre ; l'ingestion réelle pose un marqueur `out/.ingere`) | ✅ |
| N11 | Boucle de retour inutilisée (0 correction, 40 ignorées sans raison) : impossible de dire si la v4 bat la v3 | Côté code, rien de plus. Côté Eddy : 10 minutes par semaine pour donner une raison à chaque « ignorer » et corriger 5 notes, puis `python tools/eval_feedback.py` | 🧑 |
| N12 | 25 offres classées A/B restent IGNORÉ (ancien archivage par âge) | Couvert par l'ingestion Antigravity : les offres encore ouvertes repassent en NOUVEAU (126 dans l'essai à blanc) | ⏳ (avec N9) |

### Scraping

| # | Problème | Solution | Statut |
|---|---|---|---|
| S7 | Les requêtes de niche (« stage LLM », « research intern », « stage R&D IA ») ne tournent jamais : l'objectif de 150 nouvelles par source est atteint dès les 2 ou 3 premières requêtes | Plafond par requête : `passes.freshness.max_offers_per_query: 25` et `relevance: 10`. Les 20 requêtes se partagent l'objectif. Mesurer ensuite le nombre de requêtes lancées par run dans `scrape_query_stats` | ✅ réglé (freshness 20, relevance 8, 10 pages max) ; mesure au prochain run |
| S8 | LinkedIn renvoie des 429 en série et deux requêtes atteignent le plafond de 20 pages | Attente après un 429 sans `Retry-After` : 8 s, 16 s puis 30 s (2 s avant) ; fiches LinkedIn espacées de 2 à 4 s ; 10 pages max par requête | ✅ |
| S9 | 395 offres sans date de publication | Vérifié : 331 des 395 sont d'anciennes offres JobTeaser (source coupée), 44 LinkedIn de septembre, 20 Inria. Les fiches Inria n'affichent aucune date de publication ; l'interface montre déjà « Collectée il y a… » | ❌ rien à coder |
| S10 | Safran, Schneider, Stellantis, EDF, TotalEnergies sans source automatique | Mission « collecteur » Antigravity une fois par mois (incluse dans le prompt). Le code n'est pas rentable pour 5 sites aux structures différentes | 🧑 |

### Stockage

| # | Problème | Solution | Statut |
|---|---|---|---|
| D1 | 184 doublons entreprise + titre (la même offre sur LinkedIn et WTTJ, ou republiée) | À l'ingestion : clé `company_key` (déjà utilisée par le front) + titre normalisé. Un doublon n'est pas supprimé : il reçoit `duplicate_of = <id canonique>` et sort de la liste « à trier ». On garde l'offre la plus complète (description la plus longue, source prioritaire WTTJ > ATS > LinkedIn). Script ponctuel pour l'existant, sur copie puis vraie base après sauvegarde | ✅ fait autrement : la plupart des 184 sont de vraies offres distinctes (même titre, autre sujet ou autre ville). Une republication = même entreprise, même titre, même ville et texte complet similaire à 92 % : ignorée à l'ingestion, et `tools/dedupe_reposts.py` range l'existant (46 au 10/11, dont 10 à trier) |
| D2 | `CLAUDE.md` protège `data/jobs.db`, un fichier vide ; la vraie base est `data/stage_copilot.db` | Corriger la règle critique dans `CLAUDE.md` et supprimer `data/jobs.db` (0 octet, garde-fou de tests conservé) | ✅ `CLAUDE.md` corrigé ; 🧑 `data/jobs.db` à supprimer au nettoyage |
| D3 | `data/` encombré (`.bak`, `backup_20260930.db`, fichiers `-shm`/`-wal` orphelins) et 206 Mo dans `data/backups/` sans rotation | Déplacer les vieilles copies dans `data/backups/`. Rotation : garder les 10 sauvegardes les plus récentes plus une par mois (R2 garde déjà ses propres sauvegardes) | ✅ `tools/rotate_backups.py` (essai à blanc par défaut) ; 🧑 ranger les `.bak` de `data/` |
| D4 | `src/storage/database.py` fait 2 000 lignes | Plus tard : découper en `jobs.py`, `telemetry.py`, `migrations.py` sans changer l'API publique. Pas prioritaire | ⏳ (faible) |

Correction de mon rapport précédent : la fusion R2 couvre bien toutes les tables (`scrape_runs` et `seen_jobs` compris), il n'y a rien à corriger de ce côté.

### Interface et déploiement

| # | Problème | Solution | Statut |
|---|---|---|---|
| U7 | `render.yaml` déploie encore l'ancienne interface Streamlit ; `app.py`, `app_pages/`, `components/`, `utils/` sont maintenus pour rien | Décision d'Eddy : (a) garder Render comme accès mobile en lecture seule et geler Streamlit, ou (b) supprimer Streamlit et Render (branche + PR, car suppression massive). Recommandé : (a) tant que le front Next.js n'est pas hébergé | 🧑 |
| U8 | Petits manques du front (onglet « 60 et plus », commentaire laissé après « annuler », pas de « Remettre à trier » dans Exclues) | Onglet « 60 à 79 » (paramètre `max_score`), bouton « Remettre à trier » sur une offre exclue (raison `remis_a_trier`, résiste à une renotation), commentaire effacé quand on quitte IGNORÉ | ✅ |

### Confidentialité

| # | Problème | Solution | Statut |
|---|---|---|---|
| X1 | `data/profil_lettre.example.yaml` reprend le vrai parcours d'Eddy (écoles, stage UPC, engagements), avec seulement le nom remplacé ; le dépôt est **public** et le fichier est poussé depuis le 10/11 | Remplacer par un profil fictif. Le fichier reste dans l'historique Git : si c'est gênant, rendre le dépôt privé (gratuit, GitHub Actions reste gratuit dans les limites du plan) plutôt que réécrire l'historique | ✅ profil fictif ; 🧑 dépôt privé ou non |

## 4. Plan d'implémentation

Chaque étape : tests verts (`pytest`) et `ruff check .`, puis commit sur `main`, puis push. Aucune étape n'écrit dans `data/stage_copilot.db` sauf les étapes marquées 🧑.

**Étape 0 — Remettre la CI en marche (jour 1)**
1. ✅ Pousser `main`.
2. ⏳ P3 : garde-fou d'échec CI (test : `GITHUB_ACTIONS=1` sans R2 → code de sortie 1).
3. 🧑 Pousser la base locale à jour sur R2, puis ajouter les secrets (§5).
4. 🧑 Lancer le workflow à la main (`gh workflow run daily_scraper.yml`), vérifier dans le journal « Mémoire de collecte préchargée : » supérieur à 0 et « Notation v4 : N offres ».
5. 🧑 `python scripts/sync_db.py --pull` sur le PC pour récupérer les offres du run.

**Étape 1 — Notation complète (jours 1 à 3)**
1. 🧑 Finir la mission Antigravity en cours (lots 088 à 091 et les 6 lots en désaccord), puis `tools/antigravity_v4.py ingest --dry-run`, puis `ingest`.
2. ⏳ N10 : `export --dir` et protection des lots non ingérés.
3. ⏳ N8 : colonne `v4_grid_version` + `export --stale`.
4. 🧑 Vague 2 Antigravity avec le nouveau prompt, sur `--stale`.
5. ⏳ N7 : notation de toutes les nouvelles offres en ligne, avec plafond de sécurité.

**Étape 2 — Collecte plus large (jour 4)**
1. ⏳ S7 : plafonds par requête dans `config.yaml`, puis mesure sur un run (nombre de requêtes réellement lancées, part de classes A/B parmi les nouvelles).
2. ⏳ S8 : rythme LinkedIn et arrêt sur 429.
3. ⏳ S9 : date de publication LinkedIn + rattrapage sur le cache.

**Étape 3 — Base propre (jour 5)**
1. ⏳ D1 : `duplicate_of` + dédoublonnage à l'ingestion + script de rattrapage (essai sur copie, puis 🧑 sur la vraie base après sauvegarde).
2. ⏳ D2, D3 : `CLAUDE.md`, nettoyage de `data/`, rotation des sauvegardes.
3. ⏳ X1 : profil d'exemple fictif.

**Étape 4 — Finitions (quand le reste tourne)**
1. 🧑 U7 : décision Streamlit / Render.
2. ⏳ U8, P4, D4.
3. 🧑 N11 : une semaine de retours, puis `tools/eval_feedback.py` pour comparer v3 et v4.

## 5. Commandes pour Eddy (PowerShell)

Pousser d'abord la base locale sur R2, pour que la CI parte de la version à jour :

```powershell
.venv\Scripts\python.exe scripts\sync_db.py --status
.venv\Scripts\python.exe scripts\sync_db.py --push
```

Ajouter les secrets (chaque commande demande la valeur, à coller depuis ton `.env`) :

```powershell
gh secret set R2_ACCOUNT_ID
gh secret set R2_ACCESS_KEY_ID
gh secret set R2_SECRET_ACCESS_KEY
gh secret set R2_BUCKET_NAME
gh secret set DEEPSEEK_API_KEY
gh secret set GEMINI_API_KEY
```

Lancer un run de vérification :

```powershell
gh workflow run daily_scraper.yml
gh run watch
```

## 6. Coût DeepSeek estimé

Mesures sur la base du 10/11 : prompt système v4 d'environ 11 200 caractères (stable d'un appel à l'autre, donc mis en cache par DeepSeek), description médiane de 3 300 caractères, réponse JSON d'environ 1 500 caractères. Soit environ 3 500 jetons en cache, 1 100 jetons hors cache et 600 jetons de sortie par offre.

| Tarif `deepseek-chat` (par million de jetons) | Par offre | 1 000 offres | Base entière (~2 800) |
|---|---|---|---|
| V4 Flash, heures pleines : 0,30 $ entrée, 0,006 $ en cache, 1,20 $ sortie | ~0,11 centime | ~1,1 $ | ~3 $ |
| Même tarif sans cache (pire cas) | ~0,21 centime | ~2,1 $ | ~6 $ |
| Heures creuses (moitié prix) | ~0,06 centime | ~0,6 $ | ~1,6 $ |

Les tarifs viennent de comparateurs tiers et ne concordent pas tous : vérifier sur la page officielle de DeepSeek avant un gros lot. Ordre de grandeur sûr : moins de 0,5 centime par offre. Un run de 400 nouvelles offres coûte moins de 1 $.
