"""Robustesse des sources multi-boards (ATS, labos) : une panne ne bloque rien, mais se voit.

Exigence d'Eddy : « si une source ou un board ne marche pas, ça ne doit rien
bloquer, mais on doit le savoir et pourquoi ». Toutes les réponses HTTP sont
simulées (aucun appel réseau, aucune base).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scrapers.ats import DirectATSScraper  # noqa: E402
from scrapers.base import BaseScraper  # noqa: E402
from scrapers.health import (  # noqa: E402
    SOURCE_DEGRADED,
    SOURCE_DOWN,
    SOURCE_INACTIVE,
    SOURCE_OK,
    detect_board_failures,
    parse_board_status,
    summarize_source_health,
)
from scrapers.manager import SCRAPER_REGISTRY, ScraperManager  # noqa: E402
from scrapers.models import (  # noqa: E402
    PassConfig,
    PassPlan,
    PassReport,
    ScraperConfig,
    board_status_label,
    is_board_failure,
)


class _Resp:
    def __init__(self, data: Any = None, status_code: int = 200, text: str = "", bad_json: bool = False) -> None:
        self._data = data
        self._bad_json = bad_json
        self.status_code = status_code
        self.text = text
        self.request = httpx.Request("GET", "https://mock.api")

    def json(self) -> Any:
        if self._bad_json:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("simulé", request=self.request, response=self)  # type: ignore[arg-type]


_GOOD_ASHBY = {
    "jobs": [
        {
            "id": "a1",
            "title": "Machine Learning Research Intern",
            "location": "Paris, France",
            "employmentType": "Intern",
            "jobUrl": "https://jobs.ashbyhq.com/good/a1",
            "descriptionPlain": "Stage en deep learning et modèles de fondation avec PyTorch.",
            "publishedAt": "2026-10-20T10:00:00Z",
        }
    ]
}


def _scraper(boards: dict[str, list[str]]) -> DirectATSScraper:
    config = ScraperConfig(passes=PassConfig.only("relevance"), ats_boards=boards, http_max_retries=0)
    return DirectATSScraper(config)


def _mock(responses: dict[str, Any]) -> MagicMock:
    def get(url: str, **kwargs: Any) -> _Resp:
        for needle, response in responses.items():
            if needle in url:
                if isinstance(response, BaseException):
                    raise response
                return response
        return _Resp({}, status_code=404)

    return MagicMock(side_effect=get)


@pytest.mark.parametrize(
    ("failure", "expected_status", "expected_stop"),
    [
        (_Resp({"ok": False}, status_code=404), "http_404", "http_error"),
        (_Resp({}, status_code=503), "http_503", "http_error"),
        (_Resp({}, status_code=429), "http_429", "rate_limit"),
        (httpx.ReadTimeout("lecture trop longue"), "timeout", "network_error"),
        (httpx.ConnectError("DNS introuvable"), "network_error", "network_error"),
        (_Resp(text="<html>maintenance</html>", bad_json=True), "parse_error", "error"),
        (_Resp({"unexpected": True}), "ok", "stream_end"),  # objet sans « jobs » : board vide
    ],
)
def test_failing_board_does_not_block_others(failure: Any, expected_status: str, expected_stop: str) -> None:
    scraper = _scraper({"ashby": ["broken", "good"]})
    scraper.client.get = _mock({"/broken": failure, "/good": _Resp(_GOOD_ASHBY)})  # type: ignore[assignment]

    result = scraper.run()

    # Le board sain a bien été collecté malgré la panne du premier.
    assert [job.id_externe for job in result.jobs] == ["ashby_good_a1"]
    reports = {report.query: report for report in result.query_reports}
    broken = reports["ashby:broken"]
    if expected_status == "ok":
        assert broken.board_status == "vide"
    else:
        assert broken.board_status == expected_status
        assert broken.stop_reason == expected_stop
        # La cause est persistée en tête de stop_detail (seule colonne en base).
        assert broken.stop_detail.startswith(f"[{expected_status}]")
    assert reports["ashby:good"].board_status == "ok"


def test_empty_board_is_flagged_not_failed() -> None:
    scraper = _scraper({"lever": ["ghost"]})
    scraper.client.get = _mock({"lever.co": _Resp([])})  # type: ignore[assignment]
    result = scraper.run()
    report = result.query_reports[0]
    assert report.board_status == "vide"
    assert not is_board_failure(report.board_status)
    alerts = detect_board_failures(result.query_reports)
    assert len(alerts) == 1 and not alerts[0].is_failure
    assert "slug" in report.stop_detail or "aucune offre" in report.stop_detail


def test_unknown_board_kind_is_ignored_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    scraper = _scraper({"taleo": ["x"], "ashby": ["good"]})
    scraper.client.get = _mock({"/good": _Resp(_GOOD_ASHBY)})  # type: ignore[assignment]
    result = scraper.run()
    assert [job.id_externe for job in result.jobs] == ["ashby_good_a1"]
    assert "type de board inconnu" in caplog.text


def test_no_board_configured_is_reported() -> None:
    scraper = _scraper({})
    scraper.boards = {}
    result = scraper.run()
    assert result.jobs == []
    assert result.query_reports[0].board_status == "non_lance"
    assert result.query_reports[0].stop_reason == "disabled"


def test_many_boards_are_all_queried_despite_source_cap() -> None:
    """Le plafond de source ne prive plus les derniers boards de la liste."""
    slugs = [f"b{i}" for i in range(6)]
    config = ScraperConfig(
        passes=PassConfig.only("relevance"),
        ats_boards={"ashby": slugs},
        max_offers_per_source=2,
    )
    scraper = DirectATSScraper(config)

    def get(url: str, **kwargs: Any) -> _Resp:
        slug = url.rsplit("/", 1)[-1]
        data = {"jobs": [dict(_GOOD_ASHBY["jobs"][0], id=f"{slug}-1", jobUrl=f"https://jobs.ashbyhq.com/{slug}/1")]}
        return _Resp(data)

    scraper.client.get = MagicMock(side_effect=get)  # type: ignore[assignment]
    result = scraper.run()
    assert len(result.jobs) == 6
    assert {report.board_status for report in result.query_reports} == {"ok"}


def test_board_failure_alert_message_says_what_and_why() -> None:
    scraper = _scraper({"greenhouse": ["dead"], "ashby": ["good"]})
    scraper.client.get = _mock({"greenhouse": _Resp({}, status_code=404), "/good": _Resp(_GOOD_ASHBY)})  # type: ignore[assignment]
    result = scraper.run()
    alerts = detect_board_failures(result.query_reports)
    assert len(alerts) == 1
    message = alerts[0].message()
    assert "greenhouse:dead" in message and "404" in message and "continuent" in message


# --------------------------------------------------------------------------- #
# Une SOURCE entière en échec ne bloque pas les autres
# --------------------------------------------------------------------------- #
class _ExplodingScraper(BaseScraper):
    source = "ats"

    def _iter_pages(self, query: str, mode: str, cursor: Any, plan: PassPlan) -> Any:  # pragma: no cover
        raise AssertionError("jamais appelé")

    def run(self, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        raise RuntimeError("bug inattendu dans la source")


def test_manager_survives_a_crashing_source(monkeypatch: pytest.MonkeyPatch) -> None:
    good = _scraper({"ashby": ["good"]})
    good.client.get = _mock({"/good": _Resp(_GOOD_ASHBY)})  # type: ignore[assignment]

    class _GoodAcademic(DirectATSScraper):
        source = "academic"

        def __init__(self, config: ScraperConfig | None = None) -> None:
            super().__init__(config)
            self.boards = {"ashby": ["good"]}
            self.client.get = _mock({"/good": _Resp(_GOOD_ASHBY)})  # type: ignore[assignment]

    monkeypatch.setitem(SCRAPER_REGISTRY, "ats", _ExplodingScraper)
    monkeypatch.setitem(SCRAPER_REGISTRY, "academic", _GoodAcademic)
    manager = ScraperManager(
        ScraperConfig(enabled_sources=["ats", "academic"], passes=PassConfig.only("relevance"), parallel_sources=False)
    )
    result = manager.run()
    assert len(result.jobs) == 1 and result.jobs[0].source == "academic"


# --------------------------------------------------------------------------- #
# Synthèse de santé (API / UI)
# --------------------------------------------------------------------------- #
def _report(source: str, query: str, **kwargs: Any) -> PassReport:
    from datetime import datetime, timezone

    return PassReport(source=source, query=query, mode="relevance", started_at=datetime.now(timezone.utc), **kwargs)


def test_parse_board_status() -> None:
    assert parse_board_status("[http_404] HTTP 404 ; plus de résultats") == ("http_404", "HTTP 404 ; plus de résultats")
    assert parse_board_status("flux épuisé") == ("", "flux épuisé")
    assert board_status_label("http_410") == "Erreur HTTP 410"
    assert board_status_label("timeout") == "Délai dépassé"


def test_summarize_source_health_from_db_rows() -> None:
    rows = [
        # Lignes telles que lues dans scrape_query_stats (pas de colonne board_status).
        {"source": "ats", "query": "lever:a", "stop_reason": "stream_end", "stop_detail": "[ok] 3 offres", "http_requests": 1, "cards_seen": 3},
        {"source": "ats", "query": "lever:b", "stop_reason": "http_error", "stop_detail": "[http_404] HTTP 404", "http_requests": 0, "cards_seen": 0},
        {"source": "ats", "query": "ashby:c", "stop_reason": "stream_end", "stop_detail": "[vide] aucune offre", "http_requests": 1, "cards_seen": 0},
        {"source": "academic", "query": "cea", "stop_reason": "network_error", "stop_detail": "[timeout] délai dépassé", "http_requests": 0},
        {"source": "linkedin", "query": "Data Scientist", "stop_reason": "quota", "stop_detail": "quota atteint", "http_requests": 5, "cards_seen": 50},
        {"source": "jobteaser", "query": "x", "stop_reason": "auth_missing", "stop_detail": "cookies absents", "http_requests": 0},
    ]
    health = {entry["source"]: entry for entry in summarize_source_health(rows)}
    assert health["ats"]["status"] == SOURCE_DEGRADED
    assert "1 board(s) en panne" in health["ats"]["message"] and "1 board(s) vide(s)" in health["ats"]["message"]
    assert health["ats"]["boards"][0]["board"] == "lever:b"  # pannes en tête
    assert health["ats"]["boards"][0]["label"] == "Erreur HTTP 404"
    assert health["academic"]["status"] == SOURCE_DOWN
    assert health["linkedin"]["status"] == SOURCE_OK
    assert health["jobteaser"]["status"] == SOURCE_INACTIVE


def test_summarize_source_health_accepts_pass_reports() -> None:
    reports = [_report("ats", "lever:a", board_status="ok", stop_detail="[ok] x", http_requests=1, cards_seen=1)]
    assert summarize_source_health(reports)[0]["status"] == SOURCE_OK
