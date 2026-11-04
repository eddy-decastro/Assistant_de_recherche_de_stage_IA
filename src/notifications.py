"""Gestionnaire des notifications et automatisations de fin de pipeline.

Fonctionnalités :
- Envoi d'un digest synthétique des meilleures offres via Webhook (Discord / Slack / Telegram)
- Archivage des offres fermées (détection par ``scrapers.liveness`` : 404, redirection,
  « n'accepte plus de candidatures »…) ; l'archivage par âge reste disponible mais
  désactivé par défaut
- Pré-génération asynchrone des lettres de motivation pour les offres à très haut score
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from src.config import load_config
from src.constants import (
    CLOSED_OFFER_REASON,
    STATUS_EXCLUDED,
    STATUS_IGNORED,
    STATUS_NEW,
    STATUS_REJECTED,
)
from src.matching.cover_letter import CoverLetterGenerator
from src.storage.database import Database

logger = logging.getLogger("src.notifications")

__all__ = [
    "archive_expired_offers",
    "archive_old_offers",
    "pregenerate_top_cover_letters",
    "send_pipeline_digest",
]

def _effective_score(job: dict[str, Any]) -> float:
    """Retourne le score prioritaire de l'offre (note v4, puis rerank_score, puis final_score).

    Depuis N1, la note v4 est la note unique : les nouvelles offres n'ont plus de note v3.
    """
    score = job.get("v4_display_score")
    if score is None:
        score = job.get("rerank_score")
    if score is None:
        score = job.get("final_score")
    try:
        return float(score or 0.0)
    except (ValueError, TypeError):
        return 0.0


def _is_excluded(job: dict[str, Any]) -> bool:
    """Indique si l'offre est exclue ou rejetée par la modélisation."""
    if job.get("status") in (STATUS_EXCLUDED, STATUS_REJECTED):
        return True
    if str(job.get("verdict") or "").strip().upper() == "EXCLU":
        return True
    if job.get("exclusion_reason"):
        return True
    return False


def _parse_datetime(val: Any) -> datetime | None:
    """Convertit une chaîne ou un objet datetime en datetime UTC ou naïf."""
    if isinstance(val, datetime):
        return val
    if isinstance(val, str) and val.strip():
        clean = val.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(clean)
        except (ValueError, TypeError):
            pass
    return None


def _is_older_than(dt: datetime, days: int) -> bool:
    """Vérifie si la date spécifiée est plus ancienne que le nombre de jours donné."""
    now = datetime.now(timezone.utc) if dt.tzinfo else datetime.now()
    return dt < (now - timedelta(days=days))


def send_pipeline_digest(
    new_jobs: list[dict[str, Any]],
    config: dict[str, Any] | None = None,
    degraded_sources: int = 0,
) -> bool:
    """Envoie un digest Markdown par Webhook (Discord / Slack / Telegram) des meilleures nouvelles offres.

    Args:
        new_jobs: Liste des nouvelles offres collectées et notées lors du run.
        config: Configuration globale (charge config.yaml par défaut si None).
        degraded_sources: Nombre de sources de scraping ayant rencontré une anomalie.

    Returns:
        True si au moins une notification a été transmise avec succès (HTTP 2xx),
        False sinon (désactivé, aucune offre éligible, ou échec d'envoi).
    """
    cfg = config or load_config()
    auto_cfg = cfg.get("automations") or {}

    if not auto_cfg.get("notifications_enabled", False):
        return False

    min_score = float(auto_cfg.get("notification_min_score", 75))
    eligible_jobs = [
        j for j in new_jobs
        if not _is_excluded(j) and _effective_score(j) >= min_score
    ]
    eligible_jobs.sort(key=_effective_score, reverse=True)

    if not eligible_jobs and degraded_sources == 0:
        return False

    top_jobs = eligible_jobs[:10]
    lines = ["🎯 **Stage Copilot — Résumé de fin de pipeline**\n"]

    if degraded_sources > 0:
        lines.append(f"⚠️ **Alerte** : {degraded_sources} source(s) de scraping dégradée(s) lors de cette passe.\n")

    if top_jobs:
        lines.append(f"**{len(eligible_jobs)} nouvelle(s) offre(s) qualifiée(s)** (score >= {min_score:.0f}) :")
        for i, j in enumerate(top_jobs, 1):
            title = (j.get("title") or "Sans titre").strip()
            company = (j.get("company") or "Entreprise inconnue").strip()
            score = _effective_score(j)
            verdict = (j.get("verdict") or "N/A").strip()
            url = (j.get("url") or "").strip()
            if url:
                lines.append(f"{i}. [**{title}**]({url}) — {company} | Score : **{score:.0f}/100** ({verdict})")
            else:
                lines.append(f"{i}. **{title}** — {company} | Score : **{score:.0f}/100** ({verdict})")
    else:
        lines.append("Aucune nouvelle offre qualifiée lors de cette passe.")

    message = "\n".join(lines).strip()

    discord_url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    slack_url = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    tg_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    if not discord_url and not slack_url and not (tg_token and tg_chat_id):
        logger.warning("Notifications activées mais aucun webhook trouvé dans l'environnement.")
        return False

    success = False

    # Discord Webhook
    if discord_url:
        try:
            resp = requests.post(
                discord_url,
                json={"content": message[:1950]},
                timeout=10,
            )
            if 200 <= resp.status_code < 300:
                success = True
            else:
                logger.warning("Échec notification Discord : HTTP %s", resp.status_code)
        except Exception as exc:
            logger.warning("Exception lors de l'envoi Discord : %s", exc)

    # Slack Webhook
    if slack_url:
        try:
            resp = requests.post(
                slack_url,
                json={"text": message},
                timeout=10,
            )
            if 200 <= resp.status_code < 300:
                success = True
            else:
                logger.warning("Échec notification Slack : HTTP %s", resp.status_code)
        except Exception as exc:
            logger.warning("Exception lors de l'envoi Slack : %s", exc)

    # Telegram Bot
    if tg_token and tg_chat_id:
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{tg_token}/sendMessage",
                json={"chat_id": tg_chat_id, "text": message[:4000]},
                timeout=10,
            )
            if 200 <= resp.status_code < 300:
                success = True
            else:
                logger.warning("Échec notification Telegram : HTTP %s", resp.status_code)
        except Exception as exc:
            logger.warning("Exception lors de l'envoi Telegram : %s", exc)

    return success


