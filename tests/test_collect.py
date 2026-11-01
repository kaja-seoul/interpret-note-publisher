"""수집기 시험 (피드는 가짜 문자열)."""
import json
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from publisher import collect, config as C

NOW = datetime(2026, 10, 31, 5, 5, tzinfo=C.KST)

RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>ChatGPT adds Lock chats with a PIN</title><link>https://ex.com/a?utm_source=x</link><pubDate>Thu, 30 Oct 2026 10:00:00 GMT</pubDate><description>&lt;p&gt;Go to &lt;b&gt;Settings&lt;/b&gt; → Lock&lt;/p&gt;</description></item>
<item><title>Old thing</title><link>https://ex.com/old</link><pubDate>Mon, 01 Sep 2026 10:00:00 GMT</pubDate><description>x</description></item>
<item><title>No date item</title><link>https://ex.com/nodate</link><description>y</description></item>
</channel></rss>"""

ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>f</title>
<entry><title>Gemini app: new /skills command</title><link rel="alternate" href="https://ex.com/g"/><published>2026-10-29T12:00:00Z</published><summary>Type / in the prompt bar</summary></entry>
<entry><title>Dup of A</title><link href="https://ex.com/a"/><updated>2026-10-30T00:00:00+09:00</updated></entry>
</feed>"""

APPSTORE = json.dumps({"resultCount": 1, "results": [{"trackName": "ChatGPT", "version": "1.2026.300", "trackViewUrl": "https://apps.apple.com/kr/app/id6448311069",
                                                       "currentVersionReleaseDate": "2026-10-28T20:00:00Z", "releaseNotes": "• 대화 잠금 추가\n• 버그 수정"}]})

KOREAN = """<?xml version="1.0"?><rss version="2.0"><channel><item><title>ChatGPT 대화 잠금 기능 국내 출시</title><link>https://k.r/1</link><pubDate>Thu, 30 Oct 2026 01:00:00 GMT</pubDate></item></channel></rss>"""

FEEDS = {"u-rss": RSS, "u-atom": ATOM, "u-app": APPSTORE, "u-kr": KOREAN}
SOURCES = [
    {"name": "RSS원", "url": "u-rss", "type": "rss", "kind": "매체"},
    {"name": "Atom원", "url": "u-atom", "type": "rss", "kind": "공식 발표"},
    {"name": "앱스토어", "url": "u-app", "type": "appstore", "kind": "앱 업데이트"},
    {"name": "한국", "url": "u-kr", "type": "korean", "kind": "한국"},
    {"name": "죽은 소스", "url": "u-dead", "type": "rss", "kind": "매체"},
]


def fake_fetch(url):
    if url not in FEEDS:
        raise RuntimeError("HTTP 404")
    return FEEDS[url]


class FakeInbox:
    def __init__(self, links):
        self.links = links
        self.created = []

    def query(self, ds, filter=None, sorts=None):
        return [{"properties": {C.INBOX_P_LINK: {"url": u}}} for u in self.links]

    def create(self, ds, props):
        self.created.append(props)
        return {"id": "n"}


class ParseTests(unittest.TestCase):
    def test_rss_and_atom(self):
        r = collect.parse_feed(RSS, "s", "k")
        self.assertEqual([i.title for i in r], ["ChatGPT adds Lock chats with a PIN", "Old thing", "No date item"])
        self.assertEqual(r[0].published.tzinfo, C.KST)
        self.assertEqual(r[0].summary, "Go to Settings → Lock")
        self.assertIsNone(r[2].published)
        a = collect.parse_feed(ATOM, "s", "k")
        self.assertEqual(a[0].link, "https://ex.com/g")
        self.assertEqual(a[0].published.hour, 21)  # 12:00Z → 21:00 KST

    def test_appstore(self):
        it = collect.parse_appstore(APPSTORE, "App Store KR · ChatGPT")
        self.assertEqual(len(it), 1)
        self.assertIn("1.2026.300", it[0].title)
        self.assertIn("대화 잠금", it[0].summary)
        self.assertEqual(it[0].kind, "앱 업데이트")

    def test_fresh_keeps_undated(self):
        r = collect.fresh(collect.parse_feed(RSS, "s", "k"), NOW, 14)
        self.assertEqual([i.title for i in r], ["ChatGPT adds Lock chats with a PIN", "No date item"])

    def test_dedupe_normalizes_links(self):
        items = collect.parse_feed(RSS, "s", "k") + collect.parse_feed(ATOM, "s", "k")
        out = collect.dedupe(items, {"https://ex.com/g/"})
        self.assertEqual([i.link for i in out], ["https://ex.com/a?utm_source=x", "https://ex.com/old", "https://ex.com/nodate"])

    def test_korean_dup_flag(self):
        items = collect.parse_feed(RSS, "s", "k")
        collect.mark_korean_dup(items, ["ChatGPT 대화 잠금 기능 국내 출시"])
        self.assertEqual(items[0].korean_dup, "있을 수 있음")
        self.assertEqual(items[1].korean_dup, "없음")


class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_run_without_notion_writes_digest(self):
        res = collect.run(SOURCES, NOW, None, self.tmp / "out", log=lambda *a: None, fetch_fn=fake_fetch)
        self.assertEqual(res["items"], 4)  # RSS 2(신선) + Atom 1(중복 제외) + 앱스토어 1
        self.assertEqual(res["created"], 0)
        self.assertEqual(len(res["failed"]), 1)
        md = (self.tmp / "out" / "digest.md").read_text(encoding="utf-8")
        self.assertIn("ChatGPT adds Lock chats", md)
        self.assertIn("있을 수 있음", md)
        self.assertNotIn("Old thing", md)

    def test_run_with_notion_dedupes_and_creates(self):
        nt = FakeInbox(["https://ex.com/a"])
        res = collect.run(SOURCES, NOW, nt, self.tmp / "out", log=lambda *a: None, fetch_fn=fake_fetch)
        self.assertEqual(res["created"], 3)
        titles = [p[C.INBOX_P_TITLE]["title"][0]["text"]["content"] for p in nt.created]
        self.assertNotIn("ChatGPT adds Lock chats with a PIN", titles)
        self.assertIn("Gemini app: new /skills command", titles)
        p = nt.created[0]
        self.assertEqual(p[C.INBOX_P_STATUS]["select"]["name"], "새로")
        self.assertEqual(p[C.INBOX_P_COLLECTED]["date"]["start"], "2026-10-31")


class CapTests(unittest.TestCase):
    def test_newest_cap(self):
        items = collect.parse_feed(RSS, "s", "k")  # 2 dated + 1 undated
        out = collect.newest(items, 1)
        self.assertEqual([i.title for i in out], ["ChatGPT adds Lock chats with a PIN", "No date item"])


class FilterTests(unittest.TestCase):
    def test_title_filter_and_bytes(self):
        src = [{"name": "매체", "url": "u-rss", "type": "rss", "kind": "매체", "filter": r"ChatGPT|Gemini"}]
        res = collect.run(src, NOW, None, Path(tempfile.mkdtemp()) / "o", log=lambda *a: None,
                          fetch_fn=lambda u: FEEDS[u].encode("utf-8"))
        self.assertEqual(res["items"], 1)  # 'No date item'은 걸러지고 PIN 글만
