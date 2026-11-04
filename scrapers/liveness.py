"""Détection « offre fermée » : une offre est-elle encore ouverte aux candidatures ?

Remplace l'archivage par âge (21 jours), qui archivait des offres encore ouvertes
(NAVER Labs noté 100, publiée il y a 135 jours et toujours en ligne). Ici, seule
une preuve de fermeture archive une offre ; dans le doute, on ne conclut rien.

Verdicts :

* ``ferme`` — HTTP 404/410, redirection vers la liste des offres (Greenhouse,
  Workable…), ou page HTTP 200 qui affiche la fermeture. Marqueurs vérifiés le
  04/11/2026 sur de vraies offres de la base :

  - LinkedIn : « Les candidatures ne sont plus acceptées » (classe ``closed-job``),
    en anglais « No longer accepting applications » ;
  - WTTJ : « Ce job n'est plus disponible » ;
  - Inria : page « Erreur » (« la page que vous cherchez […] ne peut pas être trouvée ») ;
  - Workday : fiche JSON en 404 (``errorCode S21``) ou ``posted``/``canApply`` à ``false`` ;
  - Lever, Ashby, SmartRecruiters : 404 ; Greenhouse : redirection vers le site
    carrières ; Workable : redirection ``?not_found=true`` ;

* ``ouvert`` — page lue, aucun signe de fermeture ;
* ``inconnu`` — erreur réseau, délai dépassé, 429 (limite de débit), 403, 5xx,
  page de connexion (JobTeaser exige un compte) : **on ne conclut rien**.

Garde-fous : délai court, intervalle minimal par hôte, coupe-circuit par hôte
après un 429, cache disque des verdicts « ouvert » (une offre vérifiée n'est pas
re-sondée avant ``recheck_days``).
"""

from __future__ import annotations

import html
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup

from .cache import DiskCache
from .linkedin import DETAIL_ENDPOINT, extract_job_id
from .models import DEFAULT_USER_AGENT
from .workday import detail_api_url

logger = logging.getLogger("scrapers.liveness")

OPEN = "ouvert"
CLOSED = "ferme"
UNKNOWN = "inconnu"

CACHE_NAMESPACE = "liveness"
DEFAULT_TIMEOUT = 8.0
DEFAULT_MIN_INTERVAL = 1.5
DEFAULT_RECHECK_DAYS = 3.0
#: Intervalle minimal entre deux sondes d'un même hôte (secondes). Mesuré le
#: 04/11/2026 : LinkedIn répond 429 après ~6 fiches à 1,5 s d'intervalle, WTTJ
#: sert une page de vérification anti-robot (HTTP 202) après une rafale.
HOST_MIN_INTERVALS: dict[str, float] = {
    "linkedin.com": 6.0,
    "welcometothejungle.com": 4.0,
}
#: Statuts qui suspendent l'hôte pour le reste du run (limite de débit, anti-robot).
BLOCKING_STATUSES = frozenset({202, 429, 999})
#: Taille maximale de page analysée (les pages WTTJ font ~550 Ko).
MAX_BODY_CHARS = 1_500_000

#: Marqueurs de fermeture, cherchés dans le TEXTE VISIBLE (scripts et styles
#: retirés), en minuscules, apostrophes typographiques normalisées.
CLOSED_MARKERS: tuple[str, ...] = (
    # LinkedIn
    "les candidatures ne sont plus acceptées",
    "no longer accepting applications",
    # WTTJ
    "ce job n'est plus disponible",
    "this job is no longer available",
    "cette offre n'est plus disponible",
    "cette offre n'est plus en ligne",
    "l'offre n'est plus en ligne",
    # Formulations génériques (ATS, sites carrières)
    "n'accepte plus de candidatures",
    "n'accepte plus les candidatures",
    "les candidatures sont closes",
    "offre expirée",
    "cette offre a expiré",
    "this job has expired",
    "job is no longer available",
    "position is no longer available",
    "this position has been filled",
    "this job posting is no longer active",
    "the job you are looking for is no longer open",
    # Inria (page d'erreur servie en HTTP 200)
    "la page que vous cherchez à atteindre ne peut pas être trouvée",
)
#: Marqueurs bruts (HTML) spécifiques à une source.
RAW_CLOSED_MARKERS: dict[str, tuple[str, ...]] = {
    "linkedin": ('class="closed-job', "closed-job__flavor"),
}
_AUTH_HINTS = ("authwall", "/login", "/signin", "/sign_in", "/users/sign_in", "/connexion")
_LIST_REDIRECT_QUERY = ("not_found=true", "error=true")
_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", " ": " "})
_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class LivenessVerdict:
    """Verdict de vivacité d'une offre."""

    status: str
    reason: str
    http_status: int | None = None
    from_cache: bool = False

    @property
    def is_closed(self) -> bool:
        return self.status == CLOSED


