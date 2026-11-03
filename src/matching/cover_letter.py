"""Générateur de lettres de motivation personnalisées (DeepSeek V3 prioritaire, Gemini en secours).

Utilise le fichier de faits vérifiés (``data/profil_lettre.yaml`` via ``src.candidate.get_letter_facts``),
le profil candidat et la fiche de poste pour produire une lettre de motivation sobre,
factuelle et directement prête à l'envoi sans aucun placeholder ni crochet.
"""
from __future__ import annotations

import os
import re
from typing import Any

import requests
import yaml
from google import genai
from google.genai import types
from google.genai.errors import APIError

from src.candidate import get_candidate_info, get_cv_text, get_letter_facts  # noqa: F401
from src.config import load_config
from src.env import load_env_file

DEFAULT_MODEL = "gemini-3-flash-preview"

DEFAULT_CANDIDATE: dict[str, str] = {
    "name": "Eddy DE CASTRO",
    "title": "Élève-ingénieur IMT Mines Alès, double diplôme M2 Mathématiques en Action (Mines Saint-Étienne)",
    "phone": "",
    "email": "",
    "linkedin": "https://www.linkedin.com/in/eddy-de-castro/",
    "github": "https://github.com/eddy-decastro",
    "location": "Paris, France",
}


def build_system_prompt(candidate: dict[str, str] | None = None) -> str:
    """Construit le prompt système intégrant les coordonnées et les règles de véracité du candidat."""
    cand = candidate or DEFAULT_CANDIDATE
    name = cand.get("name", "Eddy DE CASTRO")
    title = cand.get(
        "title",
        "Élève-ingénieur IMT Mines Alès, double diplôme M2 Mathématiques en Action (Mines Saint-Étienne)",
    )
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

    return f"""Tu es un expert en recrutement scientifique & tech et en rédaction de candidatures pour des élèves-ingénieurs de grandes écoles françaises (IMT Mines Alès / Mines Saint-Étienne).
Ton rôle est de rédiger une lettre de motivation sobre, factuelle et personnalisée pour une offre de stage de fin d'études spécifique.

RÈGLE DE VÉRACITÉ STRICTE :
- Utilise UNIQUEMENT les faits de la section FAITS VÉRIFIÉS. N'invente aucun chiffre, outil, résultat ni fait sur l'entreprise.

RÈGLE D'OR ABSOLUE : ZÉRO CROCHET, ZÉRO PLACEHOLDER.
- Il est STRICTEMENT INTERDIT d'utiliser des crochets `[...]` ou des variables non résolues (comme [Nom du Recruteur], [Votre Téléphone], [Adresse de l'Entreprise], etc.).
- Tout doit être rédigé de façon naturelle, précise et directement exploitable.
- Ne pas mettre d'adresses postales physiques en en-tête.

POSITIONNEMENT DU CANDIDAT :
"Ingénieur ML à formation mathématique : ce qui me motive est de savoir quand un modèle se trompe et de le mesurer honnêtement, puis de mettre en service des modèles fiables."

PARCOURS SCOLAIRE EXACT À RESPECTER :
- Classes préparatoires PC* au lycée Descartes de Tours, puis admission à l'IMT Mines Alès.
- 2e année à l'IMT Mines Alès : majeure Intelligence Artificielle & Data Science, suivie en parallèle de la Licence 3 de Mathématiques Générales à l'Université de Montpellier.
- 3e année (actuelle) : élève de l'IMT Mines Alès en double diplôme Master 2 Mathématiques en Action (MAEA) à Mines Saint-Étienne (co-accrédité Mines Saint-Étienne, Centrale Lyon et ENS Lyon).
- NE JAMAIS mentionner le baccalauréat ni le lycée avant la prépa.

STRUCTURE ET CONTENU :
Longueur : 450 à 550 mots (cible 500), une page A4.

Plan (invisible, pas de titres dans la lettre) :
1. Objet + accroche (1 à 2 phrases) : intitulé exact du poste, PFE de 6 mois. Une phrase qui relie mon profil au sujet de l'offre.
2. Moi (150 à 180 mots) : qui je suis (ingénieur IA + master de maths appliquées) et ce qui me motive (savoir quand un modèle se trompe). Une ou deux expériences racontées simplement, choisies selon l'offre (selon la consigne explicite fournie), avec au plus deux chiffres en tout. Une qualité humaine prouvée par l'engagement (scouts, association ou tutorat).
3. Vous (100 à 130 mots) : ce qui m'attire dans ce sujet et cette équipe, en citant 2 éléments réels de l'offre (problème traité, application, méthode, perspective de publication, etc.). Interdiction de parler de l'entreprise au-delà de ce que dit l'offre, et interdiction d'inventer des publications, des produits ou des valeurs.
4. Nous (120 à 150 mots) : ce que je ferais concrètement pendant le stage, ce que je veux y apprendre, pourquoi j'ai envie de travailler dans cette équipe et de contribuer à son projet. Parler de thèse ou de CIFRE seulement si l'offre mentionne une poursuite en thèse (signal suite_explicite). Si l'offre dit « Possibilité de poursuite en thèse : Non », ne pas en parler.
5. Conclusion (2 à 3 phrases) : disponibilité, proposition d'entretien, formule de politesse sobre (« Je vous prie d'agréer, Madame, Monsieur, l'expression de mes salutations distinguées. »), signature.

RÈGLES D'ÉCRITURE :
- Pas trop technique : au plus 3 termes techniques par paragraphe, chacun compréhensible par un recruteur RH. Exemple : écrire « j'ai cherché les failles d'un détecteur de fraude bancaire » et non « attaque BPDA face à PGD/FGSM ». Le détail technique reste dans le CV joint.
- Le parcours scolaire tient en une phrase (le CV est joint). Ne pas citer le baccalauréat.
- L'honnêteté sur l'UPC est un argument : la défense ne faisait pas mieux qu'un modèle simple, et je l'ai démontré.
- Phrases de moins de 25 mots. Aucun cliché : « parcours d'excellence », « triple formation », « vif intérêt et enthousiasme certain », « passionné », « acteur de référence ».
- Reprendre 2 ou 3 mots-clés de l'offre (utile pour les logiciels de tri).
- Si l'offre impose une contrainte que je ne remplis pas (date de début avant avril 2027, durée différente, lieu), la lettre le dit simplement dans la conclusion au lieu de l'ignorer.
- Langue : celle de l'offre (français si l'offre est en français, anglais si l'offre est en anglais).
- Zéro crochet ni placeholder.

EXEMPLE DE TON (ne pas recopier, ne pas réutiliser ses phrases) :
Objet : Candidature au stage « Évaluation de Graph Neural Networks pour capteurs radar », Grenoble (stage de fin d'études de 6 mois)
Madame, Monsieur,
Élève-ingénieur en intelligence artificielle à l'IMT Mines Alès, je suis cette année en double diplôme dans le master de mathématiques appliquées MAEA à Mines Saint-Étienne. Votre sujet réunit deux choses qui me motivent : les graphes et l'évaluation rigoureuse des modèles.
Ce qui m'intéresse le plus en machine learning, c'est de savoir quand un modèle se trompe, et de le mesurer honnêtement. Lors de mon stage de recherche à l'UPC de Barcelone, j'ai cherché les failles d'un détecteur de fraude bancaire : une attaque adaptée le trompait dans 48 % des cas. J'ai ensuite conçu une défense qui représente le fonctionnement interne du réseau sous forme de graphes, puis je l'ai évaluée avec un protocole statistique fixé à l'avance. Conclusion : elle ne faisait pas significativement mieux qu'un modèle plus simple. Ce stage m'a appris à ne pas me contenter d'un bon chiffre et à vérifier qu'un gain est réel. Dans un projet sur environ 70 000 patients, j'ai aussi construit des prévisions accompagnées d'une marge d'erreur fiable, qui contient la vraie valeur dans 90 % des cas comme visé, puis je les ai mises en service. Ma formation en mathématiques me permet de comprendre ces méthodes au lieu de les appliquer en boîte noire. Enfin, encadrer une trentaine d'adolescents chez les scouts et présider une association m'ont appris à m'organiser et à travailler avec des personnes très différentes.
Votre sujet m'attire parce qu'il part d'une vraie difficulté : les données radar sont éparses, bruitées et peu résolues, là où les méthodes classiques peinent. Le radar a pourtant des atouts que la caméra n'a pas : il fonctionne dans l'obscurité et par mauvais temps, pour un coût modeste. Les applications que vous citez, de la surveillance de l'habitacle à la détection de chutes, touchent directement la sécurité et la santé des personnes. J'apprécie aussi que le stage s'appuie sur des méthodes développées au sein de votre laboratoire et qu'il puisse déboucher sur une publication.
Pendant ces six mois, je souhaite contribuer à la mise en œuvre et à l'amélioration de vos méthodes de traitement des données radar. Je pourrais en particulier bâtir un protocole d'évaluation solide : comparer les GNN à des références plus simples, mesurer la stabilité des résultats et repérer les situations où le modèle échoue. J'ai envie d'apprendre au sein d'une équipe pluridisciplinaire qui travaille sur des capteurs réels, et de progresser sur les réseaux de neurones sur graphes. Je prendrai le temps d'étudier les trois publications citées dans votre offre avant notre échange.
Votre offre indique un début au 1er février 2027. Mon calendrier de double diplôme me rend disponible à partir de début avril 2027, pour six mois. Si ce décalage reste compatible avec votre projet, je serais heureux d'en discuter lors d'un entretien.
Je vous prie d'agréer, Madame, Monsieur, l'expression de mes salutations distinguées.
Eddy DE CASTRO

SIGNATURE COMPLÈTE :
{signature_block}
"""


