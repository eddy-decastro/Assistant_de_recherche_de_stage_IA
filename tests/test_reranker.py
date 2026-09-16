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
from src.constants import (
    DEFAULT_SUB_SCORE,
    SUB_SCORE_KEYS,
    VERDICT_EXCELLENT,
    VERDICT_MIXED,
    VERDICT_OFF_TOPIC,
)
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
    "reasoning": "Stage de R&D pertinent, calendrier aligné, encadrement recherche.",
    "hard_cap_triggered": None,
    "sub_scores": {
        "modeling_depth": 5,
        "mentorship_team": 4,
        "career_leverage": 4,
        "pfe_compatibility": 5,
    },
    "match_reasons": ["Modélisation GNN avancée", "Équipe de recherche"],
    "red_flags": ["Périmètre encore flou"],
    "tech_stack_detected": ["PyTorch", "Python", "SQL"],
    "verdict": "EXCELLENT",
    "rerank_score": 88,
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
    assert result["sub_scores"] == VALID_PAYLOAD["sub_scores"]
    assert result["hard_cap_triggered"] is None
    assert result["reasoning"] == VALID_PAYLOAD["reasoning"]
    print("[OK] parsing JSON valide (score, verdict, sous-scores, raisons, red flags, stack)")


def test_alias_verdict_et_score_borne() -> None:
    payload = {"rerank_score": 150, "verdict": "mitige", "match_reasons": "une seule raison"}
    judge = LLMJudge(load_config(), api_key="test-key", client=_mock_client(payload))
    result = judge.judge(SAMPLE_JOB)
    assert result["rerank_score"] == 100, "Le score doit être borné à 100."
    # Alignement : un verdict incohérent avec le score (mitigé pour 100) est corrigé.
    assert result["verdict"] == VERDICT_EXCELLENT, result
    assert result["match_reasons"] == ["une seule raison"], "Une string doit devenir une liste."
    assert result["sub_scores"] == {key: DEFAULT_SUB_SCORE for key in SUB_SCORE_KEYS}, (
        "Sous-scores absents -> valeurs neutres par défaut."
    )
    print("[OK] alias de verdict + score borné + alignement verdict + normalisation")


def test_sous_scores_partiels_defauts() -> None:
    """Champ de sous-score manquant -> valeur neutre ; hors bornes -> clampé."""
    payload = {
        "rerank_score": 70,
        "verdict": "BON",
        "sub_scores": {"modeling_depth": 4, "career_leverage": 9},
    }
    judge = LLMJudge(load_config(), api_key="test-key", client=_mock_client(payload))
    sub = judge.judge(SAMPLE_JOB)["sub_scores"]
    assert sub["modeling_depth"] == 4
    assert sub["career_leverage"] == 5, "9 doit être clampé à 5."
    assert sub["mentorship_team"] == DEFAULT_SUB_SCORE, "Champ manquant -> défaut 3."
    assert sub["pfe_compatibility"] == DEFAULT_SUB_SCORE

    # sub_scores non-dictionnaire -> défauts partout.
    payload2 = {"rerank_score": 70, "sub_scores": "pas-un-dict"}
    result2 = LLMJudge(load_config(), api_key="test-key", client=_mock_client(payload2)).judge(SAMPLE_JOB)
    assert result2["sub_scores"] == {key: DEFAULT_SUB_SCORE for key in SUB_SCORE_KEYS}
    print("[OK] sous-scores : valeurs manquantes / hors bornes normalisées")


def test_hard_cap_plafonne_le_score() -> None:
    """Un verrou bloquant plafonne le score et force un verdict aligné."""
    payload = {
        "rerank_score": 88,
        "verdict": "EXCELLENT",
        "hard_cap_triggered": "Reporting / dashboards BI",
        "sub_scores": {"modeling_depth": 1, "mentorship_team": 3, "career_leverage": 3, "pfe_compatibility": 5},
    }
    result = LLMJudge(load_config(), api_key="test-key", client=_mock_client(payload)).judge(SAMPLE_JOB)
    assert result["rerank_score"] == 20, result  # plafond reporting/BI
    assert result["verdict"] == VERDICT_OFF_TOPIC, result
    assert result["hard_cap_triggered"] == "Reporting / dashboards BI"
    print("[OK] hard cap reporting -> score plafonné à 20, verdict aligné")


