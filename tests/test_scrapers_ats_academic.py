"""Tests unitaires pour les scrapers ATS Direct, R&D Académique, l'optimisation WTTJ et la parallélisation."""

from __future__ import annotations

import concurrent.futures
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scrapers.academic_rd import AcademicRDScraper
from scrapers.ats import DirectATSScraper
from scrapers.base import BaseScraper, _company_exclusion, alternance_semantic_reason, title_exclusion
from scrapers.known import InMemoryKnownIndex
from scrapers.manager import SCRAPER_REGISTRY, ScraperManager
from scrapers.models import CardEntry, PageResult, PassConfig, PassPlan, RawJob, ScraperConfig, SeenEntry
from scrapers.wttj import WelcomeToTheJungleScraper


# --------------------------------------------------------------------------- #
# Mocks HTTP pour ATS
# --------------------------------------------------------------------------- #
class _MockResponse:

    def __init__(self, data: Any, status_code: int = 200, text: str = "") -> None:
        self._data = data
        self.status_code = status_code
        self.text = text or (str(data) if isinstance(data, str) else "")
        self.request = httpx.Request("GET", "https://mock.api")

    def json(self) -> Any:
        return self._data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("Erreur HTTP simulée", request=self.request, response=self)


def test_direct_ats_scraper_filtering() -> None:
    """Vérifie que DirectATSScraper filtre les CDI, les postes hors France et garde les stages Data/IA."""
    config = ScraperConfig(
        passes=PassConfig.only("freshness", window_days=None),
        ats_boards={
            "lever": ["mistral"],
            "greenhouse": ["datadog"],
            "ashby": ["dust"],
            "smartrecruiters": ["sanofi"],
        },
    )
    scraper = DirectATSScraper(config)

    # Données simulées pour chaque board
    lever_data = [
        # 1. Stage ML France -> Doit être retenu
        {
            "id": "lev-1",
            "text": "Machine Learning Research Intern",
            "categories": {"location": "Paris, France", "commitment": "Intern"},
            "hostedUrl": "https://jobs.lever.co/mistral/lev-1",
            "descriptionPlain": "Recherche en deep learning et modèles de fondation avec PyTorch.",
            "lists": [],
            "createdAt": 1720000000000,
        },
        # 2. CDI France -> Écarté (pas un stage)
        {
            "id": "lev-2",
            "text": "Senior Machine Learning Engineer",
            "categories": {"location": "Paris, France", "commitment": "Full-time"},
            "hostedUrl": "https://jobs.lever.co/mistral/lev-2",
            "descriptionPlain": "CDI pour ingénieur senior.",
            "lists": [],
        },
        # 3. Stage USA -> Écarté (hors France)
        {
            "id": "lev-3",
            "text": "Data Science Intern",
            "categories": {"location": "San Francisco, CA, USA", "commitment": "Intern"},
            "hostedUrl": "https://jobs.lever.co/mistral/lev-3",
            "descriptionPlain": "Machine learning internship in USA.",
            "lists": [],
        },
    ]

    greenhouse_data = {
        "jobs": [
            # 1. Stage IA France -> Doit être retenu
            {
                "id": "gh-1",
                "title": "Intern - AI Research & Foundation Models",
                "location": {"name": "Paris, France"},
                "absolute_url": "https://boards.greenhouse.io/datadog/jobs/gh-1",
                "content": "<p>Stage de fin d'études en vision artificielle et NLP avec PyTorch.</p>",
                "updated_at": "2026-09-10T12:00:00Z",
            },
            # 2. Stage Sales -> Écarté par le filtre métier car non DS/ML
            {
                "id": "gh-2",
                "title": "Intern - Sales & Business Development",
                "location": {"name": "Paris, France"},
                "absolute_url": "https://boards.greenhouse.io/datadog/jobs/gh-2",
                "content": "<p>Stage commercial et prospection B2B.</p>",
                "updated_at": "2026-09-10T12:00:00Z",
            },
        ]
    }

    ashby_data = {
        "jobs": [
            # 1. Stage LLM France -> Doit être retenu
            {
                "id": "ash-1",
                "title": "LLM Systems Engineering Intern",
                "location": "Paris (France)",
                "employmentType": "Internship",
                "jobUrl": "https://jobs.ashbyhq.com/dust/ash-1",
                "descriptionPlain": "Construire des agents IA et pipelines RAG avancés avec LangChain et PyTorch.",
                "publishedAt": "2026-09-12T10:00:00Z",
            }
        ]
    }

    smartrecruiters_data = {
        "content": [
            # 1. Stage Biostatistiques & IA France -> Doit être retenu
            {
                "id": "sr-1",
                "name": "Stage M2 - Biostatistiques et Machine Learning pour la santé",
                "location": {"city": "Paris", "country": "France"},
                "typeOfEmployment": {"label": "Internship"},
                "releasedDate": "2026-09-11T08:00:00Z",
            }
        ]
    }

    def mock_get(url: str, **kwargs: Any) -> _MockResponse:
        if "lever.co" in url:
            return _MockResponse(lever_data)
        if "greenhouse.io" in url:
            return _MockResponse(greenhouse_data)
        if "ashbyhq.com" in url:
            return _MockResponse(ashby_data)
        if "smartrecruiters.com" in url:
            return _MockResponse(smartrecruiters_data)
        return _MockResponse({}, status_code=404)

    scraper.client.get = MagicMock(side_effect=mock_get)  # type: ignore[assignment]

    result = scraper.run()
    # On doit retrouver les offres ML/IA valides en France :
    # - lev-1 (Mistral AI)
    # - gh-1 (Datadog)
    # - ash-1 (Dust)
    # - sr-1 (Sanofi)
    job_ids = [j.id_externe for j in result.jobs]
    assert "lever_mistral_lev-1" in job_ids
    assert "gh_datadog_gh-1" in job_ids
    assert "ashby_dust_ash-1" in job_ids
    assert "sr_sanofi_sr-1" in job_ids

    # Les hors France et CDI ne doivent pas être conservés
    assert "lever_mistral_lev-2" not in job_ids
    assert "lever_mistral_lev-3" not in job_ids
    assert "gh_datadog_gh-2" not in job_ids  # Écarté car Sales (non DS/ML)

    for job in result.jobs:
        assert job.source == "ats"
        assert job.is_internship is True


