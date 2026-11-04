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


def _wttj_filters_from_config(monkeypatch, country) -> str:
    monkeypatch.delenv("WTTJ_COUNTRY", raising=False)
    scraper = WelcomeToTheJungleScraper(ScraperConfig(wttj_country=country))
    scraper.client.close()
    return scraper._filters()


def test_wttj_filtre_pays_reglable_en_config(monkeypatch):
    """S5 : le pays se règle dans config.yaml (scrapers.wttj_country)."""
    assert _wttj_filters_from_config(monkeypatch, "fr") == "contract_type:internship AND offices.country_code:FR"
    assert _wttj_filters_from_config(monkeypatch, ["FR", "BE"]) == (
        "contract_type:internship AND (offices.country_code:FR OR offices.country_code:BE)"
    )
    assert _wttj_filters_from_config(monkeypatch, "") == "contract_type:internship"
    assert _wttj_filters_from_config(monkeypatch, None) == "contract_type:internship"
    # Une valeur invalide (injection de filtre) est ignorée.
    assert _wttj_filters_from_config(monkeypatch, "FR OR 1") == "contract_type:internship"


def test_wttj_variable_env_prioritaire_sur_config(monkeypatch):
    monkeypatch.setenv("WTTJ_COUNTRY", "BE")
    scraper = WelcomeToTheJungleScraper(ScraperConfig(wttj_country="FR"))
    scraper.client.close()
    assert scraper._filters().endswith("offices.country_code:BE")


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


# --------------------------------------------------------------------------- #
# S6 — LinkedIn invité ignore f_JT=I : requêtes contraintes au stage, CDI écartés
# --------------------------------------------------------------------------- #
def _linkedin(**config):
    from scrapers.linkedin import LinkedInGuestScraper

    scraper = LinkedInGuestScraper(ScraperConfig(passes=PassConfig.only("relevance"), **config))
    scraper.client.close()
    return scraper


def test_linkedin_requete_contrainte_au_stage():
    from scrapers.linkedin import INTERNSHIP_KEYWORDS_CLAUSE

    scraper = _linkedin()
    assert scraper.search_keywords("Data Scientist") == f"Data Scientist {INTERNSHIP_KEYWORDS_CLAUSE}"
    # Requête qui parle déjà de stage : inchangée.
    assert scraper.search_keywords("stage deep learning") == "stage deep learning"
    assert scraper.search_keywords("research intern machine learning") == "research intern machine learning"
    # « Stagiaire » n'est pas confondu avec un mot qui contient « stage » par hasard.
    assert scraper.search_keywords("Backstage ML") == f"Backstage ML {INTERNSHIP_KEYWORDS_CLAUSE}"
    assert _linkedin(linkedin_internship_keywords=False).search_keywords("Data Scientist") == "Data Scientist"


def test_linkedin_envoie_la_requete_contrainte():
    from scrapers.models import PASS_RELEVANCE

    scraper = _linkedin()
    sent = {}

    class _Resp:
        status_code = 200
        text = "<ul></ul>"

        def raise_for_status(self):
            return None

    def fake_get(url, params=None, **kwargs):
        sent.update(params or {})
        return _Resp()

    scraper.client.get = fake_get  # type: ignore[assignment]
    plan = scraper.config.build_plan(PASS_RELEVANCE, date_order_reliable=False, server_window_filter=True)
    scraper._iter_pages("Machine Learning", PASS_RELEVANCE, None, plan)
    assert sent["keywords"].startswith("Machine Learning (stage OR")
    assert sent["f_JT"] == "I"


def test_linkedin_fiche_sans_mention_de_stage_ecartee_a_la_collecte():
    """Un CDI étiqueté « stage » par LinkedIn est rejeté avant toute notation LLM."""
    from scrapers.models import RawJob

    scraper = _linkedin()
    cdi = (
        "Nous recrutons un Data Scientist en CDI pour rejoindre notre équipe Machine Learning. "
        "Vous concevrez des modèles de deep learning en PyTorch, déploierez des pipelines MLOps "
        "et accompagnerez les équipes produit. Expérience significative appréciée."
    )
    stage = cdi.replace("en CDI", "en stage de fin d'études (6 mois)")
    job = RawJob(
        id_externe="123456789",
        source="linkedin",
        title="Data Scientist H/F",
        company="Acme",
        location="Paris",
        url="https://www.linkedin.com/jobs/view/123456789",
        description="",
    )
    scraper._fetch_detail = lambda _job: (cdi, 1)  # type: ignore[assignment]
    rejection, _, _, final = scraper._screen_job(job)
    assert rejection.startswith("pas un stage") and final is True
    scraper._fetch_detail = lambda _job: (stage, 1)  # type: ignore[assignment]
    rejection, kept, _, _ = scraper._screen_job(job)
    assert rejection == "" and "fin d'études" in kept.description
