"""Santé des sources : détecte une source qui « répond » mais ne renvoie plus rien.

Un changement de HTML, un captcha ou un blocage silencieux donne un run qui se
termine normalement avec 0 carte : sans alerte, on ne s'en aperçoit que des jours
plus tard. On compare le total de cartes vues par source au **médian des derniers
runs** : une chute franche est signalée.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .models import PassReport, is_incomplete_stop

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
