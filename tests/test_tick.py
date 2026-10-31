"""tick 판정 시험: 한국 시각에 따라 무엇을 깨우나."""
import unittest
from datetime import datetime

from publisher import config as C
from publisher.tick import decide


def at(h, m):
    return datetime(2026, 11, 3, h, m, tzinfo=C.KST)


class TickTests(unittest.TestCase):
    def test_morning_kick_window(self):
        self.assertIn("kick", decide(at(7, 15)))
        self.assertIn("kick", decide(at(7, 30)))
        self.assertIn("kick", decide(at(7, 44)))
        self.assertNotIn("kick", decide(at(7, 14)))
        self.assertNotIn("kick", decide(at(7, 45)))

    def test_evening_kick_window(self):
        self.assertIn("kick", decide(at(20, 45)))
        self.assertIn("kick", decide(at(21, 14)))
        self.assertNotIn("kick", decide(at(21, 15)))
        self.assertNotIn("kick", decide(at(20, 44)))

    def test_check_windows(self):
        self.assertIn("check", decide(at(8, 45)))
        self.assertIn("check", decide(at(9, 0)))
        self.assertNotIn("check", decide(at(9, 15)))
        self.assertIn("check", decide(at(22, 15)))
        self.assertIn("check", decide(at(22, 44)))
        self.assertNotIn("check", decide(at(22, 45)))

    def test_hourly_metrics_and_comments(self):
        self.assertEqual(decide(at(10, 0)), ["metrics", "comments"])
        self.assertEqual(decide(at(10, 14)), ["metrics", "comments"])
        self.assertEqual(decide(at(10, 15)), [])
        self.assertEqual(decide(at(10, 47)), [])

    def test_combined(self):
        self.assertEqual(decide(at(7, 0)), ["metrics", "comments"])
        self.assertEqual(decide(at(7, 15)), ["kick"])
        self.assertEqual(decide(at(9, 0)), ["check", "metrics", "comments"])
        self.assertEqual(decide(at(22, 0)), ["metrics", "comments"])
        self.assertEqual(decide(at(22, 15)), ["check"])
