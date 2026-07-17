"""SQLite connection management and upsert helpers.

Every write here is INSERT ... ON CONFLICT DO UPDATE against the natural
keys in schema.sql, so a job can be re-run any number of times and the
resulting data is identical (idempotent), aside from standings_snapshots
which intentionally accumulates one row per (snapshot_date, team_key).
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from app.config import DB_PATH, ensure_data_dir

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def get_connection() -> sqlite3.Connection:
    ensure_data_dir()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    with open(SCHEMA_PATH, "r") as f:
        conn.executescript(f.read())
    conn.commit()


def _upsert(conn: sqlite3.Connection, table: str, row: dict[str, Any], conflict_cols: list[str]) -> None:
    columns = list(row.keys())
    placeholders = ", ".join(f":{c}" for c in columns)
    col_list = ", ".join(columns)
    update_cols = [c for c in columns if c not in conflict_cols]
    if update_cols:
        update_clause = ", ".join(f"{c} = excluded.{c}" for c in update_cols)
        sql = (
            f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
            f"ON CONFLICT ({', '.join(conflict_cols)}) DO UPDATE SET {update_clause}"
        )
    else:
        sql = (
            f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
            f"ON CONFLICT ({', '.join(conflict_cols)}) DO NOTHING"
        )
    conn.execute(sql, row)


def upsert_season(conn: sqlite3.Connection, season: dict[str, Any]) -> None:
    _upsert(conn, "seasons", season, ["season_year"])


def upsert_stat_categories(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> None:
    for row in rows:
        _upsert(conn, "stat_categories", row, ["season_year", "stat_id"])


def upsert_teams(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> None:
    for row in rows:
        _upsert(conn, "teams", row, ["team_key"])


def upsert_matchup(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    _upsert(conn, "matchups", row, ["matchup_id"])


def upsert_matchup_team_stats(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> None:
    for row in rows:
        _upsert(conn, "matchup_team_stats", row, ["matchup_id", "team_key", "stat_id"])


def insert_standings_snapshot(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> None:
    for row in rows:
        _upsert(conn, "standings_snapshots", row, ["snapshot_date", "team_key"])


def upsert_team_season_stats(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> None:
    for row in rows:
        _upsert(conn, "team_season_stats", row, ["season_year", "team_key", "stat_id"])


def upsert_transaction(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    _upsert(conn, "transactions", row, ["transaction_key"])


def upsert_transaction_players(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> None:
    for row in rows:
        _upsert(conn, "transaction_players", row, ["transaction_key", "player_key", "movement"])


def upsert_final_standings(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> None:
    for row in rows:
        _upsert(conn, "final_standings", row, ["season_year", "team_key"])


def save_raw_response(
    conn: sqlite3.Connection,
    endpoint: str,
    params: str,
    body: dict[str, Any],
    season_year: int | None = None,
    week: int | None = None,
) -> None:
    _upsert(
        conn,
        "raw_responses",
        {
            "endpoint": endpoint,
            "params": params,
            "season_year": season_year,
            "week": week,
            "fetched_at": dt.datetime.utcnow().isoformat(),
            "body_json": json.dumps(body),
        },
        ["endpoint", "params"],
    )


def stored_postevent_weeks(conn: sqlite3.Connection, season_year: int) -> set[int]:
    """Weeks whose matchups are all already marked postevent (final, never refetched)."""
    rows = conn.execute(
        "SELECT week FROM matchups WHERE season_year = ? "
        "GROUP BY week HAVING MIN(status) = 'postevent' AND MAX(status) = 'postevent'",
        (season_year,),
    ).fetchall()
    return {r["week"] for r in rows}


def start_fetch_log(conn: sqlite3.Connection, kind: str) -> int:
    cur = conn.execute(
        "INSERT INTO fetch_log (run_started_at, kind, status) VALUES (?, ?, 'error')",
        (dt.datetime.utcnow().isoformat(), kind),
    )
    conn.commit()
    return cur.lastrowid


def finish_fetch_log(conn: sqlite3.Connection, log_id: int, status: str, detail: str = "") -> None:
    conn.execute(
        "UPDATE fetch_log SET run_finished_at = ?, status = ?, detail = ? WHERE id = ?",
        (dt.datetime.utcnow().isoformat(), status, detail, log_id),
    )
    conn.commit()


def last_successful_fetch(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM fetch_log WHERE status = 'ok' ORDER BY run_finished_at DESC LIMIT 1"
    ).fetchone()
