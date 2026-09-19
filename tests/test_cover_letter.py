"""Tests pour le générateur de lettres de motivation."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.matching.cover_letter import CoverLetterGenerator, get_cv_text, SYSTEM_PROMPT

SAMPLE_JOB = {
    "title": "Stage R&D Deep Learning / NLP",
    "company": "Mistral AI",
    "location": "Paris (75)",
    "description": "Recherche stagiaire de fin d'études pour travailler sur des LLM et des architectures de transformers.",
}


def test_get_cv_text() -> None:
    """Vérifie que le texte du CV est chargé depuis data/cv_eddy.txt."""
    cv = get_cv_text()
    assert isinstance(cv, str)
    assert len(cv.strip()) > 0
    assert "EXPÉRIENCE" in cv or "COMPÉTENCES" in cv or "PROFIL" in cv


def test_generator_prompt_building() -> None:
    """Vérifie la construction du prompt utilisateur."""
    generator = CoverLetterGenerator(api_key="test-key")
    prompt = generator._build_user_prompt(SAMPLE_JOB, "Mon CV de test")
    assert "Mistral AI" in prompt
    assert "Stage R&D Deep Learning / NLP" in prompt
    assert "Mon CV de test" in prompt


def test_generator_missing_api_key() -> None:
    """Vérifie le message d'avertissement lorsque la clé est absente."""
    generator = CoverLetterGenerator(api_key="")
    result = generator.generate(SAMPLE_JOB)
    assert "GEMINI_API_KEY manquante" in result


def test_generator_mock_client() -> None:
    """Vérifie la génération avec un client mocké."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "EDDY\nÉlève-ingénieur\n\nObjet : Candidature au stage\n\nMadame, Monsieur..."
    mock_client.models.generate_content.return_value = mock_response

    generator = CoverLetterGenerator(api_key="valid-key", client=mock_client)
    result = generator.generate(SAMPLE_JOB, cv_text="CV mock")

    assert "Objet : Candidature" in result
    mock_client.models.generate_content.assert_called_once()
    call_kwargs = mock_client.models.generate_content.call_args[1]
    assert call_kwargs["config"].system_instruction == SYSTEM_PROMPT
