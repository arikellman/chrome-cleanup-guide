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

    if season_row.get("scoring_type") != "roto":
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
        daily.pull_team_stat_snapshots(client, conn, league_key, season_year, snapshot_date)
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.exception("backfill team stat snapshot failed for %s", league_key)
        errors.append(f"{season_year} team_stat_snapshots: {exc}")

    errors.extend(backfill_daily_stat_deltas(client, conn, league_key, season_year, season_row))

    try:
        daily.maybe_write_final_standings(conn, season_row)
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.exception("backfill final standings failed for %s", league_key)
        errors.append(f"{season_year} final_standings: {exc}")

    return errors


def season_backfill_date_range(season_row: dict[str, Any], today: dt.date | None = None) -> tuple[dt.date, dt.date] | None:
    """Pure helper: the [start_date, end_date] range to backfill daily stat
    deltas for, clamped so we never request a future date. Returns None if
    the season has no usable start_date, or if start is already past the
    clamped end (nothing to do)."""
    today = today or dt.date.today()
    start_date_str = season_row.get("start_date")
    if not start_date_str:
        return None
    try:
        start_date = dt.date.fromisoformat(start_date_str)
    except ValueError:
        return None

    end_date = today
    end_date_str = season_row.get("end_date")
    if end_date_str:
        try:
            end_date = min(dt.date.fromisoformat(end_date_str), today)
        except ValueError:
            pass

    if start_date > end_date:
        return None
    return start_date, end_date


def backfill_daily_stat_deltas(
    client: YahooClient, conn, league_key: str, season_year: int, season_row: dict[str, Any]
) -> list[str]:
    """Loops day-by-day over the season pulling each day's individual stat
    contribution (Yahoo's type=date team stats). Unlike the cumulative
    season total (only ever available "as of right now"), Yahoo can answer
    "what happened on day X" for any past day, so this recovers full
    within-season daily history retroactively instead of only building up
    from whenever this app started running. Resumable: dates already
    stored are skipped, so a second run only fetches what's missing.
    """
    errors: list[str] = []
    date_range = season_backfill_date_range(season_row)
    if date_range is None:
        return errors
    start_date, end_date = date_range

    already = database.stored_daily_stat_delta_dates(conn, season_year)
    total_days = (end_date - start_date).days + 1
    processed = 0
    current = start_date
    while current <= end_date:
        date_str = current.isoformat()
        if date_str not in already:
            try:
                daily.pull_team_daily_stat_deltas(client, conn, league_key, season_year, date_str)
                conn.commit()
            except Exception as exc:  # noqa: BLE001
                logger.exception("backfill daily stat delta failed for %s on %s", league_key, date_str)
                errors.append(f"{season_year} daily_stat_delta {date_str}: {exc}")
        processed += 1
        if processed % 20 == 0:
            logger.info(
                "Daily stat delta backfill: %s/%s days done for season %s", processed, total_days, season_year
            )
        current += dt.timedelta(days=1)

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
