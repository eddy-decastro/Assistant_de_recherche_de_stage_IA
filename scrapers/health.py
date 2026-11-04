"""Santé des sources : détecte une source qui « répond » mais ne renvoie plus rien.

Un changement de HTML, un captcha ou un blocage silencieux donne un run qui se
termine normalement avec 0 carte : sans alerte, on ne s'en aperçoit que des jours
plus tard. On compare le total de cartes vues par source au **médian des derniers
runs** : une chute franche est signalée.
"""
from __future__ import annotations

import re
import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .models import (
    BOARD_EMPTY,
    BOARD_OK,
    BOARD_SKIPPED,
    PassReport,
    board_status_label,
    is_board_failure,
    is_incomplete_stop,
)

#: Sous ce ratio (cartes du run / médian historique), la source est jugée dégradée.
DEFAULT_DROP_RATIO = 0.2
#: Historique minimal (runs) pour que le médian soit fiable.
DEFAULT_MIN_HISTORY = 3
#: Médian minimal : une source structurellement quasi vide ne déclenche pas d'alerte.
DEFAULT_MIN_BASELINE = 10


@dataclass(frozen=True)
class SourceAlert:
    source: str
    cards: int
    baseline: float
    reasons: tuple[str, ...]

    def message(self) -> str:
        why = f" (arrêts : {', '.join(self.reasons)})" if self.reasons else ""
        return (
            f"source {self.source} dégradée : {self.cards} carte(s) vue(s) "
            f"contre ~{self.baseline:g} habituellement{why}"
        )


def detect_degraded_sources(
    reports: Sequence[PassReport],
    history: Mapping[str, Sequence[int]],
    *,
    drop_ratio: float = DEFAULT_DROP_RATIO,
    min_history: int = DEFAULT_MIN_HISTORY,
    min_baseline: float = DEFAULT_MIN_BASELINE,
) -> list[SourceAlert]:
    """Sources dont le volume de cartes du run s'effondre par rapport à l'historique.

    Seules les sources réellement tentées sont évaluées (une passe non lancée,
    source indisponible ou objectif atteint, n'est pas une dégradation).
    """
    cards: dict[str, int] = defaultdict(int)
    reasons: dict[str, set[str]] = defaultdict(set)
    attempted: set[str] = set()
    for report in reports:
        if report.http_requests > 0 or report.cards_seen > 0 or is_incomplete_stop(report.stop_reason):
            attempted.add(report.source)
        cards[report.source] += report.cards_seen
        if is_incomplete_stop(report.stop_reason):
            reasons[report.source].add(report.stop_reason)

    alerts: list[SourceAlert] = []
    for source in sorted(attempted):
        past = list(history.get(source, ()))
        if len(past) < min_history:
            continue
        baseline = statistics.median(past)
        if baseline < min_baseline:
            continue
        if cards[source] < baseline * drop_ratio:
            alerts.append(
                SourceAlert(source, cards[source], baseline, tuple(sorted(reasons[source])))
            )
    return alerts


# --------------------------------------------------------------------------- #
# Santé par board (sources multi-boards : ATS, Workday, labos publics)
# --------------------------------------------------------------------------- #
#: Préfixe d'état recopié en tête de ``stop_detail`` par ``scrapers.boards``.
_BOARD_PREFIX_RE = re.compile(r"^\[([a-z0-9_]+)\]\s*")

#: États agrégés d'une source, du plus sain au plus grave.
SOURCE_OK = "ok"
SOURCE_DEGRADED = "degradee"
SOURCE_DOWN = "en_panne"
SOURCE_INACTIVE = "inactive"


def parse_board_status(stop_detail: str | None) -> tuple[str, str]:
    """``"[http_404] HTTP 404 ; …"`` → ``("http_404", "HTTP 404 ; …")`` (``("", …)`` sinon)."""
    text = str(stop_detail or "")
    match = _BOARD_PREFIX_RE.match(text)
    if not match:
        return "", text
    return match.group(1), text[match.end():]


@dataclass(frozen=True)
class BoardAlert:
    """Board en panne (ou vide) : quoi, où, pourquoi."""

    source: str
    board: str
    status: str
    detail: str

    @property
    def is_failure(self) -> bool:
        return is_board_failure(self.status)

    def message(self) -> str:
        what = "en panne" if self.is_failure else "vide"
        detail = f" — {self.detail}" if self.detail else ""
        return (
            f"source {self.source} dégradée : board {self.board} {what} "
            f"({board_status_label(self.status)}){detail} ; les autres boards et sources continuent"
        )


def _report_board_status(report: PassReport) -> tuple[str, str]:
    status = report.board_status
    parsed, detail = parse_board_status(report.stop_detail)
    return (status or parsed), detail


