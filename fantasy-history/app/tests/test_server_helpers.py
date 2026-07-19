import unittest

from app.web.server import _is_rate_stat


class TestIsRateStat(unittest.TestCase):
    def test_recognizes_common_rate_stats(self):
        for display_name in ["ERA", "WHIP", "OBP", "AVG", "OPS", "SLG"]:
            with self.subTest(display_name=display_name):
                self.assertTrue(_is_rate_stat(display_name, None))

    def test_counting_stats_are_not_rate_stats(self):
        for display_name in ["R", "HR", "RBI", "SB", "W", "SV", "HLD", "K"]:
            with self.subTest(display_name=display_name):
                self.assertFalse(_is_rate_stat(display_name, None))

    def test_ignores_name_field(self):
        # Confirmed against a real league: substring-matching the full
        # descriptive `name` field (e.g. "Runs Batted In", "Stolen Bases")
        # misclassified RBI/SB as rate stats because both contain "ba".
        # Only the short, reliable display_name is checked now.
        self.assertFalse(_is_rate_stat("RBI", "Runs Batted In"))
        self.assertFalse(_is_rate_stat("SB", "Stolen Bases"))
        self.assertFalse(_is_rate_stat(None, "Team ERA"))

    def test_handles_missing_values(self):
        self.assertFalse(_is_rate_stat(None, None))


if __name__ == "__main__":
    unittest.main()
