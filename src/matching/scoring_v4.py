"""Notation v4 « étoiles + fourchette + classe » : schéma Pydantic, calcul déterministe et juge LLM v4.

Aucun écrasement des colonnes v3 : la v4 écrit dans ses propres colonnes (v4_*)
et conserve verify_citation pour les étoiles >= 4.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from google import genai
from google.genai import types
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.candidate import LOCAL_CANDIDATE_PATH
from src.config import PROJECT_ROOT, load_config
from src.constants import (
    AUTO_IGNORE_REASONS,
    CONTRACT_TYPES,
    REFUSAL_REASON,
    STATUS_APPLIED,
    STATUS_IGNORED,
    STATUS_INTERVIEW,
    STATUS_REJECTED,
    V4_AXIS_KEYS,
    V4_AXIS_WEIGHTS,
    V4_CLASS_OFFSETS,
    V4_CLASSES,
    V4_FLAG_CONTRACT_TO_CHECK,
    V4_FLAG_EXCLUDED_PREFIX,
    V4_TAGS,
    ignore_reason_label,
    normalize_v4_tag,
)
from src.eligibility import experience_reason, not_internship_reason
from src.env import load_env_file
from src.matching.llm_providers import (
    GeminiRateLimiter,
    OpenAICompatibleProvider,
    get_global_rate_limiter,
)
from src.matching.scoring_v3 import is_title_excluded_contract, verify_citation

logger = logging.getLogger("src.matching.scoring_v4")

DEFAULT_V4_GOOGLE_MODEL = "gemini-3.5-flash-lite"
DEFAULT_V4_GROQ_MODEL = "llama-3.3-70b-versatile"
DEFAULT_V4_DEEPSEEK_MODEL = "deepseek-chat"

PROVIDER_URLS = {
    "groq": "https://api.groq.com/openai/v1",
    "deepseek": "https://api.deepseek.com",
    "mistral": "https://api.mistral.ai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


# --------------------------------------------------------------------------- #
# 1. Schéma Pydantic de la sortie structurée du juge v4 (Section 8)
# --------------------------------------------------------------------------- #
class V4AxisEvaluation(BaseModel):
    """Évaluation d'un axe de 0 à 5 étoiles avec justification et citation."""

    model_config = ConfigDict(extra="ignore")

    stars: int = Field(ge=0, le=5)
    reason: str = Field(default="")
    quote: str = Field(default="")
    unknown: bool = Field(default=False)


class V4Axes(BaseModel):
    """Les 4 axes obligatoires de la grille v4."""

    model_config = ConfigDict(extra="ignore")

    apprendre: V4AxisEvaluation
    technique: V4AxisEvaluation
    debouche: V4AxisEvaluation
    logistique: V4AxisEvaluation

    def as_dict(self) -> dict[str, dict[str, Any]]:
        return {
            "apprendre": self.apprendre.model_dump(),
            "technique": self.technique.model_dump(),
            "debouche": self.debouche.model_dump(),
            "logistique": self.logistique.model_dump(),
        }


