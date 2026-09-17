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
    """La passe Fraîcheur s'arrête dès 2 offres consécutives déjà connues.

    ``early_stop_min_pages=1`` isole ici le compteur de la garde de pagination
    (celle-ci est testée séparément : elle interdit de conclure sur la seule
    première page).
    """
    config = _config(
        "freshness", early_stop_after_known=2, early_stop_min_pages=1, window_days=None
    )
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
    # La télémétrie dit d'où vient la série : ici, la mémoire de collecte.
    assert "en mémoire de collecte" in report.stop_detail, report.stop_detail
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
    """Sans armement explicite, le seuil est neutralisé (et expliqué) sur un ordre non fiable."""
    config = _config(
        "freshness",
        early_stop_after_known=2,
        early_stop_min_pages=1,
        arm_early_stop=False,
        window_days=7,
    )
    known = InMemoryKnownIndex(pairs={("linkedin", f"k{i}") for i in range(6)})
    scraper = _ScriptedScraper(
        config, pages=[["k1", "k2", "new1"], ["k3", "k4", "new2"]]
    )
    scraper.DATE_ORDER_RELIABLE = False
    result = scraper.run(known)
    report = result.query_reports[0]

    assert report.stop_reason == "stream_end", report.stop_reason
    # Le seuil de 2 connues consécutives est atteint deux fois, sans effet :
    # l'ordre du flux n'est pas jugé fiable et l'arrêt n'a pas été armé.
    assert report.pages_fetched == 2, report.pages_fetched
    assert report.jobs_kept == 2, report.jobs_kept
    assert "arrêt anticipé désactivé" in report.stop_detail, report.stop_detail
    scraper.close()
    print("  ordre non fiable : arrêt anticipé neutralisé et documenté OK")


def test_arret_anticipe_arme_malgre_un_ordre_non_chronologique() -> None:
    """Armé explicitement, l'arrêt sur série de connues joue même sans tri chronologique.

    C'est la règle « 10 offres déjà vues d'affilée ⇒ on a déjà tout vu » : elle se
    prononce sur une **série** (insensible aux inversions locales de date de LinkedIn),
    traverse les pages, et reste locale à la requête en cours.
    """
    config = _config(
        "freshness",
        early_stop_after_known=10,
        early_stop_min_pages=2,
        arm_early_stop=True,
        window_days=None,
        max_pages_per_query=10,
    )
    known = InMemoryKnownIndex(pairs={("linkedin", f"k{i:02d}") for i in range(12)})
    scraper = _ScriptedScraper(
        config,
        pages=[
            [f"k{i:02d}" for i in range(8)] + ["bi1"],   # 8 connues + 1 rejet (série rompue)
            ["k08", "k09", "new1"],                       # 2 connues puis 1 inédite
            ["new2"],                                     # flux épuisé ensuite
        ],
    )
    scraper.DATE_ORDER_RELIABLE = False  # LinkedIn : ordre mesuré non chronologique
    result = scraper.run(known)
    report = result.query_reports[0]

    # Séries maximales : 8 (page 1), 2 (page 2) = jamais 10 d'affilée -> pas d'arrêt.
    assert report.stop_reason == "stream_end", report.stop_reason
    assert report.pages_fetched == 3, report.pages_fetched
    assert report.jobs_known == 10, report.jobs_known

    # Deuxième essai : 10 connues d'affilée à cheval sur deux pages -> arrêt.
    # Mémoire propre : sinon les offres « inédites » du premier essai seraient déjà
    # connues ici, et le diagnostic « page stagnante » masquerait l'arrêt anticipé.
    known2 = InMemoryKnownIndex(pairs={("linkedin", f"k{i:02d}") for i in range(12)})
    scraper2 = _ScriptedScraper(
        config,
        pages=[
            ["new1"] + [f"k{i:02d}" for i in range(9)],  # 1 inédite puis 9 connues
            [f"k{i:02d}" for i in range(9, 12)],          # 1re carte -> série = 10
            ["jamais_atteint"],
        ],
    )
    scraper2.DATE_ORDER_RELIABLE = False
    result2 = scraper2.run(known2)
    report2 = result2.query_reports[0]

    assert report2.stop_reason == "early_stop", report2.stop_reason
    assert report2.pages_fetched == 2, report2.pages_fetched  # ni page 3 ni au-delà
    assert report2.jobs_kept == 1, report2.jobs_kept  # seule new1 est retenue
    assert [call[2] for call in scraper2.calls] == [None, 1], scraper2.calls
    # Le choix assumé (armement malgré un ordre non chronologique) est tracé.
    assert "armé explicitement" in report2.stop_detail, report2.stop_detail
    assert "10" in report2.stop_detail, report2.stop_detail
    scraper.close()
    scraper2.close()
    print("  arrêt anticipé armé : série de connues détectée malgré un ordre non fiable OK")


