# 설계: 외부 크론(주) + GitHub cron(백업) 이중 트리거

날짜: 2026-07-03
상태: 승인됨

## 문제

메뉴 알림의 유일한 트리거가 GitHub Actions `schedule`인데, GitHub 무료 플랜 cron은
지연·미발화가 잦다. 트리거가 안 뜨면 그날 메시지가 오지 않고, 현재 유일한 복구
수단은 수동 `gh workflow run`이다. 스크립트 로직(감지·중복 방지·9시 가드)은
견고하므로, 문제는 순수하게 "누가 확실하게 깨워주느냐"이다.

## 결정

외부 무료 크론 서비스(cron-job.org)를 **주 트리거**로 추가하고, 기존 GitHub
schedule은 **백업**으로 유지한다. 두 트리거 모두 같은 워크플로(`menu.yml`)를
깨운다.

검토한 대안:

- 외부 크론 1회성 핑(09:05 하루 한 번): 그 한 번이 일시 장애로 실패하면 다시
  GitHub cron 운에 맡기게 됨. 기각.
- GitHub schedule 제거 후 외부 크론 단독: 공짜 백업을 버리는 셈. 실익 없음. 기각.
- 기타 방향(Supabase/Vercel 스케줄, Claude routine, 로컬 작업 스케줄러):
  변경 폭이 크거나 비용·PC 상시 가동 의존. 기각.

## 아키텍처

```
cron-job.org ──(15분 간격, KST 09:05~12:50)──> GitHub API workflow_dispatch ─┐
                                                                             ├─> menu.yml → check_menu.py
GitHub schedule ──(기존 그대로, KST 07:04~12:54)─────────────────────────────┘
```

- 중복 전송 방지: `check_menu.py`의 `last_key` 비교(멱등) + 워크플로
  `concurrency: kakao-menu` 그룹(동시 실행 방지).
- 두 트리거가 동시에 죽어야만 그날 알림이 실패한다.

## 외부 크론 설정값 (cron-job.org)

| 항목 | 값 |
|---|---|
| Method / URL | `POST https://api.github.com/repos/khkim3115/kakao-menu-to-slack/actions/workflows/menu.yml/dispatches` |
| Body | `{"ref":"main"}` |
| Header 1 | `Authorization: Bearer <PAT>` |
| Header 2 | `Accept: application/vnd.github+json` |
| 스케줄 | 타임존 Asia/Seoul, 매일 09:05~12:50, 15분 간격(16회/일) |
| 실패 알림 | 활성화 (PAT 만료 401 등을 이메일로 감지) |

주말·휴무일에도 핑은 나가지만 스크립트의 로고 필터가 전송을 걸러준다 — 현재
GitHub cron이 매일 도는 것과 동일한 동작.

## PAT (GitHub fine-grained personal access token)

- 스코프: `kakao-menu-to-slack` 리포 단독
- 권한: Actions — Read and write (+ 자동 포함되는 Metadata — Read). 그 외 없음.
- 만료: 최대치(1년)로 설정. 갱신 절차를 README에 문서화.
- 보관: cron-job.org 잡 설정의 헤더에만 저장. 리포에 커밋 금지.
- 만료 시 증상: cron-job.org 실행이 401로 실패 → 실패 알림 이메일 수신 →
  PAT 재발급 후 헤더 값만 교체.

## 리포 변경 사항

코드·워크플로 변경 없음 (`workflow_dispatch`가 이미 존재). 문서만 변경:

- README에 "트리거 이중화" 섹션 추가:
  - 왜 이중 트리거인지 (GitHub cron 미발화 이력)
  - cron-job.org 잡 설정값 (위 표 그대로, 복붙 가능하게)
  - PAT 발급·갱신 단계별 절차
  - 장애 시 진단 순서: cron-job.org 실행 이력 → GitHub Actions 실행 이력 →
    스크립트 로그(`[observe]`/`[skip]`/`[hold]`/`[send]`)

## 역할 분담

- Claude: 문서 작성·커밋, 설정값 정리, 셋업 완료 후 `gh api`로 dispatch
  엔드포인트 검증.
- 사용자(수동 1회): GitHub에서 PAT 발급, cron-job.org 가입 + 잡 등록.

## 검증

1. 설정 직후: cron-job.org "test run" → GitHub Actions에 `workflow_dispatch`
   실행이 생기는지 확인.
2. 해당 실행 로그에서 `[skip] 직전에 보낸 이미지와 동일`(이미 전송된 날) 또는
   정상 전송 확인.
3. 다음 영업일 아침: 수동 개입 없이 Slack 도착 확인.

## 실패 모드와 대응

| 실패 모드 | 감지 | 대응 |
|---|---|---|
| GitHub cron 미발화 | (자동 커버) | 외부 크론이 어차피 깨움 |
| cron-job.org 장애/잡 비활성 | 실패 알림 이메일, 메시지 미도착 | GitHub cron이 백업; 필요시 `gh workflow run` |
| PAT 만료 | cron-job.org 401 실패 알림 | PAT 재발급, 헤더 교체 (README 절차) |
| 카카오 API 일시 오류 | 스크립트가 Slack에 경고 전송 후 exit 1 | 다음 핑(15분 후)이 자연 재시도 |
