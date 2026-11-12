# Assistant recherche de stage

App Web Next.js (port 3000) + FastAPI (port 8000) + SQLite de suivi de candidatures : scrapers, scoring des offres par LLM v4, sync R2. L'ancienne interface Streamlit (app.py) est dépréciée.

## Règles critiques
- **Ne jamais écrire dans `data/stage_copilot.db`** (la vraie base ; `data/jobs.db` est un ancien fichier vide) depuis des tests ou scripts d'essai : cette base contient les scores LLM, coûteux à refaire (déjà perdus une fois). Tests et essais passent par une copie temporaire (`tmp_path` pytest).
- Avant toute opération qui modifie la DB réelle : copie dans `data/backups/`.
- Ne pas écraser les statuts saisis par l'utilisateur (voir `tests/test_status_protection.py`).
- Avant la sync R2 : fusionner avec les données distantes, ne pas écraser.

## Commandes
- Tests : `pytest` (config dans `pytest.ini`).
- Lint : `ruff check .`
- App : `python run_web.py` (FastAPI sur http://127.0.0.1:8000 + Next.js React sur http://localhost:3000). Ne PAS lancer `run_app.py` (Streamlit déprécié).
- Pipeline : `python run_scrapers.py`, `python run_pipeline.py` (complet), `python run_pipeline.py --leger` (passe Fraîcheur seule). L'Action GitHub fait une collecte légère 3 fois par jour et une complète le lundi.
- Notation : **jamais par API** (`llm.scoring_api: false`, les API DeepSeek/Gemini ne servent qu'aux lettres). Notation par agents (Antigravity, Claude Code) : `python tools/antigravity_v4.py export --stale` puis `ingest --dry-run`, puis `ingest`. Prompt : `docs/PROMPT_ANTIGRAVITY_agents_remise_a_zero.md`.
- Archivage : le pipeline archive (IGNORÉ `auto_archive`) les offres à trier ni publiées ni revues au scraping depuis 30 jours (`seen_jobs.last_seen_at`) ; le scraping remet à trier, avec sa note, toute offre archivée qu'il revoit. Ne pas lancer `check_closed_offers.py --rouvrir-archivees` (va-et-vient avec l'archivage).
- Offres fermées : `python tools/check_closed_offers.py --apply` (code), puis les non tranchées (`data/antigravity/vivacite_inconnues.json`) par agents via `antigravity_v4.py export --verifier-ids`.
- Doublons : identiques par code (`tools/dedupe_reposts.py`), le reste par agents entreprise par entreprise (`tools/agents_doublons.py export` / `ingest`).
- Entretien de la base (essai à blanc par défaut, `--apply` pour écrire) : `python tools/dedupe_reposts.py`, `python tools/rotate_backups.py`.

## Conventions
- Commit direct sur `main` par défaut. Branche + PR seulement pour un changement risqué (suppression massive, refonte, secret). Avant toute suppression de fichier : vérifier qu'il n'est plus utilisé.
- Messages de commit en français, préfixe conventionnel (`feat`, `fix`...).
- Après modification d'un widget Streamlit : vérifier les clés de widget (erreur `StreamlitWidgetAlreadyInstantiatedError` déjà rencontrée).
- Fichiers locaux sensibles (`data/candidate.local.yaml`, CV) : ne jamais les committer ni les afficher dans les réponses.
