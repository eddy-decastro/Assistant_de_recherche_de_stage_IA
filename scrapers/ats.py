"""Scraper d'offres en direct sur les API publiques ATS.

Lever, Greenhouse, Ashby, SmartRecruiters et Workable exposent publiquement le
vivier complet d'une entreprise (Mistral AI, Hugging Face, Owkin, Dataiku, Alan,
Qonto, Bioptimus, Dust…), description comprise, sans passer par un agrégateur.

Chaque board (``lever:mistral``…) est une requête indépendante du moteur commun
(voir ``scrapers.boards``) : un slug mort ou une API en panne est consigné avec
son état (``http_404``, ``vide``, ``timeout``, ``parse_error``…) et n'arrête ni
les autres boards, ni les autres sources.

Slugs vérifiés par sonde le 04/11/2026 (``tools/probe_sources.py --ats``).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from bs4 import BeautifulSoup

from .boards import BoardParseError, MultiBoardScraper, response_json
from .models import CardEntry, PageResult, PassPlan, RawJob, ScraperConfig, Source

logger = logging.getLogger("scrapers.ats")

#: Boards par défaut (repli si ``scrapers.ats_boards`` est absent de config.yaml).
#: Vérifiés le 04/11/2026 : chaque slug répond et publie des offres en France.
#: Retirés car morts au 04/11 : Lever ``mistral``/``qonto`` (vides, passés sur
#: Ashby), Greenhouse ``huggingface`` (404, passé sur Workable), SmartRecruiters
#: ``sanofi``/``criteo`` (vides, passés sur Workday : voir ``scrapers.workday``).
DEFAULT_ATS_BOARDS: dict[str, list[str]] = {
    "lever": ["contentsquare", "pigment", "alice-bob", "swile", "ekimetrics"],
    "greenhouse": ["datadog", "dataiku", "bioptimus8", "algolia", "mirakl", "shifttechnology", "helsing"],
    "ashby": [
        "mistral.ai", "dust", "owkin", "alan", "qonto", "photoroom", "nabla", "hcompany",
        "doctolib", "backmarket", "ledger", "sonio", "lifen",
    ],
    "smartrecruiters": ["Ubisoft2"],
    "workable": ["huggingface"],
}

ATS_KINDS: tuple[str, ...] = ("lever", "greenhouse", "ashby", "smartrecruiters", "workable")

_FRANCE_LOC_RE = re.compile(
    r"\b(?:france|paris|lyon|grenoble|toulouse|sophia|bordeaux|nantes|lille|saclay|rennes|strasbourg|marseille|nice|montpellier|idf|ile-de-france|île-de-france|fr)\b",
    re.IGNORECASE,
)

INTERNSHIP_KEYWORDS: tuple[str, ...] = (
    "intern",
    "internship",
    "stage",
    "stagiaire",
    "pfe",
    "end of studies",
    "master thesis",
    "praktikant",
    "werkstudent",
)

COMPANY_DISPLAY_NAMES: dict[str, str] = {
    "mistral": "Mistral AI",
    "mistral.ai": "Mistral AI",
    "owkin": "Owkin",
    "qonto": "Qonto",
    "shift-technology": "Shift Technology",
    "shifttechnology": "Shift Technology",
    "backmarket": "Back Market",
    "datadog": "Datadog",
    "huggingface": "Hugging Face",
    "dataiku": "Dataiku",
    "alan": "Alan",
    "criteo": "Criteo",
    "dust": "Dust",
    "bioptimus": "Bioptimus",
    "bioptimus8": "Bioptimus",
    "sanofi": "Sanofi",
    "ubisoft": "Ubisoft",
    "ubisoft2": "Ubisoft",
    "contentsquare": "Contentsquare",
    "pigment": "Pigment",
    "alice-bob": "Alice & Bob",
    "swile": "Swile",
    "algolia": "Algolia",
    "mirakl": "Mirakl",
    "photoroom": "Photoroom",
    "nabla": "Nabla",
    "hcompany": "H Company",
    "doctolib": "Doctolib",
    "ledger": "Ledger",
    "ekimetrics": "Ekimetrics",
    "helsing": "Helsing",
    "sonio": "Sonio",
    "lifen": "Lifen",
}

#: Plafond de pagination SmartRecruiters (100 offres par appel).
SMARTRECRUITERS_MAX_POSTINGS = 500
#: Fiches détail SmartRecruiters lues par board et par run (la liste n'a pas de texte).
SMARTRECRUITERS_MAX_DETAILS = 40


def _clean_html(html_text: str | None) -> str:
    if not html_text:
        return ""
    try:
        soup = BeautifulSoup(html_text, "html.parser")
        return soup.get_text("\n", strip=True)
    except Exception:
        return re.sub(r"<[^>]+>", " ", html_text).strip()


def _is_france_location(location: str | None) -> bool:
    if not location or not location.strip():
        # Sans localisation explicite, on conserve par précaution pour le filtre aval
        return True
    return bool(_FRANCE_LOC_RE.search(location))


def _is_internship(title: str, contract_info: str = "") -> bool:
    combined = f"{title} {contract_info}".casefold()
    return any(keyword in combined for keyword in INTERNSHIP_KEYWORDS)


def _company_name(slug: str) -> str:
    return COMPANY_DISPLAY_NAMES.get(slug.lower(), slug.replace("-", " ").replace("_", " ").title())


def _parse_iso(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def split_board_key(board: str) -> tuple[str, str]:
    """``"lever:mistral"`` → ``("lever", "mistral")``."""
    kind, _, slug = board.partition(":")
    return kind.strip().lower(), slug.strip()


class DirectATSScraper(MultiBoardScraper):
    """Scraper interrogeant directement les APIs publiques des ATS d'entreprises cibles."""

    source: Source = "ats"
    DATE_ORDER_RELIABLE: bool = False
    SERVER_WINDOW_FILTER: bool = False
    LIST_HAS_DESCRIPTION: bool = True

    def __init__(self, config: ScraperConfig | None = None) -> None:
        super().__init__(config)
        self.boards: dict[str, list[str]] = (
            self.config.ats_boards if self.config.ats_boards else DEFAULT_ATS_BOARDS
        )

    def board_keys(self) -> list[str]:
        keys: list[str] = []
        for kind, slugs in self.boards.items():
            if kind not in ATS_KINDS:
                self._logger.warning("ATS : type de board inconnu ignoré (%r).", kind)
                continue
            for slug in slugs or []:
                if str(slug).strip():
                    keys.append(f"{kind}:{str(slug).strip()}")
        return keys

    # ------------------------------------------------------------------ #
    # Pagination : un board = une page (vivier complet en un appel)
    # ------------------------------------------------------------------ #
    def _fetch_board_page(self, board: str, cursor: Any, plan: PassPlan) -> PageResult:
        kind, slug = split_board_key(board)
        fetcher = {
            "lever": self._fetch_lever,
            "greenhouse": self._fetch_greenhouse,
            "ashby": self._fetch_ashby,
            "smartrecruiters": self._fetch_smartrecruiters,
            "workable": self._fetch_workable,
        }.get(kind)
        if fetcher is None:
            raise BoardParseError(f"type de board inconnu : {kind!r}")
        jobs, total, calls = fetcher(slug, board)
        france = len(jobs)
        self.note_board(
            board,
            total,
            f"{total} offre(s) sur le board, {france} stage(s) en France" if total else "",
        )
        entries = [CardEntry(key=job.id_externe or job.url, job=job) for job in jobs]
        return PageResult(entries=entries, next_cursor=None, exhausted=True, http_calls=calls)

    # ------------------------------------------------------------------ #
    # Connecteurs ATS : (offres retenues, volume brut du board, appels HTTP)
    # ------------------------------------------------------------------ #
    def _get(self, url: str, **kwargs: Any) -> Any:
        response = self.client.get(url, **kwargs)
        response.raise_for_status()
        return response_json(response)

    def _fetch_lever(self, slug: str, board: str) -> tuple[list[RawJob], int, int]:
        """Récupère les offres d'un board Lever (``[]`` = slug existant mais vide)."""
        items = self._get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
        if not isinstance(items, list):
            raise BoardParseError("liste d'offres attendue")
        company = _company_name(slug)
        jobs: list[RawJob] = []
        for item in items:
            title = str(item.get("text") or "").strip()
            categories = item.get("categories") or {}
            location = str(categories.get("location") or "")
            country = str(item.get("country") or "").upper()
            commitment = str(categories.get("commitment") or "")
            if country and country != "FR":
                continue
            if not country and not _is_france_location(location):
                continue
            if not _is_internship(title, commitment):
                continue
            desc_plain = str(item.get("descriptionPlain") or "")
            lists_content = []
            for lst in item.get("lists") or []:
                header = lst.get("text") or ""
                clean_content = _clean_html(lst.get("content") or "")
                lists_content.append(f"{header}\n{clean_content}")
            full_desc = "\n\n".join(part for part in [desc_plain, *lists_content] if part).strip()
            ext_id = str(item.get("id") or "")
            created_at_ms = item.get("createdAt")
            published_at = (
                datetime.fromtimestamp(created_at_ms / 1000.0, tz=timezone.utc)
                if isinstance(created_at_ms, (int, float))
                else None
            )
            jobs.append(
                RawJob(
                    id_externe=f"lever_{slug}_{ext_id}",
                    source=self.source,
                    title=title,
                    company=company,
                    location=location or "France",
                    url=str(item.get("hostedUrl") or "") or f"https://jobs.lever.co/{slug}/{ext_id}",
                    description=full_desc,
                    published_at=published_at,
                    is_internship=True,
                )
            )
        return jobs, len(items), 1

    def _fetch_greenhouse(self, slug: str, board: str) -> tuple[list[RawJob], int, int]:
        """Récupère les offres d'un board Greenhouse."""
        data = self._get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
        if not isinstance(data, dict):
            raise BoardParseError("objet JSON attendu")
        items = data.get("jobs") or []
        company = _company_name(slug)
        jobs: list[RawJob] = []
        for item in items:
            title = str(item.get("title") or "").strip()
            location = str((item.get("location") or {}).get("name") or "")
            if not _is_france_location(location):
                continue
            if not _is_internship(title):
                continue
            ext_id = str(item.get("id") or "")
            jobs.append(
                RawJob(
                    id_externe=f"gh_{slug}_{ext_id}",
                    source=self.source,
                    title=title,
                    company=company,
                    location=location or "France",
                    url=str(item.get("absolute_url") or "") or f"https://boards.greenhouse.io/{slug}/jobs/{ext_id}",
                    description=_clean_html(item.get("content") or ""),
                    published_at=_parse_iso(item.get("first_published") or item.get("updated_at")),
                    is_internship=True,
                )
            )
        return jobs, len(items), 1

    def _fetch_ashby(self, slug: str, board: str) -> tuple[list[RawJob], int, int]:
        """Récupère les offres d'un board Ashby."""
        data = self._get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
        if not isinstance(data, dict):
            raise BoardParseError("objet JSON attendu")
        items = data.get("jobs") or []
        company = _company_name(slug)
        jobs: list[RawJob] = []
        for item in items:
            title = str(item.get("title") or "").strip()
            locations = [str(item.get("location") or item.get("locationName") or "")]
            for extra in item.get("secondaryLocations") or []:
                if isinstance(extra, dict):
                    locations.append(str(extra.get("location") or ""))
            address = (item.get("address") or {}).get("postalAddress") or {}
            country = str(address.get("addressCountry") or "")
            location = locations[0]
            employment_type = str(item.get("employmentType") or "")
            in_france = "france" in country.casefold() or any(
                loc and _FRANCE_LOC_RE.search(loc) for loc in locations
            )
            if not in_france and (country or any(locations)):
                continue
            if not _is_internship(title, employment_type):
                continue
            ext_id = str(item.get("id") or "")
            jobs.append(
                RawJob(
                    id_externe=f"ashby_{slug}_{ext_id}",
                    source=self.source,
                    title=title,
                    company=company,
                    location=location or "France",
                    url=str(item.get("jobUrl") or "") or f"https://jobs.ashbyhq.com/{slug}/{ext_id}",
                    description=str(item.get("descriptionPlain") or _clean_html(item.get("descriptionHtml"))),
                    published_at=_parse_iso(item.get("publishedAt")),
                    is_internship=True,
                )
            )
        return jobs, len(items), 1

    def _fetch_smartrecruiters(self, slug: str, board: str) -> tuple[list[RawJob], int, int]:
        """Offres SmartRecruiters en France (liste paginée + fiche détail des stages)."""
        url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
        company = _company_name(slug)
        calls = 0
        offset = 0
        total: int | None = None
        candidates: list[dict[str, Any]] = []
        while True:
            data = self._get(url, params={"country": "fr", "limit": 100, "offset": offset})
            calls += 1
            if not isinstance(data, dict):
                raise BoardParseError("objet JSON attendu")
            content = data.get("content") or []
            if total is None:
                found = data.get("totalFound")
                total = int(found) if isinstance(found, int) else len(content)
            candidates.extend(content)
            offset += len(content)
            if not content or offset >= (total or 0) or offset >= SMARTRECRUITERS_MAX_POSTINGS:
                break

        jobs: list[RawJob] = []
        details = 0
        for item in candidates:
            title = str(item.get("name") or "").strip()
            loc_obj = item.get("location") or {}
            city = str(loc_obj.get("city") or "")
            country = str(loc_obj.get("country") or "")
            location = ", ".join(p for p in (city, country) if p)
            type_obj = item.get("typeOfEmployment") or {}
            contract_type = str(type_obj.get("label") or type_obj.get("id") or "")
            if country and country.casefold() not in ("fr", "france"):
                continue
            if not country and not _is_france_location(location):
                continue
            if not _is_internship(title, contract_type):
                continue
            ext_id = str(item.get("id") or "")
            description = ""
            posting_url = f"https://jobs.smartrecruiters.com/{slug}/{ext_id}"
            ref = item.get("ref")
            if ref and details < SMARTRECRUITERS_MAX_DETAILS:
                try:
                    detail = self._get(str(ref))
                    calls += 1
                    details += 1
                    sections = ((detail or {}).get("jobAd") or {}).get("sections") or {}
                    description = "\n\n".join(
                        _clean_html((sections.get(name) or {}).get("text"))
                        for name in ("jobDescription", "qualifications", "additionalInformation")
                        if (sections.get(name) or {}).get("text")
                    )
                    posting_url = str((detail or {}).get("postingUrl") or posting_url)
                except Exception as exc:  # noqa: BLE001 — une fiche ne doit pas tuer le board
                    self._logger.debug("SmartRecruiters : fiche %s illisible (%s)", ref, exc)
            jobs.append(
                RawJob(
                    id_externe=f"sr_{slug}_{ext_id}",
                    source=self.source,
                    title=title,
                    company=company,
                    location=location or "France",
                    url=posting_url,
                    description=description,
                    published_at=_parse_iso(item.get("releasedDate")),
                    is_internship=True,
                )
            )
        return jobs, int(total or 0), calls

    def _fetch_workable(self, slug: str, board: str) -> tuple[list[RawJob], int, int]:
        """Offres d'un compte Workable (widget public, description incluse)."""
        data = self._get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}", params={"details": "true"})
        if not isinstance(data, dict):
            raise BoardParseError("objet JSON attendu")
        items = data.get("jobs") or []
        company = _company_name(slug)
        jobs: list[RawJob] = []
        for item in items:
            title = str(item.get("title") or "").strip()
            countries = {str(item.get("country") or "").casefold()}
            for loc in item.get("locations") or []:
                if isinstance(loc, dict):
                    countries.add(str(loc.get("country") or "").casefold())
                    countries.add(str(loc.get("countryCode") or "").casefold())
            countries.discard("")
            if countries and not ({"france", "fr"} & countries):
                continue
            if not _is_internship(title, str(item.get("employment_type") or "")):
                continue
            shortcode = str(item.get("shortcode") or "")
            city = str(item.get("city") or "")
            jobs.append(
                RawJob(
                    id_externe=f"workable_{slug}_{shortcode}",
                    source=self.source,
                    title=title,
                    company=company,
                    location=", ".join(p for p in (city, "France") if p),
                    url=str(item.get("url") or "") or f"https://apply.workable.com/j/{shortcode}",
                    description=_clean_html(item.get("description")),
                    published_at=_parse_iso(item.get("published_on") or item.get("created_at")),
                    is_internship=True,
                )
            )
        return jobs, len(items), 1
