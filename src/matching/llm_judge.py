"""Juge LLM (DeepSeek) — étape 2 du ranking (LLM-as-a-Judge).

Architecture Two-Stage :
  - Étape 1 : Bi-Encoder (src/matching/scorer.py) → pré-filtrage rapide, Top-N.
  - Étape 2 : ce module → ré-évaluation fine du Top-N par un LLM (DeepSeek V3).

La clé API est lue depuis le fichier .env (variable DEEPSEEK_API_KEY).
Aucune exception ne remonte : en cas d'erreur, un fallback défensif est renvoyé.

⚠️ Le juge ne reçoit VOLONTAIREMENT pas le score de l'étape 1 : les verdicts
enregistrés montraient que le modèle commentait ce score au lieu d'évaluer la
mission (biais d'ancrage). Il juge désormais sur le CV et la fiche complète.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any

import httpx

from src.config import PROJECT_ROOT, load_config
from src.constants import (
    DEFAULT_SUB_SCORE,
    SUB_SCORE_KEYS,
    VERDICT_EXCELLENT,
    VERDICT_GOOD,
    VERDICT_MIXED,
    VERDICT_OFF_TOPIC,
    coerce_sub_score,
    first_number,
)

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

# Tolérance sur les verdicts renvoyés par le modèle (accents / variantes).
_VERDICT_ALIASES = {
    "EXCELLENT": VERDICT_EXCELLENT,
    "BON": VERDICT_GOOD,
    "MITIGE": VERDICT_MIXED,
    "MITIGÉ": VERDICT_MIXED,
    "HORS_SUJET": VERDICT_OFF_TOPIC,
    "HORS SUJET": VERDICT_OFF_TOPIC,
    "HORSSUJET": VERDICT_OFF_TOPIC,
}

SYSTEM_PROMPT = """
Tu es le Head of Data d'une scale-up tech de référence, mentor exigeant d'un candidat d'élite. Ton rôle est d'évaluer avec intransigeance l'adéquation d'une offre pour son stage de fin d'études (PFE) afin de lui garantir le meilleur tremplin de carrière possible en Data Science et R&D.

PROFIL DU CANDIDAT
- Formation : Élève-ingénieur Mines Saint-Étienne + Master 2 Recherche MAEA (Mathématiques en Action : optimisation, modélisation stochastique, HPC - Mines Saint-Étienne / Centrale Lyon / ENS Lyon) + Licence 3 Mathématiques Générales.
- Bagage technique : PyTorch (AutoGrad), Graph ML (spectral, Laplacien), Machine Learning appliqué (médical/3D, tabulaire), statistiques inférentielles avancées (bootstrap, tests non paramétriques), Docker, Linux/Bash, SQL, FastAPI, Streamlit, Git.
- Contraintes PFE : Stage conventionné de fin d'études de 6 mois, début début avril, Paris / Île-de-France impératif (ou télétravail partiel/complet compatible).
- Objectif de carrière : Entrer directement par le haut du panier (scale-up Tier 1, grand labo industriel ou académique, pôle R&D de grand groupe tech). Exclure tout rôle d'exécutant ou de support.

VERROUS BLOQUANTS (HARD CAPS)
Si une offre déclenche l'une de ces conditions, plafonne IMMÉDIATEMENT le score global (rerank_score) au plafond indiqué, peu importe la qualité du sujet :
- Alternance stricte, contrat pro ou durée < 5 mois non négociable en PFE : NOTE MAXIMALE = 15.
- Livrable principal axé sur le reporting, dashboards BI (Power BI, Tableau, Excel, Qlik) ou support data analyst : NOTE MAXIMALE = 20.
- Localisation hors Île-de-France sans mention explicite de télétravail compatible : NOTE MAXIMALE = 25.
- « IA » superficielle (simple prompt engineering, wrappers LangChain/API sans modélisation, fine-tuning ni entraînement) : NOTE MAXIMALE = 40.

GRILLE D'ÉVALUATION PAR CRITÈRES (Sous-scores de 1 à 5)
Évalue chaque dimension de manière factuelle (ce qui n'est pas écrit n'existe pas) :

1. modeling_depth (Profondeur mathématique & algorithmique)
- 1 : Simple requêtage SQL, dashboarding, nettoyage de données répétitif ou script d'automatisation.
- 2 : Machine Learning basique de surface (scikit-learn générique, régression/clustering simple sans feature engineering avancé).
- 3 : Vrai Machine Learning / Deep Learning appliqué avec modélisation solide et pipeline de validation rigoureux.
- 4 : Deep Learning avancé, Computer Vision 3D, NLP/LLM open-weights avec fine-tuning, ou pipelines d'optimisation complexes.
- 5 : R&D de pointe, formulation mathématique sur mesure (fonctions de perte custom, optimisation non convexe, Graph ML, physique/IA).

