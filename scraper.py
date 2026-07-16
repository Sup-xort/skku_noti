#!/usr/bin/env python3
"""SKKU 공지사항 게시판을 크롤링해 새 글을 Discord로 알림.

- 목록 페이지(notice01.do?mode=list)를 파싱해 게시글을 추출한다.
- seen_articles.json 에 저장된 이전에 본 articleNo 와 비교해 새 글만 골라낸다.
- 새 글을 Discord Webhook 으로 전송하고 상태 파일을 갱신한다.

환경변수:
  DISCORD_WEBHOOK_URL  (필수) Discord Incoming Webhook URL
  FORCE_LATEST         "1" 이면 새 글이 없어도 가장 최근 글 1건을 전송(동작 확인용)
"""

import json
import os
import sys
import html as html_lib
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.skku.edu/skku/campus/skk_comm/notice01.do"
LIST_URL = f"{BASE_URL}?mode=list&articleLimit=10&article.offset=0"
STATE_FILE = Path(__file__).parent / "seen_articles.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}


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
        href = link.get("href", "")
        # articleNo 추출
        no = None
        for part in href.replace("&amp;", "&").split("&"):
            if part.strip().lstrip("?").startswith("articleNo="):
                no = part.split("=", 1)[1]
                break
        if not no:
            continue

        title = " ".join(link.get_text(strip=True).split())
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

    # articleNo 내림차순(최신 먼저)
    articles.sort(key=lambda a: a["no"], reverse=True)
    return articles


def load_seen():
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            return set(data.get("seen", []))
        except (json.JSONDecodeError, ValueError):
            return set()
    return set()


def save_seen(seen):
    # 무한 증가를 막기 위해 최근 500개만 유지
    trimmed = sorted(seen, reverse=True)[:500]
    STATE_FILE.write_text(
        json.dumps({"seen": trimmed}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def send_discord(webhook, article, prefix="📢 새 공지"):
    title = article["title"]
    if len(title) > 240:
        title = title[:237] + "..."

    meta = " · ".join(x for x in [article["category"], article["writer"], article["date"]] if x)
    embed = {
        "title": title,
        "url": article["url"],
        "description": meta,
        "color": 0x0B5FA5,
        "footer": {"text": "성균관대학교 공지사항"},
    }
    payload = {"content": prefix, "embeds": [embed]}
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
        # 최초 실행: 현재 목록을 모두 '본 것'으로 등록만 하고 도배하지 않는다.
        for a in articles:
            seen.add(a["no"])
        save_seen(seen)
        print("최초 실행: 현재 목록을 기준선으로 저장했습니다. (알림 없음)")
        if force_latest:
            send_discord(webhook, articles[0], prefix="✅ 동작 확인 (최신 글)")
            print("FORCE_LATEST: 최신 글 1건을 테스트 전송했습니다.")
        return

    # 새 글 = seen 에 없는 것. 오래된 것부터 순서대로 전송.
    new_articles = [a for a in articles if a["no"] not in seen]
    new_articles.sort(key=lambda a: a["no"])

    if not new_articles:
        print("새 글이 없습니다.")
        if force_latest:
            send_discord(webhook, articles[0], prefix="✅ 동작 확인 (최신 글)")
            print("FORCE_LATEST: 최신 글 1건을 테스트 전송했습니다.")
        return

    print(f"새 글 {len(new_articles)}건 전송 중...")
    for a in new_articles:
        send_discord(webhook, a)
        seen.add(a["no"])
        print(f"  전송: [{a['no']}] {a['title']}")

    save_seen(seen)
    print("완료.")


if __name__ == "__main__":
    main()
