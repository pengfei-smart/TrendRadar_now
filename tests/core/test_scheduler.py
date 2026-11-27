
import unittest

from trendradar.core.scheduler import Scheduler


class SchedulerTimeRangeTests(unittest.TestCase):
    """Scheduler 时间区间边界测试"""

    def test_normal_range_uses_left_closed_right_open(self):
        cases = [
            ("07:59", "08:00", "09:00", False),
            ("08:00", "08:00", "09:00", True),
            ("08:59", "08:00", "09:00", True),
            ("09:00", "08:00", "09:00", False),
        ]

        for now, start, end, expected in cases:
            with self.subTest(now=now):
                self.assertEqual(
                    Scheduler._in_range(now, start, end),
                    expected,
                )

    def test_afternoon_range_boundaries(self):
        cases = [
            ("13:59", False),
            ("14:00", True),
            ("14:59", True),
            ("15:00", False),
        ]

        for now, expected in cases:
            with self.subTest(now=now):
                self.assertEqual(
                    Scheduler._in_range(now, "14:00", "15:00"),
                    expected,
                )

    def test_cross_midnight_range(self):
        cases = [
            ("22:59", False),
            ("23:00", True),
            ("23:59", True),
            ("00:00", True),
            ("04:59", True),
            ("05:00", False),
        ]

        for now, expected in cases:
            with self.subTest(now=now):
                self.assertEqual(
                    Scheduler._in_range(now, "23:00", "05:00"),
                    expected,
                )


class SchedulerAIAuthorizationTests(unittest.TestCase):
    """Scheduler AI 授权测试"""

    def setUp(self):
        from datetime import datetime

        self.current_time = datetime(2026, 11, 9, 8, 30)

        self.timeline = {
            "custom": {
                "default": {
                    "collect": True,
                    "analyze": False,
                    "push": False,
                },
                "periods": {
                    "morning": {
                        "start": "08:00",
                        "end": "09:00",
                        "analyze": True,
                    },
                    "afternoon": {
                        "start": "14:00",
                        "end": "15:00",
                        "analyze": True,
                    },
                    "night": {
                        "start": "23:00",
                        "end": "05:00",
                        "analyze": True,
                    },
                },
                "day_plans": {
                    "workday": {
                        "periods": ["morning", "afternoon", "night"],
                    },
                    "saturday": {"periods": []},
                    "sunday": {"periods": []},
                },
                "week_map": {
                    1: "workday",
                    2: "workday",
                    3: "workday",
                    4: "workday",
                    5: "workday",
                    6: "saturday",
                    7: "sunday",
                },
                "overlap": {"policy": "error_on_overlap"},
            }
        }

    def make_scheduler(self, enabled=True):
        return Scheduler(
            schedule_config={
                "enabled": enabled,
                "preset": "custom",
            },
            timeline_data=self.timeline,
            storage_backend=None,
            get_time_func=lambda: self.current_time,
        )

    def test_workday_allowed_window(self):
        scheduler = self.make_scheduler()

        self.assertTrue(scheduler.is_ai_allowed_now())

    def test_workday_outside_allowed_window(self):
        scheduler = self.make_scheduler()

        for hour, minute in [(7, 59), (9, 0), (13, 59), (15, 0), (20, 0)]:
            with self.subTest(hour=hour, minute=minute):
                self.current_time = self.current_time.replace(
                    hour=hour, minute=minute
                )
                self.assertFalse(scheduler.is_ai_allowed_now())

    def test_weekend_is_not_allowed(self):
        scheduler = self.make_scheduler()

        for day in (14, 15):
            with self.subTest(day=day):
                self.current_time = self.current_time.replace(
                    day=day, hour=8, minute=30
                )
                self.assertFalse(scheduler.is_ai_allowed_now())

    def test_disabled_schedule_is_not_allowed(self):
        scheduler = self.make_scheduler(enabled=False)

        self.assertFalse(scheduler.is_ai_allowed_now())

    def test_cross_midnight_period(self):
        scheduler = self.make_scheduler()

        self.current_time = self.current_time.replace(
            hour=23, minute=30
        )
        self.assertTrue(scheduler.is_ai_allowed_now())

        self.current_time = self.current_time.replace(
            hour=4, minute=59
        )
        self.assertTrue(scheduler.is_ai_allowed_now())

        self.current_time = self.current_time.replace(
            hour=5, minute=0
        )
        self.assertFalse(scheduler.is_ai_allowed_now())

    def test_authorization_reads_time_again_each_call(self):
        scheduler = self.make_scheduler()

        self.current_time = self.current_time.replace(hour=8, minute=30)
        self.assertTrue(scheduler.is_ai_allowed_now())

        self.current_time = self.current_time.replace(hour=9, minute=0)
        self.assertFalse(scheduler.is_ai_allowed_now())

if __name__ == "__main__":
    unittest.main()

class SchedulerRangeOverlapTests(unittest.TestCase):

    def test_adjacent_normal_ranges_do_not_overlap(self):
        self.assertFalse(
            Scheduler._ranges_overlap(
                "08:00", "09:00", "09:00", "10:00"
            )
        )

    def test_intersecting_normal_ranges_overlap(self):
        self.assertTrue(
            Scheduler._ranges_overlap(
                "08:00", "09:00", "08:30", "09:30"
            )
        )

    def test_cross_midnight_ranges_overlap(self):
        self.assertTrue(
            Scheduler._ranges_overlap(
                "23:00", "05:00", "04:00", "06:00"
            )
        )

    def test_cross_midnight_adjacent_ranges_do_not_overlap(self):
        self.assertFalse(
            Scheduler._ranges_overlap(
                "23:00", "05:00", "05:00", "06:00"
            )
        )

    def test_separated_ranges_do_not_overlap(self):
        self.assertFalse(
            Scheduler._ranges_overlap(
                "08:00", "09:00", "10:00", "11:00"
            )
        )

    def test_cross_midnight_and_early_morning_adjacency(self):
        self.assertFalse(
            Scheduler._ranges_overlap(
                "22:00", "23:00", "23:00", "23:30"
            )
        )