def archive_expired_offers(
    db: Database,
    config: dict[str, Any] | None = None,
    max_checks: int | None = None,
    checker: Any = None,
) -> int:
    """Archive les offres « NOUVEAU » dont la page prouve qu'elles sont fermées.

    Détection par ``scrapers.liveness`` (HTTP 404/410, redirection vers la liste
    des offres, texte « Les candidatures ne sont plus acceptées », « Ce job n'est
    plus disponible »…). Seul un verdict **fermé** archive : erreur réseau, 429,
    page anti-robot ou connexion requise ⇒ rien n'est conclu, l'offre reste.

    Une offre archivée passe en IGNORÉ avec ``rejection_reason = "offre_fermee"``
    et la note « [Auto] Fermée : <motif> ». Seules les offres encore NOUVEAU au
    moment de l'écriture sont touchées : un statut saisi par l'utilisateur n'est
    jamais écrasé.

    Réglages (``automations``) : ``check_expired_offers_enabled``,
    ``expired_offers_age_days`` (âge minimal avant contrôle),
    ``expired_offers_max_checks`` (sondes réseau par run ; les verdicts
    « ouvert » récents sont servis par le cache et ne comptent pas),
    ``expired_offers_recheck_days`` (validité du cache « ouvert »).

    Returns:
        Nombre d'offres archivées comme fermées.
    """
    cfg = config or load_config()
    auto_cfg = cfg.get("automations") or {}

    if not auto_cfg.get("check_expired_offers_enabled", False):
        return 0

    age_days = int(auto_cfg.get("expired_offers_age_days", 7))
    budget = int(max_checks if max_checks is not None else auto_cfg.get("expired_offers_max_checks", 40))

    try:
        jobs = db.get_jobs(statuses=STATUS_NEW)
    except TypeError:
        jobs = db.get_jobs(status=STATUS_NEW)

    candidates: list[tuple[datetime, dict[str, Any]]] = []
    for job in jobs:
        dt = _parse_datetime(job.get("created_at")) or _parse_datetime(job.get("published_at"))
        if dt and _is_older_than(dt, age_days):
            candidates.append((dt.replace(tzinfo=None), job))
    # Les plus anciennes d'abord : ce sont les plus susceptibles d'être fermées.
    candidates.sort(key=lambda item: item[0])

    own_checker = checker is None
    if own_checker:
        from scrapers.cache import DiskCache
        from scrapers.liveness import LivenessChecker

        checker = LivenessChecker(
            cache=DiskCache(),
            recheck_days=float(auto_cfg.get("expired_offers_recheck_days", 3)),
        )

    archived_count = 0
    unknown = 0
    try:
        for _, job in candidates:
            if checker.network_checks >= budget:
                break
            url = (job.get("url") or "").strip()
            verdict = checker.check(url, job.get("source"))
            if verdict.status == "inconnu":
                unknown += 1
            if not verdict.is_closed:
                continue
            current = db.get_job(job["id"]) or {}
            if current.get("status") != STATUS_NEW:
                continue  # statut changé entre-temps par l'utilisateur : on n'y touche pas
            db.update_status(job["id"], STATUS_IGNORED, rejection_reason=CLOSED_OFFER_REASON)
            existing_notes = (current.get("user_notes") or "").strip()
            auto_note = f"[Auto] Fermée : {verdict.reason}"
            new_notes = f"{existing_notes}\n{auto_note}".strip() if existing_notes else auto_note
            db.update_user_notes(job["id"], new_notes)
            archived_count += 1
            logger.info("Offre fermée archivée : %s (%s) — %s", job.get("id"), url, verdict.reason)
    finally:
        if own_checker:
            checker.close()

    logger.info(
        "Détection d'offres fermées : %d archivée(s), %d non concluante(s), %d sonde(s) réseau.",
        archived_count,
        unknown,
        checker.network_checks,
    )
    return archived_count


