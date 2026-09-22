# 네이버 로컬 임시저장 작업자

Windows 로그인 후 5분마다 Notion의 `네이버 저장 요청`을 확인합니다. 전용 Edge 프로필에 저장된 로컬 로그인만 사용하며 쿠키·비밀번호는 GitHub에 올리지 않습니다.

## 최초 1회 설정

1. 저장소 루트의 `.env`에 `NOTION_ACCESS_TOKEN`, `BLOG_DATA_SOURCE_ID`, `NAVER_BLOG_ID=himemi0412`를 둡니다.
2. PowerShell에서 `powershell -ExecutionPolicy Bypass -File scripts/install_naver_local_worker.ps1`를 실행합니다.
3. 안내된 `.venv\Scripts\python.exe -m app.naver_local_worker login`을 실행해 전용 Edge 창에서 네이버에 로그인합니다.
4. 로그아웃 후 다시 로그인하면 작업자가 자동 시작됩니다. 즉시 시험하려면 `.venv\Scripts\python.exe -m app.naver_local_worker once`를 실행합니다.

보안 확인·재로그인·캡차, 편집기 구조 변경, 재열람 검증 실패가 감지되면 Notion 상태를 바꾸지 않고 Windows 경고창과 `output/naver-local/attention.json`을 남깁니다.

## 안전 경계

- 정확히 `임시저장`이라고 표시된 버튼만 누릅니다.
- 공개·예약발행 기능은 구현하지 않습니다.
- 임시저장 후 편집기를 새로 불러 제목을 확인한 경우에만 Notion을 `임시저장 완료`로 변경합니다.
- 노트북이 꺼져 있거나 Windows 사용자 세션이 로그인되지 않았으면 대기열은 그대로 남습니다.
