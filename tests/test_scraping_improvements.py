"""Tests des améliorations de collecte : filtre métier v2, filtre en deux temps
(titre puis fiche détail), réévaluation des rejets et dédoublonnage inter-plateformes.

Aucun réseau : les fiches détail sont simulées, la base est un SQLite temporaire.

Exécution : ``python tests/test_scraping_improvements.py``
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scrapers.base import BaseScraper, describe_rejection, screen_rejection
from scrapers.known import InMemoryKnownIndex
from scrapers.models import (
    SEEN_REJECTED_BI,
    SEEN_VALIDATED,
    CardEntry,
    PageResult,
    PassConfig,
    RawJob,
    ScraperConfig,
)
from src.ingestion.bridge import find_new_raw_jobs, ingest_raw_jobs
from src.ingestion.known_index import DatabaseKnownIndex, entries_to_rows
from src.storage.database import Database


# --------------------------------------------------------------------------- #
# Filtre métier v2 : exclusions pondérées et signaux faibles
# --------------------------------------------------------------------------- #
def test_exclusion_dans_la_description_ponderee() -> None:
    """Un terme exclu isolé dans une vraie offre DS ne la rejette plus."""
    config = ScraperConfig()
    # Stack citant Tableau / Power BI, mais le corps parle surtout de ML.
    assert screen_rejection(
        "Stage Data Scientist",
        "Machine learning, deep learning et NLP avec PyTorch. Outils : Tableau.",
        config,
    ) == ""
    # « nos data analysts » : collaboration, pas le cœur du poste.
    assert screen_rejection(
        "Stage Data Scientist",
        "Vous travaillerez avec nos data analysts sur des modèles de machine learning.",
        config,
    ) == ""
    # Le vocabulaire BI domine : rejet, avec le motif « orientation BI ».
    reason = screen_rejection(
        "Stage Data Scientist", "Création de dashboards Qlik et Power BI.", config
    )
    assert reason.startswith("orientation BI"), reason
    # Exclusion dans le TITRE : toujours rédhibitoire.
    assert screen_rejection(
        "Stage Data Analyst", "Machine learning, deep learning, NLP.", config
    ).startswith("orientation BI")


def test_signaux_faibles() -> None:
    """Un mot générique seul dans la description ne suffit plus."""
    config = ScraperConfig()
    # Signal faible dans le titre : suffisant.
    assert screen_rejection("Stage IA générale", "", config) == ""
    # Un seul signal faible dans la description : insuffisant.
    assert screen_rejection(
        "Stage chargé de mission", "Définition du modèle économique de l'offre.", config
    ).startswith("aucun signal")
    # Deux signaux faibles distincts dans la description : suffisant.
    assert screen_rejection(
        "Stage chargé d'études", "Modélisation et optimisation de la chaîne logistique.", config
    ) == ""
    # Signal fort dans la description : suffisant, même avec un titre neutre.
    assert screen_rejection(
        "Stagiaire équipe Pricing", "Modèles de machine learning en production.", config
    ) == ""
    # La re-validation sur fiche complète applique les mêmes règles.
    assert describe_rejection(
        "Stage chargé de mission", "Recherche de partenaires commerciaux.", config
    ) == "aucun signal Data Science / ML dans la fiche"


def test_empreinte_du_filtre() -> None:
    """L'empreinte change avec les mots-clés et ignore l'ordre des listes."""
    base = ScraperConfig()
    same = ScraperConfig(exclusion_keywords=list(reversed(base.exclusion_keywords)))
    other = ScraperConfig(exclusion_keywords=[*base.exclusion_keywords, "sap"])
    assert base.filter_fingerprint() == same.filter_fingerprint()
    assert base.filter_fingerprint() != other.filter_fingerprint()
    assert len(base.filter_fingerprint()) == 12


# --------------------------------------------------------------------------- #
# Filtre en deux temps : titre, puis fiche détail
# --------------------------------------------------------------------------- #
class _NoDescriptionScraper(BaseScraper):
    """Source simulée dont la liste n'expose que les titres (comme LinkedIn)."""

    source = "linkedin"
    LIST_HAS_DESCRIPTION = False

    def __init__(
        self,
        config: ScraperConfig,
        titles: dict[str, str],
        details: dict[str, str | Exception],
    ) -> None:
        super().__init__(config)
        self.titles = titles
        self.details = details
        self.detail_requests: list[str] = []

    def _iter_pages(self, query: str, mode: str, cursor: Any, plan: Any) -> PageResult:
        if cursor:
            return PageResult(entries=[], exhausted=True)
        entries = [
            CardEntry(
                key=key,
                job=RawJob(
                    id_externe=key,
                    source="linkedin",
                    title=title,
                    company="Acme",
                    location="Paris",
                    url=f"https://example.com/jobs/{key}",
                    description="",
                ),
            )
            for key, title in self.titles.items()
        ]
        return PageResult(entries=entries, next_cursor=1)

    def _fetch_detail(self, job: RawJob) -> tuple[str, int]:
        self.detail_requests.append(job.id_externe)
        detail = self.details.get(job.id_externe, "")
        if isinstance(detail, Exception):
            raise detail
        return detail, 1


