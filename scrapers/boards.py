"""Socle commun des sources « multi-boards » (ATS, Workday, labos publics).

Ces sources n'ont pas de moteur de recherche : chaque board (un slug Lever, un
tenant Workday, un flux Inria…) renvoie son vivier complet. Exigence d'Eddy :
**un board en panne ne bloque rien, mais on le sait et on sait pourquoi.**

Mise en œuvre :

* chaque board est une « requête » du moteur commun (``BaseScraper.collect``) :
  il a donc sa propre ligne de télémétrie (``scrape_query_stats``) et son propre
  motif d'arrêt, et une erreur sur un board n'interrompt que ce board ;
* l'état du board est classé (``ok``, ``vide``, ``http_404``, ``timeout``,
  ``parse_error``…) puis recopié dans ``PassReport.board_status`` et en tête de
  ``stop_detail`` (« [http_404] … »), seule colonne persistée — c'est elle que
  lisent ``scrapers.health`` et l'UI ;
* les erreurs réseau/HTTP/JSON remontent jusqu'au moteur, qui les traduit en
  motif d'arrêt incomplet (``http_error``, ``network_error``, ``error``) : le run
  est marqué partiel et l'alerte « flux potentiellement perdu » est émise, sans
  toucher aux autres boards ni aux autres sources.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from .base import BaseScraper
from .known import KnownIndex
from .models import (  # noqa: F401 — codes d'état ré-exportés pour les sources
    BOARD_AUTH,
    BOARD_EMPTY,
    BOARD_ERROR,
    BOARD_FAILURE_PREFIXES,
    BOARD_NETWORK,
    BOARD_OK,
    BOARD_PARSE,
    BOARD_SKIPPED,
    BOARD_TIMEOUT,
    PASS_FRESHNESS,
    PASS_RELEVANCE,
    PageResult,
    PassPlan,
    PassReport,
    ScrapeResult,
    is_board_failure,
)


class BoardParseError(ValueError):
    """Réponse reçue mais illisible (JSON invalide, structure inattendue)."""


@dataclass
class BoardState:
    """État observé d'un board pendant le run."""

    status: str
    detail: str = ""


def classify_board_exception(exc: BaseException) -> BoardState:
    """Traduit une exception de collecte en état de board lisible."""
    if isinstance(exc, httpx.HTTPStatusError):
        code = getattr(exc.response, "status_code", 0) or 0
        return BoardState(f"http_{code}", f"HTTP {code}")
    if isinstance(exc, httpx.TimeoutException):
        return BoardState(BOARD_TIMEOUT, f"délai dépassé ({type(exc).__name__})")
    if isinstance(exc, httpx.RequestError):
        return BoardState(BOARD_NETWORK, f"erreur réseau ({type(exc).__name__})")
    if isinstance(exc, (BoardParseError, json.JSONDecodeError)):
        return BoardState(BOARD_PARSE, f"réponse illisible : {exc}"[:200])
    return BoardState(BOARD_ERROR, f"{type(exc).__name__}: {exc}"[:200])


def response_json(response: Any) -> Any:
    """Corps JSON d'une réponse, ou ``BoardParseError`` explicite."""
    try:
        return response.json()
    except (ValueError, TypeError) as exc:
        raise BoardParseError(f"JSON invalide ({type(exc).__name__})") from exc


