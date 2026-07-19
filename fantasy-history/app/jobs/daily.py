"""Daily incremental pull: standings snapshot, matchups/scoreboard for
unfinished weeks, transactions, season stats, and final standings once the
season ends.

Every write goes through app.db.database's upsert helpers (INSERT ...
ON CONFLICT DO UPDATE against natural keys), so this whole job is safe to
re-run any number of times a day, and a missed day is harmless: weeks
already stored with status 'postevent' are final and never refetched,
while any week still in progress gets refetched until it settles.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from app import config as cfg
from app.db import database
from app.yahoo import parse
from app.yahoo.client import YahooClient

logger = logging.getLogger(__name__)


def _to_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def find_subresource(league_list: list[Any], key: str) -> Any:
    """League/team sub-resources Yahoo requested via `out=` come back as
    extra elements like {"settings": [...]} or {"standings": [...]}
    appended after the flat/field-list first element."""
    for item in league_list[1:]:
        if isinstance(item, dict) and key in item:
            value = item[key]
            if isinstance(value, list) and len(value) == 1:
                return value[0]
            return value
    return {}


def _league_list(body: dict[str, Any]) -> list[Any]:
    return body["fantasy_content"]["league"]


def pull_settings_and_standings(
    client: YahooClient, conn, league_key: str, season_year: int, snapshot_date: str
) -> tuple[dict[str, Any], int | None]:
    body = client.get(f"league/{league_key}", params={"out": "settings,standings"}, season_year=season_year)
    league_list = _league_list(body)
    fields = parse.flatten_field_list(league_list[0])
    fields.setdefault("season", season_year)
    current_week = _to_int(fields.get("current_week"))

    season_row = parse.parse_league_meta(fields)
    database.upsert_season(conn, season_row)

    settings = find_subresource(league_list, "settings")
    if settings:
        stat_rows = parse.parse_stat_categories(settings, season_year)
        database.upsert_stat_categories(conn, stat_rows)

    standings = find_subresource(league_list, "standings")
    teams_node = standings.get("teams") if isinstance(standings, dict) else None
    teams_node = teams_node or {}
    team_rows = parse.parse_teams(teams_node, season_year)
    database.upsert_teams(conn, team_rows)
    snapshot_rows = parse.parse_standings_snapshot(teams_node, season_year, snapshot_date)
    database.insert_standings_snapshot(conn, snapshot_rows)

    return season_row, current_week


def pull_scoreboards(
    client: YahooClient, conn, league_key: str, season_year: int, start_week: int, end_week: int
) -> None:
    already_final = database.stored_postevent_weeks(conn, season_year)
    for week in range(start_week, end_week + 1):
        if week in already_final:
            continue
        body = client.get(
            f"league/{league_key}/scoreboard", params={"week": week}, season_year=season_year, week=week
        )
        league_list = _league_list(body)
        scoreboard = find_subresource(league_list, "scoreboard")
        matchups_rows, stats_rows = parse.parse_scoreboard(scoreboard, season_year, week)
        for m in matchups_rows:
            database.upsert_matchup(conn, m)
        database.upsert_matchup_team_stats(conn, stats_rows)


def pull_transactions(client: YahooClient, conn, league_key: str, season_year: int) -> None:
    body = client.get(f"league/{league_key}/transactions", season_year=season_year)
    league_list = _league_list(body)
    transactions_node = find_subresource(league_list, "transactions")
    tx_rows, player_rows = parse.parse_transactions(transactions_node, season_year)

    existing = {
        r["transaction_key"]
        for r in conn.execute(
            "SELECT transaction_key FROM transactions WHERE season_year = ?", (season_year,)
        )
    }
    new_tx = [r for r in tx_rows if r["transaction_key"] not in existing]
    for row in new_tx:
        database.upsert_transaction(conn, row)
    new_keys = {r["transaction_key"] for r in new_tx}
    database.upsert_transaction_players(conn, [r for r in player_rows if r["transaction_key"] in new_keys])


def _fetch_teams_node(
    client: YahooClient, conn, league_key: str, season_year: int, params: dict[str, Any]
) -> Any:
    body = client.get(
        f"league/{league_key}/teams", params={"out": "stats", **params}, season_year=season_year
    )
    league_list = _league_list(body)
    return find_subresource(league_list, "teams") or {}


def pull_team_stat_snapshots(
    client: YahooClient, conn, league_key: str, season_year: int, snapshot_date: str
) -> None:
    teams_node = _fetch_teams_node(client, conn, league_key, season_year, {"type": "season"})
    rows = parse.parse_team_stat_snapshots(teams_node, season_year, snapshot_date)
    database.insert_team_stat_snapshots(conn, rows)


def pull_team_daily_stat_deltas(
    client: YahooClient, conn, league_key: str, season_year: int, game_date: str
) -> None:
    """Pulls that SINGLE day's stat contribution per team (Yahoo's
    type=date team stats), not the season-cumulative total. Unlike the
    cumulative total, this can be requested for any past day, which is
    what makes retroactive full-season backfill possible."""
    teams_node = _fetch_teams_node(
        client, conn, league_key, season_year, {"type": "date", "date": game_date}
    )
    rows = parse.parse_team_stat_snapshots(teams_node, season_year, game_date)
    database.insert_team_daily_stat_deltas(conn, rows)


def maybe_write_final_standings(conn, season_row: dict[str, Any]) -> None:
    if not season_row.get("is_finished"):
        return
    season_year = season_row["season_year"]
    existing = conn.execute(
        "SELECT 1 FROM final_standings WHERE season_year = ? LIMIT 1", (season_year,)
    ).fetchone()
    if existing:
        return
    rows = conn.execute(
        "SELECT team_key, rank FROM standings_snapshots WHERE season_year = ? "
        "AND snapshot_date = (SELECT MAX(snapshot_date) FROM standings_snapshots WHERE season_year = ?)",
        (season_year, season_year),
    ).fetchall()
    final_rows = [
        {"season_year": season_year, "team_key": r["team_key"], "final_rank": r["rank"]} for r in rows
    ]
    database.upsert_final_standings(conn, final_rows)


def run_daily_pull(kind: str = "daily") -> dict[str, Any]:
    """Run one full pull for the currently-configured season's league.

    Returns {"status": "ok" | "partial" | "error", "errors": [...]}. Each
    step is isolated in its own try/except so one failing endpoint (e.g.
    transactions down) doesn't discard data that other steps managed to
    fetch; the run is marked 'partial' rather than aborted.
    """
    conn = database.get_connection()
    log_id = database.start_fetch_log(conn, kind)
    errors: list[str] = []
    try:
        config = cfg.load_config()
        league_key = config.get("league_key")
        season_year = config.get("season_year")
        if not league_key or not season_year:
            raise RuntimeError("No league configured yet. Run: python -m app auth")

        client = YahooClient(conn)
        snapshot_date = dt.date.today().isoformat()

        season_row = None
        current_week = None
        try:
            season_row, current_week = pull_settings_and_standings(
                client, conn, league_key, season_year, snapshot_date
            )
            conn.commit()
        except Exception as exc:  # noqa: BLE001 - isolate per-step failures
            logger.exception("settings/standings pull failed")
            errors.append(f"settings/standings: {exc}")

        # Roto leagues have no weekly head-to-head matchups; Yahoo's
        # scoreboard endpoint doesn't apply, so skip it entirely.
        if season_row and season_row.get("scoring_type") != "roto":
            try:
                start_week = season_row.get("start_week") or 1
                end_week = current_week or season_row.get("end_week") or start_week
                end_week = min(end_week, season_row.get("end_week") or end_week)
                pull_scoreboards(client, conn, league_key, season_year, start_week, max(end_week, start_week))
                conn.commit()
            except Exception as exc:  # noqa: BLE001
                logger.exception("scoreboard pull failed")
                errors.append(f"scoreboards: {exc}")

        try:
            pull_transactions(client, conn, league_key, season_year)
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            logger.exception("transactions pull failed")
            errors.append(f"transactions: {exc}")

        try:
            pull_team_stat_snapshots(client, conn, league_key, season_year, snapshot_date)
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            logger.exception("team stat snapshot pull failed")
            errors.append(f"team_stat_snapshots: {exc}")

        try:
            pull_team_daily_stat_deltas(client, conn, league_key, season_year, snapshot_date)
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            logger.exception("team daily stat delta pull failed")
            errors.append(f"team_daily_stat_deltas: {exc}")

        if season_row:
            try:
                maybe_write_final_standings(conn, season_row)
                conn.commit()
            except Exception as exc:  # noqa: BLE001
                logger.exception("final standings write failed")
                errors.append(f"final_standings: {exc}")

        status = "ok" if not errors else ("partial" if season_row else "error")
        database.finish_fetch_log(conn, log_id, status, "; ".join(errors))
        return {"status": status, "errors": errors}
    except Exception as exc:  # noqa: BLE001 - top-level so fetch_log always closes out
        logger.exception("daily pull failed")
        database.finish_fetch_log(conn, log_id, "error", str(exc))
        return {"status": "error", "errors": [str(exc)]}
    finally:
        conn.close()