2. mentorship_team (Qualité de l'encadrement & séniorité)
- 1 : Stagiaire isolé sur la data ou encadré uniquement par des profils business/produit sans compétences ML.
- 2 : Équipe tech sans data scientists seniors identifiés, encadrement flou.
- 3 : Équipe Data Science existante avec des seniors capables de relire du code et cadrer les projets.
- 4 : Pôle ML structuré, Lead Data Scientists expérimentés, méthodologies d'ingénierie robustes (MLOps, revues de code).
- 5 : Chercheurs (PhD), Staff ML Engineers reconnus, laboratoire de recherche ou équipe de référence internationale.

3. career_leverage (Prestige & tremplin professionnel)
- 1 : ESN non spécialisée ou société de conseil en régie avec incertitude sur la mission finale.
- 2 : Entreprise traditionnelle avec faible culture tech/data, stage peu différenciant sur un CV.
- 3 : PME technologique solide, grande entreprise reconnue ou scale-up établie avec visibilité marché correcte.
- 4 : Scale-up tech en forte croissance (Tier 1/2) ou grand pôle R&D industriel très valorisé par les recruteurs.
- 5 : Acteur de premier plan mondial de l'IA (Inria, CEA, licornes IA, labos tech d'élite), impact direct garanti sur le réseau.

4. pfe_compatibility (Adéquation PFE, dates & logistique)
- 1 : Incompatible (alternance imposée, césure 3 mois, hors IDF sans remote).
- 3 : Partiellement compatible mais ambigu (mention "stage ou alternance", date floue).
- 5 : Parfaitement aligné (stage conventionné 6 mois, démarrage mars/avril, Paris/remote).

FORMAT DE SORTIE (JSON STRICT)
Réponds UNIQUEMENT avec un objet JSON valide, sans texte d'introduction ni balises superflues. Remplis les champs dans cet ordre précis :

{
  "reasoning": "<Synthèse critique en 3 phrases : adéquation du calendrier, réalité mathématique de la mission vs buzzwords, calibre de l'encadrement>",
  "hard_cap_triggered": "<Nom de la contrainte bloquante déclenchée, ou null>",
  "sub_scores": {
    "modeling_depth": <Entier de 1 à 5>,
    "mentorship_team": <Entier de 1 à 5>,
    "career_leverage": <Entier de 1 à 5>,
    "pfe_compatibility": <Entier de 1 à 5>
  },
  "match_reasons": ["<Point fort factuel 1>", "<Point fort factuel 2>"],
  "red_flags": ["<Risque ou manque d'information 1>", "<Risque 2>"],
  "tech_stack_detected": ["<Techno 1>", "<Techno 2>"],
  "verdict": "EXCELLENT" | "BON" | "MITIGÉ" | "HORS_SUJET",
  "rerank_score": <Entier de 0 à 100 reflétant les sous-scores et plafonné par les hard caps>
}

RÈGLES D'ALIGNEMENT DU SCORE GLOBAL :
- EXCELLENT (85-100) : modeling_depth >= 4, mentorship_team >= 4, pfe_compatibility = 5, aucun hard cap.
- BON (65-84) : modeling_depth >= 3, encadrement solide, PFE compatible.
- MITIGÉ (40-64) : mission générique, manque de visibilité sur l'encadrement ou stack standard.
- HORS_SUJET (0-39) : hard cap déclenché, reporting ou inadéquation PFE.
"""


def load_env_file(path: str | Path | None = None) -> None:
    """Charge les variables d'un fichier .env dans os.environ (sans écraser l'existant)."""
    env_path = Path(path) if path else (PROJECT_ROOT / ".env")
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def verdict_from_score(score: float) -> str:
    """Déduit un verdict (fallback) à partir d'un score 0-100.

    Bandes alignées sur la grille du juge : EXCELLENT ≥ 85, BON ≥ 65, MITIGÉ ≥ 40,
    HORS_SUJET en deçà.
    """
    if score >= 85:
        return VERDICT_EXCELLENT
    if score >= 65:
        return VERDICT_GOOD
    if score >= 40:
        return VERDICT_MIXED
    return VERDICT_OFF_TOPIC


def _as_str_list(value: Any) -> list[str]:
    """Normalise une valeur hétérogène en liste de chaînes non vides."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


