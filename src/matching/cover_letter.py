"""Générateur de lettres de motivation personnalisées (Gemini).

Utilise le profil candidat (data/cv_eddy.txt) et la fiche de poste pour
produire une lettre de motivation complète, académique et formelle.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types
from google.genai.errors import APIError

from src.config import PROJECT_ROOT, load_config
from src.matching.llm_judge import load_env_file

DEFAULT_MODEL = "gemini-3.1-flash-lite"


def get_cv_text() -> str:
    """Charge le texte du CV par défaut (data/cv_eddy.txt)."""
    cv_path = PROJECT_ROOT / "data" / "cv_eddy.txt"
    if cv_path.exists():
        return cv_path.read_text(encoding="utf-8")
    return ""


SYSTEM_PROMPT = """Tu es un expert en recrutement et en rédaction de candidatures pour des élèves-ingénieurs de grandes écoles françaises (Mines Saint-Étienne).
Ton rôle est de rédiger une lettre de motivation complète, soignée, hautement personnalisée et formelle (style académique d'environ une page) pour une offre de stage spécifique, à partir du CV du candidat et de la fiche de poste.

RÈGLES IMPÉRATIVES DE RÉDACTION :
1. LANGUE :
   - Si la description du poste est rédigée en anglais, rédige l'intégralité de la lettre en anglais professionnel / académique.
   - Sinon, rédige en français soutenu et formel.

2. STRUCTURE DE LA LETTRE (environ 1 page standard) :
   - En-tête Expéditeur :
     EDDY
     Élève-ingénieur en dernière année — École des Mines de Saint-Étienne
     Spécialisation : Intelligence Artificielle & Data Science
     [Votre Adresse postale]
     [Votre Téléphone] | [Votre Adresse Email] | [Lien LinkedIn / GitHub]

   - En-tête Destinataire :
     À l'attention de [Nom du Responsable du recrutement / Nom de l'équipe]
     [Nom de l'Entreprise]
     [Adresse de l'Entreprise / Service concerné]

   - Objet clair et précis :
     Exemple : "Objet : Candidature au stage de fin d'études — [Intitulé exact de l'offre]"

   - Formule d'appel :
     "Madame, Monsieur," (ou le nom si identifié avec certitude).

   - Corps de texte en 3 à 4 paragraphes équilibrés et argumentés :
     a. L'ACCROCHE & L'ENTREPRISE (Vous) : Mentionner l'admiration ou l'intérêt marqué pour les projets, la réputation, les publications ou les défis techniques spécifiques de l'entreprise/du laboratoire. Montrer une excellente compréhension de la mission proposée.
     b. LE PROFIL & LES RÉALISATIONS (Moi) : Valoriser la solide formation d'ingénieur civil des Mines de Saint-Étienne. Mettre en avant 1 ou 2 projets techniques concrets du CV (ex. modélisation GNN sous PyTorch, pipelines ETL distribués, assistant RAG LLM) qui répondent directement aux besoins clés énoncés dans la description.
     c. LA COLLABORATION & LA VALEUR AJOUTÉE (Nous) : Expliquer concrètement comment le candidat compte s'intégrer, contribuer et résoudre les problématiques de l'équipe. Rappeler la disponibilité pour un stage de fin d'études de 6 mois à partir de mars 2026.

   - Formule de politesse formelle académique :
     Exemple : "Dans l'attente d'un échange au cours duquel je pourrai vous exposer plus en détail mes motivations et mon projet professionnel, je vous prie d'agréer, Madame, Monsieur, l'expression de mes salutations les plus distinguées."

   - Signature :
     EDDY

3. BALISES & PLACEHOLDERS :
   - Utilise impérativement des crochets bien visibles pour toute information manquante ou variable que le candidat doit personnaliser avant l'envoi (ex. : [Nom du Responsable], [Votre Téléphone], [Votre Adresse Email], [Adresse de l'Entreprise]).

4. TON & QUALITÉ :
   - Aucun cliché creux ("passionné et dynamique"). Utilise un vocabulaire technique précis, rigoureux et mesuré.
   - Reste fidèle aux faits du CV (ne pas inventer d'expériences ou de diplômes inexistants).
"""


class CoverLetterGenerator:
    """Générateur de lettre de motivation via Gemini."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        api_key: str | None = None,
        client: genai.Client | None = None,
    ) -> None:
        self.config = config or load_config()
        llm_cfg = self.config.get("llm", {})
        self.model = str(llm_cfg.get("model", DEFAULT_MODEL))
        self.temperature = float(llm_cfg.get("temperature", 0.2))
        self._client = client

        if api_key is not None:
            self.api_key = api_key
        else:
            load_env_file()
            self.api_key = os.environ.get("GEMINI_API_KEY", "")

    @property
    def available(self) -> bool:
        """True si une clé API est configurée."""
        return bool(self.api_key)

    def _build_user_prompt(self, job: dict[str, Any], cv_text: str) -> str:
        """Construit le contenu utilisateur pour le LLM."""
        title = job.get("title", "")
        company = job.get("company", "")
        location = job.get("location", "")
        description = (job.get("description") or "").strip()[:8000]

        return (
            "DONNÉES DU POSTE À POURVOIR :\n"
            f"Titre : {title}\n"
            f"Entreprise / Organisation : {company}\n"
            f"Localisation : {location}\n\n"
            f"Description de l'offre :\n{description or '(Description non fournie)'}\n\n"
            "--------------------------------------------------\n"
            "CV DU CANDIDAT :\n"
            f"{cv_text}\n"
            "--------------------------------------------------\n"
            "Rédige maintenant la lettre de motivation complète et formelle selon les instructions."
        )

    def generate(self, job: dict[str, Any], cv_text: str | None = None) -> str:
        """Génère une lettre de motivation.

        Renvoie le texte de la lettre ou un message explicatif en cas d'erreur.
        """
        if not self.available:
            return (
                "⚠️ Clé GEMINI_API_KEY manquante dans votre fichier .env.\n\n"
                "Veuillez ajouter votre clé API Gemini pour pouvoir générer des lettres de motivation."
            )

        cv = cv_text or get_cv_text()
        if not cv.strip():
            return "⚠️ Aucun profil candidat trouvé dans data/cv_eddy.txt."

        prompt_content = self._build_user_prompt(job, cv)
        client = self._client or genai.Client(api_key=self.api_key)

        for attempt in range(3):
            try:
                response = client.models.generate_content(
                    model=self.model,
                    contents=prompt_content,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        temperature=self.temperature,
                    ),
                )
                return (response.text or "").strip()
            except APIError as exc:
                if attempt < 2 and (exc.code in (503, 429) or "demand" in str(exc).lower()):
                    import time
                    time.sleep(1.5 * (attempt + 1))
                    continue
                return f"⚠️ Erreur API Gemini ({exc.code}) : {exc.message}"
            except Exception as exc:
                if attempt < 2:
                    import time
                    time.sleep(1.0 * (attempt + 1))
                    continue
                return f"⚠️ Erreur lors de la génération de la lettre : {exc}"
        return "⚠️ Impossible de générer la lettre après plusieurs tentatives."