SYSTEM_PROMPT = build_system_prompt()


def select_experiences_for_job(job: dict[str, Any] | str) -> tuple[str, str]:
    """Choisit les 2 expériences principales à mettre en avant selon les mots-clés de l'offre.

    Règle :
    - incertitude, fiabilité, prévision, santé → ("MedStay", "UPC")
    - robustesse, sécurité, attaque, fraude, évaluation, graphes → ("UPC", "MedStay")
    - imagerie médicale, scanner, 3D → ("PanTS", "MedStay")
    - LLM, NLP, agents, RAG → ("Stage Copilot ou CinéFilm", "UPC")
    - physique, simulation, modèles numériques → ("formation en maths du M2", "MedStay")
    - par défaut → ("UPC", "MedStay")
    """
    if isinstance(job, str):
        title_lower = job.lower()
        desc_lower = job.lower()
    else:
        title_lower = str(job.get("title") or "").lower()
        desc_lower = str(job.get("description") or "").lower()

    categories: list[tuple[tuple[str, str], tuple[str, ...]]] = [
        (
            ("PanTS", "MedStay"),
            ("imagerie médicale", "medical imaging", "scanner", "ct scan", "3d", "radiologie", "irm", "mri"),
        ),
        (
            ("MedStay", "UPC"),
            ("incertitude", "uncertainty", "fiabilité", "prévision", "forecasting", "santé", "clinique", "hôpital", "patient"),
        ),
        (
            ("UPC", "MedStay"),
            ("robustesse", "robustness", "sécurité", "attaque", "adversar", "fraude", "évaluation", "evaluation", "graphe", "graph", "gnn"),
        ),
        (
            ("Stage Copilot ou CinéFilm", "UPC"),
            ("llm", "nlp", "agent", "rag", "langage naturel", "language model", "recherche sémantique"),
        ),
        (
            ("formation en maths du M2", "MedStay"),
            ("physique", "physics", "simulation", "modèles numériques", "modèle numérique", "edp", "pde", "calcul scientifique"),
        ),
    ]

    best_pair: tuple[str, str] = ("UPC", "MedStay")
    best_score = 0

    for pair, keywords in categories:
        score = 0
        for kw in keywords:
            pattern = rf"\b{re.escape(kw)}"
            if re.search(pattern, title_lower):
                score += 3
            if re.search(pattern, desc_lower):
                score += 1
        if score > best_score:
            best_score = score
            best_pair = pair

    return best_pair


