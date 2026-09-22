"""Générateur de lettres de motivation personnalisées (Gemini).

Utilise le profil candidat (data/cv_eddy.txt et config.yaml) et la fiche de poste
pour produire une lettre de motivation complète, académique et percutante (1 à 1,5 pages,
environ 500 à 650 mots), directement prête à l'envoi sans aucun placeholder ni crochet.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types
from google.genai.errors import APIError

from src.config import PROJECT_ROOT, load_config
from src.matching.llm_judge import load_env_file

DEFAULT_MODEL = "gemini-3.1-flash-lite"

DEFAULT_CANDIDATE: dict[str, str] = {
    "name": "Eddy DE CASTRO",
    "title": "Élève-ingénieur Mines de Saint-Étienne — Double diplôme M2 Mathématiques en Action",
    "phone": "06 98 82 44 85",
    "email": "eddyprepa123@gmail.com",
    "linkedin": "https://www.linkedin.com/in/eddy-de-castro/",
    "github": "https://github.com/eddy-decastro",
    "location": "Paris, France",
}


def get_candidate_info(config: dict[str, Any] | None = None) -> dict[str, str]:
    """Récupère les coordonnées du candidat depuis config.yaml ou les valeurs par défaut."""
    cfg = config or load_config()
    raw = cfg.get("candidate", {})
    info = dict(DEFAULT_CANDIDATE)
    if isinstance(raw, dict):
        for k, v in raw.items():
            if v and str(v).strip():
                info[k] = str(v).strip()
    return info


def get_cv_text() -> str:
    """Charge le texte du CV par défaut (data/cv_eddy.txt)."""
    cv_path = PROJECT_ROOT / "data" / "cv_eddy.txt"
    if cv_path.exists():
        return cv_path.read_text(encoding="utf-8")
    return ""


def build_system_prompt(candidate: dict[str, str] | None = None) -> str:
    """Construit le prompt système intégrant les coordonnées et le parcours d'excellence du candidat."""
    cand = candidate or DEFAULT_CANDIDATE
    name = cand.get("name", "Eddy DE CASTRO")
    title = cand.get("title", "Élève-ingénieur Mines de Saint-Étienne — Double diplôme M2 Mathématiques en Action")
    phone = cand.get("phone", "")
    email = cand.get("email", "")
    linkedin = cand.get("linkedin", "")
    github = cand.get("github", "")

    sig_lines = [name]
    if title:
        sig_lines.append(title)
    contacts = [c for c in [phone, email, linkedin, github] if c]
    if contacts:
        sig_lines.append(" | ".join(contacts))
    signature_block = "\n".join(sig_lines)

    return f"""Tu es un expert en recrutement scientifique & tech de haut niveau et en rédaction de candidatures pour des élèves-ingénieurs de grandes écoles françaises (Mines Saint-Étienne / IMT Mines Alès).
Ton rôle est de rédiger une lettre de motivation complète, substantielle, argumentée et hautement personnalisée (format développé d'environ 1 à 1,5 pages, soit 500 à 650 mots) pour une offre de stage de fin d'études spécifique, à partir du CV du candidat et de la description du poste.

RÈGLE D'OR ABSOLUE : ZÉRO CROCHET, ZÉRO PLACEHOLDER.
- Il est STRICTEMENT INTERDIT d'utiliser des crochets `[...]` ou des variables non résolues (comme [Nom du Recruteur], [Votre Téléphone], [Adresse de l'Entreprise], etc.).
- Tout doit être rédigé de façon naturelle, précise et directement exploitable. Le candidat doit pouvoir copier-coller ou exporter la lettre immédiatement sans la moindre retouche manuelle obligatoire.
- Ne pas mettre d'adresses postales physiques en en-tête.

STRUCTURE ET CONTENU DÉTAILLÉ DE LA LETTRE (1 à 1,5 pages, ~500-650 mots) :
1. Objet clair et professionnel :
   Exemple : "Objet : Candidature au stage de fin d'études — [Intitulé exact du poste]" (en intégrant directement le titre réel, sans aucun crochet).

2. Formule d'appel :
   "Madame, Monsieur," (ou nom exact de la personne si mentionné sans ambiguïté dans l'offre).

3. Corps de texte en 4 à 5 paragraphes équilibrés, techniques et approfondis :
   a. L'ACCROCHE & L'ENTREPRISE (Vous) :
      Analyser avec pertinence les enjeux, la mission ou les technologies de l'entreprise. Démontrer un intérêt sincère et documenté pour ses projets, ses produits, ses défis R&D ou ses publications.

   b. FORMATION D'EXCELLENCE & TRIPLE PARCOURS MATHS / IA (Moi) :
      Valoriser le profil académique particulièrement robuste du candidat :
      - Double diplôme Master 2 Mathématiques en Action (MAEA, Mines Saint-Étienne co-accrédité Centrale Lyon et ENS Lyon) et cursus ingénieur IMT Mines Alès (spécialisation IA & Data Science).
      - MENTIONNER EXPLICITEMENT la Licence 3 de Mathématiques Générales à l'Université de Montpellier menée en parallèle de l'école d'ingénieurs.
      - Souligner l'atout de cette double compétence rare : un socle théorique de haut niveau (algèbre linéaire, calcul différentiel, optimisation convexe/non convexe, modélisation stochastique, statistiques inférentielles) combiné à une solide rigueur en génie logiciel et Machine Learning appliqué.

   c. RÉALISATIONS CONCRÈTES & PROJETS TECHNIQUES EN MIROIR (Moi) :
      Illustrer vos compétences en vous appuyant sur 2 réalisations techniques majeures du CV qui font directement écho aux missions du poste :
      - Le stage R&D à l'UPC Barcelone : modélisation en graphes (Graph ML, similarité spectrale), contournement d'obfuscation par substitut différentiable sous PyTorch (BPDA), et rigueur d'évaluation statistique (test de McNemar, bootstrap apparié).
      - Un projet applicatif ciblé du CV : par exemple MedStay-CI (quantification d'incertitude certifiée à 89,9 %, régression quantile conforme sous LightGBM/MAPIE, pipeline FastAPI/Docker avec 124 tests) ou CinéFilm IA (recherche sémantique vectorielle sous 100 ms avec bi-encodeur E5-Large, PyTorch, scoring hybride).
      Faire un pont technique direct et convaincant avec la stack et les responsabilités mentionnées dans l'offre.

   d. COLLABORATION, VALEUR AJOUTÉE & PROJECTION (Nous) :
      Expliquer comment le candidat compte s'intégrer, collaborer avec l'équipe et monter rapidement en puissance sur les problématiques du projet.

   e. MODALITÉS PRATIQUES & DISPONIBILITÉ :
      Confirmer la disponibilité pour un stage conventionné de fin d'études (PFE) d'une durée de 6 mois, à partir de début avril 2027.

4. Formule de politesse soignée :
   Élégante et formelle (ex. "Dans l'attente d'un prochain échange au cours duquel je serai ravi de vous exposer plus en détail mes motivations et l'adéquation de mon profil avec vos projets, je vous prie d'agréer, Madame, Monsieur, l'expression de mes salutations les plus distinguées.").

5. Signature complète :
{signature_block}

RÈGLES DE STYLE :
- LANGUE : Si l'offre est rédigée en anglais, rédige l'intégralité de la lettre en anglais professionnel soutenu. Sinon, en français soigné, fluide et percutant.
- TON : Rigueur scientifique, vocabulaire technique précis, assurance mesurée, aucun cliché creux.
- VÉRACITÉ : Reste strictement conforme aux éléments du CV.
"""


