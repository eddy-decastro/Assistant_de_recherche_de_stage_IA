"""Scoring hybride des offres : sémantique + typologie + mots-clés.

Formule (pondérations issues de config.yaml) :
    final = 60% similarité sémantique + 25% typologie + 15% mots-clés
"""
from __future__ import annotations

import re
import threading
import unicodedata
from pathlib import Path
from typing import Any

_model_lock = threading.Lock()

from src.config import load_config
from src.constants import TIER_1, TIER_ESN, TIER_NEUTRAL

SHORT_OR_GENERIC_COMPANIES: set[str] = {
    "nw",
    "tse",
    "bump",
    "positive",
    "swan",
    "homa",
    "iten",
    "mwm",
    "dust",
    "waat",
    "jimmy",
    "alma",
    "malt",
    "qair",
    "yubo",
}

COMPANY_ALIASES: dict[str, list[str]] = {
    "nw": ["nw", "nw groupe", "nw storm"],
    "tse": ["tse", "tse energy", "tse energie"],
    "bump": ["bump", "bump charge"],
    "positive": ["positive company", "positive technologies"],
    "swan": ["swan", "swan.io", "swan banking"],
    "homa": ["homa", "homa games"],
    "iten": ["iten", "iten batteries"],
    "mwm": ["mwm", "mwm music"],
    "dust": ["dust", "dust.tt"],
    "waat": ["waat", "waat recharge"],
}


def _normalize_name(text: str) -> str:
    """Normalise un texte : minuscules, suppression des accents et compactage des espaces."""
    decomposed = unicodedata.normalize("NFKD", str(text or "").strip().casefold())
    ascii_clean = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", ascii_clean).strip()


def _contains_keyword(text: str, keyword: str) -> bool:
    """Recherche un mot-clé avec frontières de mot (insensible à la casse et aux accents)."""
    kw = _normalize_name(keyword)
    lowered = _normalize_name(text)
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
        self.max_seq_length = scoring.get("max_seq_length")
        self.keywords = scoring.get("excellence_keywords", [])
        self.penalty_keywords = scoring.get("penalty_keywords", ["n8n", "make.com", "zapier"])
        
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
        similarity = float(embedding @ self._cv_embedding)
        return max(0.0, min(1.0, similarity)) * 100.0

    # ------------------------------------------------------------------ #
    # Sous-scores
    # ------------------------------------------------------------------ #
    def keywords_score(self, text: str) -> float:
        """Sous-score mots-clés d'excellence avec pénalités no-code (0-100)."""
        if not self.keywords:
            return 0.0
        found_count = len([kw for kw in self.keywords if _contains_keyword(text, kw)])
        base_score = min(found_count / self.keywords_saturation, 1.0) * 100.0

        # Pénalités (n8n, make.com, zapier)
        penalty_count = sum(1 for kw in self.penalty_keywords if _contains_keyword(text, kw))
        malus = penalty_count * 15.0
        return max(0.0, base_score - malus)

    def company_score(self, tier: int) -> float:
        return self.tier_company_score.get(tier, self.tier_company_score[TIER_NEUTRAL])

    def determine_tier(self, company: str) -> int:
        """Détermine la typologie d'entreprise à partir des listes de config.yaml."""
        raw_name = (company or "").strip()
        if not raw_name:
            return TIER_NEUTRAL

        companies = self.config.get("companies", {})
        excluded_defense = companies.get("excluded_defense", [])
        for entry in excluded_defense:
            if self._name_matches(raw_name, str(entry)):
                return TIER_ESN

        esn = companies.get("esn", [])
        for entry in esn:
            if self._name_matches(raw_name, str(entry)):
                return TIER_ESN

        dual_use = companies.get("dual_use", [])
        for entry in dual_use:
            if self._name_matches(raw_name, str(entry)):
                return TIER_NEUTRAL

        tier_1 = companies.get("tier_1", [])
        for entry in tier_1:
            if self._name_matches(raw_name, str(entry)):
                return TIER_1

        scaleup = companies.get("scaleup", [])
        for entry in scaleup:
            if self._name_matches(raw_name, str(entry)):
                return TIER_1

        rd_groups = companies.get("rd_groups", [])
        for entry in rd_groups:
            if self._name_matches(raw_name, str(entry)):
                return TIER_1

        return TIER_NEUTRAL

    @staticmethod
    def _name_matches(name: str, entry: str) -> bool:
        if not entry or not name:
            return False
        norm_name = _normalize_name(name)
        norm_entry = _normalize_name(entry)
        if not norm_name or not norm_entry:
            return False
        if norm_name == norm_entry:
            return True

        # Noms courts ou mots génériques : correspondance exacte ou alias stricts
        if norm_entry in SHORT_OR_GENERIC_COMPANIES:
            aliases = COMPANY_ALIASES.get(norm_entry, [norm_entry])
            norm_aliases = [_normalize_name(a) for a in aliases]
            return norm_name in norm_aliases

        # Cas général : recherche avec frontières de mot
        return _contains_keyword(norm_name, norm_entry)

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

