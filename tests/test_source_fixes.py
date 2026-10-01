"""Sources : session JobTeaser expirée signalée, stages WTTJ limités à la France."""
from __future__ import annotations

from urllib.parse import unquote

from scrapers.health import detect_degraded_sources
from scrapers.jobteaser import JobTeaserScraper
from scrapers.models import PassConfig, ScraperConfig, is_incomplete_stop
from scrapers.wttj import WelcomeToTheJungleScraper

LOGIN_PAGE = (
    "<html><head><title>Bienvenue sur votre Career Center | JobTeaser</title></head><body>"
    '<h1>Connexion via email</h1><form action="/sign_in#sign-in-form">'
    '<input type="email" name="email"><input type="password" name="password"></form>'
    "</body></html>"
)
EMPTY_RESULTS = "<html><head><title>Offres | JobTeaser</title></head><body><p>Aucune offre</p></body></html>"


def _jobteaser(pages: dict[str, str]) -> tuple[JobTeaserScraper, list[str]]:
    config = ScraperConfig(
        target_queries=["q1", "q2"],
        enabled_sources=["jobteaser"],
        passes=PassConfig.only("relevance", max_offers_per_query=5),
    )
    scraper = JobTeaserScraper(config)
    if scraper._curl is not None:
        scraper._curl.close()
    scraper._curl = None
    scraper.token = "jeton-de-test"  # satisfait _has_auth sans cookie réel
    requested: list[str] = []

    def fake_fetch_html(url: str, params: dict[str, str]) -> tuple[int, str]:
        requested.append(params.get("q", ""))
        return 200, pages[params.get("q", "")]

    scraper._fetch_html = fake_fetch_html  # type: ignore[assignment]
    return scraper, requested


def test_jobteaser_session_expiree_signalee():
    scraper, requested = _jobteaser({"q1": LOGIN_PAGE, "q2": LOGIN_PAGE})
    try:
        reports = scraper.fetch().query_reports
    finally:
        scraper.close()
    assert [r.stop_reason for r in reports] == ["auth_expired", "auth_expired"], reports
    assert all(is_incomplete_stop(r.stop_reason) for r in reports)
    assert "session" in reports[0].stop_detail.lower()
    assert requested == ["q1"], requested  # plus aucune requête une fois l'expiration vue
    assert detect_degraded_sources(reports, {"jobteaser": [500, 480, 520]})


def test_jobteaser_recherche_vide_reste_fin_de_flux():
    scraper, _ = _jobteaser({"q1": EMPTY_RESULTS, "q2": EMPTY_RESULTS})
    try:
        reports = scraper.fetch().query_reports
    finally:
        scraper.close()
    assert [r.stop_reason for r in reports] == ["stream_end", "stream_end"], reports


class _FakeAlgoliaResponse:
    status_code = 200

    def json(self):
        return {"results": [{"hits": []}]}

    def raise_for_status(self):
        return None


class _FakeAlgoliaClient:
    def __init__(self):
        self.payloads: list[dict] = []
        self.headers: dict[str, str] = {}

    def post(self, url, json=None):
        self.payloads.append(json)
        return _FakeAlgoliaResponse()

    def close(self):
        return None


def _wttj_filters(monkeypatch, country: str | None) -> str:
    if country is None:
        monkeypatch.delenv("WTTJ_COUNTRY", raising=False)
    else:
        monkeypatch.setenv("WTTJ_COUNTRY", country)
    scraper = WelcomeToTheJungleScraper(ScraperConfig())
    scraper.client.close()
    fake = _FakeAlgoliaClient()
    scraper.client = fake  # type: ignore[assignment]
    scraper._search("Data Scientist", 0)
    params = fake.payloads[0]["requests"][0]["params"]
    return unquote(params.split("filters=", 1)[1].split("&", 1)[0])


def test_wttj_stages_en_france_par_defaut(monkeypatch):
    assert _wttj_filters(monkeypatch, None) == "contract_type:internship AND offices.country_code:FR"


def test_wttj_filtre_pays_desactivable(monkeypatch):
    assert _wttj_filters(monkeypatch, "") == "contract_type:internship"


def test_rattrapage_ignore_les_sources_desactivees(tmp_path):
    """Sans source explicite, le rattrapage ne touche que les sources activées."""
    from backfill_descriptions import DescriptionBackfill
    from scrapers.cache import DiskCache
    from src.storage.database import Database

    db = Database(str(tmp_path / "t.db"))
    runner = DescriptionBackfill(
        {"scrapers": {"enabled_sources": ["linkedin", "wttj"]}}, db, DiskCache(tmp_path / "c"), sleep=0
    )
    try:
        report = runner.run()
    finally:
        runner.close()
        db.engine.dispose()
    assert set(report.per_source) == {"linkedin"}, report.per_source


def test_rattrapage_main_sans_source_explicite(tmp_path, monkeypatch):
    """Point d'entrée par défaut (--source all) : aucun plantage, sources activées seules."""
    import backfill_descriptions

    db_path = str(tmp_path / "t.db")
    monkeypatch.setattr(
        backfill_descriptions, "load_config",
        lambda: {"database": {"path": db_path}, "scrapers": {"enabled_sources": ["linkedin", "wttj"]}},
    )
    assert backfill_descriptions.main(["--dry-run", "--cache-dir", str(tmp_path / "c")]) == 0
