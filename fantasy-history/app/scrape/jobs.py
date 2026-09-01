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
import time
from typing import Any

from app.db import database
from app.scrape import browser, identity, parse

logger = logging.getLogger(__name__)

SPORT_PATH_DEFAULT = "b1"

# Standings-table columns that are display totals, not real Yahoo stat
# categories -- never resolved via identity.resolve_stat_id.
_NON_STAT_COLUMNS = {"Rank", "Team Name", "Total Points", "Pts Change", "league_id", "team_id"}


def _rows(cursor) -> list[dict[str, Any]]:
    return [dict(r) for r in cursor.fetchall()]


def _to_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        # Confirmed real: some seasons' Rank column renders as "1." (with
        # a trailing period) rather than a bare "1" -- int() rejects that
        # outright, which silently dropped every rank to None rather than
        # raising anywhere visible.
        return int(str(value).replace(",", "").rstrip("."))
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _parse_timestamp_text(timestamp_text: str | None, season_year: int) -> int | None:
    """Parses Yahoo's scraped transaction timestamp text ("Aug 7, 11:41
    am", no year -- the season_year is used instead, safe since an MLB
    season never crosses a calendar year boundary) into a Unix epoch int.

    Confirmed real and important: without this, every scraped
    transaction's `timestamp` column was left NULL, and
    app/web/server.py's `/api/transactions` sorts by `ORDER BY tr.timestamp
    DESC` -- SQLite sorts NULLs as the smallest possible value, so in DESC
    order every NULL-timestamp (i.e. every scraped) row sorted BELOW every
    real-timestamped (API-era, pre-cutover) row, no matter how recent.
    From the dashboard this looked exactly like "no transactions since
    [the API cutover date]", when the data was actually there the whole
    time -- just sorted to the bottom and never shown.
    """
    if not timestamp_text:
        return None
    try:
        parsed = dt.datetime.strptime(f"{timestamp_text} {season_year}", "%b %d, %I:%M %p %Y")
        return int(parsed.replace(tzinfo=dt.timezone.utc).timestamp())
    except ValueError:
        logger.warning("Could not parse transaction timestamp text %r", timestamp_text)
        return None


def league_home_url(league_id: str, sport_path: str = SPORT_PATH_DEFAULT, *, base_url: str | None = None) -> str:
    """Builds the league's base URL. Pass `base_url` (from
    app.scrape.season_nav.resolve_and_cache_season_league_id) for any
    season OTHER than the currently-configured one -- confirmed against a
    real live gotoseason walk-back that historical seasons can carry a
    year path segment (".../2005/b1/4256") that this f"{sport_path}/
    {league_id}" template alone would silently drop, producing a URL for
    the wrong season's data (or a 404). Without `base_url`, this always
    builds the no-year-prefix current-season pattern -- correct ONLY for
    the season in `config["yahoo_web_current_season_year"]`.
    """
    if base_url:
        return base_url
    return f"https://baseball.fantasysports.yahoo.com/{sport_path}/{league_id}"


def standings_url(league_id: str, sport_path: str = SPORT_PATH_DEFAULT, *, base_url: str | None = None) -> str:
    return f"{league_home_url(league_id, sport_path, base_url=base_url)}/standings"


def draftresults_url(league_id: str, sport_path: str = SPORT_PATH_DEFAULT, *, base_url: str | None = None) -> str:
    return f"{league_home_url(league_id, sport_path, base_url=base_url)}/draftresults"


def transactions_url(
    league_id: str,
    sport_path: str = SPORT_PATH_DEFAULT,
    *,
    count: int = 25,
    start: int | None = None,
    base_url: str | None = None,
) -> str:
    """Builds a `?transactionsfilter=all&count=...` transactions URL.

    CONFIRMED LIVE (directly from the user's own logged-in session, not a
    guess): this URL shape -- including this function's own `count=25`
    default with no `start` -- is Yahoo's SECOND page of transactions, not
    the first. The true first/freshest page is the bare URL with no query
    string at all (see `transactions_first_page_url`). scrape_pull_transactions
    below no longer uses this function to seed its pagination for exactly
    that reason; it's kept only in case some other caller wants a specific
    `count=`/`start=` combination directly.
    """
    url = f"{league_home_url(league_id, sport_path, base_url=base_url)}/transactions?transactionsfilter=all&count={count}"
    if start:
        url += f"&start={start}"
    return url


