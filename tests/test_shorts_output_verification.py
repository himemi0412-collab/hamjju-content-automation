from pathlib import Path
from types import SimpleNamespace

import pytest

from app.notion_client import result_blocks
from app.pipeline import Pipeline


def make_page(status='검토 대기'):
    return {'properties': {
        '제목': {'type': 'title', 'title': [{'plain_text': '확인 영상'}]},
        '상태': {'type': 'select', 'select': {'name': status}},
        '최종 영상': {'type': 'files', 'files': [{'name': 'short.mp4'}]},
    }}


def test_shorts_readback_requires_saved_script_status_and_video():
    generated = {'title': '확인 영상', 'hook': '확인', 'scenes': []}
    blocks = result_blocks('japan_shorts', generated, {'pass': True, 'blocking_issues': []})
    media = {'video': 'output/short.mp4', 'verification': {'pass': True},
             'notion_video_attached': True, 'youtube_url': 'https://www.youtube.com/watch?v=abc'}
    notion = SimpleNamespace(retrieve_page=lambda _: make_page(),
                             read_page_blocks=lambda _: blocks)
    pipeline = object.__new__(Pipeline)
    pipeline.notion = notion
    observed = pipeline._verify_shorts_output('page', '확인 영상', '검토 대기', blocks, media)
    assert observed['body_blocks_verified'] == len(blocks)
    assert observed['video_attachment_verified'] is True
    assert observed['youtube_upload_performed'] is False
    notion.retrieve_page = lambda _: make_page(status='제작중')
    with pytest.raises(RuntimeError, match='status'):
        pipeline._verify_shorts_output('page', '확인 영상', '검토 대기', blocks, media)
    notion.retrieve_page = lambda _: make_page()
    notion.read_page_blocks = lambda _: []
    with pytest.raises(RuntimeError, match='script blocks'):
        pipeline._verify_shorts_output('page', '확인 영상', '검토 대기', blocks, media)


def test_shorts_readback_rejects_unverified_media():
    blocks = result_blocks('japan_shorts', {'title': '확인 영상'}, {'pass': True})
    pipeline = object.__new__(Pipeline)
    pipeline.notion = SimpleNamespace(retrieve_page=lambda _: make_page(),
                                     read_page_blocks=lambda _: blocks)
    with pytest.raises(RuntimeError, match='media verification'):
        pipeline._verify_shorts_output('page', '확인 영상', '검토 대기', blocks,
                                      {'video': 'short.mp4', 'verification': {'pass': False}})
    with pytest.raises(RuntimeError, match='video required'):
        pipeline._verify_shorts_output('page', '확인 영상', '검토 대기', blocks, {}, require_media=True)
