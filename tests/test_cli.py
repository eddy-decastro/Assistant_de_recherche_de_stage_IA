"""Tests de l'interface en ligne de commande du pipeline (parsing des options)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from run_scrapers import parse_args


def test_options_par_defaut() -> None:
    """Sans option : aucune étape de ranking n'est déclenchée."""
    args = parse_args([])
    assert args.trigger_scoring is False
    assert args.rescore_all is False
    assert args.trigger_rerank is False
    assert args.top_rerank is None, "Le défaut doit venir de config.yaml (ranking.top_n_rerank)."
    print("  CLI : options par défaut OK (aucun ranking déclenché)")


def test_options_actives() -> None:
    """Toutes les options de scoring/reranking sont reconnues."""
    args = parse_args(
        ["--trigger-scoring", "--rescore-all", "--trigger-rerank", "--top-rerank", "5"]
    )
    assert args.trigger_scoring is True
    assert args.rescore_all is True
    assert args.trigger_rerank is True
    assert args.top_rerank == 5
    assert args.reset_rerank is None, "Aucune ré-évaluation forcée par défaut."
    print("  CLI : --trigger-scoring / --rescore-all / --trigger-rerank / --top-rerank OK")


def test_option_reset_rerank() -> None:
    """--reset-rerank N : ré-évaluation forcée des N meilleures offres."""
    args = parse_args(["--trigger-rerank", "--reset-rerank", "20"])
    assert args.reset_rerank == 20, args.reset_rerank
    print("  CLI : --reset-rerank OK (ré-évaluation forcée du Top-N)")


def test_option_no_collect() -> None:
    """--no-collect : rescore/rerank sans recollecter (après enrichissement)."""
    args = parse_args(["--no-collect", "--rescore-all"])
    assert args.no_collect is True and args.rescore_all is True
    assert parse_args([]).no_collect is False
    print("  CLI : --no-collect OK (travail sur la base existante)")


def test_options_hygiene() -> None:
    """--dedupe / --revalidate / --dry-run : nettoyage avant dépense de tokens."""
    args = parse_args(["--dedupe", "--revalidate", "--dry-run"])
    assert args.dedupe is True and args.revalidate is True and args.dry_run is True
    defaults = parse_args([])
    assert defaults.dedupe is False and defaults.revalidate is False and defaults.dry_run is False
    print("  CLI : --dedupe / --revalidate / --dry-run OK")


def test_run_pipeline_delegue() -> None:
    """run_pipeline est un raccourci : il ne doit contenir aucun code de collecte."""
    source = (PROJECT_ROOT / "run_pipeline.py").read_text(encoding="utf-8")
    assert "run_scrapers_main" in source, "run_pipeline doit déléguer au pipeline unifié."
    # L'API WTTJ v1 (404) ne doit plus être *importée* ni *appelée* (elle peut être citée).
    assert "from src.ingestion.wttj import" not in source, "L'API WTTJ v1 (404) ne doit plus être importée."
    assert "scrape_jobs(" not in source, "run_pipeline ne doit plus appeler le scraper WTTJ v1."
    print("  CLI : run_pipeline délègue bien à run_scrapers (aucune duplication)")


def test_options_collecte_hybride() -> None:
    """--passes / --only-source / --top-telemetry : pilotage fin de la collecte."""
    args = parse_args(["--passes", "freshness", "--only-source", "linkedin", "--top-telemetry", "10"])
    assert args.passes == "freshness"
    assert args.only_source == "linkedin"
    assert args.top_telemetry == 10
    defaults = parse_args([])
    assert defaults.passes is None and defaults.only_source is None
    assert defaults.top_telemetry == 0, "Aucune télémétrie affichée par défaut."

    help_text = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "run_scrapers.py"), "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        cwd=str(PROJECT_ROOT),
    )
    assert help_text.returncode == 0, help_text.stderr
    for option in ("--passes", "--only-source", "--top-telemetry"):
        assert option in help_text.stdout, f"{option} doit apparaître dans --help"
    print("  CLI : options de collecte hybride OK (--passes / --only-source / --top-telemetry)")


if __name__ == "__main__":
    test_options_par_defaut()
    test_options_actives()
    test_option_reset_rerank()
    test_option_no_collect()
    test_options_hygiene()
    test_run_pipeline_delegue()
    test_options_collecte_hybride()
    print("TOUS LES TESTS PASSENT")
