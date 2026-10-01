"""Garde-fou de régression des règles de scoring v3 sur le golden set.

``tools/eval_golden.py`` n'appelle pas le LLM : il rejoue les règles déterministes
(exclusion de contrat, planchers par catégorie, plafonds éthiques, bonus) sur 30
offres annotées à la main. Ce test détecte donc une régression de ces règles ou
de ``config.yaml``, pas une dérive de qualité du juge LLM.

Seuils posés sous les valeurs actuelles (Spearman 0,932, catégories 80 %) pour
tolérer un petit réglage, pas un changement qui dégrade la grille.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

MIN_OFFERS = 30
MIN_SPEARMAN = 0.90
MIN_CATEGORY_ACCURACY = 75.0


def _load_eval_golden() -> ModuleType:
    spec = importlib.util.spec_from_file_location("eval_golden", PROJECT_ROOT / "tools" / "eval_golden.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def golden_metrics() -> dict:
    return _load_eval_golden().evaluate_golden_set()


def test_golden_set_complet(golden_metrics) -> None:
    assert golden_metrics["total"] >= MIN_OFFERS


def test_correlation_de_rang(golden_metrics) -> None:
    assert golden_metrics["spearman"] >= MIN_SPEARMAN


def test_accord_sur_les_categories(golden_metrics) -> None:
    assert golden_metrics["category_accuracy"] >= MIN_CATEGORY_ACCURACY


def test_aucune_exclusion_a_tort(golden_metrics) -> None:
    assert golden_metrics["false_positive_exclusions"] == 0


def test_defense_toujours_plafonnee(golden_metrics) -> None:
    assert golden_metrics["false_negative_ethics"] == 0
