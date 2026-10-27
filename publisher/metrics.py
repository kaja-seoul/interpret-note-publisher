"""24시간 지표를 '게시 로그와 지표'에 채운다. Claude API는 쓰지 않는다.

매시 47분에 돈다. 게시 링크가 있고 '조회 24h'가 빈 행 중 게시 뒤 24시간이 지난 것을 채운다.
- 조회·좋아요·리포스트·공유: Threads 미디어 Insights (views, likes, reposts, shares)
- 외부 댓글: 대화(conversation)에서 내 계정 답글을 뺀 수, 게시 뒤 24시간 안에 달린 것만
- 팔로워 24h 뒤: 잴 때의 팔로워 수
- 그날 팔로워 증감: 매일 08시 대 실행이 data/followers.csv에 팔로워 수를 적고,
  D일 증감 = (D+1일 기록) − (D일 기록). D일 기록이 없으면 그날 첫 글의 '팔로워 게시 전'을 쓴다.
  D일에 올린 글 모두에 같은 값을 적는다(하루 2글이라 글별로 가를 수 없음).
게시 뒤 30시간이 넘도록 못 잰 행은 24시간 값이 아니므로 채우지 않고 메모에 남긴다.
"""
from __future__ import annotations

import csv
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from . import config as C
from .notion import Notion, date_start, number_of, rt, text_of, url_of

FOLLOWERS_CSV = Path(__file__).resolve().parent.parent / "data" / "followers.csv"
WINDOW_MIN_H = 24
WINDOW_MAX_H = 30
SNAPSHOT_HOUR = 8  # KST 08시 대 실행이 하루 기록을 남긴다


def _when(page: dict) -> datetime | None:
    s = date_start(page, "게시 시각")
    if not s or "T" not in s and " " not in s:
        return None
    d = datetime.fromisoformat(s.replace("Z", "+00:00").replace(" ", "T"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=C.KST)
    return d.astimezone(C.KST)


def _media_id(page: dict, by_link: dict[str, str]) -> str | None:
    m = re.search(r"media (\d+)", text_of(page, "메모"))
    if m:
        return m.group(1)
    link = url_of(page, "게시 링크").rstrip("/")
    code = link.rsplit("/", 1)[-1]
    return by_link.get(code)


# ---- 팔로워 하루 기록 ----
def load_snapshots() -> dict[date, int]:
    out = {}
    if FOLLOWERS_CSV.exists():
        with open(FOLLOWERS_CSV, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    out[date.fromisoformat(row["date"])] = int(row["followers"])
                except (KeyError, ValueError):
                    pass
    return out


def save_snapshot(day: date, at: datetime, followers: int) -> bool:
    snaps = load_snapshots()
    if day in snaps:
        return False
    FOLLOWERS_CSV.parent.mkdir(parents=True, exist_ok=True)
    new = not FOLLOWERS_CSV.exists()
    with open(FOLLOWERS_CSV, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["date", "time_kst", "followers"])
        w.writerow([day.isoformat(), at.strftime("%H:%M"), followers])
    return True


# ---- 실행 ----
def run(nt: Notion, th, now: datetime, log=print) -> dict:
    me = th.me()
    if me.get("username") != C.ACCOUNT:
        raise RuntimeError(f"토큰 계정이 @{me.get('username')}다")
    th.user_id = me["id"]
    followers = th.followers_count()
    result = {"filled": [], "late": [], "daily": [], "snapshot": None}

    # 1) 하루 기록 (08시 대 실행만)
    if followers is not None and now.hour == SNAPSHOT_HOUR:
        if save_snapshot(now.date(), now, followers):
            result["snapshot"] = followers
            log(f"팔로워 하루 기록: {now.date()} {followers}")

    rows = nt.query(C.POSTLOG_DS, {"property": "게시 링크", "url": {"is_not_empty": True}})
    rows = [r for r in rows if "집계에서 뺀다" not in text_of(r, "메모")]

    # 2) 24시간 지표
    pending = [r for r in rows if number_of(r, "조회 24h") is None and "지표 못 잼" not in text_of(r, "메모")]
    by_link = {}
    if pending:
        for p in th.my_posts():
            code = (p.get("permalink") or "").rstrip("/").rsplit("/", 1)[-1]
            if code:
                by_link[code] = p["id"]
    for r in pending:
        when = _when(r)
        if not when:
            continue
        age_h = (now - when).total_seconds() / 3600
        if age_h < WINDOW_MIN_H:
            continue
        title = text_of(r, "글")
        memo = text_of(r, "메모")
        if age_h > WINDOW_MAX_H:
            nt.update(r["id"], {"메모": {"rich_text": rt((memo + "\n" if memo else "") +
                      f"[게시기] 게시 뒤 {age_h:.0f}시간이 지나 24시간 지표를 못 잼 (지표 못 잼)")}})
            result["late"].append(title)
            continue
        mid = _media_id(r, by_link)
        if not mid:
            log(f"media id를 못 찾음: {title}")
            continue
        ins = th.media_insights(mid)
        cutoff = when + timedelta(hours=24)
        ext = 0
        total_replies = 0
        for rep in th.replies(mid):
            total_replies += 1
            mine = rep.get("is_reply_owned_by_me") or rep.get("username") == C.ACCOUNT
            ts = rep.get("timestamp")
            in_window = True
            if ts:
                try:
                    in_window = datetime.fromisoformat(ts.replace("+0000", "+00:00")) <= cutoff
                except ValueError:
                    pass
            if not mine and in_window:
                ext += 1
        props = {
            "조회 24h": {"number": ins.get("views")},
            "좋아요 24h": {"number": ins.get("likes")},
            "리포스트 24h": {"number": ins.get("reposts")},
            "공유 24h": {"number": ins.get("shares")},
            "외부 댓글 24h": {"number": ext},
        }
        if followers is not None:
            props["팔로워 24h 뒤"] = {"number": followers}
        note = (f"[게시기] 지표 {now:%m/%d %H:%M} 측정(게시 {age_h:.1f}시간 뒤) · 인용 {ins.get('quotes', 0)} · "
                f"전체 답글 {total_replies}(내 답글 포함)")
        props["메모"] = {"rich_text": rt((memo + "\n" if memo else "") + note)}
        props = {k: v for k, v in props.items() if not (isinstance(v, dict) and "number" in v and v["number"] is None)}
        nt.update(r["id"], props)
        result["filled"].append(f"{title}: 조회 {ins.get('views')} · 공유 {ins.get('shares')} · 리포스트 {ins.get('reposts')} · 외부 댓글 {ext}")
        log(f"채움: {result['filled'][-1]}")

    # 3) 그날 팔로워 증감 (어제 글에, 오늘 기록이 생긴 뒤)
    snaps = load_snapshots()
    for r in rows:
        if number_of(r, "그날 팔로워 증감") is not None:
            continue
        when = _when(r)
        if not when:
            continue
        d = when.date()
        end = snaps.get(d + timedelta(days=1))
        if end is None:
            continue
        start = snaps.get(d)
        if start is None:
            same_day_sorted = sorted(
                [(_when(x), number_of(x, "팔로워 게시 전")) for x in rows
                 if _when(x) and _when(x).date() == d and number_of(x, "팔로워 게시 전") is not None])
            start = same_day_sorted[0][1] if same_day_sorted else None
        if start is None:
            continue
        nt.update(r["id"], {"그날 팔로워 증감": {"number": end - start}})
        result["daily"].append(f"{text_of(r, '글')}: {end - start:+d} ({d:%m/%d})")
    return result