def source_for_url(url: str, source: str | None = None) -> str:
    """Source de l'offre (colonne ``jobs.source`` si connue, sinon déduite de l'hôte)."""
    if source:
        return "wttj" if source == "welcome_to_the_jungle" else str(source)
    host = (urlsplit(url).hostname or "").lower()
    if "linkedin.com" in host:
        return "linkedin"
    if "welcometothejungle.com" in host:
        return "wttj"
    if "jobteaser.com" in host:
        return "jobteaser"
    if "myworkdayjobs.com" in host:
        return "workday"
    return "web"


def probe_url(url: str, source: str) -> str:
    """URL réellement sondée : l'API la plus légère qui fait foi pour la source."""
    if source == "linkedin":
        job_id = extract_job_id(url)
        if job_id:
            return DETAIL_ENDPOINT.format(job_id=job_id)
    if source == "workday" or "myworkdayjobs.com" in url:
        api = detail_api_url(url)
        if api:
            return api
    return url


def visible_text(page: str) -> str:
    """Texte visible d'une page, normalisé pour la recherche de marqueurs."""
    if not page:
        return ""
    try:
        soup = BeautifulSoup(page[:MAX_BODY_CHARS], "lxml")
        for node in soup(["script", "style", "noscript", "template"]):
            node.decompose()
        text = soup.get_text(" ")
    except Exception:  # noqa: BLE001 — repli brutal, jamais d'exception
        text = re.sub(r"<[^>]+>", " ", page[:MAX_BODY_CHARS])
    text = html.unescape(text).translate(_APOSTROPHES).casefold()
    return _WS_RE.sub(" ", text)


def _job_token(url: str) -> str:
    """Dernier segment significatif de l'URL (identifiant ou slug de l'offre)."""
    segments = [segment for segment in urlsplit(url).path.split("/") if segment]
    return segments[-1].casefold() if segments else ""


def _redirected_to_list(requested: str, final: str) -> bool:
    """La requête a-t-elle été redirigée hors de la fiche (liste, accueil carrières) ?"""
    if not final or final.rstrip("/") == requested.rstrip("/"):
        return False
    lowered = final.casefold()
    if any(flag in lowered for flag in _LIST_REDIRECT_QUERY):
        return True
    token = _job_token(requested)
    if not token or token in lowered:
        return False
    final_path = [segment for segment in urlsplit(final).path.split("/") if segment]
    requested_path = [segment for segment in urlsplit(requested).path.split("/") if segment]
    return len(final_path) < len(requested_path)


def classify_response(
    source: str,
    requested_url: str,
    status_code: int,
    final_url: str,
    body: str,
) -> LivenessVerdict:
    """Verdict à partir d'une réponse HTTP (fonction pure, testée sans réseau)."""
    lowered_final = (final_url or "").casefold()
    if status_code in (404, 410):
        return LivenessVerdict(CLOSED, f"HTTP {status_code} (offre supprimée)", status_code)
    if status_code == 429:
        return LivenessVerdict(UNKNOWN, "HTTP 429 (limite de débit) : rien conclu", status_code)
    if status_code in (202, 999):
        return LivenessVerdict(UNKNOWN, f"HTTP {status_code} (vérification anti-robot) : rien conclu", status_code)
    if status_code in (401, 403):
        return LivenessVerdict(UNKNOWN, f"HTTP {status_code} (accès refusé) : rien conclu", status_code)
    if status_code >= 500:
        return LivenessVerdict(UNKNOWN, f"HTTP {status_code} (site en panne) : rien conclu", status_code)
    if status_code >= 400:
        return LivenessVerdict(UNKNOWN, f"HTTP {status_code} inattendu : rien conclu", status_code)
    if any(hint in lowered_final for hint in _AUTH_HINTS):
        return LivenessVerdict(UNKNOWN, "page de connexion : rien conclu", status_code)
    if _redirected_to_list(requested_url, final_url):
        return LivenessVerdict(CLOSED, "redirection vers la liste des offres", status_code)

    if "/wday/cxs/" in requested_url:
        try:
            data = json.loads(body or "{}")
        except ValueError:
            return LivenessVerdict(UNKNOWN, "fiche Workday illisible : rien conclu", status_code)
        info = data.get("jobPostingInfo") if isinstance(data, dict) else None
        if not isinstance(info, dict):
            return LivenessVerdict(UNKNOWN, "fiche Workday sans jobPostingInfo : rien conclu", status_code)
        if info.get("posted") is False or info.get("canApply") is False:
            return LivenessVerdict(CLOSED, "Workday : offre dépubliée (candidature impossible)", status_code)
        return LivenessVerdict(OPEN, "fiche Workday publiée", status_code)

    for marker in RAW_CLOSED_MARKERS.get(source, ()):
        if marker in (body or ""):
            return LivenessVerdict(CLOSED, "la page affiche « les candidatures ne sont plus acceptées »", status_code)
    text = visible_text(body or "")
    for marker in CLOSED_MARKERS:
        if marker in text:
            return LivenessVerdict(CLOSED, f"la page affiche « {marker} »", status_code)
    if not text.strip():
        return LivenessVerdict(UNKNOWN, "page vide : rien conclu", status_code)
    return LivenessVerdict(OPEN, "page lue, aucun signe de fermeture", status_code)


