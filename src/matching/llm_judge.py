"""Juge LLM (Gemini) — étape 2 du ranking (LLM-as-a-Judge).

La clé API est lue depuis le fichier .env (variable GEMINI_API_KEY).
Aucune exception ne remonte : en cas d'erreur, un fallback défensif est renvoyé.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types
from google.genai.errors import APIError

from src.config import PROJECT_ROOT, load_config
from src.constants import (
    DEFAULT_SUB_SCORE,
    SUB_SCORE_KEYS,
    SUB_SCORE_WEIGHTS,
    VERDICT_EXCELLENT,
    VERDICT_GOOD,
    VERDICT_MIXED,
    VERDICT_OFF_TOPIC,
    coerce_sub_score,
    first_number,
)

DEFAULT_MODEL = "gemini-3.1-flash-lite"

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

# Plafonds stricts autorisés par la grille de cadrage
HARD_CAP_RULES: dict[str, int] = {
    "ALTERNANCE": 15,
    "NOT_A_PFE": 15,
    "BI_REPORTING": 30,
    "FINANCE": 50,
    "SHALLOW_AI": 40,
}

def get_system_prompt() -> str:
    prompt_path = PROJECT_ROOT / "data" / "prompt_rerank.txt"
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    return "Tu es un évaluateur d'offres de stage."



def load_env_file(path: str | Path | None = None) -> None:
    """Charge les variables d'un fichier .env ou de st.secrets dans os.environ (sans écraser l'existant)."""
    # 1. Si exécuté dans Streamlit (Streamlit Community Cloud)
    try:
        import streamlit as st
        if hasattr(st, "secrets"):
            for k, v in st.secrets.items():
                if isinstance(v, str) and k not in os.environ:
                    os.environ[k] = v
    except Exception:
        pass

    # 2. Depuis le fichier .env local
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
    """Déduit un verdict (fallback) à partir d'un score 0-100.

    Bandes alignées sur la grille du juge : EXCELLENT ≥ 85, BON ≥ 65, MITIGÉ ≥ 40,
    HORS_SUJET en deçà.
    """
    if score >= 85:
        return VERDICT_EXCELLENT
    if score >= 65:
        return VERDICT_GOOD
    if score >= 40:
        return VERDICT_MIXED
    return VERDICT_OFF_TOPIC


def calculate_score_from_sub_scores(sub_scores: dict[str, int] | None) -> int:
    """Calcule la note globale 0-100 à partir des 5 sous-scores (échelle 1-5).

    Pondérations :
    - modeling_depth : 30%
    - mentorship_team : 25%
    - engineering_practice : 20%
    - option_value : 15%
    - logistics : 10%

    Formule de conversion linéaire :
    W = sum(poids * sous_score) dans [1.0, 5.0]
    score = (W - 1.0) / 4.0 * 100.0 (arrondi à l'entier le plus proche dans [0, 100]).
    """
    if not sub_scores:
        return 0
    weighted_sum = sum(
        SUB_SCORE_WEIGHTS.get(key, 0.20) * coerce_sub_score(sub_scores.get(key, DEFAULT_SUB_SCORE))
        for key in SUB_SCORE_KEYS
    )
    score = (weighted_sum - 1.0) / 4.0 * 100.0
    return max(0, min(100, int(round(score))))


def _as_str_list(value: Any) -> list[str]:
    """Normalise une valeur hétérogène en liste de chaînes non vides."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


# --- Verrous bloquants (hard caps) ------------------------------------------- #
# Le prompt instruit le LLM de spécifier hard_cap_triggered ("NONE", "ALTERNANCE", etc.)
# Le code applique un plafonnement strict et déterministe.

#: Préfixes de négation courants dans les réponses LLM.
_NEGATION_PREFIXES: tuple[str, ...] = (
    "aucun ", "aucune ", "pas de ", "pas d'", "non ", "sans ",
    "no ", "none ", "n/a", "not ", "rien",
)


def _normalize_hard_cap(value: Any) -> str | None:
    """Normalise ``hard_cap_triggered`` en chaîne canonique, ou ``None`` si aucun verrou."""
    text = str(value or "").strip()
    if not text or text.casefold() in ("null", "none", "aucun", "aucune", "n/a"):
        return None
    lowered = text.casefold()
    if any(lowered.startswith(prefix) for prefix in _NEGATION_PREFIXES):
        return None
    upper = text.upper()
    if upper in HARD_CAP_RULES:
        return upper
    return text


def hard_cap_max(reason: str | None) -> int | None:
    """Plafond associé à un verrou bloquant (``None`` = pas de plafond)."""
    if not reason:
        return None
    upper = reason.strip().upper()
    if upper in HARD_CAP_RULES:
        return HARD_CAP_RULES[upper]
    if upper == "NONE":
        return None
    lowered = reason.casefold()
    for cap_name, max_score in HARD_CAP_RULES.items():
        if cap_name.lower() in lowered:
            return max_score
    config = load_config()
    hard_caps_list = config.get("scoring", {}).get("hard_caps", [])
    for cap in hard_caps_list:
        keywords = cap.get("keywords", [])
        max_score = cap.get("max_score", 100)
        for keyword in keywords:
            if re.search(rf"(?<![\w]){re.escape(keyword.casefold())}(?![\w])", lowered):
                return max_score
    return None


def _key_signature(name: Any) -> str:
    """Signature insensible à la casse, aux accents et aux séparateurs d'une clé.

    ``"modeling_depth"``, ``"modelingDepth"`` et ``"Modeling Depth"`` produisent la
    même signature (``modelingdepth``).
    """
    decomposed = unicodedata.normalize("NFKD", str(name or "").casefold())
    ascii_only = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]", "", ascii_only)


