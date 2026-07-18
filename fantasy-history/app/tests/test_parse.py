"""Unit tests for app.yahoo.parse against fixture JSON that documents the
assumed Yahoo Fantasy Sports API response shape (see fixtures/*.json and
the module docstring in app/yahoo/parse.py for caveats)."""
import json
import unittest
from pathlib import Path

from app.yahoo import parse

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load(name: str):
    with open(FIXTURES / name) as f:
        return json.load(f)


class TestGenericHelpers(unittest.TestCase):
    def test_unwrap_collection_dict(self):
        node = {"0": "a", "1": "b", "count": 2}
        self.assertEqual(parse.unwrap_collection(node), ["a", "b"])

    def test_unwrap_collection_list_passthrough(self):
        self.assertEqual(parse.unwrap_collection(["a", "b"]), ["a", "b"])

    def test_unwrap_collection_none(self):
        self.assertEqual(parse.unwrap_collection(None), [])

    def test_flatten_field_list(self):
        result = parse.flatten_field_list([{"a": 1}, {"b": 2}])
        self.assertEqual(result, {"a": 1, "b": 2})

    def test_merge_named_node(self):
        node = [[{"a": 1}, {"b": 2}], {"extra": {"x": 1}}]
        result = parse.merge_named_node(node)
        self.assertEqual(result, {"a": 1, "b": 2, "extra": {"x": 1}})


class TestLeagueParsing(unittest.TestCase):
    def test_parse_league_meta(self):
        fields = load("league_fields.json")
        season = parse.parse_league_meta(fields)
        self.assertEqual(season["season_year"], 2024)
        self.assertEqual(season["league_key"], "458.l.12345")
        self.assertEqual(season["league_name"], "The Boys of Summer")
        self.assertEqual(season["num_teams"], 10)
        self.assertEqual(season["scoring_type"], "head")
        self.assertEqual(season["start_week"], 1)
        self.assertEqual(season["end_week"], 24)
        self.assertEqual(season["is_finished"], 1)

    def test_parse_stat_categories(self):
        settings = load("settings.json")
        rows = parse.parse_stat_categories(settings, 2024)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["stat_id"], 7)
        self.assertEqual(rows[0]["display_name"], "R")
        self.assertEqual(rows[0]["display_order"], 0)
        self.assertEqual(rows[1]["stat_id"], 12)
        self.assertEqual(rows[1]["display_order"], 1)


class TestTeamParsing(unittest.TestCase):
    def test_parse_teams(self):
        teams_node = load("teams_node.json")
        rows = parse.parse_teams(teams_node, 2024)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["team_key"], "458.l.12345.t.1")
        self.assertEqual(rows[0]["name"], "Bash Brothers")
        self.assertEqual(rows[0]["manager_nickname"], "Ari")
        self.assertEqual(rows[0]["manager_guid"], "GUID1")
        self.assertEqual(rows[0]["logo_url"], "https://example.com/logo1.png")
        self.assertEqual(rows[1]["manager_nickname"], "Sam")

    def test_parse_standings_snapshot(self):
        teams_node = load("teams_node.json")
        rows = parse.parse_standings_snapshot(teams_node, 2024, "2024-07-01")
        self.assertEqual(len(rows), 2)
        team1 = rows[0]
        self.assertEqual(team1["rank"], 1)
        self.assertEqual(team1["wins"], 80)
        self.assertEqual(team1["losses"], 40)
        self.assertAlmostEqual(team1["pct"], 0.667)
        self.assertEqual(team1["playoff_seed"], 1)
        self.assertEqual(team1["snapshot_date"], "2024-07-01")

    def test_parse_team_stat_snapshots(self):
        teams_node = load("teams_node.json")
        rows = parse.parse_team_stat_snapshots(teams_node, 2024, "2024-07-01")
        self.assertEqual(len(rows), 4)  # 2 teams x 2 stat categories
        self.assertTrue(all(r["snapshot_date"] == "2024-07-01" for r in rows))
        team1_stats = {r["stat_id"]: r["value"] for r in rows if r["team_key"] == "458.l.12345.t.1"}
        self.assertEqual(team1_stats[7], "845")
        self.assertEqual(team1_stats[12], "210")


class TestScoreboardParsing(unittest.TestCase):
    def test_parse_scoreboard(self):
        scoreboard = load("scoreboard.json")
        matchups, stats = parse.parse_scoreboard(scoreboard, 2024, 5)
        self.assertEqual(len(matchups), 1)
        m = matchups[0]
        self.assertEqual(m["team1_key"], "458.l.12345.t.1")
        self.assertEqual(m["team2_key"], "458.l.12345.t.2")
        self.assertEqual(m["status"], "postevent")
        self.assertEqual(m["winner_team_key"], "458.l.12345.t.1")
        self.assertEqual(m["matchup_id"], "2024:5:458.l.12345.t.1:458.l.12345.t.2")

        self.assertEqual(len(stats), 4)  # 2 teams x 2 categories
        team1_runs = next(s for s in stats if s["team_key"] == "458.l.12345.t.1" and s["stat_id"] == 7)
        self.assertEqual(team1_runs["value"], "20")
        self.assertEqual(team1_runs["won_category"], 1)

        team2_hr = next(s for s in stats if s["team_key"] == "458.l.12345.t.2" and s["stat_id"] == 12)
        self.assertEqual(team2_hr["won_category"], 1)
        team1_hr = next(s for s in stats if s["team_key"] == "458.l.12345.t.1" and s["stat_id"] == 12)
        self.assertEqual(team1_hr["won_category"], 0)


class TestUserLeagueDiscovery(unittest.TestCase):
    def test_parse_user_leagues(self):
        body = load("user_leagues.json")
        leagues = parse.parse_user_leagues(body)
        self.assertEqual(len(leagues), 1)
        self.assertEqual(leagues[0]["league_key"], "458.l.12345")
        self.assertEqual(leagues[0]["name"], "The Boys of Summer")
        self.assertEqual(leagues[0]["season"], 2024)
        self.assertEqual(leagues[0]["game_key"], "458")


class TestTransactionParsing(unittest.TestCase):
    def test_parse_transactions(self):
        node = load("transactions.json")
        tx_rows, player_rows = parse.parse_transactions(node, 2024)
        self.assertEqual(len(tx_rows), 1)
        self.assertEqual(tx_rows[0]["transaction_key"], "458.l.12345.tr.1")
        self.assertEqual(tx_rows[0]["type"], "add/drop")

        self.assertEqual(len(player_rows), 2)
        added = next(p for p in player_rows if p["movement"] == "add")
        self.assertEqual(added["player_name"], "Some Player")
        self.assertEqual(added["dest_team_key"], "458.l.12345.t.1")
        dropped = next(p for p in player_rows if p["movement"] == "drop")
        self.assertEqual(dropped["source_team_key"], "458.l.12345.t.1")


if __name__ == "__main__":
    unittest.main()