def test_garde_de_pagination_de_l_arret_anticipe() -> None:
    """La garde de pagination interdit de conclure sur la seule première page.

    Sans elle, « 10 offres déjà vues » sur une page de 10 cartes reviendrait au
    diagnostic « page stagnante » ; avec elle, la passe observe au moins deux pages
    puis s'arrête dès que la série est atteinte — sans attendre la fin du flux.
    """
    config = _config(
        "freshness",
        early_stop_after_known=2,
        early_stop_min_pages=2,
        arm_early_stop=True,
        window_days=None,
    )
    known = InMemoryKnownIndex(pairs={("linkedin", f"k{i}") for i in range(1, 5)})
    scraper = _ScriptedScraper(
        config, pages=[["k1", "k2", "new1"], ["k3", "k4", "new2"], ["new3"]]
    )
    result = scraper.run(known)
    report = result.query_reports[0]

    assert report.stop_reason == "early_stop", report.stop_reason
    assert report.pages_fetched == 2, report.pages_fetched
    assert [call[2] for call in scraper.calls] == [None, 1], scraper.calls
    assert report.jobs_kept == 1, report.jobs_kept      # new1 (page 1)
    assert report.jobs_known == 4, report.jobs_known    # k1 → k4
    assert [job.id_externe for job in result.jobs] == ["new1"], result.jobs
    scraper.close()
    print("  arrêt anticipé : garde de pagination respectée, arrêt dès la page 2 OK")


def test_objectif_de_source_arrete_les_requetes_restantes() -> None:
    """« Les N dernières » : objectif de source atteint, requêtes restantes non lancées.

    L'intention est un objectif **par source**, pas par requête : une fois le nombre de
    nouvelles offres atteint, la passe s'arrête. Chaque requête non lancée est
    consignée en télémétrie — une collecte amputée ne doit jamais être indiscernable
    d'un vivier épuisé.
    """
    config = ScraperConfig(
        target_queries=["q1", "q2"],
        max_offers_per_source=50,
        enabled_sources=["linkedin"],
        passes=PassConfig.only(
            "freshness",
            target_new_per_source=2,
            max_offers_per_query=10,
            max_pages_per_query=5,
            early_stop_after_known=0,
            window_days=None,
        ),
    )
    scraper = _ScriptedScraper(config, pages=[["n1", "n2", "n3", "n4"]])
    result = scraper.run(NullKnownIndex())
    first, second = result.query_reports

    assert [call[0] for call in scraper.calls] == ["q1"], scraper.calls
    assert first.target_new == 2 and first.jobs_kept == 2, first
    assert first.stop_reason == "quota", first.stop_reason
    assert "objectif de la passe atteint" in first.stop_detail, first.stop_detail
    assert second.jobs_kept == 0, second
    assert second.stop_reason == "quota", second.stop_reason
    assert "requête non lancée" in second.stop_detail, second.stop_detail
    assert second.target_new == 2, second
    assert [job.id_externe for job in result.jobs] == ["n1", "n2"], result.jobs
    scraper.close()
    print("  objectif de source : requêtes restantes non lancées mais consignées OK")


def test_reserve_de_budget_protege_la_passe_de_rattrapage() -> None:
    """La passe Fraîcheur ne peut pas consommer la part réservée au rattrapage.

    Sans réserve, une Fraîcheur généreuse atteindrait le plafond de source et la passe
    Pertinence — « les 10 plus pertinentes », qui rattrape les offres anciennes encore
    actives — serait lancée avec un budget nul. Ici le plafond est volontairement serré
    (12) pour vérifier que les deux objectifs cohabitent : 2 en Fraîcheur, 10 en
    Pertinence.
    """
    config = ScraperConfig(
        target_queries=["q1"],
        max_offers_per_source=12,
        enabled_sources=["linkedin"],
        passes={
            "freshness": PassConfig.only(
                "freshness",
                target_new_per_source=40,
                early_stop_after_known=0,
                window_days=None,
            )["freshness"],
            "relevance": PassConfig.only(
                "relevance",
                target_new_per_source=10,
                max_offers_per_query=20,
                max_pages_per_query=5,
            )["relevance"],
        },
    )
    scraper = _ScriptedScraper(
        config,
        pages=[],
        pages_by_mode={
            "freshness": [["f1", "f2", "f3", "f4", "f5"]],
            "relevance": [[f"r{i}" for i in range(1, 13)]],
        },
    )
    result = scraper.run(NullKnownIndex())
    freshness, relevance = result.query_reports

    # Fraîcheur : bridée par le plafond de source, réserve du rattrapage déduite.
    assert freshness.jobs_kept == 2, freshness
    assert "plafond de la source atteint" in freshness.stop_detail, freshness.stop_detail
    # Pertinence : budget intact, objectif de 10 atteint.
    assert relevance.jobs_kept == 10, relevance
    assert relevance.target_new == 10, relevance
    assert relevance.stop_reason == "quota", relevance.stop_reason
    assert "objectif de la passe atteint" in relevance.stop_detail, relevance.stop_detail
    assert len(result.jobs) == 12, result.jobs
    scraper.close()
    print("  réserve de budget : les deux objectifs de source cohabitent OK")


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
    test_arret_anticipe_arme_malgre_un_ordre_non_chronologique()
    test_garde_de_pagination_de_l_arret_anticipe()
    test_objectif_de_source_arrete_les_requetes_restantes()
    test_reserve_de_budget_protege_la_passe_de_rattrapage()
    test_rate_limit_et_erreurs_traces()
    test_source_indisponible_et_passe_desactivee()
    test_decisions_memoire_de_collecte()
    test_index_memoire_et_url_canonique()
    print("[OK] test_hybrid_collection.py : tous les tests passent.")


if __name__ == "__main__":
    main()
