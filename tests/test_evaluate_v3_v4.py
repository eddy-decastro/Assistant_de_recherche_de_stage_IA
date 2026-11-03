"""Tests unitaires du script d'évaluation et de comparaison v3 vs v4."""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluate_v3_v4 import (  # noqa: E402
    build_comparison_report,
    compute_roc_auc,
    select_evaluation_cohorts,
)
from src.constants import STATUS_APPLIED, STATUS_IGNORED, STATUS_NEW  # noqa: E402


def test_compute_roc_auc() -> None:
    assert compute_roc_auc([90.0, 80.0], [40.0, 50.0]) == 1.0
    assert compute_roc_auc([30.0, 40.0], [80.0, 90.0]) == 0.0
    assert compute_roc_auc([50.0], [50.0]) == 0.5
    assert compute_roc_auc([], [50.0]) is None


def test_select_evaluation_cohorts_excludes_auto_archive() -> None:
    jobs = [
        {"id": "p1", "status": STATUS_APPLIED, "rerank_score": 88.0, "v4_display_score": 91.0, "v4_class": "A"},
        {"id": "i_human", "status": STATUS_IGNORED, "rejection_reason": "", "rerank_score": 52.0, "v4_display_score": 45.0, "v4_class": "D"},
        {"id": "i_auto", "status": STATUS_IGNORED, "rejection_reason": "auto_archive", "rerank_score": 85.0},
        {"id": "i_auto_note", "status": STATUS_IGNORED, "user_notes": "[Auto] Archivée (plus de 14 jours)", "rerank_score": 80.0},
        {"id": "n1", "status": STATUS_NEW, "rerank_score": 84.0, "v4_display_score": 89.5, "v4_class": "B"},
        {"id": "n_exclu", "status": STATUS_NEW, "verdict": "EXCLU", "exclusion_reason": "Alternance"},
    ]
    cohorts = select_evaluation_cohorts(jobs, ignored_sample_size=150)
    assert [j["id"] for j in cohorts["applied"]] == ["p1"]
    assert [j["id"] for j in cohorts["human_ignored"]] == ["i_human"]
    assert [j["id"] for j in cohorts["active_new"]] == ["n1"]

    report = build_comparison_report(jobs)
    assert report["auc_v3"] == 1.0
    assert report["auc_v4"] == 1.0
    assert report["applied_in_e"] == 0
    assert report["applied_under_50"] == 0
    assert "Rapport de comparaison" in report["markdown"]
