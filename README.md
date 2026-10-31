# interpret-note-publisher

스레드 계정 게시기. Notion 콘텐츠 캘린더에서 운영자가 **승인**한 글만 Threads 공식 API로 올린다. Claude API는 쓰지 않는다.

## 흐름

```
깨우기: 외부 cron(cron-job.org 등)이 15분마다 tick → tick.yml이 한국 시각을 보고 kick을 보냄 (슬롯 52분 전)
  GitHub 예약 실행은 10/27~31 전부 2~6시간 늦게 와서 못 믿는다. 예비로만 둔다. 자세한 건 kick/README.md
  깬 시각이 [슬롯-55분, 슬롯+90분] 안이면 그 슬롯을 맡고, 밖이면 그냥 끝난다
  └ prepare   감시 모드: 창이 닫힐 때까지 3분마다 캘린더에서 상태 '승인' + 오늘 + 이 슬롯 행 하나를 찾음
              → 형식 검사 → 카드 받기 (늦게 승인해도 창 안이면 잡는다. 08:30 승인 → 08:33 게시)
  └ pages     카드를 GitHub Pages에 공개 (Threads가 공개 주소에서 이미지를 가져감)
  └ post      08:07·21:37 + 0~9분까지 기다림 → 승인·내용이 그대로인지 다시 확인
              → 본문 + 카드(1/2 → 2/2 캐러셀) 게시 → 첫 답글(reply_to_id)
              → 캘린더 '게시됨' + 게시 링크 → '게시 로그와 지표'에 한 줄 → posts/에 원장 커밋
```

- 저녁 글이 없는 날은 조용히 끝난다.
- 게시 시각이 지나 승인된 글은 다음 슬롯에 올린다. 그 슬롯에 이미 승인 글이 있으면 올리지 않고 알린다. 예정 시각에서 36시간이 지나면 '보류'로 옮긴다.
- 같은 슬롯에 승인 글이 2개면 아무것도 올리지 않는다. 하루 최대 2글.
- 준비한 뒤 본문·첫 답글·카드가 바뀌거나 승인이 풀리면 올리지 않는다.
- 같은 본문이 이미 계정에 있으면 다시 올리지 않는다(중복 게시 방지).

## 형식 검사 (코드, LLM 없음)

본문: 첫 줄 25자 이내(공백 포함) · 빈 줄 빼고 8줄 이하 · 링크 0개(맨 도메인 포함) · 500자 이하(이모지는 UTF-8 바이트로 셈).
토픽: 캘린더 '토픽' 칸에 태그 1개(1~50자, `.`·`&` 금지)를 넣으면 Threads 토픽 태그로 단다. 비면 안 단다. 규칙에 어긋나면 알리고 태그 없이 올린다.
첫 답글: API 한도만(500자, 고유 링크 5개). 카드: PNG/JPG, 8MB 이하, 20장 이하.
하나라도 어기면 올리지 않고 알린다.

## 알림

빨간불(실패한 실행) = 볼 것이 있다는 뜻이다. GitHub가 메일을 보내고, 그 글의 Notion 행에 `[게시기]` 댓글로 이유를 남긴다.
`check`(슬롯 뒤 08:45·22:15 무렵)가 아직 '승인'인 글과 Threads API 상태를 보고 빨간불을 낸다. metrics·comments는 API 오류를 경고로만 남긴다(메일이 매시 오지 않게).

## Secrets (Settings → Secrets and variables → Actions)

| 이름 | 무엇 | 누가 넣나 |
|---|---|---|
| `NOTION_TOKEN` | Notion 내부 통합 키 | 운영자 |
| `THREADS_APP_ID` | Meta Threads 앱 ID | 운영자 |
| `THREADS_APP_SECRET` | Meta Threads 앱 시크릿 | 운영자 |
| `THREADS_AUTH_CODE` | 처음 한 번, 1시간짜리 코드 | 운영자 (token-setup이 쓰고 지움) |
| `THREADS_TOKEN` | 장기 토큰(60일) | token-setup이 넣고, token-refresh가 매주 새로 고침 |
| `GH_PAT` | 이 저장소 Secrets 쓰기 권한만 있는 토큰 | 운영자 |

