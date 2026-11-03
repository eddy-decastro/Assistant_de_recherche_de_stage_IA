"""Script d'évaluation et de comparaison côte à côte entre la grille v3 et la grille v4.

Implémente les points 2 et 3 de la Section 13 de la spécification v4 :
1. Sélection des cohortes :
   - Offres ``POSTULÉ`` (positifs réels d'Eddy)
   - Échantillon de 150 offres ``IGNORÉ`` manuellement (``rejection_reason != 'auto_archive'``)
   - Flux actif ``NOUVEAU`` non exclu
2. Option ``--grade`` : sauvegarde la base dans ``data/backups/`` puis note en v4
   les offres non encore notées de ces cohortes via ``LLMJudgeV4``.
3. Calcul de l'AUC ROC (``POSTULÉ`` vs ``IGNORÉ`` humains) pour :
   - Similarité sémantique étape 1 (``final_score``)
   - Juge v3 (``rerank_score``)
   - Juge v4 (``v4_display_score``)
4. Génération du rapport Markdown ``data/rapport_comparaison_v3_v4.md`` (Top 20 v3 vs Top 20 v4).
"""
from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from src.config import PROJECT_ROOT, load_config
from src.constants import (
    STATUS_APPLIED,
    STATUS_EXCLUDED,
    STATUS_IGNORED,
    STATUS_NEW,
    V4_AXIS_KEYS,
)
from src.matching.scoring_v4 import LLMJudgeV4
from src.storage.database import Database


def compute_roc_auc(positives: Sequence[float], negatives: Sequence[float]) -> float | None:
    """Calcule l'AUC ROC exacte (statistique U de Mann-Whitney avec gestion des ex-aequo)."""
    pos = [float(x) for x in positives if x is not None]
    neg = [float(x) for x in negatives if x is not None]
    if not pos or not neg:
        return None
    wins = 0.0
    for p in pos:
        for n in neg:
            if p > n:
                wins += 1.0
            elif p == n:
                wins += 0.5
    return wins / (len(pos) * len(neg))


def select_evaluation_cohorts(
    jobs: Sequence[dict[str, Any]],
    *,
    ignored_sample_size: int = 150,
) -> dict[str, list[dict[str, Any]]]:
    """Sépare les offres en 3 cohortes : ``applied``, ``human_ignored`` et ``active_new``."""
    applied: list[dict[str, Any]] = []
    human_ignored: list[dict[str, Any]] = []
    active_new: list[dict[str, Any]] = []

    for job in jobs:
        status = job.get("status")
        is_exclu = (
            status == STATUS_EXCLUDED
            or job.get("verdict") == "EXCLU"
            or bool(job.get("exclusion_reason"))
        )
        if status == STATUS_APPLIED:
            applied.append(job)
        elif status == STATUS_IGNORED:
            reason = str(job.get("rejection_reason") or "").strip().lower()
            notes = str(job.get("user_notes") or "").strip()
            if reason != "auto_archive" and "[auto] archivée" not in notes.lower():
                human_ignored.append(job)
        elif status == STATUS_NEW and not is_exclu:
            active_new.append(job)

    # Prioriser dans l'échantillon IGNORÉ les offres ayant déjà un rerank_score v3 (pour comparer à périmètre égal)
    human_ignored.sort(
        key=lambda j: (
            j.get("rerank_score") is not None,
            str(j.get("created_at") or ""),
        ),
        reverse=True,
    )
    return {
        "applied": applied,
        "human_ignored": human_ignored[:ignored_sample_size],
        "active_new": active_new,
    }


def _format_stars_compact(job: dict[str, Any]) -> str:
    stars_dict = job.get("v4_stars")
    if not isinstance(stars_dict, dict) or not stars_dict:
        return "—"
    parts = []
    short_map = {"apprendre": "App", "technique": "Tech", "debouche": "Déb", "logistique": "Log"}
    for k in V4_AXIS_KEYS:
        ax = stars_dict.get(k)
        s = ax.get("stars", "?") if isinstance(ax, dict) else "?"
        parts.append(f"{short_map.get(k, k)}:{s}★")
    return " ".join(parts)


def _format_range_compact(job: dict[str, Any]) -> str:
    center = job.get("v4_center")
    adj = job.get("v4_adjustment")
    if center is None:
        return "—"
    c = float(center)
    lo = max(0.0, c - 10.0)
    hi = min(100.0, c + 10.0)
    adj_str = f"{int(adj):+d}" if adj is not None else "+0"
    return f"[{lo:.0f}, {hi:.0f}] (c={c:.1f}, {adj_str})"


