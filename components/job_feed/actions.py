"""Validation et application des actions émises par le composant ``job_feed``."""
from __future__ import annotations

import logging
from typing import Any, Callable

from src.constants import STATUS_APPLIED, STATUS_IGNORED, STATUS_INTERVIEW, STATUS_NEW
from utils.data import _set_status

logger = logging.getLogger(__name__)

ALLOWED_STATUSES = frozenset({STATUS_NEW, STATUS_APPLIED, STATUS_INTERVIEW, STATUS_IGNORED})


def parse_action(raw: Any) -> dict[str, str] | None:
    """Valide la charge d'un trigger ; ``None`` si elle est absente ou forgée."""
    if not isinstance(raw, dict):
        return None
    job_id = raw.get("id")
    if not isinstance(job_id, str) or not job_id:
        return None
    kind = raw.get("type")
    if kind == "letter":
        return {"type": "letter", "id": job_id}
    status = raw.get("status")
    if kind == "status" and isinstance(status, str) and status in ALLOWED_STATUSES:
        return {"type": "status", "id": job_id, "status": status}
    return None


def apply_status_action(
    db: Any, raw: Any, set_status: Callable[[Any, str, str], None] | None = None
) -> str:
    """Persiste un changement de statut valide.

    Retourne ``"applied"`` (écrit), ``"ignored"`` (rien à faire ou charge invalide)
    ou ``"failed"`` (l'écriture a levé une exception).
    ``set_status`` est résolu à l'appel (et non à la définition) pour rester substituable.
    """
    set_status = set_status or _set_status
    action = parse_action(raw)
    if action is None or action["type"] != "status":
        return "ignored"
    try:
        set_status(db, action["id"], action["status"])
    except Exception:  # noqa: BLE001 - l'UI signale l'échec, jamais de crash de rerun
        logger.exception("Échec du changement de statut %s -> %s", action["id"], action["status"])
        return "failed"
    return "applied"
