"""Le garde-fou de tests/conftest.py bloque bien tout appel LLM réel."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
import pytest
import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Constantes seulement : la classe d'erreur levée vient du module conftest chargé par pytest.
from tests.conftest import LLM_KEY_ENV_VARS, is_llm_url  # noqa: E402


def test_cles_llm_videes_meme_apres_chargement_du_env() -> None:
    from src.env import load_env_file

    load_env_file()  # le vrai .env ne doit pas pouvoir remplir les clés
    for name in LLM_KEY_ENV_VARS:
        assert os.environ.get(name) == ""


def test_is_llm_url() -> None:
    assert is_llm_url("https://api.deepseek.com/chat/completions")
    assert is_llm_url("https://generativelanguage.googleapis.com/v1beta/models/x:generateContent")
    assert is_llm_url("https://api.groq.com/openai/v1/chat/completions")
    assert not is_llm_url("https://www.linkedin.com/jobs/view/1")
    assert not is_llm_url("http://testserver/api/jobs")
    assert not is_llm_url("https://evil-api.deepseek.com.example.org/")


@pytest.mark.allow_blocked_llm_attempt
def test_requests_vers_un_llm_bloque() -> None:
    with pytest.raises(requests.ConnectionError, match="Appel LLM réel bloqué"):
        requests.post("https://api.deepseek.com/chat/completions", json={}, timeout=1)


@pytest.mark.allow_blocked_llm_attempt
def test_httpx_vers_un_llm_bloque() -> None:
    with httpx.Client() as client, pytest.raises(httpx.ConnectError):
        client.post("https://generativelanguage.googleapis.com/v1beta/models/m:generateContent", json={})


@pytest.mark.allow_blocked_llm_attempt
def test_juges_et_lettre_sans_cle_ni_reseau() -> None:
    """Sans mock, les composants LLM du projet ne trouvent aucune clé : aucun appel possible."""
    from src.matching.cover_letter import CoverLetterGenerator
    from src.matching.scoring_v4 import LLMJudgeV4

    judge = LLMJudgeV4(config={"scoring_v4": {}, "llm": {}})
    assert not judge.available
    gen = CoverLetterGenerator()
    assert not gen.api_key
    assert not gen.deepseek_api_key
