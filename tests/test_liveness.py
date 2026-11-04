"""Détection « offre fermée » (S4) : verdicts ouvert / fermé / inconnu, sans réseau réel."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scrapers.cache import DiskCache  # noqa: E402
from scrapers.liveness import (  # noqa: E402
    CLOSED,
    OPEN,
    UNKNOWN,
    LivenessChecker,
    classify_response,
    probe_url,
    source_for_url,
)

# Extraits fidèles des pages réelles sondées le 04/11/2026 (structure conservée).
LINKEDIN_CLOSED = """<section class="top-card-layout"><h2 class="topcard__title">Stage de fin d'études</h2>
<figure class="closed-job closed-job__flavor topcard__flavor-row">
<span class="closed-job__flavor--closed">Les candidatures ne sont plus acceptées</span></figure></section>"""
LINKEDIN_OPEN = """<section class="top-card-layout"><h2 class="topcard__title">Stage - IA</h2>
<button class="top-card-layout__cta" data-modal="job-details-topcard-apply-modal">Postuler</button>
<div class="description">Dans le cadre de sa politique diversité, toutes les candidatures sont étudiées.</div></section>"""
WTTJ_CLOSED = """<html><body><main><h1>Data Scientist IA générative (F/H) - Stage</h1>
<div class="_content-text">Ce job n&#x27;est plus disponible.</div><a>Postuler</a></main>
<script>self.__next_f.push(["no longer accepting applications"])</script></body></html>"""
WTTJ_OPEN = """<html><body><main><h1>Stage - Data Scientist</h1><a>Postuler</a></main>
<script>var i18n = {"closed": "Ce job n'est plus disponible."};</script></body></html>"""
INRIA_SOFT_404 = """<html><head><title>Erreur</title></head><body><main>Désolé, la page que vous cherchez
à atteindre ne peut pas être trouvée. Il est possible qu'elle ait été supprimée.</main></body></html>"""


@pytest.mark.parametrize(
    ("source", "status", "requested", "final", "body", "expected"),
    [
        ("linkedin", 200, "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/1", None, LINKEDIN_CLOSED, CLOSED),
        ("linkedin", 200, "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/1", None, LINKEDIN_OPEN, OPEN),
        ("wttj", 200, "https://www.welcometothejungle.com/fr/companies/axa/jobs/x", None, WTTJ_CLOSED, CLOSED),
        # Un marqueur présent uniquement dans un script (traductions) ne compte pas.
        ("wttj", 200, "https://www.welcometothejungle.com/fr/companies/w/jobs/y", None, WTTJ_OPEN, OPEN),
        ("web", 200, "https://jobs.inria.fr/public/classic/fr/offres/2020-1", None, INRIA_SOFT_404, CLOSED),
        ("web", 404, "https://jobs.lever.co/pigment/abc", None, "Not found", CLOSED),
        ("web", 410, "https://jobs.ashbyhq.com/dust/abc", None, "", CLOSED),
        # Greenhouse : offre fermée → redirection vers l'accueil carrières.
        ("web", 200, "https://job-boards.greenhouse.io/datadog/jobs/1234567", "https://careers.datadoghq.com/", "<p>Careers</p>", CLOSED),
        # Workable : redirection ?not_found=true.
        ("web", 200, "https://apply.workable.com/huggingface/j/ABC123/", "https://apply.workable.com/huggingface/?not_found=true", "<p>Jobs</p>", CLOSED),
        # Redirection de langue sur la même offre : toujours ouverte.
        ("wttj", 200, "https://www.welcometothejungle.com/fr/companies/w/jobs/stage-ds_paris", "https://www.welcometothejungle.com/en/companies/w/jobs/stage-ds_paris", WTTJ_OPEN, OPEN),
        ("web", 429, "https://x.fr/job/1", None, "", UNKNOWN),
        ("wttj", 202, "https://www.welcometothejungle.com/fr/companies/w/jobs/y", None, "<html><script>challenge()</script></html>", UNKNOWN),
        ("web", 403, "https://x.fr/job/1", None, "Forbidden", UNKNOWN),
        ("web", 503, "https://x.fr/job/1", None, "Maintenance", UNKNOWN),
        ("linkedin", 200, "https://www.linkedin.com/jobs/view/1", "https://www.linkedin.com/authwall?trk=x", "<p>Connexion</p>", UNKNOWN),
        ("web", 200, "https://x.fr/job/1", None, "", UNKNOWN),
    ],
)
def test_classify_response(source, status, requested, final, body, expected) -> None:
    verdict = classify_response(source, requested, status, final or requested, body)
    assert verdict.status == expected, verdict.reason


