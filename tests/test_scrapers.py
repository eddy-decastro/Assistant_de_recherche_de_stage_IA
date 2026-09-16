"""Tests du module de scraping unifié (filtrage anti-BI, modèles, parsing, pagination)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scrapers import linkedin as linkedin_module
from scrapers.base import _contains_keyword, BaseScraper
from scrapers.jobteaser import JobTeaserScraper
from scrapers.linkedin import GUEST_ENDPOINT, LinkedInGuestScraper
from scrapers.manager import ScraperManager
from scrapers.models import RawJob, ScrapeResult, ScraperConfig


class _FakeResponse:
    """Réponse HTTP factice (texte ou code d'erreur), sans réseau."""

    def __init__(self, text: str = "", status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code
        self.request = httpx.Request("GET", GUEST_ENDPOINT)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("erreur simulée", request=self.request, response=self)


class _FakeClient:
    """Client factice : sert une page par offset et enregistre les offsets demandés."""

    def __init__(self, pages: dict[int, str | int]) -> None:
        self.pages = pages
        self.requested_starts: list[int] = []

    def get(self, url: str, params: dict | None = None) -> _FakeResponse:
        start = int((params or {}).get("start", 0))
        self.requested_starts.append(start)
        page = self.pages.get(start, "")
        if isinstance(page, int):  # code HTTP simulé (ex. 429)
            return _FakeResponse("", status_code=page)
        return _FakeResponse(page)

    def close(self) -> None:  # compatibilité BaseScraper.close()
        return None


def _linkedin_card(urn: str, title: str = "Stage Data Scientist", company: str = "Mistral AI") -> str:
    """Fabrique une carte d'offre LinkedIn conforme au HTML réel (mode invité)."""
    return (
        "<li>"
        '<div class="base-card base-search-card job-search-card" '
        f'data-entity-urn="urn:li:jobPosting:{urn}">'
        '<a class="base-card__full-link" '
        f'href="https://fr.linkedin.com/jobs/view/stage-{urn}?position=1&amp;refId=x">'
        f'<span class="sr-only">{title}</span></a>'
        f'<h3 class="base-search-card__title">{title}</h3>'
        '<h4 class="base-search-card__subtitle">'
        f'<a class="hidden-nested-link" href="/company/x">{company}</a></h4>'
        '<div class="base-search-card__metadata">'
        '<span class="job-search-card__location">Paris</span>'
        '<time class="job-search-card__listdate" datetime="2026-09-07">il y a 6 jours</time>'
        "</div></div></li>"
    )


def _linkedin_page(prefix: str, count: int = 10) -> str:
    """Construit une page de ``count`` cartes d'offres LinkedIn."""
    return "<ul>" + "".join(_linkedin_card(f"{prefix}{i:02d}") for i in range(count)) + "</ul>"


def _run_linkedin_fetch(
    pages: dict[int, str | int], queries: list[str] | None = None
) -> tuple[ScrapeResult, _FakeClient]:
    """Exécute ``fetch()`` avec un client factice, sans réseau ni pause entre pages."""
    config = ScraperConfig(
        target_queries=queries or ["Stage Data Scientist"], max_offers_per_source=50
    )
    scraper = LinkedInGuestScraper(config)
    real_client = scraper.client
    client = _FakeClient(pages)
    scraper.client = client  # type: ignore[assignment]
    real_client.close()
    previous_range = linkedin_module.SLEEP_RANGE
    linkedin_module.SLEEP_RANGE = (0.0, 0.0)  # neutralise les pauses anti-flood
    try:
        result = scraper.fetch()
    finally:
        linkedin_module.SLEEP_RANGE = previous_range
        scraper.close()
    return result, client


def test_is_valid_job() -> None:
    cfg = ScraperConfig()

    class _FakeScraper(BaseScraper):
        source = "wttj"

        def fetch(self) -> ScrapeResult:  # pragma: no cover
            return ScrapeResult()

    scraper = _FakeScraper(cfg)
    try:
        cases = [
            ("Stage Data Scientist", "PyTorch ML", True),
            ("Stage Data Analyst Power BI", "", False),
            ("Stage Business Intelligence", "ML", False),
            ("Stage Data Engineer", "Spark", True),
            ("Stage Tableau de bord", "", False),
            ("Stage Chargé de reporting", "", False),
            ("Stage Machine Learning Engineer", "NLP", True),
            ("Stage Recherche IA", "intelligence artificielle", True),
            ("Stage Data Scientist", "dashboard Qlik", False),
        ]
        for title, desc, expected in cases:
            got = scraper.is_valid_job(title, desc)
            assert got is expected, f"{title!r}: attendu {expected}, obtenu {got}"
        print(f"  is_valid_job : OK ({len(cases)} cas)")
    finally:
        scraper.close()


def test_word_boundary() -> None:
    # "vba" ne doit pas matcher à l'intérieur d'un mot plus long.
    assert _contains_keyword("advbance", "vba") is False
    assert _contains_keyword("excel vba macros", "vba") is True
    assert _contains_keyword("power bi analyste", "power bi") is True
    assert _contains_keyword("analyste power bi", "power bi") is True
    print("  _contains_keyword : OK")


def test_models() -> None:
    cfg = ScraperConfig()
    assert cfg.max_offers_per_source == 50
    job = RawJob(
        id_externe="1",
        source="wttj",
        title="Stage Data Scientist",
        company="Mistral AI",
        location="Paris",
        url="https://example.com/job/1",
        description="PyTorch",
    )
    assert job.source == "wttj"
    assert job.is_internship is True
    assert job.published_at is None
    print("  modèles : OK")


def test_dedupe_url() -> None:
    assert ScraperManager._normalize_url("HTTPS://X/Job/?q=1") == "https://x/job"
    assert ScraperManager._normalize_url("https://x/job/") == "https://x/job"
    assert ScraperManager._normalize_url("") == ""
    print("  déduplication URL : OK")


def test_jobteaser_cookie_parsing() -> None:
    pairs = JobTeaserScraper._parse_cookie_string("a=1; jobteaser_session=abc;   ; b=2")
    assert pairs == [("a", "1"), ("jobteaser_session", "abc"), ("b", "2")]
    print("  JobTeaser : parsing cookies OK")


def test_jobteaser_next_data_extraction() -> None:
    payload = {
        "props": {
            "pageProps": {
                "jobOffers": [
                    {
                        "id": "42",
                        "title": "Stage Data Scientist",
                        "company": {"name": "Mistral AI"},
                        "location": {"city": "Paris", "country": "France"},
                        "contract": "internship",
                        "description": "PyTorch",
                    },
                    {"foo": "bar"},  # bruit à ignorer
                ]
            }
        }
    }
    html = (
        "<html><body><script id='__NEXT_DATA__' type='application/json'>"
        + json.dumps(payload)
        + "</script></body></html>"
    )
    raw_jobs = JobTeaserScraper._extract_jobs_from_html(html)
    assert len(raw_jobs) == 1, f"attendu 1 offre, obtenu {len(raw_jobs)}"

    scraper = JobTeaserScraper.__new__(JobTeaserScraper)  # évite le réseau
    scraper.base_url = "https://emse.jobteaser.com"
    scraper.offers_url = "https://emse.jobteaser.com/fr/job-offers"
    job = scraper._to_raw_job(raw_jobs[0])
    assert job is not None
    assert job.title == "Stage Data Scientist"
    assert job.company == "Mistral AI"
    assert job.location == "Paris, France"
    assert job.is_internship is True
    assert job.url.endswith("/fr/job-offers/42")
    print("  JobTeaser : extraction __NEXT_DATA__ OK")


def test_jobteaser_html_fallback() -> None:
    html = (
        '<html><body><a href="/fr/job-offers/99">Stage Machine Learning</a>'
        '<a href="/fr/about">A propos</a></body></html>'
    )
    raw_jobs = JobTeaserScraper._extract_jobs_from_html(html)
    assert len(raw_jobs) == 1
    assert raw_jobs[0]["title"] == "Stage Machine Learning"
    print("  JobTeaser : repli liens HTML OK")


def test_jobteaser_card_extraction() -> None:
    html = (
        '<div data-testid="jobad-card">'
        '<p data-testid="jobad-card-company-name">Amundi</p>'
        '<h3><a href="/fr/job-offers/9b758aca-9a81-4adb-8acb-fd096ddcae36-amundi-stage-data-science-h-f">'
        "Stage Data Science H/F</a></h3>"
        '<div data-testid="jobad-card-location">Paris, France</div>'
        '<div data-testid="jobad-card-contract">Stage 4 à 6 mois</div>'
        "</div>"
    )
    raw_jobs = JobTeaserScraper._extract_jobs_from_html(html)
    assert len(raw_jobs) == 1, raw_jobs
    assert raw_jobs[0]["company"] == "Amundi"
    assert raw_jobs[0]["contract"] == "Stage 4 à 6 mois"

    scraper = JobTeaserScraper.__new__(JobTeaserScraper)  # évite le réseau
    scraper.base_url = "https://emse.jobteaser.com"
    job = scraper._to_raw_job(raw_jobs[0])
    assert job is not None
    assert job.title == "Stage Data Science H/F"
    assert job.company == "Amundi"
    assert job.location == "Paris, France"
    assert job.is_internship is True
    assert job.id_externe == "9b758aca-9a81-4adb-8acb-fd096ddcae36"
    assert job.url == (
        "https://emse.jobteaser.com/fr/job-offers/"
        "9b758aca-9a81-4adb-8acb-fd096ddcae36-amundi-stage-data-science-h-f"
    )
    print("  JobTeaser : extraction cartes SSR OK")


def test_jobteaser_non_internship_contract() -> None:
    scraper = JobTeaserScraper.__new__(JobTeaserScraper)
    scraper.base_url = "https://emse.jobteaser.com"
    job = scraper._to_raw_job(
        {
            "title": "Data Scientist",
            "company": "ACME",
            "contract": "Alternance 12 mois",
            "url": "/fr/job-offers/11111111-2222-3333-4444-555555555555-acme-data-scientist",
        }
    )
    assert job is not None
    assert job.is_internship is False
    print("  JobTeaser : contrat non-stage détecté OK")


def test_linkedin_parse_cards() -> None:
    html = "<ul>" + _linkedin_card("4464330131") + _linkedin_card("4464331502") + "</ul>"
    jobs, keys = LinkedInGuestScraper._parse_cards_page(html)

    assert len(jobs) == 2, jobs
    first = jobs[0]
    assert first.title == "Stage Data Scientist", first.title
    assert first.company == "Mistral AI", first.company
    assert first.location == "Paris", first.location
    assert first.source == "linkedin"
    assert first.is_internship is True
    # L'identifiant provient de la div interne (data-entity-urn), pas de l'URL.
    assert first.id_externe == "4464330131", first.id_externe
    # L'URL est débarrassée de sa query string (déduplication stable).
    assert first.url == "https://fr.linkedin.com/jobs/view/stage-4464330131", first.url
    assert first.published_at is not None and first.published_at.year == 2026
    assert keys == ["4464330131", "4464331502"], keys
    # Compatibilité historique : _parse_cards reste appelable seul.
    assert len(LinkedInGuestScraper._parse_cards(html)) == 2
    print("  LinkedIn : parsing des cartes OK (urn, url, date, société)")


def test_linkedin_parse_cards_incomplete() -> None:
    incomplete = (
        '<li><div data-entity-urn="urn:li:jobPosting:999">'
        '<h3 class="base-search-card__title"></h3></div></li>'
    )
    jobs, keys = LinkedInGuestScraper._parse_cards_page(incomplete)
    assert jobs == [], jobs
    # La carte reste comptée : indispensable pour mesurer la progression de pagination.
    assert keys == ["999"], keys
    print("  LinkedIn : carte inexploitable comptée pour la pagination OK")


def test_linkedin_pagination_step() -> None:
    """La pagination avance du nombre RÉEL de cartes reçues (10), et non de 25."""
    result, client = _run_linkedin_fetch({0: _linkedin_page("50"), 10: _linkedin_page("60")})

    # 0 -> 10 -> 20 : le pas vaut 10 (et non 25), la page vide (offset 20) arrête la boucle.
    assert client.requested_starts == [0, 10, 20], client.requested_starts
    assert len(result.jobs) == 20, len(result.jobs)
    assert result.found == 20, result.found
    print("  LinkedIn : pas = taille réelle de page OK (aucune offre sautée)")


def test_linkedin_pagination_stagnation() -> None:
    """Une page déjà vue arrête la pagination (garde-fou anti-boucle infinie)."""
    page = _linkedin_page("70")
    result, client = _run_linkedin_fetch({0: page, 10: page})

    assert client.requested_starts == [0, 10], client.requested_starts
    assert len(result.jobs) == 10, len(result.jobs)
    print("  LinkedIn : arrêt sur page déjà vue OK")


def test_linkedin_dedupe_across_queries() -> None:
    """Deux requêtes renvoyant les mêmes cartes ne dupliquent pas les offres."""
    result, client = _run_linkedin_fetch(
        {0: _linkedin_page("80")}, queries=["Stage Data Scientist", "Stage NLP"]
    )

    # Chaque requête pagine (0 -> 10, puis page vide) et les offres communes sont filtrées.
    assert client.requested_starts == [0, 10, 0, 10], client.requested_starts
    assert len(result.jobs) == 10, len(result.jobs)
    print("  LinkedIn : déduplication inter-requêtes OK")


def test_linkedin_http_429_fallback() -> None:
    """Un 429 arrête proprement le scraper (aucune exception remontée)."""
    result, client = _run_linkedin_fetch({0: 429})

    assert client.requested_starts == [0], client.requested_starts
    assert result.jobs == [] and result.found == 0
    print("  LinkedIn : repli propre sur 429 OK")


if __name__ == "__main__":
    test_is_valid_job()
    test_word_boundary()
    test_models()
    test_dedupe_url()
    test_jobteaser_cookie_parsing()
    test_jobteaser_next_data_extraction()
    test_jobteaser_html_fallback()
    test_jobteaser_card_extraction()
    test_jobteaser_non_internship_contract()
    test_linkedin_parse_cards()
    test_linkedin_parse_cards_incomplete()
    test_linkedin_pagination_step()
    test_linkedin_pagination_stagnation()
    test_linkedin_dedupe_across_queries()
    test_linkedin_http_429_fallback()
    print("TOUS LES TESTS PASSENT")
