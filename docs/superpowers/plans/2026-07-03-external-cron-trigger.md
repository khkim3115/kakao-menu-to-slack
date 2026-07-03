# 외부 크론 이중 트리거 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** cron-job.org 외부 크론을 주 트리거로 추가해, GitHub cron 미발화 시에도 수동 개입 없이 메뉴 알림이 도착하게 한다.

**Architecture:** cron-job.org가 KST 09:05~12:50에 15분 간격으로 GitHub `workflow_dispatch` API를 호출하고, 기존 GitHub `schedule`은 백업으로 유지한다. 중복 전송은 `check_menu.py`의 `last_key` 비교와 워크플로 `concurrency` 그룹이 막는다. 리포 변경은 README 문서화뿐이다.

**Tech Stack:** GitHub Actions (`workflow_dispatch`), GitHub fine-grained PAT, cron-job.org, `gh` CLI (검증용)

## Global Constraints

- 코드(`check_menu.py`)·워크플로(`menu.yml`) 변경 금지 — 스펙상 문서만 변경
- PAT는 리포에 절대 커밋하지 않는다 (저장 위치는 cron-job.org 잡 설정뿐)
- 대상 리포: `khkim3115/kakao-menu-to-slack`, dispatch ref: `main`
- 문서는 기존 README와 같은 한국어·말투(평서형)·굵은 글씨 관례를 따른다
- 스펙: `docs/superpowers/specs/2026-07-03-external-cron-trigger-design.md`

---

### Task 1: README 트리거 이중화 문서화

**Files:**
- Modify: `README.md` (4곳: 7행 요약, 71~81행 cron 튜닝 blockquote, 새 섹션 추가, 파일 구조)

**Interfaces:**
- Consumes: 없음
- Produces: README의 "트리거 이중화" 섹션 — Task 2에서 사용자에게 안내할 설정값·절차의 원본

- [ ] **Step 1: 요약 불릿 갱신 (7행)**

Edit — old:
```markdown
- 별도 서버 불필요 — **GitHub Actions(무료 cron)** 로 동작
```
new:
```markdown
- 별도 서버 불필요 — **GitHub Actions** 로 동작 (트리거는 외부 크론 + GitHub cron **이중화**)
```

- [ ] **Step 2: cron 튜닝 섹션의 blockquote 교체 (79~81행)**

Edit — old:
```markdown
> GitHub 무료 cron 은 지연·드롭이 심해(관측상 시간 단위로 빠지기도 함) 폴링 창을 일부러 넓게 잡았다.
> 그래도 스케줄이 아예 안 뜨는 날엔 수동으로 1회 실행하면 된다:
> `gh workflow run menu.yml --ref main` (중복 방지 로직이 있어 이중 전송 걱정 없음).
```
new:
```markdown
> GitHub 무료 cron 은 지연·드롭이 심해(관측상 시간 단위로 빠지기도 함) 폴링 창을 일부러 넓게 잡았고,
> 그것도 모자라 아예 안 뜨는 날이 있어 **외부 크론을 주 트리거로 이중화**했다(아래 "트리거 이중화" 참고).
> 그래도 안 올 땐 수동 1회 실행: `gh workflow run menu.yml --ref main` (중복 방지 로직이 있어 이중 전송 걱정 없음).
```

- [ ] **Step 3: "트리거 이중화" 섹션 추가**

"시간대(cron) 튜닝" 섹션과 "로고가 바뀌었을 때" 섹션 사이(`---` 구분선 뒤)에 아래 내용을 그대로 삽입:

````markdown
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

주말·휴무일에도 핑은 나가지만 로고 필터가 전송을 걸러준다(기존 GitHub cron 과 동일한 동작).

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
3. **스크립트 로그** — `[observe]`(현재 이미지) / `[skip]`(로고·중복) / `[hold]`(9시 이전) / `[send]` 중 무엇이 찍혔나?

---
````

(기존 "로고가 바뀌었을 때" 앞의 `---` 는 위 삽입 블록 끝의 `---` 로 대체되는 셈이므로 중복되지 않게 유지 확인.)

- [ ] **Step 4: 파일 구조에 docs/ 추가**

