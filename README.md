# SKKU 공지사항 Discord 알림 봇

성균관대학교 [공지사항 게시판](https://www.skku.edu/skku/campus/skk_comm/notice01.do)에
새 글이 올라오면 Discord 채널로 알림을 보내는 봇입니다.

별도의 서버 없이 **GitHub Actions 크론**으로 매시간 게시판을 확인하고,
새 글을 **Discord Incoming Webhook**으로 전송합니다.

## 동작 방식

1. `scraper.py` 가 목록 페이지(`notice01.do?mode=list`)를 파싱해 게시글의
   `articleNo`·제목·카테고리·작성자·날짜를 추출합니다. (서버 사이드 렌더링이라 JS 실행 불필요)
2. `seen_articles.json` 에 저장된 "이전에 본 글 번호"와 비교해 **새 글만** 골라냅니다.
   새 글이 10건(한 페이지)을 넘겨 쌓여 있어도, 이미 본 글이 나올 때까지 **다음 페이지로 넘어가며
   전부** 수집합니다. (안전 상한 `MAX_PAGES`)
3. 새 글의 상세페이지에서 **본문 미리보기·첨부파일 목록**을 가져와,
   카테고리별 색상/이모지로 꾸민 Discord 임베드로 전송하고 상태 파일을 갱신해 다시 커밋합니다.

### 알림 카드에 담기는 정보
- 카테고리(색상·이모지로 구분) / 제목(클릭 시 원문 이동)
- 본문 앞부분 미리보기(기본 180자)
- 작성자 · 작성일 · 첨부파일 이름 목록

> 최초 실행 시에는 도배를 막기 위해 현재 목록을 전부 "기준선"으로만 저장하고 알림은 보내지 않습니다.
> 그 이후 올라오는 글부터 알림이 갑니다.

## 설정 방법

### 1. Discord Webhook 만들기
1. 알림을 받을 Discord 채널 → **채널 편집 → 연동(Integrations) → 웹후크(Webhooks) → 새 웹후크**
2. 웹후크 URL 복사

### 2. GitHub Secret 등록
저장소 → **Settings → Secrets and variables → Actions → New repository secret**
- Name: `DISCORD_WEBHOOK_URL`
- Value: 위에서 복사한 웹후크 URL

### 3. Actions 권한 확인
저장소 → **Settings → Actions → General → Workflow permissions** 에서
**Read and write permissions** 가 켜져 있어야 상태 파일 커밋이 됩니다.

## 사용 방법

### 자동 실행
등록만 해두면 **매시 정각**에 자동으로 게시판을 확인합니다.
(GitHub Actions 크론은 부하 상황에 따라 수 분 지연될 수 있습니다.)

### 수동 실행 / 동작 확인
저장소 → **Actions 탭 → SKKU Notice Watcher → Run workflow** 버튼을 누르면 즉시 실행됩니다.
`force_latest` 옵션이 켜져 있으면(기본값) 새 글이 없어도 **현재 가장 최근 글 1건**을
테스트로 Discord에 보내주므로 연동이 잘 됐는지 바로 확인할 수 있습니다.

### 로컬 실행
```bash
pip install -r requirements.txt
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
export FORCE_LATEST=1   # 최신 글 1건 테스트 전송(선택)
python scraper.py
```

## 디자인/표시 커스터마이즈
`scraper.py` 상단 상수로 조절합니다.
- `CATEGORY_COLORS` / `CATEGORY_EMOJI` — 카테고리별 임베드 색상·이모지
- `PREVIEW_LEN` — 본문 미리보기 글자 수(기본 180)
- `LOGO_URL` — 카드에 표시되는 로고 아이콘

> 지금은 본문을 **그대로 발췌**해 미리보기로 보여줍니다. 진짜 AI 요약이 필요하면
> `fetch_detail` 에서 얻은 본문을 LLM API(Claude/OpenAI 등)로 요약하도록 확장할 수 있습니다.
> (별도 API 키·호출 비용 필요)

## 크론 주기 변경
`.github/workflows/notice.yml` 의 `cron` 값을 수정하세요. (UTC 기준)
- `'0 * * * *'` — 매시 정각 (기본값)
- `'*/30 * * * *'` — 30분마다
- `'0 0,3,6,9,12 * * *'` — 특정 시각들

## 파일 구성
| 파일 | 설명 |
|------|------|
| `scraper.py` | 크롤링 + 새 글 판별 + Discord 전송 |
| `.github/workflows/notice.yml` | GitHub Actions 스케줄/수동 실행 워크플로우 |
| `seen_articles.json` | 이전에 본 글 번호 저장(상태 파일, 자동 갱신) |
| `requirements.txt` | 파이썬 의존성 |
