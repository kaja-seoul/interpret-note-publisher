import unittest
from datetime import datetime

from publisher import cli, comments, config as C


def K(h, m, d=28):
    return datetime(2026, 10, d, h, m, tzinfo=C.KST)


class SlotWindowTests(unittest.TestCase):
    def test_windows(self):
        self.assertEqual(cli.current_slot(K(7, 13)), "08:07")
        self.assertEqual(cli.current_slot(K(9, 37)), "08:07")
        self.assertIsNone(cli.current_slot(K(9, 38)))
        self.assertIsNone(cli.current_slot(K(3, 2)))  # 10/28 03:02 엉뚱한 실행
        self.assertIsNone(cli.current_slot(K(7, 11)))
        self.assertEqual(cli.current_slot(K(20, 43)), "21:37")
        self.assertEqual(cli.current_slot(K(23, 7)), "21:37")
        self.assertIsNone(cli.current_slot(K(23, 8)))


class FakeNotion:
    def __init__(self, rows=None):
        self.rows, self.created, self.updates = rows or [], [], []

    def query(self, ds, filter=None, sorts=None):
        return self.rows

    def create(self, ds, props):
        self.created.append(props)

    def update(self, pid, props):
        self.updates.append((pid, props))


class FakeThreads:
    user_id = "me"

    def me(self):
        return {"id": "9", "username": "interpret.note"}

    def recent_posts(self, limit=10):
        return [{"id": "P1", "permalink": "https://t/p1"}, {"id": "P2", "permalink": "https://t/p2"},
                {"id": "R", "media_type": "REPOST_FACADE"}, {"id": "P3"}, {"id": "P4"}]

    def replies(self, mid):
        if mid != "P1":
            return []
        return [
            {"id": "A", "username": "kim", "text": "질문 있어요", "timestamp": "2026-10-28T00:10:00+0000",
             "replied_to": {"id": "P1"}, "permalink": "https://t/a"},
            {"id": "SRC", "username": "interpret.note", "is_reply_owned_by_me": True, "replied_to": {"id": "P1"}},
            {"id": "B", "username": "lee", "text": "좋아요", "replied_to": {"id": "A"}},
            {"id": "M", "username": "interpret.note", "is_reply_owned_by_me": True, "replied_to": {"id": "B"}},
        ]


class CommentTests(unittest.TestCase):
    def test_collect(self):
        nt = FakeNotion()
        res = comments.run(nt, FakeThreads(), K(9, 5), log=lambda *a: None)
        self.assertEqual(res["posts"], 3)  # 리포스트 빼고 최근 3개
        self.assertEqual(res["new"], 2)
        a, b = nt.created
        self.assertEqual(a["답글 ID"]["rich_text"][0]["text"]["content"], "A")
        self.assertFalse(a["내가 답함"]["checkbox"])
        self.assertEqual(a["상태"]["select"]["name"], "새로")
        self.assertFalse(a["대댓"]["checkbox"])
        self.assertTrue(b["내가 답함"]["checkbox"])   # 내가 B에 답함
        self.assertEqual(b["상태"]["select"]["name"], "보냄")
        self.assertTrue(b["대댓"]["checkbox"])        # A에 단 답글
        self.assertNotIn("답글 링크", b)              # 링크 없으면 넣지 않음

    def test_existing_updated_not_duplicated(self):
        row = {"id": "row-A", "properties": {
            "답글 ID": {"type": "rich_text", "rich_text": [{"plain_text": "A"}]},
            "내가 답함": {"type": "checkbox", "checkbox": False},
            "상태": {"type": "select", "select": {"name": "초안"}},
        }}
        th = FakeThreads()
        base = th.replies("P1")
        th.replies = lambda mid: (base + [{"id": "M2", "username": "interpret.note", "is_reply_owned_by_me": True,
                                           "replied_to": {"id": "A"}}]) if mid == "P1" else []
        nt = FakeNotion([row])
        res = comments.run(nt, th, K(22, 35), log=lambda *a: None)
        self.assertEqual(res["new"], 1)  # B만 새로
        pid, props = nt.updates[0]
        self.assertEqual(pid, "row-A")
        self.assertTrue(props["내가 답함"]["checkbox"])
        self.assertEqual(props["상태"]["select"]["name"], "보냄")


if __name__ == "__main__":
    unittest.main()
