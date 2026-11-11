"""Mission doublons par agents : groupes, lots, garde-fous d'ingestion (base temporaire)."""
from __future__ import annotations

import json
from pathlib import Path

from src.constants import DUPLICATE_REASON, STATUS_APPLIED, STATUS_IGNORED, STATUS_NEW
from src.storage.database import Database
from tools import agents_doublons as ad

TEXT = "Mission : développer des modèles de vision par ordinateur pour la détection d'objets. " * 5


def _job(i: str, title: str, company: str = "Thales DMS", status: str = STATUS_NEW, desc: str = "") -> dict:
    return {"id": i, "title": title, "company": company, "url": f"https://x/{i}", "location": "Paris",
            "status": status, "description": desc or f"texte unique {i} " * 30, "source": "linkedin"}


def test_groups_by_company_and_title() -> None:
    jobs = [
        _job("a", "Stage - Vision par ordinateur H/F"),
        _job("b", "Stage vision par ordinateur (F/H)", company="Thales"),
        _job("c", "Stage cybersécurité réseau"),
        _job("d", "Stage vision par ordinateur", company="Airbus"),
    ]
    groups = ad.build_groups(jobs)
    assert list(groups) == ["thales"]
    assert {j["id"] for j in groups["thales"][0]} == {"a", "b"}


def test_identical_text_groups_even_with_other_title() -> None:
    jobs = [_job("a", "Computer vision intern", desc=TEXT), _job("b", "Stage détection d'objets", desc=TEXT)]
    assert {j["id"] for j in ad.build_groups(jobs)["thales"][0]} == {"a", "b"}


def test_lots_never_split_a_group() -> None:
    groups = {"thales": [[_job(f"g1-{i}", "x") for i in range(30)], [_job(f"g2-{i}", "y") for i in range(15)]],
              "airbus": [[_job(f"g3-{i}", "z", "Airbus") for i in range(12)]]}
    lots = ad.make_lots(groups, max_per_lot=40)
    for lot in lots:
        assert sum(len(g["offres"]) for g in lot["groupes"]) <= 40
    sizes = sorted(len(g["offres"]) for lot in lots for g in lot["groupes"])
    assert sizes == [12, 15, 30]


def test_big_chain_is_split() -> None:
    titles = [f"Stage data scientist {w}" for w in "alpha beta gamma delta epsilon zeta eta theta iota kappa".split()]
    titles += [f"Stage ingenieur data {w}" for w in "lambda mu nu xi omicron pi rho sigma tau upsilon phi".split()]
    jobs = [_job(str(i), t) for i, t in enumerate(titles)]
    assert all(len(g) <= ad.MAX_GROUP for g in ad.build_groups(jobs).get("thales", []))


def test_ingest_protects_eddy_and_checks_group(tmp_path: Path) -> None:
    db_path = tmp_path / "base.db"
    db = Database(str(db_path))
    for j in [_job("keep", "Stage vision"), _job("dup", "Stage vision"), _job("eddy", "Stage vision"),
              _job("autre", "Stage cyber")]:
        db.upsert_job(dict(j))
    db.update_status("eddy", STATUS_APPLIED)

    work = tmp_path / "vague"
    (work / "in").mkdir(parents=True)
    (work / "out").mkdir()
    lot = {"groupes": [{"groupe": "g0001", "entreprise": "Thales",
                        "offres": [{"id": "keep"}, {"id": "dup"}, {"id": "eddy"}]}]}
    (work / "in" / "lot_001.json").write_text(json.dumps(lot), encoding="utf-8")
    out = {"groupes": [{"groupe": "g0001", "doublons": [
        {"garder": "keep", "retirer": ["dup", "eddy", "autre"], "raison": "republication"}]}]}
    (work / "out" / "lot_001_out.json").write_text(json.dumps(out), encoding="utf-8")

    stats = ad.ingest(dry_run=False, db_path=db_path, work_dir=work)
    assert stats["retirees"] == 1
    assert stats["refusees_decision_eddy"] == 1
    assert stats["refusees_hors_groupe"] == 1
    fresh = Database(str(db_path))
    dup = fresh.get_job("dup")
    assert dup["status"] == STATUS_IGNORED and dup["rejection_reason"] == DUPLICATE_REASON
    assert "doublon de keep" in dup["user_notes"]
    assert fresh.get_job("eddy")["status"] == STATUS_APPLIED
    assert fresh.get_job("autre")["status"] == STATUS_NEW
