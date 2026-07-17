#!/usr/bin/env python3
"""SKKU 공지 게시판들을 크롤링해 새 글을 Discord로 알림.

여러 게시판을 BOARDS 설정으로 관리한다. 각 게시판은 자기만의
Discord Webhook(환경변수)과 상태파일(seen_*.json)을 가진다.

- skku_list : 대학 공지(notice01.do). 목록 페이지네이션 + 상세페이지에서 본문/첨부 수집.
- sce_lobby : 학과 공지(반도체융합공학과). 상세페이지는 로그인 벽이라,
              메인(index.do) 로비에 노출되는 최신 공지 4건을 파싱(본문 전문이 로비에 포함됨).

환경변수:
  DISCORD_WEBHOOK_URL       대학 공지용 Webhook (skku 보드)
  SCE_DISCORD_WEBHOOK_URL   학과 공지용 Webhook (sce 보드)
  FORCE_LATEST              "1" 이면 새 글이 없어도 각 보드의 최신 글 1건을 전송(동작 확인용)

Webhook 이 설정되지 않은 보드는 조용히 건너뛴다.
"""

import html as html_lib
import json
import os
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

HERE = Path(__file__).parent

# ─────────────────────────── 게시판 설정 ───────────────────────────
BOARDS = [
    {
        "key": "skku",
        "name": "성균관대학교 공지사항",
        "type": "skku_list",
        "webhook_env": "DISCORD_WEBHOOK_URL",
        "state_file": "seen_articles.json",
        "base_url": "https://www.skku.edu/skku/campus/skk_comm/notice01.do",
        # 고정공지("공지")는 교수임용 등 위주라 알림에서 제외한다.
        "ignore_pinned": True,
        # 표시 스타일: 카테고리별 색상/이모지 + 로고 아이콘 + 상세 필드
        "style": "full",
        "logo": "https://www.skku.edu/_res/skku/img/skku_s.png",
        "new_prefix": "📢 **새 공지가 올라왔어요!**",
    },
    {
        "key": "sce",
        "name": "반도체융합공학과 공지",
        "type": "sce_lobby",
        "webhook_env": "SCE_DISCORD_WEBHOOK_URL",
        "state_file": "seen_sce.json",
        "index_url": "https://sce.skku.edu/sce/index.do",
        "view_base": "https://sce.skku.edu/sce/notice.do",
        # 표시 스타일: 미니멀 · 단일 테마색.
        # 상단  "💽 SCE Notice [카테고리]" · 제목 · 본문 · 하단 "SCE Notice · 날짜 · (상대시각)"
        "style": "simple",
        "logo": None,
        "accent_color": 0x0EA5A6,   # 테크 틸(teal) — 대학 카드와 확실히 구분
        "symbol": "💽",
        "card_label": "SCE Notice",
        "new_prefix": "",           # 머리말 없이 임베드로 바로
    },
]

# ─────────────────────────── 공통 설정 ───────────────────────────
PAGE_SIZE = 10   # skku_list: 한 페이지 게시글 수
MAX_PAGES = 10   # skku_list: 새 글을 찾아 거슬러 올라갈 최대 페이지(안전 상한)
MAX_NOTIFY = 20  # 한 실행에서 보낼 최대 알림 수(상태 손상 등 폭주 방지)
PREVIEW_LEN = 180  # 본문 미리보기 최대 길이(글자)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

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


def _truncate(text, limit=PREVIEW_LEN):
    text = _clean(text)
    if len(text) > limit:
        return text[:limit].rstrip() + " …"
    return text


