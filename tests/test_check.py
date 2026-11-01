"""check 명령 시험: 슬롯이 지난 '승인' 행 찾기, Threads API 상태."""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from publisher import cli, config as C
from publisher.threads import ThreadsError
from tests.test_pick import FakeNotion, row


def kst(d, h, m):
    return datetime(2026, 10, d, h, m, tzinfo=C.KST)


class GoodThreads:
    def me(self):
        return {"id": "9", "username": "example.account"}


class DeadThreads:
    def me(self):
        raise ThreadsError("GET me 실패 (HTTP 400): API access blocked.")


class FindStuckTests(unittest.TestCase):
    def test_in_window_and_over_window(self):
        nt = FakeNotion([
            row("a", "2026-10-31", "08:07"),              # 08:07 슬롯, 지금 08:50 → 창 안
            row("b", "2026-10-30", "21:37"),              # 어제 저녁, 창 끝남, 36시간 안
            row("c", "2026-10-31", "21:37"),              # 오늘 저녁, 아직 안 옴
            row("d", "2026-10-31", "08:07", link="https://t/x"),  # 이미 올림
            row("e", "2026-10-29", "08:07"),              # 36시간 넘음 → 게시기가 보류로 옮김
        ])
        out = cli.find_stuck(nt, kst(31, 8, 50))
        got = {r["id"]: in_window for r, when, in_window in out}
        self.assertEqual(got, {"a": True, "b": False})

    def test_nothing(self):
        nt = FakeNotion([row("c", "2026-10-31", "21:37")])
        self.assertEqual(cli.find_stuck(nt, kst(31, 8, 50)), [])


class CmdCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.outputs = self.tmp / "out.txt"
        self.outputs.write_text("")
        mock.patch.object(cli, "NOTICES", self.tmp / "notices.txt").start()
        mock.patch.object(cli, "POSTS", self.tmp / "posts").start()
        mock.patch.object(cli, "now_kst", lambda: kst(31, 8, 50)).start()
        mock.patch.object(cli.C, "RUNLOG_DS", "").start()
        mock.patch.dict("os.environ", {"GITHUB_OUTPUT": str(self.outputs)}).start()

    def tearDown(self):
        mock.patch.stopall()

    def outs(self):
        return dict(l.split("=", 1) for l in self.outputs.read_text().splitlines() if "=" in l)

    def test_all_good(self):
        nt = FakeNotion([row("c", "2026-10-31", "21:37")])
        mock.patch.object(cli, "notion", lambda: nt).start()
        mock.patch.object(cli, "threads", lambda: GoodThreads()).start()
        self.assertEqual(cli.main(["check"]), 0)
        self.assertEqual(self.outs()["rekick"], "false")
        self.assertEqual(self.outs()["notices"], "false")

    def test_rekick_inside_window(self):
        nt = FakeNotion([row("a", "2026-10-31", "08:07")])
        mock.patch.object(cli, "notion", lambda: nt).start()
        mock.patch.object(cli, "threads", lambda: GoodThreads()).start()
        self.assertEqual(cli.main(["check"]), 0)
        self.assertEqual(self.outs()["rekick"], "true")
        self.assertEqual(nt.comments, [])

    def test_stuck_after_window_is_red_with_comment(self):
        nt = FakeNotion([row("b", "2026-10-30", "21:37")])
        mock.patch.object(cli, "notion", lambda: nt).start()
        mock.patch.object(cli, "threads", lambda: GoodThreads()).start()
        self.assertEqual(cli.main(["check"]), 1)
        self.assertEqual(self.outs()["rekick"], "false")
        self.assertEqual(len(nt.comments), 1)
        self.assertIn("아직 '승인'", nt.comments[0][1])
        self.assertEqual(self.outs()["notices"], "true")

    def test_dead_threads_api_is_red(self):
        nt = FakeNotion([])
        mock.patch.object(cli, "notion", lambda: nt).start()
        mock.patch.object(cli, "threads", lambda: DeadThreads()).start()
        self.assertEqual(cli.main(["check"]), 1)

    def test_wrong_account_is_red(self):
        nt = FakeNotion([])

        class Other:
            def me(self):
                return {"id": "1", "username": "someone.else"}

        mock.patch.object(cli, "notion", lambda: nt).start()
        mock.patch.object(cli, "threads", lambda: Other()).start()
        self.assertEqual(cli.main(["check"]), 1)


