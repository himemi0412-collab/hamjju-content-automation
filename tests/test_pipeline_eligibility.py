from dataclasses import replace

from app.config import load_channels
from app.pipeline import page_is_eligible
from app.pipeline import page_is_manual_priority


def blog_context(**overrides):
    properties = {
        '상태': '작성 요청',
        '목록 구분': '다음 5편',
        '선별 상태': '추천',
        '모델 확인': '공식 확인',
        '진행 순서': 4,
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


def test_blog_item_without_positive_work_order_is_not_eligible():
    cfg = load_channels()['naver_blog']
    assert not page_is_eligible(cfg, blog_context(**{'진행 순서': 0}))
    assert not page_is_eligible(cfg, blog_context(**{'진행 순서': None}))
    assert not page_is_eligible(cfg, blog_context(**{'진행 순서': True}))


def test_shorts_channel_is_rechecked_before_processing():
    cfg = load_channels()['ppojjugi_shorts']
    assert page_is_eligible(cfg, {
        'properties': {'상태': '작성 요청', '채널': '햄찌 창작 쇼츠'},
    })
    assert not page_is_eligible(cfg, {
        'properties': {'상태': '작성 요청', '채널': '일본 유튜브 쇼츠'},
    })


def test_revision_short_is_only_eligible_for_explicit_retry():
    cfg = load_channels()['japan_shorts']
    context = {
        'properties': {'상태': '수정 필요', '채널': '일본 유튜브 쇼츠'},
    }
    assert not page_is_eligible(cfg, context)
    retry_cfg = replace(cfg, ready_status=cfg.revision_status)
    assert page_is_eligible(retry_cfg, context)


def test_direct_user_topic_is_manual_priority():
    assert page_is_manual_priority({'properties': {
        '키워드 출처': {'type': 'select', 'select': {'name': '직접 입력'}},
    }})
    assert page_is_manual_priority({'properties': {
        '다음 행동': {'type': 'rich_text', 'rich_text': [{'plain_text': '사용자 직접 추가 · 우선 제작'}]},
    }})
    assert not page_is_manual_priority({'properties': {
        '키워드 출처': {'type': 'select', 'select': {'name': '계절·시기'}},
    }})