#: Correspondance signature -> clé canonique des sous-scores (tolérance de forme).
_SUB_SCORE_ALIASES: dict[str, str] = {_key_signature(key): key for key in SUB_SCORE_KEYS}


def _normalize_sub_scores(value: Any) -> dict[str, int]:
    """Normalise ``sub_scores`` en dict complet ``{clé: entier 1-5}``.

    Les clés sont comparées **après normalisation** (casse, accents, séparateurs) :
    un modèle qui répond ``modelingDepth``, ``modeling depth`` ou
    ``Modeling-Depth`` est compris, sans perdre l'information. Champ manquant,
    JSON non-dictionnaire ou valeur inexploitable ⇒ valeur neutre
    (``DEFAULT_SUB_SCORE``) : l'interface reste stable.
    """
    if not isinstance(value, dict):
        return {key: DEFAULT_SUB_SCORE for key in SUB_SCORE_KEYS}
    provided: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        canonical = _SUB_SCORE_ALIASES.get(_key_signature(raw_key))
        if canonical is not None and canonical not in provided:
            provided[canonical] = raw_value
    return {
        key: coerce_sub_score(provided.get(key, DEFAULT_SUB_SCORE)) for key in SUB_SCORE_KEYS
    }


logger = logging.getLogger("src.matching.llm_judge")


class GeminiRateLimiter:
    """Régulateur de débit global thread-safe pour l'API Gemini.

    - Respecte le plafond RPM (ex: 14 requêtes/minute en Free Tier).
    - En cas d'erreur 429, bloque TOUS les threads pour la durée exacte
      demandée par l'API (retry-after jusqu'à 65s).
    """

    def __init__(self, rpm: int = 14) -> None:
        self.rpm = max(1, rpm)
        self.interval = 60.0 / self.rpm
        self._lock = threading.Lock()
        self._last_call_time: float = 0.0
        self._blocked_until: float = 0.0

    def wait_for_slot(self) -> float:
        """Attend le prochain créneau disponible. Retourne le temps d'attente effectif en secondes."""
        with self._lock:
            now = time.time()
            total_waited = 0.0

            # 1. Si bloqué globalement suite à un 429
            if now < self._blocked_until:
                wait_time = self._blocked_until - now
                logger.info("  ⏳ Pause globale rate limit : attente de %.1fs...", wait_time)
                time.sleep(wait_time)
                total_waited += wait_time
                now = time.time()

            # 2. Espacement minimal entre requêtes
            elapsed = now - self._last_call_time
            if elapsed < self.interval:
                sleep_time = self.interval - elapsed
                time.sleep(sleep_time)
                total_waited += sleep_time
                now = time.time()

            self._last_call_time = now
            return total_waited

    def report_429(self, retry_after: float) -> None:
        """Déclenche une pause globale sur tous les threads."""
        with self._lock:
            target = time.time() + max(1.0, retry_after)
            if target > self._blocked_until:
                self._blocked_until = target


_GLOBAL_RATE_LIMITER: GeminiRateLimiter | None = None
_LIMITER_LOCK = threading.Lock()