class RunLogNotion(FakeNotion):
    """캘린더 행과 런 로그 행을 데이터 소스별로 돌려준다."""

    def __init__(self, rows, runlog):
        super().__init__(rows)
        self.runlog = runlog  # [(date_iso, job)]

    def query(self, ds, filter=None, sorts=None):
        if ds == "runlog":
            day = filter["and"][0]["date"]["equals"]
            job = filter["and"][1]["select"]["equals"]
            return [{"id": "r"} for d, j in self.runlog if d == day and j == job]
        return super().query(ds, filter, sorts)


def ledger(tmp, day, slot, media="1"):
    d = tmp / "posts" / f"{day.replace('-', '')}-{slot.replace(':', '')}-abc"
    d.mkdir(parents=True, exist_ok=True)
    import json
    (d / "post.json").write_text(json.dumps({"media_id": media, "posted_at": f"{day}T08:10:00+09:00"}), encoding="utf-8")


class HeartbeatAndZeroDayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.outputs = self.tmp / "out.txt"
        self.outputs.write_text("")
        mock.patch.object(cli, "NOTICES", self.tmp / "notices.txt").start()
        mock.patch.object(cli, "POSTS", self.tmp / "posts").start()
        mock.patch.object(cli, "threads", lambda: GoodThreads()).start()
        mock.patch.dict("os.environ", {"GITHUB_OUTPUT": str(self.outputs)}).start()

    def tearDown(self):
        mock.patch.stopall()

    def test_morning_run_logged_is_green(self):
        mock.patch.object(cli.C, "RUNLOG_DS", "runlog").start()
        mock.patch.object(cli, "now_kst", lambda: kst(31, 8, 50)).start()
        nt = RunLogNotion([], [("2026-10-31", "아침 런")])
        mock.patch.object(cli, "notion", lambda: nt).start()
        ledger(self.tmp, "2026-10-30", "21:37")
        self.assertEqual(cli.main(["check"]), 0)

    def test_morning_run_missing_is_red(self):
        mock.patch.object(cli.C, "RUNLOG_DS", "runlog").start()
        mock.patch.object(cli, "now_kst", lambda: kst(31, 8, 50)).start()
        nt = RunLogNotion([], [("2026-10-30", "아침 런")])
        mock.patch.object(cli, "notion", lambda: nt).start()
        ledger(self.tmp, "2026-10-30", "21:37")
        self.assertEqual(cli.main(["check"]), 1)

    def test_evening_expects_evening_job(self):
        mock.patch.object(cli.C, "RUNLOG_DS", "runlog").start()
        mock.patch.object(cli, "now_kst", lambda: kst(31, 22, 20)).start()
        nt = RunLogNotion([], [("2026-10-31", "저녁 글감 런")])
        mock.patch.object(cli, "notion", lambda: nt).start()
        ledger(self.tmp, "2026-10-31", "08:07")
        self.assertEqual(cli.main(["check"]), 0)

    def test_no_runlog_configured_skips_heartbeat(self):
        mock.patch.object(cli.C, "RUNLOG_DS", "").start()
        mock.patch.object(cli, "now_kst", lambda: kst(31, 8, 50)).start()
        nt = RunLogNotion([], [])
        mock.patch.object(cli, "notion", lambda: nt).start()
        ledger(self.tmp, "2026-10-31", "08:07")
        self.assertEqual(cli.main(["check"]), 0)

    def test_zero_days_three_is_red_two_is_warning(self):
        mock.patch.object(cli.C, "RUNLOG_DS", "").start()
        nt = RunLogNotion([], [])
        mock.patch.object(cli, "notion", lambda: nt).start()
        ledger(self.tmp, "2026-10-28", "21:37")
        mock.patch.object(cli, "now_kst", lambda: kst(30, 8, 50)).start()
        self.assertEqual(cli.main(["check"]), 0)       # 2일째: 경고만
        mock.patch.object(cli, "now_kst", lambda: kst(31, 8, 50)).start()
        self.assertEqual(cli.main(["check"]), 1)       # 3일째: 빨강

    def test_no_ledger_at_all_is_quiet(self):
        mock.patch.object(cli.C, "RUNLOG_DS", "").start()
        nt = RunLogNotion([], [])
        mock.patch.object(cli, "notion", lambda: nt).start()
        mock.patch.object(cli, "now_kst", lambda: kst(31, 8, 50)).start()
        self.assertEqual(cli.main(["check"]), 0)