# --- Verrous bloquants (hard caps) ------------------------------------------- #
# Le prompt demande au modèle de plafonner lui-même le score, mais on applique ici
# un filet défensif : si un verrou est signalé, le score est borné par son plafond
# quoi que le modèle ait répondu (garantie d'alignement indépendante du LLM).
_HARD_CAPS: tuple[tuple[tuple[str, ...], int], ...] = (
    # Contrat / durée
    (("alternance", "contrat pro", "apprentissage", "durée", "5 mois",
      "césure", "cesure", "cursus"), 15),
    # Reporting / BI / support data
    (("reporting", "dashboard", "power bi", "qlik", "business intelligence",
      "analyst", "support", "excel", "tableau"), 20),
    # Localisation
    (("localisation", "localization", "île-de-france", "ile-de-france", "idf",
      "télétravail", "teletravail", "remote", "paris", "géographi", "geographi"), 25),
    # « IA » superficielle
    (("superficiel", "superficielle", "prompt engineering", "wrapper", "langchain",
      "sans modélisation", "sans modelisation", "automatisation"), 40),
)


def _normalize_hard_cap(value: Any) -> str | None:
    """Normalise ``hard_cap_triggered`` en chaîne, ou ``None`` si aucun verrou."""
    text = str(value or "").strip()
    if not text or text.casefold() in ("null", "none", "aucun", "aucune"):
        return None
    return text


def hard_cap_max(reason: str | None) -> int | None:
    """Plafond associé à un verrou bloquant (``None`` = pas de plafond).

    Correspondance par mots-clés, volontairement défensive : ``hard_cap_triggered``
    est un nom libre, on matche donc les familles de contraintes (contrat, BI,
    localisation, « IA » superficielle).
    """
    if not reason:
        return None
    lowered = reason.casefold()
    for keywords, max_score in _HARD_CAPS:
        if any(keyword in lowered for keyword in keywords):
            return max_score
    return None


def _key_signature(name: Any) -> str:
    """Signature insensible à la casse, aux accents et aux séparateurs d'une clé.

    ``"modeling_depth"``, ``"modelingDepth"`` et ``"Modeling Depth"`` produisent la
    même signature (``modelingdepth``).
    """
    decomposed = unicodedata.normalize("NFKD", str(name or "").casefold())
    ascii_only = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]", "", ascii_only)


#: Correspondance signature -> clé canonique des sous-scores (tolérance de forme).
_SUB_SCORE_ALIASES: dict[str, str] = {_key_signature(key): key for key in SUB_SCORE_KEYS}


def _normalize_sub_scores(value: Any) -> dict[str, int]:
    """Normalise ``sub_scores`` en dict complet ``{clé: entier 1-5}``.

    Les clés sont comparées **après normalisation** (casse, accents, séparateurs) :
    un modèle qui répond ``modelingDepth``, ``modeling depth`` ou
    ``Modeling-Depth`` est compris, sans perdre l'information. Champ manquant,
    JSON non-dictionnaire ou valeur inexploitable ⇒ valeur neutre
    (``DEFAULT_SUB_SCORE``) : l'interface reste stable.
    """
    if not isinstance(value, dict):
        return {key: DEFAULT_SUB_SCORE for key in SUB_SCORE_KEYS}
    provided: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        canonical = _SUB_SCORE_ALIASES.get(_key_signature(raw_key))
        if canonical is not None and canonical not in provided:
            provided[canonical] = raw_value
    return {
        key: coerce_sub_score(provided.get(key, DEFAULT_SUB_SCORE)) for key in SUB_SCORE_KEYS
    }