def test_hard_cap_localisation() -> None:
    payload = {"rerank_score": 80, "verdict": "BON", "hard_cap_triggered": "Localisation hors Île-de-France"}
    result = LLMJudge(load_config(), api_key="test-key", client=_mock_client(payload)).judge(SAMPLE_JOB)
    assert result["rerank_score"] == 25
    assert result["verdict"] == VERDICT_OFF_TOPIC
    print("[OK] hard cap localisation -> score plafonné à 25")


def test_hard_cap_null_aucun_plafond() -> None:
    payload = {"rerank_score": 88, "verdict": "EXCELLENT", "hard_cap_triggered": None}
    result = LLMJudge(load_config(), api_key="test-key", client=_mock_client(payload)).judge(SAMPLE_JOB)
    assert result["rerank_score"] == 88
    assert result["hard_cap_triggered"] is None
    print("[OK] hard_cap_triggered null -> aucun plafond")


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
            sub_scores=result["sub_scores"],
            hard_cap_triggered=result["hard_cap_triggered"],
        )

        assert db.count_ranked() == 1
        assert db.get_unranked_jobs() == [], "L'offre evaluee ne doit plus etre candidate."

        row = db.get_jobs()[0]
        assert row["rerank_score"] == 88
        assert row["verdict"] == VERDICT_EXCELLENT
        assert row["match_reasons"] == VALID_PAYLOAD["match_reasons"]
        assert row["tech_stack"] == VALID_PAYLOAD["tech_stack_detected"]
        assert row["sub_scores"] == VALID_PAYLOAD["sub_scores"]
        assert row["hard_cap_triggered"] is None
        db.engine.dispose()
    print("[OK] persistance : update_rerank + get_unranked_jobs + get_jobs (JSON decode)")