# ─────────────────── skku_list (대학 공지) 파서 ───────────────────
# 식별(중복 판정)은 URL 의 articleNo(생성 시 부여되는 고정 DB id, 재사용·이동 없음)로 한다.
# 목록에 보이는 "No.XXXXX"는 현재 위치로 계산되는 번호라 글이 삭제되면 밀리므로 식별에 쓰지 않는다.
# 순서는 사이트가 준 목록 순서(최신 먼저)를 그대로 신뢰한다.
def skku_fetch_page(board, offset=0):
    """대학 공지 목록의 한 페이지를 사이트 원본 순서(최신 먼저) 그대로 파싱해 반환."""
    base = board["base_url"]
    list_url = f"{base}?mode=list&articleLimit={PAGE_SIZE}&article.offset={offset}"
    resp = requests.get(list_url, headers=HEADERS, timeout=20)
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

        articles.append(
            {
                "no": int(no),
                "title": html_lib.unescape(title),
                "url": f"{base}?mode=view&articleNo={no}&article.offset=0&articleLimit=10",
                "category": category,
                "writer": writer,
                "date": date,
                "pinned": pinned,
            }
        )

    # 정렬하지 않는다 — 사이트 원본 순서(최신 먼저)를 그대로 유지.
    return articles


def skku_seed(board):
    """최초 실행 기준선: 현재 노출되는 글들의 articleNo 를 모두 저장(알림 없음).

    깊은 페이지까지 훑어 기준선에 포함해야, 이후 새 글이 옛 글을 아래로 밀어내도
    그 옛 글을 새 글로 오인하지 않는다.
    """
    seen, new_state = skku_detect(board, {"seen": []}, seed_mode=True)
    return new_state


def skku_latest(board):
    """동작확인용: 현재 목록 맨 위(비고정) 글."""
    arts = skku_fetch_page(board, 0)
    for a in arts:
        if not (a["pinned"] and board.get("ignore_pinned", False)):
            return a
    return arts[0] if arts else None


def skku_detect(board, state, seed_mode=False):
    """articleNo(고정 DB id) 기준으로 새 글을 감지. 반환: (오래된 것 먼저, 갱신된 state).

    - seen 에 없는 articleNo 가 새 글. articleNo 는 삭제·번호밀림·재사용에 영향받지 않는다.
    - 최신 먼저 순서로 훑다가, 새 글이 하나도 없는 페이지를 만나면 그 아래는 모두 본 글이므로 멈춘다.
    - seed_mode=True(최초 기준선 만들기)면 알림 목록을 만들지 않고 seen 만 채운다.

    안전장치: MAX_PAGES / 같은 페이지 반복(offset 무시) 감지 / MAX_NOTIFY.
    """
    seen_list = state.get("seen", [])
    seen = set(seen_list)
    ignore_pinned = board.get("ignore_pinned", False)

    collected = []            # 새 글(최신 먼저)
    collected_ids = set()
    prev_ids = None
    for page in range(MAX_PAGES):
        arts = skku_fetch_page(board, page * PAGE_SIZE)
        if not arts:
            break
        ids = tuple(a["no"] for a in arts)
        if ids == prev_ids:   # offset 이 안 먹혀 같은 페이지가 반복됨 → 폭주 방지
            print(f"경고[{board['key']}]: 페이지 {page} 가 이전과 동일 → 중단", file=sys.stderr)
            break
        prev_ids = ids

        page_new = []
        for a in arts:
            if a["pinned"] and ignore_pinned:
                continue
            if a["no"] in seen or a["no"] in collected_ids:
                continue
            collected_ids.add(a["no"])
            page_new.append(a)
        collected.extend(page_new)
        if seed_mode:
            continue          # 기준선 만들 땐 멈추지 않고 깊은 페이지까지 훑는다
        if not page_new:      # 새 글이 없는 페이지 → 그 아래는 모두 본 글
            break
    else:
        if not seed_mode:
            print(f"경고[{board['key']}]: {MAX_PAGES}페이지까지 계속 새 글이 있었습니다. "
                  "일부 오래된 글을 놓쳤을 수 있습니다.", file=sys.stderr)

    # seen 갱신: 이번에 본 글(최신 먼저)을 앞에 붙이고 최근 500개만 유지.
    merged = list(dict.fromkeys([a["no"] for a in collected] + seen_list))[:500]
    new_state = {"seen": merged}

    if seed_mode:
        return [], new_state

    new = list(reversed(collected))   # 오래된 것 먼저
    if len(new) > MAX_NOTIFY:
        print(f"경고[{board['key']}]: 새 글 {len(new)}건 → 최신 {MAX_NOTIFY}건만 전송",
              file=sys.stderr)
        new = new[-MAX_NOTIFY:]
    return new, new_state


