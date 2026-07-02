# kakao-menu-to-slack

1층 구내식당 **더 미라클푸드** 카카오톡 채널([`pf.kakao.com/_xjxoPlG`](https://pf.kakao.com/_xjxoPlG))의
**프로필 사진(대표 이미지)** 은 매일 오전에 **당일 메뉴 이미지**로 바뀐다.
이 저장소는 그 이미지를 자동으로 감지해 **매일 아침 Slack** 으로 보내준다.

- 별도 서버 불필요 — **GitHub Actions(무료 cron)** 로 동작
- 카카오 로그인/계정 불필요 — **공개 API**로 프로필 이미지를 읽음
- 의존성 없음 — **Python 표준 라이브러리만** 사용

---

## 동작 원리

```
GitHub Actions (KST 오전 시간대, 10분 간격)
   ① 공개 JSON API 호출 → 현재 프로필 이미지(id/path/url) 추출
      https://pf.kakao.com/rocket-web/web/v2/profiles/_xjxoPlG
   ② 회사 로고면 무시(= 메뉴 아님)
   ③ 직전에 보낸 이미지와 다른 새 이미지면 = 오늘의 메뉴 → Slack 전송
   ④ 마지막 전송 이미지를 state/last_seen.json 에 커밋(다음 실행이 기억)
```

식당은 오전엔 메뉴, 이후엔 로고로 되돌리는 패턴이라 **단발 실행이 아니라 폴링**한다.
**로고 필터 + 변경 감지** 덕분에 주말·휴무·메뉴 없는 날(로고 그대로)은 **자동으로 아무것도 보내지 않는다.**

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
`.github/workflows/menu.yml` 의 `schedule` 은 **KST 07:04~12:54** 를 10분 간격으로 폴링한다
(분을 4분 오프셋해 정각 혼잡을 피한다).
전송은 `check_menu.py` 의 `SEND_AFTER_HOUR_KST`(기본 9) 가드로 **09시 이전에는 하지 않는다**
(9시 이전에 메뉴가 감지돼도 상태를 저장하지 않아, 9시 이후 첫 폴링이 전송).
- 전송 시각을 바꾸려면: `SEND_AFTER_HOUR_KST`(필요시 cron 창도) 를 조정.
- **Actions 로그의 `[observe]`** 줄로 메뉴가 실제로 올라오는 시각을 확인해 창/간격을 조정하면 된다.

> GitHub 무료 cron 은 지연·드롭이 심해(관측상 시간 단위로 빠지기도 함) 폴링 창을 일부러 넓게 잡았다.
> 그래도 스케줄이 아예 안 뜨는 날엔 수동으로 1회 실행하면 된다:
> `gh workflow run menu.yml --ref main` (중복 방지 로직이 있어 이중 전송 걱정 없음).

---

## 로고가 바뀌었을 때
식당이 회사 로고 이미지를 새로 교체하면 메뉴로 오인해 1회 전송될 수 있다.
평일 오후(메뉴 내려간 시간)에 `python check_menu.py` 를 실행해 `[observe]` 의 `path`/`id` 를 확인하고,
`check_menu.py` 상단 상수를 갱신:
```python
KNOWN_LOGO_PATH = "<새 path>"
KNOWN_LOGO_ID = <새 id>
```

---

## 파일 구조
```
kakao-menu-to-slack/
├─ .github/workflows/menu.yml   # cron 스케줄 + 스크립트 실행 + state 자동 커밋
├─ check_menu.py                # 가져오기 → 로고/중복 필터 → Slack 전송
├─ state/last_seen.json         # 마지막으로 보낸 이미지 (자동 커밋, 변경 이력 = 메뉴 기록)
├─ .gitignore
└─ README.md
```

## 한계 / 비고
- 메뉴가 **공개 프로필 사진**으로 올라오는 경우에만 동작한다(현재 그렇게 운영 중으로 확인됨).
  만약 어느 날부터 비공개 카톡 메시지로만 발송된다면 공개 API로는 가져올 수 없다.
- Slack 전송은 `attachments`(image_url) 형식을 우선 사용하고, 실패 시 순수 `text`+링크 펼치기로
  자동 폴백한다(에러 본문은 Actions 로그에 기록). 공개 `https` 이미지 URL을 쓴다(kakaocdn https 확인됨).
  - 참고: Block Kit `image` 블록은 이 Webhook에서 400(invalid_blocks)이 나서 사용하지 않는다.
  - 더 확실한 표시가 필요하면 봇 토큰 + 파일 업로드(`files_upload_v2`)로 전환 가능.
