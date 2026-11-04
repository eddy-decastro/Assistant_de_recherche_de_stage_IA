"""Scraper Workday : stages des grands groupes R&D via l'API JSON publique des sites carrières.

Thales, Airbus, Sanofi, Renault, Valeo, Michelin, Air Liquide, Criteo, Ipsen,
Philips, NXP… publient leurs offres sur ``<tenant>.<wdN>.myworkdayjobs.com``.
Le site carrières (une application JavaScript) s'appuie sur une API JSON
publique, sans compte ni jeton :

* liste : ``POST https://<hôte>/wday/cxs/<tenant>/<site>/jobs`` avec
  ``{"appliedFacets": {...}, "limit": 20, "offset": N, "searchText": ""}`` ;
* fiche : ``GET https://<hôte>/wday/cxs/<tenant>/<site>/job/<chemin>``.

Le filtre « stage en France » passe par les **facettes** du site : chaque
tenant a ses propres noms (``locationCountry``, ``Country``,
``locationHierarchy1``… ; ``workerSubType``, ``Hiring_Type``, ``jobFamilyGroup``…)
et ses propres identifiants de valeurs. Elles sont donc découvertes à chaque run
(premier appel sans filtre) : pays « France », types de contrat « Intern /
Stage / Trainee » (hors apprentissage et VIE). Sans facette de contrat, le
filtre retombe sur le titre ; sans facette pays, sur la fiche détail.

La liste n'a pas de description : la fiche est lue par le moteur commun
(``LIST_HAS_DESCRIPTION = False``) après pré-filtre gratuit du titre, et mise en
cache disque. Chaque site est un board indépendant (``scrapers.boards``) : un
tenant mort est consigné (``http_404``, ``http_422``, ``timeout``…) sans rien
bloquer.

Tenants vérifiés par sonde le 04/11/2026 (``tools/probe_sources.py --source workday``).
Non disponibles sur Workday (sites propres ou autre ATS) : Safran, Schneider
Electric (iCIMS), Stellantis, EDF, TotalEnergies.
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from .base import markup_to_text
from .boards import BoardParseError, MultiBoardScraper, response_json
from .models import CardEntry, PageResult, PassPlan, RawJob, ScraperConfig, Source

logger = logging.getLogger("scrapers.workday")

#: Libellé et couleur de badge proposés pour l'UI (``src.constants`` : voir handoff).
WORKDAY_SOURCE_LABEL = "Grands groupes (Workday)"
WORKDAY_SOURCE_COLOR = "#0EA5E9"

#: Sites vérifiés le 04/11/2026 (repli si ``scrapers.workday_boards`` est absent).
DEFAULT_WORKDAY_BOARDS: tuple[dict[str, str], ...] = (
    {"name": "Thales", "url": "https://thales.wd3.myworkdayjobs.com/Careers"},
    {"name": "Airbus", "url": "https://ag.wd3.myworkdayjobs.com/Airbus"},
    {"name": "Sanofi", "url": "https://sanofi.wd3.myworkdayjobs.com/SanofiCareers"},
    {"name": "Renault Group", "url": "https://alliancewd.wd3.myworkdayjobs.com/renault-group-careers"},
    {"name": "Valeo", "url": "https://valeo.wd3.myworkdayjobs.com/valeo_jobs"},
    {"name": "Michelin", "url": "https://michelinhr.wd3.myworkdayjobs.com/Michelin"},
    {"name": "Air Liquide", "url": "https://airliquidehr.wd3.myworkdayjobs.com/AirLiquideExternalCareer"},
    {"name": "Criteo", "url": "https://criteo.wd3.myworkdayjobs.com/Criteo_Career_Site"},
    {"name": "Ipsen", "url": "https://ipsen.wd103.myworkdayjobs.com/Ipsen_Careers"},
    {"name": "Philips", "url": "https://philips.wd3.myworkdayjobs.com/jobs-and-careers"},
    {"name": "NXP", "url": "https://nxp.wd3.myworkdayjobs.com/careers"},
)

#: Taille de page imposée par Workday (au-delà : HTTP 400).
PAGE_SIZE = 20
#: Appels « liste » regroupés dans une page du moteur (100 offres).
CALLS_PER_ENGINE_PAGE = 5
LIST_SLEEP = (0.2, 0.5)
DETAIL_SLEEP = (0.2, 0.6)
CACHE_NAMESPACE = "workday"
FRANCE = "france"

_LOCALE_RE = re.compile(r"^[a-z]{2}-[A-Z]{2}$")
_HOST_RE = re.compile(r"^([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com$", re.IGNORECASE)
#: Types de contrat « stage » (fort) et « stagiaire au sens large » (faible).
_STRONG_INTERN_RE = re.compile(r"\b(?:intern|interns|internship|internships|stage|stages|stagiaire|stagiaires|pfe)\b", re.I)
_WEAK_INTERN_RE = re.compile(r"\b(?:trainee|trainees|student|students|praktikum|praktikant)\b", re.I)
_EXCLUDED_CONTRACT_RE = re.compile(
    r"\b(?:apprenti\w*|apprentice\w*|alternan\w*|vie|v\.i\.e\.?|dual|work placement|graduate)\b", re.I
)
_TITLE_INTERN_RE = re.compile(r"\b(?:stage|stagiaire|intern|internship|pfe|trainee)\b", re.I)
_POSTED_DAYS_RE = re.compile(r"(\d+)\+?\s*(?:days?|jours?)", re.I)
_REQ_ID_RE = re.compile(r"_([A-Za-z0-9-]+)$")
_REPOST_SUFFIX_RE = re.compile(r"-\d{1,2}$")
#: Motif posé quand la fiche n'a pas pu être lue : rejet NON définitif, l'offre
#: est réévaluée au run suivant (la fiche, une fois en cache, ne coûte plus rien).
DETAIL_MISSING_REASON = "fiche détail non lue (plafond du run ou erreur) : offre réévaluée au prochain run"


def parse_board_url(url: str) -> tuple[str, str, str]:
    """``https://thales.wd3.myworkdayjobs.com/fr-FR/Careers`` → ``(hôte, tenant, site)``."""
    parts = urlsplit(str(url or "").strip())
    host = (parts.hostname or "").lower()
    match = _HOST_RE.match(host)
    if not match:
        raise BoardParseError(f"URL Workday invalide : {url!r} (attendu https://<tenant>.wdN.myworkdayjobs.com/<site>)")
    segments = [segment for segment in parts.path.split("/") if segment]
    segments = [segment for segment in segments if not _LOCALE_RE.match(segment)]
    if not segments:
        raise BoardParseError(f"URL Workday sans site : {url!r}")
    return host, match.group(1).lower(), segments[0]


