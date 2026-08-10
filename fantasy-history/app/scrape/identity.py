"""Bridges scraped identifiers with the existing team_key/stat_id
identity space.

The `teams`/`stat_categories` tables already have real Yahoo identifiers
for the current season (2026), populated back when the API worked:
team_key like "469.l.74647.t.9", and numeric stat_ids like R=7, HR=12,
... For that same season_year, scraping should REUSE those exact ids so
the switch from API to scraping is invisible in the data (a chart
spanning the cutover date shouldn't see a fake identity change). For any
other season_year -- every prior/historical season, which the API era
never reached -- there's nothing to reuse, so a fresh, internally-
consistent identity is synthesized and persisted on first sight.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any

from app.db import database

# (display_name, position_type) pairs where a LOWER raw value is the
# better one, matching the sort_order convention already in schema.sql
# (1 = higher is better/descending rank, 0 = lower is better). Batting K
# (strikeouts as a hitter) counts against a team the same way ERA/WHIP do
# for pitching.
_LOWER_IS_BETTER = {("ERA", "P"), ("WHIP", "P"), ("K", "B")}

# Matches the "_2", "_3", ... suffix app.scrape.parse.parse_standings_tables
# adds to disambiguate a second column literally named e.g. "K".
_TRAILING_DUP_SUFFIX_RE = re.compile(r"_\d+$")


def normalize_stat_column(col_key: str) -> tuple[str, bool]:
    """"K_2" -> ("K", False). "GP *" (Yahoo's own display-only marker) ->
    ("GP", True). Kept here (in addition to being re-exported from
    app.scrape.parse) since it's identity-resolution logic, not parsing --
    it decides what `display_name` means to resolve_stat_id below."""
    name = _TRAILING_DUP_SUFFIX_RE.sub("", col_key).strip()
    is_display_only = name.endswith("*")
    if is_display_only:
        name = name[:-1].strip()
    return name, is_display_only


def resolve_team_key(
    conn: sqlite3.Connection,
    season_year: int,
    league_id: str,
    team_id: str,
    *,
    name: str | None = None,
) -> str:
    """Look up `teams` for an existing (season_year, team_id) row and
    reuse its team_key if found (bridges continuity with any already-
    collected API-era history for that season); otherwise synthesize
    `f"{league_id}.t.{team_id}"` and upsert a fresh teams row (true for
    every season the API era never reached), optionally seeding `name`
    on that fresh row.
    """
    row = conn.execute(
        "SELECT team_key FROM teams WHERE season_year = ? AND team_id = ?",
        (season_year, str(team_id)),
    ).fetchone()
    if row is not None:
        return row["team_key"]

    team_key = f"{league_id}.t.{team_id}"
    database.upsert_teams(
        conn,
        [
            {
                "season_year": season_year,
                "team_key": team_key,
                "team_id": str(team_id),
                "name": name,
                "logo_url": None,
                "manager_nickname": None,
                "manager_guid": None,
                "manager_key": None,
                "division_id": None,
                "faab_balance": None,
                "waiver_priority": None,
                "number_of_moves": None,
                "number_of_trades": None,
            }
        ],
    )
    return team_key


def resolve_stat_id(
    conn: sqlite3.Connection,
    season_year: int,
    display_name: str,
    position_type: str | None,
    *,
    is_display_only: int = 0,
    display_order: int | None = None,
) -> int:
    """Look up `stat_categories` for an existing (season_year,
    display_name, position_type) row and reuse its stat_id if found
    (bridges with the current season's already-populated real Yahoo
    stat_ids, e.g. R=7, HR=12, ERA=26, ...); otherwise synthesize a new id
    (9000 + an incrementing counter scoped to this season_year) and
    upsert a fresh stat_categories row using `is_display_only` and
    `display_order` if the caller has them (column position on the
    scraped page), defaulting `display_order` to "next available slot"
    and `sort_order` from the `_LOWER_IS_BETTER` convention above.
    """
    row = conn.execute(
        "SELECT stat_id FROM stat_categories WHERE season_year = ? AND display_name = ? "
        "AND COALESCE(position_type, '') = COALESCE(?, '')",
        (season_year, display_name, position_type),
    ).fetchone()
    if row is not None:
        return row["stat_id"]

    max_row = conn.execute(
        "SELECT MAX(stat_id) AS m FROM stat_categories WHERE season_year = ? AND stat_id >= 9000",
        (season_year,),
    ).fetchone()
    next_id = (max_row["m"] + 1) if max_row and max_row["m"] is not None else 9000

    if display_order is None:
        count_row = conn.execute(
            "SELECT COUNT(*) AS c FROM stat_categories WHERE season_year = ?", (season_year,)
        ).fetchone()
        display_order = count_row["c"] if count_row else 0

    sort_order = 0 if (display_name, position_type) in _LOWER_IS_BETTER else 1

    database.upsert_stat_categories(
        conn,
        [
            {
                "season_year": season_year,
                "stat_id": next_id,
                "name": display_name,
                "display_name": display_name,
                "sort_order": sort_order,
                "display_order": display_order,
                "is_display_only": is_display_only,
                "position_type": position_type,
            }
        ],
    )
    return next_id


def synthesize_transaction_key(
    season_year: int, dest_team_id: Any, timestamp_text: str | None, player_yahoo_ids: list[str | None]
) -> str:
    """Scraped transactions have no unique id exposed anywhere on the
    page, so build a deterministic one from (season_year, destination
    team_id, timestamp text, sorted player ids) -- collisions should be
    vanishingly rare for a personal league (would require the exact same
    team executing two transactions moving the exact same set of players
    within the same displayed minute)."""
    ids = sorted(str(p) for p in player_yahoo_ids if p is not None)
    return f"scrape:{season_year}:{dest_team_id}:{timestamp_text}:{','.join(ids)}"