def archive_old_offers(
    db: Database,
    config: dict[str, Any] | None = None,
) -> int:
    """Passe en IGNORÉ (archivé) les offres 'NOUVEAU' plus vieilles que ``auto_archive_age_days``.

    DÉSACTIVÉ par défaut (``auto_archive_enabled: false``) depuis le 04/11/2026 :
    l'âge archivait des offres encore ouvertes (981 offres, dont 92 notées ≥ 80).
    La détection d'offre fermée (``archive_expired_offers``) la remplace.

    L'âge se mesure à la date de publication si elle est connue, sinon à la date
    de collecte. Les offres POSTULÉ, ENTRETIEN, IGNORÉ, REJETÉ et EXCLU ne sont
    jamais touchées. Aucun appel réseau.

    Returns:
        Nombre d'offres archivées.
    """
    cfg = config or load_config()
    auto_cfg = cfg.get("automations") or {}

    if not auto_cfg.get("auto_archive_enabled", False):
        return 0

    age_days = int(auto_cfg.get("auto_archive_age_days", 21))

    try:
        jobs = db.get_jobs(statuses=STATUS_NEW)
    except TypeError:
        jobs = db.get_jobs(status=STATUS_NEW)

    archived_count = 0
    for job in jobs:
        dt = _parse_datetime(job.get("published_at")) or _parse_datetime(job.get("created_at"))
        if not dt or not _is_older_than(dt, age_days):
            continue
        db.update_status(job["id"], STATUS_IGNORED, rejection_reason="auto_archive")
        existing_notes = (job.get("user_notes") or "").strip()
        auto_note = f"[Auto] Archivée : offre de plus de {age_days} jours"
        new_notes = f"{existing_notes}\n{auto_note}".strip() if existing_notes else auto_note
        db.update_user_notes(job["id"], new_notes)
        archived_count += 1

    return archived_count


def pregenerate_top_cover_letters(
    db: Database,
    config: dict[str, Any] | None = None,
) -> int:
    """Pré-génère et enregistre la lettre de motivation des meilleures nouvelles offres.

    Args:
        db: Instance de connexion à la base de données.
        config: Configuration globale (charge config.yaml si None).

    Returns:
        Nombre de lettres générées et sauvegardées en base.
    """
    cfg = config or load_config()
    auto_cfg = cfg.get("automations") or {}

    if not auto_cfg.get("pregenerate_letters_enabled", False):
        return 0

    top_n = int(auto_cfg.get("pregenerate_letters_top_n", 3))
    min_score = float(auto_cfg.get("pregenerate_letters_min_score", 85))

    try:
        jobs = db.get_jobs(statuses=STATUS_NEW)
    except TypeError:
        jobs = db.get_jobs(status=STATUS_NEW)

    candidates: list[tuple[float, dict[str, Any]]] = []
    for j in jobs:
        if (j.get("cover_letter") or "").strip():
            continue
        score = _effective_score(j)
        if score >= min_score:
            candidates.append((score, j))

    candidates.sort(key=lambda item: item[0], reverse=True)
    selected = [j for _, j in candidates[:top_n]]

    if not selected:
        return 0

    gen = CoverLetterGenerator(config=cfg)
    generated_count = 0

    for job in selected:
        try:
            try:
                letter = gen.generate(job, allow_fallback=False)
            except TypeError:
                letter = gen.generate(job)
            if letter and not str(letter).strip().startswith("⚠️"):
                db.update_cover_letter(job["id"], letter)
                generated_count += 1
        except Exception as exc:
            logger.warning("Erreur génération lettre pour l'offre %s : %s", job.get("id"), exc)

    return generated_count