class LivenessChecker:
    """Sonde polie (délai court, débit limité par hôte, cache) des URLs d'offres."""

    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        min_interval: float = DEFAULT_MIN_INTERVAL,
        cache: DiskCache | None = None,
        recheck_days: float = DEFAULT_RECHECK_DAYS,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._own_client = client is None
        self.client = client or httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": DEFAULT_USER_AGENT,
                "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
            },
        )
        self.min_interval = max(0.0, float(min_interval))
        self.cache = cache
        self.recheck = timedelta(days=max(0.0, float(recheck_days)))
        self._sleep = sleep
        self._clock = clock
        self._last_hit: dict[str, float] = {}
        #: Hôtes coupés pour le reste du run (429 reçu) : plus aucun appel.
        self._blocked_hosts: dict[str, str] = {}
        #: Appels réseau effectués (le cache ne compte pas).
        self.network_checks = 0

    # ------------------------------------------------------------------ #
    def _cached(self, url: str) -> LivenessVerdict | None:
        if self.cache is None:
            return None
        raw = self.cache.get(CACHE_NAMESPACE, url)
        if not raw:
            return None
        try:
            data = json.loads(raw)
            checked = datetime.fromisoformat(str(data["checked_at"]))
        except (ValueError, KeyError, TypeError):
            return None
        if data.get("status") != OPEN or datetime.now(timezone.utc) - checked > self.recheck:
            return None
        return LivenessVerdict(OPEN, str(data.get("reason") or "vérifiée récemment"), data.get("http_status"), True)

    def _remember(self, url: str, verdict: LivenessVerdict) -> None:
        if self.cache is None or verdict.status != OPEN:
            return
        payload = {
            "status": verdict.status,
            "reason": verdict.reason,
            "http_status": verdict.http_status,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            self.cache.set(CACHE_NAMESPACE, url, json.dumps(payload, ensure_ascii=False))
        except OSError as exc:  # pragma: no cover — cache non critique
            logger.debug("Cache de vivacité non écrit (%s)", exc)

    def _interval(self, host: str) -> float:
        for suffix, interval in HOST_MIN_INTERVALS.items():
            if host == suffix or host.endswith(f".{suffix}"):
                return max(self.min_interval, interval)
        return self.min_interval

    def _throttle(self, host: str) -> None:
        last = self._last_hit.get(host)
        if last is not None:
            wait = self._interval(host) - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        self._last_hit[host] = self._clock()

    # ------------------------------------------------------------------ #
    def check(self, url: str, source: str | None = None) -> LivenessVerdict:
        """Verdict pour une URL d'offre. Ne lève jamais d'exception."""
        url = str(url or "").strip()
        if not url.startswith(("http://", "https://")):
            return LivenessVerdict(UNKNOWN, "URL absente ou invalide")
        kind = source_for_url(url, source)
        if kind == "jobteaser":
            return LivenessVerdict(UNKNOWN, "JobTeaser exige une connexion : non vérifiable")
        cached = self._cached(url)
        if cached is not None:
            return cached
        target = probe_url(url, kind)
        host = (urlsplit(target).hostname or "").lower()
        if host in self._blocked_hosts:
            return LivenessVerdict(UNKNOWN, f"hôte suspendu pour ce run ({self._blocked_hosts[host]})")
        self._throttle(host)
        self.network_checks += 1
        try:
            response = self.client.get(target)
        except httpx.TimeoutException:
            return LivenessVerdict(UNKNOWN, "délai dépassé : rien conclu")
        except httpx.HTTPError as exc:
            return LivenessVerdict(UNKNOWN, f"erreur réseau ({type(exc).__name__}) : rien conclu")
        except Exception as exc:  # noqa: BLE001 — une sonde ne doit jamais casser le pipeline
            return LivenessVerdict(UNKNOWN, f"erreur inattendue ({type(exc).__name__}) : rien conclu")
        status = int(getattr(response, "status_code", 0) or 0)
        if status in BLOCKING_STATUSES:
            self._blocked_hosts[host] = f"HTTP {status}"
            logger.warning("Vivacité : %s répond %s, hôte suspendu pour ce run.", host, status)
        try:
            body = response.text or ""
        except Exception:  # noqa: BLE001
            body = ""
        verdict = classify_response(kind, target, status, str(getattr(response, "url", "") or target), body)
        self._remember(url, verdict)
        return verdict

    def close(self) -> None:
        if self._own_client:
            self.client.close()

    def __enter__(self) -> LivenessChecker:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
