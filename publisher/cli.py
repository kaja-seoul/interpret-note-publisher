"""게시기 명령.

  python -m publisher.cli prepare --slot 08:07 [--page-id ID] [--now]
  python -m publisher.cli post --key KEY --pages-url URL [--dry-run] [--now]
  python -m publisher.cli build-site
  python -m publisher.cli verify            # 토큰·Notion 연결 확인 (값은 안 찍음)
  python -m publisher.cli check             # 슬롯이 지났는데 '승인'인 글, Threads API 상태 (문제면 빨간불)
  python -m publisher.cli tick              # 외부 cron의 tick: 지금 깨울 워크플로 목록을 출력에 남김

prepare: 캘린더에서 올릴 글 하나를 고르고 형식 검사, 카드를 staged/<key>/에 받는다.
         --watch면 게시 창(슬롯 55분 전 ~ 90분 뒤)이 닫힐 때까지 3분마다 다시 본다. 깨우기 한 번으로 늦은 승인까지 받는다.
post:    목표 시각까지 기다렸다가 게시 → 첫 답글 → 캘린더·게시 로그 기록 → posts/<key>/ 원장.
알릴 일이 생기면 그 글 행에 Notion 댓글을 달고 notices.txt에 적는다. 워크플로가 마지막에 빨간불로 끝내 GitHub 메일이 간다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import shutil
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

from . import checks, config as C
from .notion import Notion, date_start, files_of, number_of, rt, select_of, text_of, url_of
from .threads import Threads, ThreadsError

ROOT = Path(__file__).resolve().parent.parent
STAGED = ROOT / "staged"
POSTS = ROOT / "posts"
NOTICES = ROOT / "notices.txt"


# ---------- 공통 ----------
def now_kst() -> datetime:
    return datetime.now(C.KST)


def log(msg: str) -> None:
    print(msg, flush=True)


def gh_output(**kw) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    lines = [f"{k}={v}" for k, v in kw.items()]
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    for ln in lines:
        log(f"[output] {ln}")


def summary(md: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(md + "\n")


_DEDUPE: set[tuple[str, str]] | None = None   # 감시 모드에서만 켠다: 같은 행에 같은 알림을 몇 분마다 반복하지 않게


def notice(nt: Notion | None, page_id: str | None, msg: str) -> None:
    """운영자에게 알림: Notion 행 댓글 + notices.txt (워크플로가 빨간불로 끝냄)."""
    if _DEDUPE is not None:
        key = (page_id or "", msg.strip())
        if key in _DEDUPE:
            return
        _DEDUPE.add(key)
    log(f"[알림] {msg}")
    with open(NOTICES, "a", encoding="utf-8") as f:
        f.write(msg.strip() + "\n\n")
    if nt and page_id:
        try:
            nt.comment(page_id, f"[게시기] {msg}")
        except Exception as e:  # 댓글 실패는 알림을 막지 않는다
            log(f"Notion 댓글 실패: {e}")


def notion() -> Notion:
    return Notion(os.environ.get("NOTION_TOKEN", ""))


def threads() -> Threads:
    return Threads(os.environ.get("THREADS_TOKEN", ""), os.environ.get("THREADS_USER_ID") or None)


def row_when(page: dict) -> tuple[datetime | None, str]:
    """(예정 날짜의 슬롯 시각 KST, 슬롯)."""
    slot = select_of(page, C.P_SLOT)
    start = date_start(page, C.P_DATE)
    if not start or slot not in C.SLOTS:
        return None, slot
    d = datetime.fromisoformat(start)
    if d.tzinfo is None:
        day = d.date()
    else:
        day = d.astimezone(C.KST).date()
    h, m = C.SLOTS[slot]
    return datetime(day.year, day.month, day.day, h, m, tzinfo=C.KST), slot


def variable_of(page: dict) -> str:
    v = select_of(page, C.P_VAR)
    if v:
        return v
    m = re.search(r"변수\s*[:：]?\s*(A|B|실험\s*전)", text_of(page, C.P_MEMO))
    return re.sub(r"\s+", " ", m.group(1)) if m else ""


def cards_ordered(page: dict) -> list[dict]:
    files = files_of(page, C.P_CARDS)

    def k(f):
        m = re.search(r"(\d+)\s*of\s*\d+", f["name"], re.I)
        return int(m.group(1)) if m else 999

    if all(re.search(r"\d+\s*of\s*\d+", f["name"], re.I) for f in files):
        files = sorted(files, key=k)
    return files


def snapshot(page: dict) -> str:
    parts = [
        select_of(page, C.P_STATUS),
        checks.normalize(text_of(page, C.P_BODY)),
        checks.normalize(text_of(page, C.P_REPLY)),
        "|".join(f["name"] for f in cards_ordered(page)),
    ]
    return hashlib.sha256("\n\0".join(parts).encode()).hexdigest()


def topic_of(nt: Notion | None, page: dict) -> str:
    """'토픽' 칸의 태그 1개. 규칙(1~50자, '.'·'&' 없음)에 어긋나면 알리고 태그 없이 올린다."""
    tag = (select_of(page, C.P_TOPIC) or text_of(page, C.P_TOPIC)).strip().lstrip("#")
    if not tag:
        return ""
    if len(tag) > 50 or "." in tag or "&" in tag:
        notice(nt, page["id"], f"토픽 태그 '{tag}'는 규칙(1~50자, '.'·'&' 금지)에 어긋나 태그 없이 올린다.")
        return ""
    return tag


def last_post_day():
    """posts/ 원장에서 마지막으로 올린 날(KST date). 없으면 None."""
    days = []
    for p in POSTS.glob("*/post.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if d.get("media_id") and d.get("posted_at"):
                days.append(datetime.fromisoformat(d["posted_at"]).astimezone(C.KST).date())
        except (ValueError, KeyError):
            pass
    return max(days) if days else None


def posted_slot(day, slot: str) -> bool:
    """이 날 이 슬롯 글을 이미 올렸나 (posts/ 원장 기준)."""
    for p in POSTS.glob(f"{day:%Y%m%d}-{slot.replace(':', '')}-*/post.json"):
        try:
            if json.loads(p.read_text(encoding="utf-8")).get("media_id"):
                return True
        except ValueError:
            pass
    return False


def posted_today(day) -> int:
    n = 0
    for p in POSTS.glob(f"{day:%Y%m%d}-*/post.json"):
        try:
            if json.loads(p.read_text(encoding="utf-8")).get("media_id"):
                n += 1
        except ValueError:
            pass
    return n


# ---------- prepare ----------
def current_slot(now: datetime) -> str | None:
    """깬 시각으로 맡을 슬롯을 정한다. [슬롯-EARLY_MIN, 슬롯+LATE_LIMIT_MIN] 밖이면 None."""
    for slot in C.SLOT_ORDER:
        h, m = C.SLOTS[slot]
        sdt = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if sdt - timedelta(minutes=C.EARLY_MIN) <= now <= sdt + timedelta(minutes=C.LATE_LIMIT_MIN):
            return slot
    return None



def pick(nt: Notion, slot: str, now: datetime, page_id: str | None, dry_run: bool = False) -> tuple[dict | None, datetime | None]:
    if page_id:
        page = nt.page(page_id)
        status = select_of(page, C.P_STATUS)
        # 드라이런은 게시하지 않으니 '승인 대기'·'보류' 행도 시험할 수 있다
        if status != C.S_APPROVED and not (dry_run and status != C.S_POSTED):
            notice(nt, page_id, f"시험 대상 행의 상태가 '{status}'다. '승인'인 행만 올린다.")
            return None, None
        if url_of(page, C.P_LINK):
            notice(nt, page_id, "이미 게시 링크가 있는 행이다. 다시 올리지 않는다.")
            return None, None
        return page, now

    rows = nt.query(C.CALENDAR_DS, {"property": C.P_STATUS, "status": {"equals": C.S_APPROVED}})
    h, m = C.SLOTS[slot]
    slot_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
    exact, overdue = [], []
    for r in rows:
        if url_of(r, C.P_LINK):
            continue
        when, rslot = row_when(r)
        if when is None:
            if rslot != "수동":
                log(f"건너뜀(날짜·슬롯 없음): {text_of(r, C.P_TITLE)}")
            continue
        if when == slot_dt:
            exact.append(r)
        elif when < slot_dt:
            overdue.append((when, r))
    overdue.sort(key=lambda x: x[0])

    fresh = []
    for when, r in overdue:
        if slot_dt - when > timedelta(hours=C.OVERDUE_FRESH_HOURS):
            # 같은 알림이 매번 반복되지 않게 '보류'로 옮긴다
            try:
                nt.update(r["id"], {C.P_STATUS: {"status": {"name": "보류"}}})
            except Exception as e:
                log(f"보류로 바꾸기 실패: {e}")
            notice(nt, r["id"], f"예정 시각({when:%m/%d %H:%M})에서 {C.OVERDUE_FRESH_HOURS}시간이 지나 올리지 않고 '보류'로 옮겼다. 날짜·슬롯을 다시 정해 '승인'하면 된다.")
        else:
            fresh.append((when, r))

    if len(exact) > 1:
        for r in exact:
            notice(nt, r["id"], f"{now:%m/%d} {slot} 슬롯에 '승인' 글이 {len(exact)}개다. 하나만 남기면 다음 실행 때 올린다.")
        return None, None
    if exact:
        for when, r in fresh:
            notice(nt, r["id"], f"늦은 승인 글이지만 {slot} 슬롯에 이미 승인 글이 있어 이번엔 올리지 않는다. 다음 슬롯에 다시 본다.")
        return exact[0], slot_dt
    if fresh:
        for when, r in fresh[1:]:
            notice(nt, r["id"], "늦은 승인 글이 여러 개라 가장 오래된 것부터 하나씩 올린다. 이 글은 다음 슬롯에 다시 본다.")
        return fresh[0][1], slot_dt
    return None, None


def cmd_prepare(a) -> int:
    global _DEDUPE
    _DEDUPE = None
    NOTICES.unlink(missing_ok=True)
    nt = notion()
    now = now_kst()
    slot = a.slot
    if slot == "auto":
        slot = current_slot(now)
        if not slot:
            log(f"{now:%m/%d %H:%M}는 게시 창 밖이다 (슬롯 55분 전 ~ 90분 뒤). 아무것도 하지 않는다.")
            _note(True, f"{now:%m/%d %H:%M} 게시 창 밖: 할 일 없음")
            gh_output(has_post="false")
            summary(f"### {now:%m/%d %H:%M}: 게시 창 밖 (예약 실행이 늦거나 일찍 옴)")
            return 0
    if not a.page_id and not C.LIVE:
        log("예약 게시가 꺼져 있다 (config.LIVE = False). 아무것도 올리지 않는다.")
        gh_output(has_post="false")
        summary(f"### {now:%m/%d} {slot}: 예약 게시 꺼짐 (운영자가 넘기기 전)")
        return 0
    if getattr(a, "watch", False) and not a.page_id:
        return watch(nt, slot, a)
    page, slot_dt = pick(nt, slot, now, a.page_id, a.dry_run)
    if not page:
        log("올릴 글 없음.")
        _note(True, f"{now:%m/%d %H:%M} · {slot} 슬롯: 올릴 '승인' 글 없음" + (" (드라이런)" if a.dry_run else ""))
        gh_output(has_post="false")
        summary(f"### {now:%m/%d} {slot}: 올릴 승인 글 없음")
        return 0
    return stage(nt, page, slot, slot_dt, now, a)


def watch(nt: Notion, slot: str, a) -> int:
    """감시 모드: 게시 창이 닫힐 때까지 몇 분마다 '승인' 행을 다시 본다. 깨우기 한 번으로 늦은 승인까지 받는다."""
    global _DEDUPE
    _DEDUPE = set()
    h, m = C.SLOTS[slot]
    start = now_kst()
    slot_dt = start.replace(hour=h, minute=m, second=0, microsecond=0)
    deadline = slot_dt + timedelta(minutes=C.LATE_LIMIT_MIN)
    if posted_slot(start.date(), slot):
        log(f"{start:%m/%d} {slot} 슬롯은 이미 올렸다. 끝.")
        _note(True, f"{start:%m/%d} {slot} 슬롯: 이미 게시됨 · 할 일 없음")
        gh_output(has_post="false")
        return 0
    log(f"감시 시작 {start:%H:%M:%S} · {slot} 슬롯 · {deadline:%H:%M}까지 {C.WATCH_INTERVAL_SEC}초마다 '승인' 행을 본다")
    skipped: dict[str, str] = {}   # 형식 탈락한 행: 내용이 바뀔 때까지 다시 보지 않는다
    polls = 0
    while True:
        now = now_kst()
        page, sdt = pick(nt, slot, now, None, a.dry_run)
        polls += 1
        if page:
            snap = snapshot(page)
            if skipped.get(page["id"]) != snap:
                if stage(nt, page, slot, sdt, now, a) == 0:
                    return 0
                skipped[page["id"]] = snap
                log("탈락한 행은 내용이 바뀔 때까지 건너뛴다. 계속 감시한다.")
        if now >= deadline:
            log(f"{now:%H:%M} 게시 창 끝. 올릴 '승인' 글 없음 ({polls}번 봄).")
            _note(True, f"{now:%m/%d} {slot} 슬롯: 창이 끝날 때까지 올릴 '승인' 글 없음 ({polls}번 확인)")
            gh_output(has_post="false")
            summary(f"### {now:%m/%d} {slot}: 창이 끝날 때까지 올릴 승인 글 없음 ({polls}번 확인)")
            return 0
        time.sleep(C.WATCH_INTERVAL_SEC)


def stage(nt: Notion, page: dict, slot: str, slot_dt: datetime | None, now: datetime, a) -> int:
    """고른 글을 형식 검사하고 카드를 받아 staged/<key>/에 둔다. 0이면 has_post=true."""
    pid = page["id"]
    title = text_of(page, C.P_TITLE)
    if posted_today(now.date()) >= C.DAILY_MAX:
        notice(nt, pid, f"오늘 이미 {C.DAILY_MAX}글을 올렸다(하루 최대 {C.DAILY_MAX}). 이 글은 올리지 않는다.")
        gh_output(has_post="false")
        return 0

    body = checks.normalize(text_of(page, C.P_BODY))
    reply = checks.normalize(text_of(page, C.P_REPLY))
    res = checks.check_body(body)
    problems = list(res.problems) + checks.check_reply(reply)
    cards = cards_ordered(page)
    problems += checks.check_cards([f["name"] for f in cards])
    if len(cards) < C.CARDS_MIN and C.NO_CARD_FLAG not in text_of(page, C.P_MEMO):
        problems.append(f"'카드' 칸에 카드가 없다 (카드 1은 늘 넣는다. 카드 없이 올리려면 메모에 '{C.NO_CARD_FLAG}')")
    log(f"글: {title}\n{res.summary()}")
    if problems:
        notice(nt, pid, "형식 검사에서 탈락해 올리지 않는다.\n" + "\n".join(f"- {p}" for p in problems))
        gh_output(has_post="false")
        summary(f"### 탈락: {title}\n" + "\n".join(f"- {p}" for p in problems))
        return 1

    key = f"{now:%Y%m%d}-{slot.replace(':', '')}-{pid.replace('-', '')[:6]}"
    out = STAGED / key
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    saved = []
    for i, f in enumerate(cards, 1):
        if not f["url"]:
            notice(nt, pid, f"카드 파일 '{f['name']}'의 주소를 받지 못했다.")
            return 1
        r = requests.get(f["url"], timeout=60)
        r.raise_for_status()
        data = r.content
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            ext = "png"
        elif data[:3] == b"\xff\xd8\xff":
            ext = "jpg"
        else:
            notice(nt, pid, f"카드 '{f['name']}'가 PNG/JPG 이미지가 아니다.")
            return 1
        if len(data) > 8 * 1024 * 1024:
            notice(nt, pid, f"카드 '{f['name']}'가 8MB를 넘는다.")
            return 1
        fn = f"card{i}.{ext}"
        (out / fn).write_bytes(data)
        saved.append({"file": fn, "name": f["name"]})

    if a.now or a.page_id:
        target = now
    else:
        target = slot_dt + timedelta(minutes=random.randint(0, C.JITTER_MAX_MIN), seconds=random.randint(0, 59))

    meta = {
        "key": key,
        "page_id": pid,
        "page_url": page.get("url", ""),
        "title": title,
        "series": select_of(page, C.P_SERIES),
        "number": number_of(page, C.P_NUMBER),
        "variable": variable_of(page),
        "slot": slot,
        "first_line": res.first_line,
        "first_line_len": res.first_line_len,
        "text": body,
        "reply": reply,
        "cards": saved,
        "source_url": url_of(page, C.P_SOURCE),
        "topic": topic_of(nt, page),
        "target": target.isoformat(),
        "prepared_at": now.isoformat(),
        "snapshot": snapshot(page),
        "dry_run": bool(a.dry_run),
    }
    (out / "post.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    gh_output(has_post="true", key=key, card_count=str(len(saved)))
    summary(f"### 준비됨: {title}\n- {res.summary()}\n- 카드 {len(saved)}장\n- 목표 시각 {target:%H:%M:%S}")
    _note(True, f"준비됨: {title} · 카드 {len(saved)}장 · 목표 {target:%H:%M}" + (" (드라이런)" if a.dry_run else ""))
    return 0


# ---------- build-site ----------
def cmd_build_site(a) -> int:
    site = ROOT / "_site"
    if site.exists():
        shutil.rmtree(site)
    (site / "c").mkdir(parents=True)
    static = ROOT / "site"
    if static.exists():
        shutil.copytree(static, site, dirs_exist_ok=True)
    (site / ".nojekyll").write_text("")
    for base in (POSTS, STAGED):
        if not base.exists():
            continue
        for d in base.iterdir():
            if not d.is_dir():
                continue
            imgs = [p for p in d.iterdir() if re.match(r"card\d+\.(png|jpg)$", p.name)]
            if imgs:
                (site / "c" / d.name).mkdir(exist_ok=True)
                for p in imgs:
                    shutil.copy2(p, site / "c" / d.name / p.name)
    n = sum(1 for _ in (site / "c").rglob("card*"))
    log(f"_site 준비: 카드 {n}장")
    return 0


# ---------- post ----------
def wait_until(target: datetime) -> None:
    while True:
        left = (target - now_kst()).total_seconds()
        if left <= 0:
            return
        log(f"목표 시각 {target:%H:%M:%S}까지 {int(left)}초 기다림")
        time.sleep(min(left, 60))


def wait_public(urls: list[str], max_wait: int = 300) -> None:
    deadline = time.time() + max_wait
    pending = list(urls)
    while pending and time.time() < deadline:
        still = []
        for u in pending:
            try:
                r = requests.head(u, timeout=20, allow_redirects=True)
                ok = r.status_code == 200 and r.headers.get("content-type", "").startswith("image/")
            except requests.RequestException:
                ok = False
            if not ok:
                still.append(u)
        pending = still
        if pending:
            time.sleep(10)
    if pending:
        raise RuntimeError("카드 공개 주소가 열리지 않는다: " + ", ".join(pending))


def same_text(a: str, b: str) -> bool:
    return checks.normalize(a) == checks.normalize(b)


def cmd_post(a) -> int:
    NOTICES.unlink(missing_ok=True)
    d = STAGED / a.key
    meta = json.loads((d / "post.json").read_text(encoding="utf-8"))
    nt, th = notion(), threads()
    pid = meta["page_id"]

    target = datetime.fromisoformat(meta["target"])
    if not a.now:
        if now_kst() - target > timedelta(minutes=C.LATE_LIMIT_MIN):
            notice(nt, pid, f"게시기가 목표 시각보다 {C.LATE_LIMIT_MIN}분 넘게 늦게 깨어나 올리지 않았다. 다음 슬롯에 다시 본다.")
            return 0
        wait_until(target)

    dry = a.dry_run or meta.get("dry_run")
    page = nt.page(pid)
    if not dry and select_of(page, C.P_STATUS) != C.S_APPROVED:
        log(f"상태가 '{select_of(page, C.P_STATUS)}'로 바뀌어 올리지 않는다.")
        summary(f"### 올리지 않음: 준비 뒤 상태가 '{select_of(page, C.P_STATUS)}'로 바뀜")
        return 0
    if snapshot(page) != meta["snapshot"]:
        notice(nt, pid, "준비한 뒤 본문·첫 답글·카드가 바뀌어 이번엔 올리지 않았다. 같은 창의 다음 깨우기(슬롯 뒤 90분까지)가 다시 검사해 올리고, 없으면 다음 슬롯에 올린다.")
        return 0

    base = a.pages_url.rstrip("/") + f"/c/{meta['key']}/"
    urls = [base + c["file"] for c in meta["cards"]]
    if urls:
        wait_public(urls)

    me = th.me()
    username = me.get("username", "")
    C.check_account(username)
    th.user_id = me["id"]

    followers_before = th.followers_count()

    media_id = None
    for p in th.recent_posts(10):
        if p.get("text") and same_text(p["text"], meta["text"]):
            media_id = p["id"]
            log(f"같은 글이 이미 올라가 있다(media {media_id}). 다시 올리지 않고 기록만 잇는다.")
            break

    if media_id is None:
        cid = th.create_post_container(meta["text"], urls, topic_tag=meta.get("topic") or None)
        th.wait_ready(cid)
        if dry:
            kind = "TEXT" if not urls else ("IMAGE" if len(urls) == 1 else "CAROUSEL")
            try:
                api_kind = th._req("GET", cid, {"fields": "status,media_type"}).get("media_type")
            except Exception:
                api_kind = None
            _note(True, f"드라이런 통과: 카드 {len(urls)}장 → {kind} 컨테이너 FINISHED"
                        + (f" (API media_type {api_kind})" if api_kind else "") + ". 게시 안 함")
            log(f"[드라이런] 컨테이너 준비 완료({cid}). 게시하지 않는다. 컨테이너는 24시간 뒤 사라진다.")
            if meta["reply"]:
                log("[드라이런] 첫 답글은 본글이 있어야 만들 수 있어 건너뛴다.")
            summary(f"### 드라이런 통과: {meta['title']}\n- 카드 {len(urls)}장 공개 주소 OK\n- 계정 @{username}\n- 컨테이너 FINISHED (게시 안 함)")
            return 0
        try:
            media_id = th.publish(cid)
        except Exception as e:
            notice(nt, pid, f"게시 실패: {e}")
            raise
    elif dry:
        log("[드라이런] 이미 올라간 글이라 할 일이 없다.")
        return 0

    permalink = ""
    for _ in range(5):
        try:
            permalink = th.permalink(media_id)
            if permalink:
                break
        except Exception:
            pass
        time.sleep(5)
    posted_at = now_kst()
    log(f"게시됨: {permalink or media_id}")

    # 캘린더 먼저 (중복 게시를 막는 1순위 기록)
    try:
        props = {C.P_STATUS: {"status": {"name": C.S_POSTED}}}
        if permalink:
            props[C.P_LINK] = {"url": permalink}
        nt.update(pid, props)
    except Exception as e:
        notice(None, None, f"게시는 됐지만 캘린더 갱신 실패: {e} / 링크 {permalink}")

    # 첫 답글
    reply_id = None
    if meta["reply"]:
        try:
            for r in th.replies(media_id):
                if r.get("is_reply_owned_by_me") and same_text(r.get("text", ""), meta["reply"]):
                    reply_id = r["id"]
                    break
            if not reply_id:
                time.sleep(10)
                rc = th.create_reply_container(meta["reply"], media_id)
                th.wait_ready(rc)
                reply_id = th.publish(rc)
            log(f"첫 답글 달림: {reply_id}")
        except Exception as e:
            notice(nt, pid, f"본글은 올라갔지만 첫 답글 실패: {e}\n직접 달 첫 답글:\n{meta['reply']}")

    # 게시 로그
    try:
        title = meta["title"] or (f"{meta['series']} #{int(meta['number']):03d}" if meta.get("number") else meta["series"])
        props = {
            "글": {"title": rt(title)},
            "게시 시각": {"date": {"start": posted_at.replace(microsecond=0).isoformat()}},
            "콘텐츠": {"relation": [{"id": pid}]},
            "첫 줄 글자 수": {"number": meta["first_line_len"]},
        }
        if permalink:
            props["게시 링크"] = {"url": permalink}
        if meta.get("series"):
            props["연재"] = {"select": {"name": meta["series"]}}
        if meta.get("variable"):
            props["변수"] = {"select": {"name": meta["variable"]}}
        if followers_before is not None:
            props["팔로워 게시 전"] = {"number": followers_before}
        props["메모"] = {"rich_text": rt(f"게시기 자동 기록 · media {media_id} · 슬롯 {meta['slot']}")}
        nt.create(C.POSTLOG_DS, props)
    except Exception as e:
        notice(nt, pid, f"게시 로그 한 줄 만들기 실패: {e}")

    # 원장 (저장소에 커밋될 기록: 게시된 글과 카드만)
    led = POSTS / meta["key"]
    led.mkdir(parents=True, exist_ok=True)
    for c in meta["cards"]:
        shutil.copy2(d / c["file"], led / c["file"])
    rec = {k: meta[k] for k in ("key", "title", "series", "number", "variable", "slot", "first_line_len", "text", "reply", "cards", "source_url")}
    rec.update({"media_id": media_id, "reply_id": reply_id, "permalink": permalink, "posted_at": posted_at.isoformat(),
                "followers_before": followers_before})
    (led / "post.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    summary(f"### 게시됨: {meta['title']}\n- {permalink}\n- 첫 답글: {'있음' if reply_id else '없음'}")
    _note(True, f"게시됨: {meta['title']} · {permalink or media_id} · 첫 답글 {'있음' if reply_id else '없음'}")
    return 0


# ---------- comments ----------
def _warn(msg: str) -> None:
    print(f"::warning title=게시기 경고::{msg}", flush=True)
    summary(f"- ⚠️ {msg}")


def cmd_comments(a) -> int:
    from . import comments
    try:
        res = comments.run(notion(), threads(), now_kst(), log)
    except ThreadsError as e:
        _warn(f"Threads API 호출 실패로 댓글을 못 모았다 (check 워크플로가 알린다): {e}")
        return 0
    _note(True, f"댓글 수집: 최근 글 {res['posts']}개 · 외부 답글 {res['total_external']}개 · 새로 넣음 {res['new']} · 고침 {res['updated']}")
    return 0


# ---------- metrics ----------
def cmd_metrics(a) -> int:
    from . import metrics
    try:
        res = metrics.run(notion(), threads(), now_kst(), log)
    except ThreadsError as e:
        _warn(f"Threads API 호출 실패로 지표를 못 쟀다 (check 워크플로가 알린다): {e}")
        gh_output(snapshot="false")
        return 0
    for line in res["filled"]:
        _note(True, "24시간 지표 채움 · " + line)
    for line in res["daily"]:
        _note(True, "그날 팔로워 증감 · " + line)
    for t in res["late"]:
        _note(True, f"30시간이 지나 24시간 지표를 못 잼 · {t}")
    if res["snapshot"] is not None:
        _note(True, f"팔로워 하루 기록 · {res['snapshot']}")
    gh_output(snapshot="true" if res["snapshot"] is not None else "false")
    return 0


# ---------- check ----------
def find_stuck(nt: Notion, now: datetime) -> list[tuple[dict, datetime, bool]]:
    """아직 '승인'인데 예정 슬롯 시각이 지난 행. (행, 슬롯 시각, 아직 게시 창 안인가)."""
    out = []
    for r in nt.query(C.CALENDAR_DS, {"property": C.P_STATUS, "status": {"equals": C.S_APPROVED}}):
        if url_of(r, C.P_LINK):
            continue
        when, _ = row_when(r)
        if when is None or when > now:
            continue
        if now - when > timedelta(hours=C.OVERDUE_FRESH_HOURS):
            continue  # 게시기가 다음 실행에서 '보류'로 옮긴다
        out.append((r, when, now <= when + timedelta(minutes=C.LATE_LIMIT_MIN)))
    return out


def cmd_check(a) -> int:
    """슬롯이 지난 뒤 '승인'에 머문 글과 Threads API 상태를 본다. 문제면 1(빨간불·메일)."""
    NOTICES.unlink(missing_ok=True)
    now = now_kst()
    ok, rekick = True, False
    try:
        me = threads().me()
        C.check_account(me.get("username"))
        _note(True, f"Threads API 정상 · @{me.get('username')}")
    except Exception as e:
        ok = False
        _note(False, f"Threads API 호출 실패: {e} → 게시·지표·댓글 수집이 모두 멈춘다. Meta 개발자 화면에서 앱 상태·테스터 권한·토큰을 확인하라")
    nt = notion()
    try:
        stuck = find_stuck(nt, now)
    except Exception as e:
        ok, stuck = False, []
        _note(False, f"Notion 읽기 실패: {e}")
    for r, when, in_window in stuck:
        title = text_of(r, C.P_TITLE)
        if in_window:
            rekick = True
            _note(True, f"'{title}' ({when:%m/%d %H:%M} 슬롯)이 아직 '승인'이다. 창 안이라 게시기를 한 번 더 깨운다")
        else:
            ok = False
            notice(nt, r["id"], f"{when:%m/%d %H:%M} 슬롯 창이 끝났는데 아직 '승인'이다. 게시기가 안 깼거나 실패했다. "
                                "그대로 두면 다음 슬롯에 올라가고, 바로 올리려면 Actions의 publish를 page_id로 수동 실행(dry_run 끄기)한다")
    if not stuck:
        _note(True, f"{now:%m/%d %H:%M} 남은 '승인' 글 없음")
    # 심장박동: 오늘 그 런이 돌았나 (런 로그 DB가 설정돼 있을 때만)
    if C.RUNLOG_DS:
        job = C.RUNLOG_MORNING_JOB if now.hour < 12 else C.RUNLOG_EVENING_JOB
        try:
            if run_logged(nt, now.date(), job):
                _note(True, f"{now:%m/%d} {job} 런 로그 있음")
            else:
                ok = False
                _note(False, f"{now:%m/%d} {job}이 돌지 않았다(런 로그 없음). 사용량 한도나 오류다. 오늘 글은 쉬거나 '보류' 행을 승인한다")
        except Exception as e:
            _note(False, f"런 로그 읽기 실패: {e}")
    # 글 0일 연속 (posts/ 원장)
    last = last_post_day()
    if last is not None:
        gap = (now.date() - last).days
        if gap >= C.ZERO_DAYS_RED:
            ok = False
            _note(False, f"{last:%m/%d} 뒤로 {gap}일째 게시 0. 글감 회의가 필요하다(보고서 4절 소재 파이프라인)")
        elif gap >= C.ZERO_DAYS_WARN:
            _warn(f"{last:%m/%d} 뒤로 {gap}일째 게시 0")
    gh_output(rekick="true" if rekick else "false", stuck="true" if stuck else "false")
    return 0 if ok else 1


def run_logged(nt: Notion, day, job: str) -> bool:
    rows = nt.query(C.RUNLOG_DS, {"and": [
        {"property": C.RUNLOG_P_DATE, "date": {"equals": day.isoformat()}},
        {"property": C.RUNLOG_P_JOB, "select": {"equals": job}},
    ]})
    return bool(rows)


# ---------- tick ----------
def cmd_tick(a) -> int:
    from .tick import decide
    now = now_kst()
    jobs = decide(now)
    log(f"{now:%m/%d %H:%M} KST → 깨울 것: {', '.join(jobs) or '없음'}")
    gh_output(dispatch=",".join(jobs))
    return 0


# ---------- selftest ----------
def cmd_selftest(a) -> int:
    """Threads가 GitHub Pages 이미지를 가져가는지 본다. 캐러셀 컨테이너까지만 만들고 게시하지 않는다."""
    base = a.pages_url.rstrip("/") + "/selftest/"
    urls = [base + "t1.png", base + "t2.png"]
    wait_public(urls)
    th = threads()
    me = th.me()
    try:
        C.check_account(me.get("username"))
    except RuntimeError as e:
        _note(False, str(e))
        return 1
    th.user_id = me["id"]
    try:
        cid = th.create_post_container("게시기 시험 (게시하지 않음)", urls)
        th.wait_ready(cid)
    except Exception as e:
        _note(False, f"캐러셀 컨테이너 실패 (Pages 이미지 주소를 Threads가 못 가져감?): {e}")
        return 1
    _note(True, f"캐러셀 컨테이너 FINISHED: Threads가 Pages 이미지 2장을 가져갔다. 게시는 안 함 (컨테이너 {cid}는 24시간 뒤 사라짐)")
    return 0


# ---------- verify ----------
CAL_PROPS = [C.P_TITLE, C.P_STATUS, C.P_SLOT, C.P_DATE, C.P_BODY, C.P_REPLY, C.P_CARDS, C.P_LINK,
             C.P_SERIES, C.P_NUMBER, C.P_VAR, C.P_MEMO, C.P_SOURCE]
COMMENT_PROPS = ["내용", "종류", "상태", "내가 답함", "작성자", "시각", "글 링크", "답글 링크", "대댓", "답글 ID", "글 ID", "수집 시각"]
LOG_PROPS = ["글", "게시 시각", "게시 링크", "연재", "콘텐츠", "변수", "첫 줄 글자 수", "메모", "팔로워 게시 전",
             "조회 24h", "좋아요 24h", "외부 댓글 24h", "리포스트 24h", "공유 24h", "팔로워 24h 뒤", "그날 팔로워 증감"]


def _note(ok: bool, msg: str) -> None:
    """결과를 로그 + 실행 요약 + 주석(annotation)으로 남긴다. 비밀값은 넣지 않는다."""
    level = "notice" if ok else "error"
    print(f"::{level} title=verify::{msg}", flush=True)
    summary(f"- {'✅' if ok else '❌'} {msg}")


def cmd_verify(a) -> int:
    ok = True
    names = ("THREADS_TOKEN", "NOTION_TOKEN", "GH_PAT", "THREADS_APP_ID", "THREADS_APP_SECRET")
    missing = [n for n in names if not os.environ.get(n)]
    if any(n in missing for n in ("THREADS_TOKEN", "NOTION_TOKEN")):
        ok = False
    # GitHub 주석은 단계마다 notice 10개까지라 한 줄로 묶는다
    _note(not missing, "Secrets: 모두 있음" if not missing else "Secrets 없음: " + ", ".join(missing))

    if os.environ.get("THREADS_TOKEN"):
        try:
            th = threads()
            me = th.me()
            th.user_id = me["id"]
            try:
                C.check_account(me.get("username"))
                _note(True, f"Threads 계정 @{me.get('username')}")
            except RuntimeError as e:
                ok = False
                _note(False, f"Threads 계정 @{me.get('username')} · {e}")
            lim = (th.publishing_limit().get("data") or [{}])[0]
            used = lim.get("quota_usage")
            total = (lim.get("config") or {}).get("quota_total")
            _note(True, f"Threads 게시 한도: 24시간 {used}/{total}")
            fc = th.followers_count()
            _note(fc is not None, f"Threads 팔로워: {fc}" if fc is not None else "Threads 팔로워 읽기 실패 (threads_manage_insights 권한 확인)")
            posts = [p for p in th.recent_posts(5) if p.get("media_type") != "REPOST_FACADE"]
            if posts:
                p0 = posts[0]
                ins = th.media_insights(p0["id"])
                reps = th.replies(p0["id"])
                ext = sum(1 for r in reps if not r.get("is_reply_owned_by_me"))
                good = bool(ins)
                try:
                    hits = th.keyword_search("AI", 5)
                    own = sum(1 for h in hits if h.get("username") == C.ACCOUNT)
                    _note(True, f"키워드 검색: 됨 · 'AI' 최근 {len(hits)}건 중 내 글 {own}건"
                                + (" (내 글만 나옴 → 앱 심사 전 제한)" if hits and own == len(hits) else ""))
                except Exception as e:
                    msg = str(e)
                    _note(True, "키워드 검색: 권한 없음 (threads_keyword_search 미승인) · "
                                + ("permission" if "ermission" in msg else msg[:120]))
                _note(good, f"최근 글 지표 읽기 ({(p0.get('permalink') or '').rsplit('/', 1)[-1]}): "
                            + (", ".join(f"{k} {v}" for k, v in ins.items()) if ins else "실패")
                            + f" · 답글 {len(reps)}(외부 {ext})")
        except Exception as e:
            ok = False
            _note(False, f"Threads 확인 실패: {e}")

    if os.environ.get("NOTION_TOKEN"):
        nt = notion()
        from .comments import COMMENTS_DS
        for name, ds, need in (("콘텐츠 캘린더", C.CALENDAR_DS, CAL_PROPS), ("게시 로그와 지표", C.POSTLOG_DS, LOG_PROPS),
                               ("댓글함", COMMENTS_DS, COMMENT_PROPS)):
            try:
                info = nt._req("GET", f"data_sources/{ds}")
                have = set((info.get("properties") or {}).keys())
                missing = [p for p in need if p not in have]
                rows = nt.query(ds)
                good = not missing
                ok &= good
                _note(good, f"Notion {name}: 행 {len(rows)}개" + (f", 없는 칸: {', '.join(missing)}" if missing else ", 필요한 칸 모두 있음"))
            except Exception as e:
                ok = False
                hint = " ('스레드 운영' 페이지에 연결 접근 권한을 줬는지 확인)" if "404" in str(e) or "object_not_found" in str(e) else ""
                _note(False, f"Notion {name} 읽기 실패{hint}: {str(e)[:200]}")
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="publisher")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--slot", required=True, choices=list(C.SLOTS) + ["auto"])
    p.add_argument("--page-id", default="")
    p.add_argument("--now", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--watch", action="store_true", help="게시 창이 닫힐 때까지 승인 글을 기다린다")
    p = sub.add_parser("post")
    p.add_argument("--key", required=True)
    p.add_argument("--pages-url", required=True)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--now", action="store_true")
    sub.add_parser("build-site")
    sub.add_parser("verify")
    sub.add_parser("metrics")
    sub.add_parser("comments")
    sub.add_parser("check")
    sub.add_parser("tick")
    p = sub.add_parser("selftest")
    p.add_argument("--pages-url", required=True)
    a = ap.parse_args(argv)
    fn = {"prepare": cmd_prepare, "post": cmd_post, "build-site": cmd_build_site, "verify": cmd_verify,
          "selftest": cmd_selftest, "metrics": cmd_metrics, "comments": cmd_comments,
          "check": cmd_check, "tick": cmd_tick}[a.cmd]
    rc = fn(a)
    if a.cmd in ("prepare", "post", "check"):
        gh_output(notices="true" if NOTICES.exists() and NOTICES.read_text(encoding="utf-8").strip() else "false")
    return rc


if __name__ == "__main__":
    sys.exit(main())
