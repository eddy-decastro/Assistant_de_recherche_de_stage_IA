"""Calcul déterministe de la note v3 : sous-scores, bonus vérifiés, planchers et plafonds.

Aucun appel réseau ni LLM ici : le juge fournit des éléments structurés, ce module
en tire la note finale de façon reproductible (tests/test_golden_regression.py).
"""
from __future__ import annotations

import logging
import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from src.config import load_config
from src.constants import (
    BENCHMARK_PENALTY,
    DEFAULT_SUB_SCORE,
    HARD_CAP_RULES,
    MAX_BONUS_TOTAL,
    SIGNAL_BONUSES,
    SUB_SCORE_KEYS,
    SUB_SCORE_WEIGHTS,
    VERDICT_EXCELLENT,
    VERDICT_GOOD,
    VERDICT_MIXED,
    VERDICT_OFF_TOPIC,
    coerce_sub_score,
)
from src.eligibility import experience_reason, not_internship_reason
from src.matching.scorer import Scorer

logger = logging.getLogger("src.matching.scoring_v3")


@dataclass
class ScoreBreakdown:
    """Résultat détaillé et déterministe du calcul de score v3."""
    quality_score: int
    final_score: int
    floor_value: int | None
    floor_reason: str | None
    cap_applied: str | None
    cap_value: int | None
    excluded: bool
    exclusion_reason: str | None
    scaleup_suggested: bool


def verdict_from_score(score: float, config: dict[str, Any] | None = None) -> str:
    """Déduit un verdict à partir d'un score 0-100 et des seuils config."""
    cfg = (config or load_config()).get("scoring_v3", {}).get("verdict_thresholds", {})
    t_exc = float(cfg.get("excellent", 85))
    t_good = float(cfg.get("good", 70))
    t_mixed = float(cfg.get("mixed", 50))
    if score >= t_exc:
        return VERDICT_EXCELLENT
    if score >= t_good:
        return VERDICT_GOOD
    if score >= t_mixed:
        return VERDICT_MIXED
    return VERDICT_OFF_TOPIC


def normalize_text_for_evidence(text: str) -> str:
    """Normalise un texte : minuscules, sans accents, ponctuation remplacée par espaces."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(text).casefold())
    ascii_clean = "".join(c for c in decomposed if not unicodedata.combining(c))
    cleaned = re.sub(r"[^\w\s]", " ", ascii_clean)
    return re.sub(r"\s+", " ", cleaned).strip()


_GLOBAL_CITATION_STATS = {"checked": 0, "verified": 0, "rejected": 0}


def verify_citation(citation: str | None, full_text: str, tolerance: float = 0.8) -> bool:
    """Vérifie si une citation apparaît dans le texte de l'offre (tolérance 80% des mots)."""
    global _GLOBAL_CITATION_STATS
    if not citation or not full_text:
        return False
    norm_cit = normalize_text_for_evidence(citation)
    if not norm_cit or norm_cit in ("non precise", "non", "null", "none"):
        return False

    norm_doc = normalize_text_for_evidence(full_text)
    _GLOBAL_CITATION_STATS["checked"] += 1

    # 1. Correspondance exacte en sous-chaîne
    if norm_cit in norm_doc:
        _GLOBAL_CITATION_STATS["verified"] += 1
        return True

    words_cit = norm_cit.split()
    if not words_cit:
        _GLOBAL_CITATION_STATS["rejected"] += 1
        return False

    if len(words_cit) <= 2:
        ok = norm_cit in norm_doc
        if ok:
            _GLOBAL_CITATION_STATS["verified"] += 1
        else:
            _GLOBAL_CITATION_STATS["rejected"] += 1
        return ok

    # 2. Fenêtre glissante avec tolérance 80%
    words_doc = norm_doc.split()
    doc_len = len(words_doc)
    cit_len = len(words_cit)
    required_matches = int(math.ceil(tolerance * cit_len))
    target_set = set(words_cit)
    window_size = cit_len + 6

    for i in range(max(1, doc_len - window_size + 1)):
        window = set(words_doc[i : i + window_size])
        if len(target_set.intersection(window)) >= required_matches:
            _GLOBAL_CITATION_STATS["verified"] += 1
            return True

    _GLOBAL_CITATION_STATS["rejected"] += 1
    logger.info("Citation non vérifiée : %r", citation)
    return False


def is_title_excluded_contract(title: str, exclusion_keywords: list[str]) -> tuple[bool, str | None]:
    """Exclusion AVANT appel LLM si le titre contient un mot exclu SANS mention de stage."""
    norm_title = normalize_text_for_evidence(title)
    if "stage" in norm_title or "intern" in norm_title or "pfe" in norm_title:
        return False, None
    for kw in exclusion_keywords:
        norm_kw = normalize_text_for_evidence(kw)
        if norm_kw and re.search(rf"(?<![\w]){re.escape(norm_kw)}(?![\w])", norm_title):
            return True, f"Titre contient '{kw}' sans mention de stage"
    return False, None