def skku_enrich(board, article):
    """상세페이지에서 본문 미리보기/첨부파일을 채운다(실패해도 무해)."""
    try:
        resp = requests.get(article["url"], headers=HEADERS, timeout=20)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")

        label = soup.find("dt", string=lambda s: s and "게시글 내용" in s)
        body_el = label.find_next("dd") if label else None
        if body_el:
            text = "\n".join(l for l in body_el.get_text("\n", strip=True).splitlines() if l.strip())
            article["preview"] = _truncate(text)

        attachments = []
        for a in soup.select("a[href*='mode=download']"):
            name = _clean(a.get_text(strip=True))
            if name and name not in attachments:
                attachments.append(name)
        article["attachments"] = attachments
    except Exception as e:  # noqa: BLE001
        print(f"  (상세 조회 실패 {board['key']} no={article['no']}: {e})", file=sys.stderr)
    return article


# ─────────────────── sce_lobby (학과 공지) 파서 ───────────────────
def sce_fetch_articles(board):
    """학과 메인 로비의 최신 공지(보통 4건)를 파싱. 본문 전문이 로비에 포함돼 있어
    별도 상세 조회 없이 미리보기까지 채운다. (상세페이지는 로그인 벽)"""
    resp = requests.get(board["index_url"], headers=HEADERS, timeout=20)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "utf-8"
    soup = BeautifulSoup(resp.text, "html.parser")

    origin = board["index_url"].split("/", 3)
    origin = f"{origin[0]}//{origin[2]}"  # https://sce.skku.edu

    articles = []
    ul = soup.select_one("ul.mini-type01")
    if not ul:
        return articles

    for li in ul.find_all("li", recursive=False):
        link = li.select_one(".mini-board-content-title a[href*='articleNo=']")
        if not link:
            continue
        href = link.get("href", "").replace("&amp;", "&")
        no = None
        for part in href.split("?", 1)[-1].split("&"):
            if part.startswith("articleNo="):
                no = part.split("=", 1)[1]
                break
        if not no:
            continue

        title = _clean(link.get_text(strip=True))
        cat_el = li.select_one(".mini-board-category")
        category = cat_el.get_text(strip=True) if cat_el else ""
        date_el = li.select_one(".mini-board-date")
        date = date_el.get_text(strip=True) if date_el else ""

        inner = li.select_one(".mini-board-content-inner")
        body = _clean(inner.get_text(" ", strip=True)) if inner else ""
        if body.startswith(title):  # 로비 본문 앞에 제목이 중복돼 있으면 제거
            body = body[len(title):].strip()

        # 로비에 첨부파일이 노출되는 경우가 있으면 수집(현재는 대개 없음)
        attachments = []
        for a in li.select("a[href*='download'], a[href*='fileDown'], a[href*='attach']"):
            name = _clean(a.get_text(strip=True))
            if name and name not in attachments:
                attachments.append(name)

        url = href if href.startswith("http") else origin + href
        articles.append(
            {
                "no": int(no),
                "title": html_lib.unescape(title),
                "url": url,
                "category": category,
                "writer": "",
                "date": date,
                "pinned": False,
                "preview": _truncate(body),
                "attachments": attachments,
            }
        )

    # 로비는 이미 최신 먼저 순서 — 정렬하지 않고 원본 순서 유지.
    return articles


def sce_seed(board):
    """최초 실행 기준선: 현재 로비 글들을 본 것으로 저장(알림 없음)."""
    arts = sce_fetch_articles(board)
    return {"seen": [a["no"] for a in arts]}


