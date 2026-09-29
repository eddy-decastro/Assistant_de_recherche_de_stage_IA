"""Sérialisation des offres pour le composant ``job_feed`` (fonctions pures).

Le composant affiche les valeurs via ``textContent`` : les chaînes sont donc
transmises brutes (jamais échappées en HTML).
"""
from __future__ import annotations

import re
from typing import Any, Sequence

from src.constants import (
    FLAG_LABELS,
    FLAG_TONES,
    HARD_CAP_LABELS,
    STATUS_EXCLUDED,
    STATUS_NEW,
    STRUCTURE_TYPE_LABELS,
    STRUCTURE_TYPE_TONES,
    SUB_SCORE_KEYS,
    SUB_SCORE_LABELS,
    SUB_SCORE_SHORT_LABELS,
    TIER_1,
    TIER_ESN,
    TIER_LABELS,
    VERDICT_LABELS,
    coerce_sub_score,
    source_label,
)
from utils.data import (
    STATUS_LABELS,
    VERDICT_TONES,
    contract_label,
    detected_technologies,
    effective_score,
    group_jobs_by_source,
    is_reranked,
    relative_date,
    score_alignment,
    source_color,
)

DESCRIPTION_LIMIT = 4000
TRUNCATION_MARK = "\n… (fiche tronquée : voir l'offre)"

# (clé du signal, libellé, tonalité) — mêmes signaux que la grille v3.
SIGNAL_DEFS = (
    ("encadrant_explicite", "Encadrant explicite (+6)", "positive"),
    ("donnees_reelles_explicites", "Données réelles (+3)", "positive"),
    ("suite_explicite", "Débouché / Thèse (+3)", "positive"),
    ("donnees_benchmark_seulement", "Benchmark seul (-5)", "alert"),
)

_SUBSCORE_TONES = {5: "positive", 4: "accent", 3: "mute", 2: "warn"}


def _one_line(value: Any) -> str:
    """Compacte tous les espaces (titres, entreprises, lieux)."""
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _multiline(value: Any) -> str:
    """Compacte les espaces mais conserve les sauts de ligne (fiche de poste)."""
    text = re.sub(r"[\t\r ]+", " ", str(value or ""))
    return re.sub(r"\n{2,}", "\n", text).strip()


def _limited(text: str) -> str:
    """Borne la fiche de poste et signale la coupe."""
    return text if len(text) <= DESCRIPTION_LIMIT else text[:DESCRIPTION_LIMIT] + TRUNCATION_MARK


