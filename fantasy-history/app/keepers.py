"""League keeper rules.

A player is "frozen" if he was on the SAME manager's roster on both the
freeze date (Sep 1) and the last day of the season. A frozen player's
keeper round is his current-season draft round minus KEEPER_ROUND_OFFSET,
no matter who drafted him, capped at LATEST_KEEPER_ROUND (which is also
the keeper round for undrafted players). Drafted in round
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
) -> dict[str, list[dict[str, Any]]]:
    """{team name: [player rows]} covering every player on each team's
    `end_date` roster, sorted keepers-first by keeper round."""
    freeze = {
        (r["team_key"], r["player_key"])
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
        """
        SELECT rs.team_key, COALESCE(t.name, rs.team_key) AS team_name, rs.player_key, rs.player_name
        FROM roster_snapshots rs
        LEFT JOIN teams t ON t.team_key = rs.team_key AND t.season_year = rs.season_year
        WHERE rs.season_year = ? AND rs.snapshot_date = ?
        """,
        (season_year, end_date),
    ).fetchall()

    report: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        frozen = (r["team_key"], r["player_key"]) in freeze
        dr = draft_round.get(r["player_key"])
        report.setdefault(r["team_name"], []).append(
            {"player_name": r["player_name"], "draft_round": dr, **player_status(frozen, dr)}
        )
    for players in report.values():
        players.sort(
            key=lambda p: (_STATUS_ORDER[p["status"]], p["keeper_round"] or 0, p["player_name"] or "")
        )
    return dict(sorted(report.items()))