def test_workday_detail_classification() -> None:
    url = "https://thales.wd3.myworkdayjobs.com/wday/cxs/thales/Careers/job/Paris/Stage_R1"
    assert classify_response("workday", url, 404, url, '{"errorCode":"S21"}').status == CLOSED
    assert classify_response("workday", url, 200, url, '{"jobPostingInfo": {"posted": true, "canApply": true}}').status == OPEN
    assert classify_response("workday", url, 200, url, '{"jobPostingInfo": {"posted": false}}').status == CLOSED
    assert classify_response("workday", url, 200, url, "pas du json").status == UNKNOWN


def test_probe_url_uses_lightest_endpoint() -> None:
    assert probe_url("https://fr.linkedin.com/jobs/view/stage-ia-at-acme-4398230707", "linkedin") == (
        "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/4398230707"
    )
    assert probe_url("https://thales.wd3.myworkdayjobs.com/fr-FR/Careers/job/Paris/Stage_R1", "workday") == (
        "https://thales.wd3.myworkdayjobs.com/wday/cxs/thales/Careers/job/Paris/Stage_R1"
    )
    assert source_for_url("https://emse.jobteaser.com/fr/job-offers/x") == "jobteaser"
    assert source_for_url("https://x.fr", "welcome_to_the_jungle") == "wttj"


# --------------------------------------------------------------------------- #
# Sonde : débit, cache, coupe-circuit, erreurs réseau
# --------------------------------------------------------------------------- #
def _checker(handler, tmp_path: Path | None = None, sleeps: list[float] | None = None) -> LivenessChecker:
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    clock = iter(range(0, 10_000))
    return LivenessChecker(
        client,
        min_interval=1.0,
        cache=DiskCache(tmp_path / "cache") if tmp_path else None,
        sleep=(sleeps.append if sleeps is not None else (lambda _s: None)),
        clock=lambda: float(next(clock)) * 0.1,
    )


def test_checker_closed_and_open(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "closed" in str(request.url):
            return httpx.Response(404)
        return httpx.Response(200, text=WTTJ_OPEN)

    checker = _checker(handler, tmp_path)
    assert checker.check("https://ex.fr/job/closed").is_closed
    assert checker.check("https://ex.fr/job/open").status == OPEN
    assert checker.network_checks == 2
    # Verdict « ouvert » servi par le cache : aucun nouvel appel.
    again = checker.check("https://ex.fr/job/open")
    assert again.status == OPEN and again.from_cache and checker.network_checks == 2


def test_checker_rate_limit_suspends_host(tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(429)

    checker = _checker(handler)
    first = checker.check("https://ex.fr/job/1")
    second = checker.check("https://ex.fr/job/2")
    assert first.status == UNKNOWN and second.status == UNKNOWN
    assert len(calls) == 1  # l'hôte n'est plus sollicité après le 429
    assert "suspendu" in second.reason


@pytest.mark.parametrize("error", [httpx.ReadTimeout("lent"), httpx.ConnectError("dns")])
def test_checker_network_errors_conclude_nothing(error: Exception) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    verdict = _checker(handler).check("https://ex.fr/job/1")
    assert verdict.status == UNKNOWN


def test_checker_throttles_per_host() -> None:
    sleeps: list[float] = []
    checker = LivenessChecker(
        httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=WTTJ_OPEN))),
        min_interval=1.0,
        sleep=sleeps.append,
        clock=lambda: 0.0,
    )
    checker.check("https://a.fr/job/1")
    checker.check("https://a.fr/job/2")
    checker.check("https://b.fr/job/1")
    assert sleeps == [1.0]


def test_jobteaser_and_invalid_urls_are_not_probed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("aucun appel attendu")

    checker = _checker(handler)
    assert checker.check("https://emse.jobteaser.com/fr/job-offers/x", "jobteaser").status == UNKNOWN
    assert checker.check("", "linkedin").status == UNKNOWN
    assert checker.network_checks == 0
