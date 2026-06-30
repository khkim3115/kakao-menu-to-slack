#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
더 미라클푸드 카카오톡 채널의 '당일 메뉴' 프로필 이미지를 감지해 Slack으로 보낸다.

동작 원리:
  1) 공개 API(로그인 불필요)에서 현재 채널 프로필 이미지를 읽는다.
  2) 회사 로고면 무시한다(= 메뉴 아님).
  3) 직전에 보낸 이미지와 다른 새 이미지면 = 오늘의 메뉴 → Slack 전송 후 상태 저장.
  → 이 한 가지 로직으로 주말·휴무·메뉴 없는 날(로고 그대로)은 자동으로 아무것도 보내지 않는다.

환경변수:
  SLACK_WEBHOOK_URL  (필수)  Slack Incoming Webhook URL
  CHANNEL_ID         (선택)  기본 "_xjxoPlG"
  TEST_MODE          (선택)  "1"이면 로고/중복 필터를 무시하고 현재 이미지를 강제 전송(배선 점검용)
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

# 현재 회사 로고(= 메뉴 아님). 식당이 로고 이미지를 교체하면 아래 값을 갱신하세요.
#   확인 방법: python check_menu.py 를 평일 오후(메뉴 내려간 시간)에 실행해
#   [observe] 로그의 path/id 값을 그대로 복사.
KNOWN_LOGO_PATH = "r4cHt/dJMcagTBNko/4t7NCly6CZ9dNJ8tWWqCf1"
KNOWN_LOGO_ID = 189733018

STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "last_seen.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")


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
    return {"id": None, "path": pm.group(1) if pm else None, "url": url, "source": "og"}


def img_key(img):
    """변경 감지/중복 방지용 단일 키. path가 두 경로(JSON/og) 모두에 존재하므로 우선 사용."""
    if img.get("path"):
        return f"path:{img['path']}"
    if img.get("id"):
        return f"id:{img['id']}"
    return f"url:{img.get('url')}"


def is_logo(img):
    return img.get("path") == KNOWN_LOGO_PATH or (img.get("id") and img["id"] == KNOWN_LOGO_ID)


def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(img):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    now = datetime.now(KST)
    state = {
        "last_key": img_key(img),
        "last_image_id": img.get("id"),
        "last_image_path": img.get("path"),
        "last_image_url": img.get("url"),
        "last_sent_kst": now.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"[state] 저장 완료: last_key={state['last_key']}")


def _post(webhook, payload):
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "replace")


def post_slack(img, note=None):
    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook:
        raise RuntimeError("SLACK_WEBHOOK_URL 환경변수가 설정되지 않았습니다")
    now = datetime.now(KST)
    title = f"🍱 오늘의 점심 메뉴 ({now.month}/{now.day}, {WEEKDAYS_KO[now.weekday()]})"
    if note:
        title += f" — {note}"
    payload = {
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": title, "emoji": True}},
            {"type": "image", "image_url": img["url"], "alt_text": "오늘의 메뉴"},
            {"type": "context", "elements": [
                {"type": "mrkdwn", "text": f"<{HOME_URL}|더 미라클푸드 채널> · {now.strftime('%H:%M')} KST"},
            ]},
        ]
    }
    print(f"[slack] 전송 응답: {_post(webhook, payload)}")


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

    print("[send] 새 메뉴 감지 → Slack 전송")
    post_slack(img)
    save_state(img)


if __name__ == "__main__":
    main()
