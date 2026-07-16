#!/usr/bin/env python3
"""SKKU 공지사항 게시판을 크롤링해 새 글을 Discord로 알림.

- 목록 페이지(notice01.do?mode=list)를 파싱해 게시글을 추출한다.
- seen_articles.json 에 저장된 이전에 본 articleNo 와 비교해 새 글만 골라낸다.
- 각 새 글의 상세페이지에서 본문 미리보기/첨부파일을 가져와
  보기 좋은 Discord 임베드로 전송하고 상태 파일을 갱신한다.

환경변수:
  DISCORD_WEBHOOK_URL  (필수) Discord Incoming Webhook URL
  FORCE_LATEST         "1" 이면 새 글이 없어도 가장 최근 글 1건을 전송(동작 확인용)
"""

import html as html_lib
import json
import os
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.skku.edu/skku/campus/skk_comm/notice01.do"
LIST_URL = f"{BASE_URL}?mode=list&articleLimit=10&article.offset=0"
STATE_FILE = Path(__file__).parent / "seen_articles.json"

LOGO_URL = "https://www.skku.edu/_res/skku/img/skku_s.png"

# 본문 미리보기 최대 길이(글자)
PREVIEW_LEN = 180

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

# 카테고리별 임베드 색상 (없는 카테고리는 DEFAULT_COLOR)
CATEGORY_COLORS = {
    "학사": 0x1B6CA8,       # 파랑
    "장학": 0x2E9E5B,       # 초록
    "등록": 0x2E9E5B,
    "채용/모집": 0xE67E22,   # 주황
    "취업/창업": 0xE67E22,
    "행사/세미나": 0x8E44AD,  # 보라
    "봉사": 0x16A085,       # 청록
    "국제": 0x2980B9,
    "일반": 0x7F8C8D,       # 회색
}
DEFAULT_COLOR = 0x0B5FA5

# 카테고리별 앞머리 이모지
CATEGORY_EMOJI = {
    "학사": "🎓",
    "장학": "💰",
    "등록": "💳",
    "채용/모집": "📌",
    "취업/창업": "💼",
    "행사/세미나": "🎉",
    "봉사": "🤝",
    "국제": "🌏",
    "일반": "📄",
}


def _clean(text):
    return " ".join(text.split())


def _cat_key(category):
    """'[채용/모집]' → '채용/모집'."""
    return category.strip().strip("[]").strip()