class MultiBoardScraper(BaseScraper):
    """Source composée de boards indépendants, un par « requête » du moteur.

    Les sous-classes implémentent :

    * ``board_keys()`` : la liste ordonnée des boards (clé lisible, ex.
      ``lever:mistral``) ;
    * ``_fetch_board_page(board, cursor, plan)`` : une page du board. Elle appelle
      ``self.note_board(board, total)`` dès que le volume brut du board est connu
      (``total == 0`` ⇒ board ``vide``) et laisse remonter toute erreur.
    """

    #: Libellé de la passe non exécutée (vivier complet extrait en une passe).
    SINGLE_PASS_NOTE = "vivier complet extrait en une seule passe (pas de tri ni de recherche)"
    #: Nouvelles offres retenues au plus par board et par run.
    BOARD_QUOTA = 100
    #: Plafond de pages par board (une page = un appel, ou un lot d'appels Workday).
    BOARD_MAX_PAGES = 20

    def __init__(self, config: Any = None) -> None:
        super().__init__(config)
        self._board_states: dict[str, BoardState] = {}

    # ------------------------------------------------------------------ #
    # À implémenter
    # ------------------------------------------------------------------ #
    def board_keys(self) -> list[str]:  # pragma: no cover - abstrait
        raise NotImplementedError

    def _fetch_board_page(self, board: str, cursor: Any, plan: PassPlan) -> PageResult:  # pragma: no cover
        raise NotImplementedError

    # ------------------------------------------------------------------ #
    # État des boards
    # ------------------------------------------------------------------ #
    def note_board(self, board: str, total: int | None, detail: str = "") -> None:
        """Consigne le volume brut d'un board (avant filtre France / stage)."""
        if total is not None and total <= 0:
            self._board_states[board] = BoardState(
                BOARD_EMPTY, detail or "le board répond mais ne publie aucune offre (slug à vérifier ?)"
            )
            return
        current = self._board_states.get(board)
        if current is None or current.status == BOARD_OK:
            self._board_states[board] = BoardState(
                BOARD_OK, detail or (f"{total} offre(s) publiée(s) sur le board" if total is not None else "")
            )

    @property
    def board_states(self) -> dict[str, BoardState]:
        return dict(self._board_states)

    def _iter_pages(self, query: str, mode: str, cursor: Any, plan: PassPlan) -> PageResult:
        try:
            page = self._fetch_board_page(query, cursor, plan)
        except Exception as exc:  # noqa: BLE001 — consigné puis relancé vers le moteur
            state = classify_board_exception(exc)
            self._board_states[query] = state
            self._logger.warning(
                "%s : board %s en échec (%s : %s) — les autres boards continuent.",
                self.source,
                query,
                state.status,
                state.detail,
            )
            if isinstance(exc, (BoardParseError, json.JSONDecodeError)):
                # Le moteur classerait l'erreur en « error » générique : message explicite.
                raise BoardParseError(state.detail) from exc
            raise
        if query not in self._board_states:
            self.note_board(query, None)
        return page

    # ------------------------------------------------------------------ #
    # Orchestration : une passe, un board par requête
    # ------------------------------------------------------------------ #
    def _single_mode(self, selected_modes: Sequence[str]) -> str:
        """Mode exécuté : « Rattrapage » si demandé (aucune fenêtre temporelle).

        Un board ATS ne trie pas par date : une offre de stage publiée il y a deux
        mois et toujours ouverte doit être collectée — la fenêtre de 14 jours de la
        passe « Fraîcheur » l'écarterait à tort.
        """
        if PASS_RELEVANCE in selected_modes:
            return PASS_RELEVANCE
        return selected_modes[0] if selected_modes else PASS_FRESHNESS

    def collect(
        self,
        known_index: KnownIndex | None = None,
        *,
        validate_jobs: bool = False,
        modes: Sequence[str] | None = None,
        queries: Sequence[str] | None = None,
        on_batch_collected: Any = None,
    ) -> ScrapeResult:
        selected_modes = list(modes) if modes else self.config.enabled_modes()
        mode = self._single_mode(selected_modes)
        boards = list(queries) if queries else self.board_keys()
        if not boards:
            now = datetime.now(timezone.utc)
            result = ScrapeResult()
            result.query_reports.append(
                PassReport(
                    source=self.source,
                    query="(aucun board)",
                    mode=mode,
                    started_at=now,
                    finished_at=now,
                    duration_seconds=0.0,
                    stop_reason="disabled",
                    stop_detail=f"[{BOARD_SKIPPED}] aucun board configuré",
                    board_status=BOARD_SKIPPED,
                )
            )
            self._logger.warning("%s : aucun board configuré — source ignorée.", self.source)
            return result

        # Un board n'est pas une requête de recherche : ni objectif de source (il
        # affamerait les derniers boards de la liste), ni fenêtre temporelle. Seuls
        # le quota par board et le plafond global de la source s'appliquent.
        original_config = self.config
        board_pass = original_config.pass_config(mode).model_copy(
            update={
                "target_new_per_source": None,
                "window_days": None,
                "max_offers_per_query": self.BOARD_QUOTA,
                "max_pages_per_query": max(
                    self.BOARD_MAX_PAGES, original_config.pass_config(mode).max_pages_per_query
                ),
            }
        )
        # Plafond de source relevé au quota cumulé des boards : sinon les premiers
        # boards de la liste (au premier run, tout est inédit) consomment le
        # plafond et les derniers ne sont jamais interrogés (« non_lance »).
        self.config = original_config.model_copy(
            update={
                "passes": {**original_config.passes, mode: board_pass},
                "max_offers_per_source": max(
                    original_config.max_offers_per_source, self.BOARD_QUOTA * len(boards)
                ),
            }
        )
        try:
            result = super().collect(
                known_index=known_index,
                validate_jobs=validate_jobs,
                modes=[mode],
                queries=boards,
                on_batch_collected=on_batch_collected,
            )
        finally:
            self.config = original_config
        self._annotate_reports(result.query_reports)

        for other in selected_modes:
            if other == mode:
                continue
            now = datetime.now(timezone.utc)
            result.query_reports.append(
                PassReport(
                    source=self.source,
                    query="(tous les boards)",
                    mode=other,
                    started_at=now,
                    finished_at=now,
                    duration_seconds=0.0,
                    stop_reason="unsupported",
                    stop_detail=self.SINGLE_PASS_NOTE,
                )
            )
        self._log_board_summary(result.query_reports)
        return result

    def _annotate_reports(self, reports: Sequence[PassReport]) -> None:
        """Recopie l'état de chaque board dans sa ligne de télémétrie."""
        for report in reports:
            state = self._board_states.get(report.query)
            if state is None:
                state = BoardState(BOARD_SKIPPED, "board non interrogé (objectif ou plafond de la source atteint)")
            report.board_status = state.status
            prefix = f"[{state.status}]"
            detail = " ".join(part for part in (prefix, state.detail) if part)
            if report.stop_detail:
                detail = f"{detail} ; {report.stop_detail}"
            report.stop_detail = detail[:300]

    def _log_board_summary(self, reports: Sequence[PassReport]) -> None:
        by_status: dict[str, list[str]] = {}
        for report in reports:
            if report.board_status:
                by_status.setdefault(report.board_status, []).append(report.query)
        if not by_status:
            return
        summary = " | ".join(f"{status} : {len(names)}" for status, names in sorted(by_status.items()))
        self._logger.info("%s : état des boards — %s", self.source, summary)
        empty = by_status.get(BOARD_EMPTY, [])
        if empty:
            self._logger.warning(
                "%s : board(s) vide(s), slug probablement obsolète : %s", self.source, ", ".join(empty)
            )
        failed = [name for status, names in by_status.items() if is_board_failure(status) for name in names]
        if failed:
            self._logger.warning(
                "%s : board(s) en panne (le reste de la collecte continue) : %s",
                self.source,
                ", ".join(f"{name} ({self._board_states[name].status})" for name in failed if name in self._board_states),
            )
