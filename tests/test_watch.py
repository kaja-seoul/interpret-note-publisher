"""감시 모드(prepare --watch) 시험: 승인이 늦게 와도 창 안이면 잡고, 창이 끝나면 조용히 끝난다."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from publisher import cli, config as C
from tests.test_pick import FakeNotion, row


class Clock:
    """now_kst 대역: sleep을 부를 때마다 시간이 간다."""

    def __init__(self, start):
        self.now = start

    def __call__(self):
        return self.now

    def sleep(self, sec):
        self.now += timedelta(seconds=sec)


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.clock = Clock(datetime(2026, 10, 31, 7, 16, tzinfo=C.KST))
        self.staged = []
        mock.patch.object(cli, "NOTICES", self.tmp / "notices.txt").start()
        mock.patch.object(cli, "POSTS", self.tmp / "posts").start()
        mock.patch.object(cli, "now_kst", self.clock).start()
        mock.patch.object(cli.time, "sleep", self.clock.sleep).start()
        mock.patch.object(cli.C, "LIVE", True).start()
        self.outputs = self.tmp / "out.txt"
        self.outputs.write_text("")
        mock.patch.dict("os.environ", {"GITHUB_OUTPUT": str(self.outputs)}).start()

    def tearDown(self):
        mock.patch.stopall()

    def outs(self):
        d = {}
        for l in self.outputs.read_text().splitlines():
            if "=" in l:
                k, v = l.split("=", 1)
                d[k] = v
        return d

    def fake_stage(self, rc=0):
        def stage(nt, page, slot, slot_dt, now, a):
            self.staged.append((page["id"], now))
            cli.gh_output(has_post="true" if rc == 0 else "false")
            return rc
        return stage

    def test_late_approval_is_caught(self):
        nt = FakeNotion([row("a", "2026-10-31", "08:07", status="승인 대기")])
        mock.patch.object(cli, "notion", lambda: nt).start()
        mock.patch.object(cli, "stage", self.fake_stage()).start()
        approve_at = datetime(2026, 10, 31, 8, 20, tzinfo=C.KST)
        orig_query = nt.query

        def query(ds, filter=None, sorts=None):
            if self.clock.now >= approve_at:
                nt.rows[0]["properties"]["상태"]["status"]["name"] = "승인"
            return orig_query(ds, filter, sorts)
        nt.query = query
        self.assertEqual(cli.main(["prepare", "--slot", "auto", "--watch"]), 0)
        self.assertEqual(len(self.staged), 1)
        self.assertGreaterEqual(self.staged[0][1], approve_at)
        self.assertLess(self.staged[0][1], approve_at + timedelta(seconds=C.WATCH_INTERVAL_SEC + 1))
        self.assertEqual(self.outs()["has_post"], "true")

    def test_window_end_without_post(self):
        nt = FakeNotion([])
        mock.patch.object(cli, "notion", lambda: nt).start()
        mock.patch.object(cli, "stage", self.fake_stage()).start()
        self.assertEqual(cli.main(["prepare", "--slot", "auto", "--watch"]), 0)
        self.assertEqual(self.staged, [])
        self.assertEqual(self.outs()["has_post"], "false")
        end = datetime(2026, 10, 31, 9, 37, tzinfo=C.KST)
        self.assertGreaterEqual(self.clock.now, end)
        self.assertLess(self.clock.now, end + timedelta(seconds=C.WATCH_INTERVAL_SEC + 1))

    def test_format_fail_is_not_restaged_until_changed(self):
        nt = FakeNotion([row("a", "2026-10-31", "08:07")])
        mock.patch.object(cli, "notion", lambda: nt).start()
        results = iter([1, 0])

        def stage(nt_, page, slot, slot_dt, now, a):
            rc = next(results)
            self.staged.append((page["id"], now, rc))
            cli.gh_output(has_post="true" if rc == 0 else "false")
            return rc
        mock.patch.object(cli, "stage", stage).start()
        fix_at = datetime(2026, 10, 31, 8, 0, tzinfo=C.KST)
        orig_query = nt.query

        def query(ds, filter=None, sorts=None):
            if self.clock.now >= fix_at:
                nt.rows[0]["properties"]["본문"] = {"type": "rich_text", "rich_text": [{"plain_text": "고침"}]}
            return orig_query(ds, filter, sorts)
        nt.query = query
        self.assertEqual(cli.main(["prepare", "--slot", "auto", "--watch"]), 0)
        self.assertEqual([s[2] for s in self.staged], [1, 0])
        self.assertLess(self.staged[0][1], fix_at)
        self.assertGreaterEqual(self.staged[1][1], fix_at)

    def test_already_posted_slot_ends_at_once(self):
        d = self.tmp / "posts" / "20261031-0807-abc"
        d.mkdir(parents=True)
        (d / "post.json").write_text(json.dumps({"media_id": "1"}), encoding="utf-8")
        nt = FakeNotion([row("a", "2026-10-31", "08:07")])
        mock.patch.object(cli, "notion", lambda: nt).start()
        mock.patch.object(cli, "stage", self.fake_stage()).start()
        self.assertEqual(cli.main(["prepare", "--slot", "auto", "--watch"]), 0)
        self.assertEqual(self.staged, [])
        self.assertEqual(self.clock.now, datetime(2026, 10, 31, 7, 16, tzinfo=C.KST))

    def test_duplicate_notices_once(self):
        nt = FakeNotion([row("a", "2026-10-31", "08:07"), row("b", "2026-10-31", "08:07")])
        mock.patch.object(cli, "notion", lambda: nt).start()
        mock.patch.object(cli, "stage", self.fake_stage()).start()
        self.assertEqual(cli.main(["prepare", "--slot", "auto", "--watch"]), 0)
        self.assertEqual(len(nt.comments), 2)  # 행마다 한 번씩만 (몇 분마다 반복하지 않음)
        self.assertEqual(self.staged, [])
