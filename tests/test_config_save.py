"""Tests de l'écriture des réglages de collecte dans config.yaml."""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_config, save_scraper_defaults  # noqa: E402

CONFIG_TEXT = """\
# Commentaire en tête, à conserver
scrapers:
  enabled_sources:
  - linkedin
  target_queries:
  - Data Scientist
  max_offers_per_source: 200  # plafond
llm:
  provider: deepseek
"""


def _write(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG_TEXT, encoding="utf-8")
    return path


def test_ecrit_dans_la_section_scrapers(tmp_path) -> None:
    path = _write(tmp_path)
    save_scraper_defaults(["NLP", "LLM"], ["wttj"], 50, path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["scrapers"]["target_queries"] == ["NLP", "LLM"]
    assert data["scrapers"]["enabled_sources"] == ["wttj"]
    assert data["scrapers"]["max_offers_per_source"] == 50
    assert "scraping" not in data
    assert data["llm"] == {"provider": "deepseek"}


def test_conserve_les_commentaires(tmp_path) -> None:
    path = _write(tmp_path)
    save_scraper_defaults(["NLP"], ["wttj"], 50, path)
    text = path.read_text(encoding="utf-8")
    assert "# Commentaire en tête, à conserver" in text
    assert "# plafond" in text


def test_la_valeur_est_relue_par_load_config(tmp_path) -> None:
    path = _write(tmp_path)
    save_scraper_defaults(["NLP"], ["wttj"], 50, path)
    assert load_config(path)["scrapers"]["max_offers_per_source"] == 50
