"""Tests du rattrapage des descriptions (cache disque, extracteurs, quota, backfill).

Aucun accès réseau : les réponses HTML sont synthétiques (structure copiée des
pages réelles) et les clients HTTP sont factices.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backfill_descriptions import DescriptionBackfill  # noqa: E402
from scrapers.cache import DiskCache  # noqa: E402
from scrapers.jobteaser import JobTeaserScraper  # noqa: E402
from scrapers.linkedin import (  # noqa: E402
    DETAIL_ENDPOINT,
    LinkedInGuestScraper,
    extract_job_id,
    parse_description,
)
from scrapers.models import ScraperConfig  # noqa: E402
from src.constants import TIER_1  # noqa: E402
from src.storage.database import Database  # noqa: E402

# --- Extraits HTML calqués sur les pages réelles ---------------------------- #

LINKEDIN_DETAIL_HTML = """
<html><body>
<section class="core-section-container my-3 description">
  <div class="core-section-container__content break-words">
    <div class="description__text description__text--rich">
      <section class="show-more-less-html" data-max-lines="5">
        <div class="show-more-less-html__markup show-more-less-html__markup--clamp-after-5">
          <strong>Job Description<br/><br/></strong>
          <p>Nous recherchons un stagiaire en apprentissage par renforcement profond.</p>
          <ul><li>Modelisation PyTorch</li><li>Optimisation et probabilites</li></ul>
        </div>
      </section>
    </div>
  </div>
</section>
</body></html>
"""

JOBTEASER_DETAIL_HTML = """
<html><body><main>
  <div data-testid="jobad-DetailView__Heading__title">Internship - Data scientist</div>
  <article class="Description-module__LAa0-a__main">
    <div data-testid="jobad-DetailView__Description">
      <p>Job Title Internship - Data scientist</p>
      <p>Nous recherchons un stagiaire pour travailler sur des modeles de ML a grande echelle.</p>
      <ul><li>Python et PyTorch</li><li>Spark et Airflow</li></ul>
    </div>
  </article>