def detect_start_date_mismatch(job: dict[str, Any]) -> str | None:
    """Détecte si l'offre mentionne une date de début antérieure à avril 2027."""
    text = f"{job.get('title') or ''} {job.get('description') or ''}"
    patterns = [
        r"\b(?:1er\s+|0?1\s+|début\s+)?(?:janvier|février|fevrier|mars|january|february|march)\s+2027\b",
        r"\b0?[1-9]/0?[1-3]/2027\b",
        r"\bdébut\s+(?:janvier|février|fevrier|mars)\b",
    ]
    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            return m.group(0).strip()
    return None


def is_english_text(text: str) -> bool:
    """Détecte si un texte d'offre est principalement en anglais."""
    if not text:
        return False
    lower = text.lower()
    en_markers = [
        " the ", " and ", " with ", " for ", " intern", " team ", " working ",
        " skills", " requirements", " experience", " develop", " we are looking",
        " we offer", " who you are", " about the role", " qualifications", " what you will do",
    ]
    fr_markers = [
        " le ", " la ", " les ", " des ", " pour ", " avec ", " dans ",
        " stage", " stagiaire", " recherche ", " au sein de ", " vous ",
        " nous ", " missions", " profil", " formation", " candidature",
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
    """Génère une lettre de motivation de secours utilisant exclusivement les faits vérifiés.

    Suit le plan Moi / Vous / Nous, sans éloge inventé sur l'entreprise ni chiffre hors
    de ``data/profil_lettre.yaml``.
    """
    _ = cv_text
    facts = get_letter_facts()
    identite = facts.get("identite") or {}
    cand = candidate or DEFAULT_CANDIDATE
    name = cand.get("name") or identite.get("nom") or "Eddy DE CASTRO"
    title_cand = (
        cand.get("title")
        or identite.get("titre_court")
        or "Élève-ingénieur IMT Mines Alès, double diplôme M2 Mathématiques en Action (Mines Saint-Étienne)"
    )
    phone = cand.get("phone", "")
    email = cand.get("email", "")
    linkedin = cand.get("linkedin", "https://www.linkedin.com/in/eddy-de-castro/")
    github = cand.get("github", "https://github.com/eddy-decastro")

    raw_title = (job.get("title") or "Stage R&D / Data Science & Machine Learning").strip()
    raw_company = (job.get("company") or "votre entreprise").strip()
    description = (job.get("description") or "").strip()
    full_text = f"{raw_title} {raw_company} {description}".lower()

    contacts = [c for c in [phone, email, linkedin, github] if c]
    signature_block = f"{name}\n{title_cand}" + (f"\n{' | '.join(contacts)}" if contacts else "")

    structure_type = str(job.get("structure_type") or "").upper()
    signals = job.get("signals")
    if not signals and job.get("signals_json"):
        try:
            import json
            signals = json.loads(job["signals_json"]) if isinstance(job["signals_json"], str) else job["signals_json"]
        except Exception:
            signals = {}
    signals = signals or {}
    has_suite = bool((signals.get("suite_explicite") or {}).get("present"))
    if "possibilité de poursuite en thèse : non" in full_text or "poursuite en thèse : non" in full_text:
        has_suite = False

    is_en = is_english_text(full_text)

    if is_en:
        subject = f"Subject: Application for End-of-Studies Internship — {raw_title}"
        salutation = "Dear Hiring Team,"
        p_intro = (
            f"Currently in my third year as an engineering student in Artificial Intelligence and Data Science at "
            f"IMT Mines Alès and enrolled in the M2 Mathematics in Action dual degree at Mines Saint-Étienne, "
            f"I am applying for the 6-month end-of-studies internship {raw_title} at {raw_company}."
        )
        p_moi = (
            "After completing French preparatory classes in Physics and Chemistry (PC* at Lycée Descartes in Tours), "
            "I joined IMT Mines Alès while completing a Bachelor's degree (Licence 3) in General Mathematics at the "
            "University of Montpellier in parallel, before joining the M2 Mathematics in Action at Mines Saint-Étienne "
            "(co-accredited with Centrale Lyon and ENS Lyon), covering stochastic modeling, optimization, statistical "
            "learning, high-performance computing, and uncertainty quantification. What motivates me most in machine "
            "learning is knowing when a model makes mistakes, measuring it honestly, and deploying reliable models. "
            "During my research internship at UPC (Universitat Politècnica de Catalunya) in Barcelona from May to August "
            "2026, I audited the robustness of a neural network bank fraud detector (AUC 98 %), where an adapted attack "
            "fooled the model in 48 % of cases. I designed a graph-based defense analyzing the internal functioning of "
            "the network, which intercepted 68 % of successful attacks, and evaluated it with a pre-specified statistical "
            "protocol showing no significant advantage over a simpler SVM model. This taught me not to settle for a good "
            "number, but to verify that a gain is real. In my MedStay-CI hospital length-of-stay project on around 70,000 "
            "patients, I built predictions with a guaranteed error margin containing the true value in 90 % of cases as "
            "targeted, reduced intervals by 11 % without losing reliability, and deployed the model as an API with more "
            "than 120 tests. I also built Stage Copilot to collect and evaluate more than 1,200 internship offers, "
            "CinéFilm IA for semantic movie recommendation over more than 6,000 films, and PanTS on 420 GB of 3D medical "
            "scanners (82 % accuracy, +10 points over baseline). Leading a summer camp for about thirty teenagers with "
            "the Scouts Unitaires de France, presiding the EM'Aumônerie association for one year, and giving weekly math "
            "tutoring taught me how to organize teamwork and communicate clearly."
        )
        p_vous = (
            f"Your internship offer for the position of {raw_title} at {raw_company} directly matches my background in "
            f"applied mathematics, statistical learning, uncertainty quantification, and software engineering, as well as "
            f"my desire to work on concrete modeling and evaluation problems with your team."
        )
        if custom_instruction and custom_instruction.strip():
            p_vous += f" In particular, I take note of this priority: {custom_instruction.strip()}."
        p_nous = (
            f"During these six months at {raw_company}, I want to contribute concretely to the modeling, evaluation, "
            f"and software implementation required for {raw_title}. I would bring a methodical approach to setting up "
            f"clear baselines, measuring model stability, identifying failure modes, and writing clean, well-tested code. "
            f"Working with your team would allow me to deepen my practice on real problems while sharing clear and "
            f"reproducible experimental results throughout the project."
        )
        if has_suite:
            p_nous += " As the offer mentions a possible continuation, I would also be interested in discussing a PhD or CIFRE track."
        p_closing = (
            "I am available for a 6-month end-of-studies internship starting in early April 2027, and I would be glad "
            "to discuss my application during an interview.\n\nSincerely,"
        )
        return "\n\n".join([subject, salutation, p_intro, p_moi, p_vous, p_nous, p_closing, signature_block]).strip()

    subject = f"Objet : Candidature au stage de fin d'études — {raw_title}"
    salutation = "Madame, Monsieur,"
    p_intro = (
        f"Élève-ingénieur en troisième année à l'IMT Mines Alès en spécialité Intelligence Artificielle et Data Science, "
        f"actuellement en double diplôme dans le Master 2 Mathématiques en Action (MAEA) à Mines Saint-Étienne, "
        f"je vous adresse ma candidature pour le stage de fin d'études de 6 mois « {raw_title} » au sein de {raw_company}."
    )
    p_moi = (
        "Après une classe préparatoire PC* au lycée Descartes de Tours, j'ai intégré l'IMT Mines Alès et mené en "
        "parallèle une Licence 3 de Mathématiques Générales à l'Université de Montpellier, avant de rejoindre cette "
        "année le M2 Mathématiques en Action à Mines Saint-Étienne (co-accrédité avec Centrale Lyon et l'ENS Lyon). "
        "Ce qui me motive en machine learning est de savoir quand un modèle se trompe, de le mesurer honnêtement, puis "
        "de mettre en service des modèles fiables. Lors de mon stage de recherche à l'UPC (Universitat Politècnica de "
        "Catalunya) à Barcelone de mai à août 2026, j'ai audité la robustesse d'un détecteur de fraude bancaire par "
        "réseau de neurones (AUC 98 %) : une attaque adaptée trompait le modèle dans 48 % des cas. J'ai conçu une "
        "défense qui analyse le fonctionnement interne du réseau sous forme de graphes et interceptait 68 % des attaques, "
        "puis je l'ai évaluée avec un protocole statistique fixé à l'avance montrant qu'il n'y avait pas d'avantage "
        "significatif face à un modèle plus simple (SVM). Ce stage m'a appris à ne pas me contenter d'un bon chiffre et "
        "à vérifier qu'un gain est réel. Dans mon projet MedStay-CI de prévision de la durée d'hospitalisation sur "
        "environ 70 000 patients, j'ai construit une marge d'erreur qui contient la vraie valeur dans 90 % des cas comme "
        "visé, réduit les intervalles de 11 % sans perte de fiabilité, et mis le modèle en service sous forme d'API avec "
        "plus de 120 tests. J'ai également développé Stage Copilot (plus de 1 200 offres évaluées), CinéFilm IA "
        "(recommandation de films par recherche sémantique sur plus de 6 000 films) et participé au projet PanTS sur "
        "420 Go de scanners 3D (82 % de bonnes réponses, +10 points sur la référence). Enfin, la direction d'un camp "
        "d'été pour une trentaine d'adolescents chez les Scouts Unitaires de France, la présidence de l'association "
        "EM'Aumônerie pendant un an et les cours particuliers de mathématiques chaque semaine m'ont appris à m'organiser "
        "et à travailler avec rigueur et clarté."
    )
    p_vous = (
        f"Votre offre pour le poste « {raw_title} » au sein de {raw_company} retient mon attention par la cohérence "
        f"entre les missions proposées et ma formation en mathématiques appliquées, apprentissage statistique et "
        f"développement logiciel, ainsi que par la possibilité de travailler sur un sujet concret au sein de votre équipe."
    )
    if custom_instruction and custom_instruction.strip():
        p_vous += f" Je prends notamment en compte votre priorité concernant : {custom_instruction.strip()}."

    if structure_type == "LABO_PUBLIC":
        p_nous = (
            f"Pendant ces six mois au sein du laboratoire de {raw_company}, je souhaite contribuer concrètement aux "
            f"travaux liés au sujet « {raw_title} » : mettre en place un protocole d'évaluation rigoureux, comparer les "
            f"méthodes étudiées à des références simples, analyser les cas d'échec et documenter les expériences avec soin."
        )
    elif structure_type == "SCALEUP_IA":
        p_nous = (
            f"Pendant ces six mois au sein de {raw_company}, je souhaite contribuer concrètement au développement et à "
            f"l'évaluation des modèles pour vos produits et votre roadmap technique autour du poste « {raw_title} », en "
            f"veillant à la fiabilité des résultats comme à la qualité des tests et de la mise en service."
        )
    else:
        p_nous = (
            f"Pendant ces six mois au sein de {raw_company}, je souhaite contribuer concrètement aux travaux du stage "
            f"« {raw_title} » : construire des références solides, mesurer honnêtement la fiabilité des modèles sur vos "
            f"données et livrer un code propre et testé au sein de votre équipe."
        )
    if has_suite:
        p_nous += (
            " Votre offre mentionnant une suite possible, je serais également intéressé pour échanger sur une "
            "éventuelle poursuite en thèse ou CIFRE à l'issue du stage."
        )

    p_closing = (
        "Je suis disponible pour un stage de fin d'études de 6 mois à partir de début avril 2027 et je serais heureux "
        "d'échanger avec vous lors d'un entretien.\n\n"
        "Je vous prie d'agréer, Madame, Monsieur, l'expression de mes salutations distinguées."
    )
    return "\n\n".join([subject, salutation, p_intro, p_moi, p_vous, p_nous, p_closing, signature_block]).strip()


_NUMBER_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])\d{1,3}(?:[\s\u00a0\u202f,]\d{3})+(?![A-Za-z0-9_])"
    r"|(?<![A-Za-z0-9_])\d+(?:[.,]\d+)?(?![A-Za-z0-9_])"
)
_THOUSANDS_RE = re.compile(r"^\d{1,3}(?:[\s\u00a0\u202f,]\d{3})+$")
_PHONE_RE = re.compile(r"(?:\+33\s?|0)[1-9](?:[\s.-]?\d{2}){4}")
_URL_RE = re.compile(r"https?://\S+")
_DATE_EXEMPT_NUMBERS: frozenset[str] = frozenset(
    {"1", "2", "3", "4", "5", "6", "2022", "2023", "2024", "2025", "2026", "2027", "2028"}
)


