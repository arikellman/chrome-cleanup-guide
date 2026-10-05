import datetime as dt
import unittest

from app.jobs.backfill import season_backfill_date_range


class TestSeasonBackfillDateRange(unittest.TestCase):
    def test_finished_season_uses_its_own_end_date(self):
        season_row = {"start_date": "2023-03-30", "end_date": "2023-09-30"}
        result = season_backfill_date_range(season_row, today=dt.date(2026, 7, 19))
        self.assertEqual(result, (dt.date(2023, 3, 30), dt.date(2023, 9, 30)))

    def test_in_progress_season_is_clamped_to_today(self):
        season_row = {"start_date": "2026-03-28", "end_date": "2026-09-29"}
        result = season_backfill_date_range(season_row, today=dt.date(2026, 7, 19))
        self.assertEqual(result, (dt.date(2026, 3, 28), dt.date(2026, 7, 19)))

    def test_missing_start_date_returns_none(self):
        self.assertIsNone(season_backfill_date_range({}, today=dt.date(2026, 7, 19)))

    def test_missing_end_date_falls_back_to_today(self):
        season_row = {"start_date": "2026-03-28"}
        result = season_backfill_date_range(season_row, today=dt.date(2026, 7, 19))
        self.assertEqual(result, (dt.date(2026, 3, 28), dt.date(2026, 7, 19)))

    def test_season_not_started_yet_returns_none(self):
        season_row = {"start_date": "2026-08-01", "end_date": "2026-12-01"}
        result = season_backfill_date_range(season_row, today=dt.date(2026, 7, 19))
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