# --------------------------------------------------------------------------- #
# Mocks HTTP pour AcademicRDScraper
# --------------------------------------------------------------------------- #
def test_academic_rd_scraper_feeds() -> None:
    """Vérifie le parsing RSS / HTML et filtrage pour Inria, CNRS et CEA."""
    scraper = AcademicRDScraper(
        ScraperConfig(passes=PassConfig.only("freshness", window_days=None))
    )

    inria_rss = """<?xml version="1.0" encoding="utf-8"?>
    <rss version="2.0">
      <channel>
        <title>Offres Inria</title>
        <item>
          <title>Stage M2 - Apprentissage par renforcement pour la robotique autonome</title>
          <link>https://jobs.inria.fr/public/classic/fr/offres/2026-001</link>
          <guid>inria-2026-001</guid>
          <description>Sujet de stage de fin d'études : modélisation et deep reinforcement learning avec PyTorch.</description>
          <pubDate>Thu, 10 Sep 2026 09:00:00 +0200</pubDate>
        </item>
        <item>
          <title>Poste CDI - Gestionnaire comptable et financier</title>
          <link>https://jobs.inria.fr/public/classic/fr/offres/2026-002</link>
          <guid>inria-2026-002</guid>
          <description>Gestion des budgets de l'institut.</description>
          <pubDate>Fri, 11 Sep 2026 10:00:00 +0200</pubDate>
        </item>
      </channel>
    </rss>
    """

    cnrs_rss = """<?xml version="1.0" encoding="utf-8"?>
    <rss version="2.0">
      <channel>
        <title>Emploi CNRS</title>
        <item>
          <title>Stage de recherche PFE - Modélisation par réseaux de neurones profonds pour le climat</title>
          <link>https://emploi.cnrs.fr/Offres/2026-CNRS-01.aspx</link>
          <guid>cnrs-2026-01</guid>
          <description>Stage Master 2 / PFE Ingénieur en Intelligence Artificielle et modélisation physique.</description>
          <pubDate>Mon, 14 Sep 2026 14:00:00 +0200</pubDate>
        </item>
      </channel>
    </rss>
    """

    def mock_get(url: str, **kwargs: Any) -> _MockResponse:
        if "inria" in url:
            return _MockResponse("", status_code=200, text=inria_rss)
        if "cnrs" in url:
            return _MockResponse("", status_code=200, text=cnrs_rss)
        return _MockResponse("", status_code=404)

    scraper.client.get = MagicMock(side_effect=mock_get)  # type: ignore[assignment]

    result = scraper.run()
    titles = [j.title for j in result.jobs]
    assert any("Apprentissage par renforcement" in t for t in titles)
    assert any("Modélisation par réseaux de neurones" in t for t in titles)
    # L'offre de gestionnaire comptable doit être éliminée par le filtre métier
    assert not any("Gestionnaire comptable" in t for t in titles)

    for job in result.jobs:
        assert job.source == "academic"
        assert job.is_internship is True