class LLMJudge:
    """Étape 2 : ré-évaluation fine d'une offre via l'API DeepSeek (deepseek-chat)."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        api_key: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.config = config or load_config()
        llm_cfg = self.config.get("llm", {})
        self.base_url = str(llm_cfg.get("base_url", DEFAULT_BASE_URL)).rstrip("/")
        self.model = str(llm_cfg.get("model", DEFAULT_MODEL))
        self.temperature = float(llm_cfg.get("temperature", 0.0))
        self.timeout = float(llm_cfg.get("timeout_seconds", 60))
        self.client = client  # injectable (tests via httpx.MockTransport)

        if api_key is not None:
            self.api_key = api_key
        else:
            load_env_file()
            self.api_key = os.environ.get("DEEPSEEK_API_KEY", "")

    @property
    def available(self) -> bool:
        """True si une clé API est configurée."""
        return bool(self.api_key)

    # ------------------------------------------------------------------ #
    # Construction des messages
    # ------------------------------------------------------------------ #
    def _build_messages(
        self, job: dict[str, Any], cv_text: str | None = None
    ) -> list[dict[str, str]]:
        # Aucun score de l'étape 1 n'est transmis : constaté en pratique, le juge
        # commentait le score bi-encoder au lieu de juger la mission (« le score
        # préliminaire est faible, mais… »). Le juge doit statuer sur les FAITS
        # (CV + description complète), sans ancre numérique.
        description = (job.get("description") or "").strip()[:6000]
        user_content = (
            "OFFRE DE STAGE À ÉVALUER\n"
            f"Titre : {job.get('title', '')}\n"
            f"Entreprise : {job.get('company', '')} (typologie tier {job.get('company_tier', '?')})\n"
            f"Localisation : {job.get('location', '')}\n\n"
            f"Description :\n{description or '(description indisponible)'}\n"
        )
        if cv_text:
            user_content += f"\nCV DU CANDIDAT (extrait) :\n{cv_text[:3000]}\n"
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

    # ------------------------------------------------------------------ #
    # Appel API
    # ------------------------------------------------------------------ #
    def _post_chat(self, messages: list[dict[str, str]]) -> str:
        """Appelle l'endpoint chat/completions et retourne le contenu texte."""
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        client = self.client or httpx.Client(timeout=self.timeout)
        close_client = self.client is None
        try:
            response = client.post(
                f"{self.base_url}/chat/completions", json=payload, headers=headers
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
        finally:
            if close_client:
                client.close()

    # ------------------------------------------------------------------ #
    # Parsing / normalisation
    # ------------------------------------------------------------------ #
    def _parse_response(self, content: str | None, job: dict[str, Any]) -> dict[str, Any]:
        """Parse la réponse JSON du LLM en structure normalisée et fiable.

        Extrait, en plus du score et du verdict, la grille de sous-scores
        qualitatifs (1-5) et le verrou bloquant éventuel. Le score global est
        plafonné par le hard cap, puis le verdict est re-dérivé du score pour
        garantir l'alignement (un modèle qui répondrait EXCELLENT avec un score
        plafonné à 20 est corrigé).
        """
        cleaned = _CODE_FENCE_RE.sub("", content or "").strip()
        try:
            data = json.loads(cleaned)
        except (json.JSONDecodeError, TypeError) as exc:
            return self._fallback(job, reason=f"Réponse LLM non parsable ({exc}).")
        if not isinstance(data, dict):
            return self._fallback(job, reason="Réponse LLM invalide (JSON non-objet).")

        score = self._coerce_score(data.get("rerank_score"), job)

        hard_cap = _normalize_hard_cap(data.get("hard_cap_triggered"))
        cap = hard_cap_max(hard_cap)
        if cap is not None:
            score = min(score, cap)

        raw_verdict = str(data.get("verdict", "")).strip().upper()
        verdict = _VERDICT_ALIASES.get(raw_verdict)
        if verdict != verdict_from_score(score):
            verdict = verdict_from_score(score)

        return {
            "rerank_score": score,
            "verdict": verdict,
            "sub_scores": _normalize_sub_scores(data.get("sub_scores")),
            "hard_cap_triggered": hard_cap,
            "reasoning": str(data.get("reasoning") or "").strip(),
            "match_reasons": _as_str_list(data.get("match_reasons")),
            "red_flags": _as_str_list(data.get("red_flags")),
            "tech_stack": _as_str_list(data.get("tech_stack_detected")),
        }

    @staticmethod
    def _coerce_score(value: Any, job: dict[str, Any]) -> int:
        """Convertit une valeur en entier borné 0-100, sinon retombe sur final_score.

        Tolérant à la forme : ``85``, ``"85/100"``, ``"Score : 85"`` ou ``"85 %"``
        sont tous lus comme 85 — un modèle qui répond ``85/100`` ne doit pas faire
        perdre son jugement au profit du score de l'étape 1.
        """
        number = first_number(value)
        if number is None:
            number = first_number(job.get("final_score"))
        if number is None:
            return 0
        return max(0, min(100, int(round(number))))

    def _fallback(self, job: dict[str, Any], reason: str = "") -> dict[str, Any]:
        """Fallback défensif : réutilise le score initial et signale le problème."""
        score = self._coerce_score(job.get("final_score"), job)
        return {
            "rerank_score": score,
            "verdict": verdict_from_score(score),
            "sub_scores": {key: DEFAULT_SUB_SCORE for key in SUB_SCORE_KEYS},
            "hard_cap_triggered": None,
            "reasoning": "",
            "match_reasons": [],
            "red_flags": [reason] if reason else [],
            "tech_stack": [],
        }

    # ------------------------------------------------------------------ #
    # API publique
    # ------------------------------------------------------------------ #
    def judge(self, job: dict[str, Any], cv_text: str | None = None) -> dict[str, Any]:
        """Évalue une offre. Ne lève JAMAIS d'exception (fallback garanti)."""
        if not self.available:
            return self._fallback(job, reason="DEEPSEEK_API_KEY absente — analyse LLM ignorée.")
        try:
            content = self._post_chat(self._build_messages(job, cv_text))
            return self._parse_response(content, job)
        except httpx.HTTPStatusError as exc:
            return self._fallback(
                job, reason=f"Erreur HTTP API DeepSeek ({exc.response.status_code})."
            )
        except httpx.HTTPError as exc:
            return self._fallback(job, reason=f"Erreur réseau API DeepSeek : {exc}.")
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            return self._fallback(job, reason=f"Réponse API DeepSeek inattendue : {exc}.")
