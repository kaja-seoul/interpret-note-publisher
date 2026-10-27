"""슬롯 고르기 규칙 시험 (가짜 Notion)."""
import unittest
from datetime import datetime
from unittest import mock

from publisher import cli, config as C


def row(pid, date, slot, status="승인", link=""):
    return {
        "id": pid,
        "properties": {
            "제목": {"type": "title", "title": [{"plain_text": pid}]},
            "상태": {"type": "status", "status": {"name": status}},
            "게시 예정": {"type": "date", "date": {"start": date}},
            "슬롯": {"type": "select", "select": {"name": slot}},
            "게시 링크": {"type": "url", "url": link or None},
        },
    }


class FakeNotion:
    def __init__(self, rows):
        self.rows = rows
        self.comments = []

    def query(self, ds, filter=None, sorts=None):
        return [r for r in self.rows if r["properties"]["상태"]["status"]["name"] == "승인"]

    def page(self, pid):
        return next(r for r in self.rows if r["id"] == pid)

    def comment(self, pid, text):
        self.comments.append((pid, text))

    def update(self, pid, props):
        r = self.page(pid)
        if "상태" in props:
            r["properties"]["상태"]["status"]["name"] = props["상태"]["status"]["name"]


NOW = datetime(2026, 10, 28, 7, 50, tzinfo=C.KST)
EVE = datetime(2026, 10, 28, 21, 20, tzinfo=C.KST)


class PickTests(unittest.TestCase):
    def setUp(self):
        self.p = mock.patch.object(cli, "NOTICES", cli.ROOT / "notices.test.txt")
        self.p.start()

    def tearDown(self):
        (cli.ROOT / "notices.test.txt").unlink(missing_ok=True)
        self.p.stop()

    def test_exact_slot(self):
        nt = FakeNotion([row("a", "2026-10-28", "08:07"), row("b", "2026-10-28", "21:37")])
        page, when = cli.pick(nt, "08:07", NOW, None)
        self.assertEqual(page["id"], "a")
        self.assertEqual(when.hour, 8)

    def test_nothing_in_evening(self):
        nt = FakeNotion([row("a", "2026-10-28", "08:07", status="게시됨")])
        page, _ = cli.pick(nt, "21:37", EVE, None)
        self.assertIsNone(page)

    def test_late_approved_goes_to_next_slot(self):
        nt = FakeNotion([row("a", "2026-10-28", "08:07")])
        page, when = cli.pick(nt, "21:37", EVE, None)
        self.assertEqual(page["id"], "a")
        self.assertEqual((when.hour, when.minute), (21, 37))

    def test_late_blocked_when_slot_taken(self):
        nt = FakeNotion([row("a", "2026-10-28", "08:07"), row("b", "2026-10-28", "21:37")])
        page, _ = cli.pick(nt, "21:37", EVE, None)
        self.assertEqual(page["id"], "b")
        self.assertTrue(any(pid == "a" for pid, _ in nt.comments))

    def test_two_approved_same_slot_blocks(self):
        nt = FakeNotion([row("a", "2026-10-28", "08:07"), row("b", "2026-10-28", "08:07")])
        page, _ = cli.pick(nt, "08:07", NOW, None)
        self.assertIsNone(page)
        self.assertEqual(len(nt.comments), 2)

    def test_stale_overdue_not_posted(self):
        nt = FakeNotion([row("a", "2026-10-26", "08:07")])
        page, _ = cli.pick(nt, "08:07", NOW, None)
        self.assertIsNone(page)
        self.assertTrue(nt.comments)
        self.assertEqual(nt.rows[0]["properties"]["상태"]["status"]["name"], "보류")

    def test_already_linked_skipped(self):
        nt = FakeNotion([row("a", "2026-10-28", "08:07", link="https://www.threads.com/@x/post/1")])
        page, _ = cli.pick(nt, "08:07", NOW, None)
        self.assertIsNone(page)

    def test_manual_slot_ignored(self):
        nt = FakeNotion([row("a", "2026-10-28", "수동")])
        page, _ = cli.pick(nt, "08:07", NOW, None)
        self.assertIsNone(page)

    def test_datetime_date(self):
        nt = FakeNotion([row("a", "2026-10-28T08:07:00.000+09:00", "08:07")])
        page, _ = cli.pick(nt, "08:07", NOW, None)
        self.assertEqual(page["id"], "a")

    def test_page_id_must_be_approved(self):
        nt = FakeNotion([row("a", "2026-10-28", "08:07", status="승인 대기")])
        page, _ = cli.pick(nt, "08:07", NOW, "a")
        self.assertIsNone(page)


if __name__ == "__main__":
    unittest.main()
