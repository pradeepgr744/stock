import unittest
from datetime import date

from scripts.download_nifty200_history import month_windows


class HistoricalDownloaderTests(unittest.TestCase):
    def test_month_windows_split_to_calendar_months(self):
        windows = list(month_windows(date(2026, 1, 31), date(2026, 3, 2)))
        self.assertEqual(
            windows,
            [
                (date(2026, 1, 31), date(2026, 1, 31)),
                (date(2026, 2, 1), date(2026, 2, 28)),
                (date(2026, 3, 1), date(2026, 3, 2)),
            ],
        )

    def test_single_month_window(self):
        self.assertEqual(
            list(month_windows(date(2026, 2, 1), date(2026, 2, 28))),
            [(date(2026, 2, 1), date(2026, 2, 28))],
        )


if __name__ == "__main__":
    unittest.main()
