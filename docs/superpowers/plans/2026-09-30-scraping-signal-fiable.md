# Scraping : signal fiable et collecte moins coûteuse — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rendre le statut des runs de scraping digne de confiance (fin des `PARTIAL` permanents, compteur d'insertions exact), réduire les requêtes gaspillées de la passe Pertinence, et diagnostiquer les sources à 0 carte.

**Architecture:** Trois corrections ciblées dans le moteur existant (`scrapers/base.py::_collect_pass`, `config.yaml`, `run_scrapers.py::main`) plus un diagnostic outillé. Aucun nouveau module. S'appuie sur la branche `feat/scraping-fiabilite` (retry HTTP + alerte source dégradée), déjà poussée.

**Tech Stack:** Python 3.12, httpx, pydantic v2, SQLAlchemy/SQLite, pytest.

**Spec:** Pas de spec séparée. Constat tiré de la base locale (`data/stage_copilot.db`, runs du 18 au 28/09/2026) :
- Tous les runs récents sont `PARTIAL`, à cause de `max_pages` en passe Pertinence.
- Passe Pertinence sur 5 runs : LinkedIn 3 200 cartes lues / 2 603 déjà connues / 168 gardées ; JobTeaser 5 625 / 3 951 / 213.
- Run du 23/09 : 415 validées, `total_inserted = 0`, alors que 314 offres ont été créées ce jour-là. Cause : le callback de notation live (`create_batch_callback`, `src/matching/live_scorer.py:217`) insère pendant la collecte, et l'ingestion finale ne voit plus que des doublons.
- JobTeaser : 10 passes à `stream_end` avec 0 carte. WTTJ : 132 offres au total.

## Global Constraints

- Travailler sur la branche `feat/scraping-fiabilite`, dans le worktree `C:/Users/anita/Documents/eddy/Mines_sainte/code/wt_fiabilite`.
- Commentaires, libellés et messages en français, dans le style existant (docstrings explicatives, `#:` pour les attributs).
- Messages de commit en anglais style conventionnel (`feat(scraping): …`), terminés par `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Lancer les tests avec `python -m pytest -q --ignore=tests/test_app.py`. `reportlab` est absent en local, donc `test_pdf_export_generation` échoue avant toute modification : échec connu, à ignorer.
- Ne pas toucher au checkout principal (`Assistant_recherche_de_stage`), qui contient du travail non commité.

## Review Focus

1. Passe arrêtée par `max_pages` alors que la dernière page apportait encore du neuf : elle doit rester une perte (`max_pages`). Test : Task 1, `test_max_pages_avec_offres_nouvelles_reste_perte`. Une page vide ne peut pas atteindre ce point (elle arrête la passe en `stream_end`), mais le calcul du ratio garde `if page.entries` contre la division par zéro.
2. Passe Pertinence dont les offres connues sont entrecoupées d'offres nouvelles : l'arrêt anticipé ne doit pas se déclencher (la série est remise à zéro). Couvert par le test existant `test_serie_de_connues_interrompue` et par `test_pertinence_serie_interrompue` (Task 2).
3. Run avec `--no-collect` : `total_inserted` ne doit pas devenir négatif ni planter (aucun run ouvert). Test : Task 3, `test_no_collect_n_ouvre_pas_de_run`.
4. Doublon inter-sources fusionné à l'ingestion (description mise à jour, pas d'insertion) : il ne doit pas compter comme insertion. Couvert par le comptage `count_jobs` avant/après (Task 3).
5. Anciens runs en base avec `max_pages` : ils restent affichés tels quels. Seul le libellé du nouveau motif est ajouté, sans migration. Vérification manuelle en Task 1, Step 6.

---

### Task 1: Motif d'arrêt `max_pages_saturated` (plafond atteint sans perte)

Quand la passe atteint le plafond de pages alors que la dernière page était composée à 80 % ou plus d'offres déjà connues, le flux n'apporte plus rien : ce n'est pas une perte. On le signale par un motif distinct, hors `INCOMPLETE_STOP_REASONS`, pour que le run ne soit plus `PARTIAL` à tort et que l'alerte de santé reste significative.

**Files:**
- Modify: `scrapers/models.py` (Literal `StopReason` vers la ligne 204, et ajout d'une constante)
- Modify: `scrapers/base.py` (`_collect_pass`, vers les lignes 668-890)
- Modify: `src/constants.py` (`STOP_REASON_LABELS`, vers la ligne 359)
- Test: `tests/test_hybrid_collection.py`

**Interfaces:**
- Produces: motif d'arrêt `"max_pages_saturated"`, constante `scrapers.models.SATURATION_RATIO: float = 0.8`.

- [ ] **Step 1: Write the failing tests** (à ajouter en fin de `tests/test_hybrid_collection.py`, avant le bloc `if __name__ == "__main__":` s'il existe)

```python
def test_max_pages_sature_n_est_pas_une_perte() -> None:
    """Plafond atteint sur une page déjà connue à 80 % : vivier épuisé, pas de perte."""
    from scrapers.models import is_incomplete_stop

    config = _config("relevance", max_pages_per_query=2, window_days=None)
    known = InMemoryKnownIndex(
        pairs={("linkedin", k) for k in ("k1", "k2", "k3", "k4")}
    )
    scraper = _ScriptedScraper(
        config,
        pages=[["n1", "n2"], ["k1", "k2", "k3", "k4", "n3"], ["n4"]],
    )
    report = scraper.run(known).query_reports[0]
    assert report.stop_reason == "max_pages_saturated", report.stop_reason
    assert not is_incomplete_stop(report.stop_reason)
    scraper.close()


