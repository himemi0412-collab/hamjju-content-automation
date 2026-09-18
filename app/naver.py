class NaverDraftAutomationUnavailable(RuntimeError):
    pass


def explain() -> str:
    return (
        '네이버 블로그는 공식 글쓰기 Open API가 제공되지 않아 이 프로젝트는 공개/예약 발행용 자동화를 구현하지 않습니다. '
        '검수된 생성 결과는 Notion의 네이버 저장 요청 상태와 READY_FOR_NAVER_DRAFT 계약으로 넘기며, '
        '로그인 쿠키를 GitHub에 저장하지 않는 기존 로컬 비공개 임시저장 절차가 이어서 처리합니다.'
    )


def save_draft(*args, **kwargs):
    raise NaverDraftAutomationUnavailable(explain())
