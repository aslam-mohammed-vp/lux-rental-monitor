import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from scripts.check_schedule import main, should_run


class ScheduleTest(unittest.TestCase):
    def test_default_window_boundaries_and_delay(self):
        for hour, minute, expected in [(6, 59, False), (7, 0, True),
                                       (7, 17, True), (23, 0, True),
                                       (23, 14, True), (23, 15, False)]:
            with self.subTest(hour=hour, minute=minute):
                self.assertEqual(should_run(datetime(2026, 11, 3, hour, minute)), expected)

    def test_configured_interval_and_overnight_window(self):
        self.assertTrue(should_run(datetime(2026, 11, 3, 23, 2), "22:00", "06:00", "30"))
        self.assertFalse(should_run(datetime(2026, 11, 3, 23, 17), "22:00", "06:00", "30"))
        self.assertTrue(should_run(datetime(2026, 11, 3, 6), "22:00", "06:00", "30"))
        self.assertFalse(should_run(datetime(2026, 11, 3, 12), "22:00", "06:00", "30"))

    def test_local_window_in_winter_and_summer(self):
        for month, utc_hour in [(1, 6), (7, 5)]:
            local = datetime(2026, month, 3, utc_hour, tzinfo=ZoneInfo("UTC")).astimezone(
                ZoneInfo("Europe/Luxembourg"))
            self.assertEqual(local.hour, 7)
            self.assertTrue(should_run(local))

    def test_invalid_configuration_fails(self):
        for config in [("7:00", "23:00", "15"), ("07:05", "23:00", "15"),
                       ("07:00", "24:00", "15"), ("07:00", "23:00", "0"),
                       ("07:00", "23:00", "20")]:
            with self.subTest(config=config), self.assertRaises(ValueError):
                should_run(datetime(2026, 11, 3, 12), *config)

    def test_manual_run_bypasses_window_and_outputs_decision(self):
        with tempfile.NamedTemporaryFile(mode="r+") as output:
            with patch.dict(os.environ, {"GITHUB_EVENT_NAME": "workflow_dispatch",
                                         "GITHUB_OUTPUT": output.name,
                                         "MONITOR_START": "invalid"}):
                main()
            self.assertEqual(output.read(), "run_monitor=true\n")


if __name__ == "__main__":
    unittest.main()
