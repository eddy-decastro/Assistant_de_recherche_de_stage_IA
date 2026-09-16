"""Constantes métier partagées par l'ensemble du projet."""

import re
from typing import Any

# Vocabulaire des décisions de collecte et motifs d'arrêt : défini par les
# scrapers (qui décident), ré-exporté ici pour la persistance, l'observabilité et
# le dashboard — une seule source de vérité, donc aucune divergence possible
# entre « qui décide » et « qui archive ».
from scrapers.models import (  # noqa: F401  (ré-export volontaire)
    SEEN_DECISIONS,
    SEEN_DUPLICATE,
    SEEN_KNOWN,
    SEEN_OUT_OF_WINDOW,
    SEEN_REJECTED_BI,
    SEEN_REJECTED_CONTRACT,
    SEEN_VALIDATED,
    is_incomplete_stop,
)

# --- Typologie d'entreprise (company_tier) ---
TIER_1 = 1       # Scale-up / Lab de recherche — meilleure note
TIER_NEUTRAL = 2 # Grand groupe tech / entreprise non listée
TIER_ESN = 3     # ESN / SSII — pénalisée

TIER_LABELS = {
    TIER_1: "Tier 1",
    TIER_NEUTRAL: "Neutre",
    TIER_ESN: "ESN",
}

# --- Statuts de candidature ---
STATUS_NEW = "NOUVEAU"
STATUS_APPLIED = "POSTULÉ"
STATUS_INTERVIEW = "ENTRETIEN"
STATUS_IGNORED = "IGNORÉ"
# Offre écartée par la re-validation métier sur texte complet (faux stage, BI…).
STATUS_REJECTED = "REJETÉ"

VALID_STATUSES = {
    STATUS_NEW,
    STATUS_APPLIED,
    STATUS_INTERVIEW,
    STATUS_IGNORED,
    STATUS_REJECTED,
}

# Ordre d'affichage dans le dashboard : statuts de travail (hors flux par défaut
# pour l'offre écartée, qui reste consultable en cochant explicitement « Rejeté »).
STATUS_ORDER = [STATUS_NEW, STATUS_APPLIED, STATUS_INTERVIEW, STATUS_IGNORED]

# Options proposées par le sélecteur de statut (le rejet est opt-in).
STATUS_OPTIONS = [*STATUS_ORDER, STATUS_REJECTED]

# --- Verdicts du juge LLM (étape 2 : reranking) ---
VERDICT_EXCELLENT = "EXCELLENT"
VERDICT_GOOD = "BON"
VERDICT_MIXED = "MITIGÉ"
VERDICT_OFF_TOPIC = "HORS_SUJET"

VERDICTS = [VERDICT_EXCELLENT, VERDICT_GOOD, VERDICT_MIXED, VERDICT_OFF_TOPIC]

# Libellés affichés (dashboard, rapports de validation) — source unique de vérité.
VERDICT_LABELS = {
    VERDICT_EXCELLENT: "Excellent",
    VERDICT_GOOD: "Bon",
    VERDICT_MIXED: "Mitigé",
    VERDICT_OFF_TOPIC: "Hors sujet",
}

VERDICT_COLORS = {
    VERDICT_EXCELLENT: "#16a34a",  # vert
    VERDICT_GOOD: "#2563eb",       # bleu
    VERDICT_MIXED: "#f59e0b",      # orange
    VERDICT_OFF_TOPIC: "#dc2626",  # rouge
}

# --- Sous-scores du juge LLM (grille d'évaluation qualitative, échelle 1-5) ---
# Les quatre dimensions de la grille ; l'ordre est celui d'affichage dans l'UI.
SUB_SCORE_KEYS = (
    "modeling_depth",
    "mentorship_team",
    "career_leverage",
    "pfe_compatibility",
)
# Valeur neutre utilisée quand un champ de sous-score est manquant ou inexploitable
# (repli sécurisé en cas d'erreur de parsing de la réponse du modèle).
DEFAULT_SUB_SCORE = 3

SUB_SCORE_LABELS = {
    "modeling_depth": "Modélisation",
    "mentorship_team": "Encadrement",
    "career_leverage": "Carrière",
    "pfe_compatibility": "Calendrier PFE",
}

# Libellés compacts, pour la ligne de mini-indicateurs affichée sur la carte
# (forme courte, alignée sur l'usage : « 📐 Modélisation : 4/5 | 👥 Équipe : 5/5 »).
SUB_SCORE_SHORT_LABELS = {
    "modeling_depth": "Modélisation",
    "mentorship_team": "Équipe",
    "career_leverage": "Carrière",
    "pfe_compatibility": "PFE",
}


_NUMBER_RE = re.compile(r"-?\d+(?:[.,]\d+)?")


