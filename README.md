# 햄쮸 콘텐츠 자동화

블로그와 YouTube Shorts 반복 제작을 **기존 Notion 대기열 중심으로 자동화**하는 개인용 Python 프로젝트입니다.

## 저비용 재시도 정책

- 예약 블로그 작업은 QA 실패 시 원고와 카드 5장을 자동으로 통째로 재생성하지 않습니다.
- 실패한 블로그 결과물은 Actions artifact에 보존하고 `resume_blog` 또는
  `rerender_blog_cards`로 필요한 단계만 명시적으로 복구합니다.
- 쇼츠의 QA 자동 재시도는 기본 1회로 제한합니다.
- 완료된 항목은 Notion 상태와 `output/state.db` 체크포인트로 다시 처리하지 않습니다.
- 예산 장부와 처리 체크포인트는 실패한 실행에서도 다음 GitHub Actions 실행으로 복원됩니다.

## 현재 연결 대상

- 블로그 대기열 Data Source: `21a60556-f086-4b7a-96b3-81995c77edef`
- 쇼츠 대기열 Data Source: `e7ad07b7-0652-4747-956d-e7b071d4bde9`
- 쇼츠 채널은 `삐죽이(햄찌 창작 쇼츠)` / `일본 유튜브 쇼츠`를 별도로 처리합니다. 기존 자동화와 OAuth 호환성을 위해 삐죽이의 내부 키만 `ppojjugi_shorts`로 유지합니다.

## 단일 운영 계약

실행 환경별 소유권은 [`docs/OPERATING_CONTRACT.md`](docs/OPERATING_CONTRACT.md)와
프로그램 검증용 [`config/operating_contract.yaml`](config/operating_contract.yaml)에 고정합니다.
GitHub Actions는 원고·카드·쇼츠 이미지·음성·영상·QA·Notion 전달과 YouTube 비공개
업로드를 담당하고, Codex `automation-3`은 검수된 네이버 원고의 임시저장과 재열기
확인만 담당합니다. 공개·예약발행 결정은 항상 사용자 소유입니다. 임시 5분 예약은
운영 일정에 포함하지 않습니다.

## 하는 일

### 블로그

`작성 요청` → `생성 중` → AI 원고 작성 → 카드뉴스 5장 렌더링 → 실제 이미지와 원고 독립 QA → Notion 첨부·본문 재조회 검증 → `네이버 저장 요청` + `READY_FOR_NAVER_DRAFT`

- `목록 구분`이 `이전 주제 보관`인 항목은 자동 실행 후보에서 제외합니다.
- 블로그는 `선별 상태=추천`, `모델 확인=공식 확인`, `진행 순서>0`을 모두 만족해야 자동 실행 후보가 됩니다.
- 후보가 여러 개면 생성 시각이 아니라 `진행 순서` 오름차순으로 1건을 고릅니다.
- 카드뉴스는 카드별 주제에 맞는 무문자 사진형 배경을 AI로 만든 뒤, 한글 제목·설명은 Pillow로 정확히 조판합니다.
- 신규 카드뉴스는 주제와 정보 역할을 보고 Swiss Typography, Technical Manual, Bento Editorial 등 허용된 20개 이름 중 중심 디자인 언어 하나를 자동 선택합니다. 각 언어에는 색상 토큰뿐 아니라 `표지 구도 → 글꼴 체계 → 이미지 전략 → 재질 → 카드 역할별 확장`을 담은 cover-first blueprint가 있으며, 표지에서 확정한 디자인 시스템을 나머지 네 장에 확장합니다. 스타일 이름만 바꾼 화이트 배경·둥근 박스·아이콘 반복은 독립 QA에서 차단합니다. Screenshot Editorial은 별도 큰 화면 중심 렌더러를 사용해 코드·채팅·파일·체크·분기 화면이 카드마다 실제 정보의 주인공이 되도록 합니다.
- 디자인 방향을 고르기 전에는 별도의 `concept_exploration` 모드를 사용할 수 있습니다. 이 모드는 같은 제목·부제목을 유지한 채 20개 디자인 언어를 각각 독립된 1080×1350 표지로 만들고, 4개씩 묶은 비교판 5장과 전체 개요를 출력한 뒤 `READY_FOR_USER_REVIEW`에서 멈춥니다. 이 20장은 한 카드뉴스 세트가 아니며, 사용자가 한 스타일을 선택하기 전에는 본편 5장을 제작하지 않습니다.
- 블로그 제작은 `references/naver_blog_baseline.md`의 `HAMZZU_NAVER_REFERENCE_V1`을 매 실행 로드합니다. 이 파일에는 사용자가 지정한 공개 글 4개와 원고·카드뉴스 공통 기준이 버전 관리되며, ID/URL/생성 응답 계약이 빠지면 Notion 전달 전에 실패합니다.
- 색 구성은 선택한 디자인 언어의 팔레트를 따르되 한 세트 안에서는 제한된 색만 반복하며, 누런 필터·겨자색 중심·세피아 배경은 쓰지 않습니다.
- 네이버 공개/예약 발행 기능은 없습니다.

