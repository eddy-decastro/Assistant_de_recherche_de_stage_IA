"""Scraper Workday : facettes, pagination, fiche détail, robustesse (réponses simulées)."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scrapers.workday as workday  # noqa: E402
from scrapers.cache import DiskCache  # noqa: E402
from scrapers.manager import SCRAPER_REGISTRY  # noqa: E402
from scrapers.models import PassConfig, ScraperConfig  # noqa: E402
from scrapers.workday import (  # noqa: E402
    DETAIL_MISSING_REASON,
    WorkdayScraper,
    detail_api_url,
    discover_facets,
    is_intern_contract,
    parse_board_url,
    parse_posted_on,
)

FRANCE_ID = "54c5b6971ffb4bf0b116fe7651ec789a"
FACETS = [
    {
        "facetParameter": "workerSubType",
        "descriptor": "Job Type",
        "values": [
            {"descriptor": "Regular Employee", "id": "regular", "count": 2000},
            {"descriptor": "Intern/Trainee (Fixed Term) (Trainee)", "id": "intern", "count": 900},
            {"descriptor": "UK - Apprentices (Trainee)", "id": "apprentice", "count": 40},
            {"descriptor": "VIE - French International Internship Programme", "id": "vie", "count": 4},
        ],
    },
    {
        "facetParameter": "locationMainGroup",
        "values": [
            {
                "facetParameter": "locationCountry",
                "descriptor": "Location Country",
                "values": [
                    {"descriptor": "France", "id": FRANCE_ID, "count": 2400},
                    {"descriptor": "Germany", "id": "de", "count": 300},
                ],
            },
            {
                "facetParameter": "locations",
                "descriptor": "Locations",
                "values": [{"descriptor": "Toute France", "id": "x", "count": 2}],
            },
        ],
    },
]


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(workday.time, "sleep", lambda *_: None)


class _Resp:
    def __init__(self, data: Any = None, status_code: int = 200, bad_json: bool = False) -> None:
        self._data = data
        self._bad_json = bad_json
        self.status_code = status_code
        self.text = json.dumps(data) if data is not None else ""
        self.request = httpx.Request("POST", "https://mock.myworkdayjobs.com")

    def json(self) -> Any:
        if self._bad_json:
            raise ValueError("JSON invalide")
        return self._data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("simulé", request=self.request, response=self)  # type: ignore[arg-type]


def _posting(i: int, title: str = "Stage - Data Scientist Machine Learning F/H", posted: str = "Posted Today") -> dict[str, Any]:
    return {
        "title": title,
        "externalPath": f"/job/Paris/Stage-{i}_R00{i}-1",
        "locationsText": "Paris",
        "postedOn": posted,
        "bulletFields": ["Intern/Trainee (Fixed Term) (Trainee)", f"R00{i}"],
    }


def _detail(country: str = "France") -> dict[str, Any]:
    return {
        "jobPostingInfo": {
            "jobDescription": "<p>Stage de fin d'études : <b>deep learning</b> et PyTorch pour la vision.</p>",
            "startDate": "2026-10-28",
            "country": {"descriptor": country, "id": FRANCE_ID},
            "location": "Paris",
        },
        "hiringOrganization": {"name": "Thales SIX"},
    }


def _scraper(boards: list[dict[str, str]] | None = None, **config: Any) -> WorkdayScraper:
    cfg = ScraperConfig(
        passes=PassConfig.only("relevance"),
        workday_boards=boards or [{"name": "Thales", "url": "https://thales.wd3.myworkdayjobs.com/fr-FR/Careers"}],
        http_max_retries=0,
        **config,
    )
    return WorkdayScraper(cfg)


# --------------------------------------------------------------------------- #
# Fonctions pures
# --------------------------------------------------------------------------- #
def test_parse_board_url_and_detail_url() -> None:
    assert parse_board_url("https://thales.wd3.myworkdayjobs.com/fr-FR/Careers") == (
        "thales.wd3.myworkdayjobs.com",
        "thales",
        "Careers",
    )
    assert parse_board_url("https://ipsen.wd103.myworkdayjobs.com/en-EN/Ipsen_Careers")[2] == "Ipsen_Careers"
    with pytest.raises(ValueError):
        parse_board_url("https://example.com/jobs")
    assert detail_api_url("https://thales.wd3.myworkdayjobs.com/fr-FR/Careers/job/Paris/Stage_R1") == (
        "https://thales.wd3.myworkdayjobs.com/wday/cxs/thales/Careers/job/Paris/Stage_R1"
    )
    assert detail_api_url("https://www.linkedin.com/jobs/view/123") == ""


def test_parse_posted_on() -> None:
    now = datetime(2026, 11, 4, 15, 0, tzinfo=timezone.utc)
    assert parse_posted_on("Posted Today", now) == datetime(2026, 11, 4, tzinfo=timezone.utc)
    assert parse_posted_on("Posted Yesterday", now) == datetime(2026, 11, 3, tzinfo=timezone.utc)
    assert parse_posted_on("Posted 5 Days Ago", now) == datetime(2026, 10, 30, tzinfo=timezone.utc)
    assert parse_posted_on("Posted 30+ Days Ago", now) is None
    assert parse_posted_on("", now) is None


def test_intern_contract_detection() -> None:
    assert is_intern_contract("Intern/Trainee (Fixed Term) (Trainee)")
    assert is_intern_contract("Apprentice/Intern (Fixed Term)")  # Sanofi : stage + alternance mêlés
    assert is_intern_contract("Trainee / Student (Fixed Term)")
    assert is_intern_contract("EA Trainees, Interns and Apprentices")
    assert not is_intern_contract("UK - Apprentices (Trainee)")
    assert not is_intern_contract("VIE - French International Internship Programme")
    assert not is_intern_contract("International Work Placement (VIE)")
    assert not is_intern_contract("Regular Employee")


def test_discover_facets_picks_france_and_intern_types() -> None:
    applied, notes = discover_facets(FACETS)
    assert applied == {"workerSubType": ["intern"], "locationCountry": [FRANCE_ID]}
    assert notes == []


def test_discover_facets_without_contract_facet() -> None:
    applied, notes = discover_facets([FACETS[1]])
    assert applied == {"locationCountry": [FRANCE_ID]}
    assert any("titre" in note for note in notes)


# --------------------------------------------------------------------------- #
# Collecte complète
# --------------------------------------------------------------------------- #
def _install(scraper: WorkdayScraper, list_pages: list[list[dict[str, Any]]], total: int, detail: Any = None) -> list[dict[str, Any]]:
    bodies: list[dict[str, Any]] = []

    def post(url: str, json: dict[str, Any] | None = None, **kwargs: Any) -> _Resp:  # noqa: A002
        bodies.append(json or {})
        if not (json or {}).get("appliedFacets"):
            return _Resp({"total": 5000, "facets": FACETS, "jobPostings": [_posting(999, "CDI")]})
        index = int((json or {}).get("offset", 0)) // workday.PAGE_SIZE
        page = list_pages[index] if index < len(list_pages) else []
        return _Resp({"total": total if index == 0 else 0, "jobPostings": page})

    def get(url: str, **kwargs: Any) -> _Resp:
        assert "/wday/cxs/thales/Careers/job/" in url
        if isinstance(detail, BaseException):
            raise detail
        return detail if isinstance(detail, _Resp) else _Resp(detail or _detail())

    scraper.client.post = MagicMock(side_effect=post)  # type: ignore[assignment]
    scraper.client.get = MagicMock(side_effect=get)  # type: ignore[assignment]
    return bodies


def test_workday_collects_french_internships_with_detail(tmp_path: Path) -> None:
    scraper = _scraper()
    scraper.detail_cache = DiskCache(tmp_path / "cache")
    page1 = [_posting(i) for i in range(20)]
    page2 = [_posting(20), _posting(21, "Stage - Assistant comptable F/H")]
    bodies = _install(scraper, [page1, page2], total=22)

    result = scraper.run()

    # Les facettes découvertes sont appliquées à la recherche paginée.
    assert bodies[1]["appliedFacets"] == {"workerSubType": ["intern"], "locationCountry": [FRANCE_ID]}
    assert [body["offset"] for body in bodies[1:]] == [0, 20]
    ids = [job.id_externe for job in result.jobs]
    assert len(ids) == 21 and "workday_thales_R000" in ids
    job = result.jobs[0]
    assert job.source == "workday"
    assert job.company == "Thales"
    assert job.url == "https://thales.wd3.myworkdayjobs.com/fr-FR/Careers/job/Paris/Stage-0_R000-1"
    assert "deep learning" in job.description
    # Date réelle de la fiche (startDate), pas l'approximation « Posted Today ».
    assert job.published_at == datetime(2026, 10, 28, tzinfo=timezone.utc)
    report = result.query_reports[0]
    assert report.board_status == "ok"
    assert report.stop_detail.startswith("[ok] 22 offre(s) de stage en France")
    # Fiche en cache : un second run ne refait aucun appel détail.
    calls_before = scraper.client.get.call_count
    again = _scraper()
    again.detail_cache = DiskCache(tmp_path / "cache")
    _install(again, [page1, page2], total=22)
    again.run()
    assert again.client.get.call_count == 0 and calls_before > 0


def test_workday_reposts_are_deduplicated() -> None:
    scraper = _scraper()
    first = _posting(1)
    repost = dict(first, externalPath="/job/Lyon/Stage-1_R001-2")
    _install(scraper, [[first, repost]], total=2)
    result = scraper.run()
    assert [job.id_externe for job in result.jobs] == ["workday_thales_R001"]


def test_workday_rejects_offer_outside_france() -> None:
    scraper = _scraper()
    _install(scraper, [[_posting(1)]], total=1, detail=_detail(country="Germany"))
    result = scraper.run()
    assert result.jobs == []
    assert any("hors France" in (entry.rejection_reason or "") for entry in result.seen)


def test_workday_postpones_offer_when_detail_unreadable() -> None:
    scraper = _scraper()
    _install(scraper, [[_posting(1)]], total=1, detail=_Resp({}, status_code=500))
    result = scraper.run()
    assert result.jobs == []
    rejected = [entry for entry in result.seen if entry.rejection_reason == DETAIL_MISSING_REASON]
    assert rejected and rejected[0].filter_version is None  # non définitif : réévalué au run suivant


@pytest.mark.parametrize(
    ("failure", "status"),
    [
        (_Resp({"errorCode": "HTTP_422"}, status_code=422), "http_422"),
        (_Resp({"errorCode": "S21"}, status_code=404), "http_404"),
        (httpx.ReadTimeout("trop long"), "timeout"),
        (_Resp(bad_json=True), "parse_error"),
    ],
)
def test_dead_workday_board_does_not_block_others(failure: Any, status: str) -> None:
    scraper = _scraper(
        [
            {"name": "Mort", "url": "https://mort.wd3.myworkdayjobs.com/External"},
            {"name": "Thales", "url": "https://thales.wd3.myworkdayjobs.com/Careers"},
        ]
    )
    _install(scraper, [[_posting(1)]], total=1)
    healthy_post = scraper.client.post.side_effect

    def post(url: str, **kwargs: Any) -> _Resp:
        if "mort.wd3" in url:
            if isinstance(failure, BaseException):
                raise failure
            return failure
        return healthy_post(url, **kwargs)

    scraper.client.post = MagicMock(side_effect=post)  # type: ignore[assignment]
    result = scraper.run()
    statuses = {report.query: report.board_status for report in result.query_reports}
    assert statuses == {"Mort": status, "Thales": "ok"}
    assert [job.id_externe for job in result.jobs] == ["workday_thales_R001"]


def test_invalid_board_url_is_a_parse_error_not_a_crash() -> None:
    scraper = _scraper([{"name": "Cassé", "url": "https://example.com/jobs"}])
    result = scraper.run()
    assert result.query_reports[0].board_status == "parse_error"


def test_empty_workday_board() -> None:
    scraper = _scraper()
    _install(scraper, [[]], total=0)
    result = scraper.run()
    assert result.query_reports[0].board_status == "vide"


def test_workday_registered_in_manager() -> None:
    assert SCRAPER_REGISTRY["workday"] is WorkdayScraper
    from typing import get_args

    from scrapers.models import Source

    assert "workday" in get_args(Source)
