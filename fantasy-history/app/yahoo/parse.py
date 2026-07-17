"""Normalizes Yahoo Fantasy Sports API JSON into plain dicts/lists.

Yahoo's "JSON" is a machine translation of XML: collections are objects
keyed by numeric strings plus a "count" key instead of real arrays, and
many resources (team, player) represent their fields as a list of
single-key dicts to be merged rather than one flat dict. ALL of that
oddity is isolated in this file behind two generic helpers so the rest of
the app only ever deals with plain dicts and lists.

IMPORTANT for whoever runs this against real data first: these parsers are
written from Yahoo's documented/commonly-observed response shape (this
sandbox has no network access to Yahoo to verify against a live league).
Every response is also saved verbatim into the raw_responses table
(app/db/database.py:save_raw_response) specifically so that if a field
here is named or nested differently than expected, it can be fixed here
without needing to re-fetch anything -- just read raw_responses and adjust
the relevant function below. test_parse.py documents the assumed shape.
"""
from __future__ import annotations

from typing import Any


def unwrap_collection(node: Any) -> list[Any]:
    """Yahoo represents collections as {"0": ..., "1": ..., "count": N}
    (occasionally already a plain list). Return the ordered list of items.
    """
    if node is None:
        return []
    if isinstance(node, list):
        return node
    if isinstance(node, dict):
        items = []
        i = 0
        while str(i) in node:
            items.append(node[str(i)])
            i += 1
        return items
    return []


def flatten_field_list(field_list: Any) -> dict[str, Any]:
    """Merge Yahoo's "list of single-key dicts" field representation into
    one flat dict, e.g. [{"team_key": "x"}, {"name": "y"}] -> {"team_key":
    "x", "name": "y"}. Passes through values that are already dicts/lists
    (e.g. "managers", "team_logos") unchanged for the caller to unwrap.
    """
    if isinstance(field_list, dict):
        return field_list
    result: dict[str, Any] = {}
    for entry in field_list or []:
        if isinstance(entry, dict):
            result.update(entry)
    return result


def merge_named_node(node: Any) -> dict[str, Any]:
    """Handle Yahoo's `[fields_list, {"team_stats": {...}}, {"team_standings":
    {...}}]` pattern used for team/player nodes: the first element is a
    fields list to flatten, subsequent elements are named sub-resources
    merged in directly.
    """
    if isinstance(node, dict):
        return node
    if not isinstance(node, list) or not node:
        return {}
    merged = flatten_field_list(node[0])
    for extra in node[1:]:
        if isinstance(extra, dict):
            merged.update(extra)
    return merged


def _scalar(value: Any) -> Any:
    """Yahoo sometimes wraps a scalar as {"": "value"} or returns "" for
    absent numeric fields; pass through anything else untouched."""
    if isinstance(value, dict) and set(value.keys()) <= {""}:
        return value.get("")
    return value


# ---------------------------------------------------------------------
# League / settings
# ---------------------------------------------------------------------

def parse_league_meta(league_fields: dict[str, Any]) -> dict[str, Any]:
    season_year = int(league_fields.get("season") or 0)
    return {
        "season_year": season_year,
        "game_key": str(league_fields.get("game_key") or ""),
        "league_key": league_fields.get("league_key"),
        "league_name": league_fields.get("name"),
        "num_teams": int(league_fields.get("num_teams") or 0) or None,
        "scoring_type": league_fields.get("scoring_type"),
        "start_week": _to_int(league_fields.get("start_week")),
        "end_week": _to_int(league_fields.get("end_week")),
        "start_date": league_fields.get("start_date"),
        "end_date": league_fields.get("end_date"),
        "playoff_start_week": _to_int(league_fields.get("playoff_start_week")),
        "is_finished": 1 if str(league_fields.get("is_finished", "0")) == "1" else 0,
        "settings_json": None,
    }


def _to_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_stat_categories(settings: dict[str, Any], season_year: int) -> list[dict[str, Any]]:
    stats_node = (settings.get("stat_categories") or {}).get("stats")
    rows = []
    for item in unwrap_collection(stats_node):
        stat = item.get("stat", item) if isinstance(item, dict) else item
        rows.append(
            {
                "season_year": season_year,
                "stat_id": int(stat["stat_id"]),
                "name": stat.get("name"),
                "display_name": stat.get("display_name"),
                "sort_order": _to_int(stat.get("sort_order")),
                "is_display_only": 1 if str(stat.get("is_only_display_stat", "0")) == "1" else 0,
                "position_type": stat.get("position_type"),
            }
        )
    return rows