def get_global_rate_limiter(rpm: int = 14) -> GeminiRateLimiter:
    global _GLOBAL_RATE_LIMITER
    with _LIMITER_LOCK:
        if _GLOBAL_RATE_LIMITER is None or _GLOBAL_RATE_LIMITER.rpm != rpm:
            _GLOBAL_RATE_LIMITER = GeminiRateLimiter(rpm)
        return _GLOBAL_RATE_LIMITER


class LLMJudge:
    """Étape 2 : ré-évaluation fine d'une offre via l'API Gemini."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        api_key: str | None = None,
        client: genai.Client | None = None,
        rate_limiter: GeminiRateLimiter | None = None,
    ) -> None:
        self.config = config or load_config()
        llm_cfg = self.config.get("llm", {})
        self.model = str(llm_cfg.get("model", DEFAULT_MODEL))
        self.temperature = float(llm_cfg.get("temperature", 0.0))
        self.tier = str(llm_cfg.get("tier", "free")).casefold()
        default_rpm = 14 if self.tier == "free" else 120
        self.rpm = int(llm_cfg.get("rate_limit_rpm", default_rpm))
        self.rate_limiter = rate_limiter or get_global_rate_limiter(self.rpm)
        self._client = client  # injectable pour mock

        if api_key is not None:
            self.api_key = api_key
        else:
            load_env_file()
            self.api_key = os.environ.get("GEMINI_API_KEY", "")

    @property
    def available(self) -> bool:
        """True si une clé API est configurée."""
        return bool(self.api_key)

    # ------------------------------------------------------------------ #
    # Construction des messages
    # ------------------------------------------------------------------ #
    def _build_content(self, job: dict[str, Any], cv_text: str | None = None) -> str:
        description = (job.get("description") or "").strip()[:12000]
        user_content = (
            "<offre>\n"
            f"Titre : {job.get('title', '')}\n"
            f"Entreprise : {job.get('company', '')} (typologie tier {job.get('company_tier', '?')})\n"
            f"Localisation : {job.get('location', '')}\n\n"
            f"Description :\n{description or '(description indisponible)'}\n"
            "</offre>"
        )
        if cv_text:
            user_content += f"\n\n<cv_candidat>\n{cv_text[:6000]}\n</cv_candidat>"
        return user_content

    # ------------------------------------------------------------------ #
    # Appel API
    # ------------------------------------------------------------------ #
    def _post_chat(self, user_content: str) -> str:
        """Appelle l'API Gemini et retourne le contenu texte (avec retries défensifs)."""
        # Silence les avertissements AFC verbeux du SDK google_genai
        logging.getLogger("google_genai.models").setLevel(logging.ERROR)

        client = self._client or genai.Client(api_key=self.api_key)
        last_error = None

        for attempt in range(4):
            # Régulation de débit avant chaque requête (respecte les 14 RPM)
            self.rate_limiter.wait_for_slot()
            try:
                response = client.models.generate_content(
                    model=self.model,
                    contents=user_content,
                    config=types.GenerateContentConfig(
                        system_instruction=get_system_prompt(),
                        temperature=self.temperature,
                        response_mime_type="application/json"
                    )
                )
                return response.text
            except APIError as exc:
                last_error = exc
                # Si erreur de quota (429) ou surcharge temporaire (503)
                if attempt < 3 and (exc.code in (503, 429) or "quota" in str(exc).lower() or "demand" in str(exc).lower()):
                    delay = 5.0 * (attempt + 1)
                    match = re.search(r"retry in (\d+(?:\.\d+)?)s", str(exc), re.IGNORECASE)
                    if match:
                        suggested = float(match.group(1))
                        delay = max(suggested + 1.5, delay)

                    logger.warning(
                        "Quota/charge Gemini atteint (%s). Pause automatique de %.1fs avant nouvel essai (%d/3)...",
                        exc.code, delay, attempt + 1
                    )
                    self.rate_limiter.report_429(delay)
                    time.sleep(delay)
                    continue
                raise
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                raise

        if last_error:
            raise last_error
        return ""

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

        info_level = str(data.get("information_level") or "").strip().upper()
        raw_sub = data.get("sub_scores")
        if info_level == "INSUFFISANT" or raw_sub is None:
            sub_scores = None
            score = 0
        else:
            sub_scores = _normalize_sub_scores(raw_sub)
            score = calculate_score_from_sub_scores(sub_scores)

        # Si le score calculé est 0 et qu'un rerank_score explicite était fourni dans une réponse d'ancien format
        if score == 0 and data.get("rerank_score") is not None and info_level != "INSUFFISANT":
            score = self._coerce_score(data.get("rerank_score"), job)

        hard_cap = _normalize_hard_cap(data.get("hard_cap_triggered"))
        cap = hard_cap_max(hard_cap)
        if cap is not None:
            score = min(score, cap)

        verdict = verdict_from_score(score)

        reasoning_text = str(data.get("reasoning") or "").strip()
        questions = _as_str_list(data.get("questions_entretien"))
        if questions:
            q_formatted = "\n\n💡 Questions clés pour l'entretien :\n" + "\n".join(f"• {q}" for q in questions)
            combined_reasoning = (reasoning_text + q_formatted).strip()
        else:
            combined_reasoning = reasoning_text

        raw_flags = _as_str_list(data.get("flags"))
        raw_red_flags = _as_str_list(data.get("red_flags"))
        flags_formatted = [f"[{f}]" for f in raw_flags if f and f != "NONE"]
        combined_red_flags = flags_formatted + [
            r for r in raw_red_flags if not any(r.startswith(f) for f in flags_formatted)
        ]

        return {
            "rerank_score": score,
            "verdict": verdict,
            "sub_scores": sub_scores if sub_scores is not None else {key: DEFAULT_SUB_SCORE for key in SUB_SCORE_KEYS},
            "hard_cap_triggered": hard_cap,
            "hard_cap_evidence": data.get("hard_cap_evidence"),
            "reasoning": combined_reasoning,
            "flags": raw_flags,
            "match_reasons": _as_str_list(data.get("match_reasons")),
            "red_flags": combined_red_flags,
            "tech_stack": _as_str_list(data.get("tech_stack_detected")),
            "questions_entretien": questions,
            "evidence": data.get("evidence") if isinstance(data.get("evidence"), dict) else {},
            "information_level": info_level or None,
        }

    @staticmethod
    def _coerce_score(value: Any, job: dict[str, Any]) -> int:
        """Convertit une valeur en entier borné 0-100, sinon retombe sur final_score."""
        number = first_number(value)
        if number is None:
            number = first_number(job.get("final_score"))
        if number is None:
            return 0
        return max(0, min(100, int(round(number))))

    def _fallback(
        self, job: dict[str, Any], reason: str = "", is_api_error: bool = False
    ) -> dict[str, Any]:
        """Fallback défensif : réutilise le score initial ou signale l'erreur API.

        Si is_api_error est True (ex: quota 429 ou panne réseau), rerank_score est None
        afin de ne pas corrompre l'offre avec une fausse note 0.0 et de permettre
        sa réévaluation future.
        """
        score = None if is_api_error else self._coerce_score(job.get("final_score"), job)
        verdict = None if is_api_error else verdict_from_score(score or 0.0)
        return {
            "rerank_score": score,
            "verdict": verdict,
            "sub_scores": {key: DEFAULT_SUB_SCORE for key in SUB_SCORE_KEYS} if not is_api_error else None,
            "hard_cap_triggered": None,
            "hard_cap_evidence": None,
            "reasoning": "",
            "flags": [],
            "match_reasons": [],
            "red_flags": [reason] if reason else [],
            "tech_stack": [],
            "questions_entretien": [],
            "evidence": {},
            "information_level": "API_ERROR" if is_api_error else "INSUFFISANT",
            "api_error": is_api_error,
        }

    # ------------------------------------------------------------------ #
    # API publique
    # ------------------------------------------------------------------ #
    def judge(self, job: dict[str, Any], cv_text: str | None = None) -> dict[str, Any]:
        """Évalue une offre. Ne lève JAMAIS d'exception (fallback garanti)."""
        if not self.available:
            return self._fallback(
                job, reason="GEMINI_API_KEY absente — analyse LLM ignorée.", is_api_error=True
            )
        try:
            content = self._post_chat(self._build_content(job, cv_text))
            return self._parse_response(content, job)
        except APIError as exc:
            return self._fallback(
                job, reason=f"Erreur API Gemini ({exc.code} - {exc.message}).", is_api_error=True
            )
        except Exception as exc:
            return self._fallback(
                job, reason=f"Réponse API Gemini inattendue : {exc}.", is_api_error=True
            )