class V4JudgeOutput(BaseModel):
    """Sortie structurée complète attendue du juge LLM v4."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    contract_type: str = Field(default="STAGE")
    # Citation exacte de l'offre justifiant contract_type (absente des anciennes notes).
    contract_quote: str = Field(default="")
    duration_months: int | None = Field(default=None)
    is_cesure: bool = Field(default=False)
    offer_class: str = Field(alias="class")
    class_reason: str = Field(default="")
    class_doubt: bool = Field(default=False)
    axes: V4Axes
    reputation_points: int = Field(ge=-10, le=10)
    reputation_reason: str = Field(default="")
    offer_quality_points: int = Field(ge=-10, le=10)
    offer_quality_reason: str = Field(default="")
    adjustment: int = Field(default=0)
    tags: list[str] = Field(default_factory=list)
    summary: str = Field(default="")

    def model_post_init(self, __context: Any) -> None:
        cls_norm = str(self.offer_class or "").strip().upper()
        if cls_norm not in V4_CLASSES:
            raise ValueError(f"Classe v4 invalide : {self.offer_class!r} (attendu A..E)")
        self.offer_class = cls_norm
        ct_norm = str(self.contract_type or "STAGE").strip().upper()
        if ct_norm not in CONTRACT_TYPES:
            ct_norm = "STAGE"
        self.contract_type = ct_norm


def get_response_schema_v4() -> dict[str, Any]:
    """Retourne le JSON Schema compatible Google GenAI / OpenAI pour la sortie v4."""
    axis_schema = {
        "type": "OBJECT",
        "properties": {
            "stars": {"type": "INTEGER"},
            "reason": {"type": "STRING"},
            "quote": {"type": "STRING"},
            "unknown": {"type": "BOOLEAN"},
        },
        "required": ["stars", "reason", "quote", "unknown"],
    }
    return {
        "type": "OBJECT",
        "properties": {
            "contract_type": {
                "type": "STRING",
                "enum": list(CONTRACT_TYPES),
            },
            "contract_quote": {"type": "STRING"},
            "duration_months": {"type": "INTEGER", "nullable": True},
            "is_cesure": {"type": "BOOLEAN"},
            "class": {
                "type": "STRING",
                "enum": list(V4_CLASSES),
            },
            "class_reason": {"type": "STRING"},
            "class_doubt": {"type": "BOOLEAN"},
            "axes": {
                "type": "OBJECT",
                "properties": {
                    "apprendre": axis_schema,
                    "technique": axis_schema,
                    "debouche": axis_schema,
                    "logistique": axis_schema,
                },
                "required": ["apprendre", "technique", "debouche", "logistique"],
            },
            "reputation_points": {"type": "INTEGER"},
            "reputation_reason": {"type": "STRING"},
            "offer_quality_points": {"type": "INTEGER"},
            "offer_quality_reason": {"type": "STRING"},
            "adjustment": {"type": "INTEGER"},
            "tags": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
            },
            "summary": {"type": "STRING"},
        },
        "required": [
            "contract_type",
            "contract_quote",
            "class",
            "class_reason",
            "class_doubt",
            "axes",
            "reputation_points",
            "reputation_reason",
            "offer_quality_points",
            "offer_quality_reason",
            "adjustment",
            "tags",
            "summary",
        ],
    }


def _load_candidate_logistics_context() -> str:
    """Lit les préférences logistiques éventuelles depuis data/candidate.local.yaml."""
    if not LOCAL_CANDIDATE_PATH.exists():
        return ""
    try:
        import yaml

        raw = yaml.safe_load(LOCAL_CANDIDATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return ""
        parts: list[str] = []
        if raw.get("location"):
            parts.append(f"Ville principale : {raw['location']}")
        if raw.get("accepted_cities"):
            cities = raw["accepted_cities"]
            if isinstance(cities, list):
                parts.append(f"Villes acceptées : {', '.join(str(c) for c in cities)}")
            else:
                parts.append(f"Villes acceptées : {cities}")
        if raw.get("start_window") or raw.get("dates"):
            parts.append(f"Fenêtre de dates : {raw.get('start_window') or raw.get('dates')}")
        return " | ".join(parts)
    except Exception:
        return ""


def get_system_prompt_v4(feedback_block: str | None = None) -> str:
    """Charge data/prompt_v4.txt et y injecte les paramètres logistiques locaux s'ils existent.

    ``feedback_block`` (voir ``build_feedback_block``) est ajouté en fin de prompt :
    les décisions récentes d'Eddy servent de calibration, pas de règles.
    """
    prompt_path = PROJECT_ROOT / "data" / "prompt_v4.txt"
    base = (
        prompt_path.read_text(encoding="utf-8")
        if prompt_path.exists()
        else "Tu évalues une offre de stage pour Eddy. Tu relèves des faits, tu ne fais pas de compliments."
    )
    logistics_ctx = _load_candidate_logistics_context()
    if logistics_ctx:
        base += f"\n\nPRÉCISIONS LOGISTIQUES DU PROFIL LOCAL : {logistics_ctx}\n"
    if feedback_block and feedback_block.strip():
        base += f"\n\n{feedback_block.strip()}\n"
    return base


# --------------------------------------------------------------------------- #
# 1 bis. Boucle de retour : décisions d'Eddy injectées dans le prompt (N3)
# --------------------------------------------------------------------------- #
FEEDBACK_HEADER = (
    "DÉCISIONS RÉCENTES D'EDDY (préférences de calibration, PAS des règles absolues : "
    "la grille ci-dessus reste prioritaire ; sers-t'en pour trancher les cas limites, "
    "ne recopie jamais ces notes)"
)
DEFAULT_FEEDBACK_MAX_EXAMPLES = 12
DEFAULT_FEEDBACK_MAX_CHARS = 1500


def is_applied_decision(job: dict[str, Any]) -> bool:
    """Vrai si Eddy a postulé à l'offre (POSTULÉ, ENTRETIEN, ou REJETÉ après candidature)."""
    status = job.get("status")
    if status in (STATUS_APPLIED, STATUS_INTERVIEW):
        return True
    if status == STATUS_REJECTED:
        return bool(job.get("applied_at")) or job.get("rejection_reason") == REFUSAL_REASON
    return False


def is_manual_ignore(job: dict[str, Any]) -> bool:
    """Vrai si l'offre a été ignorée à la main (hors archivage automatique et offre fermée)."""
    if job.get("status") != STATUS_IGNORED:
        return False
    if str(job.get("rejection_reason") or "") in AUTO_IGNORE_REASONS:
        return False
    return "[auto] archivée" not in str(job.get("user_notes") or "").casefold()


