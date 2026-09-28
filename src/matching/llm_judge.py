"""Juge LLM (Gemini) — étape 2 du ranking (LLM-as-a-Judge).

La clé API est lue depuis le fichier .env (variable GEMINI_API_KEY).
Aucune exception ne remonte : en cas d'erreur, un fallback défensif est renvoyé.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types
from google.genai.errors import APIError
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception, RetryCallState

from src.config import PROJECT_ROOT, load_config
from src.constants import (
    BENCHMARK_PENALTY,
    CONTRACT_TYPES,
    DEFAULT_SUB_SCORE,
    FLAGS,
    HARD_CAP_RULES,
    MAX_BONUS_TOTAL,
    SIGNAL_BONUSES,
    STRUCTURE_TYPES,
    SUB_SCORE_KEYS,
    SUB_SCORE_WEIGHTS,
    VERDICT_EXCELLENT,
    VERDICT_GOOD,
    VERDICT_MIXED,
    VERDICT_OFF_TOPIC,
    coerce_sub_score,
    first_number,
)
from src.matching.scorer import Scorer

DEFAULT_MODEL = "gemini-3.8-flash"

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

_EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
_PHONE_RE = re.compile(r"(?:(?:\+|00)33|0)\s*[1-9](?:[\s.-]*\d{2}){4}")
_URL_RE = re.compile(r"https?://(?:www\.)?(?:linkedin\.com|github\.com)[^\s)]+", re.IGNORECASE)

logger = logging.getLogger("src.matching.llm_judge")


@dataclass
class ScoreBreakdown:
    """Résultat détaillé et déterministe du calcul de score v3."""
    quality_score: int
    final_score: int
    floor_value: int | None
    floor_reason: str | None
    cap_applied: str | None
    cap_value: int | None
    excluded: bool
    exclusion_reason: str | None
    scaleup_suggested: bool


def get_system_prompt() -> str:
    prompt_path = PROJECT_ROOT / "data" / "prompt_rerank.txt"
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    return "Tu es un évaluateur d'offres de stage."


def load_env_file(path: str | Path | None = None) -> None:
    """Charge les variables d'un fichier .env ou de st.secrets dans os.environ (sans écraser l'existant)."""
    try:
        import streamlit as st
        if hasattr(st, "secrets"):
            for k, v in st.secrets.items():
                if isinstance(v, str) and k not in os.environ:
                    os.environ[k] = v
    except Exception:
        pass

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


def verdict_from_score(score: float, config: dict[str, Any] | None = None) -> str:
    """Déduit un verdict à partir d'un score 0-100 et des seuils config."""
    cfg = (config or load_config()).get("scoring_v3", {}).get("verdict_thresholds", {})
    t_exc = float(cfg.get("excellent", 85))
    t_good = float(cfg.get("good", 70))
    t_mixed = float(cfg.get("mixed", 50))
    if score >= t_exc:
        return VERDICT_EXCELLENT
    if score >= t_good:
        return VERDICT_GOOD
    if score >= t_mixed:
        return VERDICT_MIXED
    return VERDICT_OFF_TOPIC


def normalize_text_for_evidence(text: str) -> str:
    """Normalise un texte : minuscules, sans accents, ponctuation remplacée par espaces."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(text).casefold())
    ascii_clean = "".join(c for c in decomposed if not unicodedata.combining(c))
    cleaned = re.sub(r"[^\w\s]", " ", ascii_clean)
    return re.sub(r"\s+", " ", cleaned).strip()


_GLOBAL_CITATION_STATS = {"checked": 0, "verified": 0, "rejected": 0}


def verify_citation(citation: str | None, full_text: str, tolerance: float = 0.8) -> bool:
    """Vérifie si une citation apparaît dans le texte de l'offre (tolérance 80% des mots)."""
    global _GLOBAL_CITATION_STATS
    if not citation or not full_text:
        return False
    norm_cit = normalize_text_for_evidence(citation)
    if not norm_cit or norm_cit in ("non precise", "non", "null", "none"):
        return False

    norm_doc = normalize_text_for_evidence(full_text)
    _GLOBAL_CITATION_STATS["checked"] += 1

    # 1. Correspondance exacte en sous-chaîne
    if norm_cit in norm_doc:
        _GLOBAL_CITATION_STATS["verified"] += 1
        return True

    words_cit = norm_cit.split()
    if not words_cit:
        _GLOBAL_CITATION_STATS["rejected"] += 1
        return False

    if len(words_cit) <= 2:
        ok = norm_cit in norm_doc
        if ok:
            _GLOBAL_CITATION_STATS["verified"] += 1
        else:
            _GLOBAL_CITATION_STATS["rejected"] += 1
        return ok

    # 2. Fenêtre glissante avec tolérance 80%
    words_doc = norm_doc.split()
    doc_len = len(words_doc)
    cit_len = len(words_cit)
    required_matches = int(math.ceil(tolerance * cit_len))
    target_set = set(words_cit)
    window_size = cit_len + 6

    for i in range(max(1, doc_len - window_size + 1)):
        window = set(words_doc[i : i + window_size])
        if len(target_set.intersection(window)) >= required_matches:
            _GLOBAL_CITATION_STATS["verified"] += 1
            return True

    _GLOBAL_CITATION_STATS["rejected"] += 1
    logger.info("Citation non vérifiée : %r", citation)
    return False


