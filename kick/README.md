# 깨우기

게시기(publish)는 정해진 시각에 저절로 깨어나지 않는다. 누가 깨워야 한다. GitHub 예약 실행(cron)은 10/27~31 확인 결과 전부 예정보다 2~6시간 늦게 와서 쓸 수 없었다.

## 깨우는 길 (10/31 정리)

| 길 | 누가 | 언제 | 하는 일 |
|---|---|---|---|
| **tick** (기본) | 외부 cron(예: cron-job.org) → `tick.yml` | 15분마다 | 한국 시각을 보고 `kick`(07:15~07:44, 20:45~21:14) · `check`(08:45~09:14, 22:15~22:44) · `metrics`·`comments`(매시)를 repository_dispatch로 깨운다. 판정은 `publisher/tick.py` |
| **kick** | tick.yml, check.yml | 슬롯 52분 전 | publish가 **감시 모드**로 돈다: 창(슬롯 55분 전 ~ 90분 뒤)이 닫힐 때까지 3분마다 '승인' 행을 다시 보고, 생기면 슬롯 시각(+0~9분)에 올린다. 슬롯이 지난 뒤 승인되면 바로 올린다 |
| `kick/slot.txt` push | 사람·Claude | 비상 | 첫 줄 `live auto <시각> <누가>` = 감시 모드 실제 게시, `dry <슬롯·auto> <시각> <누가>` = 드라이런 한 번 |
| Run workflow | 사람 | 비상·시험 | Actions → publish → Run workflow. slot `auto` + dry_run 끔 = 감시 모드 실제 게시. page_id를 넣으면 그 행을 시각과 상관없이 바로 올린다 |
| GitHub 예약 | GitHub | 07:05·07:25·20:35·20:55 | 예비. 늦게 와서 창 밖이면 그냥 끝나고, 창 안이면 감시 모드로 돈다 |

- 여러 번 깨워도 한 슬롯에 한 번만 올라간다: 실행은 차례로 돌고(concurrency 'publish'), 이미 올린 슬롯은 원장(posts/)을 보고 바로 끝내며, '게시됨'·게시 링크가 있는 행은 고르지 않는다.
- `check.yml`(tick의 check, 예비 cron 09:50·23:25)은 슬롯이 지났는데 아직 '승인'인 글이 있으면 알리고(빨간불 → GitHub 메일 + 그 행 '[게시기]' 댓글), 아직 창 안이면 게시기를 한 번 더 깨운다. Threads API가 죽어 있어도(예: `API access blocked`) 여기서 빨간불이 난다. metrics·comments는 API 오류를 경고로만 남긴다(메일 폭탄 방지).

## 외부 cron 설정 (운영자, 한 번)

1. GitHub → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → Generate. Repository access: 이 저장소만. Permissions: **Contents: Read and write** (repository_dispatch용). 만료는 1년. 값을 복사한다(한 번만 보인다).
2. cron-job.org(무료) 가입 → Create cronjob:
   - URL: `https://api.github.com/repos/<owner>/<repo>/dispatches`
   - Schedule: every 15 minutes
   - Advanced → Request method **POST**, Headers: `Authorization: Bearer <토큰>`, `Accept: application/vnd.github+json`, Body: `{"event_type":"tick"}`
3. 저장 뒤 "Run now"를 눌러 Actions에 `tick` 실행이 생기는지 본다. 게시 창 밖이면 tick은 아무것도 깨우지 않고 끝난다(정상).
4. 토큰은 채팅·코드에 적지 않는다. 만료 전에 다시 발급해 cron-job.org에만 넣는다.

## 옛 길 (10/28~10/31, 이제 안 씀)

Claude 예약 작업 '스레드 게시기 깨우기' 4개와 '확인' 2개가 kick/slot.txt를 push해 깨웠다. 사용량 한도에 걸리면 같이 멈추는 약점이 있어(10/31 아침 실제로 실패) tick으로 바꿨다. 예약 작업은 운영자가 끈다.
