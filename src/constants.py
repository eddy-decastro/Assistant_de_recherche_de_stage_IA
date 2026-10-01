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
STATUS_EXCLUDED = "EXCLU"

VALID_STATUSES = {
    STATUS_NEW,
    STATUS_APPLIED,
    STATUS_INTERVIEW,
    STATUS_IGNORED,
    STATUS_REJECTED,
    STATUS_EXCLUDED,
}

# Ordre d'affichage dans le dashboard : statuts de travail (hors flux par défaut
# pour l'offre écartée, qui reste consultable en cochant explicitement « Rejeté »).
STATUS_ORDER = [STATUS_NEW, STATUS_APPLIED, STATUS_INTERVIEW, STATUS_IGNORED]

# Options proposées par le sélecteur de statut (le rejet est opt-in).
STATUS_OPTIONS = [*STATUS_ORDER, STATUS_REJECTED, STATUS_EXCLUDED]

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
# Les cinq dimensions de la nouvelle grille calibrée :
SUB_SCORE_KEYS = (
    "technical_depth",
    "target_alignment",
    "learning_environment",
    "logistics",
)

# Coefficients de pondération du prompt v3 (somme = 1.0)
SUB_SCORE_WEIGHTS = {
    "technical_depth": 0.35,
    "target_alignment": 0.20,
    "learning_environment": 0.30,
    "logistics": 0.15,
}

# Valeur neutre par défaut pour une information absente (3 selon la règle v3)
DEFAULT_SUB_SCORE = 3

SUB_SCORE_LABELS = {
    "technical_depth": "Profondeur technique",
    "target_alignment": "Adéquation au sujet cible",
    "learning_environment": "Cadre d'apprentissage",
    "logistics": "Logistique",
    # Rétro-compatibilité / affichage des évaluations v1
    "supervision": "Encadrement / Mentorship",
    "real_impact": "Impact du Livrable",
    "structure_fit": "Adéquation Structure",
    "next_step": "Débouchés / Thèse",
    "modeling_depth": "Modélisation",
    "mentorship_team": "Encadrement",
    "engineering_practice": "Pratiques d'ingénierie",
    "option_value": "Thèse / CDI",
    "career_leverage": "Carrière",
    "pfe_compatibility": "Calendrier PFE",
}

# Libellés compacts pour la ligne de mini-indicateurs affichée sur la carte
SUB_SCORE_SHORT_LABELS = {
    "technical_depth": "Technique",
    "target_alignment": "Alignement",
    "learning_environment": "Cadre",
    "logistics": "Logistique",
    # Rétro v1
    "supervision": "Équipe",
    "real_impact": "Impact",
    "structure_fit": "Structure",
    "next_step": "Débouché",
    "modeling_depth": "Modélisation",
    "mentorship_team": "Équipe",
    "engineering_practice": "Ingénierie",
    "option_value": "Tremplin",
    "career_leverage": "Carrière",
    "pfe_compatibility": "Calendrier PFE",
}

# --- Énumérations et règles du juge v3 ---
CONTRACT_TYPES = (
    "STAGE",
    "STAGE_OU_ALTERNANCE",
    "ALTERNANCE",
    "CDI_CDD",
    "AUTRE",
)

CONTRACT_TYPE_LABELS = {
    "STAGE": "Stage",
    "STAGE_OU_ALTERNANCE": "Stage ou alternance",
    "ALTERNANCE": "Alternance",
    "CDI_CDD": "CDI / CDD / Salarié",
    "AUTRE": "Contrat non précisé",
}

STRUCTURE_TYPES = (
    "ESN_CONSEIL",
    "LABO_PUBLIC",
    "LABO_PRIVE",
    "GRAND_GROUPE_RD",
    "SCALEUP_IA",
    "STARTUP_PETITE",
    "AUTRE",
    "INCONNU",
)

STRUCTURE_TYPE_LABELS = {
    "SCALEUP_IA": "Scale-up IA",
    "GRAND_GROUPE_RD": "Grand groupe R&D",
    "LABO_PRIVE": "Labo privé",
    "LABO_PUBLIC": "Labo public",
    "STARTUP_PETITE": "Petite startup (<15)",
    "ESN_CONSEIL": "ESN / Conseil",
    "AUTRE": "Autre entreprise",
    "INCONNU": "Structure inconnue",
}

STRUCTURE_TYPE_TONES = {
    "SCALEUP_IA": "positive",      # vert
    "GRAND_GROUPE_RD": "positive", # vert
    "LABO_PRIVE": "positive",      # vert
    "LABO_PUBLIC": "positive",     # vert
    "STARTUP_PETITE": "warn",      # orange
    "AUTRE": "mute",
    "INCONNU": "warn",             # orange
    "ESN_CONSEIL": "alert",        # rouge
}

# Plafonds stricts (hard caps) v3
HARD_CAP_RULES: dict[str, int] = {
    "DEFENSE": 10,
    "TRADING": 25,
    "BI_REPORTING": 30,
    "ESN_REGIE": 35,
    "ENCADREMENT_ABSENT": 35,
    "SHALLOW_AI": 40,
}

