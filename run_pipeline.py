"""Lancement du pipeline complet : collecte → filtrage → SQLite → scoring → reranking.

Raccourci documenté (README) strictement équivalent à :

```bash
python run_scrapers.py --trigger-scoring --trigger-rerank
```

Enchaînement :
  1. ``ScraperManager`` collecte les offres (WTTJ / LinkedIn / JobTeaser) ;
  2. filtre anti-BI puis ingestion SQLite **idempotente** (déduplication id + URL) ;
  3. étape 1 du ranking : score Bi-Encoder ``all-MiniLM-L6-v2`` des offres non notées ;
  4. étape 2 du ranking : reranking LLM (DeepSeek) du Top-N non encore analysé
     (ignoré proprement si ``DEEPSEEK_API_KEY`` est absente).

Note : l'ancien scraper ``src.ingestion.wttj`` (API v1 ``/api/v1/jobs``) est **obsolète** —
l'API a été retirée par Welcome to the Jungle fin 2024 (réponse **404** vérifiée). La
collecte passe désormais exclusivement par le module unifié ``scrapers``.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from run_scrapers import main as run_scrapers_main  # noqa: E402


def main() -> None:
    """Collecte, score et reranke les offres (pipeline complet en une commande)."""
    run_scrapers_main(["--trigger-scoring", "--trigger-rerank"])


if __name__ == "__main__":
    main()



