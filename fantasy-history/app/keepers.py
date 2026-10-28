"""League keeper rules.

A player is "frozen" if he was on the SAME manager's roster on both the
freeze date (Sep 1 -- the active roster that day, including waiver
claims processed overnight) and the last day of the season. A frozen
player's keeper round is his current-season draft round minus
KEEPER_ROUND_OFFSET, no matter who drafted him (a player kept this year
counts at the round he was kept in), capped at LATEST_KEEPER_ROUND --
which is also the keeper round for undrafted players. Drafted in round
KEEPER_ROUND_OFFSET or earlier means not keeper-eligible at all.
"""
from __future__ import annotations

import sqlite3
from typing import Any

KEEPER_ROUND_OFFSET = 6
LATEST_KEEPER_ROUND = 16

NOT_FROZEN = "not_frozen"
NOT_ELIGIBLE = "not_eligible"
ELIGIBLE = "eligible"


def keeper_round(draft_round: int | None) -> int | None:
    """None means not keeper-eligible (drafted too early)."""
    if draft_round is None:
        return LATEST_KEEPER_ROUND
    r = draft_round - KEEPER_ROUND_OFFSET
    if r < 1:
        return None
    return min(r, LATEST_KEEPER_ROUND)


def player_status(frozen: bool, draft_round: int | None) -> dict[str, Any]:
    if not frozen:
        return {"status": NOT_FROZEN, "keeper_round": None, "label": "Not frozen"}
    kr = keeper_round(draft_round)
    if kr is None:
        return {
            "status": NOT_ELIGIBLE,
            "keeper_round": None,
            "label": f"Not keeper eligible (drafted Rd {draft_round})",
        }
    drafted = f"drafted Rd {draft_round}" if draft_round is not None else "undrafted"
    return {"status": ELIGIBLE, "keeper_round": kr, "label": f"Keeper Rd {kr} ({drafted})"}


_STATUS_ORDER = {ELIGIBLE: 0, NOT_ELIGIBLE: 1, NOT_FROZEN: 2}


def build_report(
    conn: sqlite3.Connection, season_year: int, freeze_date: str, end_date: str
) -> list[dict[str, Any]]:
    """One entry per team, sorted by team name: {"team", "manager",
    "players"}, where players covers every player on that team's
    `end_date` roster, sorted keepers-first by keeper round. Each player
    also carries `freeze_team`: who had him on `freeze_date` (None = no
    one, i.e. a free agent)."""
    teams = {
        r["team_key"]: {"team": r["name"] or r["team_key"], "manager": r["manager_nickname"]}
        for r in conn.execute(
            "SELECT team_key, name, manager_nickname FROM teams WHERE season_year = ?", (season_year,)
        )
    }
    freeze_owner = {
        r["player_key"]: r["team_key"]
        for r in conn.execute(
            "SELECT team_key, player_key FROM roster_snapshots WHERE season_year = ? AND snapshot_date = ?",
            (season_year, freeze_date),
        )
    }
    draft_round = {
        r["player_key"]: r["round"]
        for r in conn.execute(
            "SELECT player_key, round FROM draft_picks WHERE season_year = ? AND player_key IS NOT NULL",
            (season_year,),
        )
    }
    rows = conn.execute(
        "SELECT team_key, player_key, player_name FROM roster_snapshots WHERE season_year = ? AND snapshot_date = ?",
        (season_year, end_date),
    ).fetchall()

    by_team: dict[str, dict[str, Any]] = {}
    for r in rows:
        meta = teams.get(r["team_key"], {"team": r["team_key"], "manager": None})
        entry = by_team.setdefault(r["team_key"], {**meta, "players": []})
        owner = freeze_owner.get(r["player_key"])
        dr = draft_round.get(r["player_key"])
        entry["players"].append(
            {
                "player_name": r["player_name"],
                "draft_round": dr,
                "freeze_team": teams[owner]["team"] if owner in teams else owner,
                **player_status(owner == r["team_key"], dr),
            }
        )
    for entry in by_team.values():
        entry["players"].sort(
            key=lambda p: (_STATUS_ORDER[p["status"]], p["keeper_round"] or 0, p["player_name"] or "")
        )
    return sorted(by_team.values(), key=lambda e: e["team"])
