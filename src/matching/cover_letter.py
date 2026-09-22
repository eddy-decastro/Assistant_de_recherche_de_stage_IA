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

DEFAULT_MODEL = "gemini-3-flash-preview"

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


def is_english_text(text: str) -> bool:
    """Détecte si un texte d'offre est principalement en anglais."""
    if not text:
        return False
    lower = text.lower()
    en_markers = [
        " the ", " and ", " with ", " for ", " intern", " team ", " working ",
        " skills", " requirements", " experience", " develop", " we are looking",
        " we offer", " who you are", " about the role", " qualifications", " what you will do"
    ]
    fr_markers = [
        " le ", " la ", " les ", " des ", " pour ", " avec ", " dans ",
        " stage", " stagiaire", " recherche ", " au sein de ", " vous ",
        " nous ", " missions", " profil", " formation", " candidature"
    ]
    en_score = sum(lower.count(m) for m in en_markers)
    fr_score = sum(lower.count(m) for m in fr_markers)
    return en_score > fr_score and en_score >= 3


def generate_algorithmic_cover_letter(
    job: dict[str, Any],
    candidate: dict[str, str] | None = None,
    cv_text: str | None = None,
    custom_instruction: str | None = None,
) -> str:
    """Génère une lettre de motivation déterministe de secours (haute fidélité) sans appel API.

    Garantit 100% de disponibilité même en cas de panne, quota épuisé (429) ou
    surcharge temporaire de l'API Gemini (503).
    """
    cand = candidate or DEFAULT_CANDIDATE
    name = cand.get("name", "Eddy DE CASTRO")
    title_cand = cand.get("title", "Élève-ingénieur Mines de Saint-Étienne — Double diplôme M2 Mathématiques en Action")
    phone = cand.get("phone", "06 98 82 44 85")
    email = cand.get("email", "eddyprepa123@gmail.com")
    linkedin = cand.get("linkedin", "https://www.linkedin.com/in/eddy-de-castro/")
    github = cand.get("github", "https://github.com/eddy-decastro")

    raw_title = (job.get("title") or "Stage R&D / Data Science & Machine Learning").strip()
    raw_company = (job.get("company") or "votre entreprise").strip()
    description = (job.get("description") or "").strip()
    full_text = f"{raw_title} {raw_company} {description}".lower()

    contacts = [c for c in [phone, email, linkedin, github] if c]
    signature_block = f"{name}\n{title_cand}\n" + (" | ".join(contacts) if contacts else "")

    is_en = is_english_text(full_text)

    is_vision = any(k in full_text for k in ["vision", "image", "imagerie", "segmentation", "détection", "diffusion", "nerf", "3d", "splatting", "biomédical", "médicale"])
    is_nlp = any(k in full_text for k in ["nlp", "llm", "rag", "transformer", "sémantique", "langage", "texte", "bert", "e5", "agent"])
    is_graph = any(k in full_text for k in ["graph", "gnn", "graphe", "spectrale", "topologie", "réseau"])

    if is_en:
        salutation = "Dear Hiring Team,"
        subject = f"Subject: Application for End-of-Studies Internship — {raw_title}"

        p_hook = (
            f"As a leading innovator in its sector, {raw_company} consistently develops state-of-the-art technological and "
            f"scientific solutions driven by cutting-edge data science and advanced modeling. Impressed by your achievements, "
            f"your culture of R&D excellence, and your forward-looking projects, I am writing to enthusiastically submit my "
            f"application for the {raw_title} internship position."
        )

        p_education = (
            "Currently an engineering graduate student at École des Mines de Saint-Étienne, holding a strong foundation from "
            "IMT Mines Alès (specializing in Artificial Intelligence & Data Science), I am pursuing a concurrent dual Master of Science "
            "in Mathematics in Action (M2 MAEA, co-accredited by École Centrale de Lyon and ENS de Lyon). In parallel with this intensive "
            "engineering curriculum, I also completed a Bachelor's Degree (Licence 3) in Pure and Applied Mathematics at the University "
            "of Montpellier. This dual background provides me with advanced mathematical mastery — linear algebra, convex and non-convex "
            "optimization, differential calculus, stochastic processes, and statistical inference — seamlessly combined with rigorous software engineering "
            "practices and applied Machine Learning."
        )

        p_experience = (
            "I demonstrated this synergy between mathematical rigor and deep learning during my R&D research internship at the Universitat "
            "Politècnica de Catalunya (UPC) in Barcelona. Embedded within an international research team, I designed Graph Neural Network (GNN) "
            "architectures and spectral similarity algorithms in PyTorch. Confronted with sophisticated code obfuscation techniques, I developed "
            "a novel bypass based on differentiable substitution (BPDA), scientifically validated through rigorous statistical hypothesis testing "
            "(McNemar test and paired bootstrap with p < 0.001), elevating detection accuracy to 96.2%. Furthermore, my key software projects "
            "reflect this standard of excellence: MedStay-CI (certified 89.9% uncertainty quantification using LightGBM quantile regression "
            "and Conformal Prediction with MAPIE, wrapped in a Dockerized FastAPI service covered by 124 unit tests) and CinéFilm AI "
            "(sub-100ms real-time semantic vector search using PyTorch and an E5-Large bi-encoder with hybrid dense/lexical scoring)."
        )

        custom_block = ""
        if custom_instruction and custom_instruction.strip():
            custom_block = f"\n\nRegarding your specific focus: {custom_instruction.strip()}.\n"

        p_fit = (
            f"Joining {raw_company} represents an inspiring opportunity to dedicate my dual mathematical and software competencies "
            f"to your engineering challenges. Autonomous, diligent, and adept at bridging cutting-edge scientific literature with high-performance "
            f"production code, I look forward to contributing actively to your technical roadmap."
        )

        p_logistics = "I am available for a full-time 6-month end-of-studies internship starting in early April 2027."
        p_closing = "I would welcome the opportunity to discuss my background and how my skills align with your objectives in an interview."
        valediction = "Sincerely,"

        body_parts = [
            subject,
            "",
            salutation,
            "",
            p_hook,
            "",
            p_education,
            "",
            p_experience + custom_block,
            "",
            p_fit,
            "",
            p_logistics,
            "",
            p_closing,
            "",
            valediction,
            "",
            signature_block,
        ]
        return "\n".join(body_parts).strip()

    else:
        salutation = "Madame, Monsieur,"
        subject = f"Objet : Candidature au stage de fin d'études — {raw_title}"

        p_hook = (
            f"Acteur de référence dans son domaine, {raw_company} développe des initiatives technologiques et des solutions d'envergure "
            f"qui mobilisent une expertise de pointe en modélisation et en science des données. Vivement intéressé par vos réalisations, "
            f"votre culture de l'innovation et vos défis scientifiques actuels, c'est avec un vif intérêt et un enthousiasme certain "
            f"que je vous adresse ma candidature pour le poste de {raw_title}."
        )

        p_education = (
            "Actuellement élève-ingénieur aux Mines de Saint-Étienne et issu du cursus ingénieur de l'IMT Mines Alès "
            "(spécialisation Intelligence Artificielle & Data Science), je prépare en double diplôme le Master 2 Mathématiques en Action "
            "(MAEA, co-accrédité par l'École Centrale de Lyon et l'ENS de Lyon). En parallèle de ce cursus en grande école, j'ai également "
            "validé une Licence 3 de Mathématiques Générales à l'Université de Montpellier. Cette triple formation me confère une maîtrise "
            "approfondie des fondements théoriques — algèbre linéaire avancée, optimisation convexe et non convexe, calcul différentiel, "
            "modélisation stochastique et statistiques inférentielles — associée à une grande rigueur méthodologique et à de solides compétences "
            "en ingénierie logicielle et Machine Learning appliqué."
        )

        tech_focus = ""
        if is_vision:
            tech_focus = " Particulièrement sensible aux enjeux de traitement d'images et de vision par ordinateur, je sais mobiliser les architectures d'apprentissage profond adaptées à vos données complexes."
        elif is_nlp:
            tech_focus = " Passionné par le traitement automatique du langage naturel (NLP) et les architectures de transformers, je dispose d'une solide expérience sur l'exploitation et l'optimisation des représentations vectorielles denses."
        elif is_graph:
            tech_focus = " Fort d'une expertise pointue en modélisation relationnelle et Graph Machine Learning, je sais concevoir des représentations spectrales et topologiques performantes."

        p_experience = (
            "Cette synergie entre rigueur mathématique et mise en œuvre applicative s'est illustrée lors de mon stage de recherche R&D "
            "au sein du laboratoire de l'Universitat Politècnica de Catalunya (UPC) à Barcelone. Au cœur d'une équipe internationale, j'ai "
            "développé des architectures de Graph Machine Learning (GNN) et des analyses de similarité spectrale sous PyTorch. Face à des "
            "mécanismes d'obfuscation complexes, j'ai conçu un contournement reposant sur un substitut différentiable (BPDA), validé scientifiquement "
            "par des tests d'hypothèses rigoureux (test de McNemar et bootstrap apparié avec p < 0,001), permettant d'atteindre un taux de détection "
            "de 96,2 %. En parallèle, mes réalisations logicielles témoignent de mon exigence de qualité : conception du projet MedStay-CI "
            "(quantification d'incertitude certifiée à 89,9 % par régression quantile LightGBM et Conformal Prediction MAPIE, API conteneurisée "
            "FastAPI/Docker avec 124 tests unitaires) et du moteur CinéFilm IA (recherche sémantique vectorielle bilingue temps réel sous 100 ms via un "
            f"bi-encodeur E5-Large sous PyTorch).{tech_focus}"
        )

        custom_block = ""
        if custom_instruction and custom_instruction.strip():
            custom_block = f"\n\nEn accord avec vos priorités opérationnelles : {custom_instruction.strip()}.\n"

        p_fit = (
            f"Intégrer les équipes de {raw_company} représente pour moi l'opportunité de mettre cette double culture scientifique et logicielle "
            f"au service direct de vos projets d'innovation. Autonome, rigoureux et habitué à explorer la littérature scientifique internationale "
            f"comme à transformer des concepts théoriques en code robuste et performant, je saurai m'adapter rapidement à votre environnement "
            f"technique et contribuer efficacement à vos objectifs R&D."
        )

        p_logistics = "Je suis disponible pour un stage conventionné de fin d'études (PFE) d'une durée de 6 mois, à compter de début avril 2027."
        p_politeness = (
            "Dans l'attente d'un échange au cours duquel je serai ravi de vous exposer plus en détail la convergence entre mes compétences "
            "et vos besoins, je vous prie d'agréer, Madame, Monsieur, l'expression de mes salutations les plus distinguées."
        )

        body_parts = [
            subject,
            "",
            salutation,
            "",
            p_hook,
            "",
            p_education,
            "",
            p_experience + custom_block,
            "",
            p_fit,
            "",
            p_logistics,
            "",
            p_politeness,
            "",
            signature_block,
        ]
        return "\n".join(body_parts).strip()


