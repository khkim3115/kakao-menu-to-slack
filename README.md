# kakao-menu-to-slack

1층 구내식당 **더 미라클푸드** 카카오톡 채널([`pf.kakao.com/_xjxoPlG`](https://pf.kakao.com/_xjxoPlG))의
**프로필 사진(대표 이미지)** 은 매일 오전에 **당일 메뉴 이미지**로 바뀐다.
이 저장소는 그 이미지를 자동으로 감지해 **매일 아침 Slack** 으로 보내준다.

- 별도 서버 불필요 — **GitHub Actions** 로 동작 (트리거는 외부 크론 + GitHub cron **이중화**)
- 카카오 로그인/계정 불필요 — **공개 API**로 프로필 이미지를 읽음
- 의존성은 [`holidays`](https://pypi.org/project/holidays/) 하나 — 한국 공휴일(대체공휴일 포함) 판정용.
  없으면 경고만 내고 주말 체크만 적용된다(나머지는 표준 라이브러리).

---

## 동작 원리

```
GitHub Actions (KST 평일 오전 시간대, 10분 간격)
   ⓪ 주말 또는 한국 공휴일(대체공휴일 포함)이면 즉시 종료
   ① 공개 JSON API 호출 → 현재 프로필 이미지(id/path/url) 추출
      https://pf.kakao.com/rocket-web/web/v2/profiles/_xjxoPlG
   ② 회사 로고(등록 목록)면 무시(= 메뉴 아님)
   ③ 직전에 보낸 이미지와 다른 새 이미지면 = 오늘의 메뉴 → Slack 전송
      (단, 오늘 이미 보냈으면 하루 1건 캡으로 재전송 안 함)
   ④ 마지막 전송 이미지를 state/last_seen.json 에 커밋(다음 실행이 기억)
```

식당은 오전엔 메뉴, 이후엔 로고로 되돌리는 패턴이라 **단발 실행이 아니라 폴링**한다.
**로고 필터 + 변경 감지**가 휴무·메뉴 없는 날(로고 그대로)을 거르고,
**주말·공휴일 달력 가드(⓪)** 가 휴일에 이미지가 바뀌어도 전송되지 않게 확실히 막고
(2026-07-04 토요일: 새 로고 변형으로 교체 → 필터 통과 → 오발송 사례),
**하루 1건 캡(③)** 이 같은 날 재업로드(오타 수정본 등)로 인한 중복 전송을 막는다
(2026-07-06: 09:05 메뉴 발송 후 10:35 오타 수정 재업로드 → 중복 발송 사례).
트레이드오프: 하루 1건 캡 때문에 식당이 낮에 올리는 "정정판 메뉴"는 오지 않는다
(억제된 이미지도 '본 것'으로 기록해 다음 날 스테일 발송을 막는다).
알려진 잔여 리스크: 등록 안 된 새 로고 변형 등이 아침에 **먼저** 오발송되면 그날 캡이
소진돼 진짜 메뉴가 억제된다. 이때 Actions 로그에 `[skip] ... 하루 1건 캡` 이 남고,
복구는 프로필에 메뉴가 떠 있는 동안 **Run workflow → test=true** 수동 실행(강제 전송).

---

## 설치 / 설정 (약 5분)

### 1. Slack Incoming Webhook 발급
1. <https://api.slack.com/apps> → **Create New App** → *From scratch* → 워크스페이스 선택
2. 좌측 **Incoming Webhooks** → **Activate Incoming Webhooks** 켜기
3. **Add New Webhook to Workspace** → 메뉴를 받을 **채널 선택** → 생성된
   `https://hooks.slack.com/services/...` URL 복사

### 2. 이 저장소를 본인 GitHub 계정에 올리기
```bash
cd kakao-menu-to-slack
git init
git add .
git commit -m "init: kakao menu to slack"
gh repo create kakao-menu-to-slack --public --source=. --push
# (gh 없이) GitHub에서 빈 레포 생성 후: git remote add origin <URL> && git push -u origin main
```
> **public 권장:** Actions 분(分) 무제한 + 스케줄 안정적. private 도 무료 2,000분/월 내에서 충분.

### 3. Webhook URL 을 GitHub Secret 으로 등록
레포 → **Settings → Secrets and variables → Actions → New repository secret**
- Name: `SLACK_WEBHOOK_URL`
- Value: 1번에서 복사한 Webhook URL

### 4. 배선 테스트 (즉시)
레포 → **Actions → kakao-menu-to-slack → Run workflow** → **test = `true`** 로 실행.
현재 프로필(로고라도)이 Slack 에 도착하면 가져오기·전송 경로 정상.
이후 자동 스케줄은 매 평일 오전에 **메뉴가 바뀌는 순간 1건**만 보낸다.

---

## 로컬에서 테스트
```bash
# Slack 까지 실제 전송 (로고/중복 무시)
SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..." python check_menu.py --test

# 전송 없이 현재 프로필 상태만 관찰 (id/path/logo 여부 확인)
python check_menu.py        # SLACK_WEBHOOK_URL 없이 실행하면 로고일 땐 그냥 skip 로그만 출력
```

---

## 시간대(cron) 튜닝
`.github/workflows/menu.yml` 의 `schedule` 은 **KST 평일(월~금) 07:04~12:54** 를 10분 간격으로 폴링한다
(분을 4분 오프셋해 정각 혼잡을 피한다). 공휴일은 cron 으로 거를 수 없으므로
`check_menu.py` 의 주말·공휴일 가드가 스크립트 단에서 걸러낸다.
전송은 `check_menu.py` 의 `SEND_AFTER_HOUR_KST`(기본 9) 가드로 **09시 이전에는 하지 않는다**
(9시 이전에 메뉴가 감지돼도 상태를 저장하지 않아, 9시 이후 첫 폴링이 전송).
- 전송 시각을 바꾸려면: `SEND_AFTER_HOUR_KST`(필요시 cron 창도) 를 조정.
- **Actions 로그의 `[observe]`** 줄로 메뉴가 실제로 올라오는 시각을 확인해 창/간격을 조정하면 된다.

> GitHub 무료 cron 은 지연·드롭이 심해(관측상 시간 단위로 빠지기도 함) 폴링 창을 일부러 넓게 잡았고,
> 그것도 모자라 아예 안 뜨는 날이 있어 **외부 크론을 주 트리거로 이중화**했다(아래 "트리거 이중화" 참고).
> 그래도 안 올 땐 수동 1회 실행: `gh workflow run menu.yml --ref main` (중복 방지 로직이 있어 이중 전송 걱정 없음).

---

## 트리거 이중화 (외부 크론 + GitHub cron)

GitHub 무료 cron 은 지연을 넘어 **아예 발화하지 않는 날**이 있다(관측됨). 그래서
외부 크론 서비스 **cron-job.org** 를 주 트리거로 두고, GitHub `schedule` 은 백업으로 유지한다.
둘 다 같은 워크플로를 깨우며, 중복 방지(`last_key` 비교 + `concurrency`) 덕분에 메시지는 하루 1건만 간다.

```
cron-job.org ──(15분 간격, KST 09:05~12:50)──> workflow_dispatch API ─┐
                                                                      ├─> menu.yml → check_menu.py
GitHub schedule ──(KST 07:04~12:54, 10분 간격)────────────────────────┘
```

### cron-job.org 잡 설정값

| 항목 | 값 |
|---|---|
| URL | `https://api.github.com/repos/khkim3115/kakao-menu-to-slack/actions/workflows/menu.yml/dispatches` |
| Method | `POST` |
| Body | `{"ref":"main"}` |
| Header 1 | `Authorization: Bearer <PAT>` |
| Header 2 | `Accept: application/vnd.github+json` |
| 스케줄 | 타임존 `Asia/Seoul`, 매일 시: 9~12 / 분: 5,20,35,50 (= 09:05~12:50, 16회/일) |
| 알림 | 실행 실패 시 이메일 알림 켜기 |

주말·공휴일에도 핑은 나가지만 스크립트의 주말·공휴일 가드가 즉시 종료시킨다
(cron-job.org 잡을 월~금으로 좁혀도 되지만, 공휴일은 어차피 스크립트가 걸러야 하므로 필수는 아니다).

### PAT 발급 (최초 1회, 만료 시 재발급)

1. GitHub → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**
2. Repository access: **Only select repositories** → `kakao-menu-to-slack`
3. Permissions → Repository permissions → **Actions: Read and write** (그 외 권한 추가 금지)
4. Expiration: 최대(1년) 설정 → 토큰 복사 → cron-job.org 잡의 `Authorization` 헤더에 `Bearer <토큰>` 으로 저장
5. **토큰을 리포에 커밋하지 말 것.**

만료되면 cron-job.org 실행이 401 로 실패하고 알림 메일이 온다 → 위 1~4 재수행 후 헤더 값만 교체.

### 메시지가 안 올 때 진단 순서

1. **cron-job.org 실행 이력** — 잡이 돌았나? 401(PAT 만료) / 404(URL 오타) 인가?
2. **GitHub Actions 실행 이력** — `gh run list --workflow=menu.yml` 에 오늘 run 이 있나?
3. **스크립트 로그** — `[skip]`(주말·공휴일·로고·중복) / `[observe]`(현재 이미지) / `[hold]`(9시 이전) / `[send]` 중 무엇이 찍혔나?

---

## 로고가 바뀌었을 때
식당이 등록되지 않은 새 로고 이미지를 쓰면 메뉴로 오인해 1회 전송될 수 있다
(실제 사례: 2026-07-04 노란 로고 변형). 평일 오후(메뉴 내려간 시간)에
`python check_menu.py` 를 실행해 `[observe]` 의 `path`/`id` 를 확인하고,
`check_menu.py` 상단 목록에 **추가**(기존 항목은 유지 — 로고를 번갈아 쓸 수 있다):
```python
KNOWN_LOGOS = [
    ("r4cHt/dJMcagTBNko/4t7NCly6CZ9dNJ8tWWqCf1", 189733018),  # 기본 로고(흰 배경)
    ("qlXJg/dJMcadCt7VC/EbJ6hkYOJdKKOTiORMmnW1", 189957406),  # 노란 로고(어두운 배경)
    ("<새 path>", <새 id>),
]
```

---

## 파일 구조
```
kakao-menu-to-slack/
├─ .github/workflows/menu.yml   # cron 스케줄 + 스크립트 실행 + state 자동 커밋
├─ check_menu.py                # 가져오기 → 로고/중복 필터 → Slack 전송
├─ state/last_seen.json         # 마지막으로 보낸 이미지 (자동 커밋, 변경 이력 = 메뉴 기록)
├─ docs/superpowers/            # 설계 스펙 / 구현 계획 문서
├─ .gitignore
└─ README.md
```

## 한계 / 비고
- 공휴일 판정은 `holidays` 패키지 데이터 기준이다(설·추석 연휴, 대체공휴일, 선거일 포함).
  워크플로가 매 실행 최신 버전을 설치하므로 새로 지정되는 임시공휴일도 패키지 업데이트를 통해 반영된다.
  공휴일이 아닌 식당 자체 휴무일은 기존처럼 로고 필터가 처리한다.
- 메뉴가 **공개 프로필 사진**으로 올라오는 경우에만 동작한다(현재 그렇게 운영 중으로 확인됨).
  만약 어느 날부터 비공개 카톡 메시지로만 발송된다면 공개 API로는 가져올 수 없다.
- Slack 전송은 `attachments`(image_url) 형식을 우선 사용하고, 실패 시 순수 `text`+링크 펼치기로
  자동 폴백한다(에러 본문은 Actions 로그에 기록). 공개 `https` 이미지 URL을 쓴다(kakaocdn https 확인됨).
  - 참고: Block Kit `image` 블록은 이 Webhook에서 400(invalid_blocks)이 나서 사용하지 않는다.
  - 더 확실한 표시가 필요하면 봇 토큰 + 파일 업로드(`files_upload_v2`)로 전환 가능.
