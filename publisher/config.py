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
OVERDUE_FRESH_HOURS = 36    # 늦은 승인 글은 예정 시각 36시간 안까지만 (스킬 7장 신선도)
DAILY_MAX = 2               # 하루 최대 2글 (스킬 2장)
CARDS_MIN = 1               # 카드 1(원문 카드)은 늘 넣는다 (스킬 8장). 메모에 '카드 없음'이 있으면 예외
NO_CARD_FLAG = "카드 없음"

ACCOUNT = os.environ.get("THREADS_USERNAME", "interpret.note")

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

S_APPROVED = "승인"
S_POSTED = "게시됨"