def transactions_first_page_url(
    league_id: str, sport_path: str = SPORT_PATH_DEFAULT, *, base_url: str | None = None
) -> str:
    """The TRUE first/freshest page of the league's transactions list: the
    bare URL with no query string.

    CONFIRMED LIVE by the user directly, from their own logged-in browser:
    the bare URL showed entries through "Sep 1, 11:33 am" while
    `transactions_url(..., count=25)` -- what scrape_pull_transactions
    previously started from, believing it to be page 1 -- was actually one
    page further back in time (Yahoo's page 2). Every prior scrape
    therefore permanently skipped the newest ~25 transactions on each pull.
    """
    return f"{league_home_url(league_id, sport_path, base_url=base_url)}/transactions"


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

    # The dashboard's season picker (and every /api/* endpoint's "latest
    # season" default) reads from the `seasons` table, not from the
    # presence of teams/standings rows -- confirmed real: scraping wrote
    # a full season's worth of teams/standings/stats/draft/transactions
    # data without ever touching `seasons`, so none of it was reachable
    # from the dashboard at all. game_key/league_key are the (dormant)
    # API's own identifiers with no scraped equivalent -- synthesized
    # here so the NOT NULL/UNIQUE constraints are satisfied without
    # colliding with any real API-era value for this or another season.
    # scoring_type is hardcoded "roto" since that's confirmed true for
    # this league across every season scraped so far -- if a scraped
    # league is ever NOT roto-scored throughout its history, this would
    # need to come from the page itself instead. Deliberately only sets
    # the columns above: the CURRENT season may already have real
    # league_name/start_date/end_date/is_finished/etc. from the (dormant)
    # API era, and _upsert only touches columns present in this dict --
    # omitting the rest leaves those existing good values alone instead
    # of clobbering them with NULL on every scrape.
    database.upsert_season(
        conn,
        {
            "season_year": season_year,
            "game_key": "scrape",
            "league_key": f"scrape.{league_id}",
            "num_teams": len(parsed["points"]) or None,
            "scoring_type": "roto",
        },
    )

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


def scrape_pull_standings(
    conn, season_year: int, league_id: str, sport_path: str = SPORT_PATH_DEFAULT, *, base_url: str | None = None
) -> dict[str, Any]:
    html = browser.fetch_page(standings_url(league_id, sport_path, base_url=base_url), wait_selector="table")
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


