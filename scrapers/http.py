"""Client HTTP résilient : nouvelles tentatives avec attente croissante.

Un blocage passager (429, 502/503, timeout) ne doit pas coûter une passe entière.
``RetryingClient`` réessaie de façon transparente pour tous les appels
(``get``/``post``…), donc sans modifier les scrapers.

Règles :

* statuts réessayés : 429 et 5xx passagers (500, 502, 503, 504) ; les 4xx (403,
  404…) ne le sont jamais — les rejouer ne fait qu'aggraver un blocage ;
* erreurs réseau (``httpx.TransportError``, timeouts inclus) réessayées ;
* ``Retry-After`` respecté, mais plafonné : au-delà, on rend la réponse telle
  quelle (la passe s'arrête proprement en ``rate_limit``) plutôt que de bloquer
  le cron ;
* à court de tentatives, la dernière réponse (ou exception) remonte inchangée :
  le traitement d'erreur existant des scrapers reste valable.
"""
from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

import httpx

logger = logging.getLogger("scrapers.http")

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


def parse_retry_after(value: str | None) -> float | None:
    """Délai en secondes d'un en-tête ``Retry-After`` (entier ou date HTTP)."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


class RetryingClient(httpx.Client):
    """``httpx.Client`` qui rejoue les requêtes échouées de façon transitoire."""

    def __init__(
        self,
        *args,
        max_retries: int = 3,
        backoff_seconds: float = 2.0,
        max_wait_seconds: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.max_retries = max(0, int(max_retries))
        self.backoff_seconds = max(0.0, float(backoff_seconds))
        self.max_wait_seconds = max(0.0, float(max_wait_seconds))
        self._sleep = sleep
        #: Nombre de rejeux effectués (visible pour le rapport / les tests).
        self.retries_done = 0

    def _delay(self, attempt: int, response: httpx.Response | None) -> float | None:
        """Attente avant le prochain essai, ou ``None`` si on doit renoncer."""
        if response is not None:
            asked = parse_retry_after(response.headers.get("Retry-After"))
            if asked is not None:
                return asked if asked <= self.max_wait_seconds else None
        base = self.backoff_seconds * (2**attempt)
        return min(self.max_wait_seconds, base + random.uniform(0, base * 0.25))

    def send(self, request: httpx.Request, **kwargs) -> httpx.Response:  # type: ignore[override]
        attempt = 0
        while True:
            try:
                response = super().send(request, **kwargs)
            except httpx.TransportError as exc:
                if attempt >= self.max_retries:
                    raise
                delay = self._delay(attempt, None)
                reason = type(exc).__name__
            else:
                if response.status_code not in RETRY_STATUSES or attempt >= self.max_retries:
                    return response
                delay = self._delay(attempt, response)
                if delay is None:  # Retry-After trop long : on rend la main
                    return response
                reason = f"HTTP {response.status_code}"
                response.close()
            attempt += 1
            self.retries_done += 1
            logger.info(
                "Nouvelle tentative %d/%d dans %.1f s (%s) : %s",
                attempt, self.max_retries, delay, reason, request.url.host,
            )
            self._sleep(delay)
