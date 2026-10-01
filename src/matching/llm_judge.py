"""Juge LLM (Gemini) — étape 2 du ranking (LLM-as-a-Judge).

La clé API est lue depuis le fichier .env (variable GEMINI_API_KEY).
Aucune exception ne remonte : en cas d'erreur, un fallback défensif est renvoyé.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import requests
from google import genai
from google.genai import types
from google.genai.errors import APIError
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from src.config import load_config
from src.constants import (
    CONTRACT_TYPES,
    DEFAULT_SUB_SCORE,
    FLAGS,
    STRUCTURE_TYPES,
    SUB_SCORE_KEYS,
    VERDICT_EXCELLENT,
    VERDICT_GOOD,
    VERDICT_MIXED,
    VERDICT_OFF_TOPIC,
    coerce_sub_score,
)
from src.env import load_env_file  # noqa: F401  (ré-export : ancien emplacement)
from src.matching.judge_schema import get_response_schema, get_system_prompt
from src.matching.llm_providers import (  # noqa: F401  (ré-exports : ancien emplacement)
    BaseLLMProvider,
    DailyQuotaExceededError,
    GeminiRateLimiter,
    OpenAICompatibleProvider,
    _is_api_retryable,
    _log_retry,
    get_global_rate_limiter,
)
from src.matching.scoring_v3 import (  # noqa: F401  (ré-exports : ancien emplacement)
    ScoreBreakdown,
    compute_final_score,
    is_title_excluded_contract,
    normalize_text_for_evidence,
    verdict_from_score,
    verify_citation,
)

DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_OPENAI_BASE_URL = "https://api.deepseek.com"

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


def anonymize_cv(cv_text: str) -> str:
    """Retire les coordonnées personnelles du texte du CV avant transmission au LLM."""
    if not cv_text:
        return ""
    text = _EMAIL_RE.sub("[EMAIL_MASQUÉ]", cv_text)
    text = _PHONE_RE.sub("[TÉLÉPHONE_MASQUÉ]", text)
    text = _URL_RE.sub("[PROFIL_MASQUÉ]", text)
    return text


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
        self.provider = str(llm_cfg.get("provider") or "google").casefold()
        self.base_url = str(llm_cfg.get("base_url") or DEFAULT_OPENAI_BASE_URL)

        if api_key is not None:
            self.api_key = api_key
        else:
            load_env_file()
            self.api_key = os.environ.get(self.api_key_env, "")

    @property
    def openai_compatible(self) -> bool:
        """True si le juge passe par un endpoint compatible OpenAI (DeepSeek, Groq...)."""
        return self.provider != "google"

    @property
    def api_key_env(self) -> str:
        """Nom de la variable d'environnement qui porte la clé du fournisseur actif."""
        if self.provider == "google":
            return "GEMINI_API_KEY"
        return f"{self.provider.upper()}_API_KEY"

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
        """Appelle l'API du fournisseur actif et retourne le contenu texte (avec retries défensifs)."""
        if self.openai_compatible:
            self.rate_limiter.wait_for_slot()
            system_prompt = (
                f"{get_system_prompt()}\n\nRéponds uniquement par un objet JSON respectant "
                f"ce JSON Schema :\n{json.dumps(get_response_schema(), ensure_ascii=False)}"
            )
            return OpenAICompatibleProvider(
                api_key=self.api_key, base_url=self.base_url, model=self.model
            ).generate(user_content, system_prompt)
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
                job, reason=f"{self.api_key_env} absente — analyse LLM ignorée.", is_api_error=True
            )
        try:
            content = self._post_chat(self._build_content(job, cv_text))
            return self._parse_response(content, job)
        except APIError as exc:
            return self._fallback(
                job, reason=f"Erreur API Gemini ({exc.code} - {exc.message}).", is_api_error=True
            )
        except requests.RequestException as exc:
            return self._fallback(
                job, reason=f"Erreur API {self.provider} ({exc}).", is_api_error=True
            )
        except Exception as exc:
            return self._fallback(
                job, reason=f"Réponse API Gemini inattendue : {exc}.", is_api_error=True
            )