def first_number(value: Any) -> float | None:
    """Premier nombre d'une valeur renvoyée par un LLM (``None`` si aucun).

    Tolère les formes que produisent réellement les modèles malgré la consigne
    « entier » : ``85``, ``85.0``, ``"85/100"``, ``"Score : 85"``, ``"85 %"``,
    ``"4,5"``. Utilisé pour le score global comme pour les sous-scores.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = _NUMBER_RE.search(str(value or ""))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", "."))
    except ValueError:
        return None


def coerce_sub_score(value: Any) -> int:
    """Borne un sous-score dans [1, 5] (repli neutre ``DEFAULT_SUB_SCORE`` sinon).

    Tolérant à la forme (``"4/5"``, ``4.0``, ``"5"``) : un modèle qui répond
    « 4/5 » ne doit pas faire perdre l'information.
    """
    number = first_number(value)
    if number is None:
        return DEFAULT_SUB_SCORE
    return max(1, min(5, int(round(number))))

# --- Plateformes source des offres (colonne jobs.source) ---
# Les scrapers unifiés écrivent "wttj" ; l'ingestion historique WTTJ
# (src/ingestion/wttj.py) écrivait "welcome_to_the_jungle" : les deux valeurs
# désignent la même plateforme et partagent donc le même libellé.
SOURCE_LABELS = {
    "linkedin": "LinkedIn",
    "wttj": "Welcome to the Jungle",
    "welcome_to_the_jungle": "Welcome to the Jungle",
    "jobteaser": "JobTeaser",
}

# Couleur de badge par plateforme (repli : gris ardoise).
SOURCE_COLORS = {
    "linkedin": "#0a66c2",
    "wttj": "#ca8a04",
    "welcome_to_the_jungle": "#ca8a04",
    "jobteaser": "#7c3aed",
}

SOURCE_FALLBACK_COLOR = "#475569"

# Ordre d'affichage préféré des plateformes ; les sources inconnues suivent.
SOURCE_ORDER = ["linkedin", "wttj", "welcome_to_the_jungle", "jobteaser"]


def source_label(source: str | None) -> str:
    """Libellé lisible d'une plateforme source (repli : valeur brute ou 'Inconnue')."""
    if not source:
        return "Inconnue"
    return SOURCE_LABELS.get(source, source)


def source_rank(source_or_label: str | None) -> int:
    """Rang d'affichage d'une plateforme (``SOURCE_ORDER``, inconnues en dernier).

    Accepte indifféremment la valeur technique (``wttj``) ou le libellé affiché
    (``Welcome to the Jungle``), ce qui permet de trier des groupes déjà étiquetés.
    """
    label = source_label(source_or_label)
    for index, known in enumerate(SOURCE_ORDER):
        if source_label(known) == label:
            return index
    return len(SOURCE_ORDER)


# --- Décisions de la mémoire de collecte --------------------------------------
# ``SEEN_VALIDATED``, ``SEEN_REJECTED_BI``… sont ré-exportés depuis
# ``scrapers.models`` (voir l'import en tête de module) : les décisions sont prises
# par le moteur de collecte, qui en est la source de vérité.

# --- Cycle de vie d'un run de collecte (table ``scrape_runs``) ---
RUN_RUNNING = "RUNNING"
RUN_OK = "OK"
RUN_PARTIAL = "PARTIAL"   # au moins une passe interrompue (rate limit, erreur…)
RUN_ERROR = "ERROR"
#: Run jamais clos (processus tué, coupure) : marqué au démarrage du run suivant.
#: C'est un signal d'observabilité à part entière : la collecte a pu être tronquée.
RUN_INTERRUPTED = "INTERRUPTED"

#: Libellés affichés pour l'état d'un run de collecte (dashboard télémétrie).
RUN_LABELS = {
    RUN_RUNNING: "En cours",
    RUN_OK: "Terminé",
    RUN_PARTIAL: "Partiel (flux tronqué)",
    RUN_ERROR: "Erreur",
    RUN_INTERRUPTED: "Interrompu",
}


def run_label(status: str | None) -> str:
    """Libellé lisible d'un état de run (repli : valeur brute ou « Inconnu »)."""
    if not status:
        return "Inconnu"
    return RUN_LABELS.get(status, status)

# --- Motifs d'arrêt d'une passe (colonne ``scrape_query_stats.stop_reason``) ---
STOP_REASON_LABELS = {
    "quota": "Quota atteint",
    "early_stop": "Arrêt anticipé (jonction avec le scrape précédent)",
    "window_end": "Hors fenêtre temporelle (flux épuisé)",
    "stream_end": "Fin de flux (plus de résultats)",
    "max_pages": "Plafond de pages atteint (flux potentiellement tronqué)",
    "duplicate_page": "Page déjà vue (pagination stagnante)",
    "rate_limit": "Rate limit (429) — flux perdu",
    "http_error": "Erreur HTTP — flux perdu",
    "network_error": "Erreur réseau — flux perdu",
    "auth_missing": "Authentification absente (cookies / jeton)",
    "unsupported": "Tri ou pagination non supporté par la source",
    "disabled": "Passe désactivée par configuration",
    "error": "Erreur inattendue",
}

# Motifs signalant une perte de flux (rate limit, plafond…) : ``is_incomplete_stop``
# est ré-exporté depuis ``scrapers.models`` (voir l'import en tête de module).


def stop_reason_label(reason: str | None) -> str:
    """Libellé lisible d'un motif d'arrêt (repli : valeur brute ou « Inconnu »)."""
    if not reason:
        return "Inconnu"
    return STOP_REASON_LABELS.get(reason, reason)


# --- Types de passe de la collecte hybride ------------------------------------
# Les identifiants de mode sont définis par les scrapers (source de vérité) et
# ré-exportés ici avec leurs libellés d'affichage.
from scrapers.models import (  # noqa: E402  (ré-export volontaire)
    PASS_FRESHNESS,
    PASS_MODES,
    PASS_RELEVANCE,
)

PASS_LABELS = {
    PASS_FRESHNESS: "Fraîcheur (tri par date)",
    PASS_RELEVANCE: "Rattrapage (tri par pertinence)",
}


def pass_label(mode: str | None) -> str:
    """Libellé lisible d'un mode de collecte (repli : valeur brute ou « Inconnu »)."""
    if not mode:
        return "Inconnu"
    return PASS_LABELS.get(mode, mode)