# ---------------------------------------------------------------------
# Teams / standings
# ---------------------------------------------------------------------

def _flatten_team_node(raw_team: Any) -> dict[str, Any]:
    merged = merge_named_node(raw_team)
    managers = unwrap_collection(merged.get("managers"))
    manager_nickname = None
    manager_guid = None
    if managers:
        manager = managers[0].get("manager", managers[0]) if isinstance(managers[0], dict) else {}
        manager_nickname = manager.get("nickname")
        manager_guid = manager.get("guid")
    logo_url = None
    logos = unwrap_collection(merged.get("team_logos"))
    if logos:
        logo = logos[0].get("team_logo", logos[0]) if isinstance(logos[0], dict) else {}
        logo_url = logo.get("url")
    merged["_manager_nickname"] = manager_nickname
    merged["_manager_guid"] = manager_guid
    merged["_logo_url"] = logo_url
    return merged


def parse_teams(teams_node: Any, season_year: int) -> list[dict[str, Any]]:
    rows = []
    for item in unwrap_collection(teams_node):
        raw_team = item.get("team", item) if isinstance(item, dict) else item
        team = _flatten_team_node(raw_team)
        rows.append(
            {
                "season_year": season_year,
                "team_key": team.get("team_key"),
                "team_id": str(team.get("team_id")) if team.get("team_id") is not None else None,
                "name": team.get("name"),
                "logo_url": team.get("_logo_url"),
                "manager_nickname": team.get("_manager_nickname"),
                "manager_guid": team.get("_manager_guid"),
                "division_id": team.get("division_id"),
            }
        )
    return rows


def parse_standings_snapshot(
    teams_node: Any, season_year: int, snapshot_date: str
) -> list[dict[str, Any]]:
    rows = []
    for item in unwrap_collection(teams_node):
        raw_team = item.get("team", item) if isinstance(item, dict) else item
        team = _flatten_team_node(raw_team)
        standings = team.get("team_standings") or {}
        outcome = standings.get("outcome_totals") or {}
        rows.append(
            {
                "snapshot_date": snapshot_date,
                "season_year": season_year,
                "team_key": team.get("team_key"),
                "rank": _to_int(standings.get("rank")),
                "wins": _to_int(outcome.get("wins")),
                "losses": _to_int(outcome.get("losses")),
                "ties": _to_int(outcome.get("ties")),
                "pct": float(outcome.get("percentage")) if outcome.get("percentage") not in (None, "") else None,
                "games_back": standings.get("games_back"),
                "points_for": _to_float(standings.get("points_for")),
                "points_against": _to_float(standings.get("points_against")),
                "playoff_seed": _to_int(standings.get("playoff_seed")),
            }
        )
    return rows


def parse_team_season_stats(teams_node: Any, season_year: int) -> list[dict[str, Any]]:
    rows = []
    for item in unwrap_collection(teams_node):
        raw_team = item.get("team", item) if isinstance(item, dict) else item
        team = _flatten_team_node(raw_team)
        team_key = team.get("team_key")
        stats = (team.get("team_stats") or {}).get("stats")
        for stat_entry in unwrap_collection(stats):
            stat = stat_entry.get("stat", stat_entry) if isinstance(stat_entry, dict) else stat_entry
            rows.append(
                {
                    "season_year": season_year,
                    "team_key": team_key,
                    "stat_id": int(stat["stat_id"]),
                    "value": str(stat.get("value")),
                }
            )
    return rows


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------
# Matchups / scoreboard
# ---------------------------------------------------------------------