def sce_detect(board, state):
    """로비에서 seen 에 없는 글을 새 글로. 반환: (오래된 것 먼저, 갱신된 state)."""
    seen = set(state.get("seen", []))
    arts = sce_fetch_articles(board)          # 최신 먼저
    new = [a for a in arts if a["no"] not in seen]
    new.reverse()                              # 오래된 것 먼저
    for a in new:
        seen.add(a["no"])
    new_state = {"seen": sorted(seen)[-500:]}
    if len(new) > MAX_NOTIFY:                   # 폭주 방지(로비는 최대 4건이라 사실상 무해)
        print(f"경고[{board['key']}]: 새 글 {len(new)}건 → 최신 {MAX_NOTIFY}건만 전송",
              file=sys.stderr)
        new = new[-MAX_NOTIFY:]
    return new, new_state


def sce_latest(board):
    arts = sce_fetch_articles(board)
    return arts[0] if arts else None


# ─────────────────── 보드 타입 디스패치 ───────────────────
def board_is_first_run(board, state):
    return not state.get("seen")


def board_seed(board):
    """최초 실행 기준선 state 를 만든다(알림 없음)."""
    if board["type"] == "skku_list":
        return skku_seed(board)
    if board["type"] == "sce_lobby":
        return sce_seed(board)
    raise ValueError(board["type"])


def board_detect(board, state):
    """새 글(오래된 것 먼저)과 갱신된 state 를 반환. 미리보기/첨부까지 채운다."""
    if board["type"] == "skku_list":
        new, new_state = skku_detect(board, state)
        for a in new:
            skku_enrich(board, a)
        return new, new_state
    if board["type"] == "sce_lobby":
        return sce_detect(board, state)  # 이미 미리보기 포함
    raise ValueError(board["type"])


def board_latest(board):
    """동작확인(FORCE_LATEST)용 최신 글 1건(미리보기/첨부 채워서)."""
    if board["type"] == "skku_list":
        a = skku_latest(board)
        if a:
            skku_enrich(board, a)
        return a
    if board["type"] == "sce_lobby":
        return sce_latest(board)
    raise ValueError(board["type"])


