"""통역 노트 모음 페이지 시험."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from publisher import archive, cli


def post(dir_, key, posted_at, media="m", text="첫 줄 <b>\n둘째 줄", reply="통역 노트 #001 · 출처 ↓\nhttps://x.y", number=1):
    d = dir_ / key
    d.mkdir(parents=True)
    (d / "card1.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 10)
    (d / "post.json").write_text(json.dumps({
        "key": key, "title": "통역 노트 #001 · 제목", "series": "통역 노트", "number": number, "slot": "08:07",
        "text": text, "reply": reply, "cards": [{"file": "card1.png", "name": "x_1of1.png"}],
        "source_url": "https://example.com/src?a=1&b=2", "media_id": media, "reply_id": "r",
        "permalink": "https://www.threads.com/@example.account/post/ABC", "posted_at": posted_at, "followers_before": 10,
    }, ensure_ascii=False), encoding="utf-8")


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.posts = self.tmp / "posts"
        post(self.posts, "20261027-0807-aaa", "2026-10-27T12:43:00+09:00", number=2)
        post(self.posts, "20261028-2137-bbb", "2026-10-28T23:41:01+09:00", number=3)
        post(self.posts, "20261029-0807-ccc", "2026-10-29T08:10:00+09:00", media="", number=4)  # 안 올라간 글

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_only_published_newest_first(self):
        posts = archive.load_posts(self.posts)
        self.assertEqual([p["_key"] for p in posts], ["20261028-2137-bbb", "20261027-0807-aaa"])

    def test_render_escapes_and_links(self):
        h = archive.render(archive.load_posts(self.posts))
        self.assertIn("통역 노트 #003", h)
        self.assertIn("첫 줄 &lt;b&gt;", h)                 # HTML 이스케이프
        self.assertIn("../c/20261028-2137-bbb/card1.png", h)
        self.assertIn("https://example.com/src?a=1&amp;b=2", h)
        self.assertIn("2026.10.28 23:41", h)
        self.assertIn("글 2개", h)
        self.assertNotIn("20261029-0807-ccc", h)

    def test_build_site_makes_notes_only_when_enabled(self):
        root = self.tmp / "root"
        root.mkdir()
        with mock.patch.object(cli, "ROOT", root), mock.patch.object(cli, "POSTS", self.posts), \
             mock.patch.object(cli, "STAGED", self.tmp / "staged"):
            with mock.patch.dict(os.environ, {"NOTES_PAGE": ""}):
                self.assertEqual(cli.main(["build-site"]), 0)
                self.assertFalse((root / "_site" / "notes").exists())
                self.assertTrue((root / "_site" / "c" / "20261028-2137-bbb" / "card1.png").exists())
            with mock.patch.dict(os.environ, {"NOTES_PAGE": "true"}):
                self.assertEqual(cli.main(["build-site"]), 0)
                html = (root / "_site" / "notes" / "index.html").read_text(encoding="utf-8")
                self.assertIn("통역 노트 #002", html)
