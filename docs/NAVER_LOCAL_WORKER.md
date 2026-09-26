# 네이버 로컬 임시저장 작업자

Windows 로그인 후 5분마다 Notion의 `네이버 저장 요청`을 확인합니다. 전용 Edge 프로필에 저장된 로컬 로그인만 사용하며 쿠키·비밀번호는 GitHub에 올리지 않습니다. 콘텐츠 작성과 QA는 GitHub 파이프라인이 맡고, 로컬 작업자는 QA 통과 스냅샷과 카드 5장을 받아 브라우저 어댑터에 전달합니다.

## 최초 1회 설정

1. 저장소 루트의 `.env`에 `NOTION_ACCESS_TOKEN`, `BLOG_DATA_SOURCE_ID`, `NAVER_BLOG_ID=himemi0412`를 둡니다.
2. PowerShell에서 `powershell -ExecutionPolicy Bypass -File scripts/install_naver_local_worker.ps1`를 실행합니다.
3. 안내된 `.venv\Scripts\python.exe -m app.naver_local_worker login`을 실행해 전용 Edge 창에서 네이버에 로그인합니다.
4. 로그아웃 후 다시 로그인하면 작업자가 자동 시작됩니다. 즉시 시험하려면 `.venv\Scripts\python.exe -m app.naver_local_worker once`를 실행합니다.

보안 확인·재로그인·캡차, 편집기 구조 변경, 임시저장 목록 재열기 실패가 감지되면 해당 Notion 항목을 `수정 필요`로 표시하고 Windows 경고창과 `output/naver-local/attention.json`을 남깁니다. 실패 항목은 다시 자동 처리하지 않아 중복 저장을 막습니다.

## 안전 경계

- 네이버 안내에 따른 편집기의 `저장` 동작만 실행합니다. 이는 임시저장이고 발행은 하지 않습니다.
- 공개·예약발행 기능은 구현하지 않습니다.
- Notion에 저장된 QA 스냅샷은 여러 코드 블록으로 나뉠 수 있습니다. 연속 블록을 원문 그대로 결합하고 QA 통과와 제목·본문이 있는 스냅샷만 사용합니다. 로컬 임시 snapshot 파일은 전달에 필요하지 않습니다.
- 임시저장 목록에서 같은 제목을 찾아 다시 열고 제목·본문·이미지 5장을 확인한 경우에만 Notion을 `임시저장 완료`로 변경합니다.
- 목록을 열 수 없거나 원고가 일치하지 않으면 완료 처리하지 않습니다.
- 노트북이 꺼져 있거나 Windows 사용자 세션이 로그인되지 않았으면 대기열은 그대로 남습니다.
- GitHub Actions는 블로그 전달 뒤 최대 15분 동안 같은 Notion 항목의 최종 상태를 읽습니다. 완료·실패가 기록되면 그 결과로 요약을 만들고, 시간 초과나 읽기 실패는 PASS로 계산하지 않습니다.

## QA 실패 기록과 복구

수정 원고 재검수 결과는 `output/naver-revalidation/<page-id>/<run-id>/qa-report.json`에 UTF-8 JSON으로 원자적으로 저장합니다. 본문과 카드 1~5를 개별 PASS/FAIL로 기록하고, 각 실패에는 규칙 ID, 근거, 대상, 재시도 가능 여부, 권장 수정 및 QA 오류 분류(`QA_CONTENT_FAIL`, `QA_PARSE_FAIL`, `QA_API_FAIL`, `QA_TIMEOUT`)를 보존합니다. 파싱/API 실패는 콘텐츠 품질 FAIL과 구분하며 PASS 처리하지 않습니다.

전체 본문 QA와 카드 QA가 모두 PASS하기 전에는 새 canonical 원고 hash나 카드 hash를 만들지 않고, handoff/저장 대기 상태를 기록하지 않습니다. 카드 QA 응답에 1~5 개별 판정이 없거나 모순되면 `QA_PARSE_FAIL`로 닫고 다섯 카드 모두 미확인 FAIL로 기록합니다.

## Windows 작업 분류

- `Hamjju Naver Draft Worker`는 로컬 네이버 로그인 세션이 필요한 단일 polling 작업이므로 ACTIVE입니다. GitHub Actions에는 해당 세션이 없고, 이 작업을 제거하면 네이버 임시저장 경로가 멈춥니다.
- `scripts/install_naver_local_worker.ps1`은 설치 또는 재등록 때만 실행하는 설정 스크립트입니다. 상시 감시나 별도 retry worker가 아닙니다.
- 현재 저장소에는 별도 watchdog, snapshot worker, retry worker, `.bat`, `.cmd`, Task Scheduler XML이 없습니다. `output/naver-local`은 카드 다운로드와 오류 알림 기록에 쓰므로 자동 삭제하지 않습니다.
- Edge 프로필과 `.env`의 로그인·인증 자료는 이 작업이 사용하므로 삭제하거나 초기화하지 않습니다.