### 햄찌 창작 쇼츠

`작성 요청` → `Work 제작 중` → 대본/장면표 → 독립 QA → 이미지/TTS/MP4 → Notion 최종 영상 첨부 → 채널 일치 확인 → YouTube `private` 업로드 → `비공개 업로드 완료`

대본의 내레이션·대사·발화 자막은 친구에게 말하듯 자연스러운 한국어 반말을 사용합니다. 존댓말, 무례한 명령조, 억지 유행어는 사용하지 않습니다.

### 일본 쇼츠

위와 동일하지만 일본 60대 이상 시청자, 자연스러운 일본어, 쇼와 생활감, 누런/세피아 금지 규칙을 별도로 적용합니다.
대본은 딱딱한 `です・ます`체 대신 따뜻한 보통체로 작성하되, 무례한 말투나 과한 젊은 슬랭은 사용하지 않습니다.

## 안전장치

1. 쇼츠 제작 실행은 YouTube에 `private`로만 업로드합니다. 공개 승인은 항상 NO에서 시작합니다.
2. YouTube 업로더는 코드상 `privacyStatus=private`만 지원하며, 고정된 채널 ID와 OAuth 채널이 다르면 전송 전에 중단합니다.
3. 네이버 공개 발행/예약 발행 자동화는 구현하지 않았습니다.
4. 로컬/동일 실행 환경에서는 SQLite 기록으로 중복 실행을 막고, GitHub Actions에서는 동시 실행 제한과 Notion의 `작성 요청 → 생성 중` 상태 전환으로 중복 처리를 막습니다.
5. 오류가 나면 성공으로 기록하지 않고 `수정 필요` 상태로 남깁니다.
6. 블로그·dry run·텍스트 전용·영상 복구는 YouTube 업로드가 OFF이고, 검수된 쇼츠 제작·재시도에서만 비공개 업로드가 켜집니다.
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

디자인을 고르기 위한 20개 표지 비교는 Notion·OpenAI API·외부 게시 없이 로컬에서 만들 수 있습니다.

```bash
python -m app.main explore-card-design \
  --title "Codex에서 ChatGPT 열어서 토큰 아끼는 법" \
  --subtitle "Quick Chat 활용" \
  --output output/card-design-exploration
```

GitHub Actions의 수동 실행에서는 `mode=explore_card_design`을 선택합니다. 결과 artifact에는 개별 표지 20장, 비교판 5장, 전체 개요와 선택 대기 manifest가 포함됩니다. 이 모드는 제작 상태판·Notion·비용 장부를 변경하지 않습니다.

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
`fal-ai/gemini-3.1-flash-tts`만 사용하며, 삐죽이는 `Aoede` 한국어 1인 화자로
고정합니다. 이번 일본 최종 영상은 `Gacrux` 일본어 60~70대 여성 1인 화자를
사용합니다. 다음 일본 영상은 20대 여성·60~70대 여성·20대 남성·60~70대 남성
참고 음성 가운데 이야기의 주인공과 시점에 맞는 한 명을 먼저 선택하고, 한 편 안에서는
그 화자만 유지합니다. 남성 후보는 `docs/japan_voice_reference.md`에 기록하며 실제
참고 음성을 듣고 승인하기 전에는 자동 기본값으로 사용하지 않습니다. temperature는
모두 `1.1`입니다. 나레이션과 캐릭터 목소리를 임의로 나누거나 서로 다른 음성 소스를
섞지 않습니다. Fal 오류가 나면 동일 요청을 다시 과금하기 전에
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

로컬 기본값은 OFF입니다. GitHub의 검수된 쇼츠 제작 단계만 환경변수로 ON을 명시합니다.

```env
AUTO_PRIVATE_YOUTUBE_UPLOAD=false
```

업로드 코드는 `private`만 허용합니다. 공개·일부공개 전환과 삭제 기능은 자동화하지 않습니다.

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

