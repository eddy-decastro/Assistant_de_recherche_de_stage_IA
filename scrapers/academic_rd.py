"""Scraper des stages R&D des instituts de recherche publics (Inria, CEA).

Sources vérifiées par sonde le 04/11/2026 :

* **Inria** — l'ancien flux RSS (``/offres/rss``) répond HTTP 200 avec une page
  « Erreur » : il est remplacé par la page de liste publique (toutes les offres,
  une carte ``div.job-card`` par offre) puis la fiche de chaque offre candidate
  (« Type de contrat : Stage »), mise en cache disque ;
* **CEA** — l'ancienne page INSTN répond 404 : remplacée par les flux RSS
  Talentsoft de ``emploi.cea.fr`` (contrat « Stage » et profil « Mathématiques,
  information scientifique, logiciel », 20 offres les plus récentes chacun) ;
* **CNRS** — ``emploi.cnrs.fr/Rss.aspx`` redirige vers une page 404 et le portail
  ne publie pas de stages (CDD, doctorants, concours) : source retirée.

Chaque flux est un board indépendant (voir ``scrapers.boards``) : un flux en
panne est consigné (``http_404``, ``parse_error``…) sans bloquer l'autre.
"""

from __future__ import annotations

import email.utils
import html
import logging
import random
import re
import time
from datetime import datetime, timezone
from typing import Any

from bs4 import BeautifulSoup

from .boards import BoardParseError, MultiBoardScraper
from .models import CardEntry, PageResult, PassPlan, RawJob, ScraperConfig, Source

logger = logging.getLogger("scrapers.academic")

INRIA_BASE = "https://jobs.inria.fr"
INRIA_OFFERS_URL = f"{INRIA_BASE}/public/classic/fr/offres"
CEA_RSS_URL = "https://www.emploi.cea.fr/handlers/offerRss.ashx"
#: Flux CEA : contrat « Stage » (579) et profil « Maths, info scientifique, logiciel » (1898).
CEA_FEEDS: tuple[dict[str, str], ...] = (
    {"LCID": "1036", "Rss_Contract": "579"},
    {"LCID": "1036", "Rss_Profile": "1898"},
)
ACADEMIC_FEEDS: tuple[str, ...] = ("inria", "cea")

#: Fiches Inria lues par run (les fiches déjà en cache ne comptent pas).
INRIA_MAX_DETAILS = 60
INRIA_DETAIL_SLEEP = (0.3, 0.8)
CACHE_NAMESPACE_INRIA = "inria"

#: Titres Inria qui ne sont pas des stages : aucune fiche n'est lue pour eux.
_INRIA_NON_STAGE_TITLE = re.compile(
    r"\b(?:phd|ph\.d|doctorant|doctorat|post-?doc|post-doctoral|postdoctoral|chercheur|"
    r"chef de|responsable|gestionnaire|assistant(?:e)? (?:de|administratif|administrative)|"
    r"directeur|directrice|cdd|cdi|starting faculty|chargé|chargée|technicien|technicienne)\b",
    re.IGNORECASE,
)
_STAGE_TITLE = re.compile(r"\b(?:stage|stagiaire|intern|internship|pfe|master)\b", re.IGNORECASE)
_CEA_REF_RE = re.compile(r"^\s*(\d{4}-\d+)\s*-\s*")

_WS_RE = re.compile(r"\s+")
_TAG_RE = re.compile(r"<[^>]+>")


def _clean_text(value: str | None) -> str:
    if not value:
        return ""
    unescaped = html.unescape(value)
    stripped = _TAG_RE.sub(" ", unescaped)
    return _WS_RE.sub(" ", stripped).strip()