class CoverLetterGenerator:
    """Générateur de lettre de motivation via Gemini avec repli automatique haute fidélité."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        api_key: str | None = None,
        client: genai.Client | None = None,
    ) -> None:
        self.config = config or load_config()
        llm_cfg = self.config.get("llm", {})
        self.model = str(llm_cfg.get("cover_letter_model") or llm_cfg.get("model", DEFAULT_MODEL))
        self.temperature = float(llm_cfg.get("temperature", 0.2))
        self.candidate = get_candidate_info(self.config)
        self.system_prompt = build_system_prompt(self.candidate)
        self._client = client
        self.last_source: str = ""
        self.last_error: str = ""

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

    def generate_fallback(
        self,
        job: dict[str, Any],
        cv_text: str | None = None,
        custom_instruction: str | None = None,
    ) -> str:
        """Génère la lettre de motivation via le moteur algorithmique déterministe de secours."""
        self.last_source = "fallback"
        return generate_algorithmic_cover_letter(
            job=job,
            candidate=self.candidate,
            cv_text=cv_text or get_cv_text(),
            custom_instruction=custom_instruction,
        )

    def generate(
        self,
        job: dict[str, Any],
        cv_text: str | None = None,
        custom_instruction: str | None = None,
        allow_fallback: bool = True,
    ) -> str:
        """Génère une lettre de motivation.

        En cas d'erreur de l'API Gemini (surcharge 503, quota 429, etc.), bascule
        automatiquement sur la solution de secours haute fidélité si allow_fallback=True.
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

        fallback_models: list[str] = []
        for m in [
            self.model,
            "gemini-3-flash-preview",
            "gemini-3.8-flash",
            "gemini-3.7-flash",
            "gemini-3.6-flash",
            "gemini-3.5-flash",
            "gemini-3.1-flash-lite",
            "gemini-flash-latest",
            "gemini-flash-lite-latest",
        ]:
            if m and m not in fallback_models:
                fallback_models.append(m)

        self.last_error = ""
        for model_candidate in fallback_models:
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
                    self.last_source = "gemini"
                    return self._postprocess_letter(raw_text)
            except APIError as exc:
                self.last_error = f"⚠️ Erreur API Gemini ({exc.code}) : {exc.message}"
                # En cas de 404, 429 ou 503 (surcharge), bascule instantanément vers le modèle suivant
                continue
            except Exception as exc:
                self.last_error = f"⚠️ Erreur : {exc}"
                continue

        # Si tous les modèles ont échoué (par exemple à cause d'une saturation 503 générale de Google) :
        if allow_fallback:
            return self.generate_fallback(job, cv_text=cv, custom_instruction=custom_instruction)

        return self.last_error or "⚠️ Impossible de générer la lettre après plusieurs tentatives."