def parse_scoreboard(scoreboard: dict[str, Any], season_year: int, week: int) -> tuple[list[dict], list[dict]]:
    matchups_rows: list[dict[str, Any]] = []
    stats_rows: list[dict[str, Any]] = []

    matchups_node = (scoreboard.get("matchups") or {})
    for item in unwrap_collection(matchups_node):
        matchup = item.get("matchup", item) if isinstance(item, dict) else item
        teams = unwrap_collection(matchup.get("teams"))
        if len(teams) < 2:
            continue
        team_nodes = []
        for t in teams:
            raw_team = t.get("team", t) if isinstance(t, dict) else t
            team_nodes.append(_flatten_team_node(raw_team))

        team1_key = team_nodes[0].get("team_key")
        team2_key = team_nodes[1].get("team_key")
        matchup_id = f"{season_year}:{week}:{team1_key}:{team2_key}"

        matchups_rows.append(
            {
                "matchup_id": matchup_id,
                "season_year": season_year,
                "week": week,
                "team1_key": team1_key,
                "team2_key": team2_key,
                "is_playoffs": 1 if str(matchup.get("is_playoffs", "0")) == "1" else 0,
                "is_consolation": 1 if str(matchup.get("is_consolation", "0")) == "1" else 0,
                "status": matchup.get("status"),
                "winner_team_key": matchup.get("winner_team_key"),
                "is_tied": 1 if str(matchup.get("is_tied", "0")) == "1" else 0,
                "week_start_date": matchup.get("week_start"),
                "week_end_date": matchup.get("week_end"),
            }
        )

        stat_winners = {}
        for sw in unwrap_collection(matchup.get("stat_winners")):
            winner = sw.get("stat_winner", sw) if isinstance(sw, dict) else sw
            stat_winners[int(winner["stat_id"])] = {
                "winner_team_key": winner.get("winner_team_key"),
                "is_tied": str(winner.get("is_tied", "0")) == "1",
            }

        for team in team_nodes:
            team_key = team.get("team_key")
            stats = (team.get("team_stats") or {}).get("stats")
            for stat_entry in unwrap_collection(stats):
                stat = stat_entry.get("stat", stat_entry) if isinstance(stat_entry, dict) else stat_entry
                stat_id = int(stat["stat_id"])
                winner_info = stat_winners.get(stat_id, {})
                won = None
                tied = winner_info.get("is_tied", False)
                if winner_info.get("winner_team_key"):
                    won = winner_info["winner_team_key"] == team_key
                stats_rows.append(
                    {
                        "matchup_id": matchup_id,
                        "team_key": team_key,
                        "stat_id": stat_id,
                        "value": str(stat.get("value")),
                        "won_category": None if won is None else int(won),
                        "tied_category": int(tied),
                    }
                )

    return matchups_rows, stats_rows


# ---------------------------------------------------------------------
# User / league discovery (one-time setup only)
# ---------------------------------------------------------------------

def parse_user_leagues(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten `users;use_login=1/games;game_codes=mlb/leagues` into a
    simple list of {league_key, name, season, game_key} for the user to
    pick from during `python -m app auth`."""
    users_node = (body.get("fantasy_content") or {}).get("users")
    leagues: list[dict[str, Any]] = []
    for u in unwrap_collection(users_node):
        user = u.get("user", u) if isinstance(u, dict) else u
        user = merge_named_node(user) if isinstance(user, list) else user
        for g in unwrap_collection(user.get("games")):
            game = g.get("game", g) if isinstance(g, dict) else g
            game = merge_named_node(game) if isinstance(game, list) else game
            for l_item in unwrap_collection(game.get("leagues")):
                league = l_item.get("league", l_item) if isinstance(l_item, dict) else l_item
                league = flatten_field_list(league) if isinstance(league, list) else league
                leagues.append(
                    {
                        "league_key": league.get("league_key"),
                        "name": league.get("name"),
                        "season": _to_int(league.get("season")),
                        "game_key": game.get("game_key"),
                    }
                )
    return leagues


# ---------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------

def parse_transactions(transactions_node: Any, season_year: int) -> tuple[list[dict], list[dict]]:
    tx_rows: list[dict[str, Any]] = []
    player_rows: list[dict[str, Any]] = []

    for item in unwrap_collection(transactions_node):
        tx = item.get("transaction", item) if isinstance(item, dict) else item
        tx = merge_named_node(tx) if isinstance(tx, list) else tx
        tx_key = tx.get("transaction_key")
        tx_rows.append(
            {
                "transaction_key": tx_key,
                "season_year": season_year,
                "type": tx.get("type"),
                "status": tx.get("status"),
                "timestamp": _to_int(tx.get("timestamp")),
                "raw_json": None,
            }
        )
        for p_item in unwrap_collection(tx.get("players")):
            player = p_item.get("player", p_item) if isinstance(p_item, dict) else p_item
            player = merge_named_node(player) if isinstance(player, list) else player
            tx_data = player.get("transaction_data") or {}
            if isinstance(tx_data, list):
                tx_data = tx_data[0] if tx_data else {}
            player_rows.append(
                {
                    "transaction_key": tx_key,
                    "player_key": player.get("player_key"),
                    "player_name": (player.get("name") or {}).get("full")
                    if isinstance(player.get("name"), dict)
                    else player.get("name"),
                    "movement": tx_data.get("type"),
                    "source_team_key": tx_data.get("source_team_key"),
                    "dest_team_key": tx_data.get("destination_team_key"),
                }
            )

    return tx_rows, player_rows
