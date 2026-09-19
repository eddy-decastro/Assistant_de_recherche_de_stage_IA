"""Tests du module de scraping unifié (filtrage anti-BI, modèles, parsing, pagination)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scrapers import linkedin as linkedin_module
from scrapers.base import _contains_keyword, BaseScraper
from scrapers.jobteaser import JobTeaserScraper
from scrapers.linkedin import GUEST_ENDPOINT, LinkedInGuestScraper
from scrapers.manager import ScraperManager
from scrapers.models import (
    CardEntry,
    PageResult,
    PassConfig,
    RawJob,
    ScrapeResult,
    ScraperConfig,
)


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


class _PerQueryClient(_FakeClient):
    """Client factice servant des pages différentes selon la REQUÊTE et l'offset.

    ``_FakeClient`` ne connaît que l'offset : deux requêtes y reçoivent la même
    page, ce qui masque l'origine des offres. Ce client-là permet de vérifier
    que chaque requête cible apporte bien ses propres résultats.
    """

    def __init__(self, pages_by_query: dict[str, dict[int, str | int]]) -> None:
        super().__init__({})
        self.pages_by_query = pages_by_query

    def get(self, url: str, params: dict | None = None) -> _FakeResponse:
        params = params or {}
        query = str(params.get("keywords", ""))
        start = int(params.get("start", 0))
        self.requested_starts.append(start)
        page = self.pages_by_query.get(query, {}).get(start, "")
        if isinstance(page, int):  # code HTTP simulé (ex. 429)
            return _FakeResponse("", status_code=page)
        return _FakeResponse(page)


def _run_linkedin_fetch(
    pages: dict[int, str | int] | None = None,
    queries: list[str] | None = None,
    *,
    client: _FakeClient | None = None,
    max_offers_per_source: int = 50,
    max_offers_per_query: int | None = None,
    mode: str = "relevance",
) -> tuple[ScrapeResult, _FakeClient]:
    """Exécute ``fetch()`` avec un client factice, sans réseau ni pause entre pages.

    Mono-passe par défaut (``relevance``) : ces tests vérifient la mécanique de
    pagination et de quota, indépendamment de la stratégie hybride — qui est
    couverte par ``tests/test_hybrid_collection.py``.
    """
    config = ScraperConfig(
        target_queries=queries or ["Stage Data Scientist"],
        max_offers_per_source=max_offers_per_source,
        passes=PassConfig.only(
            mode,
            max_offers_per_query=max_offers_per_query or max_offers_per_source,
            # Objectif de source neutralisé : ces tests portent sur la mécanique de
            # pagination et de quota, pas sur les objectifs métier (« 40 dernières »
            # / « 10 plus pertinentes ») qui sont couverts par
            # ``tests/test_hybrid_collection.py``.
            target_new_per_source=None,
        ),
    )
    scraper = LinkedInGuestScraper(config)
    real_client = scraper.client
    fake = client if client is not None else _FakeClient(pages or {})
    scraper.client = fake  # type: ignore[assignment]
    real_client.close()
    previous_range = linkedin_module.SLEEP_RANGE
    linkedin_module.SLEEP_RANGE = (0.0, 0.0)  # neutralise les pauses anti-flood
    try:
        result = scraper.fetch()
    finally:
        linkedin_module.SLEEP_RANGE = previous_range
        scraper.close()
    return result, fake


def test_is_valid_job() -> None:
    cfg = ScraperConfig()

    class _FakeScraper(BaseScraper):
        source = "wttj"

        def _iter_pages(self, query: str, mode: str, cursor: Any, plan: Any) -> PageResult:
            return PageResult()  # pragma: no cover

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

    # "ia" et "ai" ne doivent pas matcher dans les mots français ou anglais courants
    assert _contains_keyword("Stagiaire dialogue client", "ia") is False
    assert _contains_keyword("Stage initial", "ia") is False
    assert _contains_keyword("Spécialiste relations", "ia") is False
    assert _contains_keyword("Stage clair et net", "ai") is False
    assert _contains_keyword("Candidat motivé", "ai") is False

    # "ia" et "ai" isolés ou délimités doivent matcher
    assert _contains_keyword("STAGE IA pour la 3D", "ia") is True
    assert _contains_keyword("Stage Ingénieur AI", "ai") is True
    assert _contains_keyword("Stage (IA)", "ia") is True
    assert _contains_keyword("Stage IA/ML", "ia") is True

    # Tolérance aux espaces multiples
    assert _contains_keyword("STAGE - Deep Reinforcement  Learning", "reinforcement learning") is True
    print("  _contains_keyword : OK")


def test_improved_filtering_and_acronyms() -> None:
    from scrapers.base import screen_rejection
    config = ScraperConfig()

    # Offres IA/DS légitimes qui étaient auparavant rejetées
    valid_titles = [
        "STAGE IA pour la compréhension de scènes 3D (F/H)",
        "Stage - Ingénieur IA - Modélisation de l'entreprise (F/H)",
        "STAGE - Deep Reinforcement  Learning",
        "Ingénieur.e IA - Stage",
        "AI Engineering Intern",
        "Stage Builder IA Agentique (H/F)",
        "Stage ingénieur prédiction de séries temporelles F/H/X",
        "Stage – Validation de modèles de sillage via données SCADA",
        "Internship - Graduate Program DA/DS/DI F/M",
    ]
    for title in valid_titles:
        assert screen_rejection(title, "", config) == "", f"Rejeté à tort : {title}"

    # Vrais hors-sujet qui doivent rester strictement rejetés
    invalid_titles = [
        "Stagiaire - Juriste Propriété Intellectuelle (H/F)",
        "Stage Assistant Chef de Projet RH (F/H)",
        "Stage Commercial B2B",
        "STAGE - Communication & Marketing Digital",
        "Assistant Contrôleur de Gestion",
        "Analyste fonctionnel PMO",
    ]
    for title in invalid_titles:
        assert screen_rejection(title, "", config) != "", f"Accepté à tort : {title}"


def test_models() -> None:
    cfg = ScraperConfig()
    assert cfg.max_offers_per_source == 120
    # Collecte hybride par défaut : fraîcheur (7 j, objectif de 40 nouvelles pour la
    # source, arrêt anticipé à 10 déjà-vues, armé) + rattrapage (objectif de 10
    # nouvelles, aucun arrêt anticipé).
    assert set(cfg.enabled_modes()) == {"freshness", "relevance"}, cfg.enabled_modes()
    assert cfg.pass_config("freshness").early_stop_after_known == 10
    assert cfg.pass_config("freshness").early_stop_min_pages == 2
    assert cfg.pass_config("freshness").arm_early_stop is True
    assert cfg.pass_config("freshness").target_new == 40
    assert cfg.pass_config("relevance").early_stop_after_known == 0
    assert cfg.pass_config("relevance").target_new == 10
    assert cfg.pass_config("freshness").window_days == 7.0
    assert cfg.pass_config("freshness").window_seconds == 604800
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
    # _normalize_url est désormais alignée sur canonical_url (sans schéma, sans www.)
    assert ScraperManager._normalize_url("HTTPS://X/Job/?q=1") == "x/Job"
    assert ScraperManager._normalize_url("https://x/job/") == "x/job"
    assert ScraperManager._normalize_url("http://www.example.com/j") == "example.com/j"
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

    # 1re requête : 0 -> 10, puis page vide (flux épuisé). 2e requête : la première
    # page ne contient QUE des cartes déjà connues du run -> arrêt immédiat
    # (déduplication transverse) sans requêter la page suivante.
    assert client.requested_starts == [0, 10, 0], client.requested_starts
    assert len(result.jobs) == 10, len(result.jobs)
    print("  LinkedIn : déduplication inter-requêtes OK")


def test_linkedin_http_429_fallback() -> None:
    """Un 429 arrête proprement le scraper (aucune exception remontée)."""
    result, client = _run_linkedin_fetch({0: 429})

    assert client.requested_starts == [0], client.requested_starts
    assert result.jobs == [] and result.found == 0
    print("  LinkedIn : repli propre sur 429 OK")


def test_linkedin_quota_par_requete() -> None:
    """Chaque requête cible consomme son quota : la 1re ne bloque plus les suivantes.

    Le client factice sert une page distincte par requête, ce qui permet de
    vérifier *l'origine* des offres collectées (et non seulement leur nombre).
    """
    client = _PerQueryClient(
        {
            "Stage Data Scientist": {0: _linkedin_page("90"), 10: _linkedin_page("95")},
            "Stage NLP": {0: _linkedin_page("70")},
        }
    )
    result, client = _run_linkedin_fetch(
        queries=["Stage Data Scientist", "Stage NLP"],
        client=client,
        max_offers_per_source=10,
        max_offers_per_query=5,
    )

    # 1re requête : 5 offres (quota atteint) ; 2e requête : TOUJOURS interrogée (elle
    # apporte 5 offres de sa propre page). Auparavant, le plafond global atteint par
    # la 1re requête court-circuitait toutes les suivantes.
    assert client.requested_starts == [0, 0], client.requested_starts
    assert len(result.jobs) == 10, len(result.jobs)
    # ``found`` compte désormais les cartes EXAMINÉES : 5 par requête, le quota
    # interrompant la page dès la 5e offre retenue (au lieu de 10 auparavant, où
    # la page entière était comptée).
    assert result.found == 10, result.found
    urns = {job.id_externe for job in result.jobs}
    assert urns == {*(f"90{i:02d}" for i in range(5)), *(f"70{i:02d}" for i in range(5))}, urns
    print("  LinkedIn : quota par requête OK (chaque requête contribue)")


def test_jobteaser_quota_par_requete() -> None:
    """Idem côté JobTeaser : le quota est appliqué par requête, pas globalement."""
    cards = "".join(
        '<div data-testid="jobad-card"><h3><a href="/fr/job-offers/'
        f'9b758aca-9a81-4adb-8acb-fd096ddcae3{i}-stage-data-{i}">Stage Data {i}</a></h3></div>'
        for i in range(8)
    )
    config = ScraperConfig(
        target_queries=["q1", "q2"],
        max_offers_per_source=10,
        passes=PassConfig.only("relevance", max_offers_per_query=3),
    )
    scraper = JobTeaserScraper(config)
    real_client, real_curl = scraper.client, scraper._curl
    scraper.client = _FakeClient({})
    scraper._curl = None
    scraper.token = "jeton-de-test"  # satisfait _has_auth sans cookie réel
    requested: list[str] = []

    def fake_fetch_html(url: str, params: dict[str, str]) -> tuple[int, str]:
        requested.append(params.get("q", ""))
        return 200, cards

    scraper._fetch_html = fake_fetch_html  # type: ignore[assignment]
    try:
        result = scraper.fetch()
    finally:
        real_client.close()
        scraper.close()
        if real_curl is not None:
            real_curl.close()

    assert requested == ["q1", "q2"], requested
    assert len(result.jobs) == 6, len(result.jobs)
    # Cartes examinées : 3 en q1 (quota) puis 6 en q2 — les 3 premières cartes de
    # q2 sont déjà connues du run (déduplication transverse) avant d'atteindre le
    # quota. Aucune offre n'est donc collectée deux fois.
    assert result.found == 9, result.found
    print("  JobTeaser : quota par requête OK (les 2 requêtes contribuent)")


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
    test_linkedin_quota_par_requete()
    test_jobteaser_quota_par_requete()
    print("TOUS LES TESTS PASSENT")
