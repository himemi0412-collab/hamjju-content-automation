from app.config import load_channels
from app.pipeline import page_is_eligible


def blog_context(**overrides):
    properties = {
        '상태': '작성 요청',
        '목록 구분': '다음 5편',
        '선별 상태': '추천',
        '모델 확인': '공식 확인',
    }
    properties.update(overrides)
    return {'properties': properties}


def test_reviewed_blog_item_is_eligible():
    cfg = load_channels()['naver_blog']
    assert page_is_eligible(cfg, blog_context())


def test_held_blog_item_is_not_eligible():
    cfg = load_channels()['naver_blog']
    assert not page_is_eligible(cfg, blog_context(**{'선별 상태': '보류'}))


def test_archived_or_changed_status_blog_item_is_not_eligible():
    cfg = load_channels()['naver_blog']
    assert not page_is_eligible(cfg, blog_context(**{'목록 구분': '이전 주제 보관'}))
    assert not page_is_eligible(cfg, blog_context(**{'상태': '생성 중'}))


def test_shorts_channel_is_rechecked_before_processing():
    cfg = load_channels()['ppojjugi_shorts']
    assert page_is_eligible(cfg, {
        'properties': {'상태': '작성 요청', '채널': '햄찌 창작 쇼츠'},
    })
    assert not page_is_eligible(cfg, {
        'properties': {'상태': '작성 요청', '채널': '일본 유튜브 쇼츠'},
    })
