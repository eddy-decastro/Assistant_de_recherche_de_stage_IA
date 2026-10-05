"""Fusion à trois points de deux copies de la base SQLite (synchronisation R2).

L'app Streamlit et le pipeline (cron) travaillent chacun sur une copie locale de
la même base, puis la téléversent entière sur le bucket. Sans fusion, le dernier
téléversement écrase les écritures de l'autre : nouvelles offres du cron perdues,
ou statuts de candidature modifiés pendant un run perdus.

``merge_remote_changes`` rejoue dans la copie locale les changements faits côté
distant depuis la dernière synchronisation commune (la « base »), ligne par ligne
et colonne par colonne, pour toutes les tables munies d'une clé primaire.
"""
from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("src.storage.db_merge")

# Colonnes écrites par l'utilisateur depuis l'app (kanban, candidatures). En cas de
# conflit, le pipeline doit laisser gagner la version distante (celle de l'app).
USER_COLUMNS: dict[str, tuple[str, ...]] = {
    "jobs": ("status", "rejection_reason", "applied_at"),
}


@dataclass
class MergeStats:
    inserted: int = 0
    updated: int = 0
    deleted: int = 0
    conflicts: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.inserted or self.updated or self.deleted)


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return [row[0] for row in rows]


def _columns(conn: sqlite3.Connection, table: str) -> tuple[list[str], list[str]]:
    """Retourne (colonnes, colonnes de clé primaire dans l'ordre)."""
    info = conn.execute(f"PRAGMA table_info({_quote(table)})").fetchall()
    columns = [row[1] for row in info]
    pk = [row[1] for row in sorted((r for r in info if r[5] > 0), key=lambda r: r[5])]
    return columns, pk


def _rows(
    conn: sqlite3.Connection, table: str, columns: list[str], pk: list[str]
) -> dict[tuple[Any, ...], dict[str, Any]]:
    cols = ", ".join(_quote(c) for c in columns)
    out: dict[tuple[Any, ...], dict[str, Any]] = {}
    for values in conn.execute(f"SELECT {cols} FROM {_quote(table)}"):
        row = dict(zip(columns, values))
        out[tuple(row[c] for c in pk)] = row
    return out


def merge_remote_changes(
    local_path: Path | str,
    remote_path: Path | str,
    base_path: Path | str | None = None,
    prefer_remote: Mapping[str, Iterable[str]] | None = None,
) -> MergeStats:
    """Applique dans ``local_path`` les changements distants survenus depuis ``base_path``.

    Règles, par ligne (clé primaire) puis par colonne :
    - ligne ajoutée à distance et absente localement : insérée ;
    - colonne modifiée à distance et inchangée localement : valeur distante reprise ;
    - colonne modifiée des deux côtés : la valeur locale gagne, sauf pour les
      colonnes listées dans ``prefer_remote`` ;
    - ligne supprimée à distance : supprimée localement si elle n'y a pas changé ;
    - ligne supprimée localement : reste supprimée.

    Sans base (première synchronisation), toute ligne distante est traitée comme
    ajoutée : on obtient l'union des deux copies, la version locale l'emportant.
    """
    prefer = {table: set(cols) for table, cols in (prefer_remote or {}).items()}
    stats = MergeStats()
    local = sqlite3.connect(str(local_path), timeout=30)
    remote = sqlite3.connect(f"file:{Path(remote_path).as_posix()}?mode=ro", uri=True)
    base = (
        sqlite3.connect(f"file:{Path(base_path).as_posix()}?mode=ro", uri=True)
        if base_path and Path(base_path).exists()
        else None
    )
    try:
        local_tables = set(_tables(local))
        base_tables = set(_tables(base)) if base is not None else set()
        with local:
            for table in _tables(remote):
                if table not in local_tables:
                    continue
                local_cols, pk = _columns(local, table)
                remote_cols, remote_pk = _columns(remote, table)
                if not pk or pk != remote_pk:
                    continue
                columns = [c for c in local_cols if c in remote_cols]
                if base is not None and table in base_tables:
                    base_cols, _ = _columns(base, table)
                    columns = [c for c in columns if c in base_cols]
                    b_rows = _rows(base, table, columns, pk)
                else:
                    b_rows = {}
                l_rows = _rows(local, table, columns, pk)
                r_rows = _rows(remote, table, columns, pk)
                _merge_table(local, table, columns, pk, l_rows, r_rows, b_rows, prefer.get(table, set()), stats)
    finally:
        local.close()
        remote.close()
        if base is not None:
            base.close()
    return stats


def _merge_table(
    conn: sqlite3.Connection,
    table: str,
    columns: list[str],
    pk: list[str],
    l_rows: dict[tuple[Any, ...], dict[str, Any]],
    r_rows: dict[tuple[Any, ...], dict[str, Any]],
    b_rows: dict[tuple[Any, ...], dict[str, Any]],
    prefer_remote: set[str],
    stats: MergeStats,
) -> None:
    where = " AND ".join(f"{_quote(c)} = ?" for c in pk)
    for key in r_rows.keys() | b_rows.keys():
        r, b, loc = r_rows.get(key), b_rows.get(key), l_rows.get(key)
        if r == b:
            continue  # aucun changement distant
        if r is None:  # supprimée à distance
            if loc is not None and loc == b:
                conn.execute(f"DELETE FROM {_quote(table)} WHERE {where}", key)
                stats.deleted += 1
            continue
        if loc is None:
            if b is None:  # ajoutée à distance
                cols = ", ".join(_quote(c) for c in columns)
                marks = ", ".join("?" for _ in columns)
                try:
                    conn.execute(
                        f"INSERT INTO {_quote(table)} ({cols}) VALUES ({marks})",
                        [r[c] for c in columns],
                    )
                    stats.inserted += 1
                except sqlite3.IntegrityError as exc:
                    logger.warning("Fusion %s : ligne %s ignorée (%s).", table, key, exc)
                    stats.conflicts += 1
            continue  # sinon supprimée localement : elle le reste
        updates: dict[str, Any] = {}
        for col in columns:
            if col in pk or r[col] == loc[col]:
                continue
            remote_changed = b is None or r[col] != b[col]
            local_changed = b is not None and loc[col] != b[col]
            if not remote_changed:
                continue
            if local_changed or b is None:
                stats.conflicts += 1
                if col not in prefer_remote:
                    continue
            updates[col] = r[col]
        if updates:
            sets = ", ".join(f"{_quote(c)} = ?" for c in updates)
            conn.execute(
                f"UPDATE {_quote(table)} SET {sets} WHERE {where}",
                [*updates.values(), *key],
            )
            stats.updated += 1
