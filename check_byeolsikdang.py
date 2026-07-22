#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
별식당(Instagram @byeolsikdang)의 '당일 점심 메뉴판' 사진을 감지해 Slack으로 보낸다.

카카오 봇(check_menu.py)과 같은 Slack 채널에 붙는 **두 번째 메뉴 소스**다.
카카오 채널처럼 쉬운 공개 소스가 없어(별식당은 pf.kakao 채널이 없고 개인계정으로 보여
공식 Graph API도 안 됨) Apify 관리형 스크레이퍼(apify/instagram-scraper)를 표준
라이브러리 HTTPS 호출 1번으로 불러 온다. Apify의 주거용 프록시가 인스타의
데이터센터-IP 차단과 계정유형 제약을 모두 우회한다.

동작 원리:
  0) 유료 Apify 호출 "전에" 무료 가드로 단락시켜 비용을 최소화한다(핵심):
     - 주말·한국 공휴일이면 즉시 종료 (Apify 호출 안 함)
     - 오늘 이미 전송했으면 종료 (하루 1건 캡, Apify 호출 안 함)
     - SEND_AFTER_HOUR_KST 이전이면 보류 (Apify 호출 안 함)
  1) fetch_recent_posts(): 유일한 유료 호출. 최근 게시물 JSON 배열을 받는다.
  2) 각 게시물을 [observe] 로그로 남긴다(캡션 필터 튜닝용).
  3) select_menu_post(): type∈{Image,Sidecar} + 오늘(KST) 날짜 + 캡션 매치(메뉴판) +
     이미지 존재 → 최신 1건. 매치 안 되면 전송 안 함(임의 음식사진 자동 전송 금지).
  4) shortcode 가 직전에 보낸 것과 같으면 중복 → skip.
  5) Slack 전송(제목 + IG permalink; Block Kit image 블록 → text+unfurl 폴백) 후 상태 저장.
     게시 직후 전송이라 IG CDN URL 이 신선하다. 만약 Slack 이 IG 이미지를 못 띄우면
     README 의 'files_upload_v2 승격' 노트대로 봇토큰 업로드로 전환한다.

check_menu.py 는 절대 수정하지 않고 헬퍼만 import 한다(import 는 부수효과 없음).

환경변수:
  APIFY_TOKEN         (필수)  Apify API 토큰 (Apify Console → Settings → API)
  SLACK_WEBHOOK_URL   (필수)  Slack Incoming Webhook URL
                              (워크플로 test 모드에서는 SLACK_WEBHOOK_URL_TEST=나와의 대화 로 주입)
  TEST_MODE           (선택)  "1"이면 캡션/중복/주말·공휴일 필터를 무시하고 최신 이미지 게시물을
                              강제 전송(배선 점검용). CLI 플래그 --test 와 동일.
  MENU_CAPTION_REQUIRE_ALL, MENU_CAPTION_REQUIRE_ANY  (선택)  캡션 필터 튜닝(쉼표 구분).
  SEND_AFTER_HOUR_KST (선택)  이 시각(KST) 이전에는 전송 보류. 기본 9(메뉴판이 평일 ~09시 게시).
  RESULTS_LIMIT       (선택)  Apify 가 가져올 최근 게시물 수. 기본 6.

모드(인자):
  (없음)     실제 경로: 가드 → 필터 → dedup → 전송.
  --observe  전송 없이 최근 게시물과 필터 판정만 출력(캡션 필터 튜닝용). Apify 1회 호출.
  --test     캡션 필터를 무시하고 최신 이미지 게시물을 강제 전송(배선 점검용).

선택 의존성:
  holidays (pip)  한국 공휴일(대체공휴일 포함) 판정용. 없으면 경고 후 주말 체크만 적용.
                  (check_menu.rest_day_reason 이 처리.)
