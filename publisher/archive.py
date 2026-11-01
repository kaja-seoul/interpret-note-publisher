"""통역 노트 모음 페이지 (= '내 웹'의 첫 판). posts/ 원장에서 게시된 글만 읽어 정적 HTML 한 장을 만든다. LLM 없음.

- Actions Variables `NOTES_PAGE`가 true일 때만 build-site가 `_site/notes/index.html`을 만든다(운영자가 공개 범위를 정한 뒤 켠다).
- 카드는 build-site가 이미 `_site/c/<key>/`에 복사하므로 그 주소(`../c/<key>/card1.png`)를 쓴다.
- 글 본문·첫 답글·원문 링크·스레드 링크·게시 시각만 담는다. 승인 전 초안은 posts/에 없으니 여기에도 없다.
"""
from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path

from . import config as C

CSS = """:root{--bg:#F6F4EF;--ink:#1B1B1F;--quiet:#6B6A70;--line:#D9D6CF;--brand:#D6176E}
@media (prefers-color-scheme:dark){:root{--bg:#151518;--ink:#F2F0EA;--quiet:#A3A1A8;--line:#33323A}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:system-ui,-apple-system,"Noto Sans KR",sans-serif;line-height:1.55;word-break:keep-all}
main{max-width:640px;margin:0 auto;padding:40px 16px 80px}
h1{font-size:28px;margin:0 0 6px}.lead{color:var(--quiet);margin:0 0 32px}
article{border-top:2px solid var(--line);padding:28px 0}
.band{display:flex;gap:10px;align-items:baseline;font-size:14px;color:var(--quiet)}.band b{color:var(--brand);font-weight:800}
h2{font-size:20px;margin:8px 0 12px}
.body{white-space:pre-wrap;margin:0 0 14px}
img{width:100%;height:auto;border-radius:14px;border:1px solid var(--line);margin:8px 0 12px;display:block}
details{margin:6px 0 12px;color:var(--quiet)}summary{cursor:pointer}details .body{color:var(--ink)}
.links a{margin-right:14px;color:var(--brand);text-decoration:none;font-weight:700}.links a:hover{text-decoration:underline}
footer{margin-top:40px;color:var(--quiet);font-size:13px}
"""


def load_posts(posts_dir: Path) -> list[dict]:
    """게시된 글(media_id·permalink 있음)만, 최신순."""
    out = []
    for p in sorted(posts_dir.glob("*/post.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if not d.get("media_id") or not d.get("permalink"):
            continue
        d["_key"] = p.parent.name
        out.append(d)
    out.sort(key=lambda d: d.get("posted_at", ""), reverse=True)
    return out


def _when(s: str) -> str:
    try:
        return datetime.fromisoformat(s).astimezone(C.KST).strftime("%Y.%m.%d %H:%M")
    except (ValueError, TypeError):
        return ""


def _label(d: dict) -> str:
    series = d.get("series") or ""
    num = d.get("number")
    if series and num:
        return f"{series} #{int(num):03d}"
    return series or d.get("slot") or ""


def render(posts: list[dict], title: str = "통역 노트 모음", lead: str = "실리콘밸리·AI 영어 원문을 쉬운 한국어로 통역한 글. 출처는 글마다 달려 있다.",
           site_root: str = "..") -> str:
    e = html.escape
    items = []
    for d in posts:
        cards = "".join(
            f'<img src="{e(site_root)}/c/{e(d["_key"])}/{e(c["file"])}" alt="{e(d.get("title") or "")} 카드" loading="lazy">'
            for c in d.get("cards") or [] if c.get("file")
        )
        reply = d.get("reply") or ""
        reply_html = f'<details><summary>첫 답글 · 출처</summary><p class="body">{e(reply)}</p></details>' if reply else ""
        links = f'<a href="{e(d["permalink"])}" target="_blank" rel="noopener">스레드에서 보기</a>'
        if d.get("source_url"):
            links += f'<a href="{e(d["source_url"])}" target="_blank" rel="noopener">원문</a>'
        items.append(
            f'<article id="{e(d["_key"])}"><div class="band"><b>{e(_label(d))}</b><span>{e(_when(d.get("posted_at", "")))}</span></div>'
            f'<h2>{e((d.get("text") or "").split(chr(10))[0])}</h2>'
            f'<p class="body">{e(d.get("text") or "")}</p>{cards}{reply_html}<p class="links">{links}</p></article>'
        )
    body = "\n".join(items) if items else "<p>아직 올린 글이 없다.</p>"
    return (
        '<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{e(title)}</title><style>{CSS}</style></head><body><main><h1>{e(title)}</h1><p class="lead">{e(lead)}</p>'
        f'{body}<footer>글 {len(posts)}개 · 이 페이지는 게시기가 게시 원장(posts/)에서 자동으로 만든다.</footer></main></body></html>'
    )
