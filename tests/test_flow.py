"""prepare → build-site → post 전체 흐름 시험 (Notion·Threads·HTTP 모두 가짜)."""
import json
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from publisher import cli, config as C
from tests.test_checks import N002

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100


def cal_row():
    return {
        "id": "11111111-2222-3333-4444-555555555555",
        "url": "https://www.notion.so/x",
        "properties": {
            "제목": {"type": "title", "title": [{"plain_text": "통역 노트 #003"}]},
            "상태": {"type": "status", "status": {"name": "승인"}},
            "게시 예정": {"type": "date", "date": {"start": "2026-10-28"}},
            "슬롯": {"type": "select", "select": {"name": "08:07"}},
            "게시 링크": {"type": "url", "url": None},
            "본문": {"type": "rich_text", "rich_text": [{"plain_text": N002}]},
            "첫 답글": {"type": "rich_text", "rich_text": [{"plain_text": "통역 노트 #003 · 출처 ↓\n\nhttps://example.com/a"}]},
            "카드": {"type": "files", "files": [
                {"name": "실밸통역사_통역노트003_2of2.png", "type": "file", "file": {"url": "https://n/2"}},
                {"name": "실밸통역사_통역노트003_1of2.png", "type": "file", "file": {"url": "https://n/1"}},
            ]},
            "연재": {"type": "select", "select": {"name": "통역 노트"}},
            "번호": {"type": "number", "number": 3},
            "변수": {"type": "select", "select": None},
            "메모": {"type": "rich_text", "rich_text": [{"plain_text": "변수 B · 템플릿 8"}]},
            "원문 링크": {"type": "url", "url": "https://example.com/a"},
        },
    }


class FakeNotion:
    def __init__(self):
        self.row = cal_row()
        self.updates, self.created, self.comments = [], [], []

    def query(self, ds, filter=None, sorts=None):
        return [self.row] if self.row["properties"]["상태"]["status"]["name"] == "승인" else []

    def page(self, pid):
        return self.row

    def update(self, pid, props):
        self.updates.append(props)

    def create(self, ds, props):
        self.created.append((ds, props))

    def comment(self, pid, text):
        self.comments.append(text)


class FakeThreads:
    def __init__(self):
        self.user_id = "me"
        self.published, self.containers = [], []

    def me(self):
        return {"id": "99", "username": "interpret.note"}

    def followers_count(self):
        return 42

    def recent_posts(self, limit=10):
        return [{"id": m, "text": t} for m, t in self.published]

    def create_post_container(self, text, urls, alt_texts=None):
        self.containers.append(("post", text, urls))
        return "c1"

    def create_reply_container(self, text, reply_to):
        self.containers.append(("reply", text, reply_to))
        return "c2"

    def wait_ready(self, cid, max_wait=300):
        return None

    def publish(self, cid):
        mid = "m1" if cid == "c1" else "r1"
        text = next(c[1] for c in self.containers if (c[0] == "post") == (cid == "c1"))
        if cid == "c1":
            self.published.append((mid, text))
        return mid

    def permalink(self, mid):
        return "https://www.threads.com/@interpret.note/post/ABC"

    def replies(self, mid):
        return []