def build_comparison_report(
    jobs: Sequence[dict[str, Any]],
    *,
    ignored_sample_size: int = 150,
    top_n: int = 20,
) -> dict[str, Any]:
    """Construit les métriques et le rapport Markdown de comparaison v3 vs v4."""
    cohorts = select_evaluation_cohorts(jobs, ignored_sample_size=ignored_sample_size)
    applied = cohorts["applied"]
    ignored = cohorts["human_ignored"]
    active_new = cohorts["active_new"]

    auc_semantic = compute_roc_auc(
        [float(j["final_score"]) for j in applied if j.get("final_score") is not None],
        [float(j["final_score"]) for j in ignored if j.get("final_score") is not None],
    )
    auc_v3 = compute_roc_auc(
        [float(j["rerank_score"]) for j in applied if j.get("rerank_score") is not None],
        [float(j["rerank_score"]) for j in ignored if j.get("rerank_score") is not None],
    )
    auc_v4 = compute_roc_auc(
        [float(j["v4_display_score"]) for j in applied if j.get("v4_display_score") is not None],
        [float(j["v4_display_score"]) for j in ignored if j.get("v4_display_score") is not None],
    )

    applied_v4 = [j for j in applied if j.get("v4_display_score") is not None]
    applied_in_e = [j for j in applied_v4 if str(j.get("v4_class") or "").upper() == "E"]
    applied_under_50 = [j for j in applied_v4 if float(j.get("v4_display_score") or 0.0) < 50.0]

    # Top N sur le flux actif NOUVEAU (ou à défaut sur l'ensemble des offres notées)
    pool = active_new if any(j.get("rerank_score") is not None or j.get("v4_display_score") is not None for j in active_new) else list(jobs)
    top_v3 = sorted(
        [j for j in pool if j.get("rerank_score") is not None],
        key=lambda j: float(j.get("rerank_score") or 0.0),
        reverse=True,
    )[:top_n]
    top_v4 = sorted(
        [j for j in pool if j.get("v4_display_score") is not None],
        key=lambda j: float(j.get("v4_display_score") or 0.0),
        reverse=True,
    )[:top_n]

    lines = [
        "# Rapport de comparaison : Scoring v3 vs Scoring v4 (« étoiles + fourchette + classe »)",
        "",
        f"*Généré le {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*",
        "",
        "## 1. Volumétrie des cohortes d'évaluation",
        "",
        f"- **Offres `POSTULÉ` (positifs d'Eddy)** : {len(applied)} (dont {len(applied_v4)} notées en v4)",
        f"- **Offres `IGNORÉ` humaines (`rejection_reason != 'auto_archive'`)** : {len(ignored)} "
        f"(dont {sum(1 for j in ignored if j.get('v4_display_score') is not None)} notées en v4)",
        f"- **Offres `NOUVEAU` actives (hors EXCLU)** : {len(active_new)} "
        f"(dont {sum(1 for j in active_new if j.get('v4_display_score') is not None)} notées en v4)",
        "",
        "## 2. Séparation `POSTULÉ` vs `IGNORÉ` humains (ROC AUC)",
        "",
        "| Système de notation | AUC ROC (`POSTULÉ` vs `IGNORÉ` humains) | Cible spécification |",
        "|---|---|---|",
        f"| Étape 1 — Similarité sémantique MiniLM (`final_score`) | `{auc_semantic:.3f}` | Référence (~0.59) |"
        if auc_semantic is not None
        else "| Étape 1 — Similarité sémantique MiniLM (`final_score`) | `N/A` | Référence (~0.59) |",
        f"| Juge LLM v3 (`rerank_score`) | `{auc_v3:.3f}` | Référence (~0.68) |"
        if auc_v3 is not None
        else "| Juge LLM v3 (`rerank_score`) | `N/A` | Référence (~0.68) |",
        f"| **Juge LLM v4 (`v4_display_score`)** | **`{auc_v4:.3f}`** | **≥ 0.72** |"
        if auc_v4 is not None
        else "| **Juge LLM v4 (`v4_display_score`)** | `En attente de notation (--grade)` | **≥ 0.72** |",
        "",
        "## 3. Vérification des garde-fous sur les offres `POSTULÉ`",
        "",
        f"- Offres `POSTULÉ` classées en **E** : **{len(applied_in_e)}** (cible : 0)",
        f"- Offres `POSTULÉ` avec `v4_display_score < 50` : **{len(applied_under_50)}** (cible : 0)",
        "",
        f"## 4. Top {top_n} du flux actif selon la grille v4",
        "",
        "| Rang | Entreprise | Titre | Note v3 | Classe v4 | Étoiles v4 | Fourchette (centre, ajust.) | Note v4 |",
        "|---|---|---|---|---|---|---|---|",
    ]

    if top_v4:
        for idx, j in enumerate(top_v4, start=1):
            v3_s = f"{float(j['rerank_score']):.0f}" if j.get("rerank_score") is not None else "—"
            v4_s = f"**{float(j['v4_display_score']):.0f}**" if j.get("v4_display_score") is not None else "—"
            lines.append(
                f"| {idx} | {j.get('company') or '—'} | {(j.get('title') or '')[:55]} | {v3_s} | "
                f"**{j.get('v4_class') or '—'}** | {_format_stars_compact(j)} | {_format_range_compact(j)} | {v4_s} |"
            )
    else:
        lines.append("| — | *Aucune offre notée en v4* | Lancez `python evaluate_v3_v4.py --grade` | — | — | — | — | — |")

    lines.extend(
        [
            "",
            f"## 5. Top {top_n} du flux actif selon la grille v3 (référence actuelle)",
            "",
            "| Rang | Entreprise | Titre | Note v3 | Verdict v3 | Classe v4 | Note v4 |",
            "|---|---|---|---|---|---|---|",
        ]
    )

    if top_v3:
        for idx, j in enumerate(top_v3, start=1):
            v3_s = f"**{float(j['rerank_score']):.0f}**" if j.get("rerank_score") is not None else "—"
            v4_s = f"{float(j['v4_display_score']):.0f}" if j.get("v4_display_score") is not None else "—"
            lines.append(
                f"| {idx} | {j.get('company') or '—'} | {(j.get('title') or '')[:55]} | {v3_s} | "
                f"{j.get('verdict') or '—'} | {j.get('v4_class') or '—'} | {v4_s} |"
            )
    else:
        lines.append("| — | *Aucune offre notée en v3* | — | — | — | — | — |")

    return {
        "auc_semantic": auc_semantic,
        "auc_v3": auc_v3,
        "auc_v4": auc_v4,
        "applied_count": len(applied),
        "applied_v4_count": len(applied_v4),
        "applied_in_e": len(applied_in_e),
        "applied_under_50": len(applied_under_50),
        "markdown": "\n".join(lines) + "\n",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Évaluation et comparaison v3 vs v4.")
    parser.add_argument(
        "--grade",
        action="store_true",
        help="Appelle le juge LLM v4 sur les offres des cohortes non encore notées en v4.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=280,
        help="Nombre maximal d'appels LLM v4 lors d'un --grade (défaut: 280).",
    )
    parser.add_argument(
        "--ignored-sample",
        type=int,
        default=150,
        help="Taille de l'échantillon d'offres IGNORÉ humaines (défaut: 150).",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=str(PROJECT_ROOT / "data" / "rapport_comparaison_v3_v4.md"),
        help="Chemin du rapport Markdown généré.",
    )
    args = parser.parse_args()

    config = load_config()
    db_path = Path(config.get("database", {}).get("path", "data/stage_copilot.db"))
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path

    db = Database(str(db_path))
    all_jobs = db.get_jobs(limit=10000)

    if args.grade:
        backup_dir = PROJECT_ROOT / "data" / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"stage_copilot_pre_eval_v4_{stamp}.db"
        if db_path.exists():
            shutil.copy2(db_path, backup_path)
            print(f"[Backup] Base sauvegardée vers {backup_path}")

        cohorts = select_evaluation_cohorts(all_jobs, ignored_sample_size=args.ignored_sample)
        to_grade: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for group in (cohorts["applied"], cohorts["active_new"], cohorts["human_ignored"]):
            for job in group:
                jid = str(job["id"])
                if jid not in seen_ids and job.get("v4_display_score") is None and (job.get("description") or "").strip():
                    seen_ids.add(jid)
                    to_grade.append(job)

        to_grade = to_grade[: args.limit]
        print(f"[Juge v4] {len(to_grade)} offre(s) à évaluer...")
        judge = LLMJudgeV4(config=config)
        graded_ok = 0
        for idx, job in enumerate(to_grade, start=1):
            res = judge.evaluate_single_job_v4(job)
            if res.get("v4_status") == "OK":
                db.update_v4(str(job["id"]), res)
                graded_ok += 1
            print(
                f"  [{idx}/{len(to_grade)}] {job.get('company')} - {job.get('title')[:40]} -> "
                f"{res.get('v4_status')} ({res.get('v4_class')} · {res.get('v4_display_score')})"
            )
        print(f"[Juge v4] Terminé : {graded_ok}/{len(to_grade)} offre(s) notées avec succès.")
        all_jobs = db.get_jobs(limit=10000)

    report = build_comparison_report(all_jobs, ignored_sample_size=args.ignored_sample)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report["markdown"], encoding="utf-8")
    print(f"[Rapport] Écrit dans {out_path}")
    print(
        f"[Métriques] AUC sémantique={report['auc_semantic']} | "
        f"AUC v3={report['auc_v3']} | AUC v4={report['auc_v4']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
