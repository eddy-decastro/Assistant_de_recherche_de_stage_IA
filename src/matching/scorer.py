"""Scoring hybride des offres : sémantique + typologie + mots-clés.

Formule (pondérations issues de config.yaml) :
    final = 60% similarité sémantique + 25% typologie + 15% mots-clés
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from src.config import load_config
from src.constants import TIER_1, TIER_ESN, TIER_NEUTRAL

TIER_COMPANY_SCORE = {
    TIER_1: 100.0,
    TIER_NEUTRAL: 60.0,
    TIER_ESN: 20.0,
}


class Scorer:
    """Calcule un score final (0-100) pour chaque offre."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or load_config()
        scoring = self.config.get("scoring", {})
        self.weights = scoring.get(
            "weights", {"semantic": 0.60, "company": 0.25, "keywords": 0.15}
        )
        self.model_name = scoring.get("model_name", "all-MiniLM-L6-v2")
        self.keywords = scoring.get("excellence_keywords", [])

        cv_path = Path(scoring.get("cv_path", "data/cv_eddy.txt"))
        self.cv_text = cv_path.read_text(encoding="utf-8") if cv_path.exists() else ""

        # Chargés paresseusement pour ne pas imposer torch à l'import.
        self._model = None
        self._cv_embedding = None

    # ------------------------------------------------------------------ #
    # Embeddings
    # ------------------------------------------------------------------ #
    def _ensure_model(self) -> None:
        if self._model is None:
            from sentence_transformers import SentenceTransformer  # lazy import

            self._model = SentenceTransformer(self.model_name)
            self._cv_embedding = self._model.encode(
                self.cv_text, normalize_embeddings=True, show_progress_bar=False
            )

    def semantic_score(self, text: str) -> float:
        """Similarité cosinus entre le CV et le texte de l'offre (0-100)."""
        if not text.strip() or not self.cv_text.strip():
            return 0.0
        self._ensure_model()
        embedding = self._model.encode(
            text, normalize_embeddings=True, show_progress_bar=False
        )
        # Les vecteurs sont normalisés : le produit scalaire = similarité cosinus.
        similarity = float(embedding @ self._cv_embedding)
        return max(0.0, min(1.0, similarity)) * 100.0

    # ------------------------------------------------------------------ #
    # Sous-scores
    # ------------------------------------------------------------------ #
    def keywords_score(self, text: str) -> float:
        """Part des mots-clés d'excellence présents dans l'offre (0-100)."""
        if not self.keywords:
            return 0.0
        lowered = text.casefold()
        present = sum(1 for kw in self.keywords if kw.casefold() in lowered)
        return (present / len(self.keywords)) * 100.0

    @staticmethod
    def company_score(tier: int) -> float:
        return TIER_COMPANY_SCORE.get(tier, 50.0)

    def determine_tier(self, company: str) -> int:
        """Détermine la typologie d'entreprise à partir des listes de config.yaml."""
        name = (company or "").strip().casefold()
        if not name:
            return TIER_NEUTRAL

        companies = self.config.get("companies", {})
        tier_1 = [str(c).strip().casefold() for c in companies.get("tier_1", [])]
        esn = [str(c).strip().casefold() for c in companies.get("esn", [])]

        for entry in tier_1:
            if self._name_matches(name, entry):
                return TIER_1
        for entry in esn:
            if self._name_matches(name, entry):
                return TIER_ESN
        return TIER_NEUTRAL

    @staticmethod
    def _name_matches(name: str, entry: str) -> bool:
        if not entry:
            return False
        if name == entry:
            return True
        # Correspondance par sous-chaîne uniquement pour les noms suffisamment
        # longs, afin d'éviter les faux positifs (ex: "Alan" vs "Analytics").
        if len(entry) >= 5:
            return entry in name or name in entry
        return False

    # ------------------------------------------------------------------ #
    # Score final
    # ------------------------------------------------------------------ #
    def score(self, job: dict[str, Any]) -> dict[str, Any]:
        """Calcule les scores et retourne une copie enrichie de l'offre."""
        text = f"{job.get('title', '')}\n{job.get('description', '')}"
        tier = self.determine_tier(job.get("company", ""))

        semantic = self.semantic_score(text)
        company = self.company_score(tier)
        keywords = self.keywords_score(text)

        final = (
            self.weights["semantic"] * semantic
            + self.weights["company"] * company
            + self.weights["keywords"] * keywords
        )

        scored = dict(job)
        scored["company_tier"] = tier
        scored["semantic_score"] = round(semantic, 2)
        scored["final_score"] = round(final, 2)
        return scored