def _short(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def _recency_key(job: dict[str, Any]) -> str:
    stamp = job.get("applied_at") or job.get("v4_graded_at") or job.get("created_at") or ""
    return str(stamp)


def _feedback_line(job: dict[str, Any], applied: bool) -> str:
    title = _short(job.get("title"), 70) or "Offre sans titre"
    company = _short(job.get("company"), 40) or "entreprise inconnue"
    cls = str(job.get("v4_class") or "").strip().upper()
    score = job.get("v4_display_score")
    if cls and score is not None:
        note = f"classe v4 {cls}, {int(round(float(score)))}/100"
    elif cls:
        note = f"classe v4 {cls}"
    else:
        note = "non notée v4"
    if applied:
        decision = "POSTULÉ"
    else:
        reason = ignore_reason_label(job.get("rejection_reason")) or "sans raison"
        decision = f"IGNORÉ ({reason})"
        comment = _short(job.get("rejection_comment"), 100)
        if comment:
            decision += f" « {comment} »"
    return f"- {decision} : {title} — {company} [{note}]"


def build_feedback_block(
    jobs: list[dict[str, Any]],
    max_examples: int = DEFAULT_FEEDBACK_MAX_EXAMPLES,
    max_chars: int = DEFAULT_FEEDBACK_MAX_CHARS,
) -> str:
    """Construit le bloc « DÉCISIONS RÉCENTES D'EDDY » (déterministe, équilibré, borné).

    - Positifs : offres postulées (``is_applied_decision``) ; négatifs : ignorées à la
      main (``is_manual_ignore``), en priorité celles qui portent une raison.
    - Moitié / moitié (le reste est complété par l'autre camp s'il manque d'exemples),
      les plus récentes d'abord, tri stable par identifiant à date égale.
    - Les lignes sont alternées puis retirées par la fin jusqu'à tenir dans ``max_chars``.
    Retourne ``""`` s'il n'y a aucun exemple.
    """
    max_examples = max(0, int(max_examples))
    if max_examples == 0:
        return ""
    positives = sorted(
        (j for j in jobs if is_applied_decision(j)),
        key=lambda j: (_recency_key(j), str(j.get("id") or "")),
        reverse=True,
    )
    negatives = sorted(
        (j for j in jobs if is_manual_ignore(j)),
        key=lambda j: (
            bool(j.get("rejection_reason")),
            _recency_key(j),
            str(j.get("id") or ""),
        ),
        reverse=True,
    )
    half = max_examples // 2
    n_pos = min(len(positives), max(half, max_examples - len(negatives)))
    n_neg = min(len(negatives), max_examples - n_pos)
    picked_pos = positives[:n_pos]
    picked_neg = negatives[:n_neg]

    lines: list[str] = []
    for i in range(max(len(picked_pos), len(picked_neg))):
        if i < len(picked_pos):
            lines.append(_feedback_line(picked_pos[i], applied=True))
        if i < len(picked_neg):
            lines.append(_feedback_line(picked_neg[i], applied=False))
    if not lines:
        return ""

    def _render(selected: list[str]) -> str:
        return FEEDBACK_HEADER + " :\n" + "\n".join(selected)

    while lines and len(_render(lines)) > max_chars:
        lines.pop()
    return _render(lines) if lines else ""


# --------------------------------------------------------------------------- #
# 1 ter. Corrections de note par Eddy (N6), prioritaires sur les décisions
# --------------------------------------------------------------------------- #
USER_GRADES_HEADER = (
    "CORRECTIONS D'EDDY (notes qu'il aurait mises lui-même ; PRIORITAIRES sur les décisions "
    "ci-dessous). Pour une offre similaire (même entreprise, même type de mission), oriente "
    "classe, étoiles et ajustement dans le sens de sa correction et de son « car » ; "
    "ne les recopie pas aveuglément sur une offre différente, la grille reste la base"
)
DEFAULT_USER_GRADES_MAX_ITEMS = 10
DEFAULT_USER_GRADES_MAX_CHARS = 1500


def _grade_text(cls: Any, score: Any) -> str:
    """« B 70 », « B », « 70 » ou « non notée »."""
    letter = str(cls or "").strip().upper()
    number = "" if score is None or score == "" else str(int(round(float(score))))
    return " ".join(part for part in (letter, number) if part) or "non notée"


def has_user_grade(job: dict[str, Any]) -> bool:
    """Vrai si Eddy a laissé une correction (classe, note ou commentaire)."""
    return bool(
        str(job.get("user_v4_class") or "").strip()
        or job.get("user_v4_score") is not None
        or str(job.get("user_v4_comment") or "").strip()
    )


_LEADING_BECAUSE_RE = re.compile(r"^\s*(?:car\b|parce\s+que\b|parce\s+qu')\s*[:,]?\s*", re.IGNORECASE)


def user_grade_reason(comment: Any) -> str:
    """Le « car … » d'Eddy sur une ligne, sans le « car » initial s'il l'a tapé."""
    return _LEADING_BECAUSE_RE.sub("", re.sub(r"\s+", " ", str(comment or "")).strip())


def _user_grade_line(job: dict[str, Any]) -> str:
    title = _short(job.get("title"), 70) or "Offre sans titre"
    company = _short(job.get("company"), 40) or "entreprise inconnue"
    ai = _grade_text(job.get("v4_class"), job.get("v4_display_score"))
    eddy = _grade_text(job.get("user_v4_class"), job.get("user_v4_score"))
    if eddy == "non notée":
        eddy = "pas de note précise"
    line = f"- {company} — {title} : IA {ai} → Eddy {eddy}"
    comment = _short(user_grade_reason(job.get("user_v4_comment")), 160)
    if comment:
        line += f", car « {comment} »"
    return line


def build_user_grades_block(
    grades: list[dict[str, Any]],
    max_items: int = DEFAULT_USER_GRADES_MAX_ITEMS,
    max_chars: int = DEFAULT_USER_GRADES_MAX_CHARS,
) -> str:
    """Construit le bloc « CORRECTIONS D'EDDY » (déterministe, borné).

    Les corrections les plus récentes d'abord (``user_v4_at``, puis identifiant),
    au plus ``max_items`` lignes, retirées par la fin jusqu'à tenir dans
    ``max_chars``. Chaque ligne : entreprise, titre, note de l'IA, note d'Eddy et
    son « car … ». Retourne ``""`` sans correction.
    """
    max_items = max(0, int(max_items))
    if max_items == 0:
        return ""
    picked = sorted(
        (g for g in grades if has_user_grade(g)),
        key=lambda g: (str(g.get("user_v4_at") or ""), str(g.get("id") or "")),
        reverse=True,
    )[:max_items]
    lines = [_user_grade_line(g) for g in picked]

    def _render(selected: list[str]) -> str:
        return USER_GRADES_HEADER + " :\n" + "\n".join(selected)

    while lines and len(_render(lines)) > max_chars:
        lines.pop()
    return _render(lines) if lines else ""


def user_grades_settings(config: dict[str, Any] | None) -> dict[str, Any]:
    """Paramètres des corrections d'Eddy (``scoring_v4.user_grades``), avec valeurs par défaut."""
    ug = ((config or {}).get("scoring_v4") or {}).get("user_grades") or {}
    return {
        "enabled": bool(ug.get("enabled", True)),
        "max_items": int(ug.get("max_items", DEFAULT_USER_GRADES_MAX_ITEMS)),
        "max_chars": int(ug.get("max_chars", DEFAULT_USER_GRADES_MAX_CHARS)),
    }


def build_calibration_block(db: Any, config: dict[str, Any] | None) -> str:
    """Bloc complet injecté dans le prompt : corrections d'Eddy, puis décisions récentes.

    Chaque partie est désactivable en config (``scoring_v4.user_grades.enabled``,
    ``scoring_v4.feedback.enabled``) et ne fait jamais échouer la notation.
    """
    parts: list[str] = []
    grades_cfg = user_grades_settings(config)
    if grades_cfg["enabled"] and hasattr(db, "get_user_grades"):
        try:
            parts.append(
                build_user_grades_block(
                    db.get_user_grades(),
                    max_items=grades_cfg["max_items"],
                    max_chars=grades_cfg["max_chars"],
                )
            )
        except Exception as exc:
            logger.warning("Corrections d'Eddy indisponibles : %s", exc)
    fb_cfg = feedback_settings(config)
    if fb_cfg["enabled"]:
        try:
            parts.append(
                build_feedback_block(
                    db.get_feedback_jobs(),
                    max_examples=fb_cfg["max_examples"],
                    max_chars=fb_cfg["max_chars"],
                )
            )
        except Exception as exc:
            logger.warning("Bloc de décisions d'Eddy indisponible : %s", exc)
    return "\n\n".join(part for part in parts if part)


def feedback_settings(config: dict[str, Any] | None) -> dict[str, Any]:
    """Paramètres de la boucle de retour (``scoring_v4.feedback``), avec valeurs par défaut."""
    fb = ((config or {}).get("scoring_v4") or {}).get("feedback") or {}
    return {
        "enabled": bool(fb.get("enabled", True)),
        "max_examples": int(fb.get("max_examples", DEFAULT_FEEDBACK_MAX_EXAMPLES)),
        "max_chars": int(fb.get("max_chars", DEFAULT_FEEDBACK_MAX_CHARS)),
    }


# --------------------------------------------------------------------------- #
# 2. Calcul déterministe de la note v4 (Sections 3, 4, 5, 6, 7)
# --------------------------------------------------------------------------- #
@dataclass
class V4ScoreBreakdown:
    """Résultat détaillé et reproductible du calcul de score v4."""

    final_class: str
    raw_class: str
    class_reason: str
    class_doubt: bool
    axes: dict[str, dict[str, Any]]
    effective_stars: dict[str, int]
    center: float
    band_low: float
    band_high: float
    reputation_points: int
    offer_quality_points: int
    adjustment: int
    class_score: float
    display_score: float
    tags: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    summary: str = ""
    excluded: bool = False
    exclusion_reason: str | None = None


def compute_final_score_v4(
    parsed: V4JudgeOutput | dict[str, Any],
    job: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> V4ScoreBreakdown:
    """Calcule la note v4 pure et déterministe à partir de la sortie structurée du LLM."""
    cfg = config or load_config()
    v4_cfg = cfg.get("scoring_v4") or {}
    v3_cfg = cfg.get("scoring_v3") or {}

    if isinstance(parsed, dict):
        judge_out = V4JudgeOutput.model_validate(parsed)
    else:
        judge_out = parsed

    min_duration = int(v4_cfg.get("min_duration_months", v3_cfg.get("min_duration_months", 4)))
    weights_cfg = v4_cfg.get("axis_weights") or V4_AXIS_WEIGHTS
    weights: dict[str, float] = {
        k: float(weights_cfg.get(k, V4_AXIS_WEIGHTS[k])) for k in V4_AXIS_KEYS
    }
    band_half_width = float(v4_cfg.get("band_half_width", 10.0))
    offsets_cfg = v4_cfg.get("class_offsets") or V4_CLASS_OFFSETS
    class_offsets: dict[str, float] = {
        c: float(offsets_cfg.get(c, V4_CLASS_OFFSETS[c])) for c in V4_CLASSES
    }
    weak_cfg = v4_cfg.get("weak_axis") or {}
    weak_enabled = bool(weak_cfg.get("enabled", True))
    apprendre_star_threshold = int(weak_cfg.get("apprendre_max_stars_cap", 1))
    apprendre_score_cap = float(weak_cfg.get("apprendre_class_score_cap", 55.0))
    tech_zero_class = str(weak_cfg.get("technique_zero_force_class", "E")).upper()

    bounds = v4_cfg.get("adjust_bounds") or [-10, 10]
    adj_min, adj_max = int(bounds[0]), int(bounds[1])
    high_star_threshold = int(v4_cfg.get("high_star_needs_quote", 4))

    doc_text = f"{job.get('title', '')}\n{job.get('description', '')}"
    axes_dict = judge_out.axes.as_dict()

    # 1. Filtres durs d'exclusion (Section 1 : conservés tels quels)
    exclusion_kws = v3_cfg.get("exclusion_contract_keywords", [])
    title_excluded, title_reason = is_title_excluded_contract(job.get("title", ""), exclusion_kws)
    exclusion_reason: str | None = None

    contract_quote = str(judge_out.contract_quote or "").strip()
    contract_incompatible = judge_out.contract_type in ("ALTERNANCE", "CDI_CDD")
    contract_proven = contract_incompatible and verify_citation(contract_quote, doc_text)
    # Contrat incompatible annoncé sans citation vérifiable : pas d'exclusion,
    # l'offre est notée normalement et signalée « contrat à vérifier ».
    contract_to_check = contract_incompatible and not contract_proven

    if title_excluded:
        exclusion_reason = title_reason
    elif contract_proven:
        exclusion_reason = f"Type de contrat incompatible ({judge_out.contract_type})"
    else:
        elig_reason = (
            not_internship_reason(job.get("title"), job.get("description"))
            or experience_reason(job.get("title"), job.get("description"))
        )
        if elig_reason:
            exclusion_reason = elig_reason
        elif judge_out.is_cesure:
            exclusion_reason = "Stage de césure / court hors PFE"
        elif judge_out.duration_months is not None and int(judge_out.duration_months) < min_duration:
            exclusion_reason = (
                f"Durée inférieure au seuil minimal ({judge_out.duration_months} mois < {min_duration} mois)"
            )

    if exclusion_reason:
        return V4ScoreBreakdown(
            final_class="E",
            raw_class=judge_out.offer_class,
            class_reason=judge_out.class_reason,
            class_doubt=judge_out.class_doubt,
            axes=axes_dict,
            effective_stars={k: 0 for k in V4_AXIS_KEYS},
            center=0.0,
            band_low=0.0,
            band_high=0.0,
            reputation_points=judge_out.reputation_points,
            offer_quality_points=judge_out.offer_quality_points,
            adjustment=0,
            class_score=0.0,
            display_score=0.0,
            tags=[],
            flags=[f"{V4_FLAG_EXCLUDED_PREFIX} {exclusion_reason}"],
            summary=judge_out.summary,
            excluded=True,
            exclusion_reason=exclusion_reason,
        )

    # 2. Vérification des étoiles hautes >= 4 (Section 3)
    effective_stars: dict[str, int] = {}
    flags: list[str] = [V4_FLAG_CONTRACT_TO_CHECK] if contract_to_check else []

    for axis in V4_AXIS_KEYS:
        ax_data = axes_dict[axis]
        raw_stars = max(0, min(5, int(ax_data.get("stars", 0))))
        quote = str(ax_data.get("quote") or "").strip()
        # L'ancienne citation factice « reputation » (prompt d'avant N4) ne prouve rien :
        # elle compte comme une citation absente ; la réputation passe par reputation_points.
        if quote.casefold() in ("reputation", "réputation"):
            quote = ""

        if raw_stars >= high_star_threshold:
            if verify_citation(quote, doc_text):
                effective_stars[axis] = raw_stars
            else:
                effective_stars[axis] = max(0, raw_stars - 1)
                flags.append(f"[CITATION_NON_VERIFIEE:{axis}]")
        else:
            effective_stars[axis] = raw_stars

        ax_data["effective_stars"] = effective_stars[axis]

    # 3. Calcul du centre et de la fourchette (Section 4)
    max_pondere = 5.0 * sum(weights.values())
    weighted_sum = sum(weights[k] * effective_stars[k] for k in V4_AXIS_KEYS)
    center = round(100.0 * weighted_sum / max_pondere, 4) if max_pondere > 0 else 0.0
    band_low = max(0.0, round(center - band_half_width, 4))
    band_high = min(100.0, round(center + band_half_width, 4))

    # 4. Ajustement recalculé par le code (Section 6)
    recalculated_adj = int(judge_out.reputation_points) + int(judge_out.offer_quality_points)
    adjustment = max(adj_min, min(adj_max, recalculated_adj))

    # 5. Note de classe et règle de l'axe faible (Sections 4 & 5)
    final_class = judge_out.offer_class
    class_score = max(0.0, min(100.0, round(center + adjustment, 4)))

    if weak_enabled:
        if effective_stars["technique"] == 0:
            final_class = tech_zero_class
        if effective_stars["apprendre"] <= apprendre_star_threshold:
            class_score = min(class_score, apprendre_score_cap)
            if "ENCADREMENT_FAIBLE" not in flags:
                flags.append("ENCADREMENT_FAIBLE")

    # 6. Note affichée avec barème caché de classe (Section 4)
    offset = class_offsets.get(final_class, 0.0)
    display_score = max(0.0, min(100.0, round(class_score + offset, 4)))

    # 7. Étiquettes normalisées (Section 7)
    valid_tags: list[str] = []
    for t in judge_out.tags:
        t_clean = normalize_v4_tag(t)
        if t_clean in V4_TAGS and t_clean not in valid_tags:
            valid_tags.append(t_clean)
    if judge_out.class_doubt and "class_doubt" not in valid_tags:
        valid_tags.append("class_doubt")

    return V4ScoreBreakdown(
        final_class=final_class,
        raw_class=judge_out.offer_class,
        class_reason=judge_out.class_reason,
        class_doubt=judge_out.class_doubt,
        axes=axes_dict,
        effective_stars=effective_stars,
        center=center,
        band_low=band_low,
        band_high=band_high,
        reputation_points=judge_out.reputation_points,
        offer_quality_points=judge_out.offer_quality_points,
        adjustment=adjustment,
        class_score=class_score,
        display_score=display_score,
        tags=valid_tags,
        flags=flags,
        summary=judge_out.summary,
        excluded=False,
        exclusion_reason=None,
    )


# --------------------------------------------------------------------------- #
# 3. Juge LLM v4 : Gemini Flash-Lite -> secours Groq -> secours DeepSeek
# --------------------------------------------------------------------------- #
def v4_result_from_output(
    validated: V4JudgeOutput,
    raw_dict: dict[str, Any],
    job: dict[str, Any],
    config: dict[str, Any] | None,
    model_label: str,
) -> dict[str, Any]:
    """Transforme une sortie LLM validée en colonnes v4 prêtes pour ``Database.update_v4``.

    Partagé par le juge en ligne et l'ingestion des lots notés hors ligne (Antigravity).
    """
    breakdown = compute_final_score_v4(validated, job, config)
    stars_payload = {
        k: {
            "stars": breakdown.effective_stars[k],
            "raw_stars": breakdown.axes[k]["stars"],
            "reason": breakdown.axes[k]["reason"],
            "quote": breakdown.axes[k]["quote"],
            "unknown": breakdown.axes[k]["unknown"],
        }
        for k in V4_AXIS_KEYS
    }
    raw_stored = dict(raw_dict)
    raw_stored["class"] = validated.offer_class
    return {
        "v4_status": "EXCLU" if breakdown.excluded else "OK",
        "v4_class": breakdown.final_class,
        "v4_stars_json": json.dumps(stars_payload, ensure_ascii=False),
        "v4_center": breakdown.center,
        "v4_adjustment": breakdown.adjustment,
        "v4_class_score": breakdown.class_score,
        "v4_display_score": breakdown.display_score,
        "v4_tags_json": json.dumps(breakdown.tags, ensure_ascii=False),
        "v4_flags_json": json.dumps(breakdown.flags, ensure_ascii=False),
        "v4_raw_json": json.dumps(raw_stored, ensure_ascii=False),
        "v4_model": model_label,
        "v4_graded_at": datetime.utcnow(),
        "excluded": breakdown.excluded,
        "exclusion_reason": breakdown.exclusion_reason,
        "contract_to_verify": V4_FLAG_CONTRACT_TO_CHECK in breakdown.flags,
        "summary": breakdown.summary,
        "api_error": False,
    }


def _resolve_provider_key(provider: str) -> str:
    load_env_file()
    prov = provider.strip().casefold()
    env_var = "GEMINI_API_KEY" if prov == "google" else f"{prov.upper()}_API_KEY"
    return os.environ.get(env_var, "").strip()


class LLMJudgeV4:
    """Juge LLM v4 avec validation Pydantic, 1 nouvel essai par provider et cascade Gemini -> Groq -> DeepSeek."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        api_key: str | None = None,
        groq_api_key: str | None = None,
        deepseek_api_key: str | None = None,
        client: genai.Client | None = None,
        rate_limiter: GeminiRateLimiter | None = None,
        db: Any | None = None,
        feedback_block: str | None = None,
    ) -> None:
        self.config = config or load_config()
        v4_cfg = self.config.get("scoring_v4") or {}
        llm_cfg = self.config.get("llm") or {}

        self.google_model = str(v4_cfg.get("model") or DEFAULT_V4_GOOGLE_MODEL)
        self.groq_model = str(v4_cfg.get("groq_model") or DEFAULT_V4_GROQ_MODEL)
        self.deepseek_model = str(v4_cfg.get("deepseek_model") or DEFAULT_V4_DEEPSEEK_MODEL)
        self.temperature = 0.0

        rpm = int(llm_cfg.get("rate_limit_rpm", 60))
        rpd = int(llm_cfg.get("rate_limit_rpd", 5000))
        self.rate_limiter = rate_limiter or get_global_rate_limiter(rpm, rpd)
        self._client = client

        self.google_api_key = (
            api_key if api_key is not None else _resolve_provider_key("google")
        )
        self.groq_api_key = (
            groq_api_key if groq_api_key is not None else _resolve_provider_key("groq")
        )
        self.deepseek_api_key = (
            deepseek_api_key
            if deepseek_api_key is not None
            else _resolve_provider_key("deepseek")
        )

        # Boucle de retour (N3 + N6) : calculée une fois par juge, à partir de la
        # base passée en paramètre (jamais lue implicitement) ou d'un bloc fourni.
        # Corrections de note d'Eddy d'abord, puis ses décisions récentes.
        if feedback_block is not None:
            self.feedback_block = feedback_block
        elif db is not None:
            self.feedback_block = build_calibration_block(db, self.config)
        else:
            self.feedback_block = ""

    @property
    def system_prompt(self) -> str:
        """Prompt système complet (grille + logistique locale + décisions d'Eddy)."""
        return get_system_prompt_v4(self.feedback_block)

    @property
    def available(self) -> bool:
        return bool(
            self._client
            or self.google_api_key
            or self.groq_api_key
            or self.deepseek_api_key
        )

    def _build_user_content(self, job: dict[str, Any]) -> str:
        description = (job.get("description") or "").strip()[:12000]
        return (
            "<offre>\n"
            f"Titre : {job.get('title', '')}\n"
            f"Entreprise : {job.get('company', '')}\n"
            f"Localisation : {job.get('location', '')}\n\n"
            f"Description :\n{description or '(description indisponible)'}\n"
            "</offre>"
        )

    def _call_google(self, user_content: str) -> str:
        logging.getLogger("google_genai.models").setLevel(logging.ERROR)
        client = self._client or genai.Client(api_key=self.google_api_key)
        self.rate_limiter.wait_for_slot()
        response = client.models.generate_content(
            model=self.google_model,
            contents=user_content,
            config=types.GenerateContentConfig(
                system_instruction=self.system_prompt,
                temperature=self.temperature,
                response_mime_type="application/json",
                response_schema=get_response_schema_v4(),
            ),
        )
        return str(response.text)

    def _call_openai_compat(self, provider: str, api_key: str, model: str, user_content: str) -> str:
        self.rate_limiter.wait_for_slot()
        base_url = PROVIDER_URLS[provider]
        system_prompt = (
            f"{self.system_prompt}\n\n"
            "Réponds uniquement par un objet JSON valide respectant strictement le format demandé."
        )
        return OpenAICompatibleProvider(
            api_key=api_key,
            base_url=base_url,
            model=model,
            temperature=self.temperature,
        ).generate(user_content, system_prompt)

    @staticmethod
    def _validate_raw(raw_text: str | dict[str, Any]) -> tuple[V4JudgeOutput, dict[str, Any]]:
        if isinstance(raw_text, dict):
            data = raw_text
        else:
            cleaned = _CODE_FENCE_RE.sub("", str(raw_text or "")).strip()
            data = json.loads(cleaned)
        if not isinstance(data, dict):
            raise ValueError("La réponse JSON n'est pas un objet.")
        validated = V4JudgeOutput.model_validate(data)
        return validated, data

    def _non_note_result(self, reason: str) -> dict[str, Any]:
        """Résultat renvoyé lorsque tous les essais ont échoué : statut NON_NOTE, jamais de note inventée."""
        return {
            "v4_status": "NON_NOTE",
            "v4_class": None,
            "v4_stars_json": None,
            "v4_center": None,
            "v4_adjustment": None,
            "v4_class_score": None,
            "v4_display_score": None,
            "v4_tags_json": None,
            "v4_flags_json": json.dumps([f"[NON_NOTE] {reason}"], ensure_ascii=False),
            "v4_raw_json": None,
            "v4_model": None,
            "v4_graded_at": None,
            "excluded": False,
            "exclusion_reason": None,
            "contract_to_verify": False,
            "api_error": True,
            "error_reason": reason,
        }

    def judge(self, job: dict[str, Any]) -> dict[str, Any]:
        """Évalue une offre en v4 : filtre titre amont -> Gemini (2 essais) -> Groq -> DeepSeek -> NON_NOTE."""
        # 1. Exclusion amont par le titre (économise le quota API)
        exclusion_kws = self.config.get("scoring_v3", {}).get("exclusion_contract_keywords", [])
        title_excluded, title_reason = is_title_excluded_contract(job.get("title", ""), exclusion_kws)
        if title_excluded:
            now = datetime.utcnow()
            return {
                "v4_status": "EXCLU",
                "v4_class": "E",
                "v4_stars_json": json.dumps({k: 0 for k in V4_AXIS_KEYS}, ensure_ascii=False),
                "v4_center": 0.0,
                "v4_adjustment": 0,
                "v4_class_score": 0.0,
                "v4_display_score": 0.0,
                "v4_tags_json": "[]",
                "v4_flags_json": json.dumps(
                    [f"{V4_FLAG_EXCLUDED_PREFIX} {title_reason}"], ensure_ascii=False
                ),
                "v4_raw_json": json.dumps({"excluded": True, "reason": title_reason}, ensure_ascii=False),
                "v4_model": "rule:title_exclusion",
                "v4_graded_at": now,
                "excluded": True,
                "exclusion_reason": title_reason,
                "contract_to_verify": False,
                "api_error": False,
            }

        if not self.available:
            return self._non_note_result("Aucune clé API (GEMINI_API_KEY / GROQ_API_KEY / DEEPSEEK_API_KEY) disponible.")

        user_content = self._build_user_content(job)

        providers: list[tuple[str, str, Any]] = []
        if self._client or self.google_api_key:
            providers.append(("google", self.google_model, lambda: self._call_google(user_content)))
        if self.groq_api_key:
            providers.append(
                (
                    "groq",
                    self.groq_model,
                    lambda: self._call_openai_compat("groq", self.groq_api_key, self.groq_model, user_content),
                )
            )
        if self.deepseek_api_key:
            providers.append(
                (
                    "deepseek",
                    self.deepseek_model,
                    lambda: self._call_openai_compat(
                        "deepseek", self.deepseek_api_key, self.deepseek_model, user_content
                    ),
                )
            )

        errors: list[str] = []
        for prov_name, model_name, caller in providers:
            # 2 essais sur le fournisseur principal (ou 1 nouvel essai si JSON invalide)
            max_attempts = 2 if prov_name == providers[0][0] else 1
            for attempt in range(1, max_attempts + 1):
                try:
                    raw_resp = caller()
                    validated, raw_dict = self._validate_raw(raw_resp)
                    return v4_result_from_output(
                        validated, raw_dict, job, self.config, f"{prov_name}:{model_name}"
                    )
                except (ValidationError, json.JSONDecodeError, ValueError, Exception) as exc:
                    err_str = f"{prov_name}({model_name}) essai {attempt}: {exc}"
                    logger.warning("Juge v4 échec — %s", err_str)
                    errors.append(err_str)

        return self._non_note_result(" ; ".join(errors))

    def evaluate_single_job_v4(self, job: dict[str, Any]) -> dict[str, Any]:
        """Alias historique de ``judge`` (appelé par l'API /api/jobs/grade-v4)."""
        return self.judge(job)
