"""Flask JSON API + static dashboard, with an in-process APScheduler that
runs the daily pull job and catches up automatically after sleep/reboot so
the user never has to touch an OS-level scheduler.
"""
from __future__ import annotations

import datetime as dt
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, g, jsonify, request, send_from_directory

from app import config as cfg
from app import roto
from app.db import database
from app.jobs.daily import run_daily_pull

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
CATCH_UP_STALE_HOURS = 20

# Heuristic, not an API-provided flag: Yahoo doesn't expose whether a stat
# category is a counting stat vs. a ratio/rate stat, so this matches
# common abbreviations to decide whether daily deltas can be summed into
# a cumulative total (counting stats) or not (rate stats -- see
# api_stats_timeline for why that distinction matters).
RATE_STAT_TOKENS = ("era", "whip", "obp", "avg", "ba", "slg", "ops", "fip", "k/9", "bb/9", "k/bb")


def _is_rate_stat(display_name: str | None, name: str | None) -> bool:
    text = f"{display_name or ''} {name or ''}".lower()
    return any(token in text for token in RATE_STAT_TOKENS)

_pull_lock = threading.Lock()
_pull_running = False


def _rows(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(r) for r in cursor.fetchall()]


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = database.get_connection()
    return g.db


def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)

    @app.teardown_appcontext
    def close_db(_exc: BaseException | None) -> None:
        db = g.pop("db", None)
        if db is not None:
            db.close()

    @app.route("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.route("/favicon.ico")
    def favicon():
        return "", 204

    @app.route("/<path:filename>")
    def static_files(filename: str):
        return send_from_directory(STATIC_DIR, filename)

    @app.route("/api/meta")
    def api_meta():
        conn = get_db()
        seasons = _rows(conn.execute("SELECT * FROM seasons ORDER BY season_year DESC"))
        teams = _rows(conn.execute("SELECT * FROM teams ORDER BY season_year DESC, name"))
        stat_categories = _rows(
            conn.execute(
                "SELECT * FROM stat_categories WHERE is_display_only = 0 "
                "ORDER BY season_year DESC, display_order"
            )
        )
        last_ok = database.last_successful_fetch(conn)
        last_any = conn.execute("SELECT * FROM fetch_log ORDER BY id DESC LIMIT 1").fetchone()
        return jsonify(
            {
                "seasons": seasons,
                "teams": teams,
                "stat_categories": stat_categories,
                "last_successful_pull": dict(last_ok) if last_ok else None,
                "last_pull_attempt": dict(last_any) if last_any else None,
                "pull_running": _pull_running,
            }
        )

    @app.route("/api/standings")
    def api_standings():
        conn = get_db()
        season = request.args.get("season", type=int) or _latest_season(conn)
        if season is None:
            return jsonify({"season": None, "date": None, "standings": []})
        date = request.args.get("date")
        if not date:
            row = conn.execute(
                "SELECT MAX(snapshot_date) AS d FROM standings_snapshots WHERE season_year = ?",
                (season,),
            ).fetchone()
            date = row["d"] if row else None
        standings = []
        if date:
            standings = _rows(
                conn.execute(
                    """
                    SELECT s.*, t.name, t.manager_nickname, t.logo_url
                    FROM standings_snapshots s
                    JOIN teams t ON t.team_key = s.team_key
                    WHERE s.season_year = ? AND s.snapshot_date = ?
                    ORDER BY s.rank ASC
                    """,
                    (season, date),
                )
            )
        return jsonify({"season": season, "date": date, "standings": standings})

    @app.route("/api/standings/timeline")
    def api_standings_timeline():
        conn = get_db()
        season = request.args.get("season", type=int) or _latest_season(conn)
        if season is None:
            return jsonify({"season": None, "teams": {}})
        rows = _rows(
            conn.execute(
                """
                SELECT s.snapshot_date, s.team_key, s.rank, s.wins, s.losses, s.ties, t.name
                FROM standings_snapshots s
                JOIN teams t ON t.team_key = s.team_key
                WHERE s.season_year = ?
                ORDER BY s.snapshot_date ASC
                """,
                (season,),
            )
        )
        teams: dict[str, dict[str, Any]] = {}
        for r in rows:
            entry = teams.setdefault(r["team_key"], {"name": r["name"], "points": []})
            entry["points"].append(
                {"date": r["snapshot_date"], "rank": r["rank"], "wins": r["wins"], "losses": r["losses"]}
            )
        return jsonify({"season": season, "teams": teams})

    @app.route("/api/matchups")
    def api_matchups():
        conn = get_db()
        season = request.args.get("season", type=int) or _latest_season(conn)
        week = request.args.get("week", type=int)
        team = request.args.get("team")
        if season is None:
            return jsonify({"season": None, "matchups": []})

        query = ["SELECT * FROM matchups WHERE season_year = ?"]
        params: list[Any] = [season]
        if week is not None:
            query.append("AND week = ?")
            params.append(week)
        if team:
            query.append("AND (team1_key = ? OR team2_key = ?)")
            params.extend([team, team])
        query.append("ORDER BY week ASC, matchup_id ASC")
        matchups = _rows(conn.execute(" ".join(query), params))

        team_names = {r["team_key"]: r["name"] for r in conn.execute("SELECT team_key, name FROM teams")}
        cats = {
            r["stat_id"]: r["display_name"]
            for r in conn.execute(
                "SELECT stat_id, display_name FROM stat_categories WHERE season_year = ?", (season,)
            )
        }

        for m in matchups:
            m["team1_name"] = team_names.get(m["team1_key"])
            m["team2_name"] = team_names.get(m["team2_key"])
            stat_rows = conn.execute(
                "SELECT team_key, stat_id, value, won_category FROM matchup_team_stats WHERE matchup_id = ?",
                (m["matchup_id"],),
            ).fetchall()
            by_stat: dict[int, dict[str, Any]] = {}
            for sr in stat_rows:
                entry = by_stat.setdefault(
                    sr["stat_id"], {"stat_id": sr["stat_id"], "name": cats.get(sr["stat_id"], str(sr["stat_id"]))}
                )
                if sr["team_key"] == m["team1_key"]:
                    entry["team1_value"] = sr["value"]
                    entry["team1_won"] = sr["won_category"]
                else:
                    entry["team2_value"] = sr["value"]
            m["categories"] = list(by_stat.values())

        return jsonify({"season": season, "matchups": matchups})

    @app.route("/api/h2h")
    def api_h2h():
        conn = get_db()
        season = request.args.get("season", type=int)
        query = [
            """
            SELECT m.team1_key, m.team2_key, m.winner_team_key, m.is_tied,
                   t1.manager_guid AS guid1, t1.manager_nickname AS name1,
                   t2.manager_guid AS guid2, t2.manager_nickname AS name2
            FROM matchups m
            JOIN teams t1 ON t1.team_key = m.team1_key
            JOIN teams t2 ON t2.team_key = m.team2_key
            WHERE m.status = 'postevent' AND m.is_consolation = 0
            """
        ]
        params: list[Any] = []
        if season is not None:
            query.append("AND m.season_year = ?")
            params.append(season)
        rows = conn.execute(" ".join(query), params).fetchall()

        # Team1/team2 assignment is arbitrary per matchup (it flips from
        # week to week for the same pair of managers), so the grid key
        # must be normalized by manager_guid, not by team1/team2 order --
        # otherwise the same pair of managers ends up split across two
        # separate, incomplete grid cells.
        grid: dict[str, dict[str, Any]] = {}

        for r in rows:
            guid1, guid2 = r["guid1"], r["guid2"]
            if not guid1 or not guid2:
                continue
            if guid1 <= guid2:
                key = f"{guid1}|{guid2}"
                default = {"manager_a": r["name1"], "manager_b": r["name2"], "wins_a": 0, "wins_b": 0, "ties": 0}
                a_guid, b_guid = guid1, guid2
            else:
                key = f"{guid2}|{guid1}"
                default = {"manager_a": r["name2"], "manager_b": r["name1"], "wins_a": 0, "wins_b": 0, "ties": 0}
                a_guid, b_guid = guid2, guid1
            c = grid.setdefault(key, default)

            if r["is_tied"]:
                c["ties"] += 1
                continue
            winner_guid = None
            if r["winner_team_key"] == r["team1_key"]:
                winner_guid = guid1
            elif r["winner_team_key"] == r["team2_key"]:
                winner_guid = guid2
            if winner_guid == a_guid:
                c["wins_a"] += 1
            elif winner_guid == b_guid:
                c["wins_b"] += 1

        return jsonify({"season": season, "matchups": list(grid.values())})

    @app.route("/api/roto/standings")
    def api_roto_standings():
        """Reconstructs Yahoo's roto 'Overall Stats' / 'Overall Points'
        tables from our own daily stat snapshots (see app/roto.py), plus a
        day-over-day points change versus the previous available snapshot."""
        conn = get_db()
        season = request.args.get("season", type=int) or _latest_season(conn)
        if season is None:
            return jsonify({"season": None, "date": None, "categories": [], "standings": []})

        date = request.args.get("date")
        if not date:
            row = conn.execute(
                "SELECT MAX(snapshot_date) AS d FROM team_stat_snapshots WHERE season_year = ?", (season,)
            ).fetchone()
            date = row["d"] if row else None
        if not date:
            return jsonify({"season": season, "date": None, "categories": [], "standings": []})

        categories = _rows(
            conn.execute(
                "SELECT stat_id, display_name, position_type, sort_order, is_display_only "
                "FROM stat_categories WHERE season_year = ? ORDER BY display_order",
                (season,),
            )
        )
        scored_categories = [c for c in categories if not c["is_display_only"]]

        def stat_values_for(snapshot_date: str) -> dict[str, dict[int, float]]:
            rows = conn.execute(
                "SELECT team_key, stat_id, value FROM team_stat_snapshots "
                "WHERE season_year = ? AND snapshot_date = ?",
                (season, snapshot_date),
            ).fetchall()
            values: dict[str, dict[int, float]] = {}
            for r in rows:
                try:
                    v = float(r["value"])
                except (TypeError, ValueError):
                    continue
                values.setdefault(r["team_key"], {})[r["stat_id"]] = v
            return values

        team_values = stat_values_for(date)
        computed = roto.compute_standings(team_values, scored_categories)

        prev_row = conn.execute(
            "SELECT MAX(snapshot_date) AS d FROM team_stat_snapshots "
            "WHERE season_year = ? AND snapshot_date < ?",
            (season, date),
        ).fetchone()
        prev_date = prev_row["d"] if prev_row else None
        prev_totals: dict[str, float] = {}
        if prev_date:
            prev_computed = roto.compute_standings(stat_values_for(prev_date), scored_categories)
            prev_totals = {tk: d["total_points"] for tk, d in prev_computed.items()}

        team_info = {
            r["team_key"]: {"name": r["name"], "manager_nickname": r["manager_nickname"]}
            for r in conn.execute(
                "SELECT team_key, name, manager_nickname FROM teams WHERE season_year = ?", (season,)
            )
        }

        standings = []
        for team_key, data in computed.items():
            total = round(data["total_points"], 2)
            pts_change = round(total - prev_totals[team_key], 2) if team_key in prev_totals else None
            standings.append(
                {
                    "team_key": team_key,
                    "name": team_info.get(team_key, {}).get("name"),
                    "manager_nickname": team_info.get(team_key, {}).get("manager_nickname"),
                    "stats": team_values.get(team_key, {}),
                    "category_points": data["category_points"],
                    "total_points": total,
                    "pts_change": pts_change,
                }
            )
        standings.sort(key=lambda s: -s["total_points"])
        roto.rank_by_total_points(standings)

        return jsonify(
            {"season": season, "date": date, "prev_date": prev_date, "categories": categories, "standings": standings}
        )

    @app.route("/api/categories")
    def api_categories():
        conn = get_db()
        season = request.args.get("season", type=int) or _latest_season(conn)
        team = request.args.get("team")
        if season is None:
            return jsonify({"season": None, "categories": []})

        query = [
            """
            SELECT mts.team_key, t.name, mts.stat_id, sc.display_name,
                   SUM(CASE WHEN mts.won_category = 1 THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN mts.tied_category = 1 THEN 1 ELSE 0 END) AS ties,
                   COUNT(*) AS played
            FROM matchup_team_stats mts
            JOIN matchups m ON m.matchup_id = mts.matchup_id
            JOIN teams t ON t.team_key = mts.team_key
            LEFT JOIN stat_categories sc ON sc.season_year = m.season_year AND sc.stat_id = mts.stat_id
            WHERE m.season_year = ? AND m.status = 'postevent'
            """
        ]
        params: list[Any] = [season]
        if team:
            query.append("AND mts.team_key = ?")
            params.append(team)
        query.append("GROUP BY mts.team_key, mts.stat_id ORDER BY sc.display_order, t.name")
        rows = _rows(conn.execute(" ".join(query), params))
        return jsonify({"season": season, "categories": rows})

    @app.route("/api/stats/timeline")
    def api_stats_timeline():
        """Day-by-day cumulative value of one stat category per team, so
        within-season trends (not just weekly matchup results) are visible.

        Counting stats (HR, RBI, SB, ...) are reconstructed as a running
        sum of team_daily_stat_deltas, which covers the whole season once
        backfilled -- retroactively, not just from whenever this app
        started running. Rate stats (ERA, WHIP, OBP, ...) can't be summed
        that way (a ratio of ratios isn't the season ratio), so those fall
        back to team_stat_snapshots, which only has coverage starting from
        whenever pulls began."""
        conn = get_db()
        season = request.args.get("season", type=int) or _latest_season(conn)
        stat_id = request.args.get("stat_id", type=int)
        if season is None or stat_id is None:
            return jsonify({"season": season, "stat_id": stat_id, "teams": {}})

        cat = conn.execute(
            "SELECT display_name, name FROM stat_categories WHERE season_year = ? AND stat_id = ?",
            (season, stat_id),
        ).fetchone()
        rate_stat = _is_rate_stat(cat["display_name"] if cat else None, cat["name"] if cat else None)

        teams: dict[str, dict[str, Any]] = {}
        if rate_stat:
            rows = _rows(
                conn.execute(
                    """
                    SELECT tss.snapshot_date, tss.team_key, tss.value, t.name
                    FROM team_stat_snapshots tss
                    JOIN teams t ON t.team_key = tss.team_key
                    WHERE tss.season_year = ? AND tss.stat_id = ?
                    ORDER BY tss.snapshot_date ASC
                    """,
                    (season, stat_id),
                )
            )
            for r in rows:
                entry = teams.setdefault(r["team_key"], {"name": r["name"], "points": []})
                try:
                    value = float(r["value"])
                except (TypeError, ValueError):
                    continue
                entry["points"].append({"date": r["snapshot_date"], "value": value})
        else:
            rows = _rows(
                conn.execute(
                    """
                    SELECT tds.snapshot_date, tds.team_key, tds.value, t.name
                    FROM team_daily_stat_deltas tds
                    JOIN teams t ON t.team_key = tds.team_key
                    WHERE tds.season_year = ? AND tds.stat_id = ?
                    ORDER BY tds.team_key, tds.snapshot_date ASC
                    """,
                    (season, stat_id),
                )
            )
            running_totals: dict[str, float] = {}
            for r in rows:
                entry = teams.setdefault(r["team_key"], {"name": r["name"], "points": []})
                try:
                    delta = float(r["value"])
                except (TypeError, ValueError):
                    continue
                running_totals[r["team_key"]] = running_totals.get(r["team_key"], 0.0) + delta
                entry["points"].append({"date": r["snapshot_date"], "value": running_totals[r["team_key"]]})
        return jsonify({"season": season, "stat_id": stat_id, "teams": teams})

    @app.route("/api/transactions")
    def api_transactions():
        conn = get_db()
        season = request.args.get("season", type=int) or _latest_season(conn)
        team = request.args.get("team")
        tx_type = request.args.get("type")
        q = request.args.get("q")
        if season is None:
            return jsonify({"season": None, "transactions": []})

        query = [
            """
            SELECT DISTINCT tr.transaction_key, tr.type, tr.status, tr.timestamp
            FROM transactions tr
            LEFT JOIN transaction_players tp ON tp.transaction_key = tr.transaction_key
            WHERE tr.season_year = ?
            """
        ]
        params: list[Any] = [season]
        if team:
            query.append("AND (tp.source_team_key = ? OR tp.dest_team_key = ?)")
            params.extend([team, team])
        if tx_type:
            query.append("AND tr.type = ?")
            params.append(tx_type)
        if q:
            query.append("AND tp.player_name LIKE ?")
            params.append(f"%{q}%")
        query.append("ORDER BY tr.timestamp DESC")
        tx_rows = _rows(conn.execute(" ".join(query), params))

        for tx in tx_rows:
            tx["players"] = _rows(
                conn.execute(
                    "SELECT player_key, player_name, movement, source_team_key, dest_team_key "
                    "FROM transaction_players WHERE transaction_key = ?",
                    (tx["transaction_key"],),
                )
            )
        return jsonify({"season": season, "transactions": tx_rows})

    @app.route("/api/team/<team_key>")
    def api_team(team_key: str):
        conn = get_db()
        team = conn.execute("SELECT * FROM teams WHERE team_key = ?", (team_key,)).fetchone()
        if not team:
            return jsonify({"error": "not found"}), 404
        season = team["season_year"]
        latest_standing = conn.execute(
            """
            SELECT * FROM standings_snapshots WHERE team_key = ?
            ORDER BY snapshot_date DESC LIMIT 1
            """,
            (team_key,),
        ).fetchone()
        season_stats = _rows(
            conn.execute(
                """
                SELECT tss.stat_id, sc.display_name, tss.value
                FROM team_stat_snapshots tss
                LEFT JOIN stat_categories sc ON sc.season_year = tss.season_year AND sc.stat_id = tss.stat_id
                WHERE tss.team_key = ?
                  AND tss.snapshot_date = (
                      SELECT MAX(snapshot_date) FROM team_stat_snapshots WHERE team_key = ?
                  )
                ORDER BY sc.display_order
                """,
                (team_key, team_key),
            )
        )
        matchups = _rows(
            conn.execute(
                "SELECT * FROM matchups WHERE team1_key = ? OR team2_key = ? ORDER BY week",
                (team_key, team_key),
            )
        )
        return jsonify(
            {
                "team": dict(team),
                "season": season,
                "latest_standing": dict(latest_standing) if latest_standing else None,
                "season_stats": season_stats,
                "matchups": matchups,
            }
        )

    @app.route("/api/history")
    def api_history():
        conn = get_db()
        seasons = _rows(conn.execute("SELECT * FROM seasons ORDER BY season_year DESC"))
        for s in seasons:
            s["final_standings"] = _rows(
                conn.execute(
                    """
                    SELECT fs.final_rank, t.name, t.manager_nickname
                    FROM final_standings fs
                    JOIN teams t ON t.team_key = fs.team_key
                    WHERE fs.season_year = ?
                    ORDER BY fs.final_rank ASC
                    """,
                    (s["season_year"],),
                )
            )
        return jsonify({"seasons": seasons})

    @app.route("/api/pull", methods=["POST"])
    def api_pull():
        global _pull_running
        with _pull_lock:
            if _pull_running:
                return jsonify({"started": False, "reason": "already running"}), 409
            _pull_running = True

        def _run():
            global _pull_running
            try:
                run_daily_pull(kind="manual")
            finally:
                with _pull_lock:
                    _pull_running = False

        threading.Thread(target=_run, daemon=True).start()
        return jsonify({"started": True})

    return app


def _latest_season(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT MAX(season_year) AS y FROM seasons").fetchone()
    return row["y"] if row and row["y"] is not None else None


def _catch_up_if_stale() -> None:
    global _pull_running
    conn = database.get_connection()
    try:
        last = database.last_successful_fetch(conn)
    finally:
        conn.close()

    stale = True
    if last and last["run_finished_at"]:
        finished = dt.datetime.fromisoformat(last["run_finished_at"])
        stale = (dt.datetime.utcnow() - finished) > dt.timedelta(hours=CATCH_UP_STALE_HOURS)

    if not stale:
        return
    with _pull_lock:
        if _pull_running:
            return
        _pull_running = True

    def _run():
        global _pull_running
        try:
            logger.info("Data is stale or missing; running catch-up pull")
            run_daily_pull(kind="daily")
        finally:
            with _pull_lock:
                _pull_running = False

    threading.Thread(target=_run, daemon=True).start()


def run_server(host: str = "127.0.0.1", port: int = 8765) -> None:
    cfg.ensure_data_dir()
    config = cfg.load_config()
    pull_time = config.get("pull_time", cfg.DEFAULT_PULL_TIME)
    hour, minute = (int(p) for p in pull_time.split(":"))

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        lambda: run_daily_pull(kind="daily"),
        "cron",
        hour=hour,
        minute=minute,
        id="daily_pull",
    )
    scheduler.add_job(_catch_up_if_stale, "interval", hours=1, id="catch_up_check", next_run_time=dt.datetime.now())
    scheduler.start()

    app = create_app()
    try:
        print(f"Dashboard running at http://{host}:{port}  (daily pull scheduled for {pull_time})")
        app.run(host=host, port=port, threaded=True)
    finally:
        scheduler.shutdown(wait=False)
