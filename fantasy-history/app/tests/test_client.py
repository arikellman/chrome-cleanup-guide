import unittest

from app.yahoo.client import build_query_string


class TestBuildQueryString(unittest.TestCase):
    def test_commas_stay_literal(self):
        # Yahoo's API 400s on a percent-encoded comma in out= (discovered
        # against a real league: "Invalid subresource settings%2cstandings
        # requested"), so this must never regress to the requests default.
        result = build_query_string({"out": "settings,standings", "format": "json"})
        self.assertEqual(result, "out=settings,standings&format=json")
        self.assertNotIn("%2C", result.upper())

    def test_other_characters_still_encoded(self):
        result = build_query_string({"q": "a b"})
        self.assertIn("a+b", result)


if __name__ == "__main__":
    unittest.main()