def _single_pass_config(**overrides: Any) -> ScraperConfig:
    return ScraperConfig(
        target_queries=["q1"],
        max_offers_per_source=100,
        enabled_sources=["linkedin"],
        passes=PassConfig.only(
            "relevance", max_offers_per_query=50, target_new_per_source=None
        ),
        **overrides,
    )


def _http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.com")
    return httpx.HTTPStatusError(
        "erreur simulée", request=request, response=httpx.Response(status, request=request)
    )


def test_filtre_en_deux_temps() -> None:
    """Le titre seul n'écarte plus une offre ML ; la description est conservée."""
    config = _single_pass_config()
    scraper = _NoDescriptionScraper(
        config,
        titles={
            "pricing": "Stagiaire équipe Pricing",           # ML révélé par la fiche
            "bi": "Stage Data Analyst Power BI",             # exclu dès le titre
            "rh": "Stage chargé de mission",                 # fiche hors sujet
        },
        details={
            "pricing": "Modèles de machine learning et deep learning en production.",
            "rh": "Gestion administrative et relation client.",
        },
    )
    result = scraper.run(InMemoryKnownIndex())
    scraper.close()

    kept = {job.id_externe: job for job in result.jobs}
    assert set(kept) == {"pricing"}, kept
    assert "machine learning" in kept["pricing"].description
    # Aucune fiche détail pour une exclusion de titre.
    assert scraper.detail_requests == ["pricing", "rh"], scraper.detail_requests
    decisions = {entry.external_key: entry for entry in result.seen}
    assert decisions["pricing"].decision == SEEN_VALIDATED
    assert decisions["rh"].decision == SEEN_REJECTED_BI
    # Rejets définitifs : empreinte du filtre archivée.
    assert decisions["rh"].filter_version == config.filter_fingerprint()
    assert decisions["bi"].filter_version == config.filter_fingerprint()
    # Les fiches détail sont comptées dans la télémétrie HTTP (2 pages + 2 fiches).
    assert result.query_reports[0].http_requests == 2 + 2


def test_filtre_en_deux_temps_fiche_illisible() -> None:
    """Fiche en échec : repli sur le titre, rejet non définitif, 429 suspend l'enrichissement."""
    config = _single_pass_config()
    scraper = _NoDescriptionScraper(
        config,
        titles={
            "a": "Stagiaire équipe Pricing",
            "b": "Stage Data Scientist",
            "c": "Stagiaire contrôle de gestion",
        },
        details={"a": _http_error(429)},
    )
    result = scraper.run(InMemoryKnownIndex())
    scraper.close()

    # Après le 429, plus aucune fiche n'est demandée.
    assert scraper.detail_requests == ["a"], scraper.detail_requests
    # « Data Scientist » passe sur son titre ; les autres sont rejetées sur le titre…
    assert [job.id_externe for job in result.jobs] == ["b"], result.jobs
    decisions = {entry.external_key: entry for entry in result.seen}
    # …sans empreinte : elles seront réévaluées au prochain run.
    assert decisions["a"].filter_version is None
    assert decisions["c"].filter_version is None


def test_enrichissement_desactivable() -> None:
    """``enrich_descriptions: false`` rétablit le filtrage sur le titre seul."""
    config = _single_pass_config(enrich_descriptions=False)
    scraper = _NoDescriptionScraper(
        config,
        titles={"a": "Stagiaire équipe Pricing"},
        details={"a": "Machine learning."},
    )
    result = scraper.run(InMemoryKnownIndex())
    scraper.close()
    assert scraper.detail_requests == []
    assert result.jobs == []


