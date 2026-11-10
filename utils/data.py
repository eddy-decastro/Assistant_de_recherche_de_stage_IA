from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence
from dataclasses import dataclass
import streamlit as st
import html
from src.constants import (
    REINSTATED_REASON,
    BENCHMARK_PENALTY,
    CONTRACT_TYPE_LABELS,
    CONTRACT_TYPES,
    DEFAULT_SUB_SCORE,
    FLAG_LABELS,
    FLAG_TONES,
    FLAGS,
    HARD_CAP_LABELS,
    HARD_CAP_RULES,
    MAX_BONUS_TOTAL,
    PASS_FRESHNESS,
    PASS_LABELS,
    PASS_RELEVANCE,
    REFUSAL_REASON,
    RUN_ERROR,
    RUN_INTERRUPTED,
    RUN_LABELS,
    RUN_NOTE_ADHOC,
    RUN_NOTE_DEGRADED,
    RUN_OK,
    RUN_PARTIAL,
    RUN_RUNNING,
    SEEN_DECISION_LABELS,
    SEEN_DECISIONS,
    SEEN_DUPLICATE,
    SEEN_KNOWN,
    SEEN_OUT_OF_WINDOW,
    SEEN_REJECTED_BI,
    SEEN_REJECTED_CONTRACT,
    SEEN_REJECTED_TITLE,
    SEEN_VALIDATED,
    SIGNAL_BONUSES,
    SOURCE_COLORS,
    SOURCE_FALLBACK_COLOR,
    SOURCE_GMAIL,
    SOURCE_LABELS,
    SOURCE_MANUAL,
    SOURCE_ORDER,
    STATUS_APPLIED,
    STATUS_EXCLUDED,
    STATUS_IGNORED,
    STATUS_INTERVIEW,
    STATUS_NEW,
    STATUS_OPTIONS,
    STATUS_ORDER,
    STATUS_REJECTED,
    STOP_REASON_LABELS,
    STRUCTURE_TYPE_LABELS,
    STRUCTURE_TYPE_TONES,
    STRUCTURE_TYPES,
    SUB_SCORE_KEYS,
    SUB_SCORE_LABELS,
    SUB_SCORE_SHORT_LABELS,
    SUB_SCORE_WEIGHTS,
    TIER_1,
    TIER_ESN,
    TIER_LABELS,
    TIER_NEUTRAL,
    VALID_STATUSES,
    V4_AXIS_KEYS,
    V4_AXIS_LABELS,
    V4_CLASSES,
    V4_CLASS_LABELS,
    V4_CLASS_TONES,
    V4_FLAG_CONTRACT_TO_CHECK,
    V4_FLAG_EXCLUDED_PREFIX,
    V4_TAGS,
    V4_TAG_LABELS,
    V4_TAG_TONES,
    VERDICT_COLORS,
    VERDICT_EXCELLENT,
    VERDICT_GOOD,
    VERDICT_LABELS,
    VERDICT_MIXED,
    VERDICT_OFF_TOPIC,
    VERDICTS,
    coerce_sub_score,
    first_number,
    is_incomplete_stop,
    normalize_v4_tag,
    pass_label,
    run_label,
    seen_decision_label,
    source_label,
    source_rank,
    stop_reason_label,
)
from src.storage.database import Database
from src.config import load_config