def _texts(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    return [str(item) for item in values if str(item).strip()]


def _http_url(value: Any) -> str:
    url = str(value or "").strip()
    return url if re.match(r"^https?://", url, re.IGNORECASE) else ""


def _badges(job: dict[str, Any]) -> list[dict[str, str]]:
    badges: list[dict[str, str]] = []
    contract = contract_label(job)
    if contract:
        badges.append({"label": contract, "tone": "mute"})

    structure = job.get("structure_type")
    if structure:
        badges.append(
            {
                "label": STRUCTURE_TYPE_LABELS.get(structure, str(structure)),
                "tone": STRUCTURE_TYPE_TONES.get(structure, "mute"),
            }
        )
    else:
        tier = job.get("company_tier")
        if tier in (TIER_1, TIER_ESN):
            badges.append(
                {"label": TIER_LABELS.get(tier, str(tier)), "tone": "positive" if tier == TIER_1 else "alert"}
            )

    if job.get("floor_reason"):
        badges.append({"label": str(job["floor_reason"]), "tone": "positive"})
    elif job.get("floor_value"):
        badges.append({"label": f"plancher {job['floor_value']}", "tone": "positive"})

    cap = job.get("cap_applied") or job.get("hard_cap_triggered")
    if cap:
        cap_key = str(cap).strip().upper()
        badges.append({"label": HARD_CAP_LABELS.get(cap_key, f"plafond {cap_key}"), "tone": "alert"})

    if job.get("scaleup_suggested"):
        badges.append({"label": "scale-up suggérée (à confirmer)", "tone": "warn"})

    for flag in job.get("flags") or []:
        key = str(flag).upper()
        badges.append({"label": FLAG_LABELS.get(key, key), "tone": FLAG_TONES.get(key, "warn")})

    if not is_reranked(job) and (job.get("status") == STATUS_EXCLUDED or job.get("exclusion_reason")):
        badges.append({"label": "EXCLU", "tone": "alert"})
    return badges


def _sub_scores(job: dict[str, Any]) -> list[dict[str, Any]]:
    if not is_reranked(job):
        return []
    raw = job.get("sub_scores")
    if not isinstance(raw, dict) or not raw:
        return []
    keys = [k for k in SUB_SCORE_KEYS if k in raw] + [
        k for k in raw if k not in SUB_SCORE_KEYS and k in SUB_SCORE_LABELS
    ]
    if not keys:
        keys = list(SUB_SCORE_KEYS)
    items = []
    for key in keys:
        value = coerce_sub_score(raw.get(key))
        items.append(
            {
                "key": key,
                "label": SUB_SCORE_LABELS.get(key, key),
                "short": SUB_SCORE_SHORT_LABELS.get(key, SUB_SCORE_LABELS.get(key, key)),
                "value": value,
                "tone": _SUBSCORE_TONES.get(value, "alert"),
            }
        )
    return items


def _signals(job: dict[str, Any]) -> list[dict[str, str]]:
    raw = job.get("signals")
    if not isinstance(raw, dict):
        return []
    items = []
    for key, label, tone in SIGNAL_DEFS:
        data = raw.get(key)
        if isinstance(data, dict) and data.get("present"):
            evidence = str(data.get("evidence") or data.get("citation") or "").strip()
            items.append({"label": label, "tone": tone, "evidence": evidence})
    return items


def serialize_job(job: dict[str, Any], keywords: Sequence[str], group: str = "") -> dict[str, Any]:
    """Convertit une offre de la base en dictionnaire JSON pour le composant."""
    reranked = is_reranked(job)
    score = effective_score(job)
    align_label, align_tone = score_alignment(score)
    status = job.get("status") or STATUS_NEW
    verdict = (job.get("verdict") or "") if reranked else ""
    quality = job.get("quality_score")
    return {
        "id": str(job.get("id")),
        "title": _one_line(job.get("title")) or "Offre sans titre",
        "company": _one_line(job.get("company")) or "Entreprise inconnue",
        "location": _one_line(job.get("location")),
        "date_label": relative_date(job.get("created_at")),
        "source_label": source_label(job.get("source")),
        "source_color": source_color(job.get("source")),
        "score": int(round(score)),
        "score_origin": "rerank" if reranked else "hybride",
        "quality": float(quality) if reranked and quality is not None else None,
        "align_label": align_label,
        "align_tone": align_tone,
        "reranked": reranked,
        "verdict": verdict,
        "verdict_label": VERDICT_LABELS.get(verdict, verdict) if verdict else "",
        "verdict_tone": VERDICT_TONES.get(verdict, "mute") if verdict else "mute",
        "status": status,
        "status_label": STATUS_LABELS.get(status, status),
        "is_new": status == STATUS_NEW,
        "hard_cap": (job.get("hard_cap_triggered") or "").strip(),
        "badges": _badges(job),
        "sub_scores": _sub_scores(job),
        "signals": _signals(job),
        "technologies": detected_technologies(job, keywords),
        "strengths": _texts(job.get("match_reasons")),
        "red_flags": _texts(job.get("red_flags")),
        "reasoning": _multiline(job.get("reasoning")) if reranked else "",
        "description": _limited(_multiline(job.get("description"))),
        "rejection_reason": (job.get("rejection_reason") or "").strip(),
        "url": _http_url(job.get("url")),
        "group": group,
    }


def serialize_feed(
    jobs: list[dict[str, Any]], keywords: Sequence[str], *, grouped: bool = False
) -> list[dict[str, Any]]:
    """Sérialise le flux ; en mode groupé, l'ordre suit les groupes de plateforme."""
    if grouped:
        return [
            serialize_job(job, keywords, group=label)
            for label, members in group_jobs_by_source(jobs)
            for job in members
        ]
    return [serialize_job(job, keywords) for job in jobs]
