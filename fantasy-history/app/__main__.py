"""CLI entry point: python -m app <auth|pull|backfill|serve|status>"""
from __future__ import annotations

import argparse
import logging
import sys
import webbrowser

from app import config as cfg
from app.db import database
from app.yahoo import oauth, parse
from app.yahoo.client import YahooClient


def _to_int(value):
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def cmd_auth(_args: argparse.Namespace) -> None:
    config = cfg.load_config()

    if not config.get("client_id") or not config.get("client_secret"):
        print("Create a Yahoo Developer app first: https://developer.yahoo.com/apps/create/")
        print("  - App type: Installed Application / Confidential Client")
        print(f"  - Redirect URI: {config.get('redirect_uri', cfg.DEFAULT_REDIRECT_URI)}")
        print("  - API Permissions: Fantasy Sports (Read)")
        config["client_id"] = input("Client ID (Consumer Key): ").strip()
        config["client_secret"] = input("Client Secret (Consumer Secret): ").strip()
        cfg.save_config(config)

    url = oauth.build_authorize_url(config)
    print(f"\nOpening your browser to authorize this app with Yahoo:\n  {url}\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    print("Log in and click Agree. Yahoo will redirect to a URL that won't load")
    print("(that's expected) -- copy the 'code' value from that URL's address bar.")
    code = input("Paste the code here: ").strip()

    tokens = oauth.exchange_code(config, code)
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
    choice = input(f"Pick a league to track [0-{len(leagues) - 1}]: ").strip()
    selected = leagues[int(choice)]

    config["league_key"] = selected["league_key"]
    config["season_year"] = selected["season"]
    config["game_key"] = selected["game_key"]

    print("\nLooking for prior seasons of this league (Yahoo hides these in the UI)...")
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
    priors: list[dict] = []
    seen = {current_season}
    cursor = league_key
    for _ in range(30):  # generous cap; a real league won't have this many seasons
        try:
            body = client.get(f"league/{cursor}", params={"out": "settings"})
        except Exception:
            break
        league_list = body["fantasy_content"]["league"]
        fields = parse.flatten_field_list(league_list[0])
        renew = fields.get("renew")
        if not renew:
            break
        try:
            prior_body = client.get(f"league/{renew}")
        except Exception:
            break
        prior_fields = parse.flatten_field_list(prior_body["fantasy_content"]["league"][0])
        prior_season = _to_int(prior_fields.get("season"))
        if prior_season is None or prior_season in seen:
            break
        priors.append({"season_year": prior_season, "league_key": renew})
        seen.add(prior_season)
        cursor = renew
    return priors


def cmd_pull(_args: argparse.Namespace) -> None:
    from app.jobs.daily import run_daily_pull

    result = run_daily_pull(kind="manual")
    print(result)
    if result["status"] == "error":
        sys.exit(1)


def cmd_backfill(args: argparse.Namespace) -> None:
    from app.jobs.backfill import run_backfill

    season_year = None if args.all else args.season
    result = run_backfill(season_year=season_year)
    print(result)
    if result["status"] == "error":
        sys.exit(1)


def cmd_serve(args: argparse.Namespace) -> None:
    from app.web.server import run_server

    run_server(host=args.host, port=args.port)


def cmd_status(_args: argparse.Namespace) -> None:
    config = cfg.load_config()
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
    for table in ("seasons", "teams", "matchups", "standings_snapshots", "transactions"):
        count = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
        print(f"  {table}: {count} rows")
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

    sub.add_parser("auth", help="One-time Yahoo OAuth setup and league selection").set_defaults(func=cmd_auth)

    sub.add_parser("pull", help="Run one manual pull now").set_defaults(func=cmd_pull)

    p_backfill = sub.add_parser("backfill", help="Recover full season history")
    p_backfill.add_argument("--all", action="store_true", help="Backfill every known season")
    p_backfill.add_argument("--season", type=int, help="Backfill a single season year")
    p_backfill.set_defaults(func=cmd_backfill)

    p_serve = sub.add_parser("serve", help="Run the daily scheduler + dashboard web server")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.set_defaults(func=cmd_serve)

    sub.add_parser("status", help="Show config, auth, and database status").set_defaults(func=cmd_status)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
