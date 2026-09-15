# 햄쮸 콘텐츠 자동화

블로그와 YouTube Shorts 반복 제작을 **기존 Notion 대기열 중심으로 자동화**하는 개인용 Python 프로젝트입니다.

## 현재 연결 대상

- 블로그 대기열 Data Source: `21a60556-f086-4b7a-96b3-81995c77edef`
- 쇼츠 대기열 Data Source: `e7ad07b7-0652-4747-956d-e7b071d4bde9`
- 쇼츠 채널은 `햄찌 창작 쇼츠` / `일본 유튜브 쇼츠`를 별도로 처리합니다.

## 하는 일

### 블로그

`작성 요청` → `생성 중` → AI 원고 작성 → 독립 QA → 카드뉴스 5장 로컬 렌더링 → Notion `생성 이미지` 첨부 → `CODEX_HANDOFF_READY`

- 카드뉴스는 AI 이미지 호출이 아니라 Pillow로 직접 만들어 비용을 줄입니다.
- 색 구성은 밝은 화이트/블루/퍼플/핑크/민트 계열이며 누런/세피아 배경을 쓰지 않습니다.
- 네이버 공개/예약 발행 기능은 없습니다.

### 햄찌 창작 쇼츠

`작성 요청` → `Work 제작 중` → 대본/장면표 → 독립 QA → (선택) 이미지/TTS/MP4 → Notion 최종 영상 첨부 → (선택) YouTube 비공개 업로드 → `검토 대기` 또는 `비공개 업로드 완료`

### 일본 쇼츠

위와 동일하지만 일본 60대 이상 시청자, 자연스러운 일본어, 쇼와 생활감, 누런/세피아 금지 규칙을 별도로 적용합니다.

## 안전장치

1. YouTube 업로더는 코드상 `privacyStatus=private`만 지원합니다.
2. `public` / `unlisted` 업로드 함수는 없습니다.
3. 네이버 공개 발행/예약 발행 자동화는 구현하지 않았습니다.
4. 동일 Notion 페이지/수정본은 SQLite idempotency 로그로 중복 실행을 막습니다.
5. 오류가 나면 성공으로 기록하지 않고 `수정 필요` 상태로 남깁니다.
6. 미디어 생성과 YouTube 업로드는 기본값 OFF입니다. 실수로 비용이 발생하거나 업로드되지 않습니다.

## 1. 설치

Python 3.11 권장.

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
cp .env.example .env
```

쇼츠 MP4 합성에는 `ffmpeg`가 필요합니다. GitHub Actions 설정에는 자동 설치 단계가 들어 있습니다.

## 2. 필요한 값

`.env`에 아래 두 값은 필수입니다.

```env
OPENAI_API_KEY=...
NOTION_ACCESS_TOKEN=...
```

Notion 연결에는 현재 블로그 대기열과 쇼츠 대기열을 해당 Notion Connection에 공유해야 합니다.

## 3. 연결 상태 확인

```bash
python -m app.main doctor
```

이 명령은 콘텐츠를 생성하거나 수정하지 않습니다.

## 4. 안전한 첫 테스트

실제 수정 없이 READY 항목을 읽기만 합니다.

```bash
python -m app.main run --dry-run --limit 1
```

채널 하나만 확인하려면:

```bash
python -m app.main channel naver_blog --dry-run --limit 1
python -m app.main channel ppojjugi_shorts --dry-run --limit 1
python -m app.main channel japan_shorts --dry-run --limit 1
```

## 5. 텍스트 자동화 실행

미디어 생성 기본값은 OFF이므로 처음에는 원고/대본/QA/블로그 카드뉴스까지만 처리합니다.

```bash
python -m app.main run --limit 1
```

## 6. 쇼츠 이미지 + 음성 + MP4까지 켜기

`.env`:

```env
ENABLE_MEDIA_GENERATION=true
```

이 기능은 OpenAI 이미지/TTS API 비용이 발생할 수 있습니다.

## 7. YouTube 비공개 업로드

Google Cloud에서 YouTube Data API OAuth Client를 만든 뒤 JSON 파일을 아래 위치에 둡니다.

```text
secrets/youtube_client_secret.json
```

최초 1회:

```bash
python -m app.main setup-youtube-auth
```

그 다음 `.env`:

```env
AUTO_PRIVATE_YOUTUBE_UPLOAD=true
```

업로드 코드는 `private`로 고정되어 있습니다.

## 8. GitHub Actions 첫 연결

`.github/workflows/daily.yml`은 처음에는 **수동 실행 전용**입니다. 저장소에 올렸다고 매일 자동 실행되지 않습니다. Actions 화면에서 채널 하나와 `dry_run`을 선택하면 대기열 1건을 읽기만 하고, OpenAI 호출·Notion 수정·미디어 생성·YouTube 업로드는 하지 않습니다.

GitHub 저장소의 Actions secrets에 다음 값을 넣습니다.

- `OPENAI_API_KEY`
- `NOTION_ACCESS_TOKEN`
- `BLOG_DATA_SOURCE_ID`
- `SHORTS_DATA_SOURCE_ID`

첫 DRY RUN을 확인한 뒤에만 `execute_text`로 채널 1건을 실행합니다. 이 모드도 미디어 생성과 YouTube 업로드는 강제로 OFF입니다.

매일 스케줄, 미디어 생성, YouTube 비공개 업로드는 각각 별도 검수와 승인 후 단계적으로 켭니다. 현재 첫 연결용 workflow에서는 아래 기능을 켜지 않습니다.

- `ENABLE_MEDIA_GENERATION`
- `AUTO_PRIVATE_YOUTUBE_UPLOAD`

생성 파일은 Actions artifact로 14일 보존하도록 설정했습니다. Notion 파일 첨부가 성공하면 카드뉴스/MP4는 Notion에도 남습니다.

## 네이버 임시저장에 대해

네이버 블로그 공식 글쓰기 Open API는 현재 제공되지 않으므로, 이 프로젝트에서는 브라우저 로그인 세션/비밀번호/쿠키를 GitHub에 저장하는 위험한 방식으로 우회하지 않습니다.

대신 블로그 결과가 QA를 통과하면 현재 운영 흐름과 호환되는 `CODEX_HANDOFF_READY`로 넘깁니다. 즉, **AI 제작 단계는 이 프로그램이 맡고 네이버 임시저장 조작만 기존 안전한 클라우드 브라우저 단계로 남기는 구조**입니다.

## 비용 절약 포인트

- 이전 ChatGPT 대화 전체를 보내지 않습니다.
- Notion 현재 작업 + 채널 고정 프롬프트만 보냅니다.
- 상태 판정/중복 검사/파일명/카드뉴스 렌더링/로그는 일반 코드가 처리합니다.
- 기본 텍스트 모델은 비용 절감형 `gpt-5.6-luna`입니다.
- 블로그 카드뉴스는 AI 이미지 생성 대신 로컬 렌더링합니다.
- 미디어 생성은 명시적으로 켜야만 실행됩니다.

## 테스트

```bash
pytest -q
```

현재 포함 테스트:

- 채널 분리
- YouTube private 하드코딩
- 네이버 공개/브라우저 자동화 비활성 안전장치