# ─────────────────── 상태파일 ───────────────────
def load_state(state_file):
    if state_file.exists():
        try:
            return json.loads(state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            return {}
    return {}


def save_state(state_file, state):
    state_file.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ─────────────────── Discord 전송 ───────────────────
def _title(article):
    t = article["title"]
    return t[:237] + "..." if len(t) > 240 else t


def _iso(date):
    return f"{date}T00:00:00.000Z" if len(date) == 10 and date[4] == "-" else None


def build_embed_full(board, article):
    """대학 공지용: 카테고리별 색상/이모지 + 로고 + 상세 필드(작성/작성일/첨부)."""
    cat = _cat_key(article.get("category", ""))
    logo = board.get("logo")

    embed = {
        "author": {"name": f"{CATEGORY_EMOJI.get(cat, '🔔')} {article.get('category') or '공지'}",
                   "icon_url": logo},
        "title": _title(article),
        "url": article["url"],
        "color": CATEGORY_COLORS.get(cat, DEFAULT_COLOR),
        "fields": [],
        "footer": {"text": board["name"], "icon_url": logo},
    }
    if article.get("preview"):
        embed["description"] = article["preview"]
    if article.get("writer"):
        embed["fields"].append({"name": "✍️ 작성", "value": article["writer"], "inline": True})
    if article.get("date"):
        embed["fields"].append({"name": "📅 작성일", "value": article["date"], "inline": True})

    atts = article.get("attachments") or []
    if atts:
        value = "\n".join(f"• {name}" for name in atts[:5])
        if len(atts) > 5:
            value += f"\n… 외 {len(atts) - 5}개"
        embed["fields"].append({"name": f"📎 첨부파일 ({len(atts)})", "value": value, "inline": False})

    ts = _iso(article.get("date", ""))
    if ts:
        embed["timestamp"] = ts
    return embed


def build_embed_simple(board, article):
    """학과 공지용: 미니멀 · 단일 테마색 · 칩 이모지. 별도 필드 없이 한눈에.

    카테고리는 제목 앞 배지로, 날짜/첨부는 하단에 간결하게 녹인다.
    """
    symbol = board.get("symbol", "")
    color = board.get("accent_color", DEFAULT_COLOR)
    label = board.get("card_label", board["name"])
    cat = _cat_key(article.get("category", ""))

    # 상단(author) 라인: "💽 SCE Notice [카테고리]"
    head = f"{label} [{cat}]" if cat else label
    if symbol:
        head = f"{symbol} {head}"

    embed = {
        "author": {"name": head},
        "title": _title(article),   # 제목만 (카테고리는 상단 라인으로)
        "url": article["url"],
        "color": color,
    }
    if board.get("logo"):  # 로고가 설정돼 있으면 오른쪽 썸네일로
        embed["thumbnail"] = {"url": board["logo"]}

    parts = []
    if article.get("preview"):
        parts.append(article["preview"])
    atts = article.get("attachments") or []
    if atts:
        parts.append("📎 " + ", ".join(atts[:5]) + (f" 외 {len(atts) - 5}개" if len(atts) > 5 else ""))
    if parts:
        embed["description"] = "\n\n".join(parts)

    # 하단(footer): "SCE Notice · 날짜" + Discord 가 timestamp 를 "어제 오전"처럼 덧붙임
    date = article.get("date", "")
    embed["footer"] = {"text": f"{label} · {date}" if date else label}
    ts = _iso(date)
    if ts:
        embed["timestamp"] = ts
    return embed


def build_embed(board, article):
    if board.get("style") == "simple":
        return build_embed_simple(board, article)
    return build_embed_full(board, article)


def send_discord(webhook, board, article, prefix=None):
    if prefix is None:
        prefix = board.get("new_prefix", "📢 **새 공지가 올라왔어요!**")
    payload = {"embeds": [build_embed(board, article)]}
    if prefix:  # 머리말이 있으면 임베드 위 본문으로, 없으면 임베드만
        payload["content"] = prefix
    r = requests.post(webhook, json=payload, timeout=20)
    r.raise_for_status()


# ─────────────────── 보드 처리 ───────────────────
def process_board(board, force_latest):
    webhook = os.environ.get(board["webhook_env"])
    tag = f"[{board['key']}] {board['name']}"
    if not webhook:
        print(f"{tag}: Webhook 미설정({board['webhook_env']}) → 건너뜀")
        return

    print(f"\n=== {tag} ===")
    state_file = HERE / board["state_file"]
    state = load_state(state_file)

    def send_test_latest():
        latest = board_latest(board)
        if latest:
            send_discord(webhook, board, latest, prefix="✅ **동작 확인** — 현재 최신 글")
            print(f"  FORCE_LATEST: 최신 글 1건 테스트 전송 → {latest['title']}")

    if board_is_first_run(board, state):  # 최초 실행: 기준선만 저장, 알림 없음
        save_state(state_file, board_seed(board))
        print("  최초 실행: 기준선 저장 완료 (알림 없음)")
        if force_latest:
            send_test_latest()
        return

    new_articles, new_state = board_detect(board, state)
    if not new_articles:
        print("  새 글 없음")
        save_state(state_file, new_state)
        if force_latest:
            send_test_latest()
        return

    print(f"  새 글 {len(new_articles)}건 전송 중...")
    for a in new_articles:
        send_discord(webhook, board, a)
        print(f"    전송: [{a.get('seq') or a['no']}] {a['title']}")
    save_state(state_file, new_state)
    print("  완료.")


def main():
    force_latest = os.environ.get("FORCE_LATEST") == "1"
    configured = [b for b in BOARDS if os.environ.get(b["webhook_env"])]
    if not configured:
        print("ERROR: 설정된 Webhook 이 하나도 없습니다. "
              "DISCORD_WEBHOOK_URL 등을 설정하세요.", file=sys.stderr)
        sys.exit(1)

    had_error = False
    for board in BOARDS:
        try:
            process_board(board, force_latest)
        except Exception as e:  # noqa: BLE001 - 한 보드 실패가 다른 보드를 막지 않게
            had_error = True
            print(f"[{board['key']}] 처리 중 오류: {e}", file=sys.stderr)

    if had_error:
        sys.exit(1)


if __name__ == "__main__":
    main()
