"""Garde-fous communs à toute la suite de tests.

Aucun test ne doit appeler un vrai LLM (DeepSeek, Gemini, Groq…) : les clés du
``.env`` sont actives sur le poste d'Eddy, et un appel réel coûte du quota, prend
du temps et rend les tests non reproductibles. Deux barrières, appliquées à
chaque test (fixture ``autouse``) :

1. Les variables de clés LLM sont posées à vide. ``load_env_file`` n'écrase
   jamais une variable déjà présente : le ``.env`` ne peut donc plus les remplir.
   Un test qui veut simuler une clé la pose lui-même (``monkeypatch.setenv``)
   et doit alors mocker l'appel.
2. Toute requête HTTP (``requests`` ou ``httpx``, dont le SDK ``google-genai``)
   vers un hôte de fournisseur LLM est bloquée : elle lève une erreur réseau
   (le code applicatif bascule sur ses replis) et le test échoue à la fin, même
   si l'erreur a été avalée par le code.
"""
from __future__ import annotations

from urllib.parse import urlsplit

import httpx
import pytest
import requests
import requests.adapters

# Variables de clés lues par src/matching/* (llm_judge, scoring_v4, cover_letter…).
LLM_KEY_ENV_VARS: tuple[str, ...] = (
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GROQ_API_KEY",
    "DEEPSEEK_API_KEY",
    "MISTRAL_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
)

# Hôtes des fournisseurs LLM (PROVIDER_URLS de llm_judge / scoring_v4, SDK Gemini).
LLM_HOSTS: tuple[str, ...] = (
    "generativelanguage.googleapis.com",
    "aiplatform.googleapis.com",
    "api.deepseek.com",
    "api.groq.com",
    "api.mistral.ai",
    "openrouter.ai",
    "api.openai.com",
    "api.anthropic.com",
)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "allow_blocked_llm_attempt: le test vérifie volontairement le blocage d'un appel LLM "
        "(la tentative reste bloquée, mais ne fait pas échouer le test).",
    )


class RealLLMCallBlocked(requests.ConnectionError):
    """Appel réseau vers un fournisseur LLM intercepté pendant les tests."""


def is_llm_url(url: object) -> bool:
    """Vrai si ``url`` vise un fournisseur LLM (hôte exact ou sous-domaine)."""
    host = (urlsplit(str(url)).hostname or "").casefold()
    return any(host == h or host.endswith("." + h) for h in LLM_HOSTS)


@pytest.fixture(autouse=True)
def _block_real_llm_calls(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest):
    """Vide les clés LLM et intercepte toute requête vers un fournisseur LLM."""
    for name in LLM_KEY_ENV_VARS:
        monkeypatch.setenv(name, "")

    attempts: list[str] = []

    original_adapter_send = requests.adapters.HTTPAdapter.send

    def guarded_adapter_send(self, req, *args, **kwargs):  # type: ignore[no-untyped-def]
        if is_llm_url(req.url):
            attempts.append(str(req.url))
            raise RealLLMCallBlocked(f"Appel LLM réel bloqué pendant les tests : {req.url}")
        return original_adapter_send(self, req, *args, **kwargs)

    original_httpx_send = httpx.Client.send
    original_httpx_async_send = httpx.AsyncClient.send

    def guarded_httpx_send(self, req, *args, **kwargs):  # type: ignore[no-untyped-def]
        if is_llm_url(req.url):
            attempts.append(str(req.url))
            raise httpx.ConnectError(f"Appel LLM réel bloqué pendant les tests : {req.url}", request=req)
        return original_httpx_send(self, req, *args, **kwargs)

    async def guarded_httpx_async_send(self, req, *args, **kwargs):  # type: ignore[no-untyped-def]
        if is_llm_url(req.url):
            attempts.append(str(req.url))
            raise httpx.ConnectError(f"Appel LLM réel bloqué pendant les tests : {req.url}", request=req)
        return await original_httpx_async_send(self, req, *args, **kwargs)

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", guarded_adapter_send)
    monkeypatch.setattr(httpx.Client, "send", guarded_httpx_send)
    monkeypatch.setattr(httpx.AsyncClient, "send", guarded_httpx_async_send)

    yield attempts

    if attempts and "allow_blocked_llm_attempt" not in request.keywords:
        pytest.fail(
            "Ce test a tenté un appel LLM réel (bloqué) : "
            + ", ".join(sorted(set(attempts)))
            + ". Mocker l'appel (ex. patch de _generate_short_llm_once ou d'un faux client).",
            pytrace=False,
        )