def _parse_pubdate(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    date_clean = date_str.strip()
    try:
        parsed = email.utils.parsedate_to_datetime(date_clean)
        if parsed:
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        pass
    try:
        dt = datetime.fromisoformat(date_clean.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def parse_inria_listing(html_text: str) -> list[dict[str, str]]:
    """Cartes de la page de liste Inria : titre, URL, référence, ville."""
    soup = BeautifulSoup(html_text or "", "html.parser")
    cards = soup.select("div.job-card")
    offers: list[dict[str, str]] = []
    for card in cards:
        link = card.find("a", href=True)
        if link is None:
            continue
        title = _clean_text(link.get_text(" "))
        href = str(link["href"])
        url = href if href.startswith("http") else f"{INRIA_BASE}{href}"
        infos: dict[str, str] = {}
        for item in card.select("li"):
            label = item.find("b")
            if label is None:
                continue
            key = _clean_text(label.get_text()).casefold()
            value = _clean_text(item.get_text(" ")).split(":", 1)[-1].strip()
            infos[key] = value
        ref = infos.get("référence de l'offre") or url.rstrip("/").rsplit("/", 1)[-1]
        offers.append(
            {
                "title": title,
                "url": url,
                "ref": ref,
                "city": infos.get("ville", ""),
                "deadline": infos.get("date limite pour postuler", ""),
            }
        )
    return offers


def parse_inria_detail(html_text: str) -> tuple[str, str]:
    """``(type de contrat, texte de la fiche)`` d'une offre Inria."""
    soup = BeautifulSoup(html_text or "", "html.parser")
    main = soup.select_one("main") or soup.body or soup
    text = main.get_text("\n", strip=True)
    match = re.search(r"Type de contrat\s*:\s*\n?\s*([^\n]+)", text)
    contract = match.group(1).strip() if match else ""
    return contract, text


class AcademicRDScraper(MultiBoardScraper):
    """Stages R&D des laboratoires publics français (Inria, CEA)."""

    source: Source = "academic"
    DATE_ORDER_RELIABLE: bool = False
    SERVER_WINDOW_FILTER: bool = False
    LIST_HAS_DESCRIPTION: bool = True

    def __init__(self, config: ScraperConfig | None = None) -> None:
        super().__init__(config)

    def board_keys(self) -> list[str]:
        keys = []
        for feed in self.config.academic_feeds or []:
            name = str(feed).strip().casefold()
            if name in ACADEMIC_FEEDS:
                keys.append(name)
            elif name:
                self._logger.warning("Academic : flux inconnu ignoré (%r).", feed)
        return keys

    def _fetch_board_page(self, board: str, cursor: Any, plan: PassPlan) -> PageResult:
        if board == "inria":
            jobs, total, calls = self._fetch_inria()
        elif board == "cea":
            jobs, total, calls = self._fetch_cea()
        else:
            raise BoardParseError(f"flux inconnu : {board!r}")
        self.note_board(board, total, f"{total} offre(s) publiée(s), {len(jobs)} stage(s) retenu(s)" if total else "")
        entries = [CardEntry(key=job.id_externe or job.url, job=job) for job in jobs]
        return PageResult(entries=entries, next_cursor=None, exhausted=True, http_calls=calls)

    # ------------------------------------------------------------------ #
    # Parseur RSS générique (CEA)
    # ------------------------------------------------------------------ #
    def _parse_feed(self, content: str, default_company: str, default_location: str = "France") -> list[RawJob]:
        """Parse un flux RSS 2.0 / Atom de façon défensive."""
        jobs: list[RawJob] = []
        if not content or not content.strip():
            return []
        soup = None
        for parser in ("xml", "html.parser"):
            try:
                soup = BeautifulSoup(content, parser)
                break
            except Exception:
                continue
        if soup is None:
            return []
        items = soup.find_all("item") or soup.find_all("entry")
        for item in items:
            title_node = item.find("title")
            title = _clean_text(title_node.get_text() if title_node else "")
            if not title:
                continue
            link_node = item.find("link")
            link = ""
            if link_node:
                link = link_node.get("href") or link_node.get_text() or ""
            link = link.strip()
            guid_node = item.find("guid") or item.find("id")
            guid = (guid_node.get_text() if guid_node else "") or link
            desc_node = item.find("description") or item.find("summary") or item.find("content")
            description = _clean_text(desc_node.get_text() if desc_node else "")
            pubdate_node = item.find("pubDate") or item.find("pubdate") or item.find("published") or item.find("updated")
            published_at = _parse_pubdate(pubdate_node.get_text() if pubdate_node else None)
            categories = [_clean_text(node.get_text()) for node in item.find_all("category")]

            ref_match = _CEA_REF_RE.match(title)
            reference = ref_match.group(1) if ref_match else ""
            if ref_match:
                title = title[ref_match.end():].strip()
            location = default_location
            if len(categories) >= 3 and categories[-1]:
                location = f"{categories[-1]}, France"
            is_stage = any(cat.casefold() == "stage" for cat in categories) or bool(_STAGE_TITLE.search(title))
            id_clean = reference or re.sub(r"[^a-zA-Z0-9_-]+", "_", guid)[-64:]
            jobs.append(
                RawJob(
                    id_externe=f"{default_company.lower()}_{id_clean}",
                    source=self.source,
                    title=title,
                    company=default_company,
                    location=location,
                    url=link or f"https://www.emploi.cea.fr/offre/{id_clean}",
                    description=description,
                    published_at=published_at,
                    is_internship=is_stage,
                )
            )
        return jobs

    # ------------------------------------------------------------------ #
    # Connecteurs
    # ------------------------------------------------------------------ #
    def _fetch_cea(self) -> tuple[list[RawJob], int, int]:
        """Flux RSS CEA (stages + profil maths/info), dédupliqués par référence."""
        seen: set[str] = set()
        jobs: list[RawJob] = []
        total = 0
        calls = 0
        for params in CEA_FEEDS:
            response = self.client.get(CEA_RSS_URL, params=params)
            calls += 1
            response.raise_for_status()
            text = response.text or ""
            if "<rss" not in text[:500] and "<feed" not in text[:500]:
                raise BoardParseError("flux RSS attendu (page HTML reçue)")
            feed_jobs = self._parse_feed(text, default_company="CEA")
            total += len(feed_jobs)
            for job in feed_jobs:
                if job.id_externe in seen or not job.is_internship:
                    continue
                seen.add(job.id_externe or "")
                jobs.append(job)
        return jobs, total, calls

    def _fetch_inria(self) -> tuple[list[RawJob], int, int]:
        """Page de liste Inria + fiches des offres candidates (cache disque)."""
        response = self.client.get(INRIA_OFFERS_URL)
        response.raise_for_status()
        calls = 1
        offers = parse_inria_listing(response.text or "")
        if not offers and "job-card" not in (response.text or ""):
            raise BoardParseError("aucune carte d'offre dans la page Inria (structure modifiée ?)")
        jobs: list[RawJob] = []
        fetched = 0
        for offer in offers:
            title = offer["title"]
            if _INRIA_NON_STAGE_TITLE.search(title) and not _STAGE_TITLE.search(title):
                continue
            detail_html = None
            cache = self.detail_cache
            if cache is not None:
                detail_html = cache.get(CACHE_NAMESPACE_INRIA, offer["ref"])
            if detail_html is None and fetched < INRIA_MAX_DETAILS:
                if fetched:
                    time.sleep(random.uniform(*INRIA_DETAIL_SLEEP))
                try:
                    detail = self.client.get(offer["url"])
                    calls += 1
                    fetched += 1
                    detail.raise_for_status()
                    detail_html = detail.text or ""
                    if cache is not None and "Type de contrat" in detail_html:
                        cache.set(CACHE_NAMESPACE_INRIA, offer["ref"], detail_html)
                except Exception as exc:  # noqa: BLE001 — une fiche ne doit pas tuer le flux
                    self._logger.debug("Inria : fiche %s illisible (%s)", offer["url"], exc)
                    detail_html = None
            contract, text = parse_inria_detail(detail_html) if detail_html else ("", "")
            if contract:
                is_stage = "stage" in contract.casefold()
            else:
                is_stage = bool(_STAGE_TITLE.search(title))
            if not is_stage:
                continue
            city = offer.get("city") or ""
            jobs.append(
                RawJob(
                    id_externe=f"inria_{offer['ref']}",
                    source=self.source,
                    title=title,
                    company="Inria",
                    location=f"{city}, France" if city else "France",
                    url=offer["url"],
                    description=text,
                    published_at=None,
                    is_internship=True,
                )
            )
        return jobs, len(offers), calls
