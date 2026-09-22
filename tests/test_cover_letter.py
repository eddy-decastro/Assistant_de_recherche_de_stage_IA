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


def test_candidate_info_loading() -> None:
    """Vérifie le chargement des coordonnées du candidat."""
    from src.matching.cover_letter import get_candidate_info
    info = get_candidate_info()
    assert "Eddy" in info["name"]
    assert "06 98 82 44 85" in info["phone"]
    assert "eddyprepa123@gmail.com" in info["email"]
    assert "linkedin.com/in/eddy-de-castro" in info["linkedin"]


def test_system_prompt_content() -> None:
    """Vérifie que le prompt système intègre les coordonnées et bannit les crochets."""
    from src.matching.cover_letter import build_system_prompt
    prompt = build_system_prompt()
    assert "Eddy DE CASTRO" in prompt
    assert "06 98 82 44 85" in prompt
    assert "eddyprepa123@gmail.com" in prompt
    assert "ZÉRO CROCHET" in prompt


def test_postprocess_letter_brackets_cleanup() -> None:
    """Vérifie que la fonction de post-traitement élimine les crochets résiduels."""
    generator = CoverLetterGenerator(api_key="test-key")
    raw = "À l'attention de [Nom du Responsable],\nContact : [Téléphone] | [Email]\nDispo : [avril 2027]"
    cleaned = generator._postprocess_letter(raw)
    assert "[Nom du Responsable]" not in cleaned
    assert "l'équipe recrutement" in cleaned
    assert "06 98 82 44 85" in cleaned
    assert "eddyprepa123@gmail.com" in cleaned
    assert "avril 2027" in cleaned
    assert "[" not in cleaned
    assert "]" not in cleaned


def test_pdf_export_generation() -> None:
    """Vérifie que le générateur de PDF produit un fichier binaire PDF valide."""
    from src.matching.pdf_exporter import generate_cover_letter_pdf
    letter_text = (
        "Objet : Candidature — Stage R&D Deep Learning / NLP\n\n"
        "Madame, Monsieur,\n\n"
        "Actuellement en dernière année à l'École des Mines de Saint-Étienne, je vous présente ma candidature.\n\n"
        "Cordialement,\n"
        "Eddy DE CASTRO"
    )
    pdf_bytes = generate_cover_letter_pdf(letter_text, job=SAMPLE_JOB)
    assert isinstance(pdf_bytes, bytes)
    assert len(pdf_bytes) > 500
    assert pdf_bytes.startswith(b"%PDF-")


def test_generator_custom_instruction() -> None:
    """Vérifie que la consigne spécifique est bien injectée dans le prompt utilisateur."""
    generator = CoverLetterGenerator(api_key="test-key")
    prompt = generator._build_user_prompt(
        SAMPLE_JOB,
        "CV de test",
        custom_instruction="Mettre l'accent sur les Transformers et la vision par ordinateur",
    )
    assert "CONSIGNES SPÉCIFIQUES DU CANDIDAT" in prompt
    assert "Mettre l'accent sur les Transformers et la vision par ordinateur" in prompt