__all__ = [
    "BENCHMARK_PENALTY",
    "CONTRACT_TYPE_LABELS",
    "CONTRACT_TYPES",
    "DEFAULT_SUB_SCORE",
    "FLAG_LABELS",
    "FLAG_TONES",
    "FLAGS",
    "HARD_CAP_LABELS",
    "HARD_CAP_RULES",
    "MAX_BONUS_TOTAL",
    "PASS_FRESHNESS",
    "PASS_LABELS",
    "PASS_RELEVANCE",
    "REFUSAL_REASON",
    "RUN_ERROR",
    "RUN_INTERRUPTED",
    "RUN_LABELS",
    "RUN_NOTE_ADHOC",
    "RUN_NOTE_DEGRADED",
    "RUN_OK",
    "RUN_PARTIAL",
    "RUN_RUNNING",
    "SEEN_DECISION_LABELS",
    "SEEN_DECISIONS",
    "SEEN_DUPLICATE",
    "SEEN_KNOWN",
    "SEEN_OUT_OF_WINDOW",
    "SEEN_REJECTED_BI",
    "SEEN_REJECTED_CONTRACT",
    "SEEN_REJECTED_TITLE",
    "SEEN_VALIDATED",
    "SIGNAL_BONUSES",
    "SOURCE_COLORS",
    "SOURCE_FALLBACK_COLOR",
    "SOURCE_GMAIL",
    "SOURCE_LABELS",
    "SOURCE_MANUAL",
    "SOURCE_ORDER",
    "STATUS_APPLIED",
    "STATUS_EXCLUDED",
    "STATUS_IGNORED",
    "STATUS_INTERVIEW",
    "STATUS_NEW",
    "STATUS_OPTIONS",
    "STATUS_ORDER",
    "STATUS_REJECTED",
    "STOP_REASON_LABELS",
    "STRUCTURE_TYPE_LABELS",
    "STRUCTURE_TYPE_TONES",
    "STRUCTURE_TYPES",
    "SUB_SCORE_KEYS",
    "SUB_SCORE_LABELS",
    "SUB_SCORE_SHORT_LABELS",
    "SUB_SCORE_WEIGHTS",
    "TIER_1",
    "TIER_ESN",
    "TIER_LABELS",
    "TIER_NEUTRAL",
    "VALID_STATUSES",
    "V4_AXIS_KEYS",
    "V4_AXIS_LABELS",
    "V4_CLASSES",
    "V4_CLASS_LABELS",
    "V4_CLASS_TONES",
    "V4_TAGS",
    "V4_TAG_LABELS",
    "V4_TAG_TONES",
    "VERDICT_COLORS",
    "VERDICT_EXCELLENT",
    "VERDICT_GOOD",
    "VERDICT_LABELS",
    "VERDICT_MIXED",
    "VERDICT_OFF_TOPIC",
    "VERDICTS",
    "coerce_sub_score",
    "first_number",
    "is_incomplete_stop",
    "pass_label",
    "run_label",
    "seen_decision_label",
    "source_label",
    "source_rank",
    "stop_reason_label",
    "ALIGNMENT_FALLBACK",
    "ALIGNMENT_LEVELS",
    "DATA_CACHE_TTL",
    "DISPLAY_FLAT",
    "DISPLAY_GROUPED",
    "EXCERPT_LENGTH",
    "Filters",
    "MAX_TECH_CHIPS",
    "PAGE_SIZE_ALL",
    "PAGE_SIZES",
    "PROCESSED_STATUSES",
    "QUALIFIED_SCORE",
    "STATUS_LABELS",
    "STATUS_TONES",
    "VERDICT_TONES",
    "_esc",
    "_set_status",
    "bump_data_version",
    "contract_label",
    "detected_technologies",
    "display_score",
    "effective_score",
    "exclusion_info",
    "has_v4_score",
    "is_contract_to_verify",
    "main_score",
    "SORT_MAIN_SCORE",
    "filter_jobs",
    "get_database",
    "group_jobs_by_source",
    "is_reranked",
    "job_age_days",
    "load_jobs",
    "matches_query",
    "parse_timestamp",
    "relative_date",
    "score_alignment",
    "search_terms",
    "source_color",
    "source_distribution",
    "tone_class",
]

# Vocabulaire d'affichage (micro-copie d'ingénierie, sans emoji décoratif)
# --------------------------------------------------------------------------- #
DISPLAY_FLAT = "Flux unique (tri par score)"
DISPLAY_GROUPED = "Groupé par plateforme"

PAGE_SIZE_ALL = "Tout"
PAGE_SIZES = ("25", "50", "100", PAGE_SIZE_ALL)

# Durée de vie (s) du cache de lecture SQLite : une écriture externe au dashboard
# (rerank lancé en ligne de commande, collecte manuelle) devient visible sans
# redémarrer le serveur Streamlit.
DATA_CACHE_TTL = 60

