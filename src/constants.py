"""Constantes métier partagées par l'ensemble du projet."""

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

VALID_STATUSES = {
    STATUS_NEW,
    STATUS_APPLIED,
    STATUS_INTERVIEW,
    STATUS_IGNORED,
}

# Ordre d'affichage dans le dashboard
STATUS_ORDER = [STATUS_NEW, STATUS_APPLIED, STATUS_INTERVIEW, STATUS_IGNORED]

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
