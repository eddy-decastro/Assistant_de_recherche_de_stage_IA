"""Juge LLM (DeepSeek) — étape 2 du ranking (LLM-as-a-Judge).

Architecture Two-Stage :
  - Étape 1 : Bi-Encoder (src/matching/scorer.py) → pré-filtrage rapide, Top-N.
  - Étape 2 : ce module → ré-évaluation fine du Top-N par un LLM (DeepSeek V3).

La clé API est lue depuis le fichier .env (variable DEEPSEEK_API_KEY).
Aucune exception ne remonte : en cas d'erreur, un fallback défensif est renvoyé.

⚠️ Le juge ne reçoit VOLONTAIREMENT pas le score de l'étape 1 : les verdicts
enregistrés montraient que le modèle commentait ce score au lieu d'évaluer la
mission (biais d'ancrage). Il juge désormais sur le CV et la fiche complète.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import httpx

from src.config import PROJECT_ROOT, load_config
from src.constants import (
    VERDICT_EXCELLENT,
    VERDICT_GOOD,
    VERDICT_MIXED,
    VERDICT_OFF_TOPIC,
    VERDICTS,
)

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

# Tolérance sur les verdicts renvoyés par le modèle (accents / variantes).
_VERDICT_ALIASES = {
    "EXCELLENT": VERDICT_EXCELLENT,
    "BON": VERDICT_GOOD,
    "MITIGE": VERDICT_MIXED,
    "MITIGÉ": VERDICT_MIXED,
    "HORS_SUJET": VERDICT_OFF_TOPIC,
    "HORS SUJET": VERDICT_OFF_TOPIC,
    "HORSSUJET": VERDICT_OFF_TOPIC,
}

SYSTEM_PROMPT = """
Tu es un Lead Data Scientist qui évalue, pour un candidat précis, l'adéquation d'une offre de stage de fin d'études.

PROFIL DU CANDIDAT
- Élève-ingénieur en dernière année (École des Mines), Data Science / Machine Learning, orientation Master Recherche en Mathématiques Appliquées (type MAEA / ENS / Mines).
- Compétences : Python, PyTorch, scikit-learn, Graph ML (GNN), optimisation, NLP/LLM, Docker, Spark.

MISSION
Évaluer RIGOUREUSEMENT l'offre selon ces critères :
1. Richesse de la modélisation : PyTorch, Machine Learning, Graph ML, optimisation, probabilités, statistiques avancées (et NON un simple usage d'outils de reporting).
2. Niveau de responsabilités et d'autonomie réelles confiées au stagiaire.
3. Crédibilité et niveau de l'équipe technique (data scientists, chercheurs, équipe ML structurée).
4. Détection des offres déguisées : simple reporting Excel/PowerBI, pur support data, dashboards, saisie/nettoyage répétitif sans modélisation → à pénaliser fortement.

RÈGLES DE NOTATION (rerank_score, 0-100)
- 85-100 (EXCELLENT) : forte composante recherche / modélisation avancée (ML/DL/GNN/optimisation).
- 60-84 (BON) : vraie mission data science avec modélisation, périmètre stimulant.
- 40-59 (MITIGÉ) : data science générique ou périmètre limité, potentiel de montée en compétence.
- 0-39 (HORS_SUJET) : reporting / pur support / Business Intelligence sans modélisation.

FORMAT DE SORTIE
Réponds UNIQUEMENT par un objet JSON valide, sans texte autour, au format exact suivant :
{
  "rerank_score": <entier 0-100>,
  "verdict": "EXCELLENT" | "BON" | "MITIGÉ" | "HORS_SUJET",
  "match_reasons": ["<raison factuelle du match>", "..."],
  "red_flags": ["<point d'attention éventuel>", "..."],
  "tech_stack_detected": ["PyTorch", "SQL", "..."]
}
"""


def load_env_file(path: str | Path | None = None) -> None:
    """Charge les variables d'un fichier .env dans os.environ (sans écraser l'existant)."""
    env_path = Path(path) if path else (PROJECT_ROOT / ".env")
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def verdict_from_score(score: float) -> str:
    """Déduit un verdict (fallback) à partir d'un score 0-100."""
    if score >= 80:
        return VERDICT_EXCELLENT
    if score >= 60:
        return VERDICT_GOOD
    if score >= 40:
        return VERDICT_MIXED
    return VERDICT_OFF_TOPIC


