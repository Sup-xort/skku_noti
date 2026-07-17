# SKKU 공지 Discord 알림 봇

성균관대학교 공지 게시판에 새 글이 올라오면 Discord 채널로 알림을 보내는 봇입니다.
**여러 게시판**을 각각 다른 Discord 채널로 보낼 수 있습니다.

별도의 서버 없이 **GitHub Actions 크론**으로 매시간 게시판을 확인하고,
새 글을 **Discord Incoming Webhook**으로 전송합니다.

## 감시 중인 게시판

| 키 | 게시판 | 방식 | Webhook 시크릿 | 상태파일 |
|----|--------|------|----------------|----------|
| `skku` | [대학 공지사항](https://www.skku.edu/skku/campus/skk_comm/notice01.do) | 목록 페이지네이션 + 상세페이지 본문/첨부 | `DISCORD_WEBHOOK_URL` | `seen_articles.json` |
| `sce` | [반도체융합공학과](https://sce.skku.edu/sce/index.do) | 메인 로비 최신 공지(본문 전문 포함) | `SCE_DISCORD_WEBHOOK_URL` | `seen_sce.json` |

> **왜 방식이 다른가?** 학과 공지는 상세페이지가 SSO 로그인 벽으로 막혀 있어 크롤링할 수 없습니다.
> 대신 학과 **메인 페이지 로비**에 노출되는 최신 공지(보통 4건)에 본문 전문이 그대로 들어있어,
> 그걸 파싱합니다. 로비에는 최신 몇 건만 보이므로, 한 시간에 그보다 많은 글이 몰리면 일부를 놓칠 수 있습니다.
> (현재 학과 로비에는 첨부파일이 노출되지 않아 첨부 정보는 대학 공지에서만 제공됩니다.)

## 동작 방식

1. `scraper.py` 가 각 게시판을 파싱해 게시글의 제목·카테고리·날짜·본문을 추출합니다.
   (서버 사이드 렌더링이라 JS 실행 불필요)
2. 게시판별 상태파일(`seen_*.json`)에 저장된 기준선과 비교해 **새 글만** 골라냅니다.
   - **대학 공지**: 목록의 **표시번호(`No.XXXXX`, seq)**를 기준으로 판정합니다.
     이 게시판의 `articleNo`(내부 DB id)는 게시 순서와 무관하기 때문입니다.
     마지막으로 본 seq(`last_seq`)보다 큰 글만 새 글이며, 한 시간에 10건(한 페이지)을 넘겨
     쌓여 있어도 다음 페이지로 넘어가며 전부 수집합니다(안전 상한 `MAX_PAGES`).
     고정공지("공지", seq 없음)는 `articleNo` 로 따로 중복 관리합니다.
   - **학과 공지**: 로비에 노출되는 글을 `articleNo` 기준 `seen` 목록과 비교합니다.
3. 새 글을 카테고리별 색상/이모지로 꾸민 Discord 임베드로 전송하고 상태 파일을 갱신해 다시 커밋합니다.

### 알림 카드에 담기는 정보
- 카테고리(색상·이모지로 구분) / 제목(클릭 시 원문 이동)
- 본문 앞부분 미리보기(기본 180자)
- 작성자 · 작성일 · 첨부파일 이름 목록(있을 때)

> 최초 실행 시에는 도배를 막기 위해 현재 목록을 전부 "기준선"으로만 저장하고 알림은 보내지 않습니다.
> 그 이후 올라오는 글부터 알림이 갑니다.

## 설정 방법

### 1. Discord Webhook 만들기 (게시판마다 채널 하나씩)
1. 알림을 받을 Discord 채널 → **채널 편집 → 연동(Integrations) → 웹후크(Webhooks) → 새 웹후크**
2. 웹후크 URL 복사

### 2. GitHub Secret 등록
저장소 → **Settings → Secrets and variables → Actions → New repository secret**
- 대학 공지: Name `DISCORD_WEBHOOK_URL` / Value = 해당 채널 웹후크 URL
- 학과 공지: Name `SCE_DISCORD_WEBHOOK_URL` / Value = 다른 채널 웹후크 URL

> 시크릿이 등록되지 않은 게시판은 자동으로 **건너뜁니다.** 원하는 게시판만 켜도 됩니다.

### 3. Actions 권한 확인
저장소 → **Settings → Actions → General → Workflow permissions** 에서
**Read and write permissions** 가 켜져 있어야 상태 파일 커밋이 됩니다.

## 사용 방법

### 자동 실행
등록만 해두면 **매시 정각**에 모든 게시판을 확인합니다.
(GitHub Actions 크론은 부하 상황에 따라 수 분 지연될 수 있습니다.)

### 수동 실행 / 동작 확인
저장소 → **Actions 탭 → SKKU Notice Watcher → Run workflow** 버튼을 누르면 즉시 실행됩니다.
`force_latest` 옵션이 켜져 있으면(기본값) 새 글이 없어도 각 게시판의 **현재 가장 최근 글 1건**을
테스트로 Discord에 보내주므로 연동이 잘 됐는지 바로 확인할 수 있습니다.

### 로컬 실행
```bash
pip install -r requirements.txt
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
export SCE_DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."  # 선택
export FORCE_LATEST=1   # 최신 글 1건 테스트 전송(선택)
python scraper.py
```

## 게시판 추가하기
`scraper.py` 상단 `BOARDS` 리스트에 항목을 추가하면 됩니다.
- 같은 jwxe 목록형(`notice.do?mode=list`)이면 `type: "skku_list"` + `base_url` 지정
- 로비 노출형이면 `type: "sce_lobby"` 구조를 참고
- 새 `webhook_env`·`state_file` 을 부여하고, 워크플로우 `env` 에 시크릿을 추가

## 디자인/표시 커스터마이즈

### 게시판별 메시지 스타일
각 게시판은 `BOARDS` 항목의 `style` 값으로 카드 모양을 고릅니다.
- `"full"` (대학 공지) — 카테고리별 색상/이모지 + 로고 아이콘 + 작성/작성일/첨부 필드
- `"simple"` (학과 공지) — 미니멀 카드: 단일 테마색(`accent_color`)만으로 정체성,
  카테고리는 제목 앞 배지(`「채용/모집」`), 날짜는 하단에 간결하게. 로고/이모지 없음.

관련 설정: `accent_color`(고정 색상), `symbol`(앞 이모지, 비우면 없음), `new_prefix`(알림 머리말), `logo`(썸네일 URL).

> `logo` 에 이미지 URL(`raw.githubusercontent.com` 등)을 넣으면 카드 오른쪽에 썸네일로 표시됩니다.
> `symbol` 에 이모지를 넣으면 머리 라인에 붙습니다. 둘 다 비우면 지금처럼 텍스트만 나옵니다.
> (레포가 public 이어야 Discord 가 이미지를 가져옵니다.)

### 공통
- `CATEGORY_COLORS` / `CATEGORY_EMOJI` — `full` 스타일의 카테고리별 색상·이모지
- `PREVIEW_LEN` — 본문 미리보기 글자 수(기본 180)

> 지금은 본문을 **그대로 발췌**해 미리보기로 보여줍니다. 진짜 AI 요약이 필요하면
> 본문을 LLM API(Claude/OpenAI 등)로 요약하도록 확장할 수 있습니다. (별도 API 키·호출 비용 필요)

## 크론 주기 변경
`.github/workflows/notice.yml` 의 `cron` 값을 수정하세요. (UTC 기준)
- `'0 * * * *'` — 매시 정각 (기본값)
- `'*/30 * * * *'` — 30분마다

## 파일 구성
| 파일 | 설명 |
|------|------|
| `scraper.py` | 게시판 설정(BOARDS) + 크롤링 + 새 글 판별 + Discord 전송 |
| `.github/workflows/notice.yml` | GitHub Actions 스케줄/수동 실행 워크플로우 |
| `seen_articles.json` | 대학 공지 기준선(`last_seq`, `pinned_seen`) 저장(자동 갱신) |
| `seen_sce.json` | 학과 공지 본 글 번호(`seen`) 저장(자동 갱신) |
| `requirements.txt` | 파이썬 의존성 |
