"""Recovers full season history for the current league plus any
prior-season league keys discovered at auth time -- the feature that
brings back what Yahoo's UI hides. Safe to re-run and resumable: weeks
already stored as 'postevent' are never refetched (see daily.pull_scoreboards).
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from app import config as cfg
from app.db import database
from app.jobs import daily
from app.yahoo.client import YahooClient

logger = logging.getLogger(__name__)


def backfill_season(client: YahooClient, conn, league_key: str, season_year: int) -> list[str]:
    errors: list[str] = []
    snapshot_date = dt.date.today().isoformat()

    try:
        season_row, _ = daily.pull_settings_and_standings(client, conn, league_key, season_year, snapshot_date)
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.exception("backfill settings/standings failed for %s", league_key)
        errors.append(f"{season_year} settings/standings: {exc}")
        return errors

    try:
        start_week = season_row.get("start_week") or 1
        end_week = season_row.get("end_week") or start_week
        daily.pull_scoreboards(client, conn, league_key, season_year, start_week, end_week)
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.exception("backfill scoreboards failed for %s", league_key)
        errors.append(f"{season_year} scoreboards: {exc}")

    try:
        daily.pull_transactions(client, conn, league_key, season_year)
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.exception("backfill transactions failed for %s", league_key)
        errors.append(f"{season_year} transactions: {exc}")

    try:
        daily.pull_team_season_stats(client, conn, league_key, season_year)
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.exception("backfill team season stats failed for %s", league_key)
        errors.append(f"{season_year} team_season_stats: {exc}")

    try:
        daily.maybe_write_final_standings(conn, season_row)
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.exception("backfill final standings failed for %s", league_key)
        errors.append(f"{season_year} final_standings: {exc}")

    return errors


def run_backfill(season_year: int | None = None) -> dict[str, Any]:
    """Backfill one season_year, or every known season if season_year is None."""
    conn = database.get_connection()
    log_id = database.start_fetch_log(conn, "backfill")
    all_errors: list[str] = []
    seasons_done: list[int] = []
    try:
        config = cfg.load_config()
        client = YahooClient(conn)

        targets: list[tuple[int, str]] = []
        if config.get("league_key") and config.get("season_year"):
            targets.append((config["season_year"], config["league_key"]))
        for entry in config.get("prior_league_keys", []):
            targets.append((entry["season_year"], entry["league_key"]))

        if season_year is not None:
            targets = [t for t in targets if t[0] == season_year]

        for year, league_key in targets:
            logger.info("Backfilling season %s (%s)", year, league_key)
            all_errors.extend(backfill_season(client, conn, league_key, year))
            seasons_done.append(year)

        status = "ok" if not all_errors else "partial"
        database.finish_fetch_log(conn, log_id, status, "; ".join(all_errors))
        return {"status": status, "errors": all_errors, "seasons": seasons_done}
    except Exception as exc:  # noqa: BLE001
        logger.exception("backfill failed")
        database.finish_fetch_log(conn, log_id, "error", str(exc))
        return {"status": "error", "errors": [str(exc)]}
    finally:
        conn.close()