def compute_final_score(
    parsed: dict[str, Any],
    job: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> ScoreBreakdown:
    """Calcule la note finale pure et testable sans effet de bord ni appel réseau."""
    cfg = config or load_config()
    scoring_v3 = cfg.get("scoring_v3", {})
    min_duration = int(scoring_v3.get("min_duration_months", 4))
    floors_cfg = scoring_v3.get("floors", {})
    scaleup_floor = int(floors_cfg.get("scaleup", 70))
    rd_floor = int(floors_cfg.get("rd", 60))
    labo_public_floor = int(floors_cfg.get("labo_public", 50))
    min_tech_depth = int(floors_cfg.get("min_technical_depth", 3))
    trust_llm_scaleup = bool(floors_cfg.get("trust_llm_scaleup", False))

    bonuses_cfg = scoring_v3.get("bonuses", {})
    bonus_cap = int(bonuses_cfg.get("bonus_cap", MAX_BONUS_TOTAL))
    benchmark_penalty = int(bonuses_cfg.get("benchmark_penalty", BENCHMARK_PENALTY))

    companies_cfg = cfg.get("companies", {})
    scaleup_list = companies_cfg.get("scaleup", [])
    rd_groups_list = companies_cfg.get("rd_groups", [])
    excluded_defense_list = companies_cfg.get("excluded_defense", [])

    doc_text = f"{job.get('title', '')}\n{job.get('description', '')}"

    # 1. Vérification d'exclusion amont (titre)
    exclusion_kws = scoring_v3.get("exclusion_contract_keywords", [])
    title_excluded, title_reason = is_title_excluded_contract(job.get("title", ""), exclusion_kws)
    if title_excluded:
        return ScoreBreakdown(
            quality_score=0,
            final_score=0,
            floor_value=None,
            floor_reason=None,
            cap_applied=None,
            cap_value=None,
            excluded=True,
            exclusion_reason=title_reason,
            scaleup_suggested=False,
        )

    # 2. Vérification d'exclusion aval (LLM)
    contract_type = str(parsed.get("contract_type") or "AUTRE").upper()
    if contract_type in ("ALTERNANCE", "CDI_CDD"):
        return ScoreBreakdown(
            quality_score=0,
            final_score=0,
            floor_value=None,
            floor_reason=None,
            cap_applied=None,
            cap_value=None,
            excluded=True,
            exclusion_reason=f"Type de contrat incompatible ({contract_type})",
            scaleup_suggested=False,
        )

    eligibility_reason = (
        not_internship_reason(job.get("title"), job.get("description"))
        or experience_reason(job.get("title"), job.get("description"))
    )
    if eligibility_reason:
        return ScoreBreakdown(
            quality_score=0,
            final_score=0,
            floor_value=None,
            floor_reason=None,
            cap_applied=None,
            cap_value=None,
            excluded=True,
            exclusion_reason=eligibility_reason,
            scaleup_suggested=False,
        )

    is_cesure = bool(parsed.get("is_cesure", False))
    if is_cesure:
        return ScoreBreakdown(
            quality_score=0,
            final_score=0,
            floor_value=None,
            floor_reason=None,
            cap_applied=None,
            cap_value=None,
            excluded=True,
            exclusion_reason="Stage de césure / court hors PFE",
            scaleup_suggested=False,
        )

    duration = parsed.get("duration_months")
    if duration is not None:
        try:
            d_val = int(duration)
            if d_val < min_duration:
                return ScoreBreakdown(
                    quality_score=0,
                    final_score=0,
                    floor_value=None,
                    floor_reason=None,
                    cap_applied=None,
                    cap_value=None,
                    excluded=True,
                    exclusion_reason=f"Durée inférieure au seuil minimal ({d_val} mois < {min_duration} mois)",
                    scaleup_suggested=False,
                )
        except (ValueError, TypeError):
            pass

    # 3. Note de qualité
    sub_scores_raw = parsed.get("sub_scores") or {}
    sub_scores: dict[str, int] = {}
    for key in SUB_SCORE_KEYS:
        val = sub_scores_raw.get(key)
        sub_scores[key] = coerce_sub_score(val) if val is not None else DEFAULT_SUB_SCORE

    weighted_sum = sum(SUB_SCORE_WEIGHTS[k] * sub_scores[k] for k in SUB_SCORE_KEYS)
    q_base = (weighted_sum - 1.0) / 4.0 * 100.0

    # Signaux & bonus
    signals = parsed.get("signals") or {}
    total_bonus = 0
    penalty = 0

    for sig_name in ("encadrant_explicite", "donnees_reelles_explicites", "suite_explicite"):
        sig_data = signals.get(sig_name) or {}
        if sig_data.get("present"):
            ev = sig_data.get("evidence")
            if verify_citation(ev, doc_text):
                total_bonus += SIGNAL_BONUSES.get(sig_name, 0)
            else:
                red_flags = parsed.setdefault("red_flags", [])
                tag = f"[CITATION_NON_VERIFIEE] {sig_name}"
                if tag not in red_flags:
                    red_flags.append(tag)

    total_bonus = min(total_bonus, bonus_cap)

    # Signal négatif données de benchmark seulement
    bench_data = signals.get("donnees_benchmark_seulement") or {}
    if bench_data.get("present"):
        ev = bench_data.get("evidence")
        if verify_citation(ev, doc_text):
            penalty += benchmark_penalty
        else:
            red_flags = parsed.setdefault("red_flags", [])
            tag = "[CITATION_NON_VERIFIEE] donnees_benchmark_seulement"
            if tag not in red_flags:
                red_flags.append(tag)

    quality = max(0, min(100, int(round(q_base + total_bonus - penalty))))

    # 4. Plancher (Floor)
    company_name = job.get("company", "")
    structure_type = str(parsed.get("structure_type") or "INCONNU").upper()
    rd_nature = bool(parsed.get("rd_nature", False))

    in_scaleup_list = any(Scorer._name_matches(company_name, str(c)) for c in scaleup_list)
    in_rd_groups = any(Scorer._name_matches(company_name, str(c)) for c in rd_groups_list)

    floor_val: int | None = None
    floor_reason: str | None = None
    scaleup_suggested = False

    if structure_type == "SCALEUP_IA" and not in_scaleup_list:
        scaleup_suggested = True

    # Détermination de l'éligibilité au plancher (par ordre de priorité)
    if in_scaleup_list and structure_type != "ESN_CONSEIL":
        floor_val = scaleup_floor
        floor_reason = f"Plancher scale-up {scaleup_floor}"
    elif (
        in_rd_groups
        or structure_type in ("GRAND_GROUPE_RD", "LABO_PRIVE")
        or (rd_nature and structure_type not in ("LABO_PUBLIC", "ESN_CONSEIL", "STARTUP_PETITE"))
    ):
        floor_val = rd_floor
        floor_reason = f"Plancher grand groupe R&D / labo privé {rd_floor}"
    elif structure_type == "LABO_PUBLIC":
        floor_val = labo_public_floor
        floor_reason = f"Plancher labo public {labo_public_floor}"
    elif scaleup_suggested and trust_llm_scaleup:
        floor_val = scaleup_floor
        floor_reason = f"Plancher scale-up LLM {scaleup_floor}"

    # Le plancher ne s'applique que si technical_depth >= min_tech_depth
    tech_depth = sub_scores.get("technical_depth", DEFAULT_SUB_SCORE)
    applied_floor: int | None = None
    if floor_val is not None:
        if tech_depth >= min_tech_depth:
            applied_floor = floor_val
        else:
            floor_reason = f"Plancher non accordé (profondeur technique {tech_depth} < {min_tech_depth})"
            floor_val = None

    score_after_floor = max(quality, applied_floor) if applied_floor is not None else quality

    # 5. Plafonds (Caps)
    cap_applied: str | None = None
    cap_val: int | None = None

    # Plafond DEFENSE automatique par liste
    in_excluded_defense = any(Scorer._name_matches(company_name, str(c)) for c in excluded_defense_list)
    if in_excluded_defense:
        cap_applied = "DEFENSE"
        cap_val = HARD_CAP_RULES.get("DEFENSE", 10)

    # Plafond déclenché par le LLM
    llm_cap_raw = str(parsed.get("hard_cap_triggered") or "").strip().upper()
    if llm_cap_raw in HARD_CAP_RULES and llm_cap_raw != "NONE":
        evidence = parsed.get("hard_cap_evidence")
        # Les plafonds DEFENSE / TRADING dus à une liste n'ont pas besoin de citation
        needs_citation = not (llm_cap_raw == "DEFENSE" and in_excluded_defense)
        cit_ok = verify_citation(evidence, doc_text) if needs_citation else True

        if cit_ok:
            candidate_val = HARD_CAP_RULES[llm_cap_raw]
            if cap_val is None or candidate_val < cap_val:
                cap_applied = llm_cap_raw
                cap_val = candidate_val
        else:
            red_flags = parsed.setdefault("red_flags", [])
            tag = f"[CITATION_NON_VERIFIEE] {llm_cap_raw}"
            if tag not in red_flags:
                red_flags.append(tag)

    final_score = min(score_after_floor, cap_val) if cap_val is not None else score_after_floor

    return ScoreBreakdown(
        quality_score=quality,
        final_score=final_score,
        floor_value=floor_val,
        floor_reason=floor_reason,
        cap_applied=cap_applied,
        cap_value=cap_val,
        excluded=False,
        exclusion_reason=None,
        scaleup_suggested=scaleup_suggested,
    )