</main></body></html>
"""

class _FakeDetailClient:
    """Client HTTP factice servant une page détail et comptant les appels."""

    def __init__(self, html: str, status_code: int = 200) -> None:
        self.html = html
        self.status_code = status_code
        self.calls: list[str] = []

    def get(self, url: str, params: dict | None = None) -> httpx.Response:
        self.calls.append(url)
        request = httpx.Request("GET", url, params=params)
        response = httpx.Response(self.status_code, text=self.html, request=request)
        return response

    def close(self) -> None:  # compatibilité BaseScraper.close()
        return None


def test_cache_disque() -> None:
    """Cache : aller-retour, clé URL transformée en nom de fichier sûr, compteurs."""
    with tempfile.TemporaryDirectory() as tmp:
        cache = DiskCache(tmp)
        url = "https://fr.linkedin.com/jobs/view/stage-ia-4461774420?refId=x"
        assert cache.get("linkedin", url) is None, "Le cache doit être vide au départ."
        path = cache.set("linkedin", url, "Description en cache")
        assert path.exists() and path.suffix == ".txt", path
        # Le nom de fichier ne doit contenir aucun caractère interdit sous Windows.
        assert not set('<>:"/\\|?*') & set(path.name), path.name
        assert cache.get("linkedin", url) == "Description en cache"
        assert cache.count("linkedin") == 1
        assert (cache.hits, cache.misses) == (1, 1), (cache.hits, cache.misses)
        print("  cache disque : aller-retour + nom de fichier sûr OK")


def test_extract_job_id() -> None:
    """L'identifiant LinkedIn est extrait des différentes formes d'URL rencontrées."""
    cases = {
        "https://fr.linkedin.com/jobs/view/4461774420": "4461774420",
        "https://fr.linkedin.com/jobs/view/2027-quantitative-research-risk-and-treasury-"
        "off-cycle-analyst-paris-at-jpmorganchase-4461774420": "4461774420",
        "https://fr.linkedin.com/jobs/view/weather-quantitative-research-internship-"
        "programme-at-engelhart-4465833512": "4465833512",
        "urn:li:jobPosting:4464330131": "4464330131",
        "https://www.linkedin.com/jobs/search/?currentJobId=4464331502": "4464331502",
        "4464331502": "4464331502",
        "https://fr.linkedin.com/jobs/view/stage-aux-petits-oignons": "",
        "": "",
    }
    for value, expected in cases.items():
        got = extract_job_id(value)
        assert got == expected, f"{value!r}: attendu {expected!r}, obtenu {got!r}"
    print(f"  LinkedIn : extraction de l'identifiant OK ({len(cases)} cas)")

def test_parse_descriptions() -> None:
    """Les deux extracteurs retournent un texte structuré, sans balise."""
    linkedin_text = parse_description(LINKEDIN_DETAIL_HTML)
    assert "apprentissage par renforcement profond" in linkedin_text, linkedin_text
    assert "Modelisation PyTorch" in linkedin_text and "Optimisation" in linkedin_text
    assert "<" not in linkedin_text and ">" not in linkedin_text, "Les balises doivent être retirées."
    assert parse_description("") == "" and parse_description("<div></div>") == ""

    jobteaser_text = JobTeaserScraper.parse_description(JOBTEASER_DETAIL_HTML)
    assert "stagiaire pour travailler sur des modeles de ML" in jobteaser_text, jobteaser_text
    assert "Spark et Airflow" in jobteaser_text
    assert "<" not in jobteaser_text
    # Une page sans description jobad (mur de connexion, 403 rendu en HTML) → "".
    assert JobTeaserScraper.parse_description("<html><body><p>Accès refusé</p></body></html>") == ""
    print("  extracteurs : descriptions LinkedIn et JobTeaser OK")


def test_linkedin_fetch_description_avec_cache() -> None:
    """Le détail LinkedIn est appelé une fois, puis servi par le cache disque."""
    with tempfile.TemporaryDirectory() as tmp:
        cache = DiskCache(tmp)
        scraper = LinkedInGuestScraper(ScraperConfig())
        real_client = scraper.client
        fake = _FakeDetailClient(LINKEDIN_DETAIL_HTML)
        scraper.client = fake  # type: ignore[assignment]
        real_client.close()
        try:
            url = "https://fr.linkedin.com/jobs/view/stage-ml-at-mistral-4461774420"
            first = scraper.fetch_description(url, cache=cache)
            second = scraper.fetch_description(url, cache=cache)
        finally:
            scraper.close()

        assert "apprentissage par renforcement" in first, first
        assert second == first, "La seconde lecture doit venir du cache."
        assert len(fake.calls) == 1, fake.calls
        assert fake.calls[0] == DETAIL_ENDPOINT.format(job_id="4461774420"), fake.calls
        print("  LinkedIn : description récupérée puis servie par le cache OK")


def test_linkedin_fetch_description_erreur_http() -> None:
    """Un 429 remonte en HTTPStatusError (pour être distingué d'une page vide)."""
    scraper = LinkedInGuestScraper(ScraperConfig())
    real_client = scraper.client
    fake = _FakeDetailClient("", status_code=429)
    scraper.client = fake  # type: ignore[assignment]
    real_client.close()
    try:
        try:
            scraper.fetch_description("https://fr.linkedin.com/jobs/view/stage-4461774420")
        except httpx.HTTPStatusError as exc:
            assert exc.response.status_code == 429, exc.response.status_code
        else:
            raise AssertionError("Un 429 doit remonter une HTTPStatusError.")
    finally:
        scraper.close()
    print("  LinkedIn : 429 remonté proprement OK")


def test_jobteaser_fetch_description_erreur_http() -> None:
    """Un 403 Cloudflare remonte en HTTPStatusError (pas une description vide)."""
    scraper = JobTeaserScraper(ScraperConfig())
    real_client, real_curl = scraper.client, scraper._curl
    scraper.client, scraper._curl = _FakeDetailClient(""), None
    real_client.close()
    try:
        scraper._fetch_html = lambda url, params: (403, "")  # type: ignore[assignment]
        try:
            scraper.fetch_description("https://emse.jobteaser.com/fr/job-offers/abc")
        except httpx.HTTPStatusError as exc:
            assert exc.response.status_code == 403, exc.response.status_code
        else:
            raise AssertionError("Un 403 doit remonter une HTTPStatusError.")
    finally:
        scraper.close()
        if real_curl is not None:
            real_curl.close()
    print("  JobTeaser : 403 remonté proprement OK")

def test_scraper_config_depuis_yaml() -> None:
    """La section `scrapers` de config.yaml pilote réellement ScraperConfig."""
    config = {
        "scrapers": {
            "target_queries": ["Stage IA", "Stage ML"],
            "max_offers_per_source": 100,
            "max_offers_per_query": 30,
            "enabled_sources": ["linkedin"],
            "cle_inconnue": "ignorée sans erreur",
        }
    }
    cfg = ScraperConfig.from_config(config)
    assert cfg.target_queries == ["Stage IA", "Stage ML"], cfg.target_queries
    assert cfg.max_offers_per_source == 100
    assert cfg.per_query_quota == 30, cfg.per_query_quota
    assert cfg.enabled_sources == ["linkedin"]
    assert cfg.request_timeout_seconds == 30.0, "Les clés absentes gardent leur défaut."

    # Sans section : défauts du code, quota réparti équitablement entre requêtes.
    default = ScraperConfig.from_config({})
    assert default.max_offers_per_source == 50
    assert default.per_query_quota == 50 // len(default.target_queries), default.per_query_quota
    # Section mal typée ou invalide : repli sur les défauts, jamais d'exception.
    assert ScraperConfig.from_config({"scrapers": "pas-un-dict"}).max_offers_per_source == 50
    assert (
        ScraperConfig.from_config({"scrapers": {"max_offers_per_source": "beaucoup"}})
        .max_offers_per_source
        == 50
    )
    print("  config.yaml vers ScraperConfig : section 'scrapers' réellement lue OK")


class _FakeDescriptionScraper:
    """Scraper factice : sert des descriptions par URL, respecte le cache."""

    def __init__(self, texts: dict[str, str], status: int = 200) -> None:
        self.texts = texts
        self.status = status
        self.calls: list[str] = []

    def fetch_description(self, url: str, cache: DiskCache | None = None) -> str:
        if cache is not None:
            cached = cache.get("linkedin", url)
            if cached is not None:
                return cached
        self.calls.append(url)
        if self.status >= 400:
            request = httpx.Request("GET", url)
            raise httpx.HTTPStatusError(
                "erreur simulée",
                request=request,
                response=httpx.Response(self.status, request=request),
            )
        text = self.texts.get(url, "")
        if text and cache is not None:
            cache.set("linkedin", url, text)
        return text

    def close(self) -> None:  # compatibilité BaseScraper.close()
        return None


def _seed_db(db: Database, count: int = 2) -> list[str]:
    """Insère ``count`` offres LinkedIn sans description et retourne leurs URL."""
    urls: list[str] = []
    for index in range(count):
        url = f"https://fr.linkedin.com/jobs/view/stage-ml-{index}-446177440{index}"
        urls.append(url)
        db.upsert_job(
            {
                "title": f"Stage ML {index}",
                "company": "Mistral AI",
                "location": "Paris",
                "url": url,
                "description": "",
                "source": "linkedin",
                "company_tier": TIER_1,
                "semantic_score": 20.0,
                "final_score": 20.0,
            }
        )
    return urls


def _runner(db: Database, cache: DiskCache, fake: _FakeDescriptionScraper, **kwargs) -> DescriptionBackfill:
    """Construit un backfill dont les scrapers réels sont remplacés par le factice."""
    runner = DescriptionBackfill({"scrapers": {}}, db, cache, **kwargs)
    for scraper in runner.scrapers.values():  # libère les clients httpx réels (aucune requête)
        scraper.close()
    runner.scrapers = {"linkedin": fake}
    return runner

def test_backfill_simulation_ecriture_cache() -> None:
    """Rattrapage : simulation sans écriture, puis écriture réelle, puis cache disque."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "backfill.db")
        urls = _seed_db(db, 2)
        long_text = "Description de stage " + "modele de ML " * 20  # > MIN_DESCRIPTION_LENGTH

        # 1) Simulation : tout est récupéré, rien n'est écrit.
        fake = _FakeDescriptionScraper({url: long_text for url in urls})
        dry = _runner(db, DiskCache(Path(tmp) / "cache_dry"), fake, sleep=0.0, dry_run=True)
        report = dry.run(["linkedin"])
        dry.close()
        assert report.targets == 2 and report.enriched == 2, report
        assert report.written == 0, "La simulation ne doit rien écrire."
        assert db.count_with_description() == 0
        assert len(fake.calls) == 2, fake.calls

        # 2) Exécution réelle (cache neuf) : les deux offres sont enrichies.
        cache = DiskCache(Path(tmp) / "cache_real")
        fake = _FakeDescriptionScraper({url: long_text for url in urls})
        real = _runner(db, cache, fake, sleep=0.0)
        report = real.run(["linkedin"])
        real.close()
        assert report.enriched == 2 and report.written == 2, report
        assert report.cache_hits == 0 and report.cache_misses == 2, report
        assert db.count_with_description() == 2
        assert db.get_jobs_missing_description() == [], "Plus aucune offre sans description."
        assert len(fake.calls) == 2, fake.calls

        # 3) Relance forcée : le cache disque évite tout nouvel appel réseau.
        cached = _runner(db, cache, fake, sleep=0.0)
        report = cached.run(["linkedin"], refresh=True)
        cached.close()
        assert report.enriched == 2 and report.cache_hits == 2, report
        assert len(fake.calls) == 2, f"Aucun appel réseau attendu, obtenu {fake.calls}"
        db.engine.dispose()
    print("  backfill : simulation, écriture en base et cache disque OK")


def test_backfill_rate_limit() -> None:
    """Un 429 interrompt proprement la source et est signalé dans le rapport."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "rate.db")
        _seed_db(db, 3)
        fake = _FakeDescriptionScraper({}, status=429)
        runner = _runner(db, DiskCache(Path(tmp) / "cache"), fake, sleep=0.0)
        report = runner.run(["linkedin"])
        runner.close()

        assert report.rate_limited == ["linkedin"], report.rate_limited
        assert report.failed == 1 and report.enriched == 0, report
        assert len(fake.calls) == 1, f"Arrêt dès le premier 429 attendu : {fake.calls}"
        assert db.count_with_description() == 0
        db.engine.dispose()
    print("  backfill : arrêt propre sur 429 OK")


def test_backfill_description_trop_courte() -> None:
    """Une extraction trop courte est un échec : rien n'est écrit en base."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "short.db")
        urls = _seed_db(db, 1)
        fake = _FakeDescriptionScraper({urls[0]: "trop court"})
        runner = _runner(db, DiskCache(Path(tmp) / "cache"), fake, sleep=0.0, min_length=150)
        report = runner.run(["linkedin"])
        runner.close()

        assert report.enriched == 0 and report.failed == 1, report
        assert db.count_with_description() == 0
        db.engine.dispose()
    print("  backfill : description trop courte rejetée OK")


def test_backfill_source_non_supportee() -> None:
    """Une source sans extractrice (ex. wttj) est ignorée, jamais comptée à tort."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "unsupported.db")
        db.upsert_job(
            {
                "title": "Stage IA",
                "company": "Doctolib",
                "location": "Paris",
                "url": "https://www.welcometothejungle.com/fr/companies/x/jobs/y",
                "description": "",
                "source": "wttj",
                "company_tier": TIER_1,
                "semantic_score": 0.0,
                "final_score": 0.0,
            }
        )
        fake = _FakeDescriptionScraper({})
        runner = _runner(db, DiskCache(Path(tmp) / "cache"), fake, sleep=0.0)
        report = runner.run(["wttj"])
        runner.close()

        assert report.skipped == 1 and report.enriched == 0, report
        assert fake.calls == [], "Aucun appel réseau pour une source non supportée."
        db.engine.dispose()
    print("  backfill : source non supportée ignorée OK")


if __name__ == "__main__":
    test_cache_disque()
    test_extract_job_id()
    test_parse_descriptions()
    test_linkedin_fetch_description_avec_cache()
    test_linkedin_fetch_description_erreur_http()
    test_jobteaser_fetch_description_erreur_http()
    test_scraper_config_depuis_yaml()
    test_backfill_simulation_ecriture_cache()
    test_backfill_rate_limit()
    test_backfill_description_trop_courte()
    test_backfill_source_non_supportee()
    print("TOUS LES TESTS PASSENT")