Variables(같은 화면의 **Variables** 탭)에는 `THREADS_USERNAME`(게시할 Threads 계정 이름, `@` 없이)을 넣는다. 선택: `NOTION_RUNLOG_DS`(런 로그 데이터 소스 id)를 넣으면 check가 런이 돌았는지도 본다. `NOTION_INBOX_DS`(수집함 데이터 소스 id, 칸: 제목·링크·출처·종류·게시일·수집일·요약·한국 중복·상태)를 넣으면 collect가 행을 만든다. 비어 있으면 게시·verify·metrics·comments가 올리지 않고 멈춘다. 계정 이름은 코드에 두지 않는다.

토큰과 키는 채팅·코드·로그에 남기지 않는다.

## 워크플로

- `tick` 외부 cron이 15분마다 깨움. 한국 시각을 보고 kick(게시기)·check·metrics·comments를 깨운다 (publisher/tick.py)
- `publish` 게시. 손으로 돌릴 때: `slot`(auto = 창이 닫힐 때까지 승인 글을 기다림) · `page_id`(시험할 캘린더 행) · `dry_run`(기본 켜짐: 컨테이너까지만 만들고 게시 안 함)
- `check` 슬롯 뒤 확인: 아직 '승인'인 글이 있으면 알리고(창 안이면 한 번 더 깨움), Threads API가 죽었으면 빨간불. Variables `NOTION_RUNLOG_DS`가 있으면 오늘 아침·저녁 런의 런 로그 행이 있는지(심장박동)도 보고, posts/ 원장으로 게시 0일이 2일째면 경고·3일째면 빨간불
- `collect` 수집기 v1(매일 05시, tick): 공식 발표·릴리스 노트·앱스토어 '새로운 기능'·매체·뉴스레터·실무자·HN·유튜브 피드(publisher/sources.py)에서 최근 14일 후보를 모아 Notion '수집함'에 넣는다(Variables `NOTION_INBOX_DS`). 없으면 collected/digest.md 아티팩트. 한국 피드(GeekNews·AI타임스·조코딩)에 같은 제품 이름이 있으면 '한국 중복: 있을 수 있음'. 점수·넓이 판정은 안 한다(저녁 글감 런·운영자 몫)
- `verify` Threads 계정·한도·팔로워·키워드 검색 권한, Notion 칸 확인 (코드가 바뀌면 저절로)
- `metrics` 매시(tick): 게시 뒤 24~30시간 글의 지표를 게시 로그에, 08~10시 첫 실행이 팔로워 하루 기록
- `comments` 매시(tick): 내 최근 글 3개 답글 → Notion '댓글함' (내가 답함 표시)
- `token-setup` 첫 토큰 발급 / `token-refresh` 매주 월 03:13 갱신
- `site` 토큰 받기 페이지 공개. Variables `NOTES_PAGE=true`를 넣으면 Pages의 `notes/`에 '통역 노트 모음' 페이지(게시된 글·카드·출처·스레드 링크, posts/ 원장에서 자동 생성)도 같이 공개한다. 켜기 전엔 만들지 않는다

## 하지 않는 것

댓글·대댓 자동 게시, DM(스레드 DM API 없음), 로그인형 봇, 자동 팔로우·좋아요.

## 주의

- 공개 저장소에는 게시된 글과 카드만 커밋한다(`posts/`). 승인 전 초안은 올리지 않는다.
- 공개 저장소의 예약 실행은 60일 동안 커밋이 없으면 GitHub가 끈다. 게시 기록 커밋이 이를 막는다.
- 시험: `python -m unittest discover -s tests -t .`
