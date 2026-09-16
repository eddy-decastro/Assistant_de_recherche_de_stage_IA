"""Tests du moteur de collecte hybride (arrêt anticipé, passes, motifs d'arrêt).

Aucun réseau, aucune base : la source est simulée par un flux de pages
scriptées, ce qui permet de vérifier **exactement** les invariants de la
stratégie Date + Pertinence (arrêt anticipé, déduplication transverse, fenêtre
temporelle, motifs d'arrêt, mémoire de collecte).

Exécution : ``python tests/test_hybrid_collection.py``
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scrapers.base import BaseScraper
from scrapers.known import InMemoryKnownIndex, NullKnownIndex
from scrapers.models import (
    SEEN_KNOWN,
    SEEN_OUT_OF_WINDOW,
    SEEN_REJECTED_BI,
    SEEN_VALIDATED,
    CardEntry,
    PageResult,
    PassConfig,
    RawJob,
    ScraperConfig,
    canonical_url,
)

NOW = datetime.now(timezone.utc)


def _job(key: str, published: datetime | None = None) -> RawJob:
    """Fabrique une offre valide (sauf clés spéciales utilisées par les tests)."""
    if key.startswith("bi"):
        title = "Stage Data Analyst (Power BI)"  # écartée par le filtre anti-BI
    elif key.startswith("contract"):
        title = "Stage Data Scientist"  # contrat incompatible -> is_internship=False
    else:
        title = f"Stage Data Scientist {key}"
    return RawJob(
        id_externe=key,
        source="linkedin",
        title=title,
        company="Mistral AI",
        location="Paris",
        url=f"https://example.com/jobs/{key}",
        description="PyTorch, LLM, MLOps",
        published_at=published,
        is_internship=not key.startswith("contract"),
    )


class _ScriptedScraper(BaseScraper):
    """Source simulée : ``pages`` est une liste de pages, chaque page une liste de clés.

    ``dates`` associe une clé à sa date de publication (facultatif).
    """

    source = "linkedin"
    DATE_ORDER_RELIABLE = True
    SERVER_WINDOW_FILTER = True

    def __init__(
        self,
        config: ScraperConfig,
        pages: list[list[str]],
        dates: dict[str, datetime] | None = None,
        *,
        error: Exception | None = None,
        unavailable: str = "",
        pages_by_mode: dict[str, list[list[str]]] | None = None,
    ) -> None:
        super().__init__(config)
        self.pages = pages
        #: Flux distincts par mode (les deux passes d'une même requête ne
        #: renvoient pas le même classement en conditions réelles).
        self.pages_by_mode = pages_by_mode or {}
        self.dates = dates or {}
        self.error = error
        self._unavailable = unavailable
        self.calls: list[tuple[str, str, Any]] = []

    def unavailable_reason(self) -> str:
        return self._unavailable

    def _iter_pages(self, query: str, mode: str, cursor: Any, plan: Any) -> PageResult:
        self.calls.append((query, mode, cursor))
        if self.error is not None:
            raise self.error
        pages = self.pages_by_mode.get(mode, self.pages)
        index = int(cursor or 0)
        if index >= len(pages):
            return PageResult(entries=[], next_cursor=None, exhausted=True)
        entries = [
            CardEntry(key=key, job=_job(key, self.dates.get(key))) for key in pages[index]
        ]
        return PageResult(entries=entries, next_cursor=index + 1, exhausted=False)


def _config(mode: str, **overrides: Any) -> ScraperConfig:
    """Configuration mono-passe, quota généreux par défaut."""
    defaults: dict[str, Any] = {"max_offers_per_query": 50, "max_pages_per_query": 10}
    defaults.update(overrides)
    return ScraperConfig(
        target_queries=["q1"],
        max_offers_per_source=200,
        enabled_sources=["linkedin"],
        passes=PassConfig.only(mode, **defaults),
    )


def test_arret_anticipe_apres_n_consecutives() -> None:
    """La passe Fraîcheur s'arrête dès 2 offres consécutives déjà connues."""
    config = _config("freshness", early_stop_after_known=2, window_days=None)
    known = InMemoryKnownIndex(pairs={("linkedin", "k1"), ("linkedin", "k2")})
    scraper = _ScriptedScraper(
        config,
        pages=[["k1", "k2", "new1"], ["new2", "new3"], ["new4"]],
    )
    result = scraper.run(known)
    report = result.query_reports[0]

    assert report.stop_reason == "early_stop", report.stop_reason
    assert report.pages_fetched == 1, report.pages_fetched  # la page 1 suffit
    assert report.jobs_known == 2, report.jobs_known
    assert report.cards_seen == 2, report.cards_seen  # arrêt avant la 3e carte
    assert report.jobs_kept == 0, report.jobs_kept
    # Aucun appel à la page 2 : c'est le gain principal de l'arrêt anticipé.
    assert [call[2] for call in scraper.calls] == [None], scraper.calls
    scraper.close()
    print("  arrêt anticipé : jonction détectée après 2 connues consécutives OK")