Edit — old:
```markdown
├─ state/last_seen.json         # 마지막으로 보낸 이미지 (자동 커밋, 변경 이력 = 메뉴 기록)
├─ .gitignore
```
new:
```markdown
├─ state/last_seen.json         # 마지막으로 보낸 이미지 (자동 커밋, 변경 이력 = 메뉴 기록)
├─ docs/superpowers/            # 설계 스펙 / 구현 계획 문서
├─ .gitignore
```

- [ ] **Step 5: 렌더링 검증**

Run: `git diff README.md` 로 4개 편집이 모두 들어갔는지, 마크다운 표·코드펜스가 깨지지 않았는지 확인.
Expected: 요약 1곳 + blockquote 1곳 + 새 섹션 1개 + 파일 구조 1곳. 중첩 코드펜스(````` ```` `````) 짝 맞음.

- [ ] **Step 6: Commit**

```bash
git add README.md
git commit -m "docs: 트리거 이중화(외부 크론 + GitHub cron) 섹션 추가"
```

---

### Task 2: dispatch 엔드포인트 사전 검증 + 사용자 수동 셋업

**Files:** 없음 (리포 변경 없음)

**Interfaces:**
- Consumes: Task 1의 README "트리거 이중화" 섹션 (사용자 안내 원본)
- Produces: 동작하는 cron-job.org 잡 + PAT (Task 3 검증의 전제)

- [ ] **Step 1: 엔드포인트 사전 검증 (gh CLI, 사용자 계정 권한)**

cron-job.org 설정 전에 URL·body가 맞는지 먼저 확인한다. 이 호출은 실제로 워크플로를 1회 실행시키지만 중복 방지 로직 때문에 부작용이 없다.

Run:
```bash
gh api -X POST repos/khkim3115/kakao-menu-to-slack/actions/workflows/menu.yml/dispatches -f ref=main
```
Expected: 출력 없음(HTTP 204). 404/422 가 나오면 URL·ref 오타이므로 README 값을 수정 후 재시도.

Run(확인):
```bash
gh run list --workflow=menu.yml --limit 3
```
Expected: 방금 생긴 `workflow_dispatch` 이벤트 run 이 목록 최상단에 표시.

- [ ] **Step 2: 사용자에게 수동 셋업 안내 후 대기**

README "트리거 이중화" 섹션의 절차를 요약해 사용자에게 전달:
1. PAT 발급 (fine-grained, `kakao-menu-to-slack` 단독, Actions Read/write, 만료 1년)
2. cron-job.org 가입 → 잡 생성 (설정값 표 그대로) → 실패 알림 켜기
3. 잡 저장 후 **"Test run"(즉시 실행)** 1회 실행

사용자가 "완료" 를 알릴 때까지 Task 3 진행 금지. (사용자가 원하면 Chrome 연동으로 화면을 같이 보며 설정 보조.)

---

### Task 3: 종단 검증

**Files:** 없음 (리포 변경 없음)

**Interfaces:**
- Consumes: Task 2의 cron-job.org 테스트 실행
- Produces: 이중 트리거 동작 확인 완료 판정

- [ ] **Step 1: cron-job.org 발 dispatch 가 Actions run 을 만들었는지 확인**

Run:
```bash
gh run list --workflow=menu.yml --limit 5 --json displayTitle,event,status,conclusion,createdAt
```
Expected: 사용자의 Test run 시각과 일치하는 `"event":"workflow_dispatch"` run 존재, `conclusion: success`.

- [ ] **Step 2: 해당 run 로그에서 스크립트 판정 확인**

Run:
```bash
gh run view <run-id> --log | grep -E "\[(observe|skip|hold|send)\]"
```
Expected: `[observe]` 1줄 + (`[skip] 직전에 보낸 이미지와 동일` 또는 `[skip] …로고` 또는 `[send]`) 중 하나. 오류 없이 판정 로그가 찍히면 정상.

- [ ] **Step 3: 다음 영업일 확인 항목을 사용자에게 전달**

- 다음 영업일 아침, 수동 개입 없이 Slack 도착하는지
- cron-job.org 대시보드에 09:05 이후 실행 이력이 쌓이는지
- 안 오면 README "메시지가 안 올 때 진단 순서" 대로 확인

---

## 완료 후

`superpowers:finishing-a-development-branch` 로 브랜치 정리 — `claude/elastic-boyd-99b00c` → `main` PR 생성·머지 (dispatch 는 `main` 기준으로 동작하지만 문서도 `main` 에 있어야 실사용 가능).