"""
import os
import sys
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

# check_menu.py 의 검증된 헬퍼 재사용(수정 없음). import 는 부수효과가 없다
# (check_menu 의 실행부는 `if __name__ == "__main__"` 아래라 안전).
from check_menu import KST, WEEKDAYS_KO, rest_day_reason, sent_today, _post, to_https

PROFILE_URL = os.environ.get("BYEOLSIKDANG_PROFILE_URL",
                             "https://www.instagram.com/byeolsikdang/")

# --- Apify (관리형 인스타 스크레이퍼) ---------------------------------------
# Actor 'apify/instagram-scraper'. REST 경로에선 '~' 로 표기한다.
APIFY_ACTOR = "apify~instagram-scraper"
APIFY_RUN_SYNC_URL = (
    f"https://api.apify.com/v2/acts/{APIFY_ACTOR}/run-sync-get-dataset-items"
)
# 동기 엔드포인트는 서버측 상한 300초. urllib 은 그보다 넉넉히 낮게(≈120s) 잡아
# 무한 대기를 막는다(타임아웃 시 실패로 간주 → alert + 다음 폴링 재시도).
APIFY_TIMEOUT = int(os.environ.get("APIFY_TIMEOUT", "120"))
RESULTS_LIMIT = int(os.environ.get("RESULTS_LIMIT", "6"))


def _env_list(name, default):
    """쉼표로 구분된 환경변수를 리스트로. 미설정이면 default(리스트)."""
    v = os.environ.get(name)
    if v is None:
        return list(default)
    return [s.strip() for s in v.split(",") if s.strip()]


# --- 메뉴판 선별(튜닝 가능 상수; KNOWN_LOGOS 철학과 동일) --------------------
# 별식당 피드는 메뉴판 + 음식사진 + 공지가 섞여 있어 "최신 게시물"을 그냥 집으면 안 된다.
# 캡션 패턴으로 오늘자 메뉴판만 고른다. 실제 캡션은 `--observe` 로그로 확인해 확정한다.
MENU_CAPTION_REQUIRE_ALL = _env_list("MENU_CAPTION_REQUIRE_ALL", ["메뉴"])       # 모두 포함
MENU_CAPTION_REQUIRE_ANY = _env_list("MENU_CAPTION_REQUIRE_ANY",
                                     ["금일", "오늘", "점심"])                    # 하나 이상 포함

# 이 시각(KST) 이전에는 전송 보류. 관측상 메뉴판은 평일 08:54~09:16 에 올라오므로
# 기본 9(09시부터 전송). 캡션 없는 음식사진은 ~10:50 에 따로 올라오지만 캡션 필터가 거른다.
SEND_AFTER_HOUR_KST = int(os.environ.get("SEND_AFTER_HOUR_KST", "9"))

# 카카오와 분리된 상태 파일(shortcode 키).
STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "state", "byeolsikdang_last_seen.json")

# type 정규화: apify 신버전은 'Image'/'Video'/'Sidecar', 구버전은 GraphImage 등.
IMAGE_TYPES = {"image", "sidecar"}


# ---------------------------------------------------------------------------
# Apify 호출 + 방어적 파싱
# ---------------------------------------------------------------------------
def fetch_recent_posts():
    """Apify 동기 엔드포인트로 최근 게시물 JSON 배열을 받아 반환한다(유일한 유료 호출).

    실패 시 예외를 던진다(호출측이 alert 후 exit 1 → 다음 폴링 재시도)."""
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        raise RuntimeError("APIFY_TOKEN 환경변수가 설정되지 않았습니다")
    url = f"{APIFY_RUN_SYNC_URL}?token={token}&format=json&clean=true"
    body = {
        "directUrls": [PROFILE_URL],
        "resultsType": "posts",
        "resultsLimit": RESULTS_LIMIT,
        "onlyPostsNewerThan": "2 days",
        "addParentData": False,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=APIFY_TIMEOUT) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        # 402/403 크레딧 소진·권한, 408 동기 타임아웃 등을 본문째 노출.
        raise RuntimeError(f"Apify HTTP {e.code}: {detail[:300]}")
    data = json.loads(raw)
    if not isinstance(data, list):
        # 에러는 보통 객체로 온다.
        raise RuntimeError(f"Apify 응답이 배열이 아님: {raw[:300]}")
    return data


def _parse_ts_kst(value):
    """Apify timestamp(ISO UTC 문자열 또는 epoch)를 KST datetime 으로. 실패 시 None."""
    if value is None:
        return None
    # epoch(초) 형태 방어.
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc).astimezone(KST)
        except (OverflowError, OSError, ValueError):
            return None
    s = str(value).strip()
    if not s:
        return None
    # 'Z' → '+00:00' 으로 바꿔 구버전 파이썬 fromisoformat 도 처리.
    iso = s.replace("Z", "+00:00")
    dt = None
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        try:
            dt = datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST)


def _norm_type(raw):
    """type/__typename 을 image/video/sidecar 로 정규화."""
    t = str(raw.get("type") or raw.get("__typename") or "").lower()
    if "sidecar" in t:
        return "sidecar"
    if "video" in t or "clip" in t:
        return "video"
    if "image" in t:
        return "image"
    return t


def _extract_image_url(raw):
    """대표 이미지 URL 을 방어적으로 추출. 캐러셀(Sidecar)은 커버=첫 슬라이드."""
    url = raw.get("displayUrl") or raw.get("display_url")
    if url:
        return to_https(url)
    # 캐러셀 폴백: 첫 자식의 displayUrl / images[0].
    for key in ("childPosts", "sidecarChildren", "children"):
        children = raw.get(key)
        if isinstance(children, list) and children:
            c0 = children[0]
            if isinstance(c0, dict):
                u = c0.get("displayUrl") or c0.get("display_url")
                if not u:
                    imgs = c0.get("images")
                    if isinstance(imgs, list) and imgs:
                        u = imgs[0] if isinstance(imgs[0], str) else \
                            (imgs[0].get("url") if isinstance(imgs[0], dict) else None)
                if u:
                    return to_https(u)
    # 최후 폴백: 게시물 images[0].
    imgs = raw.get("images")
    if isinstance(imgs, list) and imgs:
        first = imgs[0]
        if isinstance(first, str):
            return to_https(first)
        if isinstance(first, dict):
            u = first.get("url") or first.get("src")
            if u:
                return to_https(u)
    return None


def normalize_post(raw):
    """Apify 게시물 원본 dict → 우리가 쓰는 정규화 dict(방어적 파싱)."""
    shortcode = raw.get("shortCode") or raw.get("shortcode") or raw.get("code")
    caption = raw.get("caption")
    if caption is None:
        caption = raw.get("captionText") or ""
    ts = _parse_ts_kst(raw.get("timestamp") or raw.get("takenAt")
                       or raw.get("takenAtTimestamp"))
    url = raw.get("url") or (
        f"https://www.instagram.com/p/{shortcode}/" if shortcode else None)
    return {
        "shortcode": shortcode,
        "type": raw.get("type") or raw.get("__typename") or "",
        "type_norm": _norm_type(raw),
        "caption": caption or "",
        "ts": ts,
        "date_kst": ts.strftime("%Y-%m-%d") if ts else None,
        "url": url,
        "image_url": _extract_image_url(raw),
    }


# ---------------------------------------------------------------------------
# 메뉴판 선별
# ---------------------------------------------------------------------------
def is_menu_caption(caption):
    """캡션이 '메뉴판' 패턴이면 True. REQUIRE_ALL 모두 + REQUIRE_ANY 중 하나 이상 포함."""
    text = caption or ""
    if not all(kw in text for kw in MENU_CAPTION_REQUIRE_ALL):
        return False
    if MENU_CAPTION_REQUIRE_ANY and not any(kw in text for kw in MENU_CAPTION_REQUIRE_ANY):
        return False
    return True


def _ts_key(post):
    """정렬용 키(타임스탬프 없으면 가장 과거로)."""
    return post["ts"] or datetime.min.replace(tzinfo=KST)


def select_menu_post(posts, now):
    """오늘자 메뉴판 게시물 중 최신 1건. 없으면 None(=미게시/캡션불일치/휴무)."""
    today = now.strftime("%Y-%m-%d")
    candidates = [
        p for p in posts
        if p["type_norm"] in IMAGE_TYPES
        and p["image_url"]
        and p["date_kst"] == today
        and is_menu_caption(p["caption"])
    ]
    if not candidates:
        return None
    candidates.sort(key=_ts_key, reverse=True)
    return candidates[0]


# ---------------------------------------------------------------------------
# 상태(별식당 전용, shortcode 키)
# ---------------------------------------------------------------------------
def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(post, sent_kst=None):
    """마지막으로 처리한 게시물을 기록. sent_today() 가 읽는 last_sent_kst 를 포함한다."""
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    now = datetime.now(KST)
    state = {
        "last_shortcode": post.get("shortcode"),
        "last_url": post.get("url"),
        "last_caption": (post.get("caption") or "")[:200],
        "last_post_ts_kst": post["ts"].strftime("%Y-%m-%d %H:%M:%S") if post.get("ts") else None,
        "last_sent_kst": sent_kst or now.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"[state] 저장 완료: last_shortcode={state['last_shortcode']}")


# ---------------------------------------------------------------------------
# Slack 전송
# ---------------------------------------------------------------------------
def post_slack(post, note=None):
    """Incoming Webhook 으로 별식당 메뉴 전송. 관대한 형식부터 순서대로 시도(첫 성공에서 종료).

    1순위: Block Kit(section 제목+IG 링크 / image 블록). IG CDN URL 은 서명·시한부라
           게시 직후 전송으로 신선함을 유지한다.
    2순위: 순수 text + IG permalink + unfurl. image 블록이 렌더 안 될 때의 폴백
           (Slack 이 인스타 게시물을 카드로 펼친다).
    둘 다 실패하면 예외(호출측 alert)."""
    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook:
        raise RuntimeError("SLACK_WEBHOOK_URL 환경변수가 설정되지 않았습니다")
    now = datetime.now(KST)
    title = f"🍽️ 별식당 오늘의 점심 ({now.month}/{now.day}, {WEEKDAYS_KO[now.weekday()]})"
    if note:
        title += f" — {note}"

    permalink = post.get("url") or PROFILE_URL
    image_url = post.get("image_url")

    section_text = f"{title}\n<{permalink}|별식당 인스타그램에서 보기>"
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": section_text}}]
    if image_url:
        blocks.append({"type": "image", "image_url": image_url,
                       "alt_text": "별식당 오늘의 점심 메뉴판"})
    blocks_payload = {"text": title, "blocks": blocks}

    # 폴백: permalink 를 펼치게 해 Slack 이 인스타 카드(+썸네일)를 붙이게 한다.
    text_payload = {
        "text": f"{title}\n{permalink}",
        "unfurl_links": True,
        "unfurl_media": True,
    }

    plan = [("blocks", blocks_payload), ("blocks(재시도)", blocks_payload),
            ("text+unfurl", text_payload)]
    last = None
    for i, (label, payload) in enumerate(plan):
        code, body = _post(webhook, payload)
        if 200 <= code < 300:
            print(f"[slack] 전송 완료 ({label})")
            return
        last = (label, code, body)
        print(f"[slack][warn] {label} 실패 code={code} body={body!r} → 다음 방식 시도",
              file=sys.stderr)
        if label.startswith("blocks") and i + 1 < len(plan):
            time.sleep(1.5)
    raise RuntimeError(f"Slack 전송 실패(모든 방식): {last}")


def alert_slack(msg):
    """오류를 1회만 가볍게 알림(실패해도 무시)."""
    try:
        webhook = os.environ.get("SLACK_WEBHOOK_URL")
        if webhook:
            _post(webhook, {"text": f":warning: 별식당봇 오류: {msg}"})
    except Exception as e:
        print(f"[warn] 경고 전송 실패: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
def _short(s, n=60):
    """로그 한 줄용 캡션 축약(개행 치환)."""
    s = (s or "").replace("\n", " ⏎ ").strip()
    return (s[:n] + "…") if len(s) > n else s


def _log_posts(posts):
    """각 게시물을 [observe] 한 줄로. 캡션 필터·필드명 튜닝용."""
    for p in posts:
        print(f"[observe] shortcode={p['shortcode']} type={p['type']!r} "
              f"date_kst={p['date_kst']} menu={is_menu_caption(p['caption'])} "
              f"img={'Y' if p['image_url'] else 'N'} url={p['url']}\n"
              f"          caption={_short(p['caption'])!r}")


def main():
    args = sys.argv[1:]
    observe_mode = "--observe" in args
    test_mode = os.environ.get("TEST_MODE") == "1" or "--test" in args
    now = datetime.now(KST)
    state = load_state()

    # 0) 유료 Apify 호출 전에 무료 가드로 단락(비용 최소화). observe/test 는 통과.
    if not test_mode and not observe_mode:
        reason = rest_day_reason(now)
        if reason:
            print(f"[skip] {reason} → 전송 안 함 (Apify 호출 안 함)")
            return
        if sent_today(state, now):
            print(f"[skip] 오늘 이미 전송함({state.get('last_sent_kst')}) → "
                  f"하루 1건 캡 (Apify 호출 안 함)")
            return
        if now.hour < SEND_AFTER_HOUR_KST:
            print(f"[hold] 아직 KST {now.hour}시 → {SEND_AFTER_HOUR_KST}시 이후 "
                  f"폴링에서 처리 (Apify 호출 안 함, 상태 저장 안 함)")
            return

    # 1) 유일한 유료 호출.
    try:
        raw_posts = fetch_recent_posts()
    except Exception as e:
        print(f"[error] Apify 조회 실패: {e}", file=sys.stderr)
        if not observe_mode:
            alert_slack(str(e))
        sys.exit(1)

    # Apify 가 항목 단위 에러를 섞어 줄 수 있어 분리.
    for r in raw_posts:
        if isinstance(r, dict) and r.get("error"):
            print(f"[warn] Apify 항목 오류: {r.get('error')} "
                  f"{r.get('errorDescription', '')}", file=sys.stderr)
    clean = [r for r in raw_posts if isinstance(r, dict) and not r.get("error")]

    if observe_mode and clean:
        # 필드명 버전차 대비: 첫 게시물의 원본 키를 노출.
        print(f"[observe] 첫 게시물 원본 키: {sorted(clean[0].keys())}")

    posts = [normalize_post(r) for r in clean]
    print(f"[observe] {now:%Y-%m-%d %H:%M:%S} KST — 최근 게시물 {len(posts)}건")
    _log_posts(posts)

    # 2) --observe: 전송 없이 종료.
    if observe_mode:
        print("[observe] 전송 없이 종료(캡션 필터 튜닝용)")
        return

    # 3) --test: 강제 전송(배선 점검). 날짜·중복·가드는 무시하되, 렌더 점검이
    #    실제 전송물로 이뤄지도록 '캡션 매치되는 메뉴판'을 우선한다. 별식당은 캡션 없는
    #    음식사진(종종 .heic)을 메뉴판보다 늦게 올려 '최신 이미지'가 메뉴판이 아닐 수 있다
    #    → 그걸 보내면 Slack 미렌더로 오판할 수 있으므로. 메뉴판이 없으면 최신 이미지로 폴백.
    if test_mode:
        images = [p for p in posts if p["type_norm"] in IMAGE_TYPES and p["image_url"]]
        if not images:
            print("[test] 이미지 게시물이 없어 전송할 것이 없음")
            return
        menu_imgs = [p for p in images if is_menu_caption(p["caption"])]
        target = sorted(menu_imgs or images, key=_ts_key, reverse=True)[0]
        kind = "메뉴판" if menu_imgs else "최신 이미지(캡션 미매치 폴백)"
        print(f"[test] 강제 전송({kind}) shortcode={target['shortcode']} "
              f"date_kst={target['date_kst']}")
        post_slack(target, note="테스트")
        return

    # 4) 실제 경로: 오늘자 메뉴판 최신 1건.
    post = select_menu_post(posts, now)
    if not post:
        print("[skip] 오늘자 메뉴판 게시물 없음(미게시/캡션불일치/휴무) → 전송 안 함")
        return

    if post["shortcode"] and post["shortcode"] == state.get("last_shortcode"):
        print(f"[skip] 직전에 보낸 게시물과 동일(shortcode={post['shortcode']}) → 중복 전송 안 함")
        return

    print(f"[send] 오늘 메뉴판 감지 → Slack 전송 shortcode={post['shortcode']} "
          f"date_kst={post['date_kst']}")
    post_slack(post)
    save_state(post)


if __name__ == "__main__":
    main()
