"""Validates app.roto's point math against real numbers from a live Yahoo
roto standings page (12-team league), not synthetic fixtures -- this is
the one module in the app whose correctness was checked against an actual
screenshot rather than assumed API shape."""
import unittest

from app import roto

# HR totals and Yahoo's own "Overall Points" HR column for the same 12
# teams, taken directly from the user's league standings page.
HR_VALUES = {
    "Prime Time": 171,
    "Birds & the Beezers": 192,
    "Bat Intentions": 170,
    "The Traveler": 163,
    "Rage Against The Slop Machine": 197,
    "Meshuganas": 194,
    "Backcrackers": 179,
    "Lightnings": 172,
    "Dirty Randy": 179,
    "Team Grimace (Gri-MAH-Chay)": 144,
    "The Ghost of Elvis Past": 157,
    "Houthi PC Small Group": 142,
}
EXPECTED_HR_POINTS = {
    "Prime Time": 6,
    "Birds & the Beezers": 10,
    "Bat Intentions": 5,
    "The Traveler": 4,
    "Rage Against The Slop Machine": 12,
    "Meshuganas": 11,
    "Backcrackers": 8.5,
    "Lightnings": 7,
    "Dirty Randy": 8.5,
    "Team Grimace (Gri-MAH-Chay)": 2,
    "The Ghost of Elvis Past": 3,
    "Houthi PC Small Group": 1,
}


class TestComputePoints(unittest.TestCase):
    def test_matches_real_yahoo_hr_points(self):
        points = roto.compute_points(HR_VALUES, higher_is_better=True)
        self.assertEqual(points, EXPECTED_HR_POINTS)

    def test_lower_is_better_reverses_ranking(self):
        values = {"a": 3.0, "b": 4.0, "c": 5.0}
        points = roto.compute_points(values, higher_is_better=False)
        self.assertEqual(points, {"a": 3, "b": 2, "c": 1})

    def test_ties_split_average_of_occupied_positions(self):
        values = {"a": 10, "b": 10, "c": 5}
        points = roto.compute_points(values, higher_is_better=True)
        # a and b tie for 1st/2nd (worth 3 and 2 points) -> average 2.5 each
        self.assertEqual(points, {"a": 2.5, "b": 2.5, "c": 1})

    def test_empty(self):
        self.assertEqual(roto.compute_points({}, True), {})


class TestComputeStandings(unittest.TestCase):
    def test_totals_and_category_breakdown(self):
        team_values = {
            "t1": {7: 10, 12: 3},
            "t2": {7: 5, 12: 8},
        }
        categories = [
            {"stat_id": 7, "sort_order": 1},
            {"stat_id": 12, "sort_order": 1},
        ]
        result = roto.compute_standings(team_values, categories)
        self.assertEqual(result["t1"]["category_points"], {7: 2, 12: 1})
        self.assertEqual(result["t2"]["category_points"], {7: 1, 12: 2})
        self.assertEqual(result["t1"]["total_points"], 3)
        self.assertEqual(result["t2"]["total_points"], 3)

    def test_lower_is_better_category_mixed_with_higher(self):
        team_values = {
            "t1": {7: 10, 99: 2.0},  # 99 = ERA-like, lower better
            "t2": {7: 5, 99: 1.0},
        }
        categories = [
            {"stat_id": 7, "sort_order": 1},
            {"stat_id": 99, "sort_order": 0},
        ]
        result = roto.compute_standings(team_values, categories)
        self.assertEqual(result["t1"]["category_points"], {7: 2, 99: 1})
        self.assertEqual(result["t2"]["category_points"], {7: 1, 99: 2})


class TestRankByTotalPoints(unittest.TestCase):
    def test_standard_competition_ranking(self):
        standings = [
            {"team_key": "a", "total_points": 110},
            {"team_key": "b", "total_points": 101.5},
            {"team_key": "c", "total_points": 101.5},
            {"team_key": "d", "total_points": 85.5},
        ]
        roto.rank_by_total_points(standings)
        ranks = {row["team_key"]: row["rank"] for row in standings}
        self.assertEqual(ranks, {"a": 1, "b": 2, "c": 2, "d": 4})


if __name__ == "__main__":
    unittest.main()