# --------------------------------------------------------------------------- #
# Réévaluation automatique des rejets (seen_jobs.filter_version)
# --------------------------------------------------------------------------- #
def test_reevaluation_des_rejets() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "memoire.db")
        try:
            current = "cafe00000001"
            db.upsert_seen_jobs(
                [
                    {"source": "linkedin", "external_key": "ok", "canonical_url": "x.com/ok",
                     "decision": "VALIDATED", "job_id": "j1"},
                    {"source": "linkedin", "external_key": "rej-now", "canonical_url": "x.com/1",
                     "decision": "REJECTED_BI", "filter_version": current},
                    {"source": "linkedin", "external_key": "rej-old", "canonical_url": "x.com/2",
                     "decision": "REJECTED_BI", "filter_version": "0ld000000000"},
                    {"source": "linkedin", "external_key": "rej-none", "canonical_url": "x.com/3",
                     "decision": "REJECTED_BI"},
                    {"source": "linkedin", "external_key": "legacy", "canonical_url": "x.com/4",
                     "decision": "KNOWN"},
                    {"source": "linkedin", "external_key": "window", "canonical_url": "x.com/5",
                     "decision": "OUT_OF_WINDOW"},
                ]
            )
            pairs, urls = db.load_seen_index(["linkedin"], filter_version=current)
            keys = {key for _, key in pairs}
            assert keys == {"ok", "rej-now", "window"}, keys
            assert "x.com/2" not in urls and "x.com/4" not in urls, urls

            # Sans empreinte : comportement historique (tout est connu).
            all_pairs, _ = db.load_seen_index(["linkedin"])
            assert len(all_pairs) == 6

            index = DatabaseKnownIndex(db, ["linkedin"], filter_version=current)
            assert index.is_known("linkedin", "rej-now")
            assert not index.is_known("linkedin", "rej-old", "https://x.com/2")

            # Une ré-observation KNOWN ne doit pas effacer la décision d'origine.
            db.upsert_seen_jobs(
                [{"source": "linkedin", "external_key": "rej-now", "canonical_url": "x.com/1",
                  "decision": "KNOWN"}]
            )
            pairs, _ = db.load_seen_index(["linkedin"], filter_version=current)
            assert ("linkedin", "rej-now") in pairs
            assert db.count_seen_jobs("REJECTED_BI") == 3
        finally:
            db.engine.dispose()


def test_entries_to_rows_transporte_empreinte() -> None:
    from scrapers.models import SeenEntry

    rows = entries_to_rows(
        [SeenEntry(source="linkedin", external_key="k", canonical_url="x.com/k",
                   decision="REJECTED_BI", filter_version="abc")]
    )
    assert rows[0]["filter_version"] == "abc"


# --------------------------------------------------------------------------- #
# Dédoublonnage inter-plateformes à l'ingestion
# --------------------------------------------------------------------------- #
def _job(source: str, key: str, title: str, company: str, description: str = "") -> RawJob:
    return RawJob(
        id_externe=key,
        source=source,  # type: ignore[arg-type]
        title=title,
        company=company,
        location="Paris",
        url=f"https://{source}.example.com/jobs/{key}",
        description=description,
    )


def test_dedoublonnage_inter_plateformes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "dedup.db")
        try:
            # Offre LinkedIn déjà en base, sans description.
            stats = ingest_raw_jobs([_job("linkedin", "1", "Stage Data Scientist (H/F)", "Doctolib SAS")], db)
            assert stats["new_inserted"] == 1
            (existing,) = db.get_jobs()

            # La même annonce arrive via WTTJ avec sa description : doublon, mais la
            # description est reportée sur la fiche existante.
            stats = ingest_raw_jobs(
                [_job("wttj", "w1", "Data Scientist - Stage", "Doctolib", "PyTorch et NLP.")], db
            )
            assert stats["new_inserted"] == 0 and stats["duplicates_skipped"] == 1, stats
            assert db.get_job(existing["id"])["description"] == "PyTorch et NLP."

            # Même plateforme, identifiant différent : deux offres distinctes.
            stats = ingest_raw_jobs(
                [_job("linkedin", "2", "Stage Data Scientist", "Doctolib")], db
            )
            assert stats["new_inserted"] == 1, stats

            # Titre réellement différent : pas un doublon.
            stats = ingest_raw_jobs(
                [_job("jobteaser", "t1", "Stage Computer Vision", "Doctolib")], db
            )
            assert stats["new_inserted"] == 1, stats

            # Dans un même lot : la version la plus documentée l'emporte.
            batch = [
                _job("linkedin", "3", "Stage NLP Engineer", "Alan", ""),
                _job("wttj", "w3", "NLP Engineer (stage)", "Alan", "Transformers et LLM."),
            ]
            new = find_new_raw_jobs(batch, db)
            assert [job.source for job in new] == ["wttj"], new
            stats = ingest_raw_jobs(batch, db)
            assert stats["new_inserted"] == 1 and stats["duplicates_skipped"] == 1, stats
        finally:
            db.engine.dispose()


if __name__ == "__main__":
    for name, func in list(globals().items()):
        if name.startswith("test_") and callable(func):
            func()
            print(f"[OK] {name}")
