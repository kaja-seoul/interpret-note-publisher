"""수집기 v1 (LLM 없음): RSS·Atom 피드와 앱스토어 '새로운 기능'을 긁어 글감 후보를 Notion '수집함'에 넣는다.

왜: 아침·저녁 런이 48시간 안 발표 + 고정 소스 6개만 보느라 후보가 굶었다(10/28~31 아침 글 4개 중 게시 0).
     리서치(2026-10-31 sources.md): 가장 잘된 글은 날짜 없는 글감이었고, 바로 쓸 것이 매일 나오는 피드가 수십 개 있다.
무엇: 소스 목록(sources.py)을 매일 한 번 읽어 최근 SINCE_DAYS(기본 14일) 안 항목을 모은다. 판단(점수·넓이)은 하지 않는다.
       판단은 저녁 글감 런(Sonnet)과 운영자가 한다. 여기서는 '한국 중복 후보'만 거칠게 표시한다.
어디로: Variables NOTION_INBOX_DS가 있으면 Notion 수집함에 행을 만든다(링크로 중복 제거).
       없으면 collected/digest.md 한 장을 만든다(워크플로 아티팩트). 둘 다 비밀값·개인정보 없음.
수집함 칸(권장): 제목(title) · 링크(url) · 출처(select) · 종류(select) · 게시일(date) · 수집일(date) · 요약(rich_text)
             · 한국 중복(select: 없음/있을 수 있음) · 상태(select: 새로/후보/씀/버림). 칸 이름은 config.INBOX_P_*.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

from . import config as C
from .notion import Notion, rt

UA = "interpret-note-collector/1.0 (+https://github.com/kaja-seoul/interpret-note-publisher; RSS reader, once a day)"
TIMEOUT = 25
ATOM = "{http://www.w3.org/2005/Atom}"
DC = "{http://purl.org/dc/elements/1.1/}"

# 한국 중복 후보 판정에 쓰는 이름들(제목에 있으면 그 이름으로 한국 피드 제목을 찾는다). 거친 휴리스틱이다
NAMES = ["ChatGPT", "GPT", "OpenAI", "Claude", "Anthropic", "Gemini", "Google", "Apple", "iPhone", "iOS", "Mac", "macOS",
         "Siri", "Notion", "Perplexity", "Samsung", "Galaxy", "Copilot", "Microsoft", "Meta", "Threads", "Instagram",
         "YouTube", "Cursor", "NotebookLM", "Sora", "Codex", "Grok", "Midjourney", "Canva", "Figma", "Slack", "Zoom"]


@dataclass
class Item:
    title: str
    link: str
    source: str
    kind: str
    published: datetime | None
    summary: str = ""
    korean_dup: str = "없음"
    extra: dict = field(default_factory=dict)


# ---- 가져오기 ----
def fetch(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, application/json, */*"}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text


def _text(el, *names) -> str:
    for n in names:
        x = el.find(n)
        if x is not None and (x.text or "").strip():
            return re.sub(r"\s+", " ", x.text).strip()
    return ""


def _date(s: str) -> datetime | None:
    s = (s or "").strip()
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            d = parsedate_to_datetime(s)
        except (TypeError, ValueError):
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=C.KST)
    return d.astimezone(C.KST)


def _strip_html(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = re.sub(r"&nbsp;|&#160;", " ", s)
    s = re.sub(r"&amp;", "&", s)
    s = re.sub(r"&lt;", "<", s)
    s = re.sub(r"&gt;", ">", s)
    s = re.sub(r"&quot;", '"', s)
    return re.sub(r"\s+", " ", s).strip()


def parse_feed(xml_text: str, source: str, kind: str, limit: int = 40) -> list[Item]:
    """RSS 2.0 / Atom 둘 다. 날짜가 없는 항목은 published=None으로 둔다(버리지 않는다)."""
    root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    out: list[Item] = []
    if root.tag == f"{ATOM}feed":
        for e in root.findall(f"{ATOM}entry")[:limit]:
            link = ""
            for l in e.findall(f"{ATOM}link"):
                if l.get("rel") in (None, "alternate") and l.get("href"):
                    link = l.get("href")
                    break
            out.append(Item(
                title=_text(e, f"{ATOM}title"), link=link, source=source, kind=kind,
                published=_date(_text(e, f"{ATOM}published", f"{ATOM}updated")),
                summary=_strip_html(_text(e, f"{ATOM}summary", f"{ATOM}content"))[:600],
            ))
    else:
        channel = root.find("channel")
        items = channel.findall("item") if channel is not None else root.findall(".//item")
        for it in items[:limit]:
            out.append(Item(
                title=_text(it, "title"), link=_text(it, "link") or (it.find("guid").text.strip() if it.find("guid") is not None and it.find("guid").text else ""),
                source=source, kind=kind,
                published=_date(_text(it, "pubDate", f"{DC}date", "published")),
                summary=_strip_html(_text(it, "description", "summary"))[:600],
            ))
    return [i for i in out if i.title and i.link]


def parse_appstore(json_text: str, source: str) -> list[Item]:
    """iTunes lookup JSON → '새로운 기능' 한 항목."""
    d = json.loads(json_text)
    res = (d.get("results") or [None])[0]
    if not res or not res.get("releaseNotes"):
        return []
    ver = res.get("version", "")
    link = f"{res.get('trackViewUrl', '')}#v{ver}"
    return [Item(
        title=f"{res.get('trackName', '앱')} {ver} 새로운 기능", link=link, source=source, kind="앱 업데이트",
        published=_date(res.get("currentVersionReleaseDate", "")), summary=_strip_html(res["releaseNotes"])[:600],
    )]


# ---- 고르기 ----
def fresh(items: list[Item], now: datetime, since_days: int) -> list[Item]:
    cut = now - timedelta(days=since_days)
    return [i for i in items if i.published is None or i.published >= cut]


def _norm_link(u: str) -> str:
    u = (u or "").strip()
    u = re.sub(r"[?&](utm_[a-z]+|ref|source)=[^&#]*", "", u)
    u = re.sub(r"[?&]$", "", u)
    return u.rstrip("/").lower()


def dedupe(items: list[Item], seen_links: set[str]) -> list[Item]:
    out, seen = [], {_norm_link(s) for s in seen_links}
    for i in items:
        k = _norm_link(i.link)
        if k in seen:
            continue
        seen.add(k)
        out.append(i)
    return out


def names_in(title: str) -> list[str]:
    t = title or ""
    return [n for n in NAMES if re.search(rf"(?<![A-Za-z]){re.escape(n)}(?![A-Za-z])", t, re.I)]


def mark_korean_dup(items: list[Item], korean_titles: list[str]) -> None:
    """한국 피드(GeekNews·AI타임스) 최근 제목에 같은 제품·회사 이름이 있으면 '있을 수 있음'. 판정은 사람·런이 한다."""
    kt = " \n ".join(korean_titles)
    for i in items:
        hit = [n for n in names_in(i.title) if re.search(rf"(?<![A-Za-z]){re.escape(n)}(?![A-Za-z])", kt, re.I)]
        i.korean_dup = "있을 수 있음" if hit else "없음"
        if hit:
            i.extra["korean_hits"] = hit


# ---- 쓰기 ----
def digest_md(items: list[Item], now: datetime) -> str:
    lines = [f"# 수집 {now:%Y-%m-%d %H:%M} KST · {len(items)}건", "",
             "| 게시일 | 출처 | 종류 | 제목 | 한국 중복 | 링크 |", "| --- | --- | --- | --- | --- | --- |"]
    for i in sorted(items, key=lambda x: (x.published or datetime.min.replace(tzinfo=C.KST)), reverse=True):
        lines.append(f"| {i.published:%m/%d} | {i.source} | {i.kind} | {i.title.replace('|', '/')} | {i.korean_dup} | {i.link} |"
                     if i.published else f"| — | {i.source} | {i.kind} | {i.title.replace('|', '/')} | {i.korean_dup} | {i.link} |")
    return "\n".join(lines) + "\n"


def inbox_props(i: Item, now: datetime) -> dict:
    p = {
        C.INBOX_P_TITLE: {"title": rt(i.title[:200])},
        C.INBOX_P_LINK: {"url": i.link},
        C.INBOX_P_SOURCE: {"select": {"name": i.source[:100]}},
        C.INBOX_P_KIND: {"select": {"name": i.kind}},
        C.INBOX_P_COLLECTED: {"date": {"start": now.date().isoformat()}},
        C.INBOX_P_SUMMARY: {"rich_text": rt(i.summary[:600])},
        C.INBOX_P_KDUP: {"select": {"name": i.korean_dup}},
        C.INBOX_P_STATUS: {"select": {"name": C.INBOX_S_NEW}},
    }
    if i.published:
        p[C.INBOX_P_PUBLISHED] = {"date": {"start": i.published.date().isoformat()}}
    return p


def existing_links(nt: Notion, days: int = 45) -> set[str]:
    since = (datetime.now(C.KST) - timedelta(days=days)).date().isoformat()
    rows = nt.query(C.INBOX_DS, {"property": C.INBOX_P_COLLECTED, "date": {"on_or_after": since}})
    out = set()
    for r in rows:
        u = ((r.get("properties") or {}).get(C.INBOX_P_LINK) or {}).get("url")
        if u:
            out.add(u)
    return out


# ---- 실행 ----
def newest(items: list[Item], n: int) -> list[Item]:
    """날짜 있는 것은 최신순으로 n건, 날짜 없는 것은 그대로(피드 순서) n건."""
    dated = sorted([i for i in items if i.published], key=lambda i: i.published, reverse=True)[:n]
    undated = [i for i in items if not i.published][:n]
    return dated + undated


def run(sources: list[dict], now: datetime, nt: Notion | None, out_dir: Path, log=print,
        since_days: int = 14, fetch_fn=fetch, max_per_source: int = C.COLLECT_MAX_PER_SOURCE) -> dict:
    """sources: [{name, url, type: rss|appstore|korean, kind}]. korean 타입은 중복 확인용이라 후보에 넣지 않는다."""
    items: list[Item] = []
    korean_titles: list[str] = []
    failed: list[str] = []
    for s in sources:
        try:
            body = fetch_fn(s["url"])
            if s.get("type") == "appstore":
                got = parse_appstore(body, s["name"])
            else:
                got = parse_feed(body, s["name"], s.get("kind", "글"))
            if s.get("type") == "korean":
                korean_titles += [i.title for i in fresh(got, now, 3)]
                log(f"한국 확인용 {s['name']}: {len(got)}건")
            else:
                keep = newest(fresh(got, now, since_days), max_per_source)
                items += keep
                log(f"{s['name']}: {len(got)}건 → 최근 {since_days}일 {len(fresh(got, now, since_days))}건 → {len(keep)}건")
        except Exception as e:  # 한 소스가 죽어도 나머지는 간다
            failed.append(f"{s['name']}: {str(e)[:120]}")
            log(f"실패 {s['name']}: {str(e)[:120]}")
    seen: set[str] = set()
    if nt is not None:
        try:
            seen = existing_links(nt)
        except Exception as e:
            log(f"수집함 읽기 실패(중복 확인 없이 진행): {e}")
    items = dedupe(items, seen)
    mark_korean_dup(items, korean_titles)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "digest.md").write_text(digest_md(items, now), encoding="utf-8")
    created = 0
    if nt is not None:
        for i in items:
            try:
                nt.create(C.INBOX_DS, inbox_props(i, now))
                created += 1
            except Exception as e:
                log(f"행 만들기 실패 {i.title[:40]}: {str(e)[:120]}")
    return {"items": len(items), "created": created, "failed": failed, "korean_titles": len(korean_titles)}