def test_max_pages_avec_offres_nouvelles_reste_perte() -> None:
    """Plafond atteint alors que la dernière page apportait du neuf : perte signalée."""
    config = _config("relevance", max_pages_per_query=2, window_days=None)
    known = InMemoryKnownIndex(pairs={("linkedin", "k1")})
    scraper = _ScriptedScraper(config, pages=[["n1", "n2"], ["n3", "n4", "k1"], ["n5"]])
    report = scraper.run(known).query_reports[0]
    assert report.stop_reason == "max_pages", report.stop_reason
    scraper.close()

```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_hybrid_collection.py -q -k max_pages`
Expected: `test_max_pages_sature_n_est_pas_une_perte` FAIL (`'max_pages' == 'max_pages_saturated'`), l'autre PASS.

- [ ] **Step 3: Implement**

Dans `scrapers/models.py`, ajouter `"max_pages_saturated",` juste après `"max_pages",` dans le Literal `StopReason` (ne pas l'ajouter à `INCOMPLETE_STOP_REASONS`). Puis, juste après la définition de `INCOMPLETE_STOP_REASONS` :

```python
#: Part minimale d'offres déjà vues (mémoire de collecte ou doublons du run) sur
#: la DERNIÈRE page pour qu'un arrêt au plafond de pages soit jugé sans perte :
#: le flux ne faisait plus que repasser sur du connu.
SATURATION_RATIO: float = 0.8
```

Dans `scrapers/base.py`, importer `SATURATION_RATIO` depuis `.models` (dans le bloc `from .models import (...)` existant).

Dans `_collect_pass`, à côté de `halt = False` (avant le `while`) :

```python
        #: Part d'offres déjà vues sur la dernière page traitée (voir SATURATION_RATIO).
        last_page_seen_ratio = 0.0
```

Au début du traitement d'une page, à côté de `duplicates_in_page = 0` :

```python
            seen_in_page = 0
```

Dans la branche « carte déjà connue ou doublon du run », juste après `streak += 1` :

```python
                    seen_in_page += 1
```

Juste après le bloc `if halt: break` qui suit la boucle `for entry in page.entries` :

```python
            last_page_seen_ratio = seen_in_page / len(page.entries) if page.entries else 0.0
```

Remplacer le `else:` final du `while` :

```python
        else:
            if last_page_seen_ratio >= SATURATION_RATIO:
                stop_reason = "max_pages_saturated"
                stop_detail = (
                    f"plafond de {plan.max_pages} page(s) atteint, dernière page déjà vue à "
                    f"{last_page_seen_ratio:.0%} : vivier de fait épuisé"
                )
            else:
                stop_reason = "max_pages"
                stop_detail = (
                    f"plafond de {plan.max_pages} page(s) atteint — flux potentiellement tronqué"
                )
```

Dans `src/constants.py`, `STOP_REASON_LABELS`, après la clé `"max_pages"` :

```python
    "max_pages_saturated": "Plafond de pages atteint sur des offres déjà connues (rien de perdu)",
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_hybrid_collection.py tests/test_reliability.py -q`
Expected: tout PASS.

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q --ignore=tests/test_app.py`
Expected: seul `test_pdf_export_generation` échoue (échec connu).

- [ ] **Step 6: Vérifier l'affichage des anciens runs**

Run: `python run_scrapers.py --no-collect --top-telemetry 10`
Expected: les anciennes lignes `max_pages` s'affichent comme avant, sans erreur.

- [ ] **Step 7: Commit**

```bash
git add scrapers/models.py scrapers/base.py src/constants.py tests/test_hybrid_collection.py
git commit -m "feat(scraping): distinguish saturated max_pages stop from real stream loss

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Arrêt anticipé en passe Pertinence

La passe Pertinence relit jusqu'à 20 pages d'offres connues à 80 % : ce sont des requêtes inutiles, qui provoquent les 429 LinkedIn. Le moteur applique déjà l'arrêt anticipé quel que soit le mode, dès que `early_stop_threshold > 0`. Il suffit donc de l'armer dans la configuration. Seuil choisi : 30 offres connues d'affilée, soit 3 pages LinkedIn pleines sans rien de neuf, et au moins 3 pages lues pour laisser à la pertinence le temps d'agir.

**Files:**
- Modify: `config.yaml` (section `scrapers.passes.relevance`, vers la ligne 43)
- Test: `tests/test_hybrid_collection.py`

**Interfaces:**
- Consumes: `ScraperConfig.from_config(config: dict) -> ScraperConfig`, `ScraperConfig.pass_config(mode: str) -> PassConfig` (existants).

- [ ] **Step 1: Write the failing tests**

```python
def test_pertinence_arret_anticipe_arme() -> None:
    """Armé par configuration, l'arrêt anticipé coupe aussi la passe Pertinence."""
    config = _config(
        "relevance",
        window_days=None,
        early_stop_after_known=3,
        arm_early_stop=True,
        early_stop_min_pages=1,
    )
    known = InMemoryKnownIndex(pairs={("linkedin", k) for k in ("k1", "k2", "k3", "k4")})
    scraper = _ScriptedScraper(config, pages=[["n1", "k1", "k2", "k3"], ["k4", "n2"]])
    report = scraper.run(known).query_reports[0]
    assert report.stop_reason == "early_stop", report.stop_reason
    assert report.pages_fetched == 1, report.pages_fetched
    scraper.close()


def test_pertinence_serie_interrompue() -> None:
    """Des offres nouvelles intercalées remettent la série à zéro : pas d'arrêt."""
    config = _config(
        "relevance",
        window_days=None,
        early_stop_after_known=3,
        arm_early_stop=True,
        early_stop_min_pages=1,
    )
    known = InMemoryKnownIndex(pairs={("linkedin", k) for k in ("k1", "k2", "k3", "k4")})
    scraper = _ScriptedScraper(config, pages=[["k1", "k2", "n1", "k3", "k4", "n2"]])
    report = scraper.run(known).query_reports[0]
    assert report.stop_reason == "stream_end", report.stop_reason
    scraper.close()


def test_config_yaml_arme_l_arret_anticipe_en_pertinence() -> None:
    """La configuration livrée arme l'arrêt anticipé de la passe Pertinence."""
    import yaml

    raw = yaml.safe_load((PROJECT_ROOT / "config.yaml").read_text(encoding="utf-8"))
    relevance = ScraperConfig.from_config(raw).pass_config("relevance")
    assert relevance.early_stop_after_known == 30
    assert relevance.arm_early_stop is True
    assert relevance.early_stop_min_pages == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_hybrid_collection.py -q -k pertinence`
Expected: `test_config_yaml_arme_l_arret_anticipe_en_pertinence` FAIL (`0 == 30`). Les deux autres PASS : le moteur le permet déjà, ces tests verrouillent le comportement.

- [ ] **Step 3: Implement** : dans `config.yaml`, section `passes.relevance`, remplacer `early_stop_after_known: 0` par :

```yaml
      # Arrêt anticipé armé : 3 pages LinkedIn pleines d'offres déjà connues
      # (mesuré : ~80 % de connues en Pertinence) = plus rien à gagner, et
      # chaque page de trop rapproche du 429.
      early_stop_after_known: 30
      arm_early_stop: true
      early_stop_min_pages: 3
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_hybrid_collection.py -q`
Expected: tout PASS. Si l'ancien test `test_passe_pertinence_sans_arret_anticipe` échoue, c'est qu'il lit `config.yaml` : il utilise `_config(...)`, donc il ne doit pas échouer. S'il échoue quand même, arrêter et signaler.

- [ ] **Step 5: Commit**

```bash
git add config.yaml tests/test_hybrid_collection.py
git commit -m "feat(scraping): arm early stop on relevance pass to cut redundant pages

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `total_inserted` exact malgré la notation live

`ingest_raw_jobs` est appelé deux fois : par le callback live pendant la collecte, puis à la fin. Le second appel ne voit que des doublons, et le run enregistre 0 insertion. On compte donc les offres en base avant la collecte et après l'ingestion : ce chiffre est juste quel que soit le chemin d'insertion, et il exclut les doublons fusionnés.

**Files:**
- Modify: `run_scrapers.py` (`main`, avant le `if args.no_collect:` vers la ligne 717, ingestion vers la ligne 792, `finish_run` et résumé vers les lignes 835-850)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `Database.count_jobs() -> int`, `Database.get_recent_runs(limit) -> list[dict]` (existants).

- [ ] **Step 1: Write the failing tests** (en fin de `tests/test_cli.py`)

```python
def test_total_inserted_compte_les_insertions_live(tmp_path, monkeypatch):
    """Offres insérées pendant la collecte (notation live) : comptées dans le run."""
    import run_scrapers
    from scrapers.models import RawJob, ScrapeResult
    from src.ingestion.bridge import ingest_raw_jobs
    from src.storage.database import Database

    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(
        run_scrapers, "load_config",
        lambda: {"database": {"path": db_path}, "scrapers": {"enabled_sources": ["wttj"]}},
    )
    jobs = [
        RawJob(id_externe=f"j{i}", source="wttj", title=f"Stage Data Scientist {i}",
               company=f"Acme{i}", location="Paris", url=f"https://example.com/j{i}",
               description="PyTorch", is_internship=True)
        for i in range(2)
    ]

    class _FakeManager:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, modes=None, on_batch_collected=None):
            ingest_raw_jobs(jobs, Database(db_path))  # simule le callback live
            return ScrapeResult(jobs=jobs, found=2, rejected_bi=0)

    monkeypatch.setattr(run_scrapers, "ScraperManager", _FakeManager)
    run_scrapers.main([])
    run = Database(db_path).get_recent_runs(limit=1)[0]
    assert run["total_inserted"] == 2, run


