import unittest

from app.jobs.daily import find_subresource, teams_stats_path


class TestTeamsStatsPath(unittest.TestCase):
    def test_season_type_uses_semicolon_chained_path(self):
        # Confirmed against a real league: ?out=stats&type=season 400s
        # with "Invalid subresource stats requested" -- stats must be a
        # path segment with semicolon-chained modifiers instead.
        path = teams_stats_path("469.l.74647", {"type": "season"})
        self.assertEqual(path, "league/469.l.74647/teams/stats;type=season")

    def test_date_type_chains_both_modifiers(self):
        path = teams_stats_path("469.l.74647", {"type": "date", "date": "2026-03-25"})
        self.assertEqual(path, "league/469.l.74647/teams/stats;type=date;date=2026-03-25")

    def test_no_params_is_bare_stats_path(self):
        path = teams_stats_path("469.l.74647", {})
        self.assertEqual(path, "league/469.l.74647/teams/stats")


class TestFindSubresource(unittest.TestCase):
    def test_plain_dict_subresource_passes_through(self):
        # e.g. standings: {"standings": {"teams": {...}}}
        league_list = [{"league_key": "x"}, {"standings": {"teams": {"0": "team"}}}]
        result = find_subresource(league_list, "standings")
        self.assertEqual(result, {"teams": {"0": "team"}})

    def test_single_element_list_wrapped_dict(self):
        league_list = [{"league_key": "x"}, {"scoreboard": [{"matchups": {"0": "m"}}]}]
        result = find_subresource(league_list, "scoreboard")
        self.assertEqual(result, {"matchups": {"0": "m"}})

    def test_list_of_single_key_dicts_gets_flattened(self):
        # Confirmed against a real league: "settings" can come back as a
        # list of single-key dicts (like team/league fields) rather than
        # a plain dict, which broke parse_stat_categories's settings.get(...).
        league_list = [
            {"league_key": "x"},
            {
                "settings": [
                    [
                        {"scoring_type": "roto"},
                        {"stat_categories": {"stats": {"0": "stat", "count": 1}}},
                    ]
                ]
            },
        ]
        result = find_subresource(league_list, "settings")
        self.assertEqual(result["scoring_type"], "roto")
        self.assertEqual(result["stat_categories"], {"stats": {"0": "stat", "count": 1}})

    def test_missing_key_returns_empty_dict(self):
        league_list = [{"league_key": "x"}]
        self.assertEqual(find_subresource(league_list, "settings"), {})


if __name__ == "__main__":
    unittest.main()
