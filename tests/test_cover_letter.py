"""Tests pour le générateur de lettres de motivation."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from google.genai.errors import APIError
from src.matching.cover_letter import (
    CoverLetterGenerator,
    get_cv_text,
    SYSTEM_PROMPT,
    generate_algorithmic_cover_letter,
    is_english_text,
)

SAMPLE_JOB = {
    "title": "Stage R&D Deep Learning / NLP",
    "company": "Mistral AI",
    "location": "Paris (75)",
    "description": "Recherche stagiaire de fin d'études pour travailler sur des LLM et des architectures de transformers.",
}

# Coordonnées fictives : les vraies vivent dans config.local.yaml (non versionné).
FAKE_CANDIDATE = {
    "name": "Eddy DE CASTRO",
    "phone": "01 23 45 67 89",
    "email": "candidat@example.com",
    "linkedin": "https://www.linkedin.com/in/eddy-de-castro/",
}


def test_get_cv_text() -> None:
    """get_cv_text renvoie toujours une chaîne (vide si aucun CV n'est configuré)."""
    assert isinstance(get_cv_text(), str)


def test_generator_prompt_building() -> None:
    """Vérifie la construction du prompt utilisateur."""
    generator = CoverLetterGenerator(api_key="test-key")
    prompt = generator._build_user_prompt(SAMPLE_JOB, "Mon CV de test")
    assert "Mistral AI" in prompt
    assert "Stage R&D Deep Learning / NLP" in prompt
    assert "Mon CV de test" in prompt


def test_generator_missing_api_key() -> None:
    """Vérifie le message d'avertissement lorsque la clé est absente."""
    generator = CoverLetterGenerator(api_key="", deepseek_api_key="")
    result = generator.generate(SAMPLE_JOB)
    assert "manquante" in result


def test_generator_mock_client() -> None:
    """Vérifie la génération avec un client mocké."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "EDDY\nÉlève-ingénieur\n\nObjet : Candidature au stage\n\nMadame, Monsieur..."
    mock_client.models.generate_content.return_value = mock_response

    generator = CoverLetterGenerator(api_key="valid-key", deepseek_api_key="", client=mock_client)
    result = generator.generate(SAMPLE_JOB, cv_text="CV mock")

    assert "Objet : Candidature" in result
    mock_client.models.generate_content.assert_called_once()
    call_kwargs = mock_client.models.generate_content.call_args[1]
    assert call_kwargs["config"].system_instruction == generator.system_prompt


def test_candidate_info_loading() -> None:
    """Vérifie le chargement des coordonnées du candidat depuis la configuration."""
    from src.matching.cover_letter import get_candidate_info
    info = get_candidate_info({"candidate": FAKE_CANDIDATE})
    assert "Eddy" in info["name"]
    assert info["phone"] == "01 23 45 67 89"
    assert info["email"] == "candidat@example.com"
    assert "linkedin.com/in/eddy-de-castro" in info["linkedin"]


def test_candidate_info_defaults_have_no_private_contact() -> None:
    """Sans configuration privée, aucun téléphone ni email n'est codé en dur."""
    from src.matching.cover_letter import get_candidate_info
    info = get_candidate_info({})
    assert info["phone"] == ""
    assert info["email"] == ""


def test_system_prompt_content() -> None:
    """Vérifie que le prompt système intègre les coordonnées et bannit les crochets."""
    from src.matching.cover_letter import build_system_prompt
    prompt = build_system_prompt(FAKE_CANDIDATE)
    assert "Eddy DE CASTRO" in prompt
    assert "01 23 45 67 89" in prompt
    assert "candidat@example.com" in prompt
    assert "ZÉRO CROCHET" in prompt


def test_postprocess_letter_brackets_cleanup() -> None:
    """Vérifie que la fonction de post-traitement élimine les crochets résiduels."""
    generator = CoverLetterGenerator(config={"candidate": FAKE_CANDIDATE}, api_key="test-key")
    raw = "À l'attention de [Nom du Responsable],\nContact : [Téléphone] | [Email]\nDispo : [avril 2027]"
    cleaned = generator._postprocess_letter(raw)
    assert "[Nom du Responsable]" not in cleaned
    assert "l'équipe recrutement" in cleaned
    assert "01 23 45 67 89" in cleaned
    assert "candidat@example.com" in cleaned
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


def test_is_english_text_detection() -> None:
    """Vérifie la détection fiable de l'anglais vs français."""
    assert is_english_text("We are looking for an ambitious intern with strong deep learning skills and experience.") is True
    assert is_english_text("Stage de fin d'études en recherche R&D au sein de notre équipe pour un élève-ingénieur.") is False


def test_algorithmic_cover_letter_french() -> None:
    """Vérifie que la lettre de secours algorithmique française est complète et sans crochets."""
    letter = generate_algorithmic_cover_letter(SAMPLE_JOB)
    assert "Objet : Candidature au stage de fin d'études — Stage R&D Deep Learning / NLP" in letter
    assert "Madame, Monsieur," in letter
    assert "Mines de Saint-Étienne" in letter
    assert "M2 Mathématiques en Action" in letter
    assert "IMT Mines Alès" in letter
    assert "Licence 3 de Mathématiques Générales à l'Université de Montpellier" in letter
    assert "UPC" in letter or "Barcelone" in letter
    assert "MedStay-CI" in letter
    assert "CinéFilm IA" in letter
    assert "avril 2027" in letter
    assert "Eddy DE CASTRO" in letter
    assert "[" not in letter and "]" not in letter
    words = len(letter.split())
    assert 400 <= words <= 700


def test_algorithmic_cover_letter_english() -> None:
    """Vérifie que la lettre de secours pour une offre en anglais est rédigée en anglais de haut niveau."""
    english_job = {
        "title": "Research Intern - Machine Learning & Foundation Models",
        "company": "DeepMind",
        "location": "London / Paris",
        "description": "We are seeking a talented research intern with strong mathematical foundations and PyTorch skills to work on frontier models.",
    }
    letter = generate_algorithmic_cover_letter(english_job)
    assert "Subject: Application for End-of-Studies Internship" in letter
    assert "Dear Hiring Team," in letter
    assert "Mines de Saint-Étienne" in letter
    assert "University of Montpellier" in letter
    assert "Barcelona" in letter
    assert "April 2027" in letter
    assert "Sincerely," in letter
    assert "Eddy DE CASTRO" in letter
    assert "[" not in letter and "]" not in letter


def test_generator_fallback_on_503_error() -> None:
    """Vérifie que le générateur bascule sur la lettre de secours si les deux APIs sont indisponibles."""
    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = APIError(
        503,
        {"error": {"code": 503, "message": "This model is currently experiencing high demand. Spikes in demand are usually temporary. Please try again later."}},
    )

    generator = CoverLetterGenerator(api_key="valid-key", client=mock_client, deepseek_api_key="")
    result = generator.generate(SAMPLE_JOB, cv_text="CV de test")

    assert not result.startswith("⚠️ Erreur API Gemini")
    assert "Objet : Candidature au stage de fin d'études" in result
    assert "Mines de Saint-Étienne" in result
    assert generator.last_source == "fallback"


def test_generator_gemini_fallback_on_deepseek_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Vérifie que le générateur bascule sur Gemini si DeepSeek renvoie une erreur."""
    # Simuler une erreur de DeepSeek
    def mock_post(*args, **kwargs):
        raise Exception("DeepSeek API down")
    monkeypatch.setattr("requests.post", mock_post)

    # Simuler le succès de Gemini
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "Objet : Candidature Gemini\n\nMadame, Monsieur,\nRédigé par Gemini."
    mock_client.models.generate_content.return_value = mock_response

    generator = CoverLetterGenerator(api_key="valid-key", client=mock_client, deepseek_api_key="mock-ds-key")
    result = generator.generate(SAMPLE_JOB, cv_text="CV de test")

    assert "Candidature Gemini" in result
    assert generator.last_source == "gemini"




