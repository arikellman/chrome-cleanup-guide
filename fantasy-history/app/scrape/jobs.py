"""Orchestrates browser-based scraping into the EXISTING schema tables
(standings_snapshots, team_stat_snapshots, draft_picks, transactions,
transaction_players, teams, stat_categories) -- no new/parallel tables.
This is the scraping-era replacement for app/jobs/daily.py +
app/jobs/backfill.py, which stay in place dormant (see their module
docstrings) in case Yahoo ever restores API access.

Each `scrape_pull_*` function does the network fetch (via
app.scrape.browser, UNVERIFIED LIVE) and then hands off to a matching
`ingest_*` function that does the parsing + identity resolution + DB
writes. The `ingest_*` functions take an HTML string directly and are
what app/tests/test_scrape_parse.py exercises against fixtures, so the
fetch/parse/write split here mirrors app/jobs/daily.py's own style (one
function per pull, each independently retryable) without needing a real
browser to test the parsing+DB-write half.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from app.db import database
from app.scrape import browser, identity, parse

logger = logging.getLogger(__name__)

SPORT_PATH_DEFAULT = "b1"

# Standings-table columns that are display totals, not real Yahoo stat
# categories -- never resolved via identity.resolve_stat_id.
_NON_STAT_COLUMNS = {"Rank", "Team Name", "Total Points", "Pts Change", "league_id", "team_id"}


def _to_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def league_home_url(league_id: str, sport_path: str = SPORT_PATH_DEFAULT) -> str:
    return f"https://baseball.fantasysports.yahoo.com/{sport_path}/{league_id}"


def standings_url(league_id: str, sport_path: str = SPORT_PATH_DEFAULT) -> str:
    return f"{league_home_url(league_id, sport_path)}/standings"


def draftresults_url(league_id: str, sport_path: str = SPORT_PATH_DEFAULT) -> str:
    return f"{league_home_url(league_id, sport_path)}/draftresults"


def transactions_url(
    league_id: str, sport_path: str = SPORT_PATH_DEFAULT, *, count: int = 25, start: int | None = None
) -> str:
    url = f"{league_home_url(league_id, sport_path)}/transactions?transactionsfilter=all&count={count}"
    if start:
        url += f"&start={start}"
    return url


# ---------------------------------------------------------------------
# Standings
# ---------------------------------------------------------------------

def ingest_standings_html(conn, html: str, season_year: int, league_id: str) -> dict[str, Any]:
    """Parses+writes one standings-page scrape. Split out from
    scrape_pull_standings so tests can exercise it against a fixture HTML
    string without a real browser.

    Only the "stats" table's raw per-category totals are written to
    team_stat_snapshots -- the "points" table's per-category roto-point
    values are NOT stored as a second value under the same stat_id (doing
    so would collide with team_stat_snapshots' (snapshot_date, team_key,
    stat_id) primary key, since both tables cover the same category
    names). This matches the existing app.roto module's own design: roto
    points are always recomputed FROM the raw stat totals, never stored
    directly (see app/roto.py's module docstring) -- so a scraped pull
    slots into that same convention. The "points" table is only used here
    for team identity (it has the team-name link; "stats" doesn't) and
    for the day's overall Rank / Total Points.
    """
    parsed = parse.parse_standings_tables(html)
    snapshot_date = dt.date.today().isoformat()

    team_key_by_name: dict[str, str] = {}
    standings_rows = []
    for row in parsed["points"]:
        name = row.get("Team Name")
        team_id = row.get("team_id")
        if not name or team_id is None:
            logger.warning("Skipping points-table row with no team_id/link: %r", row)
            continue
        team_key = identity.resolve_team_key(conn, season_year, league_id, team_id, name=name)
        team_key_by_name[name] = team_key
        standings_rows.append(
            {
                "snapshot_date": snapshot_date,
                "season_year": season_year,
                "team_key": team_key,
                "rank": _to_int(row.get("Rank")),
                "wins": None,
                "losses": None,
                "ties": None,
                "pct": None,
                "games_back": None,
                "points_for": _to_float(row.get("Total Points")),
                "points_against": None,
                "playoff_seed": None,
            }
        )
    database.insert_standings_snapshot(conn, standings_rows)

    stat_rows = []
    col_types = parsed["column_position_types"].get("stats", {})
    for row in parsed["stats"]:
        name = row.get("Team Name")
        team_key = team_key_by_name.get(name)
        if team_key is None:
            # The "stats" table's team-name cell has no link of its own
            # (confirmed) -- only resolvable if the "points" table had a
            # same-named row on this same pull.
            logger.warning("Could not resolve team_key for scraped stats-table team name %r", name)
            continue
        for col_key, value in row.items():
            if col_key in _NON_STAT_COLUMNS:
                continue
            display_name, is_display_only = identity.normalize_stat_column(col_key)
            position_type = col_types.get(col_key)
            stat_id = identity.resolve_stat_id(
                conn,
                season_year,
                display_name,
                position_type,
                is_display_only=1 if is_display_only else 0,
            )
            stat_rows.append(
                {
                    "snapshot_date": snapshot_date,
                    "season_year": season_year,
                    "team_key": team_key,
                    "stat_id": stat_id,
                    "value": value,
                }
            )
    database.insert_team_stat_snapshots(conn, stat_rows)
    return {"teams": len(standings_rows), "stat_rows": len(stat_rows)}


def scrape_pull_standings(conn, season_year: int, league_id: str, sport_path: str = SPORT_PATH_DEFAULT) -> dict[str, Any]:
    html = browser.fetch_page(standings_url(league_id, sport_path), wait_selector="table")
    result = ingest_standings_html(conn, html, season_year, league_id)
    conn.commit()
    return result


# ---------------------------------------------------------------------
# Draft results
# ---------------------------------------------------------------------

def ingest_draft_results_html(conn, html: str, season_year: int, league_id: str) -> dict[str, Any]:
    """Parses+writes one draft-results-page scrape. Draft picks only have
    a team NAME on the scraped page (no link/id -- confirmed), so team
    identity here is resolved by matching that name against whatever
    teams are already on file for this season_year (populated by a
    standings scrape of the same season, expected to always run first --
    see scrape_pull_draft_results). A name with no match is logged and
    skipped rather than guessing at an id.
    """
    picks = parse.parse_draft_results(html)
    name_to_team_key = {
        r["name"]: r["team_key"]
        for r in conn.execute(
            "SELECT name, team_key FROM teams WHERE season_year = ?", (season_year,)
        ).fetchall()
    }
    rows = []
    skipped = 0
    for pick in picks:
        team_key = name_to_team_key.get(pick["team_name"])
        if team_key is None:
            logger.warning(
                "Draft pick round %s pick %s: no team on file named %r for season %s -- skipping",
                pick["round"], pick["pick_in_round"], pick["team_name"], season_year,
            )
            skipped += 1
            continue
        overall_pick = pick["pick_in_round"]
        rows.append(
            {
                "season_year": season_year,
                # Yahoo's own draft_picks.pick (see app/yahoo/parse.py) is
                # the OVERALL pick number, but the scraped page only gives
                # per-round pick numbers -- recompute the overall number
                # from (round, pick_in_round, teams-per-round) so the
                # (season_year, pick) primary key stays meaningful and
                # collision-free across rounds.
                "pick": _overall_pick_number(pick["round"], overall_pick, len(name_to_team_key) or 1),
                "round": pick["round"],
                "team_key": team_key,
                "player_key": f"mlb.p.{pick['player_yahoo_id']}" if pick["player_yahoo_id"] else None,
                "cost": pick.get("cost"),
            }
        )
    database.upsert_draft_picks(conn, rows)
    return {"picks": len(rows), "skipped": skipped}


def _overall_pick_number(round_num: int, pick_in_round: int | None, teams_per_round: int) -> int:
    if pick_in_round is None:
        pick_in_round = 1
    return (round_num - 1) * teams_per_round + pick_in_round


def scrape_pull_draft_results(conn, season_year: int, league_id: str, sport_path: str = SPORT_PATH_DEFAULT) -> dict[str, Any]:
    html = browser.fetch_page(draftresults_url(league_id, sport_path), wait_selector="table")
    result = ingest_draft_results_html(conn, html, season_year, league_id)
    conn.commit()
    return result


# ---------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------

def ingest_transactions_html(conn, html: str, season_year: int, league_id: str) -> dict[str, Any]:
    """Parses+writes one transactions-page scrape (a single page's worth
    of rows -- pagination across pages is scrape_pull_transactions's job,
    this just ingests whatever `parse.parse_transactions` found on one
    HTML string). Returns {"transactions": N, "players": M}.
    """
    txs = parse.parse_transactions(html)
    tx_rows = []
    player_rows = []
    for tx in txs:
        if tx["team_id"] is None:
            logger.warning("Skipping transaction row with no resolvable team link: %r", tx)
            continue
        dest_team_key = identity.resolve_team_key(
            conn, season_year, tx["league_id"] or league_id, tx["team_id"], name=tx["team_name"]
        )
        player_ids = [p["player_yahoo_id"] for p in tx["players"]]
        tx_key = identity.synthesize_transaction_key(
            season_year, tx["team_id"], tx["timestamp_text"], player_ids
        )
        movements = {p["movement"] for p in tx["players"]}
        if "traded" in movements:
            tx_type = "trade"
        elif {"add", "drop"} <= movements:
            tx_type = "add/drop"
        elif "add" in movements:
            tx_type = "add"
        elif "drop" in movements:
            tx_type = "drop"
        else:
            tx_type = None
        tx_rows.append(
            {
                "transaction_key": tx_key,
                "season_year": season_year,
                "type": tx_type,
                "status": "scraped",
                "timestamp": None,  # scraped timestamp is display text only, no epoch -- see raw_json
                "raw_json": tx["timestamp_text"],
            }
        )
        for p in tx["players"]:
            player_key = f"mlb.p.{p['player_yahoo_id']}" if p["player_yahoo_id"] else None
            if player_key is None:
                continue
            movement = p["movement"]
            # Team on the scraped row is always the team that EXECUTED
            # the transaction. For a plain add, that's the destination;
            # for a plain drop, that's the source (going back to waivers/
            # free agency, which has no team_key of its own). For a trade
            # (UNVERIFIED -- see parse._classify_movement docstring, no
            # real trade row was available to confirm this), we can only
            # see one side's team per row, so best-effort treat it as the
            # receiving side.
            source_team_key = dest_team_key if movement == "drop" else None
            dest_for_row = dest_team_key if movement in ("add", "traded") else None
            player_rows.append(
                {
                    "transaction_key": tx_key,
                    "player_key": player_key,
                    "player_name": p["player_name"],
                    "movement": movement,
                    "source_team_key": source_team_key,
                    "dest_team_key": dest_for_row,
                }
            )
    for row in tx_rows:
        database.upsert_transaction(conn, row)
    database.upsert_transaction_players(conn, player_rows)
    return {"transactions": len(tx_rows), "players": len(player_rows)}


def scrape_pull_transactions(
    conn, season_year: int, league_id: str, sport_path: str = SPORT_PATH_DEFAULT, *, max_pages: int = 50
) -> dict[str, Any]:
    """Fetches and ingests every transactions page for this league.

    Pagination mechanism -- UNVERIFIED LIVE, decision tree in priority
    order (each step's assumption documented since none of it could be
    exercised against a real Yahoo session in this sandbox):

      1. Try `count=1000` directly in the URL. If Yahoo just honors a
         bigger `count=` the way it does for the API's `teams/stats`
         path (confirmed there, NOT confirmed for this HTML page), this
         single fetch returns everything and we're done. Detected by
         checking whether the returned page's row count exceeds 25 (the
         page's own default/observed page size).
      2. If (1) didn't yield more than 25 rows, try a `start=` offset
         (`count=25&start=25` for page 2, etc.), comparing each page's
         first row against the previous page's to confirm Yahoo is
         actually honoring the offset (rather than silently re-serving
         page 1). This mirrors the same semicolon-path convention
         app/jobs/daily.py already confirmed works for the API's
         `transactions;count=25;start={offset}` -- but that was the JSON
         API, and this is a `?query=string` HTML page, so it is NOT
         assumed to carry over; it's tried, not trusted.
      3. If neither URL-param trick changes the returned rows, fall back
         to app.scrape.browser.paginate_by_click, which drives a real
         click on the page's "Next 25" link and re-scrapes after each
         click -- confirmed necessary at least as a fallback, since the
         saved sample page's "Next 25" link's href was IDENTICAL to the
         current page's URL (no incrementing `start=` visible anywhere),
         strongly suggesting this page is AJAX-driven rather than a
         plain paginated GET.

    Stops fetching more pages once a page comes back with zero
    transactions (end of history) or `max_pages` is hit.
    """
    first_html = browser.fetch_page(
        transactions_url(league_id, sport_path, count=1000), wait_selector=".Tst-transaction-table"
    )
    first_rows = parse.parse_transactions(first_html)

    all_html: list[str] = []
    if len(first_rows) > 25:
        # Step 1 worked: one big fetch had everything.
        all_html = [first_html]
    else:
        page1_html = browser.fetch_page(
            transactions_url(league_id, sport_path, count=25), wait_selector=".Tst-transaction-table"
        )
        page1_rows = parse.parse_transactions(page1_html)
        page2_html = browser.fetch_page(
            transactions_url(league_id, sport_path, count=25, start=25), wait_selector=".Tst-transaction-table"
        )
        page2_rows = parse.parse_transactions(page2_html)
        page1_first_ids = {p["player_yahoo_id"] for tx in page1_rows[:1] for p in tx["players"]}
        page2_first_ids = {p["player_yahoo_id"] for tx in page2_rows[:1] for p in tx["players"]}
        if page2_rows and page1_first_ids != page2_first_ids:
            # Step 2 worked: start= is honored. Keep paging with it.
            all_html = [page1_html, page2_html]
            start = 50
            while len(all_html) < max_pages:
                page_html = browser.fetch_page(
                    transactions_url(league_id, sport_path, count=25, start=start),
                    wait_selector=".Tst-transaction-table",
                )
                if not parse.parse_transactions(page_html):
                    break
                all_html.append(page_html)
                start += 25
        else:
            # Step 3: neither URL trick worked -- drive real clicks.
            all_html = browser.paginate_by_click(
                transactions_url(league_id, sport_path, count=25),
                row_selector=".Tst-transaction-table tr",
                max_pages=max_pages,
            )

    totals = {"transactions": 0, "players": 0}
    for html in all_html:
        result = ingest_transactions_html(conn, html, season_year, league_id)
        totals["transactions"] += result["transactions"]
        totals["players"] += result["players"]
    conn.commit()
    return totals
