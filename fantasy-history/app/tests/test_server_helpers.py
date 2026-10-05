import unittest

from app.web.server import _is_rate_stat, _parse_stat_value


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


class TestParseStatValue(unittest.TestCase):
    def test_plain_number(self):
        self.assertEqual(_parse_stat_value("962"), 962.0)

    def test_strips_thousand_separator_commas(self):
        # Confirmed real: a full-roster season counting-stat total (e.g.
        # K) routinely crosses 1000 and Yahoo renders it as "1,743" --
        # bare float() raises ValueError on the comma, which used to
        # silently drop that team from the whole category everywhere
        # this helper is now used.
        self.assertEqual(_parse_stat_value("1,743"), 1743.0)
        self.assertEqual(_parse_stat_value("1,034"), 1034.0)

    def test_strips_trailing_qualifier_asterisk(self):
        # Confirmed real: Yahoo appends "*" to a rate stat (WHIP, OBP,
        # ...) for a team that hasn't met its innings/at-bat qualifying
        # minimum yet -- the number is still real, just flagged.
        self.assertEqual(_parse_stat_value("1.16*"), 1.16)
        self.assertEqual(_parse_stat_value(".329*"), 0.329)

    def test_handles_both_decorations_together(self):
        self.assertEqual(_parse_stat_value("1,234.5*"), 1234.5)

    def test_none_and_empty_and_non_numeric(self):
        self.assertIsNone(_parse_stat_value(None))
        self.assertIsNone(_parse_stat_value(""))
        self.assertIsNone(_parse_stat_value("*"))
        self.assertIsNone(_parse_stat_value("--"))


if __name__ == "__main__":
    unittest.main()
