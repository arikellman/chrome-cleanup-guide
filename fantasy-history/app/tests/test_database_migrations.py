import sqlite3
import unittest

from app.db import database


class TestColumnMigrations(unittest.TestCase):
    def test_adds_missing_columns_to_pre_existing_table(self):
        # Simulates a real user's existing fantasy.db from before the
        # manager_key/faab_balance/... columns existed: "CREATE TABLE IF
        # NOT EXISTS" in schema.sql is a no-op against a table that
        # already exists, so without this migration the later "CREATE
        # INDEX ... ON teams(manager_key)" in schema.sql would fail with
        # "no such column: manager_key" -- confirmed against a real user's
        # database.
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE teams (season_year INTEGER, team_key TEXT PRIMARY KEY, "
            "team_id TEXT, name TEXT, logo_url TEXT, manager_nickname TEXT, "
            "manager_guid TEXT, division_id TEXT)"
        )
        database.init_db(conn)  # must not raise
        columns = {r[1] for r in conn.execute("PRAGMA table_info(teams)")}
        for col in ("manager_key", "faab_balance", "waiver_priority", "number_of_moves", "number_of_trades"):
            self.assertIn(col, columns)
        conn.close()

    def test_no_op_on_fresh_database(self):
        conn = sqlite3.connect(":memory:")
        database.init_db(conn)  # fresh install: no teams table yet
        columns = {r[1] for r in conn.execute("PRAGMA table_info(teams)")}
        self.assertIn("manager_key", columns)
        conn.close()

    def test_idempotent_on_already_migrated_database(self):
        conn = sqlite3.connect(":memory:")
        database.init_db(conn)
        database.init_db(conn)  # must not raise "duplicate column"
        conn.close()


if __name__ == "__main__":
    unittest.main()