`execute_text`는 채널 1건의 원고를 만들며, 블로그에서는 사진형 배경과 로컬 한글 조판으로 카드 5장도 렌더링합니다. `recover_blog`는 `page_id`로 지정한 기존 작성 요청 블로그 1편만 제작하고 새 주제를 만들지 않습니다. `resume_blog`는 새 이미지를 만들지 않고 기존 artifact의 원고·블록·카드 바이트를 그대로 재사용하며, `수정 필요` 또는 과거 `CODEX_HANDOFF_READY` 페이지와 정확히 일치할 때만 재검수합니다. `execute_media`는 이미 검토 대상이 된 쇼츠 1건의 MP4를 만듭니다. `produce_daily_shorts`는 새 주제를 조사해 삐죽이·일본 쇼츠를 각각 1건씩 준비합니다. 독립 QA에서 멈춘 쇼츠는 원인을 보완한 뒤 `retry_revision`으로 1건만 다시 제작할 수 있습니다. 과거 artifact의 합성 파일만 복구할 때는 `repair_video`가 전체 장면 길이로 다시 합성해 같은 Notion 항목의 검토 영상을 교체합니다.

새 블로그와 과거 artifact 재검수 모두 현재 reference baseline을 통과해야 합니다. manifest에는 reference ID·SHA-256·원문 URL 4개를 남깁니다. 이전 QA를 재사용하는 경로도 현재 baseline SHA-256과 정확히 일치할 때만 허용하므로, 기준 변경 전에 만든 카드가 새 기준 검수 없이 통과하지 않습니다. 카드 세트는 주제에 따라 1080×1080 또는 1448×1086을 선택하고, 네 승인 레퍼런스에서 추출한 네 시각 계열 중 하나를 사용합니다. 공개 본문에는 별도 요청이 없는 한 형식적인 `공식 확인 링크` 섹션을 붙이지 않으며 근거는 내부 검수 필드에 보존합니다.

미디어 생성은 쇼츠 실행에서만 켜집니다. 예약 쇼츠 제작, `execute_media`, `produce_daily_shorts`, `retry_revision`은 고정 채널 ID를 검사한 뒤 YouTube에 `private`로 올립니다. `regenerate_review`와 영상 복구는 MP4·Notion 검토 기록에서 멈추고 업로드하지 않습니다. `verify_youtube_auth`는 업로드 없이 두 OAuth 연결의 채널 ID와 이름만 검사합니다.

생성 파일은 Actions artifact로 14일 보존하도록 설정했습니다. Notion 파일 첨부가 성공하면 카드뉴스/MP4는 Notion에도 남습니다.

워크플로의 GitHub 공식 액션은 Node.js 24 기반 메이저(`checkout@v7`, `setup-python@v7`, `upload-artifact@v7`)를 사용합니다.

## 네이버 임시저장에 대해

네이버 블로그 공식 글쓰기 Open API는 현재 제공되지 않으므로, 이 프로젝트에서는 브라우저 로그인 세션/비밀번호/쿠키를 GitHub에 저장하는 위험한 방식으로 우회하지 않습니다.

블로그 결과가 실제 카드 PNG를 포함한 독립 QA와 Notion 재조회 검증을 통과하면 본문·카드 5장·캡션·AI 고지·원고 해시를 `READY_FOR_NAVER_DRAFT` 계약으로 만들고 상태를 `네이버 저장 요청`으로 바꿉니다. 기존 로컬 Codex 임시저장 자동화가 이 대기열을 받아 himemi0412의 비공개 임시저장과 재열람 검증을 수행합니다. GitHub 자체는 네이버 로그인 세션을 보유하지 않으므로 PC/Codex가 꺼져 있거나 로그인이 풀리면 대기하며, 공개·예약발행은 하지 않습니다. 상태판도 Notion 전달 준비와 실제 네이버 저장 확인을 별도로 표시합니다.

블로그 제작은 대기 중인 글을 우선 처리하고 부족한 수만 새로 조사합니다. 신규 항목은 Notion 분류에 필요한 `원본 순서`와 `진행 순서`를 함께 기록합니다. 글마다 오류를 격리하며, 0건·목표 미달·QA 실패·저장 재조회 불일치는 성공으로 처리하지 않습니다. 본문 블록 전체와 카드 5장의 파일 순서·SHA-256을 다시 확인하고, 실패해도 비용 원장과 처리 기록을 다음 실행에 보존합니다. 코드 검사 push는 제작 상태판을 덮어쓰지 않습니다.

## 비용 절약 포인트

- 이전 ChatGPT 대화 전체를 보내지 않습니다.
- Notion 현재 작업 + 채널 고정 프롬프트만 보냅니다.
- 상태 판정/중복 검사/파일명/카드뉴스 렌더링/로그는 일반 코드가 처리합니다.
- 기본 텍스트 모델은 비용 절감형 `gpt-5.6-luna`입니다.
- 블로그 카드뉴스는 AI 배경 5장 비용이 들며, 한글 조판과 최종 합성은 로컬에서 처리합니다.
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
