import unittest
from datetime import datetime, timezone

from trendradar.ai.peak_period import classify_peak_period


class PeakPeriodTests(unittest.TestCase):
    def test_weekday_morning_peak(self):
        # 2026-11-19 09:00 Beijing == 01:00 UTC.
        value = datetime(2026, 11, 19, 1, 0, tzinfo=timezone.utc)
        self.assertIs(classify_peak_period(value), True)

    def test_peak_start_is_inclusive(self):
        value = datetime(2026, 11, 19, 1, 0, tzinfo=timezone.utc)
        self.assertTrue(classify_peak_period(value))

    def test_peak_end_is_exclusive(self):
        value = datetime(2026, 11, 19, 4, 0, tzinfo=timezone.utc)
        self.assertFalse(classify_peak_period(value))

    def test_afternoon_peak(self):
        value = datetime(2026, 11, 19, 6, 0, tzinfo=timezone.utc)
        self.assertTrue(classify_peak_period(value))

    def test_weekend_is_off_peak_even_without_calendar_year(self):
        value = datetime(2027, 1, 2, 2, 0, tzinfo=timezone.utc)
        self.assertFalse(classify_peak_period(value))

    def test_weekday_in_uncovered_year_is_unknown(self):
        value = datetime(2027, 1, 4, 2, 0, tzinfo=timezone.utc)
        self.assertIsNone(classify_peak_period(value))

    def test_weekday_public_holiday_is_off_peak(self):
        value = datetime(2026, 2, 16, 2, 0, tzinfo=timezone.utc)
        self.assertFalse(classify_peak_period(value))

    def test_makeup_weekend_remains_off_peak(self):
        # 2026-02-14 is an official make-up workday, but DeepSeek says weekends
        # are off-peak; this classifier follows the provider's explicit rule.
        value = datetime(2026, 2, 14, 2, 0, tzinfo=timezone.utc)
        self.assertFalse(classify_peak_period(value))

    def test_timezone_conversion_is_applied_before_classification(self):
        # 00:30 UTC is 08:30 Beijing, before the morning peak.
        value = datetime(2026, 11, 19, 0, 30, tzinfo=timezone.utc)
        self.assertFalse(classify_peak_period(value))

    def test_rejects_naive_timestamp(self):
        with self.assertRaises(ValueError):
            classify_peak_period(datetime(2026, 11, 19, 9, 0))

    def test_rejects_non_datetime(self):
        with self.assertRaises(TypeError):
            classify_peak_period("2026-11-19T09:00:00+08:00")


if __name__ == "__main__":
    unittest.main()
