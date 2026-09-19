"""Scoring hybride des offres : sémantique + typologie + mots-clés.

Formule (pondérations issues de config.yaml) :
    final = 60% similarité sémantique + 25% typologie + 15% mots-clés
"""
from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any

_model_lock = threading.Lock()

from src.config import load_config
from src.constants import TIER_1, TIER_ESN, TIER_NEUTRAL


def _contains_keyword(text: str, keyword: str) -> bool:
    """Recherche un mot-clé avec frontières de mot (insensible à la casse).

    Les expressions multi-mots sont cherchées en sous-chaîne ; les tokens
    simples utilisent une frontière de mot pour éviter les faux positifs
    (ex. ``rag`` dans ``courage``).
    """
    kw = keyword.casefold()
    lowered = text.casefold()
    # On applique les frontières de mots même pour les expressions multi-mots
    # (ex: éviter de matcher "deep learning" dans "notdeep learning")
    # Pour supporter l'alphabet français, on exclut les lettres accentuées communes.
    # On utilise \w pour inclure les lettres accentuées si le flag re.IGNORECASE ou re.UNICODE est actif (défaut).
    # Mais (?<!\w) fonctionne bien avec re.UNICODE.
    return re.search(rf"(?<![\w]){re.escape(kw)}(?![\w])", lowered) is not None






class Scorer:
    """Calcule un score final (0-100) pour chaque offre."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or load_config()
        scoring = self.config.get("scoring", {})
        self.weights = scoring.get(
            "weights", {"semantic": 0.60, "company": 0.25, "keywords": 0.15}
        )
        self.model_name = scoring.get("model_name", "all-MiniLM-L6-v2")
        # Fenêtre de contexte (tokens) — ``None`` = fenêtre native du modèle. Mesuré
        # sur la base réelle (68 fiches, verdicts du juge LLM) : l'écart 128 / 256 est
        # dans le bruit à n=30 (rho +0,01 contre +0,09, erreur type ~0,19), la fenêtre
        # native est donc conservée ; le levier reste pilotable si la base grossit.
        self.max_seq_length = scoring.get("max_seq_length")
        self.keywords = scoring.get("excellence_keywords", [])
        
        # Load from config
        self.keywords_saturation = scoring.get("keywords_saturation", 5)
        
        tier_scores = scoring.get("tier_company_score", {})
        self.tier_company_score = {
            TIER_1: float(tier_scores.get("tier_1", 100.0)),
            TIER_NEUTRAL: float(tier_scores.get("neutral", 60.0)),
            TIER_ESN: float(tier_scores.get("esn", 20.0)),
        }

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
            with _model_lock:
                if self._model is None:
                    from sentence_transformers import SentenceTransformer
                    self._model = SentenceTransformer(self.model_name)
                    if self.max_seq_length:
                        self._model.max_seq_length = int(self.max_seq_length)
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
    # Nombre de mots-clés « cœur » suffisant pour saturer le sous-score. Une
    # proportion linéaire sur toute la liste (3/19 = 15,8 %) rendait ce terme quasi
    # constant, donc non discriminant : il ne distinguait pas une offre
    # PyTorch+GNN d'une offre sans aucune compétence clé.
    # (defined via config, read into self.keywords_saturation)

    def keywords_score(self, text: str) -> float:
        """Sous-score mots-clés d'excellence (0-100) à courbe saturante.

        ``min(n / KEYWORDS_SATURATION, 1) × 100`` : détecter 5 mots-clés clés suffit
        à atteindre 100. Allonger la liste dans config.yaml n'écrase donc plus
        mécaniquement le score des offres les plus exigeantes.

        Les mots-clés sont cherchés avec frontières de mot pour éviter les faux
        positifs (ex. « RAG » dans « cou**rag**e » ou « f**rag**ile »).
        """
        if not self.keywords:
            return 0.0
        lowered = text.casefold()
        saturation = self.keywords_saturation
        found_count = len([kw for kw in self.keywords if _contains_keyword(text, kw)])
        return min(found_count / saturation, 1.0) * 100.0

    def company_score(self, tier: int) -> float:
        return self.tier_company_score.get(tier, self.tier_company_score[TIER_NEUTRAL])

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
        # Utilisation de frontières de mot pour éviter les correspondances
        # partielles ("Atos" dans "Pathos", "Cap" dans "Capgemini").
        return _contains_keyword(name, entry)

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
