"""CLI entry point: python -m app <auth|pull|backfill|scrape-auth|scrape-season|scrape-daily-stats|fix-transaction-timestamps|scrape-roster-snapshot|keeper-eligibility|serve|status>"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
import webbrowser
from typing import Any

from app import config as cfg
from app.db import database
from app.yahoo import oauth, parse
from app.yahoo.client import YahooClient

logger = logging.getLogger(__name__)


def _to_int(value):
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def cmd_auth(args: argparse.Namespace) -> None:
    config = cfg.load_config()

    if not config.get("client_id") or not config.get("client_secret"):
        print("Create a Yahoo Developer app first: https://developer.yahoo.com/apps/create/")
        print("  - App type: Installed Application / Confidential Client")
        print(f"  - Redirect URI: {config.get('redirect_uri', cfg.DEFAULT_REDIRECT_URI)}")
        print("  - API Permissions: Fantasy Sports (Read)")
        config["client_id"] = input("Client ID (Consumer Key): ").strip()
        config["client_secret"] = input("Client Secret (Consumer Secret): ").strip()
        cfg.save_config(config)

    # If we already have a working Yahoo login, don't force the user back
    # through the browser/code-paste dance just to (re)pick a league --
    # that redundant relogin is what made this step easy to abandon
    # partway through the first time.
    if cfg.has_tokens() and not args.relogin:
        print("Already authenticated with Yahoo -- skipping login, just (re)selecting your league.")
        print("(Run with --relogin if you need to sign in again, e.g. after revoking access.)\n")
    else:
        url = oauth.build_authorize_url(config)
        print(f"\nOpening your browser to authorize this app with Yahoo:\n  {url}\n")
        try:
            webbrowser.open(url)
        except Exception:
            pass
        print("Log in and click Agree. Yahoo will redirect to a URL that won't load")
        print("(that's expected) -- copy the 'code' value from that URL's address bar.")
        code = input("Paste the code here: ").strip()

        oauth.exchange_code(config, code)
        print("Authenticated with Yahoo.\n")

    client = YahooClient()
    body = client.get("users;use_login=1/games;game_codes=mlb/leagues")
    leagues = parse.parse_user_leagues(body)
    if not leagues:
        print("No MLB fantasy leagues found on this Yahoo account.")
        sys.exit(1)

    print("Your MLB fantasy leagues:")
    for i, league in enumerate(leagues):
        print(f"  [{i}] {league['season']} - {league['name']} ({league['league_key']})")
    selected = None
    while selected is None:
        choice = input(f"Pick a league to track [0-{len(leagues) - 1}]: ").strip()
        try:
            selected = leagues[int(choice)]
        except (ValueError, IndexError):
            print(f"Please enter a number from 0 to {len(leagues) - 1}.")

    # Save the league selection right away -- everything after this point
    # (prior-season discovery) is a bonus feature and must not be able to
    # cost you the core selection if it hits a snag.
    config["league_key"] = selected["league_key"]
    config["season_year"] = selected["season"]
    config["game_key"] = selected["game_key"]
    cfg.save_config(config)
    print(f"\nTracking: {selected['season']} - {selected['name']} ({selected['league_key']})")

    print("Looking for prior seasons of this league (Yahoo hides these in the UI)...")
    priors = _discover_prior_seasons(client, selected["league_key"], selected["season"])
    config["prior_league_keys"] = priors
    cfg.save_config(config)

    if priors:
        years = ", ".join(str(p["season_year"]) for p in priors)
        print(f"Found prior seasons: {years}")
    else:
        print("No prior seasons found (or this is the league's first season).")

    print("\nSetup complete. Next steps:")
    print("  python -m app backfill --all   # recover full history")
    print("  python -m app serve             # run the daily job + dashboard")


def _discover_prior_seasons(client: YahooClient, league_key: str, current_season: int) -> list[dict]:
    """Best-effort only: this is a bonus feature (recovering seasons Yahoo
    hides in its UI), not required for the app to work, so ANY failure
    here -- a network error or a response shape that doesn't match what
    we expected -- just stops the walk and returns whatever was found so
    far, rather than raising and losing the league selection that should
    be saved regardless of whether this succeeds."""
    priors: list[dict] = []
    seen = {current_season}
    cursor = league_key
    for _ in range(30):  # generous cap; a real league won't have this many seasons
        try:
            body = client.get(f"league/{cursor}", params={"out": "settings"})
            league_list = body["fantasy_content"]["league"]
            fields = parse.flatten_field_list(league_list[0])
            renew = fields.get("renew")
            if not renew:
                break
            prior_body = client.get(f"league/{renew}")
            prior_fields = parse.flatten_field_list(prior_body["fantasy_content"]["league"][0])
            prior_season = _to_int(prior_fields.get("season"))
        except Exception:  # noqa: BLE001 - see docstring
            break
        if prior_season is None or prior_season in seen:
            break
        priors.append({"season_year": prior_season, "league_key": renew})
        seen.add(prior_season)
        cursor = renew
    return priors


def _current_scrape_league_and_year(config: dict) -> tuple[str, int]:
    league_id = config.get("yahoo_web_league_id")
    season_year = config.get("yahoo_web_current_season_year")
    if not league_id or not season_year:
        raise RuntimeError(
            "yahoo_web_league_id / yahoo_web_current_season_year not set in data/config.json. "
            "Set them once: yahoo_web_league_id is the numeric id in your league's URL "
            "(the 74647 in baseball.fantasysports.yahoo.com/b1/74647/...), and "
            "yahoo_web_current_season_year is the season that id currently points to. "
            "Run `python -m app scrape-auth` first if you haven't logged in yet."
        )
    return league_id, season_year


def _scrape_one_season(conn, season_year: int, league_id: str, base_url: str | None = None) -> dict:
    """Runs all three scrape pulls (standings, draft results,
    transactions) for one season into the existing DB tables. Draft
    results and transactions both depend on that season's `teams` rows
    already existing, so standings (which populates `teams`) always runs
    first.

    `base_url` should be passed for any season OTHER than the currently
    configured one -- confirmed live that historical seasons' URLs can
    carry a year path segment the default league_id+sport_path template
    doesn't know about (see app/scrape/jobs.py's league_home_url
    docstring) -- pass None only for the current season.
    """
    from app.scrape import jobs as scrape_jobs

    result = {}
    result["standings"] = scrape_jobs.scrape_pull_standings(conn, season_year, league_id, base_url=base_url)
    result["draft_results"] = scrape_jobs.scrape_pull_draft_results(conn, season_year, league_id, base_url=base_url)
    result["transactions"] = scrape_jobs.scrape_pull_transactions(conn, season_year, league_id, base_url=base_url)
    return result


def cmd_pull(_args: argparse.Namespace) -> None:
    """Runs one manual pull of the CURRENT season via browser scraping
    (app/scrape/) -- Yahoo revoked this app's Fantasy Sports API access,
    see app/yahoo/client.py's module docstring. The old API-based
    app.jobs.daily.run_daily_pull is left in place, dormant, for if that
    ever changes.
    """
    config = cfg.load_config()
    league_id, season_year = _current_scrape_league_and_year(config)
    conn = database.get_connection()
    try:
        result = _scrape_one_season(conn, season_year, league_id)
        print(result)
    except Exception as exc:  # noqa: BLE001 - surface clearly at the CLI, including NeedsReloginError
        print(f"Scrape pull failed: {exc}")
        sys.exit(1)
    finally:
        conn.close()


def cmd_backfill(args: argparse.Namespace) -> None:
    """Recovers season history via browser scraping (app/scrape/) --
    same rationale as cmd_pull above. `--season` scrapes one season
    (walking back via the gotoseason form if it isn't the current one);
    `--all` walks back one season at a time from the current season to
    2001, stopping as soon as a season has no gotoseason option (that
    league's first season) rather than hard-failing the whole command.
    """
    from app.scrape import season_nav

    config = cfg.load_config()
    sport_path = config.get("yahoo_web_sport_path", cfg.DEFAULT_SPORT_PATH)
    league_id, current_year = _current_scrape_league_and_year(config)
    conn = database.get_connection()
    errors: list[str] = []
    seasons_done: list[int] = []
    try:
        if args.all:
            year = current_year
            while year >= 2001:
                try:
                    resolved = season_nav.resolve_and_cache_season_league_id(config, year, sport_path)
                except Exception as exc:  # noqa: BLE001 - see docstring
                    logger.exception("gotoseason resolution failed for season %s", year)
                    errors.append(f"{year}: {exc}")
                    break
                if resolved is None:
                    print(f"No {year} season found for this league -- stopping backfill.")
                    break
                try:
                    _scrape_one_season(conn, year, resolved["league_id"], base_url=resolved.get("base_url"))
                    seasons_done.append(year)
                except Exception as exc:  # noqa: BLE001 - one bad season shouldn't kill the whole backfill
                    logger.exception("Backfill failed for season %s", year)
                    errors.append(f"{year}: {exc}")
                year -= 1
        else:
            season_year = args.season or current_year
            if season_year == current_year:
                target_league_id, target_base_url = league_id, None
            else:
                resolved = season_nav.resolve_and_cache_season_league_id(config, season_year, sport_path)
                if resolved is None:
                    print(f"No {season_year} season found for this league.")
                    sys.exit(1)
                target_league_id, target_base_url = resolved["league_id"], resolved.get("base_url")
            _scrape_one_season(conn, season_year, target_league_id, base_url=target_base_url)
            seasons_done.append(season_year)
    finally:
        conn.close()

    result = {"status": "ok" if not errors else "partial", "errors": errors, "seasons": seasons_done}
    print(result)
    if not seasons_done and errors:
        sys.exit(1)


def cmd_scrape_auth(_args: argparse.Namespace) -> None:
    """One-time interactive login: opens a real browser window for the
    user to log into Yahoo (including any 2FA), then saves the session to
    data/browser_state.json for every future headless scrape to reuse.
    Mirrors cmd_auth's messaging conventions, but for the browser-scraping
    path instead of OAuth.
    """
    from app.scrape import browser

    print("Opening a browser window for a one-time interactive Yahoo login...")
    print("(If this is the first run, you may need `playwright install chromium` first.)\n")
    browser.launch_persistent_session()

    config = cfg.load_config()
    if not config.get("yahoo_web_league_id") or not config.get("yahoo_web_current_season_year"):
        print("\nOne more one-time step: edit data/config.json and set:")
        print('  "yahoo_web_league_id": "<the numeric id in your league\'s URL, e.g. the 74647')
        print('                          in baseball.fantasysports.yahoo.com/b1/74647/...>"')
        print('  "yahoo_web_current_season_year": <the season that id currently points to, e.g. 2026>')
        print("Then run: python -m app scrape-season <year>   (or --all-seasons)")
    else:
        print("\nNext steps:")
        print("  python -m app scrape-season --all-seasons   # recover full history")
        print("  python -m app pull                          # one manual pull of the current season")


def cmd_scrape_season(args: argparse.Namespace) -> None:
    from app.scrape import season_nav

    config = cfg.load_config()
    sport_path = config.get("yahoo_web_sport_path", "b1")
    league_id, current_year = _current_scrape_league_and_year(config)
    conn = database.get_connection()
    try:
        if args.all_seasons:
            year = args.year if args.year is not None else current_year
            results: dict[int, Any] = {}
            errors: list[str] = []
            while year >= 2001:
                try:
                    resolved = season_nav.resolve_and_cache_season_league_id(config, year, sport_path)
                except Exception as exc:  # noqa: BLE001 - a bad season shouldn't kill the whole walk
                    logger.exception("gotoseason resolution failed for season %s", year)
                    errors.append(f"{year}: {exc}")
                    break
                if resolved is None:
                    print(f"No {year} season found for this league -- stopping walk-back.")
                    break
                print(f"Scraping season {year} (league_id={resolved['league_id']}, base_url={resolved['base_url']})...")
                try:
                    results[year] = _scrape_one_season(
                        conn, year, resolved["league_id"], base_url=resolved.get("base_url")
                    )
                    print(f"  {year}: {results[year]}")
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Scrape failed for season %s", year)
                    errors.append(f"{year}: {exc}")
                    print(f"  {year}: FAILED -- {exc}")
                year -= 1
                # Pacing, not a network fix: Yahoo has been observed to
                # time out under sustained back-to-back scraping. Pausing
                # for a human to confirm between seasons (default on --
                # pass --no-pause to run unattended) gives it room to
                # recover rather than hammering it season after season.
                if year >= 2001 and not args.no_pause:
                    answer = input(
                        f"Continue to season {year}? [Enter = yes, q = stop here] "
                    ).strip().lower()
                    if answer.startswith("q") or answer.startswith("n"):
                        print("Stopping at your request.")
                        break
            print({"seasons": sorted(results), "errors": errors})
            if not results and errors:
                sys.exit(1)
        else:
            if args.year is None:
                print("Provide a season year, or pass --all-seasons.")
                sys.exit(1)
            if args.year == current_year:
                target_league_id, target_base_url = league_id, None
            else:
                resolved = season_nav.resolve_and_cache_season_league_id(config, args.year, sport_path)
                if resolved is None:
                    print(f"No {args.year} season found for this league.")
                    sys.exit(1)
                target_league_id, target_base_url = resolved["league_id"], resolved.get("base_url")
            result = _scrape_one_season(conn, args.year, target_league_id, base_url=target_base_url)
            print(result)
    finally:
        conn.close()


def cmd_scrape_daily_stats(args: argparse.Namespace) -> None:
    """Backfills day-by-day stat history for the CURRENT season via the
    per-team `?date=YYYY-MM-DD` "date hack" (each team page's "Starting
    Lineup Total(s)" row -- see app/scrape/parse.py's
    parse_team_daily_totals), writing into the same team_daily_stat_deltas
    table the dormant API era used, so a chart spanning the cutover date
    is continuous rather than showing a gap.

    Resumable and idempotent by default: with no --since, starts the day
    after whatever's already in team_daily_stat_deltas for this season
    (the API got this league through roughly 2026-07-21 before Yahoo
    revoked access -- this just fills the gap from there forward), or the
    season's start_date if nothing's there yet. --since/--until override
    either end explicitly.
    """
    from app.scrape import jobs as scrape_jobs

    config = cfg.load_config()
    league_id, season_year = _current_scrape_league_and_year(config)
    conn = database.get_connection()
    try:
        if args.since:
            start_date = args.since
        else:
            covered = database.stored_daily_stat_delta_dates(conn, season_year)
            if covered:
                start_date = (dt.date.fromisoformat(max(covered)) + dt.timedelta(days=1)).isoformat()
            else:
                season_row = conn.execute(
                    "SELECT start_date FROM seasons WHERE season_year = ?", (season_year,)
                ).fetchone()
                start_date = season_row["start_date"] if season_row and season_row["start_date"] else None
                if not start_date:
                    print(
                        "No prior daily stats on file and no season start_date known -- "
                        "pass --since YYYY-MM-DD to say where to start."
                    )
                    sys.exit(1)
        end_date = args.until or dt.date.today().isoformat()

        if start_date > end_date:
            print(f"Nothing to do: start_date {start_date} is after end_date {end_date}.")
            return

        print(f"Backfilling daily stats for season {season_year}, {start_date} through {end_date}...")
        result = scrape_jobs.scrape_backfill_daily_stats(conn, season_year, league_id, start_date, end_date)
        print(result)
        if result["errors"]:
            sys.exit(1)
    finally:
        conn.close()


def cmd_fix_transaction_timestamps(_args: argparse.Namespace) -> None:
    """One-time, fully offline repair for transactions written before
    `timestamp` was parsed from the scraped display text (every scraped
    transaction before that fix -- see app/scrape/jobs.py's
    _parse_timestamp_text docstring). Recomputes directly from `raw_json`,
    already in the database -- no re-scraping needed.
    """
    from app.scrape import jobs as scrape_jobs

    conn = database.get_connection()
    try:
        result = scrape_jobs.backfill_transaction_timestamps(conn)
        print(result)
    finally:
        conn.close()


def cmd_scrape_roster_snapshot(args: argparse.Namespace) -> None:
    """Captures a league-wide roster snapshot (which players are on
    which manager's roster) for one date, default today -- see
    app/db/schema.sql's roster_snapshots comment for why: keeper
    eligibility typically depends on a player being on the SAME
    manager's roster on two specific dates. Run this again near the end
    of the season, then use `python -m app keeper-eligibility` to compare.
    """
    from app.scrape import jobs as scrape_jobs

    config = cfg.load_config()
    league_id, season_year = _current_scrape_league_and_year(config)
    conn = database.get_connection()
    try:
        result = scrape_jobs.scrape_roster_snapshot(conn, season_year, league_id, snapshot_date=args.date)
        print(result)
    finally:
        conn.close()


def cmd_keeper_eligibility(args: argparse.Namespace) -> None:
    """Compares two roster snapshots (see cmd_scrape_roster_snapshot) and
    prints, per team, every player who was on that SAME manager's roster
    on BOTH dates -- the standard keeper-eligibility rule this was built
    for ("must have been on a manager's roster on [date] and the last day
    of the season")."""
    config = cfg.load_config()
    season_year = config.get("yahoo_web_current_season_year")
    conn = database.get_connection()
    try:
        available = {
            r["snapshot_date"]
            for r in conn.execute(
                "SELECT DISTINCT snapshot_date FROM roster_snapshots WHERE season_year = ?", (season_year,)
            ).fetchall()
        }
        missing = [d for d in (args.start, args.end) if d not in available]
        if missing:
            print(f"No roster snapshot for: {', '.join(missing)}. Available dates: {sorted(available)}")
            sys.exit(1)

        rows = conn.execute(
            """
            SELECT COALESCE(t.name, rs1.team_key) AS team_name, rs1.player_name
            FROM roster_snapshots rs1
            JOIN roster_snapshots rs2
                ON rs2.player_key = rs1.player_key AND rs2.team_key = rs1.team_key
                AND rs2.season_year = rs1.season_year AND rs2.snapshot_date = ?
            LEFT JOIN teams t ON t.team_key = rs1.team_key AND t.season_year = rs1.season_year
            WHERE rs1.snapshot_date = ? AND rs1.season_year = ?
            ORDER BY team_name, rs1.player_name
            """,
            (args.end, args.start, season_year),
        ).fetchall()

        by_team: dict[str, list[str]] = {}
        for r in rows:
            by_team.setdefault(r["team_name"], []).append(r["player_name"])
        for team, players in by_team.items():
            print(f"\n{team} -- {len(players)} eligible:")
            for p in players:
                print(f"  {p}")
    finally:
        conn.close()


def cmd_serve(args: argparse.Namespace) -> None:
    from app.web.server import run_server

    run_server(host=args.host, port=args.port)


def cmd_status(_args: argparse.Namespace) -> None:
    config = cfg.load_config()
    print("-- Browser scraping (app/scrape/) --")
    print(f"Browser session:   {cfg.has_browser_session()} ({cfg.BROWSER_STATE_PATH})")
    print(
        f"League tracked:    {config.get('yahoo_web_league_id')} "
        f"(season {config.get('yahoo_web_current_season_year')}, sport_path={config.get('yahoo_web_sport_path')})"
    )
    print(f"Season slug:       {config.get('yahoo_web_season_slug')}")
    print(f"Cached season ids: {config.get('scraped_season_league_ids')}")
    print()
    print("-- Yahoo API (app/yahoo/, dormant -- see its module docstrings) --")
    print(f"Config dir:      {cfg.DATA_DIR}")
    print(f"Credentials set: {cfg.has_credentials()}")
    print(f"Authenticated:   {cfg.has_tokens()}")
    print(f"League tracked:  {config.get('league_key')} (season {config.get('season_year')})")
    print(f"Prior seasons:   {[p['season_year'] for p in config.get('prior_league_keys', [])]}")

    if not cfg.DB_PATH.exists():
        print("Database:        not created yet (run pull/backfill/serve once)")
        return

    conn = database.get_connection()
    last = database.last_successful_fetch(conn)
    print(f"Last good pull:  {last['run_finished_at'] if last else 'never'}")
    for table in (
        "seasons",
        "teams",
        "matchups",
        "standings_snapshots",
        "team_stat_snapshots",
        "team_daily_stat_deltas",
        "transactions",
    ):
        count = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
        print(f"  {table}: {count} rows")
    conn.close()


def cmd_diagnose(_args: argparse.Namespace) -> None:
    """Prints exactly what's in the DB for the current season's stat
    categories and standings, so a gap (a category with no trend data, an
    empty standings table) can be pinpointed without guessing -- this
    sandbox has no way to hit Yahoo's API directly, so this is how real
    data gets inspected."""
    import json

    config = cfg.load_config()
    season_year = config.get("season_year")
    if not cfg.DB_PATH.exists() or not season_year:
        print("No database/season yet -- run pull/backfill first.")
        return

    conn = database.get_connection()

    print(f"=== stat_categories (season {season_year}) vs. data coverage ===")
    cats = conn.execute(
        "SELECT stat_id, name, display_name, position_type, sort_order, is_display_only "
        "FROM stat_categories WHERE season_year = ? ORDER BY display_order",
        (season_year,),
    ).fetchall()
    if not cats:
        print("  (no stat_categories rows -- settings pull never succeeded)")
    for c in cats:
        delta_row = conn.execute(
            "SELECT COUNT(DISTINCT snapshot_date) AS n, COUNT(*) AS rows "
            "FROM team_daily_stat_deltas WHERE season_year = ? AND stat_id = ?",
            (season_year, c["stat_id"]),
        ).fetchone()
        snap_row = conn.execute(
            "SELECT COUNT(DISTINCT snapshot_date) AS n, COUNT(*) AS rows "
            "FROM team_stat_snapshots WHERE season_year = ? AND stat_id = ?",
            (season_year, c["stat_id"]),
        ).fetchone()
        flag = " <- is_display_only" if c["is_display_only"] else ""
        print(
            f"  [{c['stat_id']:>3}] {c['display_name'] or c['name']:<12} "
            f"pos={c['position_type'] or '-':<2} sort_order={c['sort_order']}  "
            f"deltas: {delta_row['n']} distinct days ({delta_row['rows']} rows)  "
            f"snapshots: {snap_row['n']} distinct days ({snap_row['rows']} rows){flag}"
        )

    print(f"\n=== standings_snapshots (season {season_year}) ===")
    rows = conn.execute(
        "SELECT snapshot_date, team_key, rank, wins, losses, ties, pct, games_back, playoff_seed "
        "FROM standings_snapshots WHERE season_year = ? ORDER BY snapshot_date DESC, rank ASC LIMIT 5",
        (season_year,),
    ).fetchall()
    if not rows:
        print("  (no rows at all)")
    for r in rows:
        print(f"  {dict(r)}")

    print("\n=== raw team_standings shape (most recent league settings/standings pull) ===")
    raw = conn.execute(
        "SELECT body_json FROM raw_responses WHERE endpoint LIKE 'league/%' "
        "AND params LIKE '%standings%' AND season_year = ? ORDER BY fetched_at DESC LIMIT 1",
        (season_year,),
    ).fetchone()
    if not raw:
        print("  (no matching raw response saved)")
    else:
        try:
            body = json.loads(raw["body_json"])
            league_list = body["fantasy_content"]["league"]
            standings_val = None
            for item in league_list[1:]:
                if isinstance(item, dict) and "standings" in item:
                    standings_val = item["standings"]
                    break
            if isinstance(standings_val, list) and standings_val:
                standings_val = standings_val[0]
            teams_node = standings_val.get("teams") if isinstance(standings_val, dict) else None
            first_wrapper = (teams_node or {}).get("0") if isinstance(teams_node, dict) else None
            raw_team = (first_wrapper or {}).get("team") if isinstance(first_wrapper, dict) else None
            if raw_team is None:
                print("  (no 'standings.teams' found in saved response)")
            else:
                merged = parse.merge_named_node(raw_team)
                print("Unflattened team_standings node exactly as Yahoo sent it:")
                print(json.dumps(merged.get("team_standings"), indent=2)[:1500])
                print("\nAfter flatten_field_list (what parse_standings_snapshot actually reads):")
                flattened = parse.flatten_field_list(merged.get("team_standings") or {})
                print(json.dumps(flattened, indent=2)[:1500])
        except Exception as exc:  # noqa: BLE001 - diagnostic only
            print(f"  Failed to parse saved raw response: {exc}")

    other_seasons = [
        r["season_year"]
        for r in conn.execute(
            "SELECT DISTINCT season_year FROM seasons WHERE season_year != ? ORDER BY season_year",
            (season_year,),
        ).fetchall()
    ]
    if other_seasons:
        print(f"\n(Other seasons also present in `seasons` table: {other_seasons})")

    print(f"\n=== draft_picks (season {season_year}) ===")
    draft_count = conn.execute(
        "SELECT COUNT(*) AS c FROM draft_picks WHERE season_year = ?", (season_year,)
    ).fetchone()["c"]
    print(f"  {draft_count} rows")
    if not draft_count:
        print("  (no draft picks -- draftresults pull never succeeded, or backfill hasn't run)")

    print(f"\n=== team budget/moves fields (season {season_year}) ===")
    field_counts = conn.execute(
        "SELECT "
        "  SUM(CASE WHEN faab_balance IS NOT NULL THEN 1 ELSE 0 END) AS faab, "
        "  SUM(CASE WHEN waiver_priority IS NOT NULL THEN 1 ELSE 0 END) AS waiver, "
        "  SUM(CASE WHEN number_of_moves IS NOT NULL THEN 1 ELSE 0 END) AS moves, "
        "  SUM(CASE WHEN number_of_trades IS NOT NULL THEN 1 ELSE 0 END) AS trades, "
        "  COUNT(*) AS total "
        "FROM teams WHERE season_year = ?",
        (season_year,),
    ).fetchone()
    print(
        f"  of {field_counts['total']} teams: faab_balance={field_counts['faab']}  "
        f"waiver_priority={field_counts['waiver']}  number_of_moves={field_counts['moves']}  "
        f"number_of_trades={field_counts['trades']}"
    )

    print(f"\n=== transactions: DB count vs. last raw pull's reported count (season {season_year}) ===")
    db_tx_count = conn.execute(
        "SELECT COUNT(*) AS c FROM transactions WHERE season_year = ?", (season_year,)
    ).fetchone()["c"]
    reported_count = None
    raw_tx = conn.execute(
        "SELECT body_json FROM raw_responses WHERE endpoint LIKE 'league/%/transactions%' "
        "AND season_year = ? ORDER BY fetched_at DESC LIMIT 1",
        (season_year,),
    ).fetchone()
    if raw_tx:
        try:
            tx_body = json.loads(raw_tx["body_json"])
            tx_league_list = tx_body["fantasy_content"]["league"]
            for item in tx_league_list[1:]:
                if isinstance(item, dict) and "transactions" in item:
                    tx_node = item["transactions"]
                    if isinstance(tx_node, list) and tx_node:
                        tx_node = tx_node[0]
                    if isinstance(tx_node, dict):
                        reported_count = _to_int(tx_node.get("count"))
                    break
        except Exception:  # noqa: BLE001 - diagnostic only
            pass
    print(f"  DB rows: {db_tx_count}   last pull's wrapper 'count': {reported_count}")
    if reported_count is not None and db_tx_count < reported_count:
        print("  <- DB has fewer transactions than the wrapper reported; pagination may need attention")

    print(f"\n=== manager identity (season {season_year}) ===")
    manager_rows = conn.execute(
        "SELECT team_key, manager_key, manager_guid, manager_nickname FROM teams "
        "WHERE season_year = ? ORDER BY team_key",
        (season_year,),
    ).fetchall()
    if not manager_rows:
        print("  (no teams rows)")
    for r in manager_rows:
        hidden = str(r["manager_guid"]).strip().lower() == "--hidden--" if r["manager_guid"] else False
        print(
            f"  {r['team_key']:<24} manager_key={r['manager_key'] or '-':<20} "
            f"nickname={r['manager_nickname'] or '-':<15} guid_hidden={hidden}"
        )

    conn.close()


def main() -> None:
    cfg.ensure_data_dir()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(cfg.LOG_DIR / "pull.log"),
        ],
    )

    parser = argparse.ArgumentParser(prog="python -m app", description="Yahoo Fantasy Baseball history tracker")
    sub = parser.add_subparsers(dest="command", required=True)

    p_auth = sub.add_parser("auth", help="One-time Yahoo OAuth setup and league selection")
    p_auth.add_argument(
        "--relogin", action="store_true", help="Force a fresh Yahoo login even if already authenticated"
    )
    p_auth.set_defaults(func=cmd_auth)

    sub.add_parser("pull", help="Run one manual pull now").set_defaults(func=cmd_pull)

    p_backfill = sub.add_parser("backfill", help="Recover full season history")
    p_backfill.add_argument("--all", action="store_true", help="Backfill every known season")
    p_backfill.add_argument("--season", type=int, help="Backfill a single season year")
    p_backfill.set_defaults(func=cmd_backfill)

    sub.add_parser(
        "scrape-auth", help="One-time interactive Yahoo login for browser-based scraping"
    ).set_defaults(func=cmd_scrape_auth)

    p_scrape_season = sub.add_parser(
        "scrape-season", help="Scrape standings/draft results/transactions for one or every season"
    )
    p_scrape_season.add_argument(
        "year",
        type=int,
        nargs="?",
        help="Season year to scrape. With --all-seasons, this is the year to START the "
        "walk-back from instead of the current season (e.g. `scrape-season 2024 "
        "--all-seasons` skips seasons already done and resumes at 2024, walking down to 2001).",
    )
    p_scrape_season.add_argument(
        "--all-seasons", action="store_true", help="Walk back and scrape every season found, to 2001"
    )
    p_scrape_season.add_argument(
        "--no-pause",
        action="store_true",
        help="With --all-seasons, don't prompt between seasons (default pauses for confirmation, "
        "since Yahoo has been observed to time out under sustained back-to-back scraping)",
    )
    p_scrape_season.set_defaults(func=cmd_scrape_season)

    p_scrape_daily = sub.add_parser(
        "scrape-daily-stats",
        help="Backfill day-by-day stat history for the current season via the per-team ?date= page",
    )
    p_scrape_daily.add_argument(
        "--since", help="ISO date (YYYY-MM-DD) to start from. Default: resumes automatically."
    )
    p_scrape_daily.add_argument("--until", help="ISO date (YYYY-MM-DD) to stop at. Default: today.")
    p_scrape_daily.set_defaults(func=cmd_scrape_daily_stats)

    p_fix_tx_ts = sub.add_parser(
        "fix-transaction-timestamps",
        help="One-time offline repair: recompute timestamp for transactions scraped before that fix, from raw_json already on file",
    )
    p_fix_tx_ts.set_defaults(func=cmd_fix_transaction_timestamps)

    p_roster_snap = sub.add_parser(
        "scrape-roster-snapshot",
        help="Capture which players are on which manager's roster today (or a given date), for keeper tracking",
    )
    p_roster_snap.add_argument("--date", help="ISO date (YYYY-MM-DD) to snapshot. Default: today.")
    p_roster_snap.set_defaults(func=cmd_scrape_roster_snapshot)

    p_keeper = sub.add_parser(
        "keeper-eligibility",
        help="Compare two roster snapshots and list, per team, players eligible to keep (on that roster both dates)",
    )
    p_keeper.add_argument("--start", required=True, help="ISO date (YYYY-MM-DD) of the earlier snapshot")
    p_keeper.add_argument("--end", required=True, help="ISO date (YYYY-MM-DD) of the later snapshot")
    p_keeper.set_defaults(func=cmd_keeper_eligibility)

    p_serve = sub.add_parser("serve", help="Run the daily scheduler + dashboard web server")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.set_defaults(func=cmd_serve)

    sub.add_parser("status", help="Show config, auth, and database status").set_defaults(func=cmd_status)

    sub.add_parser(
        "diagnose", help="Print per-category data coverage and raw standings shape for debugging"
    ).set_defaults(func=cmd_diagnose)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