def test_no_collect_n_ouvre_pas_de_run(tmp_path, monkeypatch):
    """--no-collect : aucun run, aucun compteur négatif, aucune exception."""
    import run_scrapers
    from src.storage.database import Database

    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(run_scrapers, "load_config", lambda: {"database": {"path": db_path}})
    assert run_scrapers.main(["--no-collect"]) == 0
    assert Database(db_path).get_recent_runs(limit=1) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_cli.py -q -k "total_inserted or no_collect"`
Expected: `test_total_inserted_compte_les_insertions_live` FAIL (`total_inserted == 0`). Si le test plante plutôt sur une clé de configuration manquante (`KeyError`), ajouter cette clé au dict de `load_config` du test, puis relancer.

- [ ] **Step 3: Implement** dans `run_scrapers.py::main`

Juste avant `if args.no_collect:` :

```python
    # Compté en base plutôt que via ``ingest_raw_jobs`` : la notation live insère
    # déjà pendant la collecte, l'ingestion finale ne verrait que des doublons.
    jobs_before = db.count_jobs()
```

Juste après `stats = ingest_raw_jobs(result.jobs, db)` :

```python
    inserted = max(0, db.count_jobs() - jobs_before)
```

Dans l'appel `db.finish_run(...)`, remplacer `total_inserted=stats["new_inserted"],` par `total_inserted=inserted,`.
Dans le résumé, remplacer `logger.info(" Nouvelles offres persistées  : %d", stats["new_inserted"])` par `logger.info(" Nouvelles offres persistées  : %d", inserted)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_cli.py -q`
Expected: tout PASS.

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q --ignore=tests/test_app.py`
Expected: seul `test_pdf_export_generation` échoue (échec connu).