_config_ui = load_config().get("ui", {})
QUALIFIED_SCORE = float(_config_ui.get("qualified_score", 60.0))
MAX_TECH_CHIPS = int(_config_ui.get("max_tech_chips", 7))
EXCERPT_LENGTH = int(_config_ui.get("excerpt_length", 420))

STATUS_LABELS = {
    STATUS_NEW: "Nouveau",
    STATUS_APPLIED: "Postulé",
    STATUS_INTERVIEW: "Entretien",
    STATUS_IGNORED: "Archivé",
    STATUS_REJECTED: "Rejeté",
}

# Statuts considérés comme « traités » (masquables via la vue focus).
PROCESSED_STATUSES = (STATUS_APPLIED, STATUS_INTERVIEW, STATUS_IGNORED, STATUS_REJECTED)

STATUS_TONES = {
    STATUS_NEW: "mute",
    STATUS_APPLIED: "positive",
    STATUS_INTERVIEW: "accent",
    STATUS_IGNORED: "warn",
    STATUS_REJECTED: "alert",
}

VERDICT_TONES = {
    VERDICT_EXCELLENT: "positive",
    VERDICT_GOOD: "accent",
    VERDICT_MIXED: "warn",
    VERDICT_OFF_TOPIC: "alert",
}

# Seuils d'alignement (score effectif) → libellé + tonalité.
ALIGNMENT_LEVELS: tuple[tuple[float, str, str], ...] = (
    (80.0, "Cœur de cible", "positive"),
    (60.0, "Pertinent", "accent"),
    (40.0, "Secondaire", "warn"),
)
ALIGNMENT_FALLBACK: tuple[str, str] = ("Hors périmètre", "mute")

@st.cache_resource
def get_database() -> Database:
    """Connexion SQLite partagée (créée une seule fois par session serveur)."""
    config = load_config()
    db_path = config["database"]["path"]

    # Restauration automatique depuis le stockage distant (R2/S3) si configuré
    try:
        from src.storage.cloud_storage import download_database, is_cloud_storage_configured
        if is_cloud_storage_configured():
            download_database(target_path=db_path)
    except Exception:
        pass

    return Database(db_path)


@st.cache_data(ttl=DATA_CACHE_TTL, show_spinner=False)
def load_jobs(_db: Database, data_version: int) -> list[dict[str, Any]]:
    """Charge toutes les offres, triées par score effectif décroissant.

    ``data_version`` (session_state) invalide le cache dès qu'un statut change
    ou que le pipeline est relancé : aucune requête n'est refaite sans raison.
    Le ``ttl`` couvre le cas d'une écriture externe (rerank lancé en ligne de
    commande) : la vue se rafraîchit d'elle-même au plus tard après ``DATA_CACHE_TTL``.
    """
    return _db.get_jobs()


def bump_data_version(sync_cloud: bool = True) -> None:
    """Invalide les données mises en cache et programme éventuellement une synchronisation cloud."""
    st.session_state["data_version"] = st.session_state.get("data_version", 0) + 1
    if sync_cloud:
        try:
            from src.storage.cloud_storage import trigger_debounced_upload
            trigger_debounced_upload()
        except Exception:
            pass


def _set_status(db: Database, job_id: str, status: str) -> None:
    """Callback de bouton : persiste un statut de candidature puis invalide le cache."""
    db.update_status(job_id, status)
    bump_data_version()


# --------------------------------------------------------------------------- #
# Helpers métier (purs, donc directement testables)
# --------------------------------------------------------------------------- #
def effective_score(job: dict[str, Any]) -> float:
    """Score R&D effectif : rerank du juge LLM s'il existe, sinon score bi-encoder."""
    rerank = job.get("rerank_score")
    if rerank is not None:
        return float(rerank)
    return float(job.get("final_score") or 0.0)


def is_reranked(job: dict[str, Any]) -> bool:
    """Indique si l'offre a été évaluée par le juge LLM (étape 2)."""
    return job.get("rerank_score") is not None