def test_serie_de_connues_interrompue() -> None:
    """Une offre inconnue réinitialise le compteur : pas d'arrêt prématuré."""
    config = _config("freshness", early_stop_after_known=3, window_days=None)
    known = InMemoryKnownIndex(pairs={("linkedin", "k1"), ("linkedin", "k2")})
    scraper = _ScriptedScraper(
        config,
        pages=[["k1", "k2", "new1"], ["k3", "k4", "k5"]],  # 2 connues d'affilée au max
    )
    result = scraper.run(known)
    report = result.query_reports[0]

    assert report.stop_reason == "stream_end", report.stop_reason
    # 2 connues d'affilée au maximum (< seuil 3) : le flux est parcouru en entier.
    assert report.jobs_kept == 4, report.jobs_kept
    assert report.jobs_known == 2, report.jobs_known
    assert report.pages_fetched == 2, report.pages_fetched
    scraper.close()
    print("  série de connues interrompue : pas d'arrêt prématuré OK")


def test_passe_pertinence_sans_arret_anticipe() -> None:
    """Le mode Rattrapage ignore l'arrêt anticipé et va jusqu'au quota."""
    config = _config("relevance", max_offers_per_query=3, window_days=None)
    known = InMemoryKnownIndex(pairs={("linkedin", f"k{i}") for i in range(10)})
    scraper = _ScriptedScraper(
        config,
        pages=[["k1", "k2", "new1", "new2"], ["k3", "k4", "new3", "k5"]],
    )
    result = scraper.run(known)
    report = result.query_reports[0]

    assert report.stop_reason == "quota", report.stop_reason
    assert report.jobs_kept == 3, report.jobs_kept
    assert report.jobs_known == 4, report.jobs_known
    assert report.pages_fetched == 2, report.pages_fetched
    scraper.close()
    print("  passe pertinence : aucun arrêt anticipé, quota atteint OK")


def test_deduplication_transverse_entre_passes() -> None:
    """Une offre retenue en Fraîcheur n'est ni recomptée ni reprise en Pertinence."""
    config = ScraperConfig(
        target_queries=["q1"],
        max_offers_per_source=200,
        enabled_sources=["linkedin"],
        passes=PassConfig.only("freshness", max_offers_per_query=5, window_days=None),
    )
    config.passes["relevance"] = PassConfig.only("relevance")["relevance"].model_copy(
        update={"max_offers_per_query": 5}
    )
    scraper = _ScriptedScraper(
        config,
        pages=[],
        pages_by_mode={
            "freshness": [["a1", "a2"]],
            "relevance": [["a1", "b1", "a2", "b2"]],
        },
    )
    result = scraper.run(NullKnownIndex())
    freshness, relevance = result.query_reports

    assert freshness.stop_reason == "stream_end", freshness.stop_reason
    assert freshness.jobs_kept == 2, freshness.jobs_kept
    # En Pertinence, les 2 offres déjà retenues sont des doublons (dédup transverse).
    assert relevance.jobs_duplicate == 2, relevance.jobs_duplicate
    assert relevance.jobs_kept == 2, relevance.jobs_kept
    assert [job.id_externe for job in result.jobs] == ["a1", "a2", "b1", "b2"], result.jobs
    scraper.close()
    print("  déduplication transverse : aucune offre comptée deux fois OK")