class Resp:
    def __init__(self, content=b"", status=200, ctype="image/png"):
        self.content, self.status_code, self.headers = content, status, {"content-type": ctype}

    def raise_for_status(self):
        pass


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.patches = [
            mock.patch.object(cli, "ROOT", self.tmp),
            mock.patch.object(cli, "STAGED", self.tmp / "staged"),
            mock.patch.object(cli, "POSTS", self.tmp / "posts"),
            mock.patch.object(cli, "NOTICES", self.tmp / "notices.txt"),
            mock.patch.object(cli, "now_kst", lambda: datetime(2026, 10, 28, 7, 50, tzinfo=C.KST)),
            mock.patch.object(cli.requests, "get", lambda url, timeout=0: Resp(PNG)),
            mock.patch.object(cli.requests, "head", lambda url, timeout=0, allow_redirects=True: Resp()),
            mock.patch.object(cli, "wait_until", lambda t: None),
            mock.patch.object(cli.time, "sleep", lambda s: None),
        ]
        for p in self.patches:
            p.start()
        self.nt, self.th = FakeNotion(), FakeThreads()
        mock.patch.object(cli, "notion", lambda: self.nt).start()
        mock.patch.object(cli, "threads", lambda: self.th).start()

    def tearDown(self):
        mock.patch.stopall()
        shutil.rmtree(self.tmp)

    def run_cli(self, *args):
        return cli.main(list(args))

    def prepare(self):
        self.assertEqual(self.run_cli("prepare", "--slot", "08:07"), 0)
        keys = [d.name for d in (self.tmp / "staged").iterdir()]
        self.assertEqual(len(keys), 1)
        return keys[0]

    def test_full_post(self):
        key = self.prepare()
        meta = json.loads((self.tmp / "staged" / key / "post.json").read_text())
        self.assertEqual([c["name"][-8:] for c in meta["cards"]], ["1of2.png", "2of2.png"])
        self.assertEqual(meta["variable"], "B")
        target = datetime.fromisoformat(meta["target"])
        self.assertTrue(datetime(2026, 10, 28, 8, 7, tzinfo=C.KST) <= target < datetime(2026, 10, 28, 8, 17, tzinfo=C.KST))

        self.assertEqual(self.run_cli("build-site"), 0)
        self.assertTrue((self.tmp / "_site" / "c" / key / "card1.png").exists())

        self.assertEqual(self.run_cli("post", "--key", key, "--pages-url", "https://o.github.io/r/"), 0)
        kind, text, urls = self.th.containers[0]
        self.assertEqual(urls, [f"https://o.github.io/r/c/{key}/card1.png", f"https://o.github.io/r/c/{key}/card2.png"])
        self.assertEqual(self.th.containers[1][0], "reply")
        self.assertEqual(self.nt.updates[0]["상태"]["status"]["name"], "게시됨")
        ds, props = self.nt.created[0]
        self.assertEqual(ds, C.POSTLOG_DS)
        self.assertEqual(props["첫 줄 글자 수"]["number"], 22)
        self.assertEqual(props["변수"]["select"]["name"], "B")
        self.assertEqual(props["팔로워 게시 전"]["number"], 42)
        led = json.loads((self.tmp / "posts" / key / "post.json").read_text())
        self.assertEqual(led["media_id"], "m1")
        self.assertFalse((self.tmp / "notices.txt").exists())

    def test_dry_run_does_not_publish(self):
        key = self.prepare()
        self.assertEqual(self.run_cli("post", "--key", key, "--pages-url", "https://o.github.io/r/", "--dry-run"), 0)
        self.assertEqual(self.th.published, [])
        self.assertEqual(self.nt.updates, [])
        self.assertFalse((self.tmp / "posts").exists())

    def test_no_double_post(self):
        key = self.prepare()
        self.th.published.append(("m0", N002))  # 이미 올라가 있음
        self.run_cli("post", "--key", key, "--pages-url", "https://o.github.io/r/")
        self.assertEqual([c[0] for c in self.th.containers], ["reply"])

    def test_changed_after_prepare(self):
        key = self.prepare()
        self.nt.row["properties"]["본문"]["rich_text"][0]["plain_text"] = N002 + "\n추가"
        self.run_cli("post", "--key", key, "--pages-url", "https://o.github.io/r/")
        self.assertEqual(self.th.containers, [])
        self.assertTrue((self.tmp / "notices.txt").exists())

    def test_unapproved_after_prepare(self):
        key = self.prepare()
        self.nt.row["properties"]["상태"]["status"]["name"] = "승인 대기"
        self.run_cli("post", "--key", key, "--pages-url", "https://o.github.io/r/")
        self.assertEqual(self.th.containers, [])

    def test_format_fail(self):
        self.nt.row["properties"]["본문"]["rich_text"][0]["plain_text"] = "첫 줄이 너무 길어서 스물다섯 자를 넘는 문장이다 정말로\n둘째"
        self.assertEqual(self.run_cli("prepare", "--slot", "08:07"), 1)
        self.assertTrue(self.nt.comments)
        self.assertFalse((self.tmp / "staged").exists() and any((self.tmp / "staged").iterdir()))


if __name__ == "__main__":
    unittest.main()