# --------------------------------------------------------------------------- #
# Note principale : la note v4 (classe A-E + v4_display_score)
# --------------------------------------------------------------------------- #
# Valeurs de ``Filters.sort_by`` triant sur la note principale (v4). « v3 » et
# « v4 » sont gardés comme alias : l'ancien tri par défaut devient la note v4.
SORT_MAIN_SCORE = "score"
_MAIN_SORT_KEYS = frozenset({"", SORT_MAIN_SCORE, "v3", "v4"})
# Ancien ordre v3 (score de rerank) conservé pour comparaison explicite.
SORT_V3_LEGACY = "v3_legacy"


def _v4_flags(job: dict[str, Any]) -> list[str]:
    raw = job.get("v4_flags")
    if raw is None:
        raw = job.get("v4_flags_json")
    if isinstance(raw, str):
        try:
            import json

            raw = json.loads(raw)
        except Exception:
            raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(f) for f in raw if f]


def has_v4_score(job: dict[str, Any]) -> bool:
    """Vrai si l'offre porte une note v4 (même exclue)."""
    return job.get("v4_display_score") is not None


def exclusion_info(job: dict[str, Any]) -> tuple[bool, str]:
    """(exclue ?, motif). La v4 décide si elle a noté l'offre, sinon l'ancienne grille v3.

    Une offre remise à trier par Eddy (``REINSTATED_REASON``) n'est jamais exclue.
    """
    if str(job.get("rejection_reason") or "") == REINSTATED_REASON:
        return False, ""
    if has_v4_score(job):
        for flag in _v4_flags(job):
            if flag.startswith(V4_FLAG_EXCLUDED_PREFIX):
                return True, flag[len(V4_FLAG_EXCLUDED_PREFIX):].strip()
        return False, ""
    excluded = (
        job.get("status") == STATUS_EXCLUDED
        or job.get("verdict") == "EXCLU"
        or bool(job.get("exclusion_reason"))
    )
    return excluded, (str(job.get("exclusion_reason") or "").strip() if excluded else "")


def is_contract_to_verify(job: dict[str, Any]) -> bool:
    """Vrai si la v4 a vu un contrat ALTERNANCE / CDI_CDD sans citation vérifiable."""
    return V4_FLAG_CONTRACT_TO_CHECK in _v4_flags(job)


def display_score(job: dict[str, Any]) -> float | None:
    """Note affichée (v4_display_score) ; ``None`` si l'offre n'est pas notée v4 ou exclue."""
    if not has_v4_score(job) or exclusion_info(job)[0]:
        return None
    return float(job["v4_display_score"])


def main_score(job: dict[str, Any]) -> float:
    """Note utilisée par les filtres de seuil : v4 si présente, sinon repli sur l'ancien score."""
    if has_v4_score(job):
        return float(job.get("v4_display_score") or 0.0)
    return effective_score(job)


def _main_sort_key(job: dict[str, Any]) -> tuple[bool, float, float]:
    """Tri « Meilleur score » : notées v4 d'abord (par note affichée), puis non notées v4."""
    shown = display_score(job)
    return (shown is not None, shown if shown is not None else -1.0, effective_score(job))


def score_alignment(score: float) -> tuple[str, str]:
    """Traduit un score 0-100 en libellé d'alignement + tonalité fonctionnelle."""
    for threshold, label, tone in ALIGNMENT_LEVELS:
        if score >= threshold:
            return label, tone
    return ALIGNMENT_FALLBACK


def tone_class(tone: str) -> str:
    """Classe CSS portant les jetons de couleur d'une tonalité."""
    return f"sc-tone-{tone}"


def _esc(value: Any) -> str:
    """Échappe une valeur destinée à être injectée dans du HTML."""
    return "" if value is None else html.escape(str(value), quote=True)


