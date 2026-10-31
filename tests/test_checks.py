import unittest

from publisher import checks

N002 = """AI한테 맡길지 헷갈릴 때 쓰는 두 문장
기술로 되는 일과 맡겨도 되는 일은 따로임

빌 게이츠: "There’s no technical reason why it couldn’t. Yet it shouldn’t."
통역: 로봇의 불치병 통보, 기술로는 됨. 그래도 하면 안 됨

→ 넘기기 전에 'Human Reserved(사람 몫)'인지 묻기
나쁜 소식 메일, AI한테 쓰게 함?"""


class BodyTests(unittest.TestCase):
    def test_n002_passes(self):
        r = checks.check_body(N002)
        self.assertTrue(r.ok, r.summary())
        self.assertEqual(r.first_line_len, 22)  # 스킬 4장: 첫 줄 22자
        self.assertEqual(r.lines, 6)

    def test_first_line_too_long(self):
        r = checks.check_body("몸값 13조 원 AI 비서 '인스팅트', 가입하면 이것부터 끄기\n둘째 줄")
        self.assertFalse(r.ok)
        self.assertIn("첫 줄", r.problems[0])

    def test_exactly_25_ok(self):
        line = "가" * 25
        self.assertTrue(checks.check_body(line).ok)
        self.assertFalse(checks.check_body(line + "나").ok)

    def test_lines(self):
        body = "\n\n".join(f"줄 {i}" for i in range(8))
        self.assertTrue(checks.check_body(body).ok)
        body9 = body + "\n줄 9"
        r = checks.check_body(body9)
        self.assertFalse(r.ok)
        self.assertEqual(r.lines, 9)

    def test_links(self):
        for bad in ["원문 https://x.com/a", "www.gatesnotes.com 참고", "claude.ai에서 설정", "자세한 건 gatesnotes.com/abc"]:
            r = checks.check_body("첫 줄\n" + bad)
            self.assertFalse(r.ok, bad)
        for good in ["@example.account 통역", "v1.0 업데이트", "3.5배 빨라짐", "파일.png 열기", "e.g. 이렇게"]:
            r = checks.check_body("첫 줄\n" + good)
            self.assertTrue(r.ok, f"{good}: {r.problems}")

    def test_500_with_emoji(self):
        body = "첫 줄\n" + "가" * 494  # 3 + 1 + 494 = 498
        self.assertTrue(checks.check_body(body).ok)
        self.assertFalse(checks.check_body(body + "🔥").ok)  # 이모지 4바이트 → 502

    def test_empty(self):
        self.assertFalse(checks.check_body("  \n ").ok)

    def test_crlf_and_trailing(self):
        r = checks.check_body("\r\n첫 줄   \r\n둘째\r\n\r\n")
        self.assertTrue(r.ok)
        self.assertEqual(r.first_line, "첫 줄")


class ReplyTests(unittest.TestCase):
    def test_reply_links_ok(self):
        reply = "통역 노트 #002 · 출처 ↓\n\n빌 게이츠, Gates Notes (2026.8.26)\nhttps://www.gatesnotes.com/a"
        self.assertEqual(checks.check_reply(reply), [])

    def test_reply_too_many_links(self):
        reply = "\n".join(f"https://ex{i}.com/a" for i in range(6))
        self.assertTrue(checks.check_reply(reply))

    def test_cards(self):
        self.assertEqual(checks.check_cards(["a_1of2.png", "a_2of2.jpg"]), [])
        self.assertTrue(checks.check_cards(["a.gif"]))


if __name__ == "__main__":
    unittest.main()