def _as_str_list(value: Any) -> list[str]:
    """Normalise une valeur hétérogène en liste de chaînes non vides."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


class LLMJudge:
    """Étape 2 : ré-évaluation fine d'une offre via l'API DeepSeek (deepseek-chat)."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        api_key: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.config = config or load_config()
        llm_cfg = self.config.get("llm", {})
        self.base_url = str(llm_cfg.get("base_url", DEFAULT_BASE_URL)).rstrip("/")
        self.model = str(llm_cfg.get("model", DEFAULT_MODEL))
        self.temperature = float(llm_cfg.get("temperature", 0.0))
        self.timeout = float(llm_cfg.get("timeout_seconds", 60))
        self.client = client  # injectable (tests via httpx.MockTransport)

        if api_key is not None:
            self.api_key = api_key
        else:
            load_env_file()
            self.api_key = os.environ.get("DEEPSEEK_API_KEY", "")

    @property
    def available(self) -> bool:
        """True si une clé API est configurée."""
        return bool(self.api_key)

    # ------------------------------------------------------------------ #
    # Construction des messages
    # ------------------------------------------------------------------ #
    def _build_messages(
        self, job: dict[str, Any], cv_text: str | None = None
    ) -> list[dict[str, str]]:
        # Aucun score de l'étape 1 n'est transmis : constaté en pratique, le juge
        # commentait le score bi-encoder au lieu de juger la mission (« le score
        # préliminaire est faible, mais… »). Le juge doit statuer sur les FAITS
        # (CV + description complète), sans ancre numérique.
        description = (job.get("description") or "").strip()[:6000]
        user_content = (
            "OFFRE DE STAGE À ÉVALUER\n"
            f"Titre : {job.get('title', '')}\n"
            f"Entreprise : {job.get('company', '')} (typologie tier {job.get('company_tier', '?')})\n"
            f"Localisation : {job.get('location', '')}\n\n"
            f"Description :\n{description or '(description indisponible)'}\n"
        )
        if cv_text:
            user_content += f"\nCV DU CANDIDAT (extrait) :\n{cv_text[:3000]}\n"
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

    # ------------------------------------------------------------------ #
    # Appel API
    # ------------------------------------------------------------------ #
    def _post_chat(self, messages: list[dict[str, str]]) -> str:
        """Appelle l'endpoint chat/completions et retourne le contenu texte."""
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        client = self.client or httpx.Client(timeout=self.timeout)
        close_client = self.client is None
        try:
            response = client.post(
                f"{self.base_url}/chat/completions", json=payload, headers=headers
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
        finally:
            if close_client:
                client.close()

    # ------------------------------------------------------------------ #
    # Parsing / normalisation
    # ------------------------------------------------------------------ #
    def _parse_response(self, content: str | None, job: dict[str, Any]) -> dict[str, Any]:
        """Parse la réponse JSON du LLM en structure normalisée et fiable."""
        cleaned = _CODE_FENCE_RE.sub("", content or "").strip()
        try:
            data = json.loads(cleaned)
        except (json.JSONDecodeError, TypeError) as exc:
            return self._fallback(job, reason=f"Réponse LLM non parsable ({exc}).")
        if not isinstance(data, dict):
            return self._fallback(job, reason="Réponse LLM invalide (JSON non-objet).")

        score = self._coerce_score(data.get("rerank_score"), job)
        raw_verdict = str(data.get("verdict", "")).strip().upper()
        verdict = _VERDICT_ALIASES.get(raw_verdict)
        if verdict not in VERDICTS:
            verdict = verdict_from_score(score)

        return {
            "rerank_score": score,
            "verdict": verdict,
            "match_reasons": _as_str_list(data.get("match_reasons")),
            "red_flags": _as_str_list(data.get("red_flags")),
            "tech_stack": _as_str_list(data.get("tech_stack_detected")),
        }

    @staticmethod
    def _coerce_score(value: Any, job: dict[str, Any]) -> int:
        """Convertit une valeur en entier borné 0-100, sinon retombe sur final_score."""
        try:
            score = int(round(float(value)))
        except (TypeError, ValueError):
            score = int(round(float(job.get("final_score", 0.0) or 0.0)))
        return max(0, min(100, score))

    def _fallback(self, job: dict[str, Any], reason: str = "") -> dict[str, Any]:
        """Fallback défensif : réutilise le score initial et signale le problème."""
        score = self._coerce_score(job.get("final_score"), job)
        return {
            "rerank_score": score,
            "verdict": verdict_from_score(score),
            "match_reasons": [],
            "red_flags": [reason] if reason else [],
            "tech_stack": [],
        }

    # ------------------------------------------------------------------ #
    # API publique
    # ------------------------------------------------------------------ #
    def judge(self, job: dict[str, Any], cv_text: str | None = None) -> dict[str, Any]:
        """Évalue une offre. Ne lève JAMAIS d'exception (fallback garanti)."""
        if not self.available:
            return self._fallback(job, reason="DEEPSEEK_API_KEY absente — analyse LLM ignorée.")
        try:
            content = self._post_chat(self._build_messages(job, cv_text))
            return self._parse_response(content, job)
        except httpx.HTTPStatusError as exc:
            return self._fallback(
                job, reason=f"Erreur HTTP API DeepSeek ({exc.response.status_code})."
            )
        except httpx.HTTPError as exc:
            return self._fallback(job, reason=f"Erreur réseau API DeepSeek : {exc}.")
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            return self._fallback(job, reason=f"Réponse API DeepSeek inattendue : {exc}.")