def is_title_excluded_contract(title: str, exclusion_keywords: list[str]) -> tuple[bool, str | None]:
    """Exclusion AVANT appel LLM si le titre contient un mot exclu SANS mention de stage."""
    norm_title = normalize_text_for_evidence(title)
    if "stage" in norm_title or "intern" in norm_title or "pfe" in norm_title:
        return False, None
    for kw in exclusion_keywords:
        norm_kw = normalize_text_for_evidence(kw)
        if norm_kw and re.search(rf"(?<![\w]){re.escape(norm_kw)}(?![\w])", norm_title):
            return True, f"Titre contient '{kw}' sans mention de stage"
    return False, None


def anonymize_cv(cv_text: str) -> str:
    """Retire les coordonnées personnelles du texte du CV avant transmission au LLM."""
    if not cv_text:
        return ""
    text = _EMAIL_RE.sub("[EMAIL_MASQUÉ]", cv_text)
    text = _PHONE_RE.sub("[TÉLÉPHONE_MASQUÉ]", text)
    text = _URL_RE.sub("[PROFIL_MASQUÉ]", text)
    return text


def compute_final_score(
    parsed: dict[str, Any],
    job: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> ScoreBreakdown:
    """Calcule la note finale pure et testable sans effet de bord ni appel réseau."""
    cfg = config or load_config()
    scoring_v3 = cfg.get("scoring_v3", {})
    min_duration = int(scoring_v3.get("min_duration_months", 4))
    floors_cfg = scoring_v3.get("floors", {})
    scaleup_floor = int(floors_cfg.get("scaleup", 70))
    rd_floor = int(floors_cfg.get("rd", 60))
    labo_public_floor = int(floors_cfg.get("labo_public", 50))
    min_tech_depth = int(floors_cfg.get("min_technical_depth", 3))
    trust_llm_scaleup = bool(floors_cfg.get("trust_llm_scaleup", False))

    bonuses_cfg = scoring_v3.get("bonuses", {})
    bonus_cap = int(bonuses_cfg.get("bonus_cap", MAX_BONUS_TOTAL))
    benchmark_penalty = int(bonuses_cfg.get("benchmark_penalty", BENCHMARK_PENALTY))

    companies_cfg = cfg.get("companies", {})
    scaleup_list = companies_cfg.get("scaleup", [])
    rd_groups_list = companies_cfg.get("rd_groups", [])
    excluded_defense_list = companies_cfg.get("excluded_defense", [])

    doc_text = f"{job.get('title', '')}\n{job.get('description', '')}"

    # 1. Vérification d'exclusion amont (titre)
    exclusion_kws = scoring_v3.get("exclusion_contract_keywords", [])
    title_excluded, title_reason = is_title_excluded_contract(job.get("title", ""), exclusion_kws)
    if title_excluded:
        return ScoreBreakdown(
            quality_score=0,
            final_score=0,
            floor_value=None,
            floor_reason=None,
            cap_applied=None,
            cap_value=None,
            excluded=True,
            exclusion_reason=title_reason,
            scaleup_suggested=False,
        )

    # 2. Vérification d'exclusion aval (LLM)
    contract_type = str(parsed.get("contract_type") or "AUTRE").upper()
    if contract_type in ("ALTERNANCE", "CDI_CDD"):
        return ScoreBreakdown(
            quality_score=0,
            final_score=0,
            floor_value=None,
            floor_reason=None,
            cap_applied=None,
            cap_value=None,
            excluded=True,
            exclusion_reason=f"Type de contrat incompatible ({contract_type})",
            scaleup_suggested=False,
        )

    is_cesure = bool(parsed.get("is_cesure", False))
    if is_cesure:
        return ScoreBreakdown(
            quality_score=0,
            final_score=0,
            floor_value=None,
            floor_reason=None,
            cap_applied=None,
            cap_value=None,
            excluded=True,
            exclusion_reason="Stage de césure / court hors PFE",
            scaleup_suggested=False,
        )

    duration = parsed.get("duration_months")
    if duration is not None:
        try:
            d_val = int(duration)
            if d_val < min_duration:
                return ScoreBreakdown(
                    quality_score=0,
                    final_score=0,
                    floor_value=None,
                    floor_reason=None,
                    cap_applied=None,
                    cap_value=None,
                    excluded=True,
                    exclusion_reason=f"Durée inférieure au seuil minimal ({d_val} mois < {min_duration} mois)",
                    scaleup_suggested=False,
                )
        except (ValueError, TypeError):
            pass

    # 3. Note de qualité
    sub_scores_raw = parsed.get("sub_scores") or {}
    sub_scores: dict[str, int] = {}
    for key in SUB_SCORE_KEYS:
        val = sub_scores_raw.get(key)
        sub_scores[key] = coerce_sub_score(val) if val is not None else DEFAULT_SUB_SCORE

    weighted_sum = sum(SUB_SCORE_WEIGHTS[k] * sub_scores[k] for k in SUB_SCORE_KEYS)
    q_base = (weighted_sum - 1.0) / 4.0 * 100.0

    # Signaux & bonus
    signals = parsed.get("signals") or {}
    total_bonus = 0
    penalty = 0

    for sig_name in ("encadrant_explicite", "donnees_reelles_explicites", "suite_explicite"):
        sig_data = signals.get(sig_name) or {}
        if sig_data.get("present"):
            ev = sig_data.get("evidence")
            if verify_citation(ev, doc_text):
                total_bonus += SIGNAL_BONUSES.get(sig_name, 0)
            else:
                red_flags = parsed.setdefault("red_flags", [])
                tag = f"[CITATION_NON_VERIFIEE] {sig_name}"
                if tag not in red_flags:
                    red_flags.append(tag)

    total_bonus = min(total_bonus, bonus_cap)

    # Signal négatif données de benchmark seulement
    bench_data = signals.get("donnees_benchmark_seulement") or {}
    if bench_data.get("present"):
        ev = bench_data.get("evidence")
        if verify_citation(ev, doc_text):
            penalty += benchmark_penalty
        else:
            red_flags = parsed.setdefault("red_flags", [])
            tag = "[CITATION_NON_VERIFIEE] donnees_benchmark_seulement"
            if tag not in red_flags:
                red_flags.append(tag)

    quality = max(0, min(100, int(round(q_base + total_bonus - penalty))))

    # 4. Plancher (Floor)
    company_name = job.get("company", "")
    structure_type = str(parsed.get("structure_type") or "INCONNU").upper()
    rd_nature = bool(parsed.get("rd_nature", False))

    in_scaleup_list = any(Scorer._name_matches(company_name, str(c)) for c in scaleup_list)
    in_rd_groups = any(Scorer._name_matches(company_name, str(c)) for c in rd_groups_list)

    floor_val: int | None = None
    floor_reason: str | None = None
    scaleup_suggested = False

    if structure_type == "SCALEUP_IA" and not in_scaleup_list:
        scaleup_suggested = True

    # Détermination de l'éligibilité au plancher (par ordre de priorité)
    if in_scaleup_list and structure_type != "ESN_CONSEIL":
        floor_val = scaleup_floor
        floor_reason = f"Plancher scale-up {scaleup_floor}"
    elif (
        in_rd_groups
        or structure_type in ("GRAND_GROUPE_RD", "LABO_PRIVE")
        or (rd_nature and structure_type not in ("LABO_PUBLIC", "ESN_CONSEIL", "STARTUP_PETITE"))
    ):
        floor_val = rd_floor
        floor_reason = f"Plancher grand groupe R&D / labo privé {rd_floor}"
    elif structure_type == "LABO_PUBLIC":
        floor_val = labo_public_floor
        floor_reason = f"Plancher labo public {labo_public_floor}"
    elif scaleup_suggested and trust_llm_scaleup:
        floor_val = scaleup_floor
        floor_reason = f"Plancher scale-up LLM {scaleup_floor}"

    # Le plancher ne s'applique que si technical_depth >= min_tech_depth
    tech_depth = sub_scores.get("technical_depth", DEFAULT_SUB_SCORE)
    applied_floor: int | None = None
    if floor_val is not None:
        if tech_depth >= min_tech_depth:
            applied_floor = floor_val
        else:
            floor_reason = f"Plancher non accordé (profondeur technique {tech_depth} < {min_tech_depth})"
            floor_val = None

    score_after_floor = max(quality, applied_floor) if applied_floor is not None else quality

    # 5. Plafonds (Caps)
    cap_applied: str | None = None
    cap_val: int | None = None

    # Plafond DEFENSE automatique par liste
    in_excluded_defense = any(Scorer._name_matches(company_name, str(c)) for c in excluded_defense_list)
    if in_excluded_defense:
        cap_applied = "DEFENSE"
        cap_val = HARD_CAP_RULES.get("DEFENSE", 10)

    # Plafond déclenché par le LLM
    llm_cap_raw = str(parsed.get("hard_cap_triggered") or "").strip().upper()
    if llm_cap_raw in HARD_CAP_RULES and llm_cap_raw != "NONE":
        evidence = parsed.get("hard_cap_evidence")
        # Les plafonds DEFENSE / TRADING dus à une liste n'ont pas besoin de citation
        needs_citation = not (llm_cap_raw == "DEFENSE" and in_excluded_defense)
        cit_ok = verify_citation(evidence, doc_text) if needs_citation else True

        if cit_ok:
            candidate_val = HARD_CAP_RULES[llm_cap_raw]
            if cap_val is None or candidate_val < cap_val:
                cap_applied = llm_cap_raw
                cap_val = candidate_val
        else:
            red_flags = parsed.setdefault("red_flags", [])
            tag = f"[CITATION_NON_VERIFIEE] {llm_cap_raw}"
            if tag not in red_flags:
                red_flags.append(tag)

    final_score = min(score_after_floor, cap_val) if cap_val is not None else score_after_floor

    return ScoreBreakdown(
        quality_score=quality,
        final_score=final_score,
        floor_value=floor_val,
        floor_reason=floor_reason,
        cap_applied=cap_applied,
        cap_value=cap_val,
        excluded=False,
        exclusion_reason=None,
        scaleup_suggested=scaleup_suggested,
    )

class DailyQuotaExceededError(Exception):
    """Exception levée lorsque le quota journalier de requêtes (RPD) est épuisé."""
    pass


class BaseLLMProvider:
    """Interface abstraite pour les fournisseurs LLM."""

    def generate(self, contents: str, system_prompt: str, schema: dict[str, Any] | None = None) -> str:
        raise NotImplementedError


class OpenAICompatibleProvider(BaseLLMProvider):
    """Fournisseur de secours compatible OpenAI (Groq, Cerebras, Ollama, etc.).

    Non activé par défaut (llm.fallback_provider: null dans config.yaml).
    Permet de basculer sur des modèles open-weight gratuits avec un endpoint OpenAI standard.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.groq.com/openai/v1",
        model: str = "llama-3.3-70b-versatile",
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

    def generate(self, contents: str, system_prompt: str, schema: dict[str, Any] | None = None) -> str:
        import requests
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": contents},
            ],
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
        }
        resp = requests.post(f"{self.base_url}/chat/completions", headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        return str(data["choices"][0]["message"]["content"])


def get_response_schema() -> dict[str, Any]:
    """Retourne le JSON Schema imposant la structure et les énumérations exactes v3."""
    return {
        "type": "OBJECT",
        "properties": {
            "information_level": {
                "type": "STRING",
                "enum": ["COMPLET", "PARTIEL", "INSUFFISANT"],
            },
            "contract_type": {
                "type": "STRING",
                "enum": list(CONTRACT_TYPES),
            },
            "contract_evidence": {"type": "STRING"},
            "duration_months": {"type": "INTEGER", "nullable": True},
            "is_cesure": {"type": "BOOLEAN"},
            "evidence": {
                "type": "OBJECT",
                "properties": {
                    "structure": {"type": "STRING"},
                    "technical_depth": {"type": "STRING"},
                    "target_alignment": {"type": "STRING"},
                    "learning_environment": {"type": "STRING"},
                    "logistics": {"type": "STRING"},
                    "rd_nature": {"type": "STRING"},
                },
            },
            "signals": {
                "type": "OBJECT",
                "properties": {
                    "encadrant_explicite": {
                        "type": "OBJECT",
                        "properties": {"present": {"type": "BOOLEAN"}, "evidence": {"type": "STRING"}},
                        "required": ["present", "evidence"],
                    },
                    "donnees_reelles_explicites": {
                        "type": "OBJECT",
                        "properties": {"present": {"type": "BOOLEAN"}, "evidence": {"type": "STRING"}},
                        "required": ["present", "evidence"],
                    },
                    "suite_explicite": {
                        "type": "OBJECT",
                        "properties": {"present": {"type": "BOOLEAN"}, "evidence": {"type": "STRING"}},
                        "required": ["present", "evidence"],
                    },
                    "donnees_benchmark_seulement": {
                        "type": "OBJECT",
                        "properties": {"present": {"type": "BOOLEAN"}, "evidence": {"type": "STRING"}},
                        "required": ["present", "evidence"],
                    },
                },
            },
            "reasoning": {"type": "STRING"},
            "structure_type": {
                "type": "STRING",
                "enum": list(STRUCTURE_TYPES),
            },
            "category_confidence": {
                "type": "STRING",
                "enum": ["HAUTE", "MOYENNE", "FAIBLE"],
            },
            "rd_nature": {"type": "BOOLEAN"},
            "hard_cap_triggered": {
                "type": "STRING",
                "enum": ["NONE"] + list(HARD_CAP_RULES.keys()),
            },
            "hard_cap_evidence": {"type": "STRING", "nullable": True},
            "sub_scores": {
                "type": "OBJECT",
                "nullable": True,
                "properties": {
                    "technical_depth": {"type": "INTEGER"},
                    "target_alignment": {"type": "INTEGER"},
                    "learning_environment": {"type": "INTEGER"},
                    "logistics": {"type": "INTEGER"},
                },
            },
            "flags": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
            },
            "company_note": {
                "type": "OBJECT",
                "properties": {
                    "known": {"type": "BOOLEAN"},
                    "note": {"type": "STRING"},
                    "confidence": {"type": "STRING", "enum": ["HAUTE", "MOYENNE", "FAIBLE"]},
                },
            },
            "match_reasons": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
            },
            "red_flags": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
            },
            "tech_stack_detected": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
            },
            "questions_entretien": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
            },
        },
        "required": [
            "information_level",
            "contract_type",
            "structure_type",
            "category_confidence",
            "rd_nature",
            "hard_cap_triggered",
            "reasoning",
        ],
    }


class GeminiRateLimiter:
    """Régulateur de débit global thread-safe pour l'API Gemini.

    - Respecte le plafond RPM (ex: 15 requêtes/minute en Free Tier).
    - Respecte le plafond journalier RPD (ex: 1500 requêtes/jour).
    - En cas d'erreur 429, bloque TOUS les threads pour la durée exacte
      demandée par l'API (retry-after jusqu'à 65s).
    """

    def __init__(self, rpm: int = 15, rpd: int = 1500) -> None:
        self.rpm = max(1, rpm)
        self.rpd = max(1, rpd)
        self.interval = 60.0 / self.rpm
        self._lock = threading.Lock()
        self._last_call_time: float = 0.0
        self._blocked_until: float = 0.0
        self._daily_calls: int = 0
        self._day_start: float = time.time()

    def wait_for_slot(self) -> float:
        """Attend le prochain créneau disponible ou lève DailyQuotaExceededError si quota journalier atteint."""
        with self._lock:
            now = time.time()
            # Réinitialisation après 24 heures
            if now - self._day_start >= 86400:
                self._daily_calls = 0
                self._day_start = now

            if self._daily_calls >= self.rpd:
                raise DailyQuotaExceededError(
                    f"Quota journalier Gemini atteint ({self._daily_calls}/{self.rpd} req/jour). "
                    "Arrêt propre du traitement par lot."
                )

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
            self._daily_calls += 1
            return total_waited

    def report_429(self, retry_after: float) -> None:
        """Déclenche une pause globale sur tous les threads."""
        with self._lock:
            target = time.time() + max(1.0, retry_after)
            if target > self._blocked_until:
                self._blocked_until = target


_GLOBAL_RATE_LIMITER: GeminiRateLimiter | None = None
_LIMITER_LOCK = threading.Lock()


def get_global_rate_limiter(rpm: int = 15, rpd: int = 1500) -> GeminiRateLimiter:
    global _GLOBAL_RATE_LIMITER
    with _LIMITER_LOCK:
        if _GLOBAL_RATE_LIMITER is None or _GLOBAL_RATE_LIMITER.rpm != rpm or _GLOBAL_RATE_LIMITER.rpd != rpd:
            _GLOBAL_RATE_LIMITER = GeminiRateLimiter(rpm=rpm, rpd=rpd)
        return _GLOBAL_RATE_LIMITER


def _is_api_retryable(exc: BaseException) -> bool:
    if isinstance(exc, DailyQuotaExceededError):
        return False
    if isinstance(exc, APIError):
        return exc.code in (503, 429) or "quota" in str(exc).lower() or "demand" in str(exc).lower()
    return False


def _log_retry(retry_state: RetryCallState) -> None:
    exc = retry_state.outcome.exception()
    delay = retry_state.next_action.sleep if retry_state.next_action else 0
    logger.warning(
        "Quota/charge Gemini atteint (%s). Pause automatique %.1fs (Essai %d/5)...",
        type(exc).__name__, delay, retry_state.attempt_number
    )


def _as_str_list(value: Any) -> list[str]:
    """Normalise une valeur hétérogène en liste de chaînes non vides."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def _normalize_sub_scores(value: Any) -> dict[str, int]:
    """Normalise les 4 sous-scores v3."""
    if not isinstance(value, dict):
        return {key: DEFAULT_SUB_SCORE for key in SUB_SCORE_KEYS}
    result = {}
    for key in SUB_SCORE_KEYS:
        val = value.get(key)
        result[key] = coerce_sub_score(val) if val is not None else DEFAULT_SUB_SCORE
    return result


