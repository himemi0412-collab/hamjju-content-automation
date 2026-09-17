# 햄쮸 콘텐츠 자동화

블로그와 YouTube Shorts 반복 제작을 **기존 Notion 대기열 중심으로 자동화**하는 개인용 Python 프로젝트입니다.

## 현재 연결 대상

- 블로그 대기열 Data Source: `21a60556-f086-4b7a-96b3-81995c77edef`
- 쇼츠 대기열 Data Source: `e7ad07b7-0652-4747-956d-e7b071d4bde9`
- 쇼츠 채널은 `햄찌 창작 쇼츠` / `일본 유튜브 쇼츠`를 별도로 처리합니다.

## 하는 일

### 블로그

`작성 요청` → `생성 중` → AI 원고 작성 → 독립 QA → 카드뉴스 5장 로컬 렌더링 → Notion `생성 이미지` 첨부 → `CODEX_HANDOFF_READY`

- `목록 구분`이 `이전 주제 보관`인 항목은 자동 실행 후보에서 제외합니다.
- 블로그는 `선별 상태=추천`, `모델 확인=공식 확인`, `진행 순서>0`을 모두 만족해야 자동 실행 후보가 됩니다.
- 후보가 여러 개면 생성 시각이 아니라 `진행 순서` 오름차순으로 1건을 고릅니다.
- 카드뉴스는 AI 이미지 호출이 아니라 Pillow로 직접 만들어 비용을 줄입니다.
- 색 구성은 밝은 화이트/블루/퍼플/핑크/민트 계열이며 누런/세피아 배경을 쓰지 않습니다.
- 네이버 공개/예약 발행 기능은 없습니다.

### 햄찌 창작 쇼츠

`작성 요청` → `Work 제작 중` → 대본/장면표 → 독립 QA → (선택) 이미지/TTS/MP4 → Notion 최종 영상 첨부 → `검토 대기`

### 일본 쇼츠

위와 동일하지만 일본 60대 이상 시청자, 자연스러운 일본어, 쇼와 생활감, 누런/세피아 금지 규칙을 별도로 적용합니다.

## 안전장치

1. 현재 운영 기준에서는 YouTube 업로드를 실행하지 않습니다. 공개 승인은 항상 NO에서 시작합니다.
2. 남아 있는 YouTube 업로더 코드는 `privacyStatus=private`만 지원하지만 모든 워크플로에서 `AUTO_PRIVATE_YOUTUBE_UPLOAD=false`로 고정합니다.
3. 네이버 공개 발행/예약 발행 자동화는 구현하지 않았습니다.
4. 로컬/동일 실행 환경에서는 SQLite 기록으로 중복 실행을 막고, GitHub Actions에서는 동시 실행 제한과 Notion의 `작성 요청 → 생성 중` 상태 전환으로 중복 처리를 막습니다.
5. 오류가 나면 성공으로 기록하지 않고 `수정 필요` 상태로 남깁니다.
6. 로컬과 GitHub Actions 모두 YouTube 업로드가 OFF이며, 쇼츠 제작은 최종 MP4와 Notion 검토 기록까지만 진행합니다.
7. 블로그 카드뉴스가 정확히 5장이 아니거나 Notion 첨부가 실패하면 성공 상태로 넘기지 않습니다.
8. OAuth 토큰의 실제 채널 ID가 고정된 삐죽이/일본 채널 ID와 다르면 파일 전송 전에 중단합니다.
9. GPT Image 2는 `1024x1536` / `medium` 품질로 고정하며, `auto` 품질을 사용하지 않습니다.
10. 월별 보수적 예상비용 장부가 $22에 도달하기 전에 모든 신규 OpenAI 호출과 YouTube 업로드를 중단합니다. OpenAI 프로젝트의 $25 하드 한도는 최종 방어선입니다.

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

이 기능은 OpenAI 이미지 API와 Fal TTS 비용이 발생할 수 있습니다. 쇼츠 음성은
`fal-ai/gemini-3.1-flash-tts`만 사용하며, 삐죽이는 `Aoede` 한국어 1인 화자,
일본 쇼츠는 `Gacrux` 일본어 60~70대 여성 1인 화자로 고정합니다. temperature는
둘 다 `1.1`입니다. 한 편 안에서 나레이션과 캐릭터 목소리를 임의로 나누거나 서로
다른 음성 소스를 섞지 않습니다. Fal 오류가 나면 동일 요청을 다시 과금하기 전에
요청 ID를 확인하고, 연결 계정·`FAL_KEY` 계정·결제 계정의 일치 여부를 먼저 점검합니다.

이미지 품질과 내부 비용 한도 기본값은 다음과 같습니다.

```env
IMAGE_QUALITY=medium
OPENAI_MONTHLY_BUDGET_USD=22
OPENAI_BUDGET_LEDGER=output/openai-cost-ledger.json
OPENAI_BUDGET_REQUIRE_EXISTING_LEDGER=false
```