def _normalize_number_token(token: str) -> str:
    clean = token.strip()
    if _THOUSANDS_RE.match(clean):
        return re.sub(r"[\s\u00a0\u202f,]", "", clean)
    return re.sub(r"[\s\u00a0\u202f]", "", clean).replace(",", ".")


def _extract_normalized_numbers(text: str) -> list[tuple[str, str]]:
    if not text:
        return []
    stripped = _URL_RE.sub(" ", text)
    stripped = _PHONE_RE.sub(" ", stripped)
    results: list[tuple[str, str]] = []
    for match in _NUMBER_TOKEN_RE.finditer(stripped):
        raw = match.group(0).strip()
        norm = _normalize_number_token(raw)
        if norm:
            results.append((raw, norm))
    return results


def check_letter(
    text: str,
    facts: dict[str, Any] | None = None,
    job: dict[str, Any] | None = None,
    *,
    check_length: bool = True,
) -> list[str]:
    """Vérifie automatiquement une lettre générée et retourne la liste des problèmes (vide si OK)."""
    if not text or not text.strip():
        return ["Lettre vide."]

    facts_data = facts if facts is not None else get_letter_facts()
    job_data = job or {}
    problems: list[str] = []

    # 1. Nombres autorisés : faits vérifiés + offre + exceptions de dates/durées
    allowed_numbers: set[str] = set(_DATE_EXEMPT_NUMBERS)
    facts_str = yaml.safe_dump(facts_data, allow_unicode=True, sort_keys=False) if facts_data else ""
    for _, norm in _extract_normalized_numbers(facts_str):
        allowed_numbers.add(norm)
    for tok in re.findall(r"\d+", facts_str):
        allowed_numbers.add(tok)

    job_str = " ".join(
        str(job_data.get(k) or "") for k in ("title", "company", "location", "description")
    )
    for _, norm in _extract_normalized_numbers(job_str):
        allowed_numbers.add(norm)
    for tok in re.findall(r"\d+", job_str):
        allowed_numbers.add(tok)

    for raw_num, norm_num in _extract_normalized_numbers(text):
        if norm_num not in allowed_numbers:
            problems.append(f"Nombre non sourcé dans les faits ou l'offre : « {raw_num} »")

    # 2. Mots interdits
    interdits = (facts_data or {}).get("interdits") or []
    text_lower = text.lower()
    for word in interdits:
        w = str(word).strip()
        if w and w.lower() in text_lower:
            problems.append(f"Terme interdit détecté : « {w} »")

    # 3. Crochets
    if "[" in text or "]" in text:
        problems.append("Crochets détectés dans la lettre (`[` ou `]`).")

    # 4. Nombre de mots (420 à 600)
    if check_length:
        words_count = len(text.split())
        if not (420 <= words_count <= 600):
            problems.append(
                f"Longueur hors cible : {words_count} mots (attendu entre 420 et 600 mots)."
            )

    # 5. Langue identique à celle de l'offre
    job_lang_source = f"{job_data.get('title') or ''} {job_data.get('description') or ''}".strip()
    if job_lang_source:
        offer_is_en = is_english_text(job_lang_source)
        letter_is_en = is_english_text(text)
        if offer_is_en != letter_is_en:
            expected_lang = "anglais" if offer_is_en else "français"
            actual_lang = "anglais" if letter_is_en else "français"
            problems.append(
                f"Langue incorrecte : lettre en {actual_lang} pour une offre en {expected_lang}."
            )

    return problems


