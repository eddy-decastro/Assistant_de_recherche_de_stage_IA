"""Fournisseurs LLM, régulateur de débit global et politique de retry du juge."""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

import requests
from google.genai.errors import APIError
from tenacity import RetryCallState

logger = logging.getLogger("src.matching.llm_providers")


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
        resp = requests.post(f"{self.base_url}/chat/completions", headers=headers, json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        return str(data["choices"][0]["message"]["content"])


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
    if isinstance(exc, requests.HTTPError):
        status = getattr(exc.response, "status_code", None)
        return status in (429, 500, 502, 503, 504)
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return True
    return False


def _log_retry(retry_state: RetryCallState) -> None:
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    delay = retry_state.next_action.sleep if retry_state.next_action else 0
    logger.warning(
        "Quota/charge Gemini atteint (%s). Pause automatique %.1fs (Essai %d/5)...",
        type(exc).__name__, delay, retry_state.attempt_number
    )