# --------------------------------------------------------------------------- #
# Test Thread-Safety de KnownIndex
# --------------------------------------------------------------------------- #
def test_known_index_thread_safety() -> None:
    """Vérifie que InMemoryKnownIndex résiste aux accès concurrents en multithreading."""
    index = InMemoryKnownIndex()

    def worker(worker_id: int) -> None:
        for i in range(100):
            key = f"key_{worker_id}_{i}"
            url = f"https://example.com/{worker_id}/{i}"
            index.remember("test_source", key, url)
            assert index.is_known("test_source", key, url) is True
            entry = SeenEntry(
                source="ats",
                external_key=key,
                canonical_url=url,
                decision="VALIDATED",
            )
            index.record_seen(entry)
            index.mark_collected("test_source", key, url)
            dec = index.decision_for("ats", key, url)
            assert dec == "VALIDATED"

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(worker, w) for w in range(8)]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    assert index.stats()["remembered"] >= 800


# --------------------------------------------------------------------------- #
# Test Parallélisation dans ScraperManager
# --------------------------------------------------------------------------- #
class _DummyScraper(BaseScraper):
    source = "ats"

    def _iter_pages(self, query: str, mode: str, cursor: Any, plan: PassPlan) -> PageResult:
        time.sleep(0.05)  # simule latence réseau
        job = RawJob(
            id_externe=f"job_{self.source}",
            source=self.source,
            title=f"Stage Data Scientist {self.source}",
            company="Startup IA",
            location="Paris",
            url=f"https://startup.fr/{self.source}",
            description="Machine learning et deep learning avec PyTorch.",
        )
        return PageResult(
            entries=[CardEntry(key=job.id_externe, job=job)],
            next_cursor=None,
            exhausted=True,
        )


class _DummyScraper2(_DummyScraper):
    source = "academic"


def test_scraper_manager_parallel_execution(monkeypatch: Any) -> None:
    """Vérifie que ScraperManager exécute les sources en parallèle et préserve l'ordre."""
    monkeypatch.setitem(SCRAPER_REGISTRY, "ats", _DummyScraper)
    monkeypatch.setitem(SCRAPER_REGISTRY, "academic", _DummyScraper2)

    config = ScraperConfig(
        enabled_sources=["ats", "academic"],
        parallel_sources=True,
    )
    batches_received: list[list[RawJob]] = []

    def on_batch(jobs: list[RawJob]) -> None:
        batches_received.append(jobs)

    manager = ScraperManager(config)
    t0 = time.perf_counter()
    result = manager.run(on_batch_collected=on_batch)
    elapsed = time.perf_counter() - t0
    assert elapsed > 0
    assert len(result.jobs) == 2
    # L'ordre d'agrégation doit correspondre exactement à config.enabled_sources
    assert result.jobs[0].source == "ats"
    assert result.jobs[1].source == "academic"
    assert len(batches_received) == 2


# --------------------------------------------------------------------------- #
# Test Optimisation WTTJ Modes Fusionnés
# --------------------------------------------------------------------------- #
def test_wttj_modes_deduplication() -> None:
    """Vérifie que WTTJ avec freshness et relevance ne fait pas deux fois les requêtes."""
    scraper = WelcomeToTheJungleScraper(ScraperConfig())

    post_calls = 0

    def mock_post(url: str, **kwargs: Any) -> _MockResponse:
        nonlocal post_calls
        post_calls += 1
        return _MockResponse(
            {
                "results": [
                    {
                        "hits": [
                            {
                                "objectID": "wttj-1",
                                "name": "Stage Data Scientist",
                                "company": {"name": "Tech Co", "slug": "tech-co"},
                                "offices": [{"city": "Paris", "country": "France"}],
                                "contract_type": "internship",
                                "description": "Machine learning et IA.",
                                "slug": "stage-ds",
                                "published_at": "2026-09-10T10:00:00Z",
                            }
                        ]
                    }
                ]
            }
        )

    scraper.client.post = MagicMock(side_effect=mock_post)  # type: ignore[assignment]

    result = scraper.collect(modes=["freshness", "relevance"], queries=["Data Scientist"])

    # Vérifier que post_calls a été appelé (preload + iter_pages éventuel)
    # et que la passe "relevance" est marquée "unsupported" sans requête supplémentaire
    relevance_reports = [r for r in result.query_reports if r.mode == "relevance"]
    assert len(relevance_reports) == 1
    assert relevance_reports[0].stop_reason == "unsupported"
    assert "Index Algolia unique" in relevance_reports[0].stop_detail