def scrape_pull_draft_results(
    conn, season_year: int, league_id: str, sport_path: str = SPORT_PATH_DEFAULT, *, base_url: str | None = None
) -> dict[str, Any]:
    html = browser.fetch_page(draftresults_url(league_id, sport_path, base_url=base_url), wait_selector="table")
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
                "timestamp": _parse_timestamp_text(tx["timestamp_text"], season_year),
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
    conn,
    season_year: int,
    league_id: str,
    sport_path: str = SPORT_PATH_DEFAULT,
    *,
    max_pages: int = 50,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Fetches and ingests every transactions page for this league, via
    click-based pagination starting from the TRUE first page (the bare
    URL -- see `transactions_first_page_url`).

    This previously started from `transactions_url(count=25)`, believing
    that to be page 1 because it was the first URL/params combination
    confirmed to render real rows in a live run. It is not: the user
    confirmed directly, from their own logged-in browser, that the bare
    URL (no query string) is page 1, and `?transactionsfilter=all&
    count=25` is actually page 2 -- so every prior pull permanently
    skipped the newest ~25 transactions (matching the "still missing
    recent days" symptom seen even right after the timestamp-sorting fix).

    Also drops the old `count=25`/`start=25` URL-param pagination attempt
    that used to run before falling back to clicking: it was already
    falling through to the click-based path on every real run observed
    (the saved sample page's "Next 25" link href was IDENTICAL to the
    current page's URL, with no incrementing `start=` anywhere -- this
    page is AJAX-driven, not a plain paginated GET), so trying the
    URL-param path first was wasted requests even before the page-1 bug
    was found.

    Stops fetching more pages once a page comes back with zero
    transactions (end of history) or `max_pages` is hit. Waits for an
    actual row (not just the table shell, which is present in the DOM
    almost immediately but stays empty/hidden until populated -- confirmed
    live) via `row_selector=".Tst-transaction-table tr"`.
    """
    all_html = browser.paginate_by_click(
        transactions_first_page_url(league_id, sport_path, base_url=base_url),
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


def backfill_transaction_timestamps(conn) -> dict[str, Any]:
    """One-time repair for rows written before `timestamp` was parsed
    (every scraped transaction before this fix -- see
    _parse_timestamp_text's docstring for the full story). Fully offline:
    `raw_json` already holds the exact display text `timestamp` was always
    meant to be derived from, so this recomputes it directly from what's
    already in the database -- no re-scraping, no network calls, fixes
    every affected row in one pass regardless of whether a future re-scrape
    would ever happen to touch that particular transaction_key again.
    """
    rows = _rows(
        conn.execute("SELECT transaction_key, season_year, raw_json FROM transactions WHERE timestamp IS NULL")
    )
    fixed = 0
    still_unparseable = 0
    for row in rows:
        epoch = _parse_timestamp_text(row["raw_json"], row["season_year"])
        if epoch is None:
            still_unparseable += 1
            continue
        conn.execute(
            "UPDATE transactions SET timestamp = ? WHERE transaction_key = ?", (epoch, row["transaction_key"])
        )
        fixed += 1
    conn.commit()
    return {"checked": len(rows), "fixed": fixed, "still_unparseable": still_unparseable}


# ---------------------------------------------------------------------
# Team page, single-day totals ("date hack" retroactive daily stats)
# ---------------------------------------------------------------------

def team_daily_url(
    team_id: str, game_date: str, league_id: str, sport_path: str = SPORT_PATH_DEFAULT, *, base_url: str | None = None
) -> str:
    return f"{league_home_url(league_id, sport_path, base_url=base_url)}/{team_id}/team?date={game_date}"


def ingest_team_daily_totals_html(
    conn, html: str, season_year: int, team_key: str, game_date: str
) -> dict[str, Any]:
    """Parses+writes one team-page-on-one-date scrape into
    team_daily_stat_deltas -- the SAME (dormant, API-era) table the old
    Yahoo API's type=date team stats populated, so a chart spanning the
    API-to-scraping cutover date sees one continuous line rather than a
    gap or a duplicate category. identity.resolve_stat_id is what makes
    that continuity real: it reuses the season's already-known Yahoo
    stat_ids for these exact (display_name, position_type) pairs rather
    than minting new ones, since the team page's column headers were
    confirmed to already match the existing category names exactly (R,
    HR, RBI, SB, K, OBP, W, SV, HLD, ERA, WHIP, H/AB*, IP*).
    """
    parsed = parse.parse_team_daily_totals(html)
    rows = []
    for kind, position_type in (("batting", "B"), ("pitching", "P")):
        for col_key, value in parsed.get(kind, {}).items():
            display_name, is_display_only = identity.normalize_stat_column(col_key)
            stat_id = identity.resolve_stat_id(
                conn, season_year, display_name, position_type, is_display_only=1 if is_display_only else 0
            )
            rows.append(
                {
                    "snapshot_date": game_date,
                    "season_year": season_year,
                    "team_key": team_key,
                    "stat_id": stat_id,
                    "value": value,
                }
            )
    database.insert_team_daily_stat_deltas(conn, rows)
    return {"stat_rows": len(rows)}


def scrape_pull_team_daily_stats(
    conn,
    season_year: int,
    league_id: str,
    team_id: str,
    team_key: str,
    game_date: str,
    sport_path: str = SPORT_PATH_DEFAULT,
    *,
    base_url: str | None = None,
) -> dict[str, Any]:
    html = browser.fetch_page(
        team_daily_url(team_id, game_date, league_id, sport_path, base_url=base_url),
        wait_selector="#statTable0",
    )
    result = ingest_team_daily_totals_html(conn, html, season_year, team_key, game_date)
    conn.commit()
    return result


def scrape_backfill_daily_stats(
    conn,
    season_year: int,
    league_id: str,
    start_date: str,
    end_date: str,
    sport_path: str = SPORT_PATH_DEFAULT,
    *,
    base_url: str | None = None,
    progress_every: int = 5,
    throttle_seconds: float = 4.0,
    suspected_block_seconds: float = 15.0,
    suspected_block_streak: int = 3,
) -> dict[str, Any]:
    """Walks every day in [start_date, end_date] (inclusive, ISO
    "YYYY-MM-DD" strings) for every team already on file for this
    season, scraping that team's single-day totals via the ?date= "date
    hack".

    Resumable per (team, date) pair, not just per date -- confirmed real
    that a transient failure can hit only one or two teams on an
    otherwise-successful day, and date-only resumability would then skip
    that whole date forever on every future run since it already has SOME
    rows, silently leaving those specific teams' data missing. A failed
    team-day writes no rows at all (ingest is a no-op on an empty parse),
    so it's naturally retried on the next run without needing any special
    "force" flag.

    Throttled: confirmed real that back-to-back fetches with no pause
    between them (a season's worth of team-day pages is potentially
    hundreds of requests) triggers failures partway through a run that
    weren't happening for the first few days -- sleeps throttle_seconds
    between every team-day fetch, same "be polite to Yahoo" spirit as the
    dormant API client's own MIN_REQUEST_INTERVAL throttle. Bumped from
    the original 1.5s to 4.0s after a real live run still got rate
    limited (by Yahoo, at the IP level -- confirmed by the user's own
    separate browser also getting denied) after only about 8 minutes of
    continuous fetching at the old pace.

    Circuit breaker: a genuine "no data for this team-day" timeout is
    rare and isolated -- a real live run showed that once Yahoo starts
    blocking, every subsequent fetch times out the same way (confirmed:
    goto dropped from its normal ~1.5-2.5s down to ~0.4-0.5s, consistent
    with Yahoo serving a small denial page instead of the real one,
    followed immediately by the wait_selector timing out because the
    denial page obviously has no #statTable0). Without this, the loop
    kept hammering Yahoo every ~20s for many more team-days after the
    block started, which can only make the block worse. If
    suspected_block_streak consecutive team-day fetches each take
    longer than suspected_block_seconds (i.e. hit the full
    wait_selector timeout rather than finding real content quickly),
    stop the whole backfill immediately rather than continuing through
    the remaining days -- per-(team, date) resumability means whatever
    was already fetched stays fetched, and re-running later picks up
    exactly where this left off.
    """
    teams = _rows(conn.execute("SELECT team_key, team_id FROM teams WHERE season_year = ?", (season_year,)))
    if not teams:
        raise RuntimeError(
            f"No teams on file for season {season_year} -- run a standings scrape for this season first."
        )
    already = database.stored_daily_stat_delta_team_dates(conn, season_year)

    start = dt.date.fromisoformat(start_date)
    end = dt.date.fromisoformat(end_date)
    total_days = (end - start).days + 1
    processed = 0
    errors: list[str] = []
    consecutive_slow = 0
    blocked = False
    current = start
    while current <= end and not blocked:
        date_str = current.isoformat()
        for team in teams:
            if (team["team_key"], date_str) in already:
                continue
            t_team = time.monotonic()
            try:
                scrape_pull_team_daily_stats(
                    conn, season_year, league_id, team["team_id"], team["team_key"], date_str,
                    sport_path, base_url=base_url,
                )
            except Exception as exc:  # noqa: BLE001 - one bad team-day shouldn't kill the whole backfill
                logger.exception("Daily stats scrape failed for %s on %s", team["team_key"], date_str)
                errors.append(f"{date_str} {team['team_key']}: {exc}")
            elapsed = time.monotonic() - t_team
            if elapsed >= suspected_block_seconds:
                consecutive_slow += 1
                if consecutive_slow >= suspected_block_streak:
                    logger.error(
                        "Daily stats backfill: %s consecutive team-day fetches each took over %.0fs -- "
                        "this matches a Yahoo rate-limit/block response (a fast-loading denial page, not "
                        "the real one), not isolated missing data. Stopping this backfill run now instead "
                        "of continuing to hit Yahoo. Wait a while, then re-run scrape-daily-stats -- it "
                        "will resume from %s %s onward.",
                        consecutive_slow, suspected_block_seconds, date_str, team["team_key"],
                    )
                    errors.append(
                        f"Aborted early at {date_str} {team['team_key']}: "
                        f"{consecutive_slow} consecutive timeouts, likely rate-limited by Yahoo"
                    )
                    blocked = True
                    break
            else:
                consecutive_slow = 0
            time.sleep(throttle_seconds)
        if blocked:
            break
        processed += 1
        if processed % progress_every == 0:
            logger.info("Daily stats backfill: %s/%s days done for season %s", processed, total_days, season_year)
        current += dt.timedelta(days=1)

    return {"days_processed": processed, "errors": errors, "aborted_suspected_block": blocked}


# ---------------------------------------------------------------------
# Roster snapshots (league-wide "All Taken Players" list) -- for
# keeper-eligibility tracking: capture who's on which manager's roster on
# a given date, so a later snapshot on a different date can be compared.
# ---------------------------------------------------------------------

def taken_players_url(
    league_id: str,
    position: str,
    season_year: int,
    sport_path: str = SPORT_PATH_DEFAULT,
    *,
    base_url: str | None = None,
) -> str:
    """`position` is "B" (all batters) or "P" (all pitchers) -- confirmed
    real for "P"; "B" is a reasonable, UNVERIFIED symmetric guess (see
    parse.parse_taken_players_page's docstring)."""
    return (
        f"{league_home_url(league_id, sport_path, base_url=base_url)}/players"
        f"?status=T&pos={position}&stat1=S_S_{season_year}"
    )


def ingest_taken_players_html(conn, html: str, season_year: int, snapshot_date: str) -> dict[str, Any]:
    """Parses+writes one taken-players-page scrape into roster_snapshots.
    Split out from scrape_pull_taken_players so tests can exercise it
    against a fixture HTML string without a real browser."""
    players = parse.parse_taken_players_page(html)
    rows = []
    skipped = 0
    for p in players:
        if p["team_id"] is None or p["player_yahoo_id"] is None:
            logger.warning("Skipping taken-players row with no resolvable team/player id: %r", p)
            skipped += 1
            continue
        team_key = identity.resolve_team_key(
            conn, season_year, p["league_id"], p["team_id"], name=p["team_name"]
        )
        rows.append(
            {
                "snapshot_date": snapshot_date,
                "season_year": season_year,
                "team_key": team_key,
                "player_key": f"mlb.p.{p['player_yahoo_id']}",
                "player_name": p["player_name"],
            }
        )
    database.upsert_roster_snapshot(conn, rows)
    return {"players": len(rows), "skipped": skipped}


def scrape_pull_taken_players(
    conn,
    season_year: int,
    league_id: str,
    position: str,
    snapshot_date: str,
    sport_path: str = SPORT_PATH_DEFAULT,
    *,
    base_url: str | None = None,
    max_pages: int = 50,
) -> dict[str, Any]:
    """Walks every page of the "All Taken Players" list for one position
    ("B" or "P") via click-based pagination -- confirmed real that this
    page's "Next 25" link is AJAX-driven the same way /transactions is
    (no incrementing offset in its href), so this goes straight to the
    click fallback rather than trying a URL-param trick first.
    """
    pages_html = browser.paginate_by_click(
        taken_players_url(league_id, position, season_year, sport_path, base_url=base_url),
        row_selector="td.player",
        max_pages=max_pages,
    )
    totals = {"players": 0, "skipped": 0}
    for html in pages_html:
        result = ingest_taken_players_html(conn, html, season_year, snapshot_date)
        totals["players"] += result["players"]
        totals["skipped"] += result["skipped"]
    conn.commit()
    return totals


def scrape_roster_snapshot(
    conn,
    season_year: int,
    league_id: str,
    snapshot_date: str | None = None,
    sport_path: str = SPORT_PATH_DEFAULT,
    *,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Captures a full league-wide roster snapshot (batters + pitchers)
    for one date (default: today) -- see roster_snapshots' schema.sql
    comment for why: keeper eligibility typically depends on a player
    being on the SAME manager's roster on two specific dates, which this
    snapshot, taken twice months apart, is what makes comparable later
    (see app/__main__.py's cmd_keeper_eligibility).
    """
    snapshot_date = snapshot_date or dt.date.today().isoformat()
    totals = {"players": 0, "skipped": 0}
    for position in ("B", "P"):
        result = scrape_pull_taken_players(
            conn, season_year, league_id, position, snapshot_date, sport_path, base_url=base_url
        )
        totals["players"] += result["players"]
        totals["skipped"] += result["skipped"]
    totals["snapshot_date"] = snapshot_date
    return totals