def parse_timestamp(value: Any) -> datetime | None:
    """Convertit ``created_at`` (datetime ou chaîne ISO) en datetime naïf UTC."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def relative_date(value: Any, now: datetime | None = None) -> str:
    """Date relative compacte : « Aujourd'hui », « Il y a 5 h », « Il y a 3 j »…"""
    stamp = parse_timestamp(value)
    if stamp is None:
        return "Date inconnue"
    reference = now or datetime.now(timezone.utc).replace(tzinfo=None)
    delta = reference - stamp
    seconds = delta.total_seconds()
    if seconds < 0:
        return "À l'instant"
    if seconds < 3600:
        return "À l'instant" if seconds < 120 else f"Il y a {int(seconds // 60)} min"
    if seconds < 86400:
        return f"Il y a {int(seconds // 3600)} h"
    days = int(seconds // 86400)
    if days == 1:
        return "Hier"
    if days < 31:
        return f"Il y a {days} j"
    months = days // 30
    return f"Il y a {months} mois" if months < 12 else f"Il y a {months // 12} an(s)"


def job_age_days(job: dict[str, Any], now: datetime | None = None) -> int | None:
    """Nombre de jours écoulés depuis ``published_at`` ou ``created_at`` (``None`` si inconnu)."""
    stamp = parse_timestamp(job.get("published_at") or job.get("created_at"))
    if stamp is None:
        return None
    reference = now or datetime.now(timezone.utc).replace(tzinfo=None)
    delta = reference - stamp
    return max(0, int(delta.total_seconds() // 86400))


def detected_technologies(job: dict[str, Any], keywords: Sequence[str]) -> list[str]:
    """Technologies clés de l'offre.

    Le juge LLM remplit ``tech_stack`` ; en l'absence de verdict, on retombe sur
    la détection déterministe des mots-clés d'excellence de ``config.yaml``
    (aucune technologie n'est inventée).
    """
    declared = [str(item) for item in (job.get("tech_stack") or []) if str(item).strip()]
    if declared:
        return declared[:MAX_TECH_CHIPS]
    haystack = f"{job.get('title') or ''} {job.get('description') or ''}".lower()
    found = [keyword for keyword in keywords if keyword.lower() in haystack]
    return found[:MAX_TECH_CHIPS]


def contract_label(job: dict[str, Any]) -> str | None:
    """Type de contrat déduit de la fiche (``None`` si non identifiable)."""
    haystack = f"{job.get('title') or ''} {job.get('description') or ''}".lower()
    if "stage" in haystack or "internship" in haystack:
        return "Stage"
    if "alternance" in haystack or "apprentissage" in haystack:
        return "Alternance"
    return None


def search_terms(query: str) -> list[str]:
    """Découpe une recherche plein texte en termes significatifs (ET logique)."""
    return [term for term in query.lower().replace(",", " ").split() if term]


def _search_haystack(job: dict[str, Any]) -> str:
    """Concatène les champs indexés par la recherche plein texte."""
    parts: list[str] = [
        str(job.get("title") or ""),
        str(job.get("company") or ""),
        str(job.get("location") or ""),
        source_label(job.get("source")),
    ]
    parts.extend(str(item) for item in (job.get("tech_stack") or []))
    parts.append(str(job.get("description") or ""))
    return " ".join(parts).lower()


def matches_query(job: dict[str, Any], terms: Iterable[str]) -> bool:
    """Vrai si tous les termes recherchés apparaissent dans l'offre."""
    haystack = _search_haystack(job)
    return all(term in haystack for term in terms)


@dataclass(frozen=True)
class Filters:
    """Critères de sélection courants du flux d'offres."""

    query: str = ""
    sources: tuple[str, ...] = ()
    statuses: tuple[str, ...] = ()
    tiers: tuple[int, ...] = (TIER_1, TIER_NEUTRAL, TIER_ESN)
    min_score: float = 0.0
    llm_only: bool = False
    hide_processed: bool = False
    exclude_esn: bool = False
    exclude_dassault: bool = False
    exclude_companies: tuple[str, ...] = ()
    selected_companies: tuple[str, ...] = ()
    group_by_source: bool = False
    limit: int | None = None
    # v3 filters
    structure_types: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()
    hide_excluded: bool = True
    hide_defense_ethics: bool = True
    # v4 filters
    v4_classes: tuple[str, ...] = ()
    v4_tags: tuple[str, ...] = ()
    exclude_v4_tags: tuple[str, ...] = ()
    # « score » (défaut, alias « v3 »/« v4 ») = note v4 affichée ; « v4_class » ;
    # « v3_legacy » = ancien ordre v3. Les non notées v4 passent après.
    sort_by: str = SORT_MAIN_SCORE
    # Onglet « Exclues » : ne garder QUE les offres exclues (prime sur hide_excluded).
    only_excluded: bool = False

    def is_default(self) -> bool:
        """Vrai si aucun critère restrictif n'est appliqué.

        Un tuple vide (``statuses``, ``tiers``, ``sources``) signifie « tous les
        conserver » et ne constitue donc pas un filtre.
        """
        return not (
            self.query
            or self.sources
            or self.min_score
            or self.llm_only
            or self.exclude_esn
            or getattr(self, "exclude_dassault", False)
            or getattr(self, "exclude_companies", ())
            or getattr(self, "selected_companies", ())
            or getattr(self, "structure_types", ())
            or getattr(self, "flags", ())
            or getattr(self, "v4_classes", ())
            or getattr(self, "v4_tags", ())
            or getattr(self, "exclude_v4_tags", ())
            or str(getattr(self, "sort_by", SORT_MAIN_SCORE) or "") not in _MAIN_SORT_KEYS
            or getattr(self, "only_excluded", False)
            or not getattr(self, "hide_excluded", True)
            or not getattr(self, "hide_defense_ethics", True)
            or (self.statuses and len(self.statuses) != len(STATUS_ORDER))
            or (self.tiers and len(self.tiers) != len(TIER_LABELS))
            or self.limit is not None
        )


def filter_jobs(jobs: list[dict[str, Any]], filters: Filters) -> list[dict[str, Any]]:
    """Applique les filtres courants au flux d'offres (ordre conservé ou tri v4 si filtré par classe)."""
    terms = search_terms(filters.query)
    statuses = {STATUS_NEW} if filters.hide_processed else set(filters.statuses)
    tiers = set(filters.tiers)
    sources = set(filters.sources)
    exclude_dassault = getattr(filters, "exclude_dassault", False)
    exclude_comps_raw = getattr(filters, "exclude_companies", ())
    selected_comps_raw = getattr(filters, "selected_companies", ())
    excluded_comps = {c.strip().lower() for c in exclude_comps_raw if c and c.strip()}
    selected_comps = {c.strip().lower() for c in selected_comps_raw if c and c.strip()}
    
    # v3 filters
    hide_excluded = getattr(filters, "hide_excluded", True)
    hide_defense_ethics = getattr(filters, "hide_defense_ethics", True)
    structure_types = set(getattr(filters, "structure_types", ()))
    selected_flags = set(getattr(filters, "flags", ()))

    # v4 filters
    v4_classes = {str(c).upper() for c in getattr(filters, "v4_classes", ()) if c}
    v4_tags_filter = {normalize_v4_tag(t) for t in getattr(filters, "v4_tags", ()) if t}
    exclude_v4_tags_filter = {
        normalize_v4_tag(t) for t in getattr(filters, "exclude_v4_tags", ()) if t
    }
    sort_by = str(getattr(filters, "sort_by", SORT_MAIN_SCORE) or SORT_MAIN_SCORE)
    only_excluded = bool(getattr(filters, "only_excluded", False))

    # Pré-calcul pour les vérifications de sous-chaînes (>= 3 chars)
    excluded_comps_sub = [c for c in excluded_comps if len(c) >= 3]
    selected_comps_sub = [c for c in selected_comps if len(c) >= 3]
    
    selected: list[dict[str, Any]] = []
    for job in jobs:
        # Exclusions : la v4 décide si elle a noté l'offre, sinon l'ancienne grille v3.
        is_exclu = exclusion_info(job)[0]
        if only_excluded:
            if not is_exclu:
                continue
        elif hide_excluded and is_exclu:
            continue

        # V3 : Filtre « Masquer DÉFENSE et ETHIQUE_A_EXAMINER »
        if hide_defense_ethics:
            job_flags = [str(f).upper() for f in (job.get("flags") or [])]
            cap = str(job.get("cap_applied") or job.get("hard_cap_triggered") or "").upper()
            if (
                "DEFENSE" in job_flags
                or "DEFENSE_INDIRECTE" in job_flags
                or "ETHIQUE_A_EXAMINER" in job_flags
                or cap == "DEFENSE"
            ):
                continue

        # V3 : Filtre structure_type
        if structure_types:
            st_val = str(job.get("structure_type") or "INCONNU").upper()
            if st_val not in structure_types:
                continue

        # V3 : Filtre flags
        if selected_flags:
            job_flags = {str(f).upper() for f in (job.get("flags") or [])}
            if not job_flags.intersection(selected_flags):
                continue

        # V4 : Filtre par classe (A..E)
        if v4_classes:
            jc = str(job.get("v4_class") or "").upper()
            if jc not in v4_classes:
                continue

        # V4 : Filtre par étiquettes v4 (inclusion / exclusion)
        if v4_tags_filter or exclude_v4_tags_filter:
            j_tags = {normalize_v4_tag(t) for t in (job.get("v4_tags") or [])}
            if v4_tags_filter and not j_tags.intersection(v4_tags_filter):
                continue
            if exclude_v4_tags_filter and j_tags.intersection(exclude_v4_tags_filter):
                continue

        # Les offres écartées par la re-validation métier ne polluent pas le flux :
        # elles n'apparaissent que si l'utilisateur coche explicitement « Rejeté ».
        if job.get("status") == STATUS_REJECTED and STATUS_REJECTED not in statuses:
            continue
        company = (job.get("company") or "").strip()
        comp_lower = company.lower()
        if exclude_dassault and "dassault" in comp_lower:
            continue
        if excluded_comps and (comp_lower in excluded_comps or any(ec in comp_lower for ec in excluded_comps_sub)):
            continue
        if selected_comps and not (comp_lower in selected_comps or any(sc in comp_lower for sc in selected_comps_sub)):
            continue
        if filters.min_score and main_score(job) < filters.min_score:
            continue
        if statuses and job.get("status") not in statuses:
            continue
        if sources and job.get("source") not in sources:
            continue
        if tiers and job.get("company_tier") not in tiers:
            continue
        if filters.exclude_esn and job.get("company_tier") == TIER_ESN:
            continue
        if filters.llm_only and not is_reranked(job):
            continue
        if terms and not matches_query(job, terms):
            continue
        selected.append(job)

    # Spécification v4 section 8 : quand Eddy filtre sur une classe (ex: B), trier par v4_class_score
    if len(v4_classes) == 1 or sort_by == "v4_class":
        selected.sort(
            key=lambda j: (
                j.get("v4_class_score") is not None,
                float(j.get("v4_class_score") or 0.0),
                effective_score(j),
            ),
            reverse=True,
        )
    elif sort_by in _MAIN_SORT_KEYS:
        selected.sort(key=_main_sort_key, reverse=True)

    return selected[: filters.limit] if filters.limit else selected


def source_color(source: str | None) -> str:
    """Couleur identitaire de la plateforme d'origine (repli gris ardoise)."""
    return SOURCE_COLORS.get(source or "", SOURCE_FALLBACK_COLOR)

def source_distribution(jobs: list[dict[str, Any]]) -> list[tuple[str, int, str]]:
    """Répartition par plateforme : ``(libellé, nombre, couleur)``.

    Les alias ``wttj`` / ``welcome_to_the_jungle`` sont fusionnés et les
    plateformes les plus représentées passent en premier.
    """
    counts: dict[str, tuple[int, str]] = {}
    for job in jobs:
        label = source_label(job.get("source"))
        count, color = counts.get(label, (0, source_color(job.get("source"))))
        counts[label] = (count + 1, color)
    ordered = sorted(counts.items(), key=lambda item: (-item[1][0], source_rank(item[0])))
    return [(label, stats[0], stats[1]) for label, stats in ordered]


def group_jobs_by_source(jobs: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    """Regroupe les offres par plateforme en conservant le tri par score.

    Les alias ``wttj`` / ``welcome_to_the_jungle`` sont fusionnés sous le même
    libellé et les groupes suivent ``SOURCE_ORDER`` (inconnues en dernier).
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for job in jobs:
        groups.setdefault(source_label(job.get("source")), []).append(job)
    return sorted(groups.items(), key=lambda item: source_rank(item[0]))


# --------------------------------------------------------------------------- #