class LLMJudge:
    """Étape 2 : ré-évaluation fine d'une offre via l'API Gemini (Grille v3)."""

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
        default_rpm = 15 if self.tier == "free" else 120
        self.rpm = int(llm_cfg.get("rate_limit_rpm", default_rpm))
        self.rpd = int(llm_cfg.get("rate_limit_rpd", 1500))
        self.rate_limiter = rate_limiter or get_global_rate_limiter(self.rpm, self.rpd)
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
        raw_tier = job.get("company_tier")
        tier_label_map = {1: "tier_1", 2: "neutre", 3: "esn"}
        tier_label = tier_label_map.get(raw_tier, "neutre" if raw_tier != 1 else "tier_1")
        
        user_content = (
            "<offre>\n"
            f"Titre : {job.get('title', '')}\n"
            f"Entreprise : {job.get('company', '')} (typologie tier {tier_label})\n"
            f"Localisation : {job.get('location', '')}\n\n"
            f"Description :\n{description or '(description indisponible)'}\n"
            "</offre>"
        )
        if cv_text:
            anonymized = anonymize_cv(cv_text)
            user_content += f"\n\n<cv_candidat>\n{anonymized[:6000]}\n</cv_candidat>"
        return user_content

    # ------------------------------------------------------------------ #
    # Appel API
    # ------------------------------------------------------------------ #
    @retry(
        wait=wait_exponential(multiplier=2, min=4, max=60),
        stop=stop_after_attempt(5),
        retry=retry_if_exception(_is_api_retryable),
        after=_log_retry,
        reraise=True
    )
    def _post_chat(self, user_content: str) -> str:
        """Appelle l'API Gemini et retourne le contenu texte (avec retries défensifs)."""
        logging.getLogger("google_genai.models").setLevel(logging.ERROR)
        client = self._client or genai.Client(api_key=self.api_key)
        
        self.rate_limiter.wait_for_slot()
        response = client.models.generate_content(
            model=self.model,
            contents=user_content,
            config=types.GenerateContentConfig(
                system_instruction=get_system_prompt(),
                temperature=self.temperature,
                response_mime_type="application/json",
                response_schema=get_response_schema(),
            )
        )
        return response.text


    # ------------------------------------------------------------------ #
    # Parsing / normalisation v3
    # ------------------------------------------------------------------ #
    def _parse_response(self, content: str | None, job: dict[str, Any]) -> dict[str, Any]:
        """Parse la réponse JSON du LLM en structure normalisée v3 avec compute_final_score."""
        cleaned = _CODE_FENCE_RE.sub("", content or "").strip()
        try:
            data = json.loads(cleaned)
        except (json.JSONDecodeError, TypeError) as exc:
            return self._fallback(job, reason=f"Réponse LLM non parsable ({exc}).")
        if not isinstance(data, dict):
            return self._fallback(job, reason="Réponse LLM invalide (JSON non-objet).")

        info_level = str(data.get("information_level") or "").strip().upper()
        if info_level not in ("COMPLET", "PARTIEL", "INSUFFISANT"):
            info_level = "PARTIEL" if data.get("sub_scores") else "INSUFFISANT"

        contract_type = str(data.get("contract_type") or "AUTRE").strip().upper()
        if contract_type not in CONTRACT_TYPES:
            contract_type = "AUTRE"

        duration_months = data.get("duration_months")
        if duration_months is not None:
            try:
                duration_months = int(duration_months)
            except (ValueError, TypeError):
                duration_months = None

        is_cesure = bool(data.get("is_cesure", False))

        structure_type = str(data.get("structure_type") or "INCONNU").strip().upper()
        if structure_type not in STRUCTURE_TYPES:
            structure_type = "INCONNU"

        cat_conf = str(data.get("category_confidence") or "MOYENNE").strip().upper()
        if cat_conf not in ("HAUTE", "MOYENNE", "FAIBLE"):
            cat_conf = "MOYENNE"

        rd_nature = bool(data.get("rd_nature", False))

        # Sous-scores
        raw_sub = data.get("sub_scores")
        if info_level == "INSUFFISANT" or raw_sub is None:
            sub_scores = {key: DEFAULT_SUB_SCORE for key in SUB_SCORE_KEYS}
        else:
            sub_scores = _normalize_sub_scores(raw_sub)

        # Signaux
        raw_signals = data.get("signals")
        signals = {}
        if isinstance(raw_signals, dict):
            for sig_key in ("encadrant_explicite", "donnees_reelles_explicites", "suite_explicite", "donnees_benchmark_seulement"):
                sig_item = raw_signals.get(sig_key)
                if isinstance(sig_item, dict):
                    signals[sig_key] = {
                        "present": bool(sig_item.get("present", False)),
                        "evidence": str(sig_item.get("evidence") or "").strip(),
                    }
                else:
                    signals[sig_key] = {"present": False, "evidence": "non précisé"}
        else:
            signals = {
                sig_key: {"present": False, "evidence": "non précisé"}
                for sig_key in ("encadrant_explicite", "donnees_reelles_explicites", "suite_explicite", "donnees_benchmark_seulement")
            }

        # Company note
        raw_note = data.get("company_note")
        if isinstance(raw_note, dict):
            company_note = {
                "known": bool(raw_note.get("known", False)),
                "note": str(raw_note.get("note") or "").strip()[:200],
                "confidence": str(raw_note.get("confidence") or "MOYENNE").upper(),
            }
        else:
            company_note = {"known": False, "note": "", "confidence": "FAIBLE"}

        # Flags autorisés
        raw_flags = _as_str_list(data.get("flags"))
        valid_flags = [f.upper() for f in raw_flags if f.upper() in FLAGS]

        raw_red_flags = _as_str_list(data.get("red_flags"))
        red_flags_list = list(raw_red_flags)

        parsed_struct = {
            "information_level": info_level,
            "contract_type": contract_type,
            "contract_evidence": data.get("contract_evidence"),
            "duration_months": duration_months,
            "is_cesure": is_cesure,
            "structure_type": structure_type,
            "category_confidence": cat_conf,
            "rd_nature": rd_nature,
            "sub_scores": sub_scores,
            "signals": signals,
            "company_note": company_note,
            "hard_cap_triggered": data.get("hard_cap_triggered"),
            "hard_cap_evidence": data.get("hard_cap_evidence"),
            "flags": valid_flags,
            "red_flags": red_flags_list,
            "match_reasons": _as_str_list(data.get("match_reasons")),
            "tech_stack": _as_str_list(data.get("tech_stack_detected")),
            "questions_entretien": _as_str_list(data.get("questions_entretien")),
            "evidence": data.get("evidence") if isinstance(data.get("evidence"), dict) else {},
        }

        # Calcul déterministe via compute_final_score
        breakdown = compute_final_score(parsed_struct, job, self.config)

        if breakdown.excluded:
            verdict = "EXCLU"
            score = 0.0
        elif info_level == "INSUFFISANT":
            verdict = VERDICT_OFF_TOPIC
            score = float(breakdown.final_score)
        else:
            verdict = verdict_from_score(breakdown.final_score, self.config)
            score = float(breakdown.final_score)

        reasoning_text = str(data.get("reasoning") or "").strip()
        questions = parsed_struct["questions_entretien"]
        if questions:
            q_formatted = "\n\n💡 Questions clés pour l'entretien :\n" + "\n".join(f"• {q}" for q in questions)
            combined_reasoning = (reasoning_text + q_formatted).strip()
        else:
            combined_reasoning = reasoning_text

        # Formatage des red flags avec flags
        flags_formatted = [f"[{f}]" for f in valid_flags if f and f != "NONE"]
        combined_red_flags = flags_formatted + [
            r for r in parsed_struct["red_flags"] if not any(r.startswith(f) for f in flags_formatted)
        ]

        return {
            "rerank_score": score,
            "quality_score": breakdown.quality_score,
            "final_score": breakdown.final_score,
            "floor_value": breakdown.floor_value,
            "floor_reason": breakdown.floor_reason,
            "cap_applied": breakdown.cap_applied,
            "cap_value": breakdown.cap_value,
            "excluded": breakdown.excluded,
            "exclusion_reason": breakdown.exclusion_reason,
            "scaleup_suggested": breakdown.scaleup_suggested,
            "contract_type": contract_type,
            "structure_type": structure_type,
            "category_confidence": cat_conf,
            "rd_nature": rd_nature,
            "duration_months": duration_months,
            "is_cesure": is_cesure,
            "signals": signals,
            "company_note": company_note,
            "grading_version": "v3",
            "verdict": verdict,
            "sub_scores": sub_scores,
            "hard_cap_triggered": breakdown.cap_applied,
            "hard_cap_evidence": data.get("hard_cap_evidence"),
            "reasoning": combined_reasoning,
            "flags": valid_flags,
            "match_reasons": parsed_struct["match_reasons"],
            "red_flags": combined_red_flags,
            "tech_stack": parsed_struct["tech_stack"],
            "questions_entretien": questions,
            "evidence": parsed_struct["evidence"],
            "information_level": info_level,
        }

    def _fallback(
        self, job: dict[str, Any], reason: str = "", is_api_error: bool = False
    ) -> dict[str, Any]:
        """Fallback défensif : réutilise le score initial ou signale l'erreur API."""
        score = None if is_api_error else 0.0
        verdict = None if is_api_error else VERDICT_OFF_TOPIC
        return {
            "rerank_score": score,
            "quality_score": 0,
            "final_score": score,
            "floor_value": None,
            "floor_reason": None,
            "cap_applied": None,
            "cap_value": None,
            "excluded": False,
            "exclusion_reason": None,
            "scaleup_suggested": False,
            "contract_type": "AUTRE",
            "structure_type": "INCONNU",
            "category_confidence": "FAIBLE",
            "rd_nature": False,
            "duration_months": None,
            "is_cesure": False,
            "signals": {},
            "company_note": {"known": False, "note": "", "confidence": "FAIBLE"},
            "grading_version": "v3",
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
        """Évalue une offre avec exclusion amont sans appel LLM. Ne lève JAMAIS d'exception."""
        # 1. Vérification d'exclusion amont par le titre (économise le quota)
        exclusion_kws = self.config.get("scoring_v3", {}).get("exclusion_contract_keywords", [])
        title_excluded, title_reason = is_title_excluded_contract(job.get("title", ""), exclusion_kws)
        if title_excluded:
            return {
                "rerank_score": 0.0,
                "quality_score": 0,
                "final_score": 0,
                "floor_value": None,
                "floor_reason": None,
                "cap_applied": None,
                "cap_value": None,
                "excluded": True,
                "exclusion_reason": title_reason,
                "scaleup_suggested": False,
                "contract_type": "AUTRE",
                "structure_type": "INCONNU",
                "category_confidence": "HAUTE",
                "rd_nature": False,
                "duration_months": None,
                "is_cesure": False,
                "signals": {},
                "company_note": {"known": False, "note": "", "confidence": "FAIBLE"},
                "grading_version": "v3",
                "verdict": "EXCLU",
                "sub_scores": {key: DEFAULT_SUB_SCORE for key in SUB_SCORE_KEYS},
                "hard_cap_triggered": None,
                "hard_cap_evidence": None,
                "reasoning": f"Offre exclue avant appel LLM : {title_reason}.",
                "flags": [],
                "match_reasons": [],
                "red_flags": [f"[EXCLU] {title_reason}"],
                "tech_stack": [],
                "questions_entretien": [],
                "evidence": {},
                "information_level": "COMPLET",
            }

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

