# Diagnostic : sources à 0 carte et faible volume WTTJ (30/09/2026)

Sources des données : base locale `data/stage_copilot.db` (runs des 27 et 28/09) et sondes en direct du 30/09, en lecture seule.

## Constat préalable : la télémétrie locale mesure l'ancienne configuration

Les runs locaux des 27 et 28/09 utilisent encore les **5 anciennes requêtes** (« Stage Data Scientist », « Stage Data Science », « Stage Machine Learning », « Stage Recherche IA », « Data Science Recherche »). Le checkout principal est en retard sur `origin/main`, qui contient les 10 nouvelles requêtes sans « Stage ». Tous les chiffres d'efficacité cités dans le plan portent donc sur l'ancienne configuration. Le premier run du cron (Task 5) servira de vraie référence.

## Passes à 0 carte (runs des 27 et 28/09)

| Source | Cas | Cause | Classe |
|---|---|---|---|
| LinkedIn | 2 passes, 0 requête HTTP réussie | HTTP 429 (quota plateforme) | blocage temporaire, désormais couvert par le retry |
| WTTJ / JobTeaser | 2 passes, 0 requête | objectif de la source déjà atteint, requête non lancée | normal |
| **JobTeaser** | **les 10 passes du 28/09**, 1 requête chacune, `stream_end` « plus de résultats (page 1) » | **session expirée** | (d) cookies expirés |

### JobTeaser : preuve

Sonde en direct du 30/09 (`tools/probe_sources.py --source jobteaser --query "Data Scientist"`, depuis le checkout principal qui a le `.env`) :
- HTTP 200, 21 548 caractères, 0 carte `jobad-card`.
- Titre de la page : « Bienvenue sur votre Career Center | JobTeaser ». C'est la page d'accueil et de connexion, pas une page de challenge Cloudflare (aucune trace de « Just a moment » ni de `cf-`).

Le scraper interprète cette page comme « fin de flux » : **la panne est silencieuse**. L'alerte « source dégradée » de la branche la détecterait maintenant (0 carte contre un médian d'environ 500), mais sans en donner la cause.

## WTTJ : volume limité par le marché, pas par le scraper

Nombre de résultats Algolia (index `wttj_jobs_production_fr`) le 30/09 :

| Requête | Stage (tous pays) | Stage, France | Tous contrats |
|---|---|---|---|
| Data Scientist | 62 | 44 | 1 147 |
| Machine Learning | 164 | 113 | 2 666 |
| NLP | 18 | 11 | 223 |
| LLM | 106 | 68 | 2 525 |

Il y a peu de stages sur WTTJ, et les 132 offres en base sont cohérentes avec ce vivier. En revanche, **environ 30 % des stages renvoyés sont hors France** : le scraper ne filtre pas le pays.

## Recommandations (à valider avant tout code)

1. **JobTeaser, immédiat (action de ta part)** : renouveler les cookies, avec `scripts/fetch_jobteaser_cookie.py` ou à la main dans `.env`, et dans les secrets GitHub `JOBTEASER_*`.
2. **JobTeaser, code** : détecter la page de connexion (titre « Career Center » sans carte, ou redirection vers `sign_in`) et arrêter la passe avec le motif existant `auth_missing` au lieu de `stream_end`. La panne devient explicite et la cause apparaît dans l'alerte. Environ 1 tâche TDD.
3. **JobTeaser dans le cron** : les cookies expirent régulièrement (cf. `JOBTEASER_CF_CLEARANCE`, lié à l'IP). Avec la recommandation 2, le job passe en rouge avec une cause claire, ce qui est acceptable. Sinon, retirer `jobteaser` des sources du cron.
4. **WTTJ** : ajouter le filtre `offices.country_code:FR` (réglable) pour éliminer les stages étrangers, qui sinon coûtent du scoring LLM pour rien. Gain faible, mais le changement tient en une ligne.
5. **Mettre à jour le checkout principal** (`git pull`, après avoir mis de côté le travail non commité) pour que les runs locaux utilisent la configuration actuelle.