def detect_board_failures(
    reports: Sequence[PassReport], *, include_empty: bool = True
) -> list[BoardAlert]:
    """Boards en panne (``http_*``, ``timeout``, ``parse_error``…) et, en option, vides.

    Un board vide n'est pas une panne (petite structure sans offre ouverte), mais
    c'est souvent le signe d'un identifiant obsolète : il est signalé à part.
    """
    alerts: list[BoardAlert] = []
    seen: set[tuple[str, str]] = set()
    for report in reports:
        status, detail = _report_board_status(report)
        if not status:
            continue
        if not (is_board_failure(status) or (include_empty and status == BOARD_EMPTY)):
            continue
        key = (report.source, report.query)
        if key in seen:
            continue
        seen.add(key)
        alerts.append(BoardAlert(report.source, report.query, status, detail.split(" ; ")[0][:160]))
    return alerts


def summarize_source_health(rows: Iterable[Mapping[str, Any] | PassReport]) -> list[dict[str, Any]]:
    """Synthèse lisible de la santé des sources, pour l'API et l'UI.

    ``rows`` : passes du **dernier run** (``PassReport`` ou lignes de
    ``scrape_query_stats`` sous forme de dictionnaires, ex.
    ``db.get_recent_query_stats(run_id=…)``). Retour : une entrée par source ::

        {"source": "ats", "status": "degradee", "message": "2 board(s) en panne sur 24",
         "boards": [{"board": "lever:foo", "status": "http_404",
                     "label": "Erreur HTTP 404", "detail": "HTTP 404"}, …],
         "stop_reasons": ["http_error", "stream_end"]}

    États : ``ok`` ; ``degradee`` (au moins un board en panne ou une passe
    interrompue, mais la source a ramené des cartes) ; ``en_panne`` (tous les
    boards/passes tentés ont échoué) ; ``inactive`` (rien n'a été tenté :
    source désactivée, cookies absents…).
    """
    by_source: dict[str, dict[str, Any]] = {}
    for row in rows:
        data = row.model_dump() if isinstance(row, PassReport) else dict(row)
        source = str(data.get("source") or "?")
        entry = by_source.setdefault(
            source,
            {"source": source, "boards": [], "stop_reasons": set(), "attempted": 0, "failed": 0, "cards": 0},
        )
        stop_reason = str(data.get("stop_reason") or "")
        entry["stop_reasons"].add(stop_reason)
        status = str(data.get("board_status") or "") or parse_board_status(data.get("stop_detail"))[0]
        _, detail = parse_board_status(data.get("stop_detail"))
        cards = int(data.get("cards_seen") or 0)
        entry["cards"] += cards
        attempted = int(data.get("http_requests") or 0) > 0 or cards > 0 or is_incomplete_stop(stop_reason)
        if status:
            entry["boards"].append(
                {
                    "board": str(data.get("query") or ""),
                    "status": status,
                    "label": board_status_label(status),
                    "detail": detail.split(" ; ")[0][:160],
                }
            )
            if status != BOARD_SKIPPED:
                attempted = True
        if attempted:
            entry["attempted"] += 1
            if is_incomplete_stop(stop_reason) or is_board_failure(status):
                entry["failed"] += 1

    summary: list[dict[str, Any]] = []
    for source in sorted(by_source):
        entry = by_source[source]
        failed_boards = [b for b in entry["boards"] if is_board_failure(b["status"])]
        empty_boards = [b for b in entry["boards"] if b["status"] == BOARD_EMPTY]
        if not entry["attempted"]:
            status, message = SOURCE_INACTIVE, "aucune passe tentée (source désactivée ou indisponible)"
        elif entry["failed"] >= entry["attempted"]:
            status, message = SOURCE_DOWN, "toutes les passes tentées ont échoué"
        elif entry["failed"]:
            status = SOURCE_DEGRADED
            if failed_boards:
                message = f"{len(failed_boards)} board(s) en panne sur {len(entry['boards'])}"
            else:
                message = f"{entry['failed']} passe(s) interrompue(s) sur {entry['attempted']}"
        else:
            status, message = SOURCE_OK, f"{entry['cards']} carte(s) vue(s)"
        if empty_boards:
            message += f" ; {len(empty_boards)} board(s) vide(s)"
        boards = sorted(
            entry["boards"],
            key=lambda b: (not is_board_failure(b["status"]), b["status"] != BOARD_EMPTY, b["status"] != BOARD_OK, b["board"]),
        )
        summary.append(
            {
                "source": source,
                "status": status,
                "message": message,
                "boards": boards,
                "stop_reasons": sorted(r for r in entry["stop_reasons"] if r),
            }
        )
    return summary
