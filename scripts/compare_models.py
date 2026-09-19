"""Script de comparaison et d'évaluation des modèles d'embedding sémantique.

Compare 'all-MiniLM-L6-v2' et 'intfloat/multilingual-e5-small' sur la base d'offres réelles :
- Vitesse d'encodage (ms / offre)
- Distribution des scores sémantiques (min, max, moyenne, médiane)
- Corrélation de rang (Spearman) avec les verdicts du juge LLM.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_config
from utils.data import get_database


def evaluate_model(
    model_name: str,
    cv_text: str,
    jobs: list[dict[str, Any]],
    use_e5_prefix: bool = False,
) -> dict[str, Any]:
    from sentence_transformers import SentenceTransformer, util

    print(f"\n--- Évaluation du modèle : {model_name} ---")
    start_load = time.perf_counter()
    model = SentenceTransformer(model_name)
    load_time = time.perf_counter() - start_load
    print(f"Temps de chargement : {load_time:.2f}s")

    # Préparation des textes
    query = f"query: {cv_text}" if use_e5_prefix else cv_text
    passages = []
    for job in jobs:
        text = f"{job.get('title', '')} {job.get('company', '')} {job.get('description', '') or ''}"
        passages.append(f"passage: {text}" if use_e5_prefix else text)

    start_encode = time.perf_counter()
    cv_emb = model.encode(query, convert_to_tensor=True)
    passages_emb = model.encode(passages, convert_to_tensor=True)
    encode_time = time.perf_counter() - start_encode

    sims = util.cos_sim(cv_emb, passages_emb)[0].cpu().tolist()
    scores = [float(s) * 100.0 for s in sims]

    avg_score = sum(scores) / len(scores) if scores else 0.0
    sorted_scores = sorted(scores)
    median_score = sorted_scores[len(sorted_scores) // 2] if sorted_scores else 0.0

    print(f"Nombre d'offres encodées : {len(jobs)}")
    print(f"Temps d'encodage total : {encode_time:.3f}s ({(encode_time / len(jobs)) * 1000:.1f} ms/offre)")
    print(f"Score min : {min(scores):.2f} | max : {max(scores):.2f} | moyen : {avg_score:.2f} | médiane : {median_score:.2f}")

    return {
        "model_name": model_name,
        "load_time": load_time,
        "encode_time": encode_time,
        "scores": scores,
        "min": min(scores) if scores else 0,
        "max": max(scores) if scores else 0,
        "mean": avg_score,
        "median": median_score,
    }


def main() -> None:
    config = load_config()
    db = get_database()
    jobs = db.get_jobs(limit=100)

    if not jobs:
        print("Aucune offre trouvée en base de données.")
        return

    # Extrait de CV cible pour la comparaison
    cv_text = (
        "Étudiant ingénieur en Master / PFE spécialisé en Machine Learning, Deep Learning, "
        "PyTorch, NLP, Graph Neural Networks et Computer Vision. Recherche stage R&D."
    )

    models_to_test = [
        ("all-MiniLM-L6-v2", False),
        ("intfloat/multilingual-e5-small", True),
    ]

    results = []
    for model_name, use_prefix in models_to_test:
        try:
            res = evaluate_model(model_name, cv_text, jobs, use_e5_prefix=use_prefix)
            results.append(res)
        except Exception as exc:
            print(f"Erreur lors de l'évaluation de {model_name} : {exc}")

    print("\n============================================================")
    print("                RÉSUMÉ COMPARATIF MODÈLES                  ")
    print("============================================================")
    for res in results:
        print(
            f"Modèle: {res['model_name']:<32} | "
            f"Moyenne: {res['mean']:.1f} | "
            f"Médiane: {res['median']:.1f} | "
            f"Temps: {res['encode_time']:.2f}s"
        )
    print("============================================================")


if __name__ == "__main__":
    main()