def test_persistance_sous_scores_et_verrou() -> None:
    """Les sous-scores (JSON) et le verrou bloquant sont persistés et relus."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "test.db")
        db.upsert_job(SAMPLE_JOB)
        job_id = db.get_jobs()[0]["id"]
        sub = {"modeling_depth": 2, "mentorship_team": 4, "career_leverage": 3, "pfe_compatibility": 1}
        assert db.update_rerank(
            job_id, 20, VERDICT_OFF_TOPIC, [], ["reporting"], ["Power BI"],
            sub_scores=sub, hard_cap_triggered="Reporting / dashboards BI",
        ) is True
        row = db.get_jobs()[0]
        assert row["sub_scores"] == sub
        assert row["hard_cap_triggered"] == "Reporting / dashboards BI"
        assert row["tech_stack"] == ["Power BI"]
        db.engine.dispose()
    print("[OK] persistance : sous-scores + verrou bloquant (JSON + colonne)")


def test_parsing_tolerant_et_raisonnement() -> None:
    """Formes réelles du LLM tolérées : score « 85/100 » et clés de sous-scores variantes.

    Un modèle qui répond ``85/100`` ou ``modelingDepth`` ne doit pas faire perdre
    son jugement (repli silencieux sur le score de l'étape 1) ni un sous-score.
    """
    payload = {
        "reasoning": "Calendrier aligné, mission de modélisation réelle, encadrement senior.",
        "rerank_score": "85/100",
        "verdict": "EXCELLENT",
        "sub_scores": {
            "modelingDepth": "4/5",
            "mentorship_team": 5,
            "career-leverage": 4.0,
            "pfe compatibility": "5",
        },
    }
    result = LLMJudge(load_config(), api_key="test-key", client=_mock_client(payload)).judge(SAMPLE_JOB)
    assert result["rerank_score"] == 85, result
    assert result["sub_scores"] == {
        "modeling_depth": 4,
        "mentorship_team": 5,
        "career_leverage": 4,
        "pfe_compatibility": 5,
    }, result["sub_scores"]
    assert result["reasoning"] == payload["reasoning"]

    # « Score : 72 » (préfixe textuel) et sous-score hors bornes restent exploitables.
    payload2 = {"rerank_score": "Score : 72", "sub_scores": {"modeling_depth": 9}}
    result2 = LLMJudge(load_config(), api_key="test-key", client=_mock_client(payload2)).judge(SAMPLE_JOB)
    assert result2["rerank_score"] == 72, result2
    assert result2["sub_scores"]["modeling_depth"] == 5, "Hors bornes -> clampé."
    print("[OK] parsing tolérant : « 85/100 », clés camelCase/kebab et clamping")


def test_persistance_reasoning() -> None:
    """Le raisonnement (produit AVANT le score) est persisté et relu."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "test.db")
        db.upsert_job(SAMPLE_JOB)
        job_id = db.get_jobs()[0]["id"]
        assert db.update_rerank(
            job_id,
            88,
            VERDICT_EXCELLENT,
            ["Modélisation GNN avancée"],
            [],
            ["PyTorch"],
            sub_scores=VALID_PAYLOAD["sub_scores"],
            hard_cap_triggered=None,
            reasoning=VALID_PAYLOAD["reasoning"],
        ) is True
        row = db.get_jobs()[0]
        assert row["reasoning"] == VALID_PAYLOAD["reasoning"], row
        assert row["sub_scores"] == VALID_PAYLOAD["sub_scores"]

        # clear_rerank efface aussi le raisonnement (remise à zéro cohérente).
        assert db.clear_rerank(1) == [job_id]
        assert db.get_jobs()[0]["reasoning"] is None
        db.engine.dispose()
    print("[OK] persistance : reasoning (trace de la décision) + remise à zéro")


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
        assert candidates[0]["sub_scores"] == {} and candidates[0]["hard_cap_triggered"] is None
        assert db.clear_rerank(0) == []
        db.engine.dispose()
    print("[OK] ré-évaluation forcée : clear_rerank libère le Top-N pour un nouveau jugement")


def test_unranked_exclut_les_offres_ecartees() -> None:
    """Le juge LLM ne dépense pas de tokens sur une offre écartée par filtrage métier."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "test.db")
        db.upsert_job({**SAMPLE_JOB, "title": "Offre écartée", "url": "https://x/rej"})
        db.upsert_job({**SAMPLE_JOB, "title": "Offre valide", "url": "https://x/ok"})
        rejected_id = next(job["id"] for job in db.get_jobs() if job["title"] == "Offre écartée")
        assert db.reject_job(rejected_id, "contrat incompatible (« freelance »)") is True

        candidates = db.get_unranked_jobs(limit=10)
        assert [job["title"] for job in candidates] == ["Offre valide"], candidates
        db.engine.dispose()
    print("[OK] rerank : les offres écartées ne sont plus candidates au juge LLM")


def main() -> None:
    test_parsing_valide()
    test_alias_verdict_et_score_borne()
    test_sous_scores_partiels_defauts()
    test_parsing_tolerant_et_raisonnement()
    test_hard_cap_plafonne_le_score()
    test_hard_cap_localisation()
    test_hard_cap_null_aucun_plafond()
    test_json_invalide_fallback()
    test_cle_absente_fallback()
    test_erreur_http_fallback()
    test_prompt_sans_score_bi_encoder()
    test_clear_rerank_revaluation()
    test_unranked_exclut_les_offres_ecartees()
    test_persistance_rerank()
    test_persistance_sous_scores_et_verrou()
    test_persistance_reasoning()
    test_tri_par_rerank()
    print("\n[OK] test_reranker.py : tous les tests passent.")


if __name__ == "__main__":
    main()