def test_fenetre_temporelle_et_arret() -> None:
    """Flux trié : la première offre hors fenêtre arrête la passe (window_end)."""
    config = _config(
        "freshness", window_days=7, early_stop_after_known=0, stop_when_older_than_window=True
    )
    dates = {
        "recent1": NOW - timedelta(days=1),
        "recent2": NOW - timedelta(days=3),
        "vieux": NOW - timedelta(days=30),
        "tres_vieux": NOW - timedelta(days=60),
    }
    scraper = _ScriptedScraper(
        config, pages=[["recent1", "recent2", "vieux", "tres_vieux"]], dates=dates
    )
    result = scraper.run(NullKnownIndex())
    report = result.query_reports[0]

    assert report.stop_reason == "window_end", report.stop_reason
    assert report.jobs_kept == 2, report.jobs_kept
    assert report.jobs_out_of_window == 1, report.jobs_out_of_window
    assert report.newest_published_at is not None and report.oldest_published_at is not None
    scraper.close()
    print("  fenêtre temporelle : arrêt sur offre trop ancienne (window_end) OK")


def test_fenetre_sans_ordre_fiable() -> None:
    """Ordre non fiable : les offres anciennes sont écartées SANS arrêter la passe."""
    config = _config(
        "freshness", window_days=7, early_stop_after_known=0, stop_when_older_than_window=True
    )
    dates = {
        "recent1": NOW - timedelta(days=1),
        "vieux": NOW - timedelta(days=30),
        "recent2": NOW - timedelta(days=2),
    }
    scraper = _ScriptedScraper(config, pages=[["vieux", "recent1", "recent2"]], dates=dates)
    scraper.DATE_ORDER_RELIABLE = False  # ordre mesuré non chronologique (LinkedIn)
    result = scraper.run(NullKnownIndex())
    report = result.query_reports[0]

    assert report.stop_reason == "stream_end", report.stop_reason
    assert report.jobs_out_of_window == 1, report.jobs_out_of_window
    assert report.jobs_kept == 2, report.jobs_kept
    assert "arrêt sur fenêtre désactivé" in report.stop_detail, report.stop_detail
    scraper.close()
    print("  fenêtre sans ordre fiable : offre écartée, flux poursuivi OK")


def test_arret_anticipe_desactive_si_ordre_non_fiable() -> None:
    """Un seuil demandé est neutralisé (et expliqué) si l'ordre du flux n'est pas fiable."""
    config = _config("freshness", early_stop_after_known=2, window_days=7)
    known = InMemoryKnownIndex(pairs={("linkedin", f"k{i}") for i in range(6)})
    scraper = _ScriptedScraper(
        config, pages=[["k1", "k2", "new1"], ["k3", "k4", "new2"]]
    )
    scraper.DATE_ORDER_RELIABLE = False
    result = scraper.run(known)
    report = result.query_reports[0]

    assert report.stop_reason == "stream_end", report.stop_reason
    # Le seuil de 2 connues consécutives est atteint deux fois, sans effet :
    # l'ordre du flux n'est pas jugé fiable, l'arrêt anticipé est neutralisé.
    assert report.pages_fetched == 2, report.pages_fetched
    assert report.jobs_kept == 2, report.jobs_kept
    assert "arrêt anticipé désactivé" in report.stop_detail, report.stop_detail
    scraper.close()
    print("  ordre non fiable : arrêt anticipé neutralisé et documenté OK")


def _http_error(status: int) -> httpx.HTTPStatusError:
    """Erreur HTTP prête à être levée par une source simulée."""
    request = httpx.Request("GET", "https://example.com/jobs")
    return httpx.HTTPStatusError(
        f"HTTP {status}", request=request, response=httpx.Response(status, request=request)
    )


def test_rate_limit_et_erreurs_traces() -> None:
    """Un 429 (et une erreur réseau) est tracé comme perte de flux, sans exception."""
    config = _config("freshness", window_days=None)
    for error, expected in (
        (_http_error(429), "rate_limit"),
        (_http_error(500), "http_error"),
        (httpx.ConnectError("DNS"), "network_error"),
    ):
        scraper = _ScriptedScraper(config, pages=[], error=error)
        result = scraper.run(NullKnownIndex())
        report = result.query_reports[0]
        assert report.stop_reason == expected, (expected, report.stop_reason)
        assert report.error, "Le détail de l'erreur doit être conservé."
        scraper.close()
    print("  rate limit / erreurs HTTP / réseau : motifs d'arrêt tracés OK")


