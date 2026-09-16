"""Tests du reranker LLM (LLMJudge) et de la persistance de l'étape 2."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_config
from src.constants import VERDICT_EXCELLENT, VERDICT_MIXED
from src.matching.llm_judge import LLMJudge, verdict_from_score
from src.storage.database import Database

SAMPLE_JOB = {
    "title": "Stage Data Scientist — Graph ML",
    "company": "Doctolib",
    "location": "Paris",
    "url": "https://example.com/job/gml",
    "description": "PyTorch, GNN, optimisation, publication possible.",
    "final_score": 42.0,
    "company_tier": 1,
}

VALID_PAYLOAD = {
    "rerank_score": 88,
    "verdict": "EXCELLENT",
    "match_reasons": ["Modélisation GNN avancée", "Équipe de recherche"],
    "red_flags": ["Périmètre encore flou"],
    "tech_stack_detected": ["PyTorch", "Python", "SQL"],
}


def _mock_client(payload: dict, status: int = 200) -> httpx.Client:
    """Client httpx simulant une réponse DeepSeek (sans réseau)."""
    def handler(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status, json={"error": "boom"})
        body = {"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]}
        return httpx.Response(200, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_parsing_valide() -> None:
    judge = LLMJudge(load_config(), api_key="test-key", client=_mock_client(VALID_PAYLOAD))
    result = judge.judge(SAMPLE_JOB)
    assert result["rerank_score"] == 88, result
    assert result["verdict"] == VERDICT_EXCELLENT
    assert result["match_reasons"] == ["Modélisation GNN avancée", "Équipe de recherche"]
    assert result["red_flags"] == ["Périmètre encore flou"]
    assert result["tech_stack"] == ["PyTorch", "Python", "SQL"]
    print("[OK] parsing JSON valide (score, verdict, raisons, red flags, stack)")


def test_alias_verdict_et_score_borne() -> None:
    payload = {"rerank_score": 150, "verdict": "mitige", "match_reasons": "une seule raison"}
    judge = LLMJudge(load_config(), api_key="test-key", client=_mock_client(payload))
    result = judge.judge(SAMPLE_JOB)
    assert result["rerank_score"] == 100, "Le score doit être borné à 100."
    assert result["verdict"] == VERDICT_MIXED, result
    assert result["match_reasons"] == ["une seule raison"], "Une string doit devenir une liste."
    print("[OK] alias de verdict + score borné + normalisation des listes")


def test_json_invalide_fallback() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = {"choices": [{"message": {"content": "ceci n'est pas du JSON"}}]}
        return httpx.Response(200, json=body)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    judge = LLMJudge(load_config(), api_key="test-key", client=client)
    result = judge.judge(SAMPLE_JOB)
    assert result["rerank_score"] == int(SAMPLE_JOB["final_score"]), result
    assert result["red_flags"], "Un red flag doit signaler l'échec de parsing."
    print("[OK] JSON invalide -> fallback sur le score initial")


def test_cle_absente_fallback() -> None:
    judge = LLMJudge(load_config(), api_key="")  # force l'absence de clé
    assert judge.available is False
    result = judge.judge(SAMPLE_JOB)
    assert result["rerank_score"] == int(SAMPLE_JOB["final_score"])
    assert result["verdict"] == verdict_from_score(SAMPLE_JOB["final_score"])
    print("[OK] cle API absente -> fallback defensif")


def test_erreur_http_fallback() -> None:
    judge = LLMJudge(load_config(), api_key="test-key", client=_mock_client({}, status=429))
    result = judge.judge(SAMPLE_JOB)
    assert result["rerank_score"] == int(SAMPLE_JOB["final_score"])
    assert any("429" in flag for flag in result["red_flags"]), result
    print("[OK] erreur HTTP 429 -> fallback avec red flag")


def test_persistance_rerank() -> None:
    config = load_config()
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "test.db")
        db.upsert_job(SAMPLE_JOB)
        assert db.count_ranked() == 0

        candidates = db.get_unranked_jobs(limit=10)
        assert len(candidates) == 1, "L'offre non evaluee doit etre proposee au rerank."
        job_id = candidates[0]["id"]

        judge = LLMJudge(config, api_key="test-key", client=_mock_client(VALID_PAYLOAD))
        result = judge.judge(SAMPLE_JOB)
        db.update_rerank(
            job_id,
            result["rerank_score"],
            result["verdict"],
            result["match_reasons"],
            result["red_flags"],
            result["tech_stack"],
        )

        assert db.count_ranked() == 1
        assert db.get_unranked_jobs() == [], "L'offre evaluee ne doit plus etre candidate."

        row = db.get_jobs()[0]
        assert row["rerank_score"] == 88
        assert row["verdict"] == VERDICT_EXCELLENT
        assert row["match_reasons"] == VALID_PAYLOAD["match_reasons"]
        assert row["tech_stack"] == VALID_PAYLOAD["tech_stack_detected"]
        db.engine.dispose()
    print("[OK] persistance : update_rerank + get_unranked_jobs + get_jobs (JSON decode)")


def test_tri_par_rerank() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "test.db")
        db.upsert_job({**SAMPLE_JOB, "title": "Offre A", "url": "https://x/a", "final_score": 90.0})
        db.upsert_job({**SAMPLE_JOB, "title": "Offre B", "url": "https://x/b", "final_score": 50.0})

        assert db.get_jobs()[0]["title"] == "Offre A", "Tri initial par final_score."

        b_id = next(j["id"] for j in db.get_jobs() if j["title"] == "Offre B")
        db.update_rerank(b_id, 95, VERDICT_EXCELLENT, [], [], ["PyTorch"])
        assert db.get_jobs()[0]["title"] == "Offre B", "Le rerank_score doit primer."
        db.engine.dispose()
    print("[OK] tri : rerank_score prioritaire sur final_score (COALESCE)")


def test_prompt_sans_score_bi_encoder() -> None:
    """Le juge ne reçoit aucun score de l'étape 1 (suppression du biais d'ancrage)."""
    judge = LLMJudge(load_config(), api_key="test-key", client=_mock_client(VALID_PAYLOAD))
    messages = judge._build_messages(SAMPLE_JOB, cv_text="CV de test")

    assert messages[0]["role"] == "system" and messages[1]["role"] == "user"
    user_content = messages[1]["content"]
    assert "Score préliminaire" not in user_content, user_content
    assert "bi-encoder" not in user_content.casefold(), "Aucune ancre numérique ne doit fuiter."
    assert str(SAMPLE_JOB["final_score"]) not in user_content, user_content
    assert SAMPLE_JOB["title"] in user_content, "Le titre reste indispensable au jugement."
    assert SAMPLE_JOB["description"] in user_content, "La description complète doit être transmise."
    assert "CV de test" in user_content, "Le CV du candidat doit être transmis."

    # Une description absente est explicitement signalée (plus d'ambiguïté pour le juge).
    empty = judge._build_messages({**SAMPLE_JOB, "description": ""})
    assert "(description indisponible)" in empty[1]["content"], empty[1]["content"]
    print("[OK] prompt du juge : aucun score de l'étape 1 transmis (pas d'ancrage)")


def test_clear_rerank_revaluation() -> None:
    """clear_rerank rend les N meilleures offres à nouveau candidates au juge LLM."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "test.db")
        db.upsert_job({**SAMPLE_JOB, "title": "Offre A", "url": "https://x/a", "final_score": 90.0})
        db.upsert_job({**SAMPLE_JOB, "title": "Offre B", "url": "https://x/b", "final_score": 50.0})
        for job in db.get_jobs():
            db.update_rerank(job["id"], 70.0, VERDICT_MIXED, ["ancien verdict"], [], ["SQL"])
        assert db.count_ranked() == 2
        assert db.get_unranked_jobs() == [], "Les deux offres sont déjà jugées."

        reset_ids = db.clear_rerank(1)
        assert len(reset_ids) == 1, reset_ids
        assert db.count_ranked() == 1
        candidates = db.get_unranked_jobs(limit=5)
        assert len(candidates) == 1 and candidates[0]["title"] == "Offre A", candidates
        assert candidates[0]["red_flags"] == [] and candidates[0]["tech_stack"] == []
        assert db.clear_rerank(0) == []
        db.engine.dispose()
    print("[OK] ré-évaluation forcée : clear_rerank libère le Top-N pour un nouveau jugement")


def main() -> None:
    test_parsing_valide()
    test_alias_verdict_et_score_borne()
    test_json_invalide_fallback()
    test_cle_absente_fallback()
    test_erreur_http_fallback()
    test_prompt_sans_score_bi_encoder()
    test_clear_rerank_revaluation()
    test_persistance_rerank()
    test_tri_par_rerank()
    print("\n[OK] test_reranker.py : tous les tests passent.")


if __name__ == "__main__":
    main()
