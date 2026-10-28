import sqlite3
import unittest

from app import keepers
from app.db import database


class TestKeeperRound(unittest.TestCase):
    def test_six_rounds_earlier(self):
        self.assertEqual(keepers.keeper_round(10), 4)
        self.assertEqual(keepers.keeper_round(7), 1)
        self.assertEqual(keepers.keeper_round(22), 16)

    def test_round_six_or_earlier_not_eligible(self):
        self.assertIsNone(keepers.keeper_round(6))
        self.assertIsNone(keepers.keeper_round(1))

    def test_capped_at_sixteen(self):
        self.assertEqual(keepers.keeper_round(23), 16)
        self.assertEqual(keepers.keeper_round(29), 16)

    def test_undrafted_is_sixteen(self):
        self.assertEqual(keepers.keeper_round(None), 16)


class TestPlayerStatus(unittest.TestCase):
    def test_not_frozen_wins_regardless_of_round(self):
        self.assertEqual(keepers.player_status(False, 10)["status"], keepers.NOT_FROZEN)
        self.assertEqual(keepers.player_status(False, 3)["status"], keepers.NOT_FROZEN)

    def test_labels(self):
        self.assertEqual(keepers.player_status(True, 10)["label"], "Keeper Rd 4 (drafted Rd 10)")
        self.assertEqual(keepers.player_status(True, None)["label"], "Keeper Rd 16 (undrafted)")
        self.assertEqual(keepers.player_status(True, 5)["label"], "Not keeper eligible (drafted Rd 5)")


class TestBuildReport(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        database.init_db(self.conn)
        for team_key, name in [("t.1", "Alpha"), ("t.2", "Beta")]:
            self.conn.execute(
                "INSERT INTO teams (season_year, team_key, team_id, name) VALUES (2026, ?, ?, ?)",
                (team_key, team_key[-1], name),
            )
        snaps = [
            ("2026-09-01", "t.1", "p.kept"),
            ("2026-09-01", "t.1", "p.early"),
            ("2026-09-01", "t.1", "p.free"),
            ("2026-09-01", "t.2", "p.traded"),
            ("2026-10-28", "t.1", "p.kept"),
            ("2026-10-28", "t.1", "p.early"),
            ("2026-10-28", "t.1", "p.free"),
            ("2026-10-28", "t.1", "p.traded"),
            ("2026-10-28", "t.2", "p.late"),
        ]
        database.upsert_roster_snapshot(
            self.conn,
            [
                {"snapshot_date": d, "season_year": 2026, "team_key": t, "player_key": p, "player_name": p}
                for d, t, p in snaps
            ],
        )
        for pick, rnd, player in [(1, 10, "p.kept"), (2, 3, "p.early"), (3, 12, "p.traded"), (4, 8, "p.late")]:
            self.conn.execute(
                "INSERT INTO draft_picks (season_year, pick, round, team_key, player_key) VALUES (2026, ?, ?, 't.2', ?)",
                (pick, rnd, player),
            )

    def tearDown(self):
        self.conn.close()

    def test_report(self):
        report = keepers.build_report(self.conn, 2026, "2026-09-01", "2026-10-28")
        self.assertEqual([t["team"] for t in report], ["Alpha", "Beta"])
        alpha = {p["player_name"]: p["label"] for p in report[0]["players"]}
        self.assertEqual(
            alpha,
            {
                # drafted by a different team -- doesn't matter
                "p.kept": "Keeper Rd 4 (drafted Rd 10)",
                "p.free": "Keeper Rd 16 (undrafted)",
                "p.early": "Not keeper eligible (drafted Rd 3)",
                # on a different roster on Sep 1
                "p.traded": "Not frozen",
            },
        )
        self.assertEqual(
            [p["player_name"] for p in report[0]["players"]], ["p.kept", "p.free", "p.early", "p.traded"]
        )
        traded = report[0]["players"][-1]
        self.assertEqual(traded["freeze_team"], "Beta")
        late = report[1]["players"][0]
        self.assertEqual((late["label"], late["freeze_team"]), ("Not frozen", None))

    def test_pdf_renders(self):
        import tempfile
        from pathlib import Path

        from app import keeper_pdf

        report = keepers.build_report(self.conn, 2026, "2026-09-01", "2026-10-28")
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "k.pdf"
            keeper_pdf.render(
                report, str(path), title="Kippah 2027 Eligible Keepers", season_year=2026,
                freeze_date="2026-09-01", end_date="2026-09-27",
            )
            self.assertTrue(path.read_bytes().startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()
