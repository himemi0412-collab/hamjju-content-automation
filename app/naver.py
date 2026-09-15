class NaverDraftAutomationUnavailable(RuntimeError):
    pass


def explain() -> str:
    return (
        '네이버 블로그는 공식 글쓰기 Open API가 제공되지 않아 이 프로젝트는 공개/예약 발행용 자동화를 구현하지 않습니다. '
        '생성 결과를 Notion의 CODEX_HANDOFF_READY 상태로 넘겨 기존 비공개 임시저장 절차와 연결하세요.'
    )


def save_draft(*args, **kwargs):
    raise NaverDraftAutomationUnavailable(explain())