SYSTEM_PROMPT = build_system_prompt()


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
        # Modèle dédié pour la lettre de motivation (gemini-2.5-flash par défaut, plus qualitatif que flash-lite)
        self.model = str(llm_cfg.get("cover_letter_model") or llm_cfg.get("model", DEFAULT_MODEL))
        self.temperature = float(llm_cfg.get("temperature", 0.2))
        self.candidate = get_candidate_info(self.config)
        self.system_prompt = build_system_prompt(self.candidate)
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

    def _build_user_prompt(
        self,
        job: dict[str, Any],
        cv_text: str,
        custom_instruction: str | None = None,
    ) -> str:
        """Construit le contenu utilisateur pour le LLM, avec consigne spécifique optionnelle."""
        title = job.get("title", "")
        company = job.get("company", "")
        location = job.get("location", "")
        description = (job.get("description") or "").strip()[:8000]

        parts = [
            "DONNÉES DU POSTE À POURVOIR :",
            f"Titre : {title}",
            f"Entreprise / Organisation : {company}",
            f"Localisation : {location}",
            "",
            f"Description de l'offre :\n{description or '(Description non fournie)'}",
            "",
            "--------------------------------------------------",
            "CV DU CANDIDAT :",
            f"{cv_text}",
            "--------------------------------------------------",
        ]

        if custom_instruction and custom_instruction.strip():
            parts.extend([
                "CONSIGNES SPÉCIFIQUES DU CANDIDAT POUR CETTE LETTRE :",
                f"{custom_instruction.strip()}",
                "--------------------------------------------------",
            ])

        parts.append(
            "Rédige maintenant la lettre de motivation complète, développée (environ 1 à 1,5 pages, 500 à 650 mots) "
            "et strictement sans aucun crochet selon toutes les instructions."
        )
        return "\n".join(parts)

    def _postprocess_letter(self, text: str) -> str:
        """Nettoie d'éventuels crochets résiduels pour garantir un texte 100% propre."""
        if not text:
            return ""
        cleaned = text
        for bracket in re.findall(r"\[([^\]]+)\]", cleaned):
            lower_b = bracket.lower()
            if any(k in lower_b for k in ["nom du", "responsable", "recruteur", "destinataire"]):
                cleaned = cleaned.replace(f"[{bracket}]", "l'équipe recrutement")
            elif any(k in lower_b for k in ["téléphone", "tel"]):
                cleaned = cleaned.replace(f"[{bracket}]", self.candidate.get("phone", ""))
            elif any(k in lower_b for k in ["email", "mail"]):
                cleaned = cleaned.replace(f"[{bracket}]", self.candidate.get("email", ""))
            elif any(k in lower_b for k in ["linkedin"]):
                cleaned = cleaned.replace(f"[{bracket}]", self.candidate.get("linkedin", ""))
            elif any(k in lower_b for k in ["github"]):
                cleaned = cleaned.replace(f"[{bracket}]", self.candidate.get("github", ""))
            elif any(k in lower_b for k in ["adresse", "ville"]):
                cleaned = cleaned.replace(f"[{bracket}]", self.candidate.get("location", ""))
            else:
                cleaned = cleaned.replace(f"[{bracket}]", bracket)
        return cleaned.strip()

    def generate(
        self,
        job: dict[str, Any],
        cv_text: str | None = None,
        custom_instruction: str | None = None,
    ) -> str:
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

        prompt_content = self._build_user_prompt(job, cv, custom_instruction=custom_instruction)
        client = self._client or genai.Client(api_key=self.api_key)

        # Chaîne de repli automatique pour garantir 100% de succès sans 404/503/429
        fallback_models: list[str] = []
        for m in [self.model, "gemini-3.1-flash-lite", "gemini-flash-latest", "gemini-3.6-flash", "gemini-flash-lite-latest"]:
            if m and m not in fallback_models:
                fallback_models.append(m)

        last_error = ""
        for model_candidate in fallback_models:
            for attempt in range(2):
                try:
                    response = client.models.generate_content(
                        model=model_candidate,
                        contents=prompt_content,
                        config=types.GenerateContentConfig(
                            system_instruction=self.system_prompt,
                            temperature=self.temperature,
                        ),
                    )
                    raw_text = (response.text or "").strip()
                    if raw_text:
                        return self._postprocess_letter(raw_text)
                except APIError as exc:
                    last_error = f"⚠️ Erreur API Gemini ({exc.code}) : {exc.message}"
                    # Modèle non accessible (404) ou quota dépassé sur ce modèle (429) : basculer immédiatement
                    if exc.code in (404, 429) or "not available" in str(exc).lower():
                        break
                    # Pic de charge temporaire (503) : pause rapide
                    if attempt < 1 and (exc.code == 503 or "demand" in str(exc).lower()):
                        import time
                        time.sleep(1.0)
                        continue
                    break
                except Exception as exc:
                    last_error = f"⚠️ Erreur : {exc}"
                    break

        return last_error or "⚠️ Impossible de générer la lettre après plusieurs tentatives."
