"""외부 cron이 보내는 'tick'을 받아 무엇을 깨울지 정한다. LLM 없음.

GitHub 예약 실행(cron)은 몇 시간씩 늦게 와서(10/27~31 확인: 전부 2~6시간 지연) 분 단위 게시에 못 쓴다.
대신 외부 무료 cron(예: cron-job.org)이 15분마다 이 저장소에 tick 한 번만 보낸다
(repository_dispatch event_type=tick, 또는 tick.yml의 workflow_dispatch). tick.yml이 이 모듈로 한국 시각을 보고
아래를 골라 같은 저장소의 다른 워크플로를 깨운다. 창은 30분 폭이라 tick이 몇 분 늦어도 한 번은 걸린다.
같은 창에서 두 번 깨워도 괜찮다(publish는 한 번에 하나만 돌고, 이미 올린 슬롯은 바로 끝낸다).

- kick:     슬롯 52분 전부터 30분 (08:07 슬롯 → 07:15~07:44, 21:37 슬롯 → 20:45~21:14). publish가 감시 모드로 돈다.
- check:    슬롯 38분 뒤부터 30분 (08:45~09:14, 22:15~22:44). 아직 '승인'인 글과 Threads API 상태를 본다.
- metrics·comments: 매시 첫 tick(분이 15 미만일 때).
- collect:  05:00~05:29 (수집기 v1, 하루 한 번).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from . import config as C

TICK_MIN = 15               # 외부 cron 간격(분)
WINDOW_MIN = 30             # 깨우기 창 폭(분). tick 두 번이 들어온다
KICK_OFFSET_MIN = -52       # 슬롯 기준 kick 창 시작 (게시기 창 시작 55분 전보다 조금 뒤)
CHECK_OFFSET_MIN = 38       # 슬롯 기준 check 창 시작


def _in(now: datetime, slot_dt: datetime, offset_min: int) -> bool:
    start = slot_dt + timedelta(minutes=offset_min)
    return start <= now < start + timedelta(minutes=WINDOW_MIN)


def decide(now: datetime) -> list[str]:
    """지금(KST) 깨울 워크플로의 repository_dispatch event_type 목록."""
    out: list[str] = []
    for slot in C.SLOT_ORDER:
        h, m = C.SLOTS[slot]
        slot_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if _in(now, slot_dt, KICK_OFFSET_MIN) and "kick" not in out:
            out.append("kick")
        if _in(now, slot_dt, CHECK_OFFSET_MIN) and "check" not in out:
            out.append("check")
    collect_at = now.replace(hour=C.COLLECT_HOUR, minute=0, second=0, microsecond=0)
    if _in(now, collect_at, 0):
        out.append("collect")
    if now.minute < TICK_MIN:
        out += ["metrics", "comments"]
    return out
