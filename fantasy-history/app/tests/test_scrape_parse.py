"""Tests for app/scrape/ -- the browser-scraping replacement for the
(dormant) Yahoo API path. Fixtures in app/tests/fixtures/scrape/ are
hand-crafted to mirror the CONFIRMED real structure of Yahoo's own
standings/draftresults/transactions/league-home pages (see
app/scrape/parse.py and app/scrape/season_nav.py docstrings for exactly
what was validated against the real saved pages during development), not
copies of those multi-MB real files.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
import unittest
import unittest.mock
from pathlib import Path

from app.db import database
from app.scrape import identity, jobs, parse, season_nav

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "scrape"


def load(name: str) -> str:
    with open(FIXTURES / name, encoding="utf-8") as f:
        return f.read()


class TestParseStandings(unittest.TestCase):
    def setUp(self):
        self.html = load("standings.html")
        self.parsed = parse.parse_standings_tables(self.html)

    def test_points_table(self):
        points = self.parsed["points"]
        self.assertEqual(len(points), 2)
        self.assertEqual(points[0]["Rank"], "1")
        self.assertEqual(points[0]["Team Name"], "Prime Time")
        self.assertEqual(points[0]["league_id"], "74647")
        self.assertEqual(points[0]["team_id"], "9")
        self.assertEqual(points[0]["Total Points"], "60.5")
        self.assertEqual(points[0]["Pts Change"], "1.5")

    def test_team_name_and_points_columns_are_position_not_header_text(self):
        # Confirmed real: Yahoo's header for the team-name column varies
        # by season ("Team Name" vs plain "Team"), and the points table's
        # "Total Points" header once rendered as a private-use icon
        # character instead of literal text. Simulate both by renaming
        # the headers but leaving the actual column positions the same --
        # the parser must still recover the right values by position.
        html = self.html.replace("<th>Team Name</th>", "<th>Team</th>", 1).replace(
            "<th>Total Points</th>", "<th></th>", 1
        )
        parsed = parse.parse_standings_tables(html)
        self.assertEqual(parsed["points"][0]["Team Name"], "Prime Time")
        self.assertEqual(parsed["points"][0]["Total Points"], "60.5")
        self.assertEqual(parsed["points"][0]["Pts Change"], "1.5")

    def test_truncated_name_uses_title_attribute(self):
        points = self.parsed["points"]
        self.assertEqual(points[1]["Team Name"], "Team Grimace (Gri-MAH-Chay)")
        self.assertEqual(points[1]["team_id"], "2")

    def test_stats_table_has_no_team_link(self):
        stats = self.parsed["stats"]
        self.assertEqual(len(stats), 2)
        self.assertIsNone(stats[0]["team_id"])
        self.assertEqual(stats[0]["Team Name"], "Prime Time")
        self.assertEqual(stats[0]["R"], "145")

    def test_duplicate_k_columns_disambiguated(self):
        stats = self.parsed["stats"]
        self.assertIn("K", stats[0])
        self.assertIn("K_2", stats[0])
        self.assertEqual(stats[0]["K"], "120")  # batting K
        self.assertEqual(stats[0]["K_2"], "340")  # pitching K

    def test_column_position_types(self):
        types = self.parsed["column_position_types"]["stats"]
        self.assertEqual(types["R"], "B")
        self.assertEqual(types["HR"], "B")
        self.assertEqual(types["K"], "B")
        self.assertEqual(types["IP *"], "P")
        self.assertEqual(types["ERA"], "P")
        self.assertEqual(types["K_2"], "P")
        self.assertIsNone(types.get("Rank"))
        self.assertIsNone(types.get("Team Name"))


class TestParseDraftResults(unittest.TestCase):
    def setUp(self):
        self.picks = parse.parse_draft_results(load("draft_results.html"))

    def test_pick_count_and_rounds(self):
        self.assertEqual(len(self.picks), 3)
        self.assertEqual({p["round"] for p in self.picks}, {1, 2})

    def test_first_pick(self):
        pick = self.picks[0]
        self.assertEqual(pick["round"], 1)
        self.assertEqual(pick["pick_in_round"], 1)
        self.assertEqual(pick["player_yahoo_id"], "9877")
        self.assertEqual(pick["player_name"], "Aaron Judge")
        self.assertEqual(pick["mlb_team"], "NYY")
        self.assertEqual(pick["position"], "OF")
        self.assertEqual(pick["team_name"], "Backcrackers")

    def test_two_way_player_batter_suffix_stripped(self):
        pick = self.picks[1]
        self.assertEqual(pick["player_name"], "Shohei Ohtani")
        self.assertEqual(pick["mlb_team"], "LAD")
        self.assertEqual(pick["position"], "Util")

    def test_truncated_team_name_uses_title(self):
        pick = self.picks[1]
        self.assertEqual(pick["team_name"], "Team Grimace (Gri-MAH-Chay)")

    def test_name_with_period_parses(self):
        pick = self.picks[2]
        self.assertEqual(pick["player_name"], "Bobby Witt Jr.")
        self.assertEqual(pick["mlb_team"], "KC")
        self.assertEqual(pick["position"], "SS")


class TestParseTransactions(unittest.TestCase):
    def setUp(self):
        self.txs = parse.parse_transactions(load("transactions.html"))

    def test_row_count(self):
        self.assertEqual(len(self.txs), 3)

    def test_extension_split_name_recombined_with_space(self):
        # Confirmed real quirk: a browser extension injects markup INSIDE
        # the player-name <a>, splitting text into fragments ("Jung" /
        # "Hoo" / "Lee") that bs4's default get_text(strip=True) would
        # silently mangle into "JungHooLee". get_text(" ", strip=True) is
        # what recovers the correct "Jung Hoo Lee".
        tx = self.txs[0]
        self.assertEqual(tx["players"][0]["player_name"], "Jung Hoo Lee")
        self.assertEqual(tx["players"][0]["player_yahoo_id"], "63494")
        self.assertEqual(tx["players"][0]["movement"], "add")
        self.assertEqual(tx["players"][1]["player_name"], "Dylan Crews")
        self.assertEqual(tx["players"][1]["movement"], "drop")
        self.assertEqual(tx["team_id"], "11")
        self.assertEqual(tx["team_name"], "The Ghost of Elvis Past")
        self.assertEqual(tx["timestamp_text"], "Aug 7, 11:41 am")

    def test_cost_prefix_parsed(self):
        tx = self.txs[1]
        added = tx["players"][0]
        self.assertEqual(added["movement"], "add")
        self.assertEqual(added["cost"], 1)
        dropped = tx["players"][1]
        self.assertEqual(dropped["movement"], "drop")
        self.assertIsNone(dropped["cost"])

    def test_add_only_row_no_drop(self):
        tx = self.txs[2]
        self.assertEqual(len(tx["players"]), 1)
        self.assertEqual(tx["players"][0]["player_name"], "Tanner Bibee")
        self.assertEqual(tx["players"][0]["cost"], 2)

    def test_position_and_mlb_team_split(self):
        tx = self.txs[0]
        self.assertEqual(tx["players"][0]["mlb_team"], "SF")
        self.assertEqual(tx["players"][0]["position"], "OF")


class TestParseTeamDailyTotals(unittest.TestCase):
    def setUp(self):
        self.parsed = parse.parse_team_daily_totals(load("team_daily.html"))

    def test_batting_totals_from_starting_lineup_row(self):
        batting = self.parsed["batting"]
        self.assertEqual(batting["R"], "1")
        self.assertEqual(batting["HR"], "0")
        self.assertEqual(batting["RBI"], "1")
        self.assertEqual(batting["OBP"], ".333")
        self.assertEqual(batting["H/AB*"], "1/3")

    def test_bench_player_excluded_from_totals(self):
        # Confirmed real: the totals row already excludes bench/IL
        # players -- the fixture's bench player has very different stats
        # (HR 2, K 0) from the totals row (HR 0, K 1), so this fails loudly
        # if the parser ever starts summing every roster row itself
        # instead of reading Yahoo's own totals row.
        batting = self.parsed["batting"]
        self.assertEqual(batting["K"], "1")
        self.assertEqual(batting["HR"], "0")

    def test_pitching_totals(self):
        pitching = self.parsed["pitching"]
        self.assertEqual(pitching["W"], "1")
        self.assertEqual(pitching["K"], "7")
        self.assertEqual(pitching["ERA"], "2.00")
        self.assertEqual(pitching["WHIP"], "1.10")
        self.assertEqual(pitching["IP*"], "6.0")

    def test_roster_metadata_columns_excluded(self):
        for col in ("Pos", "Batters", "Opp", "Pre-Season", "% Start", "% Ros"):
            self.assertNotIn(col, self.parsed["batting"])


class TestSeasonNav(unittest.TestCase):
    def setUp(self):
        self.html = load("league_home.html")

    def test_extract_season_slug(self):
        slug, options = season_nav.extract_season_slug(self.html)
        self.assertEqual(slug, "kippahs")
        self.assertEqual(options[2026], "2026_kippahs")
        self.assertEqual(options[2001], "2001_kippahs")

    def test_gotoseason_form_action(self):
        action = season_nav.gotoseason_form_action(self.html)
        self.assertEqual(action, "https://baseball.fantasysports.yahoo.com/b1/74647/gotoseason")

    def test_league_id_from_url(self):
        self.assertEqual(
            season_nav.league_id_from_url("https://baseball.fantasysports.yahoo.com/b1/12345/standings"),
            "12345",
        )
        self.assertEqual(
            season_nav.league_id_from_url("https://baseball.fantasysports.yahoo.com/b1/12345"), "12345"
        )
        self.assertIsNone(season_nav.league_id_from_url("https://login.yahoo.com/whatever"))

    def test_missing_select_raises(self):
        with self.assertRaises(ValueError):
            season_nav.extract_season_slug("<html><body>no select here</body></html>")

    def test_base_url_from_redirect_current_season_no_year_prefix(self):
        self.assertEqual(
            season_nav.base_url_from_redirect("https://baseball.fantasysports.yahoo.com/b1/74647/standings"),
            "https://baseball.fantasysports.yahoo.com/b1/74647",
        )

    def test_base_url_from_redirect_historical_season_year_prefix(self):
        # Confirmed against a real live gotoseason walk-back: historical
        # seasons redirect to a URL with the year as its own path segment
        # immediately before "/b1/" (e.g. 2005), unlike the current
        # season's URL (no year segment at all).
        self.assertEqual(
            season_nav.base_url_from_redirect("https://baseball.fantasysports.yahoo.com/2005/b1/4256/standings"),
            "https://baseball.fantasysports.yahoo.com/2005/b1/4256",
        )

    def test_base_url_from_redirect_no_match(self):
        self.assertIsNone(season_nav.base_url_from_redirect("https://login.yahoo.com/whatever"))

    def test_resolve_and_cache_self_heals_legacy_bare_string_cache_entry(self):
        # Confirmed against a real data/config.json left over from before
        # base_url was added to the cache: a bare league_id string under
        # scraped_season_league_ids (this file is gitignored, so old
        # cached state can outlive whatever code wrote it) used to crash
        # every caller expecting a dict.
        config = {
            "yahoo_web_league_id": "74647",
            "yahoo_web_current_season_year": 2026,
            "scraped_season_league_ids": {"2026": "74647"},
        }
        # Patch out the real file write -- resolve_and_cache_season_league_id
        # calls cfg.save_config(config) unconditionally on a self-heal, and
        # that writes to the real data/config.json on disk; a unit test
        # must never touch a real user's config file.
        with unittest.mock.patch("app.scrape.season_nav.cfg.save_config"):
            result = season_nav.resolve_and_cache_season_league_id(config, 2026, "b1")
        self.assertEqual(
            result, {"league_id": "74647", "base_url": "https://baseball.fantasysports.yahoo.com/b1/74647"}
        )
        # Self-healed in place, so a second call doesn't re-migrate.
        self.assertEqual(config["scraped_season_league_ids"]["2026"], result)

    def test_resolve_and_cache_self_heals_historical_season_with_sport_path_override(self):
        # Confirmed against a real user-supplied config.json: a user can
        # populate scraped_season_league_ids by hand from their own
        # browsing (bare league_id strings for many past seasons), for a
        # season whose sport path differs from the league's usual one
        # (confirmed real: 2007-2009 use "b2" for this league). The
        # self-heal must build the year-prefixed form (this ISN'T the
        # current season) using that season's sport-path override, not
        # assume "b1" or the no-year-prefix current-season form.
        config = {
            "yahoo_web_league_id": "74647",
            "yahoo_web_current_season_year": 2026,
            "yahoo_web_sport_path": "b1",
            "scraped_season_league_ids": {"2007": "3228"},
            "scraped_season_sport_paths": {"2007": "b2"},
        }
        with unittest.mock.patch("app.scrape.season_nav.cfg.save_config"):
            result = season_nav.resolve_and_cache_season_league_id(config, 2007, "b1")
        self.assertEqual(
            result,
            {"league_id": "3228", "base_url": "https://baseball.fantasysports.yahoo.com/2007/b2/3228"},
        )

    def test_sport_path_for_season_override_and_default(self):
        config = {"yahoo_web_sport_path": "b1", "scraped_season_sport_paths": {"2008": "b2"}}
        self.assertEqual(season_nav.sport_path_for_season(config, 2008, "b1"), "b2")
        self.assertEqual(season_nav.sport_path_for_season(config, 2020, "b1"), "b1")

    def test_league_id_from_url_matches_non_b1_sport_path(self):
        self.assertEqual(
            season_nav.league_id_from_url("https://baseball.fantasysports.yahoo.com/2008/b2/6520/standings"),
            "6520",
        )


class TestIdentity(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        database.init_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_normalize_stat_column(self):
        self.assertEqual(identity.normalize_stat_column("GP *"), ("GP", True))
        self.assertEqual(identity.normalize_stat_column("IP *"), ("IP", True))
        self.assertEqual(identity.normalize_stat_column("K_2"), ("K", False))
        self.assertEqual(identity.normalize_stat_column("HR"), ("HR", False))

    def test_resolve_team_key_reuses_existing_api_era_row(self):
        database.upsert_teams(
            self.conn,
            [
                {
                    "season_year": 2026,
                    "team_key": "469.l.74647.t.9",
                    "team_id": "9",
                    "name": "Prime Time",
                    "logo_url": None,
                    "manager_nickname": None,
                    "manager_guid": None,
                    "manager_key": None,
                    "division_id": None,
                    "faab_balance": None,
                    "waiver_priority": None,
                    "number_of_moves": None,
                    "number_of_trades": None,
                }
            ],
        )
        key = identity.resolve_team_key(self.conn, 2026, "74647", "9")
        self.assertEqual(key, "469.l.74647.t.9")

    def test_resolve_team_key_synthesizes_for_unseen_season(self):
        key = identity.resolve_team_key(self.conn, 2015, "74647", "3", name="Birds & the Beezers")
        self.assertEqual(key, "74647.t.3")
        row = self.conn.execute(
            "SELECT name FROM teams WHERE team_key = ?", (key,)
        ).fetchone()
        self.assertEqual(row["name"], "Birds & the Beezers")

    def test_resolve_team_key_is_idempotent(self):
        key1 = identity.resolve_team_key(self.conn, 2015, "74647", "3", name="Birds & the Beezers")
        key2 = identity.resolve_team_key(self.conn, 2015, "74647", "3", name="Birds & the Beezers")
        self.assertEqual(key1, key2)
        count = self.conn.execute("SELECT COUNT(*) AS c FROM teams WHERE team_key = ?", (key1,)).fetchone()["c"]
        self.assertEqual(count, 1)

    def test_resolve_stat_id_reuses_existing_api_era_row(self):
        database.upsert_stat_categories(
            self.conn,
            [
                {
                    "season_year": 2026,
                    "stat_id": 7,
                    "name": "R",
                    "display_name": "R",
                    "sort_order": 1,
                    "display_order": 0,
                    "is_display_only": 0,
                    "position_type": "B",
                }
            ],
        )
        stat_id = identity.resolve_stat_id(self.conn, 2026, "R", "B")
        self.assertEqual(stat_id, 7)

    def test_resolve_stat_id_synthesizes_starting_at_9000(self):
        stat_id = identity.resolve_stat_id(self.conn, 2015, "HR", "B")
        self.assertEqual(stat_id, 9000)
        row = self.conn.execute(
            "SELECT sort_order, position_type FROM stat_categories WHERE season_year = ? AND stat_id = ?",
            (2015, stat_id),
        ).fetchone()
        self.assertEqual(row["position_type"], "B")
        self.assertEqual(row["sort_order"], 1)

    def test_resolve_stat_id_increments_and_is_idempotent(self):
        first = identity.resolve_stat_id(self.conn, 2015, "HR", "B")
        second = identity.resolve_stat_id(self.conn, 2015, "RBI", "B")
        self.assertEqual(second, first + 1)
        # Re-resolving the same (display_name, position_type) reuses it,
        # doesn't allocate a new id.
        again = identity.resolve_stat_id(self.conn, 2015, "HR", "B")
        self.assertEqual(again, first)

    def test_resolve_stat_id_lower_is_better_categories(self):
        era_id = identity.resolve_stat_id(self.conn, 2015, "ERA", "P")
        batting_k_id = identity.resolve_stat_id(self.conn, 2015, "K", "B")
        pitching_k_id = identity.resolve_stat_id(self.conn, 2015, "K", "P")
        era_row = self.conn.execute(
            "SELECT sort_order FROM stat_categories WHERE season_year = ? AND stat_id = ?", (2015, era_id)
        ).fetchone()
        batting_k_row = self.conn.execute(
            "SELECT sort_order FROM stat_categories WHERE season_year = ? AND stat_id = ?",
            (2015, batting_k_id),
        ).fetchone()
        pitching_k_row = self.conn.execute(
            "SELECT sort_order FROM stat_categories WHERE season_year = ? AND stat_id = ?",
            (2015, pitching_k_id),
        ).fetchone()
        self.assertEqual(era_row["sort_order"], 0)
        self.assertEqual(batting_k_row["sort_order"], 0)
        self.assertEqual(pitching_k_row["sort_order"], 1)

    def test_synthesize_transaction_key_deterministic_and_order_independent(self):
        key1 = identity.synthesize_transaction_key(2026, "11", "Aug 7, 11:41 am", ["63494", "62971"])
        key2 = identity.synthesize_transaction_key(2026, "11", "Aug 7, 11:41 am", ["62971", "63494"])
        self.assertEqual(key1, key2)
        key3 = identity.synthesize_transaction_key(2026, "7", "Aug 7, 11:41 am", ["10843", "11914"])
        self.assertNotEqual(key1, key3)


class TestIngestStandings(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        database.init_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_ingest_writes_teams_standings_and_stats(self):
        result = jobs.ingest_standings_html(self.conn, load("standings.html"), 2015, "74647")
        self.assertEqual(result["teams"], 2)

        teams = self.conn.execute("SELECT team_id, name, team_key FROM teams WHERE season_year = ? ORDER BY team_id", (2015,)).fetchall()
        self.assertEqual(len(teams), 2)
        team_ids = {t["team_id"] for t in teams}
        self.assertEqual(team_ids, {"9", "2"})

        snap = self.conn.execute(
            "SELECT rank, points_for FROM standings_snapshots WHERE season_year = ? AND team_key = ?",
            (2015, "74647.t.9"),
        ).fetchone()
        self.assertEqual(snap["rank"], 1)
        self.assertAlmostEqual(snap["points_for"], 60.5)

        # Raw per-category totals from the "stats" table land in
        # team_stat_snapshots, NOT the "points" table's roto-point values
        # (see ingest_standings_html's docstring for why).
        stat_row = self.conn.execute(
            """
            SELECT tss.value FROM team_stat_snapshots tss
            JOIN stat_categories sc ON sc.season_year = tss.season_year AND sc.stat_id = tss.stat_id
            WHERE tss.season_year = ? AND tss.team_key = ? AND sc.display_name = 'R' AND sc.position_type = 'B'
            """,
            (2015, "74647.t.9"),
        ).fetchone()
        self.assertEqual(stat_row["value"], "145")

    def test_ingest_is_idempotent_on_teams(self):
        jobs.ingest_standings_html(self.conn, load("standings.html"), 2015, "74647")
        jobs.ingest_standings_html(self.conn, load("standings.html"), 2015, "74647")
        count = self.conn.execute("SELECT COUNT(*) AS c FROM teams WHERE season_year = ?", (2015,)).fetchone()["c"]
        self.assertEqual(count, 2)

    def test_ingest_writes_a_seasons_row(self):
        # Confirmed real: a season's worth of teams/standings/stats data
        # scraped with no corresponding `seasons` row is invisible to the
        # dashboard entirely (its season picker, and every /api/*
        # endpoint's "latest season" default, read from `seasons`, not
        # from the presence of data in other tables).
        jobs.ingest_standings_html(self.conn, load("standings.html"), 2015, "74647")
        season = self.conn.execute(
            "SELECT num_teams, scoring_type FROM seasons WHERE season_year = ?", (2015,)
        ).fetchone()
        self.assertIsNotNone(season)
        self.assertEqual(season["num_teams"], 2)
        self.assertEqual(season["scoring_type"], "roto")

    def test_ingest_does_not_clobber_existing_season_metadata(self):
        # A season already on file (e.g. from the dormant API era) may
        # have real league_name/is_finished/etc. that scraping has no way
        # to fill in itself -- the seasons upsert must not overwrite those
        # with NULL just because this pass doesn't know them.
        database.upsert_season(
            self.conn,
            {
                "season_year": 2015,
                "game_key": "469",
                "league_key": "469.l.74647",
                "league_name": "Kippah",
                "is_finished": 1,
            },
        )
        jobs.ingest_standings_html(self.conn, load("standings.html"), 2015, "74647")
        season = self.conn.execute(
            "SELECT league_name, is_finished FROM seasons WHERE season_year = ?", (2015,)
        ).fetchone()
        self.assertEqual(season["league_name"], "Kippah")
        self.assertEqual(season["is_finished"], 1)


class TestIngestDraftResults(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        database.init_db(self.conn)
        # Draft results are matched to teams by name -- seed teams first,
        # the way scrape_pull_draft_results expects standings to have run
        # already for the same season.
        database.upsert_teams(
            self.conn,
            [
                {
                    "season_year": 2015, "team_key": "74647.t.1", "team_id": "1",
                    "name": "Backcrackers", "logo_url": None, "manager_nickname": None,
                    "manager_guid": None, "manager_key": None, "division_id": None,
                    "faab_balance": None, "waiver_priority": None, "number_of_moves": None,
                    "number_of_trades": None,
                },
                {
                    "season_year": 2015, "team_key": "74647.t.2", "team_id": "2",
                    "name": "Team Grimace (Gri-MAH-Chay)", "logo_url": None, "manager_nickname": None,
                    "manager_guid": None, "manager_key": None, "division_id": None,
                    "faab_balance": None, "waiver_priority": None, "number_of_moves": None,
                    "number_of_trades": None,
                },
            ],
        )

    def tearDown(self):
        self.conn.close()

    def test_ingest_resolves_team_by_name_and_computes_overall_pick(self):
        result = jobs.ingest_draft_results_html(self.conn, load("draft_results.html"), 2015, "74647")
        self.assertEqual(result["picks"], 3)
        self.assertEqual(result["skipped"], 0)

        rows = self.conn.execute(
            "SELECT pick, round, team_key, player_key FROM draft_picks WHERE season_year = ? ORDER BY pick",
            (2015,),
        ).fetchall()
        self.assertEqual(rows[0]["pick"], 1)
        self.assertEqual(rows[0]["round"], 1)
        self.assertEqual(rows[0]["team_key"], "74647.t.1")
        self.assertEqual(rows[0]["player_key"], "mlb.p.9877")
        # 2 teams on file -> round 2 pick 1 is overall pick 3 (round-1)*2+1
        self.assertEqual(rows[2]["pick"], 3)
        self.assertEqual(rows[2]["round"], 2)


class TestParseTransactionTimestamp(unittest.TestCase):
    def test_parses_month_day_time_with_season_year(self):
        epoch = jobs._parse_timestamp_text("Aug 7, 11:41 am", 2026)
        self.assertIsNotNone(epoch)
        parsed = dt.datetime.fromtimestamp(epoch, tz=dt.timezone.utc)
        self.assertEqual((parsed.year, parsed.month, parsed.day, parsed.hour, parsed.minute), (2026, 8, 7, 11, 41))

    def test_pm_time_parsed_correctly(self):
        epoch = jobs._parse_timestamp_text("Sep 1, 2:15 pm", 2026)
        parsed = dt.datetime.fromtimestamp(epoch, tz=dt.timezone.utc)
        self.assertEqual(parsed.hour, 14)

    def test_none_input_returns_none(self):
        self.assertIsNone(jobs._parse_timestamp_text(None, 2026))

    def test_unparseable_text_returns_none_not_raises(self):
        self.assertIsNone(jobs._parse_timestamp_text("not a date", 2026))

    def test_cross_month_ordering_is_correct(self):
        # This is the actual bug: string-sorting "Aug"/"Jul"/"Sep" is NOT
        # chronological order. Confirm the epoch values sort correctly
        # across a month boundary, which a naive text comparison would not.
        jul = jobs._parse_timestamp_text("Jul 21, 9:00 am", 2026)
        aug = jobs._parse_timestamp_text("Aug 10, 9:00 am", 2026)
        sep = jobs._parse_timestamp_text("Sep 1, 9:00 am", 2026)
        self.assertTrue(jul < aug < sep)


class TestIngestTransactions(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        database.init_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_ingest_writes_transactions_and_players(self):
        result = jobs.ingest_transactions_html(self.conn, load("transactions.html"), 2026, "74647")
        self.assertEqual(result["transactions"], 3)
        self.assertEqual(result["players"], 5)  # 2 + 2 + 1

        tx_types = {
            r["type"]
            for r in self.conn.execute("SELECT type FROM transactions WHERE season_year = ?", (2026,)).fetchall()
        }
        self.assertIn("add/drop", tx_types)
        self.assertIn("add", tx_types)

        player_row = self.conn.execute(
            "SELECT player_name, movement, dest_team_key, source_team_key FROM transaction_players "
            "WHERE player_key = 'mlb.p.63494'"
        ).fetchone()
        self.assertEqual(player_row["player_name"], "Jung Hoo Lee")
        self.assertEqual(player_row["movement"], "add")
        self.assertEqual(player_row["dest_team_key"], "74647.t.11")
        self.assertIsNone(player_row["source_team_key"])

        dropped_row = self.conn.execute(
            "SELECT movement, source_team_key, dest_team_key FROM transaction_players "
            "WHERE player_key = 'mlb.p.62971'"
        ).fetchone()
        self.assertEqual(dropped_row["movement"], "drop")
        self.assertEqual(dropped_row["source_team_key"], "74647.t.11")
        self.assertIsNone(dropped_row["dest_team_key"])

    def test_timestamp_column_is_populated_not_null(self):
        # Confirmed real and important: a NULL `timestamp` here made every
        # scraped transaction sort to the bottom of /api/transactions'
        # `ORDER BY tr.timestamp DESC` (SQLite sorts NULL as the smallest
        # value), below every real-timestamped API-era row -- from the
        # dashboard this looked exactly like "no transactions since the
        # API cutover date", even though the rows were there all along.
        jobs.ingest_transactions_html(self.conn, load("transactions.html"), 2026, "74647")
        rows = self.conn.execute(
            "SELECT timestamp FROM transactions WHERE season_year = 2026"
        ).fetchall()
        self.assertTrue(rows)
        for r in rows:
            self.assertIsNotNone(r["timestamp"])

    def test_ingest_is_idempotent(self):
        jobs.ingest_transactions_html(self.conn, load("transactions.html"), 2026, "74647")
        jobs.ingest_transactions_html(self.conn, load("transactions.html"), 2026, "74647")
        count = self.conn.execute("SELECT COUNT(*) AS c FROM transactions WHERE season_year = ?", (2026,)).fetchone()["c"]
        self.assertEqual(count, 3)


class TestIngestTeamDailyTotals(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        database.init_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_ingest_writes_batting_and_pitching_deltas(self):
        result = jobs.ingest_team_daily_totals_html(
            self.conn, load("team_daily.html"), 2026, "74647.t.9", "2026-08-08"
        )
        self.assertEqual(result["stat_rows"], 14)  # 7 batting + 7 pitching

        rows = self.conn.execute(
            "SELECT tdd.value, sc.display_name, sc.position_type FROM team_daily_stat_deltas tdd "
            "JOIN stat_categories sc ON sc.season_year = tdd.season_year AND sc.stat_id = tdd.stat_id "
            "WHERE tdd.season_year = 2026 AND tdd.team_key = '74647.t.9' AND tdd.snapshot_date = '2026-08-08'"
        ).fetchall()
        by_name = {(r["display_name"], r["position_type"]): r["value"] for r in rows}
        self.assertEqual(by_name[("R", "B")], "1")
        self.assertEqual(by_name[("ERA", "P")], "2.00")

    def test_reuses_existing_stat_id_for_continuity_with_api_era(self):
        # Confirmed real: the team page's column headers already match the
        # season's existing (API-era) stat_categories display names
        # exactly, so a chart spanning the API-to-scraping cutover date
        # should see one continuous stat_id, not a duplicate category.
        database.upsert_stat_categories(
            self.conn,
            [
                {
                    "season_year": 2026,
                    "stat_id": 7,
                    "name": "R",
                    "display_name": "R",
                    "sort_order": 1,
                    "display_order": 0,
                    "is_display_only": 0,
                    "position_type": "B",
                }
            ],
        )
        jobs.ingest_team_daily_totals_html(self.conn, load("team_daily.html"), 2026, "74647.t.9", "2026-08-08")
        row = self.conn.execute(
            "SELECT stat_id FROM team_daily_stat_deltas WHERE season_year = 2026 AND team_key = '74647.t.9' "
            "AND snapshot_date = '2026-08-08' AND stat_id = 7"
        ).fetchone()
        self.assertIsNotNone(row)  # reused stat_id 7, didn't mint a new 9000+ one for "R"/"B"

    def test_ingest_is_idempotent(self):
        jobs.ingest_team_daily_totals_html(self.conn, load("team_daily.html"), 2026, "74647.t.9", "2026-08-08")
        jobs.ingest_team_daily_totals_html(self.conn, load("team_daily.html"), 2026, "74647.t.9", "2026-08-08")
        count = self.conn.execute(
            "SELECT COUNT(*) AS c FROM team_daily_stat_deltas WHERE season_year = 2026 AND team_key = '74647.t.9'"
        ).fetchone()["c"]
        self.assertEqual(count, 14)

    def test_failed_team_day_leaves_no_partial_rows(self):
        # A team-day whose fetch/parse comes back empty (e.g. the
        # wait_selector timeout case in app.scrape.browser.fetch_page)
        # must write zero rows, not a partial set -- this is what makes
        # per-(team, date) resumability correct: a failed team-day has NO
        # rows in team_daily_stat_deltas, so it looks identical to "never
        # attempted" and is naturally retried, no special "force" flag
        # needed.
        result = jobs.ingest_team_daily_totals_html(self.conn, "<html></html>", 2026, "74647.t.9", "2026-08-08")
        self.assertEqual(result["stat_rows"], 0)
        count = self.conn.execute(
            "SELECT COUNT(*) AS c FROM team_daily_stat_deltas WHERE team_key = '74647.t.9'"
        ).fetchone()["c"]
        self.assertEqual(count, 0)


class TestScrapeBackfillDailyStatsCircuitBreaker(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        database.init_db(self.conn)
        for team_id in ("9", "8", "3"):
            self.conn.execute(
                "INSERT INTO teams (season_year, team_key, team_id, name) VALUES (?, ?, ?, ?)",
                (2026, f"74647.t.{team_id}", team_id, f"Team {team_id}"),
            )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_stops_early_after_consecutive_timeout_length_fetches(self):
        # Confirmed real: once Yahoo starts rate-limiting, every
        # subsequent team-day fetch takes the full ~20s wait_selector
        # timeout instead of the normal ~2s -- simulate that by making
        # scrape_pull_team_daily_stats "take" 20s per call (via a fake
        # clock) starting from the very first call, and confirm the
        # backfill stops after suspected_block_streak consecutive slow
        # calls rather than working through every remaining team/date.
        clock = [0.0]

        def fake_monotonic():
            return clock[0]

        def fake_pull(*args, **kwargs):
            clock[0] += 20.0  # every call looks like a full timeout

        with unittest.mock.patch("app.scrape.jobs.time.monotonic", side_effect=fake_monotonic), \
             unittest.mock.patch("app.scrape.jobs.time.sleep"), \
             unittest.mock.patch("app.scrape.jobs.scrape_pull_team_daily_stats", side_effect=fake_pull) as mock_pull:
            result = jobs.scrape_backfill_daily_stats(
                self.conn, 2026, "74647", "2026-08-01", "2026-08-10",
                suspected_block_seconds=15.0, suspected_block_streak=3,
            )

        self.assertTrue(result["aborted_suspected_block"])
        # 3 teams need to time out before the breaker trips -- it should
        # not have gone on to fetch every team for every one of the 10 days.
        self.assertEqual(mock_pull.call_count, 3)
        self.assertTrue(any("Aborted early" in e for e in result["errors"]))

    def test_does_not_trip_on_isolated_slow_fetch(self):
        # A single slow/timed-out team-day surrounded by normal-speed
        # ones is the legitimate "no data for this team-day" case, not a
        # block -- must not trip the breaker.
        clock = [0.0]
        call_count = [0]

        def fake_monotonic():
            return clock[0]

        def fake_pull(*args, **kwargs):
            call_count[0] += 1
            # Every 5th call is slow (isolated), the rest are fast.
            clock[0] += 20.0 if call_count[0] % 5 == 0 else 2.0

        with unittest.mock.patch("app.scrape.jobs.time.monotonic", side_effect=fake_monotonic), \
             unittest.mock.patch("app.scrape.jobs.time.sleep"), \
             unittest.mock.patch("app.scrape.jobs.scrape_pull_team_daily_stats", side_effect=fake_pull) as mock_pull:
            result = jobs.scrape_backfill_daily_stats(
                self.conn, 2026, "74647", "2026-08-01", "2026-08-03",
                suspected_block_seconds=15.0, suspected_block_streak=3,
            )

        self.assertFalse(result["aborted_suspected_block"])
        self.assertEqual(mock_pull.call_count, 9)  # 3 teams x 3 days, ran to completion


class TestStoredDailyStatDeltaTeamDates(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        database.init_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_tracks_coverage_per_team_not_just_per_date(self):
        # Confirmed real: team 9 succeeded on 2026-07-29 but team 8 didn't
        # -- per-date-only resumability would treat 2026-07-29 as fully
        # covered (it has SOME rows) and skip team 8 forever.
        jobs.ingest_team_daily_totals_html(self.conn, load("team_daily.html"), 2026, "74647.t.9", "2026-07-29")
        covered = database.stored_daily_stat_delta_team_dates(self.conn, 2026)
        self.assertIn(("74647.t.9", "2026-07-29"), covered)
        self.assertNotIn(("74647.t.8", "2026-07-29"), covered)


if __name__ == "__main__":
    unittest.main()