비용 장부는 텍스트 토큰·웹 검색·이미지·TTS를 보수적으로 합산합니다. GitHub Actions는 매 실행마다 이 파일을 캐시에서 복원하고 실행 후 다시 보존하며, 운영 환경에서는 장부가 손상되거나 예상치 않게 사라지면 유료 호출을 진행하지 않습니다. 예상 누계에 다음 호출의 예약 비용을 더했을 때 22달러 이상이면 콘텐츠 생성과 YouTube 업로드를 그 전에 중단합니다. OpenAI 프로젝트의 25달러 한도는 별도의 최종 안전장치입니다.

## 7. YouTube 업로드 정책

Google Cloud에서 YouTube Data API OAuth Client를 만든 뒤 JSON 파일을 아래 위치에 둡니다.

```text
secrets/youtube_client_secret.json
```

채널별 최초 1회:

```bash
python -m app.main setup-youtube-auth ppojjugi_shorts
python -m app.main setup-youtube-auth japan_shorts
python -m app.main verify-youtube-auth
```

현재 운영값:

```env
AUTO_PRIVATE_YOUTUBE_UPLOAD=false
```

업로드 코드는 호출하지 않습니다. 공개·비공개를 포함한 YouTube 전송은 별도 사용자 승인과 별도 작업 없이는 실행하지 않습니다.

## 8. GitHub Actions 첫 연결

`.github/workflows/daily.yml`은 매일 10:00 KST에 블로그 최대 3건, 21:00 KST에 삐죽이·일본 쇼츠를 각각 1건 준비합니다. Actions 화면에서 채널 하나와 `dry_run`을 선택하면 대기열 1건을 읽기만 하고, OpenAI 호출·Notion 수정·미디어 생성·YouTube 업로드는 하지 않습니다. Actions 로그에는 Notion 본문이나 속성값을 출력하지 않고 읽기 성공 여부만 남깁니다.

GitHub 저장소의 Actions secrets에 다음 값을 넣습니다.

- `OPENAI_API_KEY`
- `NOTION_ACCESS_TOKEN`
- `YOUTUBE_CLIENT_SECRET_JSON_B64`
- `YOUTUBE_PPOJJUGI_TOKEN_JSON_B64`
- `YOUTUBE_JAPAN_TOKEN_JSON_B64`

세 YouTube 값은 JSON 파일 원문을 Base64로 변환한 뒤 GitHub의 암호화된 Actions secret에만 저장합니다. 워크플로 실행 중 권한을 제한한 `secrets/` 파일로 복원하며, 저장소 파일·artifact·로그에는 포함하지 않습니다.

현재 블로그·쇼츠 Data Source ID는 코드 기본값에 들어 있으므로 중복 입력하지 않습니다. 대기열을 교체할 때만 Repository Variables로 별도 관리합니다.

`execute_text`는 채널 1건의 원고만 만들고, `execute_media`는 이미 검토 대상이 된 쇼츠 1건의 MP4를 만듭니다. `produce_daily_shorts`는 새 주제를 조사해 삐죽이·일본 쇼츠를 각각 1건씩 준비합니다. 독립 QA에서 멈춘 쇼츠는 원인을 보완한 뒤 `retry_revision`으로 1건만 다시 제작할 수 있습니다. 과거 artifact의 합성 파일만 복구할 때는 `repair_video`가 전체 장면 길이로 다시 합성해 같은 Notion 항목의 검토 영상을 교체합니다.

미디어 생성은 쇼츠 실행에서만 켜집니다. 예약 쇼츠 제작, `execute_media`, `produce_daily_shorts`, `retry_revision`, 영상 복구까지 모두 MP4와 Notion 검토 기록에서 멈추며 YouTube에는 업로드하지 않습니다. `verify_youtube_auth`는 업로드 없이 두 OAuth 연결의 채널 ID와 이름만 검사합니다.

생성 파일은 Actions artifact로 14일 보존하도록 설정했습니다. Notion 파일 첨부가 성공하면 카드뉴스/MP4는 Notion에도 남습니다.

워크플로의 GitHub 공식 액션은 Node.js 24 기반 메이저(`checkout@v7`, `setup-python@v7`, `upload-artifact@v7`)를 사용합니다.

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
- 이미지 품질은 `medium`으로 고정하고 이미지 1장당 $0.05, TTS 1편당 $0.03을 보수적으로 예약합니다.
- 텍스트·웹 검색은 응답 사용량을 기준으로 추정하며, 호출 전 상한을 먼저 예약한 뒤 실제 추정치로 정산합니다.

## 테스트

```bash
pytest -q
```

현재 포함 테스트:

- 채널 분리
- YouTube private 하드코딩
- 네이버 공개/브라우저 자동화 비활성 안전장치
