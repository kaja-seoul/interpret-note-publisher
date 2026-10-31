import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from publisher import config as C, metrics


def logrow(pid, title, when, link, memo="", views=None, before=None, daily=None):
    return {
        "id": pid,
        "properties": {
            "글": {"type": "title", "title": [{"plain_text": title}]},
            "게시 시각": {"type": "date", "date": {"start": when}},
            "게시 링크": {"type": "url", "url": link},
            "메모": {"type": "rich_text", "rich_text": [{"plain_text": memo}] if memo else []},
            "조회 24h": {"type": "number", "number": views},
            "팔로워 게시 전": {"type": "number", "number": before},
            "그날 팔로워 증감": {"type": "number", "number": daily},
        },
    }


class FakeNotion:
    def __init__(self, rows):
        self.rows, self.updates = rows, {}

    def query(self, ds, filter=None, sorts=None):
        return self.rows

    def update(self, pid, props):
        self.updates.setdefault(pid, {}).update(props)


class FakeThreads:
    user_id = "me"

    def me(self):
        return {"id": "9", "username": "example.account"}

    def followers_count(self):
        return 30

    def my_posts(self, max_pages=4):
        return [{"id": "111", "permalink": "https://www.threads.com/@example.account/post/De-687HGP1t"}]

    def media_insights(self, mid):
        return {"views": 500, "likes": 9, "replies": 3, "reposts": 2, "quotes": 1, "shares": 4}

    def replies(self, mid):
        return [
            {"username": "someone", "is_reply_owned_by_me": False, "timestamp": "2026-10-27T05:00:00+0000"},
            {"username": "example.account", "is_reply_owned_by_me": True, "timestamp": "2026-10-27T03:44:00+0000"},
            {"username": "late", "is_reply_owned_by_me": False, "timestamp": "2026-10-28T09:00:00+0000"},
        ]


class MetricsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.p = mock.patch.object(metrics, "FOLLOWERS_CSV", self.tmp / "followers.csv")
        self.p.start()

    def tearDown(self):
        self.p.stop()
        shutil.rmtree(self.tmp)

    def test_fill_after_24h_from_permalink(self):
        nt = FakeNotion([logrow("a", "#002", "2026-10-27T03:43:00.000+00:00",
                                "https://www.threads.com/@example.account/post/De-687HGP1t", before=17)])
        now = datetime(2026, 10, 28, 12, 47, tzinfo=C.KST)  # 24.07시간 뒤
        res = metrics.run(nt, FakeThreads(), now, log=lambda *a: None)
        u = nt.updates["a"]
        self.assertEqual(u["조회 24h"]["number"], 500)
        self.assertEqual(u["공유 24h"]["number"], 4)
        self.assertEqual(u["외부 댓글 24h"]["number"], 1)  # 내 답글·24시간 뒤 답글 제외
        self.assertEqual(u["팔로워 24h 뒤"]["number"], 30)
        self.assertEqual(len(res["filled"]), 1)

    def test_not_yet_24h(self):
        nt = FakeNotion([logrow("a", "#003", "2026-10-28T08:09:00+09:00", "https://x/post/AAA", memo="media 222")])
        metrics.run(nt, FakeThreads(), datetime(2026, 10, 29, 7, 47, tzinfo=C.KST), log=lambda *a: None)
        self.assertEqual(nt.updates, {})

    def test_too_late_marked(self):
        nt = FakeNotion([logrow("a", "#001", "2026-10-24T02:19:00+00:00", "https://x/post/OLD", memo="old")])
        res = metrics.run(nt, FakeThreads(), datetime(2026, 10, 28, 0, 47, tzinfo=C.KST), log=lambda *a: None)
        self.assertIn("지표 못 잼", nt.updates["a"]["메모"]["rich_text"][0]["text"]["content"])
        self.assertEqual(res["late"], ["#001"])

    def test_excluded_duplicate(self):
        nt = FakeNotion([logrow("a", "dup", "2026-10-27T03:43:00+00:00", "https://x/post/De-687HGP1t",
                                memo="중복. 집계에서 뺀다.")])
        metrics.run(nt, FakeThreads(), datetime(2026, 10, 28, 12, 47, tzinfo=C.KST), log=lambda *a: None)
        self.assertEqual(nt.updates, {})

    def test_daily_snapshot_and_change(self):
        rows = [
            logrow("m", "아침", "2026-10-28T08:09:00+09:00", "https://x/post/A", memo="media 1", views=10, before=30),
            logrow("e", "저녁", "2026-10-28T21:40:00+09:00", "https://x/post/B", memo="media 2", views=None, before=31),
        ]
        nt = FakeNotion(rows)
        th = FakeThreads()
        th.followers_count = lambda: 30
        metrics.run(nt, th, datetime(2026, 10, 28, 8, 47, tzinfo=C.KST), log=lambda *a: None)
        self.assertEqual(metrics.load_snapshots()[datetime(2026, 10, 28).date()], 30)
        th.followers_count = lambda: 37
        metrics.run(nt, th, datetime(2026, 10, 29, 8, 47, tzinfo=C.KST), log=lambda *a: None)
        self.assertEqual(nt.updates["m"]["그날 팔로워 증감"]["number"], 7)
        self.assertEqual(nt.updates["e"]["그날 팔로워 증감"]["number"], 7)

    def test_snapshot_only_once_per_day(self):
        nt = FakeNotion([])
        metrics.run(nt, FakeThreads(), datetime(2026, 10, 28, 8, 47, tzinfo=C.KST), log=lambda *a: None)
        res = metrics.run(nt, FakeThreads(), datetime(2026, 10, 28, 8, 59, tzinfo=C.KST), log=lambda *a: None)
        self.assertIsNone(res["snapshot"])

    def test_daily_fallback_to_followers_before(self):
        nt = FakeNotion([logrow("a", "#002", "2026-10-27T03:43:00+00:00", "https://x/post/X", views=1, before=17)])
        th = FakeThreads()
        th.followers_count = lambda: 26
        metrics.run(nt, th, datetime(2026, 10, 28, 8, 47, tzinfo=C.KST), log=lambda *a: None)
        self.assertEqual(nt.updates["a"]["그날 팔로워 증감"]["number"], 9)


if __name__ == "__main__":
    unittest.main()