class CoverLetterGenerator:
    """Générateur de lettre de motivation (DeepSeek V3 prioritaire, Google Gemini en secours)."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        api_key: str | None = None,
        client: genai.Client | None = None,
        deepseek_api_key: str | None = None,
    ) -> None:
        self.config = config or load_config()
        llm_cfg = self.config.get("llm", {})
        self.model = str(llm_cfg.get("cover_letter_model") or llm_cfg.get("model", DEFAULT_MODEL))
        self.temperature = float(llm_cfg.get("cover_letter_temperature", 0.4))
        self.candidate = get_candidate_info(self.config)
        self.facts = get_letter_facts()
        self.system_prompt = build_system_prompt(self.candidate)
        self._client = client
        self.last_source: str = ""
        self.last_error: str = ""
        self.last_warnings: list[str] = []

        if api_key is not None:
            self.api_key = api_key
        else:
            load_env_file()
            self.api_key = os.environ.get("GEMINI_API_KEY", "")

        if deepseek_api_key is not None:
            self.deepseek_api_key = deepseek_api_key
        else:
            load_env_file()
            self.deepseek_api_key = os.environ.get("DEEPSEEK_API_KEY", "")

    @property
    def available(self) -> bool:
        """True si une clé API Gemini ou DeepSeek est configurée."""
        return bool(self.api_key or self.deepseek_api_key)

    def _build_user_prompt(
        self,
        job: dict[str, Any],
        cv_text: str,
        custom_instruction: str | None = None,
    ) -> str:
        """Construit le contenu utilisateur pour le LLM, incluant FAITS VÉRIFIÉS DU CANDIDAT et CV."""
        title = job.get("title", "")
        company = job.get("company", "")
        location = job.get("location", "")
        description = (job.get("description") or "").strip()[:8000]
        facts_yaml = yaml.safe_dump(self.facts, allow_unicode=True, sort_keys=False) if self.facts else ""

        parts = [
            "DONNÉES DU POSTE À POURVOIR :",
            f"Titre : {title}",
            f"Entreprise / Organisation : {company}",
            f"Localisation : {location}",
            "",
            f"Description de l'offre :\n{description or '(Description non fournie)'}",
            "",
            "--------------------------------------------------",
            "FAITS VÉRIFIÉS DU CANDIDAT :",
            facts_yaml.strip(),
            "--------------------------------------------------",
            "CV DU CANDIDAT :",
            f"{cv_text}",
            "--------------------------------------------------",
        ]

        signals = job.get("signals")
        if not signals and job.get("signals_json"):
            try:
                import json
                signals = json.loads(job["signals_json"]) if isinstance(job["signals_json"], str) else job["signals_json"]
            except Exception:
                signals = {}
        signals = signals or {}

        structure_type = str(job.get("structure_type") or "").upper()
        tone_instructions: list[str] = []

        exp1, exp2 = select_experiences_for_job(job)
        tone_instructions.append(
            f"- CHOIX DES EXPÉRIENCES POUR LA PARTIE MOI : Raconte en priorité {exp1}, puis {exp2} (avec au plus deux chiffres en tout)."
        )

        if structure_type == "LABO_PUBLIC":
            tone_instructions.append(
                "- ORIENTATION DU TON (Laboratoire public de recherche) : Adopte un ton académique, rigoureux et approfondi."
            )
        elif structure_type == "SCALEUP_IA":
            tone_instructions.append(
                "- ORIENTATION DU TON (Scale-up IA de pointe) : Adopte un ton dynamique et orienté produit / impact."
            )
        elif structure_type in ("GRAND_GROUPE_RD", "LABO_PRIVE"):
            tone_instructions.append(
                "- ORIENTATION DU TON (R&D industrielle / labo privé) : Équilibre démarche de recherche appliquée et cas d'usage métiers."
            )

        full_lower = f"{title} {description}".lower()
        explicit_no_thesis = (
            "possibilité de poursuite en thèse : non" in full_lower
            or "poursuite en thèse : non" in full_lower
            or "phd continuation: no" in full_lower
        )
        suite_sig = signals.get("suite_explicite") or {}
        if explicit_no_thesis:
            tone_instructions.append(
                "- INTERDICTION DE MENTIONNER UNE THÈSE : L'offre indique qu'il n'y a pas de poursuite en thèse. Ne parle PAS de thèse ni de CIFRE."
            )
        elif isinstance(suite_sig, dict) and suite_sig.get("present"):
            tone_instructions.append(
                "- PERSPECTIVE DE SUITE / THÈSE (Signal explicite dans l'offre) : L'offre mentionne une perspective de continuité ou de thèse."
            )

        start_mismatch = detect_start_date_mismatch(job)
        if start_mismatch:
            tone_instructions.append(
                f"- CONTRAINTE DE DATE DE DÉBUT : L'offre indique un début ({start_mismatch}). Mentionne simplement dans la conclusion que ton calendrier de double diplôme te rend disponible à partir de début avril 2027 pour six mois."
            )

        if tone_instructions:
            parts.extend([
                "ORIENTATIONS SPÉCIFIQUES POUR CETTE OFFRE :",
                *tone_instructions,
                "--------------------------------------------------",
            ])

        if custom_instruction and custom_instruction.strip():
            parts.extend([
                "CONSIGNES SPÉCIFIQUES DU CANDIDAT POUR CETTE LETTRE :",
                f"{custom_instruction.strip()}",
                "--------------------------------------------------",
            ])

        parts.append(
            "Rédige maintenant la lettre de motivation complète, strictement sans aucun crochet "
            "et en utilisant uniquement les faits vérifiés."
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

    def generate_with_deepseek(
        self,
        job: dict[str, Any],
        cv_text: str | None = None,
        custom_instruction: str | None = None,
    ) -> str | None:
        """Génère la lettre de motivation via le modèle principal : DeepSeek (deepseek-chat)."""
        if not self.deepseek_api_key:
            return None

        cv = cv_text or get_cv_text()
        prompt_content = self._build_user_prompt(job, cv, custom_instruction=custom_instruction)

        headers = {
            "Authorization": f"Bearer {self.deepseek_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": prompt_content},
            ],
            "temperature": self.temperature,
            "max_tokens": 2000,
        }

        try:
            resp = requests.post(
                "https://api.deepseek.com/chat/completions",
                headers=headers,
                json=payload,
                timeout=35,
            )
            resp.raise_for_status()
            data = resp.json()
            raw_text = (data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
            if raw_text:
                self.last_source = "deepseek"
                return self._postprocess_letter(raw_text)
            return None
        except Exception as exc:
            self.last_error = f"⚠️ Erreur DeepSeek : {exc}"
            return None

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

    def _generate_llm_once(
        self,
        job: dict[str, Any],
        cv: str,
        custom_instruction: str | None = None,
    ) -> str | None:
        """Exécute un appel à la cascade LLM (DeepSeek puis Gemini)."""
        prompt_content = self._build_user_prompt(job, cv, custom_instruction=custom_instruction)

        # Niveau 1 : DeepSeek V3 (modèle principal)
        if self.deepseek_api_key:
            ds_result = self.generate_with_deepseek(job, cv_text=cv, custom_instruction=custom_instruction)
            if ds_result and not ds_result.startswith("⚠️"):
                self.last_source = "deepseek"
                return ds_result

        # Niveau 2 : Google Gemini (secours)
        if self.api_key:
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
                    continue
                except Exception as exc:
                    self.last_error = f"⚠️ Erreur : {exc}"
                    continue

        return None

    def generate(
        self,
        job: dict[str, Any],
        cv_text: str | None = None,
        custom_instruction: str | None = None,
        allow_fallback: bool = True,
    ) -> str:
        """Génère une lettre de motivation (DeepSeek V3 prioritaire, Gemini en secours) et la vérifie."""
        self.last_warnings = []
        if not self.available:
            return (
                "⚠️ Clé GEMINI_API_KEY ou DEEPSEEK_API_KEY manquante dans votre fichier .env.\n\n"
                "Veuillez ajouter une clé API pour pouvoir générer des lettres de motivation."
            )

        cv = cv_text or get_cv_text()
        if not cv.strip():
            return "⚠️ Aucun CV trouvé : enregistrez-le dans Paramètres ou définissez le secret CANDIDATE_CV."

        letter = self._generate_llm_once(job, cv, custom_instruction=custom_instruction)
        if letter is not None:
            problems = check_letter(letter, self.facts, job)
            if problems:
                problems_block = "\n".join(f"- {p}" for p in problems)
                retry_instruction = (
                    (f"{custom_instruction.strip()}\n\n" if custom_instruction and custom_instruction.strip() else "")
                    + f"CORRECTIONS OBLIGATOIRES (ton premier brouillon a échoué aux vérifications suivantes) :\n{problems_block}"
                )
                retried = self._generate_llm_once(job, cv, custom_instruction=retry_instruction)
                if retried is not None:
                    letter = retried
                    problems = check_letter(letter, self.facts, job)
            self.last_warnings = problems
            return letter

        # Niveau 3 : Moteur algorithmique déterministe de secours
        if allow_fallback:
            fb_letter = self.generate_fallback(job, cv_text=cv, custom_instruction=custom_instruction)
            self.last_warnings = check_letter(fb_letter, self.facts, job)
            return fb_letter

        return self.last_error or "⚠️ Impossible de générer la lettre après plusieurs tentatives."

    def generate_followup_message(
        self,
        job: dict[str, Any],
        days_since_applied: int = 14,
        channel: str = "email",
        use_llm: bool = False,
    ) -> str:
        """Génère un message de relance (email à J+N) ou d'approche directe (LinkedIn < 300 chars)."""
        cand = self.candidate or DEFAULT_CANDIDATE
        cand_name = cand.get("name") or "Eddy DE CASTRO"
        cand_title = cand.get("title") or "Élève-ingénieur IMT Mines Alès, double diplôme M2 Mathématiques en Action (Mines Saint-Étienne)"

        raw_title = str(job.get("title") or "stage").strip()
        raw_company = str(job.get("company") or "votre entreprise").strip()

        skills: list[str] = []
        raw_skills = (
            job.get("matching_skills")
            or job.get("tech_stack")
            or job.get("match_reasons")
            or job.get("rationale")
            or []
        )
        if isinstance(raw_skills, str):
            skills = [s.strip() for s in re.split(r"[,;•\n]", raw_skills) if s.strip()]
        elif isinstance(raw_skills, (list, tuple, set)):
            skills = [str(s).strip() for s in raw_skills if str(s).strip()]

        if not skills:
            desc_lower = f"{raw_title} {job.get('description', '')}".lower()
            if any(k in desc_lower for k in ("vision", "image", "cv", "segmentation")):
                skills = ["Vision par ordinateur", "Deep Learning"]
            elif any(k in desc_lower for k in ("nlp", "llm", "langage", "texte")):
                skills = ["NLP", "Modèles de fondation"]
            elif any(k in desc_lower for k in ("graph", "graphe", "gnn")):
                skills = ["Graph Machine Learning", "PyTorch"]
            elif any(k in desc_lower for k in ("quant", "incertitude", "conformal", "robuste")):
                skills = ["Quantification d'incertitudes", "Machine Learning robuste"]
            else:
                skills = ["Modélisation statistique", "Machine Learning appliqué"]

        tech_focus = " et ".join(skills[:2]) if len(skills) >= 2 else (skills[0] if skills else "Machine Learning")
        ch = (channel or "email").strip().lower()

        if use_llm and self.available:
            try:
                if ch == "linkedin":
                    prompt = (
                        f"Rédige une note d'invitation LinkedIn ultra-courte (< 280 caractères, strictement sous 300 caractères) "
                        f"pour contacter un recruteur ou Data Lead de {raw_company} concernant le stage {raw_title}. "
                        f"Candidat : {cand_name}, élève-ingénieur Mines Alès & M2 Maths en Action. "
                        f"Point technique : {tech_focus}. Zéro crochet, zéro placeholder."
                    )
                else:
                    prompt = (
                        f"Rédige un email de relance professionnel et poli à J+{days_since_applied} suite à une candidature au stage de {raw_title} chez {raw_company}. "
                        f"Longueur : 90 à 130 mots. Intègre l'objet, rappelle le double cursus mathématiques & IA d'Eddy DE CASTRO, "
                        f"cite {tech_focus}, et réitère la disponibilité pour un stage de 6 mois dès avril 2027. Zéro crochet."
                    )
                resp = self._client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=self.system_prompt,
                        temperature=0.3,
                    ),
                ) if self._client else None
                if resp and resp.text:
                    llm_text = resp.text.strip()
                    if ch == "linkedin" and len(llm_text) <= 300:
                        return llm_text
                    elif ch != "linkedin" and 60 <= len(llm_text.split()) <= 160:
                        return llm_text
            except Exception:
                pass

        if ch == "linkedin":
            msg = (
                f"Bonjour,\n"
                f"Élève-ingénieur Mines Alès & M2 Maths, je candidate au stage de {raw_title} chez {raw_company}. "
                f"Intéressé par vos travaux en {tech_focus}, j'aimerais échanger sur vos projets R&D.\n"
                f"{cand_name}"
            )
            if len(msg) >= 300:
                short_title = raw_title[:24].rstrip() + "..." if len(raw_title) > 27 else raw_title
                msg = (
                    f"Bonjour,\n"
                    f"Élève Mines Alès & M2 Maths, je candidate au stage {short_title} chez {raw_company}. "
                    f"Intéressé par vos travaux en {tech_focus}, j'aimerais échanger sur vos projets R&D.\n"
                    f"{cand_name}"
                )
            if len(msg) >= 300:
                msg = msg[:296] + "..."
            return msg

        subject = f"Objet : Relance — Candidature au stage de {raw_title} — {cand_name}"
        short_title = cand_title.split("—")[0].strip() if "—" in cand_title else cand_title

        return (
            f"{subject}\n\n"
            f"Madame, Monsieur,\n\n"
            f"Je me permets de vous relancer concernant ma candidature transmise il y a {days_since_applied} jours pour le stage de {raw_title} chez {raw_company}.\n\n"
            f"Toujours très motivé à l'idée d'intégrer vos équipes, je vous réitère mon intérêt pour vos projets. "
            f"Mon double cursus en mathématiques appliquées (M2 Mathématiques en Action) et IA (Mines Alès) "
            f"me permettra d'être rapidement opérationnel pour contribuer à vos travaux, en particulier sur {tech_focus}.\n\n"
            f"Disponible dès début avril 2027 pour un stage conventionné de 6 mois, je serais heureux d'échanger avec vous lors d'un entretien.\n\n"
            f"En vous remerciant pour votre attention, je vous prie d'agréer, Madame, Monsieur, mes salutations distinguées.\n\n"
            f"{cand_name}\n"
            f"{short_title}"
        )