def test_source_indisponible_et_passe_desactivee() -> None:
    """Cookies absents (JobTeaser) ou passe désactivée : télémétrie explicite."""
    config = _config("freshness", window_days=None)
    scraper = _ScriptedScraper(config, pages=[["a1"]], unavailable="cookies absents")
    result = scraper.run(NullKnownIndex())
    assert result.jobs == [], result.jobs
    assert result.query_reports[0].stop_reason == "auth_missing", result.query_reports
    scraper.close()

    inactive = _config("freshness", window_days=None)
    inactive.passes["freshness"] = inactive.passes["freshness"].model_copy(
        update={"enabled": False}
    )
    scraper = _ScriptedScraper(inactive, pages=[["a1"]])
    # Passe explicitement demandée mais désactivée en configuration : on consigne
    # l'arrêt plutôt que de renvoyer un résultat vide inexpliqué.
    result = scraper.run(NullKnownIndex(), modes=["freshness"])
    assert result.jobs == [], result.jobs
    assert result.query_reports[0].stop_reason == "disabled", result.query_reports
    scraper.close()
    print("  source indisponible / passe désactivée : arrêts consignés OK")


def test_decisions_memoire_de_collecte() -> None:
    """Chaque carte croisée produit une décision (retenue, BI, hors fenêtre, connue)."""
    config = _config(
        "freshness",
        window_days=7,
        early_stop_after_known=0,
        stop_when_older_than_window=False,  # les offres anciennes sont écartées, pas d'arrêt
    )
    dates = {"vieux": NOW - timedelta(days=30), "k1": NOW - timedelta(days=1)}
    known = InMemoryKnownIndex(pairs={("linkedin", "k1")})
    scraper = _ScriptedScraper(
        config,
        pages=[["new1", "bi1", "vieux", "k1", "contract1"]],
        dates=dates,
    )
    result = scraper.run(known)
    decisions = {(entry.external_key): entry for entry in result.seen}

    assert decisions["new1"].decision == SEEN_VALIDATED, decisions["new1"]
    assert decisions["bi1"].decision == SEEN_REJECTED_BI, decisions["bi1"]
    assert "power bi" in (decisions["bi1"].rejection_reason or "").casefold(), decisions["bi1"]
    assert decisions["vieux"].decision == SEEN_OUT_OF_WINDOW, decisions["vieux"]
    assert decisions["k1"].decision == SEEN_KNOWN, decisions["k1"]
    assert decisions["contract1"].canonical_url.endswith("/contract1")
    assert result.rejected_bi == 2, result.rejected_bi  # bi1 + contract1
    scraper.close()
    print("  mémoire de collecte : décisions par carte (dont rejets) OK")


def test_index_memoire_et_url_canonique() -> None:
    """L'index répond par identifiant plateforme ET par URL canonique."""
    index = InMemoryKnownIndex(pairs={("linkedin", "4400")}, urls={"linkedin.com/jobs/view/99"})
    assert index.is_known("linkedin", "4400") is True
    # L'URL est passée comme URL (3e argument) : elle est canonisée par l'index.
    assert index.is_known("linkedin", "", "http://www.linkedin.com/jobs/view/99?ref=x") is True
    assert index.is_known("linkedin", "4411") is False
    index.remember("jobteaser", "uuid-1", "https://emse.jobteaser.com/fr/job-offers/uuid-1")
    assert index.is_known("jobteaser", "", "https://emse.jobteaser.com/fr/job-offers/uuid-1") is True
    stats = index.stats()
    assert stats["hits"] == 3 and stats["remembered"] == 1, stats
    assert canonical_url("https://WWW.Example.com/Job/?q=1#x") == "example.com/Job"
    print("  index de collecte : identifiant plateforme + URL canonique OK")


def main() -> None:
    """Exécute tous les cas (aucun accès réseau)."""
    test_arret_anticipe_apres_n_consecutives()
    test_serie_de_connues_interrompue()
    test_passe_pertinence_sans_arret_anticipe()
    test_deduplication_transverse_entre_passes()
    test_fenetre_temporelle_et_arret()
    test_fenetre_sans_ordre_fiable()
    test_arret_anticipe_desactive_si_ordre_non_fiable()
    test_rate_limit_et_erreurs_traces()
    test_source_indisponible_et_passe_desactivee()
    test_decisions_memoire_de_collecte()
    test_index_memoire_et_url_canonique()
    print("[OK] test_hybrid_collection.py : tous les tests passent.")


if __name__ == "__main__":
    main()
