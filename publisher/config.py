"""설정값. 비밀값(토큰·키)은 여기에 두지 않는다. 모두 GitHub Secrets 환경변수로 받는다."""
from __future__ import annotations

import os
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

# 예약 게시 스위치. False면 07:47·21:17 예약 실행은 아무것도 올리지 않는다.
# 손으로 돌리는 시험(page_id 지정)과 드라이런은 이 값과 상관없이 된다.
# 운영자가 '게시기로 넘긴다'고 정한 뒤에만 True로 바꾼다 (런이 카드를 Notion에 붙이고, 손 게시를 멈춘 뒤).
LIVE = True  # 10/27 밤 본부 결정: 10/28 08:07 슬롯부터 켬

# Notion '스레드 운영' 데이터 소스 (collection:// ID)
CALENDAR_DS = os.environ.get("NOTION_CALENDAR_DS", "90d2816a-a68b-4a74-82e5-bff83f099149")
POSTLOG_DS = os.environ.get("NOTION_POSTLOG_DS", "84c80006-4e6d-489d-a9da-e0cacbc7b79a")

# 게시 슬롯 (KST). 순서가 '앞 슬롯' 판단에 쓰인다.
SLOTS = {"08:07": (8, 7), "21:37": (21, 37)}
SLOT_ORDER = ["08:07", "21:37"]

JITTER_MAX_MIN = 9          # 0~9분 무작위로 늦춤 (10/27 운영자 선택)
LATE_LIMIT_MIN = 90         # 목표 시각보다 90분 넘게 늦게 깨면 올리지 않고 다음 슬롯으로
EARLY_MIN = 55              # 슬롯 55분 전부터 깨어난 실행이 그 슬롯을 맡는다
WATCH_INTERVAL_SEC = 180    # 감시 모드(--watch)에서 '승인' 행을 다시 보는 간격
# GitHub 예약 실행은 늦거나 빠지기도 해서 슬롯마다 20분 간격으로 여러 번 깨운다(publish.yml).
# 슬롯은 cron 문자열이 아니라 깬 시각으로 정한다: [슬롯-55분, 슬롯+90분] 안이면 그 슬롯, 밖이면 아무것도 안 함.
OVERDUE_FRESH_HOURS = 36    # 늦은 승인 글은 예정 시각 36시간 안까지만 (스킬 7장 신선도)
DAILY_MAX = 2               # 하루 최대 2글 (스킬 2장)
CARDS_MIN = 1               # 카드 1(원문 카드)은 늘 넣는다 (스킬 8장). 메모에 '카드 없음'이 있으면 예외
NO_CARD_FLAG = "카드 없음"

# 게시할 Threads 계정 이름. 코드에 두지 않고 Actions Variables(THREADS_USERNAME)에서만 읽는다.
ACCOUNT = os.environ.get("THREADS_USERNAME", "").strip().lstrip("@")
ACCOUNT_WHERE = "Settings → Secrets and variables → Actions → Variables의 THREADS_USERNAME"


def check_account(username: str | None) -> None:
    """토큰 계정이 설정한 계정인지 본다. 설정이 비었거나 다르면 올리지 않는다."""
    if not ACCOUNT:
        raise RuntimeError(f"THREADS_USERNAME이 비어 있다. {ACCOUNT_WHERE}에 넣어라.")
    if username != ACCOUNT:
        raise RuntimeError(f"토큰 계정이 @{username}다. @{ACCOUNT}에만 올린다.")

# 캘린더 속성 이름
P_TITLE = "제목"
P_STATUS = "상태"
P_SLOT = "슬롯"
P_DATE = "게시 예정"
P_BODY = "본문"
P_REPLY = "첫 답글"
P_CARDS = "카드"
P_LINK = "게시 링크"
P_SERIES = "연재"
P_NUMBER = "번호"
P_VAR = "변수"
P_MEMO = "메모"
P_SOURCE = "원문 링크"
P_TOPIC = "토픽"             # 선택: Threads 토픽 태그 1개 (1~50자, '.'·'&' 금지). 비면 안 단다

# 런 로그 (Notion). 아침·저녁 런이 끝에 1행 쓴다. check가 "오늘 런이 돌았나"(심장박동)를 본다. 비어 있으면 그 검사는 건너뛴다
RUNLOG_DS = os.environ.get("NOTION_RUNLOG_DS", "")
RUNLOG_P_DATE = "날짜"
RUNLOG_P_JOB = "작업"
RUNLOG_MORNING_JOB = "아침 런"
RUNLOG_EVENING_JOB = "저녁 글감 런"
# 글 0일 연속 (posts/ 원장 기준): 2일째 노랑, 3일째 빨강 "글감 회의 필요"

# 수집함 (Notion). 수집기 v1(collect)이 쓴다. 비어 있으면 collected/digest.md만 만든다
INBOX_DS = os.environ.get("NOTION_INBOX_DS", "")
INBOX_P_TITLE = "제목"
INBOX_P_LINK = "링크"
INBOX_P_SOURCE = "출처"
INBOX_P_KIND = "종류"
INBOX_P_PUBLISHED = "게시일"
INBOX_P_COLLECTED = "수집일"
INBOX_P_SUMMARY = "요약"
INBOX_P_KDUP = "한국 중복"
INBOX_P_STATUS = "상태"
INBOX_S_NEW = "새로"
COLLECT_SINCE_DAYS = 14      # 신선도. 48시간이 아니다: 날짜 없는 글감이 가장 잘 됐다(리서치 2026-10-31)
COLLECT_HOUR = 5             # tick이 이 시각(KST) 창에서 collect를 깨운다
COLLECT_MAX_PER_SOURCE = 12  # 소스마다 최신 N건까지만(첫 실행에 수백 건이 한꺼번에 들어가지 않게)
ZERO_DAYS_WARN = 2
ZERO_DAYS_RED = 3

S_APPROVED = "승인"
S_POSTED = "게시됨"
