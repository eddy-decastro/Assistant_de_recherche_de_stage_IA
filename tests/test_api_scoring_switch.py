"""Interrupteur llm.scoring_api : les API ne notent plus, elles ne font que les lettres.

Aucun appel réseau, aucune base réelle.
"""
from __future__ import annotations

import pytest

from src.matching.llm_judge import LLMJudge
from src.matching.llm_providers import api_scoring_enabled
from src.matching.scoring_v4 import LLMJudgeV4

pytestmark = pytest.mark.real_scoring_switch


def _cfg(scoring_api: bool | None) -> dict:
    llm = {"provider": "deepseek", "model": "deepseek-chat"}
    if scoring_api is not None:
        llm["scoring_api"] = scoring_api
    return {"llm": llm, "scoring_v4": {"enabled": True}}


def test_switch_defaults_to_enabled() -> None:
    assert api_scoring_enabled({}) is True
    assert api_scoring_enabled(_cfg(None)) is True
    assert api_scoring_enabled(_cfg(False)) is False


def test_v4_judge_unavailable_and_never_calls_api_when_off() -> None:
    judge = LLMJudgeV4(_cfg(False), api_key="x", groq_api_key="", deepseek_api_key="x")
    assert judge.available is False
    res = judge.judge({"id": "1", "title": "Stage Data Scientist", "company": "ACME", "description": "ML"})
    assert res["v4_status"] == "NON_NOTE"
    assert "scoring_api" in res["v4_flags_json"]


def test_v4_judge_available_when_on() -> None:
    judge = LLMJudgeV4(_cfg(True), api_key="x", groq_api_key="", deepseek_api_key="")
    assert judge.available is True


def test_v3_judge_unavailable_when_off(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    assert LLMJudge(_cfg(False)).available is False


def test_real_config_keeps_api_for_letters_only() -> None:
    from src.config import load_config

    assert api_scoring_enabled(load_config()) is False
