"""댓글 수집: 내 최근 글 3개의 답글을 Threads API로 읽어 Notion '댓글함'에 넣는다. Claude API는 쓰지 않는다.

- 대화(conversation)로 모든 깊이의 답글을 읽는다(threads_read_replies).
- 내 답글은 넣지 않고, 다른 사람 답글마다 '내가 답함'(내가 그 답글에 답을 달았는지)을 적는다.
- 답글 ID로 중복을 막는다. 이미 있는 행은 '내가 답함'만 고친다. 내가 답했으면 상태 '새로'·'초안' → '보냄'.
- 공개 로그에는 개수만 남긴다(작성자·내용은 Notion에만).
"""
from __future__ import annotations

import os
from datetime import datetime

from . import config as C
from .notion import Notion, rt, text_of

COMMENTS_DS = os.environ.get("NOTION_COMMENTS_DS", "070ffacd-4239-44b5-a35b-4e9ad0551f52")
POSTS_TO_READ = 3


def _checkbox(page: dict, name: str) -> bool:
    return bool(((page.get("properties") or {}).get(name) or {}).get("checkbox"))


def _select(page: dict, name: str) -> str:
    v = ((page.get("properties") or {}).get(name) or {}).get("select") or {}
    return v.get("name", "")


def _ts(s: str | None) -> str | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("+0000", "+00:00")).astimezone(C.KST).replace(microsecond=0).isoformat()
    except ValueError:
        return None


def run(nt: Notion, th, now: datetime, log=print) -> dict:
    me = th.me()
    C.check_account(me.get("username"))
    th.user_id = me["id"]

    posts = [p for p in th.recent_posts(10) if p.get("media_type") != "REPOST_FACADE"][:POSTS_TO_READ]
    res = {"posts": len(posts), "new": 0, "updated": 0, "total_external": 0}
    if not posts:
        return res

    existing = {}
    flt = {"or": [{"property": "글 ID", "rich_text": {"equals": p["id"]}} for p in posts]}
    for row in nt.query(COMMENTS_DS, flt):
        rid = text_of(row, "답글 ID")
        if rid:
            existing[rid] = row

    for p in posts:
        convo = th.replies(p["id"])
        mine_parent_ids = set()
        for r in convo:
            if r.get("is_reply_owned_by_me") or r.get("username") == C.ACCOUNT:
                parent = (r.get("replied_to") or {}).get("id")
                if parent:
                    mine_parent_ids.add(parent)
        for r in convo:
            if r.get("is_reply_owned_by_me") or r.get("username") == C.ACCOUNT:
                continue
            res["total_external"] += 1
            rid = r["id"]
            answered = rid in mine_parent_ids
            if rid in existing:
                row = existing[rid]
                props = {}
                if answered != _checkbox(row, "내가 답함"):
                    props["내가 답함"] = {"checkbox": answered}
                if answered and _select(row, "상태") in ("새로", "초안", ""):
                    props["상태"] = {"select": {"name": "보냄"}}
                if props:
                    nt.update(row["id"], props)
                    res["updated"] += 1
                continue
            parent = (r.get("replied_to") or {}).get("id")
            text = (r.get("text") or "").strip() or "(글자 없음: 이미지·스티커 등)"
            props = {
                "내용": {"title": rt(text[:1900])},
                "종류": {"select": {"name": "내 글 답글"}},
                "상태": {"select": {"name": "보냄" if answered else "새로"}},
                "내가 답함": {"checkbox": answered},
                "작성자": {"rich_text": rt(r.get("username") or "(알 수 없음: 비공개 계정)")},
                "글 링크": {"url": p.get("permalink") or None},
                "답글 링크": {"url": r.get("permalink") or None},
                "대댓": {"checkbox": bool(parent and parent != p["id"])},
                "답글 ID": {"rich_text": rt(rid)},
                "글 ID": {"rich_text": rt(p["id"])},
                "수집 시각": {"date": {"start": now.replace(microsecond=0).isoformat()}},
            }
            ts = _ts(r.get("timestamp"))
            if ts:
                props["시각"] = {"date": {"start": ts}}
            props = {k: v for k, v in props.items() if not (isinstance(v, dict) and v.get("url", 1) is None)}
            nt.create(COMMENTS_DS, props)
            res["new"] += 1
    return res