def detail_api_url(public_url: str) -> str:
    """URL de la fiche JSON d'une offre à partir de son URL publique (``""`` si non Workday).

    ``https://h/fr-FR/Careers/job/Paris/Stage_R123`` →
    ``https://h/wday/cxs/<tenant>/Careers/job/Paris/Stage_R123``. Sert aussi à la
    détection d'offre fermée (``scrapers.liveness``).
    """
    parts = urlsplit(str(public_url or ""))
    host = (parts.hostname or "").lower()
    match = _HOST_RE.match(host)
    if not match:
        return ""
    segments = [segment for segment in parts.path.split("/") if segment and not _LOCALE_RE.match(segment)]
    if len(segments) < 3 or "job" not in segments:
        return ""
    site = segments[0]
    rest = "/".join(segments[1:])
    return f"https://{host}/wday/cxs/{match.group(1).lower()}/{site}/{rest}"


def parse_posted_on(value: str | None, now: datetime | None = None) -> datetime | None:
    """« Posted Today / Yesterday / 5 Days Ago » → date ; « 30+ Days Ago » → ``None`` (inconnue)."""
    text = str(value or "").strip().casefold()
    if not text:
        return None
    current = now or datetime.now(timezone.utc)
    today = current.replace(hour=0, minute=0, second=0, microsecond=0)
    if "today" in text or "aujourd" in text:
        return today
    if "yesterday" in text or "hier" in text:
        return today - timedelta(days=1)
    if "+" in text:
        return None
    match = _POSTED_DAYS_RE.search(text)
    if match:
        return today - timedelta(days=int(match.group(1)))
    return None


