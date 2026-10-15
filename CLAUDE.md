# Assistant recherche de stage

App Streamlit + SQLite de suivi de candidatures : scrapers, scoring des offres par LLM (DeepSeek), sync R2.

## Règles critiques
- **Ne jamais écrire dans `data/jobs.db`** depuis des tests ou scripts d'essai : cette base contient les scores LLM, coûteux à refaire (déjà perdus une fois). Tests et essais passent par une copie temporaire (`tmp_path` pytest).
- Avant toute opération qui modifie la DB réelle : copie dans `data/backups/`.
- Ne pas écraser les statuts saisis par l'utilisateur (voir `tests/test_status_protection.py`).
- Avant la sync R2 : fusionner avec les données distantes, ne pas écraser.

## Commandes
- Tests : `pytest` (config dans `pytest.ini`).
- Lint : `ruff check .`
- App : `python run_app.py` (config de preview dans `.claude/launch.json`).
- Pipeline : `python run_scrapers.py`, `python run_pipeline.py`.

## Conventions
- Une branche et une PR par tâche. Jamais de commit direct sur `main`.
- Messages de commit en français, préfixe conventionnel (`feat`, `fix`...).
- Après modification d'un widget Streamlit : vérifier les clés de widget (erreur `StreamlitWidgetAlreadyInstantiatedError` déjà rencontrée).
- Fichiers locaux sensibles (`data/candidate.local.yaml`, CV) : ne jamais les committer ni les afficher dans les réponses.
