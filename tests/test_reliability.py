"""Fiabilité du cron : nouvelles tentatives HTTP, détection de source dégradée, purge du cache."""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone

import httpx
import pytest

from scrapers.cache import DiskCache
from scrapers.health import detect_degraded_sources
from scrapers.http import RetryingClient, parse_retry_after
from scrapers.models import PassReport


def _client(responses, sleeps, **kwargs):
    """Client dont le transport rejoue ``responses`` (statut ou exception) dans l'ordre."""
    queue = list(responses)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        status, headers = item if isinstance(item, tuple) else (item, {})
        return httpx.Response(status, headers=headers, text="ok")

    client = RetryingClient(
        transport=httpx.MockTransport(handler),
        sleep=sleeps.append,
        backoff_seconds=1.0,
        **kwargs,
    )
    return client, calls


def test_retry_puis_succes_sur_429():
    sleeps: list[float] = []
    client, calls = _client([429, 503, 200], sleeps)
    assert client.get("https://x.test/").status_code == 200
    assert len(calls) == 3 and len(sleeps) == 2
    assert sleeps[1] > sleeps[0] * 1.4  # attente croissante


def test_retry_epuise_rend_la_derniere_reponse():
    sleeps: list[float] = []
    client, calls = _client([429] * 4, sleeps, max_retries=3)
    assert client.get("https://x.test/").status_code == 429
    assert len(calls) == 4  # 1 essai + 3 rejeux


def test_pas_de_retry_sur_403_ni_404():
    for status in (403, 404):
        sleeps: list[float] = []
        client, calls = _client([status], sleeps)
        assert client.get("https://x.test/").status_code == status
        assert len(calls) == 1 and not sleeps


def test_retry_after_respecte():
    sleeps: list[float] = []
    client, _ = _client([(429, {"Retry-After": "7"}), 200], sleeps)
    client.get("https://x.test/")
    assert sleeps == [7.0]


def test_retry_after_trop_long_renonce_sans_dormir():
    sleeps: list[float] = []
    client, calls = _client([(429, {"Retry-After": "600"}), 200], sleeps, max_wait_seconds=30)
    assert client.get("https://x.test/").status_code == 429
    assert len(calls) == 1 and not sleeps


def test_erreur_reseau_reessayee_puis_propagee():
    sleeps: list[float] = []
    client, calls = _client([httpx.ConnectTimeout("t"), 200], sleeps)
    assert client.get("https://x.test/").status_code == 200
    client, calls = _client([httpx.ConnectTimeout("t")] * 4, [], max_retries=3)
    with pytest.raises(httpx.ConnectTimeout):
        client.get("https://x.test/")
    assert len(calls) == 4


def test_parse_retry_after():
    assert parse_retry_after("5") == 5.0
    assert parse_retry_after(None) is None
    assert parse_retry_after("n'importe quoi") is None


def _report(source, cards, *, stop="quota", http=1):
    now = datetime.now(timezone.utc)
    return PassReport(
        source=source, query="q", mode="freshness", started_at=now,
        cards_seen=cards, http_requests=http, stop_reason=stop,
    )


def test_source_a_zero_carte_alertee():
    alerts = detect_degraded_sources(
        [_report("linkedin", 0, stop="stream_end"), _report("wttj", 120)],
        {"linkedin": [100, 90, 110, 95], "wttj": [100, 110, 90]},
    )
    assert [a.source for a in alerts] == ["linkedin"]
    assert "linkedin" in alerts[0].message()


def test_chute_sous_seuil_alertee_baisse_normale_ignoree():
    history = {"wttj": [100, 100, 100]}
    assert detect_degraded_sources([_report("wttj", 15)], history)
    assert not detect_degraded_sources([_report("wttj", 60)], history)


def test_pas_d_alerte_sans_historique_ni_baseline_ni_tentative():
    assert not detect_degraded_sources([_report("wttj", 0)], {"wttj": [50, 50]})  # historique court
    assert not detect_degraded_sources([_report("wttj", 0)], {"wttj": [2, 3, 1, 2]})  # source quasi vide
    skipped = _report("jobteaser", 0, stop="source_unavailable", http=0)
    assert not detect_degraded_sources([skipped], {"jobteaser": [80, 80, 80]})  # jamais tentée


def test_source_bloquee_avant_premiere_page_est_alertee():
    blocked = _report("jobteaser", 0, stop="http_error", http=0)
    assert detect_degraded_sources([blocked], {"jobteaser": [80, 80, 80]})


def test_prune_du_cache(tmp_path):
    cache = DiskCache(tmp_path)
    old = cache.set("ns", "vieux", "a")
    fresh = cache.set("ns", "recent", "b")
    stamp = time.time() - 60 * 86400
    os.utime(old, (stamp, stamp))
    assert cache.prune(45) == 1
    assert not old.exists() and fresh.exists()