def _parse_start_date(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip()[:10])
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc)


def _walk_facets(facets: Any, parent: str = "") -> list[tuple[str, str, dict[str, Any]]]:
    """Aplati l'arbre de facettes : ``(paramètre, libellé de la facette, valeur)``."""
    flat: list[tuple[str, str, dict[str, Any]]] = []
    for facet in facets or []:
        if not isinstance(facet, dict):
            continue
        param = str(facet.get("facetParameter") or "")
        label = f"{parent} {facet.get('descriptor') or ''}".strip()
        for value in facet.get("values") or []:
            if not isinstance(value, dict):
                continue
            if "facetParameter" in value:
                flat.extend(_walk_facets([value], label))
            elif param and value.get("id"):
                flat.append((param, label, value))
    return flat


def is_intern_contract(descriptor: str) -> bool:
    """Valeur de facette « type de contrat » correspondant à un stage ?"""
    text = str(descriptor or "")
    if re.search(r"\b(?:vie|v\.i\.e\.?)\b|international work placement", text, re.I):
        return False
    if _STRONG_INTERN_RE.search(text):
        return True
    return bool(_WEAK_INTERN_RE.search(text)) and not _EXCLUDED_CONTRACT_RE.search(text)


def discover_facets(facets: Any) -> tuple[dict[str, list[str]], list[str]]:
    """Facettes à appliquer (pays France + contrats de stage) et notes lisibles."""
    flat = _walk_facets(facets)
    applied: dict[str, list[str]] = {}
    notes: list[str] = []

    france = [
        (param, label, value)
        for param, label, value in flat
        if str(value.get("descriptor") or "").strip().casefold() == FRANCE
    ]
    preferred = [item for item in france if "country" in f"{item[0]} {item[1]}".casefold() or "pays" in item[1].casefold()]
    chosen = (preferred or france)[:1]
    if chosen:
        param, _, value = chosen[0]
        applied[param] = [str(value["id"])]
    else:
        notes.append("facette pays absente : pays vérifié sur la fiche")

    by_param: dict[str, list[dict[str, Any]]] = {}
    for param, label, value in flat:
        if "location" in param.casefold() or "country" in param.casefold() or param in applied:
            continue
        if is_intern_contract(str(value.get("descriptor") or "")):
            by_param.setdefault(param, []).append(value)
    if by_param:
        param = max(by_param, key=lambda p: sum(int(v.get("count") or 0) for v in by_param[p]))
        applied[param] = [str(v["id"]) for v in by_param[param]]
    else:
        notes.append("facette de contrat absente : stage reconnu au titre")
    return applied, notes


