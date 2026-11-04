"""Jeu d'évaluation de la note v4 contre les décisions d'Eddy (N3).

Mesure si ``v4_display_score`` sépare bien les offres auxquelles Eddy a postulé
(POSTULÉ / ENTRETIEN / REJETÉ après candidature) de celles qu'il a ignorées à la
main (hors archivage automatique et offre fermée) :

- AUC globale (probabilité qu'une offre postulée soit mieux notée qu'une ignorée),
  avec l'ancienne note v3 en comparaison ;
- détail par raison d'ignorer (``rejection_reason``) ;
- liste des offres ignorées mais notées haut (>= 70 par défaut), pour guider
  l'ajustement du prompt.

LECTURE SEULE : la base est copiée dans un dossier temporaire (API ``backup`` de
SQLite, source ouverte en ``mode=ro``) et seule la copie est lue.

Usage (PowerShell) :
    .venv\\Scripts\\python.exe tools\\eval_feedback.py
    .venv\\Scripts\\python.exe tools\\eval_feedback.py --db data\\stage_copilot.db --seuil 75
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.request import pathname2url

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.constants import (  # noqa: E402
    STATUS_APPLIED,
    STATUS_IGNORED,
    STATUS_INTERVIEW,
    STATUS_REJECTED,
    ignore_reason_label,
)
from src.matching.scoring_v4 import is_applied_decision, is_manual_ignore  # noqa: E402

DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "stage_copilot.db"
DEFAULT_HIGH_THRESHOLD = 70.0
NO_REASON = "(sans raison)"

_WANTED_COLUMNS = (
    "id",
    "title",
    "company",
    "status",
    "rejection_reason",
    "rejection_comment",
    "user_notes",
    "applied_at",
    "v4_class",
    "v4_display_score",
    "rerank_score",
)


def copy_database_readonly(source: Path, target: Path) -> None:
    """Copie ``source`` vers ``target`` sans jamais ouvrir la source en écriture."""
    uri = "file:" + pathname2url(str(source.resolve())) + "?mode=ro"
    src = sqlite3.connect(uri, uri=True)
    try:
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def load_decision_jobs(db_copy: Path) -> list[dict[str, Any]]:
    """Lit (sur la copie) les offres décidées par Eddy, colonnes absentes tolérées."""
    conn = sqlite3.connect(str(db_copy))
    try:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
        cols = [c for c in _WANTED_COLUMNS if c in existing]
        rows = conn.execute(
            f"SELECT {', '.join(cols)} FROM jobs WHERE status IN (?, ?, ?, ?)",
            (STATUS_APPLIED, STATUS_INTERVIEW, STATUS_REJECTED, STATUS_IGNORED),
        ).fetchall()
    finally:
        conn.close()
    jobs = []
    for row in rows:
        job = {c: None for c in _WANTED_COLUMNS}
        job.update(dict(zip(cols, row)))
        jobs.append(job)
    return jobs


def auc(positives: list[float], negatives: list[float]) -> float | None:
    """AUC de Mann-Whitney (égalités comptées 1/2) ; ``None`` si un camp est vide."""
    if not positives or not negatives:
        return None
    wins = 0.0
    for p in positives:
        for n in negatives:
            if p > n:
                wins += 1.0
            elif p == n:
                wins += 0.5
    return wins / (len(positives) * len(negatives))


def _scores(jobs: list[dict[str, Any]], column: str) -> list[float]:
    return [float(j[column]) for j in jobs if j.get(column) is not None]


def evaluate(jobs: list[dict[str, Any]], high_threshold: float = DEFAULT_HIGH_THRESHOLD) -> dict[str, Any]:
    """Calcule AUC globale, détail par raison et offres ignorées notées haut."""
    applied = [j for j in jobs if is_applied_decision(j)]
    ignored = [j for j in jobs if is_manual_ignore(j)]

    pos_v4 = _scores(applied, "v4_display_score")
    neg_v4 = _scores(ignored, "v4_display_score")

    by_reason: dict[str, list[dict[str, Any]]] = {}
    for job in ignored:
        by_reason.setdefault(str(job.get("rejection_reason") or NO_REASON), []).append(job)
    reasons = []
    for code in sorted(by_reason, key=lambda c: (-len(by_reason[c]), c)):
        members = by_reason[code]
        scores = _scores(members, "v4_display_score")
        reasons.append(
            {
                "code": code,
                "label": NO_REASON if code == NO_REASON else ignore_reason_label(code),
                "count": len(members),
                "scored": len(scores),
                "mean_v4": (sum(scores) / len(scores)) if scores else None,
                "auc_v4": auc(pos_v4, scores),
            }
        )

    high = sorted(
        (
            j
            for j in ignored
            if j.get("v4_display_score") is not None
            and float(j["v4_display_score"]) >= high_threshold
        ),
        key=lambda j: (-float(j["v4_display_score"]), str(j.get("id") or "")),
    )
    return {
        "applied": len(applied),
        "ignored": len(ignored),
        "applied_scored_v4": len(pos_v4),
        "ignored_scored_v4": len(neg_v4),
        "auc_v4": auc(pos_v4, neg_v4),
        "auc_v3": auc(_scores(applied, "rerank_score"), _scores(ignored, "rerank_score")),
        "mean_applied_v4": (sum(pos_v4) / len(pos_v4)) if pos_v4 else None,
        "mean_ignored_v4": (sum(neg_v4) / len(neg_v4)) if neg_v4 else None,
        "by_reason": reasons,
        "high_threshold": high_threshold,
        "high_ignored": high,
    }


def _fmt(value: float | None, digits: int = 2) -> str:
    return "n/d" if value is None else f"{value:.{digits}f}".replace(".", ",")


def format_report(result: dict[str, Any]) -> str:
    """Rapport lisible en français."""
    lines = [
        "ÉVALUATION DE LA NOTE V4 CONTRE LES DÉCISIONS D'EDDY",
        "=" * 52,
        f"Offres postulées : {result['applied']} (dont {result['applied_scored_v4']} notées v4)",
        f"Offres ignorées à la main : {result['ignored']} (dont {result['ignored_scored_v4']} notées v4)",
        f"Note v4 moyenne : postulées {_fmt(result['mean_applied_v4'], 1)} / "
        f"ignorées {_fmt(result['mean_ignored_v4'], 1)}",
        f"AUC v4 : {_fmt(result['auc_v4'])}   (ancienne note v3 : {_fmt(result['auc_v3'])})",
        "  0,50 = la note ne sépare pas mieux que le hasard ; 1,00 = séparation parfaite.",
        "",
        "DÉTAIL PAR RAISON D'IGNORER (AUC postulées contre cette raison)",
    ]
    if not result["by_reason"]:
        lines.append("  Aucune offre ignorée à la main.")
    for row in result["by_reason"]:
        lines.append(
            f"  - {row['label']} : {row['count']} offre(s), {row['scored']} notée(s) v4, "
            f"moyenne {_fmt(row['mean_v4'], 1)}, AUC {_fmt(row['auc_v4'])}"
        )
    lines.append("")
    threshold = int(result["high_threshold"])
    lines.append(f"OFFRES IGNORÉES MAIS NOTÉES >= {threshold} (pistes d'ajustement du prompt)")
    if not result["high_ignored"]:
        lines.append("  Aucune.")
    for job in result["high_ignored"]:
        reason = ignore_reason_label(job.get("rejection_reason")) or NO_REASON
        comment = str(job.get("rejection_comment") or "").strip()
        suffix = f" « {comment} »" if comment else ""
        lines.append(
            f"  - [{job.get('v4_class') or '?'} {int(round(float(job['v4_display_score'])))}] "
            f"{job.get('title') or 'Sans titre'} — {job.get('company') or '?'} : {reason}{suffix}"
        )
    return "\n".join(lines)


def run(db_path: Path, high_threshold: float = DEFAULT_HIGH_THRESHOLD) -> tuple[dict[str, Any], str]:
    """Copie la base dans un dossier temporaire, évalue et renvoie (résultat, rapport)."""
    if not db_path.exists():
        raise FileNotFoundError(f"Base introuvable : {db_path}")
    with tempfile.TemporaryDirectory(prefix="eval_feedback_") as tmp:
        copy_path = Path(tmp) / "copie.db"
        copy_database_readonly(db_path, copy_path)
        jobs = load_decision_jobs(copy_path)
    result = evaluate(jobs, high_threshold=high_threshold)
    return result, format_report(result)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AUC de la note v4 contre les décisions d'Eddy (lecture seule).")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="Base SQLite à évaluer (copiée, jamais modifiée).")
    parser.add_argument(
        "--seuil",
        type=float,
        default=DEFAULT_HIGH_THRESHOLD,
        help="Note v4 à partir de laquelle une offre ignorée est listée (défaut : 70).",
    )
    args = parser.parse_args(argv)
    try:
        _, report = run(args.db, high_threshold=args.seuil)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
