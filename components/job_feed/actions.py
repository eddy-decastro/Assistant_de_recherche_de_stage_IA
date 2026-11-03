"""Validation et application des actions émises par le composant ``job_feed``."""
from __future__ import annotations

import logging
from typing import Any, Callable

from src.constants import (
    STATUS_APPLIED,
    STATUS_IGNORED,
    STATUS_INTERVIEW,
    STATUS_NEW,
    STATUS_REJECTED,
)
from utils.data import _set_status, bump_data_version

logger = logging.getLogger(__name__)

ALLOWED_STATUSES = frozenset({STATUS_NEW, STATUS_APPLIED, STATUS_INTERVIEW, STATUS_IGNORED})
BULK_ALLOWED_STATUSES = frozenset(
    {STATUS_NEW, STATUS_APPLIED, STATUS_INTERVIEW, STATUS_IGNORED, STATUS_REJECTED}
)


def parse_action(raw: Any) -> dict[str, Any] | None:
    """Valide la charge d'un trigger ; ``None`` si elle est absente ou forgée."""
    if not isinstance(raw, dict):
        return None
    kind = raw.get("type")
    if kind in ("bulk_status", "claude_pack"):
        ids_raw = raw.get("ids")
        if not isinstance(ids_raw, list) or not ids_raw:
            return None
        ids = [str(item).strip() for item in ids_raw if isinstance(item, str) and item.strip()]
        if not ids:
            return None
        if kind == "claude_pack":
            return {"type": "claude_pack", "ids": ids}
        status = raw.get("status")
        if isinstance(status, str) and status in BULK_ALLOWED_STATUSES:
            return {"type": "bulk_status", "ids": ids, "status": status}
        return None

    job_id = raw.get("id")
    if not isinstance(job_id, str) or not job_id:
        return None
    if kind == "letter":
        return {"type": "letter", "id": job_id}
    status = raw.get("status")
    if kind == "status" and isinstance(status, str) and status in ALLOWED_STATUSES:
        return {"type": "status", "id": job_id, "status": status}
    return None


def apply_status_action(
    db: Any, raw: Any, set_status: Callable[[Any, str, str], None] | None = None
) -> str:
    """Persiste un changement de statut valide (individuel ou groupé).

    Retourne ``"applied"`` (écrit), ``"ignored"`` (rien à faire ou charge invalide)
    ou ``"failed"`` (l'écriture a levé une exception).
    ``set_status`` est résolu à l'appel (et non à la définition) pour rester substituable.
    """
    set_status = set_status or _set_status
    action = parse_action(raw)
    if action is None or action["type"] not in ("status", "bulk_status"):
        return "ignored"
    try:
        if action["type"] == "status":
            set_status(db, action["id"], action["status"])
        else:
            if hasattr(db, "bulk_update_status"):
                db.bulk_update_status(action["ids"], action["status"])
                bump_data_version()
            else:
                for jid in action["ids"]:
                    set_status(db, jid, action["status"])
    except Exception:  # noqa: BLE001 - l'UI signale l'échec, jamais de crash de rerun
        logger.exception("Échec du changement de statut %s", action)
        return "failed"
    return "applied"
