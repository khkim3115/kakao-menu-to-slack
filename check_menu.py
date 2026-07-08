#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
더 미라클푸드 카카오톡 채널의 '당일 메뉴' 프로필 이미지를 감지해 Slack으로 보낸다.

동작 원리:
  0) 주말·한국 공휴일이면 즉시 종료한다(식당이 휴일에 이미지를 바꿔도 전송 안 함).
  1) 공개 API(로그인 불필요)에서 현재 채널 프로필 이미지를 읽는다.
  2) 회사 로고(등록된 목록)면 무시한다(= 메뉴 아님).
  3) 직전에 보낸 이미지와 다른 새 이미지면 = 오늘의 메뉴 → Slack 전송 후 상태 저장.
     단, 오늘 이미 보냈으면 이미지가 또 바뀌어도 보내지 않는다(하루 1건).
  → 로고 필터가 휴무·메뉴 없는 날을 거르고, 0)의 달력 가드가 주말·공휴일을,
    하루 1건 캡이 재업로드(수정본) 중복을 막는다.

환경변수:
  SLACK_WEBHOOK_URL  (필수)  Slack Incoming Webhook URL
  CHANNEL_ID         (선택)  기본 "_xjxoPlG"
  TEST_MODE          (선택)  "1"이면 로고/중복/주말·공휴일 필터를 무시하고 현재 이미지를 강제 전송(배선 점검용)

선택 의존성:
  holidays (pip)  한국 공휴일(대체공휴일 포함) 판정용. 없으면 경고 후 주말 체크만 적용.