HARD_CAP_LABELS: dict[str, str] = {
    "DEFENSE": "Défense / Armement (10)",
    "TRADING": "Trading / HFT (25)",
    "BI_REPORTING": "BI / Reporting (30)",
    "ESN_REGIE": "ESN en régie (35)",
    "ENCADREMENT_ABSENT": "Encadrement absent (35)",
    "SHALLOW_AI": "IA superficielle (40)",
}

FLAGS = (
    "CALENDRIER_DECALE",
    "DUREE_INCERTAINE",
    "HORS_IDF",
    "PETITE_STARTUP",
    "STACK_FLOUE",
    "DEFENSE_INDIRECTE",
    "ETHIQUE_A_EXAMINER",
    "FINANCE",
)

FLAG_LABELS: dict[str, str] = {
    "CALENDRIER_DECALE": "Calendrier décalé",
    "DUREE_INCERTAINE": "Durée incertaine",
    "HORS_IDF": "Hors Île-de-France",
    "PETITE_STARTUP": "Petite startup",
    "STACK_FLOUE": "Stack floue",
    "DEFENSE_INDIRECTE": "Défense indirecte",
    "ETHIQUE_A_EXAMINER": "Éthique à examiner",
    "FINANCE": "Finance",
}

FLAG_TONES: dict[str, str] = {
    "DEFENSE_INDIRECTE": "alert",   # rouge
    "ETHIQUE_A_EXAMINER": "alert",  # rouge
    "CALENDRIER_DECALE": "warn",    # orange
    "DUREE_INCERTAINE": "warn",     # orange
    "HORS_IDF": "warn",             # orange
    "PETITE_STARTUP": "warn",       # orange
    "STACK_FLOUE": "warn",          # orange
    "FINANCE": "warn",              # orange
}

SIGNAL_BONUSES: dict[str, int] = {
    "encadrant_explicite": 6,
    "donnees_reelles_explicites": 3,
    "suite_explicite": 3,
}
BENCHMARK_PENALTY = 5
MAX_BONUS_TOTAL = 10



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
    "manuel": "Saisie manuelle",
    "gmail": "Gmail",
}

# Couleur de badge par plateforme (repli : gris ardoise).
SOURCE_COLORS = {
    "linkedin": "#1C1B19",
    "wttj": "#A8761F",
    "welcome_to_the_jungle": "#A8761F",
    "jobteaser": "#A8761F",
}

SOURCE_FALLBACK_COLOR = "#475569"

# Ordre d'affichage préféré des plateformes ; les sources inconnues suivent.
SOURCE_ORDER = ["linkedin", "wttj", "welcome_to_the_jungle", "jobteaser"]

# Candidatures enregistrées hors scraping (formulaire du Kanban, import des mails).
SOURCE_MANUAL = "manuel"
SOURCE_GMAIL = "gmail"
# Motif posé sur une offre passée en REJETÉ parce que l'entreprise a refusé la
# candidature (différent d'une exclusion par la re-validation métier).
REFUSAL_REASON = "Refus de l'entreprise"


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
#: Marqueurs de ``scrape_runs.notes`` lus par la détection de source dégradée :
#: un run personnalisé (requêtes, sources, passes ou plafond forcés) et une source
#: déjà signalée en panne ne doivent pas servir de référence.
RUN_NOTE_ADHOC = "adhoc"
RUN_NOTE_DEGRADED = "degraded:"

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
    "max_pages_saturated": "Plafond de pages atteint sur des offres déjà connues (rien de perdu)",
    "duplicate_page": "Page déjà vue (pagination stagnante)",
    "rate_limit": "Rate limit (429) — flux perdu",
    "http_error": "Erreur HTTP — flux perdu",
    "network_error": "Erreur réseau — flux perdu",
    "auth_missing": "Authentification absente (cookies / jeton)",
    "auth_expired": "Session expirée (page de connexion) — flux perdu",
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


# --- Décisions de la mémoire de collecte (colonne ``seen_jobs.decision``) ------
# Vocabulaire du pilotage : « qu'est-ce que la collecte a croisé, retenu, écarté ? »
SEEN_DECISION_LABELS = {
    SEEN_VALIDATED: "Retenue",
    SEEN_REJECTED_BI: "Refusée — hors sujet",
    SEEN_REJECTED_CONTRACT: "Refusée — contrat incompatible",
    SEEN_OUT_OF_WINDOW: "Hors fenêtre temporelle",
    SEEN_DUPLICATE: "Doublon du run",
    SEEN_KNOWN: "Déjà connue",
}


def seen_decision_label(decision: str | None) -> str:
    """Libellé lisible d'une décision de mémoire de collecte (repli : valeur brute)."""
    if not decision:
        return "Inconnue"
    return SEEN_DECISION_LABELS.get(decision, decision)


# --- Types de passe de la collecte hybride ------------------------------------
# Les identifiants de mode sont définis par les scrapers (source de vérité) et
# ré-exportés ici avec leurs libellés d'affichage.
from scrapers.models import (  # noqa: E402  (ré-export volontaire)
    PASS_FRESHNESS,
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
