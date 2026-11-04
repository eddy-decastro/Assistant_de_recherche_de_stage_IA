"""Sérialisation des offres pour le composant ``job_feed`` (fonctions pures).

Le composant affiche les valeurs via ``textContent`` : les chaînes sont donc
transmises brutes (jamais échappées en HTML).
"""
from __future__ import annotations

import re
from typing import Any, Sequence

from src.constants import (
    STATUS_IGNORED,
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
    V4_AXIS_KEYS,
    V4_AXIS_LABELS,
    V4_CLASS_LABELS,
    V4_CLASS_TONES,
    V4_TAG_LABELS,
    V4_TAG_TONES,
    VERDICT_LABELS,
    coerce_sub_score,
    ignore_reason_label,
    normalize_v4_tag,
    source_label,
)
from utils.data import (
    STATUS_LABELS,
    VERDICT_TONES,
    contract_label,
    detected_technologies,
    display_score,
    effective_score,
    exclusion_info,
    group_jobs_by_source,
    has_v4_score,
    is_contract_to_verify,
    is_reranked,
    job_age_days,
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

# Bloc « Questions clés » que l'ancien juge v3 collait à la fin de ``reasoning`` :
# les questions ont leur propre champ (questions_entretien).
_QUESTIONS_BLOCK_RE = re.compile(
    r"\s*(?:💡\s*)?Questions clés pour l'entretien\s*:(?P<body>.*)\Z",
    re.DOTALL | re.IGNORECASE,
)

# États de la note affichée (champ ``score_state``).
SCORE_STATE_V4 = "v4"
SCORE_STATE_UNSCORED = "non_notee_v4"
SCORE_STATE_EXCLUDED = "exclue"
SCORE_STATE_LABELS = {
    SCORE_STATE_V4: "Note v4",
    SCORE_STATE_UNSCORED: "Non notée v4",
    SCORE_STATE_EXCLUDED: "Exclue",
}


def split_reasoning(value: Any) -> tuple[str, list[str]]:
    """Sépare le raisonnement v3 du bloc « Questions clés pour l'entretien » collé à la fin."""
    text = str(value or "")
    match = _QUESTIONS_BLOCK_RE.search(text)
    if not match:
        return text, []
    questions = [
        line.strip().lstrip("•-*").strip()
        for line in match.group("body").splitlines()
        if line.strip().lstrip("•-*").strip()
    ]
    return text[: match.start()].rstrip(), questions


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


def _texts(values: Any, limit: int | None = None) -> list[str]:
    if isinstance(values, str):
        try:
            import json
            parsed = json.loads(values)
            if isinstance(parsed, (list, tuple)):
                values = parsed
            else:
                values = [values]
        except Exception:
            values = [values]
    elif not isinstance(values, (list, tuple)):
        return []
    res = [str(item).strip() for item in values if str(item).strip()]
    return res[:limit] if limit is not None else res


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

    evidence_raw = job.get("evidence")
    if isinstance(evidence_raw, str):
        try:
            import json
            evidence_dict = json.loads(evidence_raw)
            if not isinstance(evidence_dict, dict):
                evidence_dict = {}
        except Exception:
            evidence_dict = {}
    elif isinstance(evidence_raw, dict):
        evidence_dict = evidence_raw
    else:
        evidence_dict = {}

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
                "evidence": str(evidence_dict.get(key) or "").strip(),
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


def _v4_axes(job: dict[str, Any], use_raw_stars: bool = False) -> list[dict[str, Any]]:
    """Axes v4. ``use_raw_stars`` : offre exclue -> étoiles brutes du LLM (pas les 0 effectifs)."""
    raw = job.get("v4_stars")
    if isinstance(raw, str):
        try:
            import json
            raw = json.loads(raw)
        except Exception:
            raw = None
    if not isinstance(raw, dict) or not raw:
        return []
    items = []
    for key in V4_AXIS_KEYS:
        axis = raw.get(key)
        if not isinstance(axis, dict):
            continue
        try:
            effective = max(0, min(5, int(axis.get("stars", 0))))
        except Exception:
            effective = 0
        try:
            raw_stars = max(0, min(5, int(axis.get("raw_stars", effective))))
        except Exception:
            raw_stars = effective
        stars = raw_stars if use_raw_stars else effective
        reason = _one_line(axis.get("reason"))
        quote = _one_line(axis.get("quote"))
        unknown = bool(axis.get("unknown", False))
        tone = _SUBSCORE_TONES.get(stars, "alert")
        items.append(
            {
                "key": key,
                "label": V4_AXIS_LABELS.get(key, key),
                "stars": stars,
                "raw_stars": raw_stars,
                "effective_stars": effective,
                "reason": reason,
                "quote": quote,
                "unknown": unknown,
                "tone": tone,
            }
        )
    return items


def _v4_tags(job: dict[str, Any]) -> list[dict[str, str]]:
    raw = job.get("v4_tags")
    if isinstance(raw, str):
        try:
            import json
            raw = json.loads(raw)
        except Exception:
            raw = []
    if not isinstance(raw, (list, tuple)):
        return []
    items = []
    seen = set()
    for tag in raw:
        key = normalize_v4_tag(tag)
        if not key or key in seen:
            continue
        seen.add(key)
        items.append(
            {
                "key": key,
                "label": V4_TAG_LABELS.get(key, key),
                "tone": V4_TAG_TONES.get(key, "mute"),
            }
        )
    return items


def serialize_job(
    job: dict[str, Any],
    keywords: Sequence[str] | int = (),
    group: str = "",
    index: int = 0,
    **kwargs: Any,
) -> dict[str, Any]:
    """Convertit une offre de la base en dictionnaire JSON pour le composant."""
    if isinstance(keywords, int):
        kw_seq: Sequence[str] = ()
    else:
        kw_seq = keywords
    _ = index

    reranked = is_reranked(job)
    score = effective_score(job)
    align_label, align_tone = score_alignment(score)
    status = job.get("status") or STATUS_NEW
    verdict = (job.get("verdict") or "") if reranked else ""
    quality = job.get("quality_score")

    company_note_raw = job.get("company_note")
    if isinstance(company_note_raw, str) and company_note_raw.strip().startswith("{"):
        try:
            import json
            company_note_dict = json.loads(company_note_raw)
        except Exception:
            company_note_dict = {}
    elif isinstance(company_note_raw, dict):
        company_note_dict = company_note_raw
    else:
        company_note_dict = None

    if company_note_dict is not None:
        if company_note_dict.get("known") is True:
            company_note_str = str(company_note_dict.get("note") or "").strip()
        else:
            company_note_str = ""
    elif isinstance(company_note_raw, str):
        company_note_str = company_note_raw.strip()
    else:
        company_note_str = ""

    reasoning_text, questions_from_reasoning = split_reasoning(job.get("reasoning"))
    questions_entretien = _texts(job.get("questions_entretien"), limit=5)
    if not questions_entretien:
        questions_entretien = questions_from_reasoning[:5]
    user_notes_str = str(job.get("user_notes") or "").strip()

    v4_class = str(job.get("v4_class") or "").strip().upper()
    v4_disp_raw = job.get("v4_display_score")
    v4_cls_raw = job.get("v4_class_score")
    v4_ctr_raw = job.get("v4_center")
    v4_adj_raw = job.get("v4_adjustment")
    v4_raw_obj = job.get("v4_raw")
    if isinstance(v4_raw_obj, str):
        try:
            import json
            v4_raw_obj = json.loads(v4_raw_obj)
        except Exception:
            v4_raw_obj = None
    v4_summary = _one_line(v4_raw_obj.get("summary")) if isinstance(v4_raw_obj, dict) else ""
    v4_class_reason = _one_line(v4_raw_obj.get("class_reason")) if isinstance(v4_raw_obj, dict) else ""
    v4_adj_reason = _one_line(v4_raw_obj.get("adjustment_reason")) if isinstance(v4_raw_obj, dict) else ""
    raw_dict = v4_raw_obj if isinstance(v4_raw_obj, dict) else {}

    # Note unique affichée (N1/N2) : la v4, sinon « non notée v4 » ; une offre
    # exclue n'a pas de note affichée mais garde ses étoiles brutes.
    v4_graded = has_v4_score(job)
    excluded, exclusion_reason = exclusion_info(job)
    shown = display_score(job)
    if excluded:
        score_state = SCORE_STATE_EXCLUDED
    elif v4_graded:
        score_state = SCORE_STATE_V4
    else:
        score_state = SCORE_STATE_UNSCORED
    if shown is not None:
        display_label = f"{v4_class} · {int(round(shown))}" if v4_class else str(int(round(shown)))
        display_align_label, display_align_tone = score_alignment(shown)
    else:
        display_label = SCORE_STATE_LABELS[score_state]
        display_align_label, display_align_tone = SCORE_STATE_LABELS[score_state], (
            "alert" if excluded else "mute"
        )
    ignore_code = str(job.get("rejection_reason") or "").strip()

    return {
        "id": str(job.get("id")),
        "title": _one_line(job.get("title")) or "Offre sans titre",
        "company": _one_line(job.get("company")) or "Entreprise inconnue",
        "location": _one_line(job.get("location")),
        # Même référence que age_days : la publication, sinon la collecte (U3).
        "date_label": relative_date(job.get("published_at") or job.get("created_at")),
        "age_days": job_age_days(job),
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
        "technologies": detected_technologies(job, kw_seq),
        "strengths": _texts(job.get("match_reasons")),
        "red_flags": _texts(job.get("red_flags")),
        "reasoning": _multiline(reasoning_text) if reranked else "",
        "description": _limited(_multiline(job.get("description"))),
        "rejection_reason": (job.get("rejection_reason") or "").strip(),
        "url": _http_url(job.get("url")),
        "group": group,
        "company_note": company_note_str,
        "questions_entretien": questions_entretien,
        "user_notes": user_notes_str,
        "v4_class": v4_class,
        "v4_class_label": V4_CLASS_LABELS.get(v4_class, v4_class) if v4_class else "",
        "v4_class_tone": V4_CLASS_TONES.get(v4_class, "mute") if v4_class else "mute",
        "v4_display_score": int(round(float(v4_disp_raw))) if v4_disp_raw is not None else None,
        "v4_class_score": int(round(float(v4_cls_raw))) if v4_cls_raw is not None else None,
        "v4_center": float(v4_ctr_raw) if v4_ctr_raw is not None else None,
        "v4_adjustment": int(v4_adj_raw) if v4_adj_raw is not None else None,
        "v4_axes": _v4_axes(job, use_raw_stars=excluded),
        "v4_axes_raw": bool(excluded and v4_graded),
        "v4_tags": _v4_tags(job),
        "v4_flags": _texts(job.get("v4_flags")),
        "v4_summary": v4_summary,
        "v4_class_reason": v4_class_reason,
        "v4_adj_reason": v4_adj_reason,
        # --- Note unique affichée (le front n'affiche plus que celle-ci) ---
        "display_score": int(round(shown)) if shown is not None else None,
        "display_class": v4_class if v4_graded else "",
        "display_class_label": V4_CLASS_LABELS.get(v4_class, v4_class) if v4_graded and v4_class else "",
        "display_class_tone": V4_CLASS_TONES.get(v4_class, "mute") if v4_graded and v4_class else "mute",
        "display_label": display_label,
        "display_align_label": display_align_label,
        "display_align_tone": display_align_tone,
        "score_state": score_state,
        "score_state_label": SCORE_STATE_LABELS[score_state],
        "v4_graded": v4_graded,
        # --- Exclusion (onglet à part) et contrat ---
        "excluded": excluded,
        "exclusion_reason": exclusion_reason,
        "contract_to_verify": is_contract_to_verify(job),
        "contract_type_v4": str(raw_dict.get("contract_type") or "") if v4_graded else "",
        "contract_quote": _one_line(raw_dict.get("contract_quote")) if v4_graded else "",
        # --- Décision d'Eddy (raison d'ignorer en un clic) ---
        "ignore_reason": ignore_code if status == STATUS_IGNORED else "",
        "ignore_reason_label": ignore_reason_label(ignore_code) if status == STATUS_IGNORED else "",
        "ignore_comment": str(job.get("rejection_comment") or "").strip(),
        # --- Correction de note par Eddy (N6) : ce qu'il aurait mis ---
        "user_v4_class": str(job.get("user_v4_class") or "").strip().upper(),
        "user_v4_score": (
            int(job["user_v4_score"]) if job.get("user_v4_score") is not None else None
        ),
        "user_v4_comment": str(job.get("user_v4_comment") or "").strip(),
        "user_v4_at": (
            job["user_v4_at"].isoformat()
            if hasattr(job.get("user_v4_at"), "isoformat")
            else str(job.get("user_v4_at") or "")
        ),
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
