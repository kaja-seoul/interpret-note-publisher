"""게시 전 형식 검사. LLM 없이 코드로만 본다.

규칙 (스킬 4장 + 10월 27일 결정 '글 규칙 보강'):
- 첫 줄 25자 이내 (공백 포함)
- 본문 8줄 이하 (빈 줄 제외)
- 본문 링크 0개
- 본문 500자 이하 (Threads 셈법: 이모지는 UTF-8 바이트 수로 셈)

첫 답글은 편집 규칙이 아니라 API 한도만 본다 (500자, 고유 링크 5개).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

FIRST_LINE_MAX = 25
BODY_LINES_MAX = 8
BODY_LINKS_MAX = 0
TEXT_MAX = 500
REPLY_LINKS_MAX = 5

_INVISIBLE = {"‍", "︎", "️", "​", "⁠"}

URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"']+", re.I)
# 맨 도메인(claude.ai, gatesnotes.com/...)도 스레드가 링크로 바꿀 수 있어 잡는다.
DOMAIN_RE = re.compile(
    r"(?<![\w@./-])"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"(?:com|net|org|io|ai|co|kr|dev|app|me|so|gg|tv|ly|to|xyz|news|blog|page|site|link|us|uk|jp|cn|info|biz|tech|cc|fm|sh)"
    r"(?![a-z0-9-])(?:/[^\s<>\"']*)?",
    re.I,
)


def _is_emoji(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x1F000 <= cp <= 0x1FAFF
        or 0x2600 <= cp <= 0x27BF
        or 0x2300 <= cp <= 0x23FF
        or 0x2B00 <= cp <= 0x2BFF
        or 0x1F1E6 <= cp <= 0x1F1FF
        or 0xE0020 <= cp <= 0xE007F
        or ch in _INVISIBLE
    )


def normalize(text: str | None) -> str:
    """줄바꿈을 \n으로, 줄 끝 공백 제거, 앞뒤 빈 줄 제거."""
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.rstrip() for ln in text.split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def visible_len(s: str) -> int:
    """사람이 보는 글자 수 (공백 포함). 보이지 않는 결합 문자는 뺀다."""
    return sum(1 for ch in s if ch not in _INVISIBLE)


def threads_len(s: str) -> int:
    """Threads 500자 셈법에 맞춘 길이: 보통 글자는 1, 이모지는 UTF-8 바이트 수."""
    return sum(len(ch.encode("utf-8")) if _is_emoji(ch) else 1 for ch in s)


def find_links(s: str) -> list[str]:
    found = [m.group(0) for m in URL_RE.finditer(s)]
    rest = URL_RE.sub(" ", s)
    found += [m.group(0) for m in DOMAIN_RE.finditer(rest)]
    return found


def first_line(text: str) -> str:
    for ln in normalize(text).split("\n"):
        if ln.strip():
            return ln.strip()
    return ""


@dataclass
class CheckResult:
    ok: bool
    problems: list[str] = field(default_factory=list)
    first_line: str = ""
    first_line_len: int = 0
    lines: int = 0
    length: int = 0

    def summary(self) -> str:
        head = f"첫 줄 {self.first_line_len}자 · 본문 {self.lines}줄 · {self.length}자"
        if self.ok:
            return f"통과 ({head})"
        return f"탈락 ({head})\n" + "\n".join(f"- {p}" for p in self.problems)


def check_body(text: str | None) -> CheckResult:
    body = normalize(text)
    problems: list[str] = []
    if not body:
        return CheckResult(ok=False, problems=["본문이 비어 있다"])

    fl = first_line(body)
    fl_len = visible_len(fl)
    lines = [ln for ln in body.split("\n") if ln.strip()]
    length = threads_len(body)
    links = find_links(body)

    if fl_len > FIRST_LINE_MAX:
        problems.append(f"첫 줄이 {fl_len}자다 ({FIRST_LINE_MAX}자 이내): {fl}")
    if len(lines) > BODY_LINES_MAX:
        problems.append(f"본문이 빈 줄 빼고 {len(lines)}줄이다 ({BODY_LINES_MAX}줄 이하)")
    if len(links) > BODY_LINKS_MAX:
        problems.append("본문에 링크가 있다 (0개여야 함): " + ", ".join(links))
    if length > TEXT_MAX:
        problems.append(f"본문이 {length}자다 ({TEXT_MAX}자 이하, 이모지는 여러 자로 셈)")

    return CheckResult(
        ok=not problems,
        problems=problems,
        first_line=fl,
        first_line_len=fl_len,
        lines=len(lines),
        length=length,
    )


def check_reply(text: str | None) -> list[str]:
    """첫 답글은 API 한도만 본다. 비어 있으면 답글을 달지 않는다(문제 아님)."""
    reply = normalize(text)
    if not reply:
        return []
    problems = []
    n = threads_len(reply)
    if n > TEXT_MAX:
        problems.append(f"첫 답글이 {n}자다 ({TEXT_MAX}자 이하)")
    unique = {u.rstrip(".,)").lower() for u in find_links(reply)}
    if len(unique) > REPLY_LINKS_MAX:
        problems.append(f"첫 답글 고유 링크가 {len(unique)}개다 ({REPLY_LINKS_MAX}개 이하)")
    return problems


def check_cards(names: list[str]) -> list[str]:
    problems = []
    if len(names) > 20:
        problems.append(f"카드가 {len(names)}장이다 (20장 이하)")
    for n in names:
        if not re.search(r"\.(png|jpe?g)$", n, re.I):
            problems.append(f"카드 파일 형식이 PNG/JPG가 아니다: {n}")
    return problems