class WorkdayScraper(MultiBoardScraper):
    """Stages en France des grands groupes publiant sur Workday."""

    source: Source = "workday"
    DATE_ORDER_RELIABLE: bool = False
    SERVER_WINDOW_FILTER: bool = False
    #: La liste Workday n'a pas de description : fiche lue par le moteur commun.
    LIST_HAS_DESCRIPTION: bool = False
    #: 20 pages moteur × 100 offres : borne haute par site et par run.
    BOARD_MAX_PAGES = 20
    BOARD_QUOTA = 100

    def __init__(self, config: ScraperConfig | None = None) -> None:
        super().__init__(config)
        self.client.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
        raw = self.config.workday_boards or [dict(board) for board in DEFAULT_WORKDAY_BOARDS]
        self.boards: dict[str, dict[str, str]] = {}
        for entry in raw:
            if not isinstance(entry, dict) or not entry.get("url"):
                self._logger.warning("Workday : board ignoré (dictionnaire {name, url} attendu) : %r", entry)
                continue
            name = str(entry.get("name") or entry["url"]).strip()
            self.boards[name] = {"name": name, "url": str(entry["url"]).strip()}
        #: Par board : (facettes appliquées, filtre au titre ?, notes).
        self._facets: dict[str, tuple[dict[str, list[str]], bool, list[str]]] = {}
        #: Métadonnées lues sur la fiche (date réelle, pays) par identifiant d'offre.
        self._detail_meta: dict[str, dict[str, Any]] = {}
        self._detail_fetches = 0

    def board_keys(self) -> list[str]:
        return list(self.boards)

    # ------------------------------------------------------------------ #
    # Liste
    # ------------------------------------------------------------------ #
    def _post_jobs(self, host: str, tenant: str, site: str, facets: dict[str, list[str]], offset: int) -> dict[str, Any]:
        url = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
        body = {"appliedFacets": facets, "limit": PAGE_SIZE, "offset": offset, "searchText": ""}
        response = self.client.post(url, json=body)
        response.raise_for_status()
        data = response_json(response)
        if not isinstance(data, dict) or not isinstance(data.get("jobPostings", []), list):
            raise BoardParseError("objet JSON {jobPostings: [...]} attendu")
        return data

    def _fetch_board_page(self, board: str, cursor: Any, plan: PassPlan) -> PageResult:
        entry = self.boards.get(board)
        if entry is None:
            raise BoardParseError(f"board Workday inconnu : {board!r}")
        host, tenant, site = parse_board_url(entry["url"])
        calls = 0
        if board not in self._facets:
            discovery = self._post_jobs(host, tenant, site, {}, 0)
            calls += 1
            applied, notes = discover_facets(discovery.get("facets"))
            title_filter = not any(
                is_intern_contract(str(v.get("descriptor") or ""))
                for param, _, v in _walk_facets(discovery.get("facets"))
                if param in applied and str(v.get("id")) in applied[param]
            )
            self._facets[board] = (applied, title_filter, notes)
            if notes:
                self._logger.info("Workday %s : %s.", board, " ; ".join(notes))
        applied, title_filter, notes = self._facets[board]

        offset = int(cursor or 0)
        postings: list[dict[str, Any]] = []
        total = 0
        exhausted = False
        for _ in range(CALLS_PER_ENGINE_PAGE):
            if calls:
                time.sleep(random.uniform(*LIST_SLEEP))
            data = self._post_jobs(host, tenant, site, applied, offset)
            calls += 1
            batch = [p for p in data.get("jobPostings") or [] if isinstance(p, dict)]
            # « total » n'est fiable qu'au premier appel (Workday renvoie 0 ensuite).
            if offset == 0 or int(data.get("total") or 0) > total:
                total = max(total, int(data.get("total") or 0))
            postings.extend(batch)
            offset += len(batch)
            if len(batch) < PAGE_SIZE or (total and offset >= total):
                exhausted = True
                break

        if cursor in (None, 0):
            detail = f"{total} offre(s) {'de stage ' if not title_filter else ''}en France sur le site"
            if notes:
                detail += f" ({' ; '.join(notes)})"
            self.note_board(board, total, detail if total else "aucune offre de stage en France publiée")

        entries: list[CardEntry] = []
        for posting in postings:
            job = self._to_raw_job(entry["name"], host, tenant, site, posting, title_filter)
            if job is None:
                continue
            entries.append(CardEntry(key=job.id_externe or job.url, job=job))
        if not entries and not exhausted:
            # Page entièrement filtrée au titre : on enchaîne sans arrêter la passe.
            entries.append(CardEntry(key=f"workday_{tenant}_offset_{offset}", job=None))
        return PageResult(
            entries=entries,
            next_cursor=None if exhausted else offset,
            exhausted=exhausted,
            http_calls=calls,
        )

    def _to_raw_job(
        self, company: str, host: str, tenant: str, site: str, posting: dict[str, Any], title_filter: bool
    ) -> RawJob | None:
        title = str(posting.get("title") or "").strip()
        path = str(posting.get("externalPath") or "").strip()
        if not title or not path:
            return None
        if title_filter and not _TITLE_INTERN_RE.search(title):
            return None
        match = _REQ_ID_RE.search(path)
        req_id = match.group(1) if match else re.sub(r"[^A-Za-z0-9]+", "-", path)[-40:]
        # « R0342939-1 », « R0342939-2 » : la même réquisition publiée sur plusieurs
        # sites ; une seule offre est conservée.
        req_id = _REPOST_SUFFIX_RE.sub("", req_id) or req_id
        location = str(posting.get("locationsText") or "").strip()
        if not location or re.match(r"^\d+\s+(?:locations?|sites?|lieux)$", location, re.I):
            location = "France"
        elif "france" not in location.casefold():
            location = f"{location}, France"
        return RawJob(
            id_externe=f"workday_{tenant}_{req_id}",
            source=self.source,
            title=title,
            company=company,
            location=location,
            url=f"https://{host}/fr-FR/{site}{path}",
            description="",
            published_at=parse_posted_on(posting.get("postedOn")),
            is_internship=True,
        )

    # ------------------------------------------------------------------ #
    # Fiche détail (appelée par le moteur après le pré-filtre du titre)
    # ------------------------------------------------------------------ #
    def _fetch_detail(self, job: RawJob) -> tuple[str, int]:
        api_url = detail_api_url(job.url)
        if not api_url:
            return "", 0
        key = job.id_externe or job.url
        cache = self.detail_cache
        raw: str | None = cache.get(CACHE_NAMESPACE, key) if cache is not None else None
        calls = 0
        if raw is None:
            if self._detail_fetches:
                time.sleep(random.uniform(*DETAIL_SLEEP))
            response = self.client.get(api_url)
            self._detail_fetches += 1
            calls = 1
            response.raise_for_status()
            data = response_json(response)
            info = (data or {}).get("jobPostingInfo") if isinstance(data, dict) else None
            if not isinstance(info, dict):
                return "", calls
            raw = json.dumps(
                {
                    "jobDescription": info.get("jobDescription") or "",
                    "startDate": info.get("startDate"),
                    "country": (info.get("country") or {}).get("descriptor"),
                    "location": info.get("location"),
                    "hiringOrganization": ((data or {}).get("hiringOrganization") or {}).get("name"),
                },
                ensure_ascii=False,
            )
            if cache is not None and info.get("jobDescription"):
                cache.set(CACHE_NAMESPACE, key, raw)
        try:
            info = json.loads(raw)
        except (TypeError, ValueError):
            return "", calls
        description = markup_to_text(str(info.get("jobDescription") or "")) if info.get("jobDescription") else ""
        meta: dict[str, Any] = {}
        started = _parse_start_date(info.get("startDate"))
        if started is not None:
            meta["published_at"] = started
        if info.get("country"):
            meta["country"] = str(info["country"])
        if info.get("location") and job.location in ("", "France"):
            meta["location"] = f"{info['location']}, France"
        self._detail_meta[key] = meta
        return description, calls

    def _screen_job(self, job: RawJob) -> tuple[str, RawJob, int, bool]:
        rejection, screened, calls, final = super()._screen_job(job)
        meta = self._detail_meta.pop(job.id_externe or job.url, None) or {}
        country = str(meta.pop("country", "") or "")
        if country and country.casefold() != FRANCE and not rejection:
            rejection, final = f"hors France ({country})", True
        if not rejection and not (screened.description or "").strip():
            # Jamais d'offre Workday sans description en base : le scoring LLM
            # travaillerait sur le seul titre. Elle sera reprise au run suivant.
            rejection, final = DETAIL_MISSING_REASON, False
        if meta:
            screened = screened.model_copy(update=meta)
        return rejection, screened, calls, final
