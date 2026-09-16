"""Comparatif avant/après la refonte des descriptions (validation de l'étape 4).

Contexte : les scores et verdicts encore en base ont été produits **sans
description** (titre seul) et, pour le juge LLM, **avec** le score bi-encoder dans
le prompt (biais d'ancrage). Ce script fige cet « avant » puis affiche l'« après »
une fois `--rescore-all` et `--trigger-rerank` relancés.

    python compare_scores.py --snapshot   # À LANCER AVANT le rescore
    python compare_scores.py --compare    # À LANCER APRÈS le rerank
    python compare_scores.py --compare --top 8

Le snapshot est un simple JSON (aucune donnée sensible) : id, titre, source,
score hybride, alignement vectoriel, rerank et verdict.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_config  # noqa: E402
from src.constants import STATUS_REJECTED, VERDICT_LABELS  # noqa: E402
from src.storage.database import Database  # noqa: E402

logger = logging.getLogger("compare")

DEFAULT_SNAPSHOT = Path("data/scores_before.json")


def effective_score(job: dict[str, Any]) -> float:
    """Score effectif affiché : rerank du juge LLM s'il existe, sinon score hybride."""
    rerank = job.get("rerank_score")
    return float(rerank) if rerank is not None else float(job.get("final_score") or 0.0)


def take_snapshot(db: Database, path: Path) -> int:
    """Enregistre l'état des scores AVANT relance (id → scores + verdict)."""
    rows = db.get_jobs()
    payload = {
        "jobs": {
            job["id"]: {
                "title": job.get("title") or "",
                "company": job.get("company") or "",
                "source": job.get("source") or "",
                "final_score": float(job.get("final_score") or 0.0),
                "semantic_score": float(job.get("semantic_score") or 0.0),
                "rerank_score": job.get("rerank_score"),
                "verdict": job.get("verdict"),
                "effective_score": round(effective_score(job), 2),
                "description_length": len(job.get("description") or ""),
            }
            for job in rows
        }
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return len(rows)


def _label(verdict: str | None) -> str:
    """Libellé lisible d'un verdict (ou tiret si absent)."""
    if not verdict:
        return "—"
    return VERDICT_LABELS.get(verdict, str(verdict))

def compare(db: Database, path: Path, top: int = 5) -> int:
    """Affiche le tableau avant/après des offres les plus impactées."""
    if not path.exists():
        logger.error(
            " Snapshot introuvable (%s) : lancez d'abord `python compare_scores.py --snapshot` "
            "AVANT le rescore.",
            path,
        )
        return 1
    before: dict[str, dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))["jobs"]
    rows = db.get_jobs()

    movers: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    for job in rows:
        old = before.get(job["id"])
        if old is None:
            continue
        delta = effective_score(job) - float(old.get("effective_score") or 0.0)
        movers.append((delta, old, job))
    movers.sort(key=lambda item: abs(item[0]), reverse=True)

    if not movers:
        logger.warning(" Aucune offre commune entre le snapshot et la base actuelle.")
        return 1

    enriched = sum(1 for job in rows if (job.get("description") or "").strip())
    logger.info("=" * 118)
    logger.info(" COMPARATIF AVANT / APRÈS — %d/%d offres documentées", enriched, len(rows))
    logger.info("=" * 118)
    logger.info(
        " %-46s %-9s %7s %7s %7s  %s",
        "Titre", "Source", "Avant", "Après", "Delta", "Verdict LLM (avant -> après)",
    )
    logger.info("-" * 118)
    for delta, old, job in movers[: max(1, top)]:
        title = (job.get("title") or "")[:44]
        logger.info(
            " %-46s %-9s %7.1f %7.1f %+7.1f  %s -> %s",
            title,
            (job.get("source") or "-")[:9],
            float(old.get("effective_score") or 0.0),
            effective_score(job),
            delta,
            _label(old.get("verdict")),
            _label(job.get("verdict")),
        )
        logger.info(
            "      fiche : %d car. (0 car. avant le rattrapage, fait mesuré) | "
            "hybride %s -> %.1f | alignement vectoriel %s -> %.1f",
            len(job.get("description") or ""),
            f"{float(old.get('final_score') or 0.0):.1f}",
            float(job.get("final_score") or 0.0),
            f"{float(old.get('semantic_score') or 0.0):.1f}",
            float(job.get("semantic_score") or 0.0),
        )
    logger.info("-" * 118)

    deltas = [delta for delta, _, _ in movers]
    documentees = [job for job in rows if (job.get("description") or "").strip()]
    moyennes = (
        sum(effective_score(job) for job in documentees) / len(documentees)
        if documentees
        else 0.0
    )
    logger.info(
        " Synthèse : score effectif moyen des offres documentées = %.1f | "
        "écart absolu moyen avant/après = %.1f | plus fort écart = %+.1f",
        moyennes,
        sum(abs(delta) for delta in deltas) / len(deltas),
        max(deltas, key=abs),
    )
    return 0


def leaderboard(db: Database, top: int = 5) -> int:
    """Affiche le Top N courant : titre, entreprise, score effectif et verdict."""
    jobs = [job for job in db.get_jobs() if job.get("status") != STATUS_REJECTED]
    logger.info("=" * 104)
    logger.info(" TOP %d — %d offre(s) active(s) en base", top, len(jobs))
    logger.info("=" * 104)
    logger.info(" %-3s %-50s %-24s %6s  %s", "#", "Titre", "Entreprise", "Score", "Verdict")
    logger.info("-" * 104)
    for rank, job in enumerate(jobs[: max(1, top)], start=1):
        judged = job.get("rerank_score") is not None
        logger.info(
            " %-3d %-50s %-24s %6.0f  %s (%s)",
            rank,
            (job.get("title") or "")[:48],
            (job.get("company") or "")[:22],
            effective_score(job),
            _label(job.get("verdict")) if judged else "non jugée",
            "LLM" if judged else "hybride",
        )
    logger.info("-" * 104)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Analyse les arguments de la ligne de commande."""
    parser = argparse.ArgumentParser(
        description="Snapshot et comparatif avant/après de la refonte des descriptions."
    )
    parser.add_argument("--snapshot", action="store_true", help="Enregistre l'état AVANT.")
    parser.add_argument("--compare", action="store_true", help="Affiche le comparatif APRÈS.")
    parser.add_argument(
        "--leaderboard", action="store_true", help="Affiche le Top N courant de la base."
    )
    parser.add_argument("--top", type=int, default=5, help="Nombre d'offres affichées (défaut 5).")
    parser.add_argument("--path", default=str(DEFAULT_SNAPSHOT), help="Fichier de snapshot JSON.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée : snapshot avant relance, ou comparatif après relance."""
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )
    config = load_config()
    db = Database(config["database"]["path"])
    path = Path(args.path)
    try:
        if args.snapshot:
            count = take_snapshot(db, path)
            logger.info(" Snapshot AVANT enregistré : %d offre(s) -> %s", count, path)
            return 0
        if args.leaderboard:
            return leaderboard(db, top=args.top)
        return compare(db, path, top=args.top)
    finally:
        db.engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