- [ ] **Step 6: Commit**

```bash
git add run_scrapers.py tests/test_cli.py
git commit -m "fix(scraping): count inserted jobs from database so live scoring inserts are not lost

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Diagnostic des sources à 0 carte (JobTeaser, WTTJ)

Tâche d'enquête, pas de code produit à l'avance. Le livrable est un court rapport, et une décision à valider par l'utilisateur avant tout changement.

**Files:**
- Create: `docs/superpowers/diagnostics/2026-09-30-sources-vides.md`

- [ ] **Step 1: Identifier les requêtes vides.** Exécuter depuis le checkout principal (qui contient la vraie base, en lecture seule) :

```bash
cd "C:/Users/anita/Documents/eddy/Mines_sainte/code/Assistant_recherche_de_stage" && python -c "
import sqlite3; c=sqlite3.connect('data/stage_copilot.db')
for r in c.execute('''select source, query, mode, count(*), sum(cards_seen), max(stop_detail)
  from scrape_query_stats where run_id in (select id from scrape_runs order by started_at desc limit 5)
  group by 1,2,3 having sum(cards_seen)=0 order by 1,2'''): print(r)"
```

Expected : la liste (source, requête, mode) des passes à 0 carte.

- [ ] **Step 2: Rejouer une requête vide en direct** avec la sonde existante :

```bash
python tools/probe_sources.py --help
```

Puis lancer la sonde sur la source et la requête identifiées à l'étape 1, avec les options qu'affiche `--help`. Noter le code HTTP, la taille de la réponse et le nombre de cartes parsées.

- [ ] **Step 3: Classer chaque cas** dans le rapport, parmi : (a) requête sans résultat réel sur la plateforme (vérifier à la main sur le site), (b) blocage Cloudflare (403, page de challenge), (c) parseur cassé (réponse 200 mais 0 carte), (d) cookies expirés.

- [ ] **Step 4: WTTJ, faible volume.** Dans le même rapport, noter le nombre de hits Algolia (`nbHits`) pour « Data Scientist » avec et sans le filtre `contract_type:internship`. Cela dira si 132 offres est une limite du marché ou du scraper.

- [ ] **Step 5: Commit du rapport et arrêt.** Présenter les recommandations à l'utilisateur, par exemple retirer une requête, sortir JobTeaser du cron ou réparer le parseur, et **attendre sa décision** avant de coder.

```bash
git add docs/superpowers/diagnostics/2026-09-30-sources-vides.md
git commit -m "docs(scraping): diagnose sources returning zero cards

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Pull request

- [ ] **Step 1:** Vérifier que `gh auth status` indique une session connectée. Sinon, demander à l'utilisateur de lancer `gh auth login`, puis attendre.
- [ ] **Step 2:** `git push`
- [ ] **Step 3:** Créer la PR `feat/scraping-fiabilite` vers `main`, avec `gh pr create`. Le corps résume les 2 commits de fiabilité déjà poussés (retry, alerte source dégradée, cache CI, dépendances du cron) et les commits des tâches 1 à 4, avec les chiffres du constat ci-dessus. Il se termine par `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- [ ] **Step 4:** Après merge, lancer une fois le workflow `daily_scraper.yml` à la main (`gh workflow run daily_scraper.yml`) et relever la durée, le statut du run, les 429 et `total_inserted`. C'est le baseline de la suite.

---

## Hors périmètre (plans séparés)

- **Sources ATS** (Greenhouse, Lever, Ashby) : sous-système indépendant, nécessite la liste d'entreprises cibles de l'utilisateur. Plan dédié à écrire une fois la liste fournie.
- **Sources académiques** (ABG, Inria, CNRS) : un plan par source, parseurs hétérogènes.
- **Parallélisme des sources** et **digest quotidien** : après le baseline de la Task 5.