# --------------------------------------------------------------------------- #
# Test Pré-filtrage gratuit avant fiche détail
# --------------------------------------------------------------------------- #
def test_early_rejection_before_detail_fetch() -> None:
    """Vérifie que les titres incompatibles ou entreprises exclues sont écartés sans appel réseau."""
    config = ScraperConfig(
        excluded_companies=["ESN Interdite"],
    )

    # 1. Vérification des fonctions pures de détection
    assert alternance_semantic_reason("Alternance Data Scientist") is not None
    assert alternance_semantic_reason("Stage Data Scientist") is None
    assert alternance_semantic_reason("Stagiaire PFE Data Scientist") is None

    assert _company_exclusion("ESN Interdite Paris", config) is not None
    assert _company_exclusion("Startup IA", config) is None

    assert title_exclusion("Stage Data Analyst Power BI", config) is not None
    assert title_exclusion("Contrat d'alternance ML", config) is not None


class _MockNoDetailScraper(BaseScraper):
    source = "linkedin"
    LIST_HAS_DESCRIPTION = False

    def __init__(self, config: ScraperConfig | None = None) -> None:
        super().__init__(config)
        self.detail_fetches = 0

    def _fetch_detail(self, job: RawJob) -> tuple[str, int]:
        self.detail_fetches += 1
        return "Description détaillée récupérée du réseau", 1

    def _iter_pages(self, query: str, mode: str, cursor: Any, plan: PassPlan) -> PageResult:
        cards = [
            # Titre rejeté directement (alternance)
            CardEntry(
                key="job1",
                job=RawJob(
                    id_externe="job1",
                    source=self.source,
                    title="Alternance Data Scientist",
                    company="Company A",
                    location="Paris",
                    url="https://linkedin.com/jobs/view/1",
                    description="",
                ),
            ),
            # Entreprise rejetée directement
            CardEntry(
                key="job2",
                job=RawJob(
                    id_externe="job2",
                    source=self.source,
                    title="Stage Data Scientist",
                    company="ESN Bannie",
                    location="Paris",
                    url="https://linkedin.com/jobs/view/2",
                    description="",
                ),
            ),
            # Titre senior rejeté directement
            CardEntry(
                key="job3",
                job=RawJob(
                    id_externe="job3",
                    source=self.source,
                    title="Senior Lead Data Scientist",
                    company="Company C",
                    location="Paris",
                    url="https://linkedin.com/jobs/view/3",
                    description="",
                ),
            ),
            # Titre stage valide -> doit déclencher _fetch_detail
            CardEntry(
                key="job4",
                job=RawJob(
                    id_externe="job4",
                    source=self.source,
                    title="Stage Data Scientist Machine Learning",
                    company="Startup ML",
                    location="Paris",
                    url="https://linkedin.com/jobs/view/4",
                    description="",
                ),
            ),
        ]
        return PageResult(entries=cards, next_cursor=None, exhausted=True)


def test_no_detail_scraper_prefiltering() -> None:
    """Vérifie que pour une source sans description en liste, seules les offres valides appellent _fetch_detail."""
    cfg = ScraperConfig(excluded_companies=["ESN Bannie"])
    scraper = _MockNoDetailScraper(cfg)
    result = scraper.run()

    # Seule la 4ème offre (Stage Data Scientist valide) a appelé _fetch_detail !
    assert scraper.detail_fetches == 1
    assert len(result.jobs) == 1
    assert result.jobs[0].id_externe == "job4"

    # Les rejets pré-détail doivent figurer dans seen avec les décisions appropriées
    seen_decisions = {s.external_key: s.decision for s in result.seen}
    assert seen_decisions["job1"] == "REJECTED_CONTRACT"
    assert seen_decisions["job2"] == "REJECTED_TITLE"
    assert seen_decisions["job3"] == "REJECTED_TITLE"