"""
import os
import re
import sys
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
WEEKDAYS_KO = ["월", "화", "수", "목", "금", "토", "일"]

CHANNEL_ID = os.environ.get("CHANNEL_ID", "_xjxoPlG")
API_URL = f"https://pf.kakao.com/rocket-web/web/v2/profiles/{CHANNEL_ID}"
HOME_URL = f"https://pf.kakao.com/{CHANNEL_ID}"

# 회사 로고 이미지 목록(= 메뉴 아님). 식당이 새 로고를 쓰기 시작하면 항목을 추가하세요.
#   확인 방법: python check_menu.py 를 평일 오후(메뉴 내려간 시간)에 실행해
#   [observe] 로그의 path/id 값을 그대로 복사.
KNOWN_LOGOS = [  # (path, id)
    ("r4cHt/dJMcagTBNko/4t7NCly6CZ9dNJ8tWWqCf1", 189733018),  # 기본 로고(흰 배경)
    ("qlXJg/dJMcadCt7VC/EbJ6hkYOJdKKOTiORMmnW1", 189957406),  # 노란 로고(어두운 배경, 2026-07-04 오발송 원인)
]

# 이 시각(KST) 이전에는 전송하지 않는다. 예: 9 이면 09:00 부터 전송.
SEND_AFTER_HOUR_KST = int(os.environ.get("SEND_AFTER_HOUR_KST", "9"))

STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "last_seen.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")


def rest_day_reason(now):
    """주말·한국 공휴일이면 스킵 사유 문자열, 영업일이면 None."""
    if now.weekday() >= 5:
        return f"주말({WEEKDAYS_KO[now.weekday()]})"
    try:
        import holidays
    except ImportError:
        print("[warn] holidays 패키지 없음 → 공휴일 체크 생략 (pip install holidays)",
              file=sys.stderr)
        return None
    name = holidays.country_holidays("KR").get(now.date())
    return f"공휴일({name})" if name else None


def to_https(url):
    return re.sub(r"^http://", "https://", url) if url else url


def http_get(url, accept="application/json"):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": HOME_URL,
        "Accept": accept,
        "Accept-Language": "ko-KR,ko;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "replace")


def get_profile_image():
    """현재 프로필 이미지 정보를 dict로 반환: {id, path, url}. 실패 시 예외."""
    # 1차: 공개 JSON API (권위 있는 id/path 제공)
    try:
        data = json.loads(http_get(API_URL))
        prof = data["cards"][0]["profile"]
        pi = prof["profile_image"]
        url = pi.get("xlarge_url") or pi.get("url") or pi.get("large_url")
        return {
            "id": pi.get("id") or prof.get("profile_image_id"),
            "path": pi.get("path"),
            "url": to_https(url),
            "avg": pi.get("avg"),
            "source": "json",
        }
    except Exception as e:
        print(f"[warn] JSON API 실패 → og:image fallback 시도: {e}", file=sys.stderr)

    # 2차: 페이지 HTML의 og:image (구조 변경 대비 이중화)
    html = http_get(HOME_URL, accept="text/html")
    m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', html, re.I)
    if not m:
        raise RuntimeError("프로필 이미지를 찾지 못함 (JSON / og:image 모두 실패)")
    url = to_https(m.group(1))
    pm = re.search(r"/dn/([^/]+/[^/]+/[^/]+)/img", url)  # .../dn/<path>/img_xl.jpg
    return {"id": None, "path": pm.group(1) if pm else None, "url": url, "avg": None, "source": "og"}


def img_key(img):
    """변경 감지/중복 방지용 단일 키. path가 두 경로(JSON/og) 모두에 존재하므로 우선 사용."""
    if img.get("path"):
        return f"path:{img['path']}"
    if img.get("id"):
        return f"id:{img['id']}"
    return f"url:{img.get('url')}"


def is_logo(img):
    return any(
        img.get("path") == path or (img.get("id") and img["id"] == logo_id)
        for path, logo_id in KNOWN_LOGOS
    )


def sent_today(state, now):
    """오늘(KST) 이미 전송했으면 True. last_sent_kst 형식: 'YYYY-MM-DD HH:MM:SS'."""
    return (state.get("last_sent_kst") or "")[:10] == now.strftime("%Y-%m-%d")


def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(img, sent_kst=None):
    """마지막으로 처리한 이미지를 기록. sent_kst 를 주면 전송 시각은 그 값을 유지
    (= 전송 없이 '본 것'만 기록하는 하루 1건 캡 경로용)."""
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    now = datetime.now(KST)
    state = {
        "last_key": img_key(img),
        "last_image_id": img.get("id"),
        "last_image_path": img.get("path"),
        "last_image_url": img.get("url"),
        "last_sent_kst": sent_kst or now.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"[state] 저장 완료: last_key={state['last_key']}")


def _post(webhook, payload):
    """Slack에 POST하고 (status_code, body) 반환. HTTPError도 잡아서 본문을 돌려준다(디버깅용)."""
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def post_slack(img, note=None):
    """Incoming Webhook으로 메뉴 전송. 관대한 형식부터 순서대로 시도(첫 성공에서 종료)."""
    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook:
        raise RuntimeError("SLACK_WEBHOOK_URL 환경변수가 설정되지 않았습니다")
    now = datetime.now(KST)
    title = f"🍱 오늘의 점심 메뉴 ({now.month}/{now.day}, {WEEKDAYS_KO[now.weekday()]})"
    if note:
        title += f" — {note}"

    # 1순위: Block Kit(image 블록) — 레거시 attachments.image_url 은 최신 Slack에서
    # 렌더가 안 됐다(2026-07 확인). image 블록은 현행 1급 요소라 원격 이미지를 정상 표시한다.
    blocks_payload = {
        "text": title,  # 알림/폴백용 텍스트
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn",
             "text": f"{title}\n<{HOME_URL}|더 미라클푸드 채널>"}},
            {"type": "image", "image_url": img["url"], "alt_text": "오늘의 점심 메뉴"},
        ],
    }
    # 2순위: 순수 text + 링크 자동 펼치기(unfurl) — 가장 단순해 사실상 항상 성공.
    # image 블록이 어떤 이유로 렌더 안 될 때를 대비한 폴백(직접 .jpg 링크는 미디어로 펼쳐짐).
    text_payload = {
        "text": f"{title}\n{img['url']}",
        "unfurl_links": True,
        "unfurl_media": True,
    }

    last = None
    for label, payload in (("blocks", blocks_payload), ("text+unfurl", text_payload)):
        code, body = _post(webhook, payload)
        if 200 <= code < 300:
            print(f"[slack] 전송 완료 ({label})")
            return
        last = (label, code, body)
        print(f"[slack][warn] {label} 실패 code={code} body={body!r} → 다음 방식 시도", file=sys.stderr)
    raise RuntimeError(f"Slack 전송 실패(모든 방식): {last}")


def alert_slack(msg):
    """오류를 1회만 가볍게 알림(실패해도 무시)."""
    try:
        webhook = os.environ.get("SLACK_WEBHOOK_URL")
        if webhook:
            _post(webhook, {"text": f":warning: 메뉴봇 오류: {msg}"})
    except Exception as e:
        print(f"[warn] 경고 전송 실패: {e}", file=sys.stderr)


def main():
    test_mode = os.environ.get("TEST_MODE") == "1" or "--test" in sys.argv
    now = datetime.now(KST)

    # 주말·공휴일 가드. 로고 필터만으로는 식당이 휴일에 프로필을 바꾸면 전송돼 버린다
    # (2026-07-04 토요일 실제 발생). TEST_MODE 는 배선 점검용이므로 가드를 통과시킨다.
    if not test_mode:
        reason = rest_day_reason(now)
        if reason:
            print(f"[skip] {reason} → 전송 안 함")
            return

    try:
        img = get_profile_image()
    except Exception as e:
        print(f"[error] 프로필 이미지 조회 실패: {e}", file=sys.stderr)
        alert_slack(str(e))
        sys.exit(1)

    print(f"[observe] {now:%Y-%m-%d %H:%M:%S} KST "
          f"source={img.get('source')} id={img.get('id')} path={img.get('path')} "
          f"logo={is_logo(img)} url={img.get('url')}")

    if test_mode:
        print("[test] TEST_MODE — 로고/중복 필터를 무시하고 강제 전송")
        post_slack(img, note="테스트")
        return

    if is_logo(img):
        print("[skip] 현재 프로필은 회사 로고 → 메뉴 아님. 전송 안 함")
        return

    state = load_state()
    if img_key(img) == state.get("last_key"):
        print("[skip] 직전에 보낸 이미지와 동일 → 중복 전송 안 함")
        return

    # 하루 1건 캡: 같은 날 이미지가 또 바뀌어도(오타 수정 재업로드 등) 다시 보내지 않는다
    # (2026-07-06 09:05/10:35 중복 발송 실제 발생). 억제한 이미지도 last_key 로
    # 기록해 둔다(전송 시각은 보존) — 안 그러면 이 이미지가 다음 영업일 아침까지
    # 프로필에 남아 있을 때 어제 메뉴를 오늘 날짜로 오발송하게 된다.
    if sent_today(state, now):
        print(f"[skip] 오늘 이미 전송함({state.get('last_sent_kst')}) → 하루 1건 캡"
              f" (본 이미지로만 기록)")
        save_state(img, sent_kst=state.get("last_sent_kst"))
        return

    # 9시(KST) 이전이면 새 메뉴라도 전송 보류. 상태를 저장하지 않으므로
    # 9시 이후 첫 폴링이 동일 이미지를 '새 메뉴'로 보고 전송한다.
    if now.hour < SEND_AFTER_HOUR_KST:
        print(f"[hold] 새 메뉴 감지했으나 아직 KST {now.hour}시 → "
              f"{SEND_AFTER_HOUR_KST}시 이후에 전송(상태 저장 안 함)")
        return

    print("[send] 새 메뉴 감지 → Slack 전송")
    post_slack(img)
    save_state(img)


if __name__ == "__main__":
    main()
