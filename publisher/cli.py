"""게시기 명령.

  python -m publisher.cli prepare --slot 08:07 [--page-id ID] [--now]
  python -m publisher.cli post --key KEY --pages-url URL [--dry-run] [--now]
  python -m publisher.cli build-site
  python -m publisher.cli verify            # 토큰·Notion 연결 확인 (값은 안 찍음)

prepare: 캘린더에서 올릴 글 하나를 고르고 형식 검사, 카드를 staged/<key>/에 받는다.
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
from .threads import Threads

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


def notice(nt: Notion | None, page_id: str | None, msg: str) -> None:
    """운영자에게 알림: Notion 행 댓글 + notices.txt (워크플로가 빨간불로 끝냄)."""
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
def pick(nt: Notion, slot: str, now: datetime, page_id: str | None) -> tuple[dict | None, datetime | None]:
    if page_id:
        page = nt.page(page_id)
        if select_of(page, C.P_STATUS) != C.S_APPROVED:
            notice(nt, page_id, f"시험 대상 행의 상태가 '{select_of(page, C.P_STATUS)}'다. '승인'인 행만 올린다.")
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
    NOTICES.unlink(missing_ok=True)
    nt = notion()
    now = now_kst()
    slot = a.slot
    page, slot_dt = pick(nt, slot, now, a.page_id)
    if not page:
        log("올릴 글 없음.")
        gh_output(has_post="false")
        summary(f"### {now:%m/%d} {slot}: 올릴 승인 글 없음")
        return 0

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
        "target": target.isoformat(),
        "prepared_at": now.isoformat(),
        "snapshot": snapshot(page),
    }
    (out / "post.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    gh_output(has_post="true", key=key, card_count=str(len(saved)))
    summary(f"### 준비됨: {title}\n- {res.summary()}\n- 카드 {len(saved)}장\n- 목표 시각 {target:%H:%M:%S}")
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

    page = nt.page(pid)
    if select_of(page, C.P_STATUS) != C.S_APPROVED:
        log(f"상태가 '{select_of(page, C.P_STATUS)}'로 바뀌어 올리지 않는다.")
        summary(f"### 올리지 않음: 준비 뒤 상태가 '{select_of(page, C.P_STATUS)}'로 바뀜")
        return 0
    if snapshot(page) != meta["snapshot"]:
        notice(nt, pid, "준비한 뒤 본문·첫 답글·카드가 바뀌어 이번엔 올리지 않았다. 다음 슬롯에 다시 검사해 올린다.")
        return 0

    base = a.pages_url.rstrip("/") + f"/c/{meta['key']}/"
    urls = [base + c["file"] for c in meta["cards"]]
    if urls:
        wait_public(urls)

    me = th.me()
    username = me.get("username", "")
    if username != C.ACCOUNT:
        raise RuntimeError(f"토큰 계정이 @{username}다. @{C.ACCOUNT}에만 올린다.")
    th.user_id = me["id"]

    followers_before = th.followers_count()

    media_id = None
    for p in th.recent_posts(10):
        if p.get("text") and same_text(p["text"], meta["text"]):
            media_id = p["id"]
            log(f"같은 글이 이미 올라가 있다(media {media_id}). 다시 올리지 않고 기록만 잇는다.")
            break

    if media_id is None:
        cid = th.create_post_container(meta["text"], urls)
        th.wait_ready(cid)
        if a.dry_run:
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
    elif a.dry_run:
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
    return 0


# ---------- verify ----------
def cmd_verify(a) -> int:
    ok = True
    if os.environ.get("THREADS_TOKEN"):
        try:
            th = threads()
            me = th.me()
            th.user_id = me["id"]
            log(f"Threads: @{me.get('username')} (id {me.get('id')})")
            if me.get("username") != C.ACCOUNT:
                ok = False
                log(f"계정이 @{C.ACCOUNT}가 아니다. 게시기는 이 토큰으로 올리지 않는다.")
            lim = th.publishing_limit()
            log(f"게시 한도: {json.dumps(lim.get('data', lim), ensure_ascii=False)}")
            fc = th.followers_count()
            log(f"팔로워: {fc if fc is not None else '읽기 실패(threads_manage_insights 권한 확인)'}")
        except Exception as e:
            ok = False
            log(f"Threads 확인 실패: {e}")
    else:
        log("THREADS_TOKEN 없음 (아직 안 넣었으면 정상)")
    if os.environ.get("NOTION_TOKEN"):
        try:
            nt = notion()
            for name, ds in (("콘텐츠 캘린더", C.CALENDAR_DS), ("게시 로그와 지표", C.POSTLOG_DS)):
                rows = nt.query(ds)
                log(f"Notion {name}: 행 {len(rows)}개 읽음")
        except Exception as e:
            ok = False
            log(f"Notion 확인 실패: {e}")
    else:
        log("NOTION_TOKEN 없음 (아직 안 넣었으면 정상)")
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="publisher")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--slot", required=True, choices=list(C.SLOTS))
    p.add_argument("--page-id", default="")
    p.add_argument("--now", action="store_true")
    p = sub.add_parser("post")
    p.add_argument("--key", required=True)
    p.add_argument("--pages-url", required=True)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--now", action="store_true")
    sub.add_parser("build-site")
    sub.add_parser("verify")
    a = ap.parse_args(argv)
    fn = {"prepare": cmd_prepare, "post": cmd_post, "build-site": cmd_build_site, "verify": cmd_verify}[a.cmd]
    rc = fn(a)
    if a.cmd in ("prepare", "post"):
        gh_output(notices="true" if NOTICES.exists() and NOTICES.read_text(encoding="utf-8").strip() else "false")
    return rc


if __name__ == "__main__":
    sys.exit(main())
