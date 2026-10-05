import sqlite3
import unittest

from app.db import database
from app.jobs.daily import find_subresource, pull_draft_results, pull_transactions, teams_stats_path


def _memory_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    database.init_db(conn)
    return conn


class _FakeClient:
    """Minimal stand-in for YahooClient: returns canned bodies by path,
    with no real HTTP or raw_responses persistence involved."""

    def __init__(self, bodies: dict[str, dict]):
        self.bodies = bodies
        self.requested_paths: list[str] = []

    def get(self, path, params=None, season_year=None, week=None):
        self.requested_paths.append(path)
        return self.bodies[path]


def _tx_body(tx_items: dict, count: int) -> dict:
    return {
        "fantasy_content": {
            "league": [
                {"league_key": "458.l.12345"},
                {"transactions": {**tx_items, "count": count}},
            ]
        }
    }


def _one_tx(key: str) -> dict:
    return {
        "transaction": [
            [
                {"transaction_key": key},
                {"type": "add/drop"},
                {"status": "successful"},
                {"timestamp": "1714000000"},
            ],
            {"players": {"count": 0}},
        ]
    }


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


class TestTransactionPaginationHardening(unittest.TestCase):
    def test_no_op_when_first_page_has_everything(self):
        conn = _memory_conn()
        bodies = {
            "league/458.l.12345/transactions": _tx_body({"0": _one_tx("458.l.12345.tr.1")}, count=1),
        }
        client = _FakeClient(bodies)
        pull_transactions(client, conn, "458.l.12345", 2024)
        rows = conn.execute("SELECT transaction_key FROM transactions").fetchall()
        self.assertEqual([r["transaction_key"] for r in rows], ["458.l.12345.tr.1"])
        # Only the single (non-paginated) request should have been made.
        self.assertEqual(client.requested_paths, ["league/458.l.12345/transactions"])

    def test_pages_when_wrapper_count_exceeds_first_page(self):
        conn = _memory_conn()
        bodies = {
            "league/458.l.12345/transactions": _tx_body({"0": _one_tx("458.l.12345.tr.1")}, count=2),
            "league/458.l.12345/transactions;count=25;start=1": _tx_body(
                {"0": _one_tx("458.l.12345.tr.2")}, count=2
            ),
        }
        client = _FakeClient(bodies)
        pull_transactions(client, conn, "458.l.12345", 2024)
        rows = conn.execute("SELECT transaction_key FROM transactions ORDER BY transaction_key").fetchall()
        self.assertEqual(
            [r["transaction_key"] for r in rows], ["458.l.12345.tr.1", "458.l.12345.tr.2"]
        )
        self.assertIn("league/458.l.12345/transactions;count=25;start=1", client.requested_paths)

    def test_stops_on_empty_page_even_if_count_not_reached(self):
        conn = _memory_conn()
        bodies = {
            "league/458.l.12345/transactions": _tx_body({"0": _one_tx("458.l.12345.tr.1")}, count=5),
            "league/458.l.12345/transactions;count=25;start=1": _tx_body({}, count=5),
        }
        client = _FakeClient(bodies)
        # Should not raise/loop forever even though reported count (5) is
        # never reached.
        pull_transactions(client, conn, "458.l.12345", 2024)
        rows = conn.execute("SELECT transaction_key FROM transactions").fetchall()
        self.assertEqual(len(rows), 1)


class TestPullDraftResults(unittest.TestCase):
    def test_pull_draft_results_stores_rows(self):
        conn = _memory_conn()
        body = {
            "fantasy_content": {
                "league": [
                    {"league_key": "458.l.12345"},
                    {
                        "draft_results": {
                            "0": {
                                "draft_result": [
                                    {"pick": "1"},
                                    {"round": "1"},
                                    {"team_key": "458.l.12345.t.1"},
                                    {"player_key": "458.p.1000"},
                                ]
                            },
                            "count": 1,
                        }
                    },
                ]
            }
        }
        client = _FakeClient({"league/458.l.12345/draftresults": body})
        pull_draft_results(client, conn, "458.l.12345", 2024)
        rows = conn.execute("SELECT * FROM draft_picks").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["team_key"], "458.l.12345.t.1")
        self.assertEqual(rows[0]["player_key"], "458.p.1000")


if __name__ == "__main__":
    unittest.main()