def test_historique_des_cartes_par_source(tmp_path):
    from src.storage.database import Database

    db = Database(str(tmp_path / "t.db"))
    for total in (10, 20, 30):
        run_id = db.start_run(["linkedin"])
        db.record_query_stats(run_id, [{"source": "linkedin", "query": "q", "mode": "freshness",
                                        "cards_seen": total, "stop_reason": "quota"}])
        time.sleep(0.01)
    current = db.start_run(["linkedin"])
    db.record_query_stats(current, [{"source": "linkedin", "query": "q", "mode": "freshness",
                                     "cards_seen": 1, "stop_reason": "quota"}])
    history = db.get_source_card_history(runs=2, exclude_run_id=current)
    assert history["linkedin"] == [30, 20]
    db.engine.dispose()


def _seed_run(db, source, cards, notes=None):
    run_id = db.start_run([source])
    db.record_query_stats(run_id, [{"source": source, "query": "q", "mode": "freshness",
                                    "cards_seen": cards, "stop_reason": "quota"}])
    db.finish_run(run_id, status="OK", notes=notes)
    time.sleep(0.01)
    return run_id


def test_historique_ignore_runs_adhoc_degrades_et_vides(tmp_path):
    """La référence ne contient que des runs standards et sains de la source."""
    from src.storage.database import Database

    db = Database(str(tmp_path / "t.db"))
    _seed_run(db, "jobteaser", 100)
    _seed_run(db, "jobteaser", 90)
    _seed_run(db, "jobteaser", 5, notes="adhoc")          # collecte personnalisée
    _seed_run(db, "jobteaser", 3, notes="degraded:jobteaser")  # panne déjà signalée
    _seed_run(db, "jobteaser", 0)                          # panne non signalée
    assert db.get_source_card_history()["jobteaser"] == [90, 100]
    db.engine.dispose()


def test_panne_persistante_reste_alertee(tmp_path):
    """Trois runs en panne d'affilée ne font pas taire l'alerte (référence préservée)."""
    from src.storage.database import Database

    db = Database(str(tmp_path / "t.db"))
    for cards in (100, 110, 90):
        _seed_run(db, "jobteaser", cards)
    for _ in range(3):
        _seed_run(db, "jobteaser", 0, notes="degraded:jobteaser")
    history = db.get_source_card_history()
    assert detect_degraded_sources([_report("jobteaser", 0)], history)
    db.engine.dispose()


def _fake_manager(reports):
    from scrapers.models import ScrapeResult

    class _FakeManager:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, modes=None, on_batch_collected=None):
            return ScrapeResult(jobs=[], found=0, rejected_bi=0, query_reports=reports)

    return _FakeManager


def test_collecte_personnalisee_sans_alerte_ni_echec(tmp_path, monkeypatch):
    """--queries/--sources/--passes... : pas d'alerte, code 0, run exclu de la référence."""
    import run_scrapers
    from src.storage.database import Database

    db_path = str(tmp_path / "t.db")
    db = Database(db_path)
    for cards in (100, 110, 90):
        _seed_run(db, "linkedin", cards)
    monkeypatch.setattr(run_scrapers, "load_config",
                        lambda: {"database": {"path": db_path}, "scrapers": {"enabled_sources": ["linkedin"]}})
    monkeypatch.setattr(run_scrapers, "ScraperManager", _fake_manager([_report("linkedin", 2)]))
    assert run_scrapers.main(["--queries", "NLP", "--max-offers", "5"]) == 0
    last = Database(db_path).get_recent_runs(limit=1)[0]
    assert "adhoc" in (last["notes"] or ""), last
    assert "degraded" not in (last["notes"] or ""), last


def test_run_standard_degrade_alerte(tmp_path, monkeypatch):
    """Run standard qui s'effondre : alerte et code non nul."""
    import run_scrapers
    from src.storage.database import Database

    db_path = str(tmp_path / "t.db")
    db = Database(db_path)
    for cards in (100, 110, 90):
        _seed_run(db, "linkedin", cards)
    monkeypatch.setattr(run_scrapers, "load_config",
                        lambda: {"database": {"path": db_path}, "scrapers": {"enabled_sources": ["linkedin"]}})
    monkeypatch.setattr(run_scrapers, "ScraperManager", _fake_manager([_report("linkedin", 2)]))
    assert run_scrapers.main([]) == 1


def test_cache_ci_sauvegarde_meme_en_echec():
    """Le cache des fiches est sauvegardé même quand le job échoue (source dégradée)."""
    from pathlib import Path

    import yaml

    workflow = yaml.safe_load(
        (Path(__file__).resolve().parent.parent / ".github/workflows/daily_scraper.yml").read_text(encoding="utf-8")
    )
    steps = workflow["jobs"]["scrape-and-sync"]["steps"]
    saves = [s for s in steps if str(s.get("uses", "")).startswith("actions/cache/save")]
    restores = [s for s in steps if str(s.get("uses", "")).startswith("actions/cache/restore")]
    assert restores and saves, steps
    assert "always()" in str(saves[0].get("if", ""))
    assert "run_attempt" in saves[0]["with"]["key"]