def fetch_articles():
    """목록 페이지를 파싱해 게시글 리스트를 반환.

    반환: [{no, title, url, category, writer, date, pinned}], 최신(articleNo 큰 것)이 앞.
    """
    resp = requests.get(LIST_URL, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "utf-8"
    soup = BeautifulSoup(resp.text, "html.parser")

    articles = []
    for dl in soup.select("dl.board-list-content-wrap"):
        link = dl.select_one("dt a[href*='articleNo=']")
        if not link:
            continue
        href = link.get("href", "").replace("&amp;", "&")
        no = None
        for part in href.lstrip("?").split("&"):
            if part.startswith("articleNo="):
                no = part.split("=", 1)[1]
                break
        if not no:
            continue

        title = _clean(link.get_text(strip=True))
        cat_el = dl.select_one(".c-board-list-category")
        category = cat_el.get_text(strip=True) if cat_el else ""

        info = dl.select("dd.board-list-content-info li")
        first = info[0].get_text(strip=True) if len(info) > 0 else ""
        pinned = "공지" in first
        writer = info[1].get_text(strip=True) if len(info) > 1 else ""
        date = info[2].get_text(strip=True) if len(info) > 2 else ""

        full_url = f"{BASE_URL}?mode=view&articleNo={no}&article.offset=0&articleLimit=10"
        articles.append(
            {
                "no": int(no),
                "title": html_lib.unescape(title),
                "url": full_url,
                "category": category,
                "writer": writer,
                "date": date,
                "pinned": pinned,
            }
        )

    articles.sort(key=lambda a: a["no"], reverse=True)
    return articles


def fetch_detail(no):
    """상세페이지에서 본문 미리보기와 첨부파일 목록을 가져온다.

    실패해도 알림은 계속 가야 하므로 예외를 삼키고 빈 값을 반환한다.
    반환: {"preview": str, "attachments": [str, ...]}
    """
    url = f"{BASE_URL}?mode=view&articleNo={no}&article.offset=0&articleLimit=10"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")

        # 본문: '게시글 내용' 라벨(dt) 다음의 dd
        preview = ""
        label = soup.find("dt", string=lambda s: s and "게시글 내용" in s)
        body_el = label.find_next("dd") if label else None
        if body_el:
            text = body_el.get_text("\n", strip=True)
            text = "\n".join(line for line in text.splitlines() if line.strip())
            if len(text) > PREVIEW_LEN:
                preview = text[:PREVIEW_LEN].rstrip() + " …"
            else:
                preview = text

        # 첨부파일 이름
        attachments = []
        for a in soup.select("a[href*='mode=download']"):
            name = _clean(a.get_text(strip=True))
            if name and name not in attachments:
                attachments.append(name)

        return {"preview": preview, "attachments": attachments}
    except Exception as e:  # noqa: BLE001 - 상세 실패는 치명적이지 않음
        print(f"  (상세 조회 실패 no={no}: {e})", file=sys.stderr)
        return {"preview": "", "attachments": []}


def load_seen():
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            return set(data.get("seen", []))
        except (json.JSONDecodeError, ValueError):
            return set()
    return set()


def save_seen(seen):
    trimmed = sorted(seen, reverse=True)[:500]
    STATE_FILE.write_text(
        json.dumps({"seen": trimmed}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_embed(article, detail):
    cat = _cat_key(article["category"])
    color = CATEGORY_COLORS.get(cat, DEFAULT_COLOR)
    emoji = CATEGORY_EMOJI.get(cat, "🔔")

    title = article["title"]
    if len(title) > 240:
        title = title[:237] + "..."

    embed = {
        "author": {"name": f"{emoji} {article['category'] or '공지'}", "icon_url": LOGO_URL},
        "title": title,
        "url": article["url"],
        "color": color,
        "fields": [],
        "footer": {"text": "성균관대학교 공지사항", "icon_url": LOGO_URL},
    }

    if detail.get("preview"):
        embed["description"] = detail["preview"]

    if article.get("writer"):
        embed["fields"].append({"name": "✍️ 작성", "value": article["writer"], "inline": True})
    if article.get("date"):
        embed["fields"].append({"name": "📅 작성일", "value": article["date"], "inline": True})

    atts = detail.get("attachments") or []
    if atts:
        shown = atts[:5]
        value = "\n".join(f"• {name}" for name in shown)
        if len(atts) > 5:
            value += f"\n… 외 {len(atts) - 5}개"
        embed["fields"].append({"name": f"📎 첨부파일 ({len(atts)})", "value": value, "inline": False})

    # 날짜를 ISO timestamp 로 (YYYY-MM-DD 형태면)
    date = article.get("date", "")
    if len(date) == 10 and date[4] == "-":
        embed["timestamp"] = f"{date}T00:00:00.000Z"

    return embed


def send_discord(webhook, article, detail, prefix="📢 **새 공지가 올라왔어요!**"):
    payload = {"content": prefix, "embeds": [build_embed(article, detail)]}
    r = requests.post(webhook, json=payload, timeout=20)
    r.raise_for_status()


def main():
    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    force_latest = os.environ.get("FORCE_LATEST") == "1"

    if not webhook:
        print("ERROR: DISCORD_WEBHOOK_URL 환경변수가 없습니다.", file=sys.stderr)
        sys.exit(1)

    articles = fetch_articles()
    if not articles:
        print("게시글을 하나도 파싱하지 못했습니다. HTML 구조가 바뀌었을 수 있습니다.", file=sys.stderr)
        sys.exit(1)

    print(f"파싱된 게시글: {len(articles)}건")
    print(f"최신 글: [{articles[0]['no']}] {articles[0]['title']}")

    seen = load_seen()
    first_run = len(seen) == 0

    if first_run:
        for a in articles:
            seen.add(a["no"])
        save_seen(seen)
        print("최초 실행: 현재 목록을 기준선으로 저장했습니다. (알림 없음)")
        if force_latest:
            latest = articles[0]
            send_discord(webhook, latest, fetch_detail(latest["no"]),
                         prefix="✅ **동작 확인** — 현재 최신 글")
            print("FORCE_LATEST: 최신 글 1건을 테스트 전송했습니다.")
        return

    new_articles = [a for a in articles if a["no"] not in seen]
    new_articles.sort(key=lambda a: a["no"])

    if not new_articles:
        print("새 글이 없습니다.")
        if force_latest:
            latest = articles[0]
            send_discord(webhook, latest, fetch_detail(latest["no"]),
                         prefix="✅ **동작 확인** — 현재 최신 글")
            print("FORCE_LATEST: 최신 글 1건을 테스트 전송했습니다.")
        return

    print(f"새 글 {len(new_articles)}건 전송 중...")
    for a in new_articles:
        detail = fetch_detail(a["no"])
        send_discord(webhook, a, detail)
        seen.add(a["no"])
        print(f"  전송: [{a['no']}] {a['title']}")

    save_seen(seen)
    print("완료.")


if __name__ == "__main__":
    main()
