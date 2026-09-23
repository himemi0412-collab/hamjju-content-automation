from pathlib import Path
from types import SimpleNamespace

import pytest

from app.notion_client import result_blocks
from app.pipeline import Pipeline


def make_page(status='비공개 업로드 완료', url='https://www.youtube.com/watch?v=abc'):
    return {'properties': {
        '제목': {'type': 'title', 'title': [{'plain_text': '확인 영상'}]},
        '상태': {'type': 'select', 'select': {'name': status}},
        '최종 영상': {'type': 'files', 'files': [{'name': 'short.mp4'}]},
        'YouTube 비공개 주소': {'type': 'url', 'url': url},
    }}


def test_shorts_readback_requires_saved_script_status_video_and_url():
    generated = {'title': '확인 영상', 'hook': '확인', 'scenes': []}
    blocks = result_blocks('japan_shorts', generated, {'pass': True, 'blocking_issues': []})
    media = {'video': 'output/short.mp4', 'verification': {'pass': True},
             'notion_video_attached': True, 'youtube_url': 'https://www.youtube.com/watch?v=abc'}
    notion = SimpleNamespace(retrieve_page=lambda _: make_page(),
                             read_page_blocks=lambda _: blocks)
    pipeline = object.__new__(Pipeline)
    pipeline.notion = notion
    observed = pipeline._verify_shorts_output('page', '확인 영상', '비공개 업로드 완료', blocks, media)
    assert observed['body_blocks_verified'] == len(blocks)
    assert observed['video_attachment_verified'] is True

    notion.retrieve_page = lambda _: make_page(url='https://www.youtube.com/watch?v=wrong')
    with pytest.raises(RuntimeError, match='YouTube URL'):
        pipeline._verify_shorts_output('page', '확인 영상', '비공개 업로드 완료', blocks, media)
    notion.retrieve_page = lambda _: make_page(status='제작 중')
    with pytest.raises(RuntimeError, match='status'):
        pipeline._verify_shorts_output('page', '확인 영상', '비공개 업로드 완료', blocks, media)
    notion.retrieve_page = lambda _: make_page()
    notion.read_page_blocks = lambda _: []
    with pytest.raises(RuntimeError, match='script blocks'):
        pipeline._verify_shorts_output('page', '확인 영상', '비공개 업로드 완료', blocks, media)


def test_shorts_readback_rejects_unverified_media():
    blocks = result_blocks('japan_shorts', {'title': '확인 영상'}, {'pass': True})
    pipeline = object.__new__(Pipeline)
    pipeline.notion = SimpleNamespace(retrieve_page=lambda _: make_page(),
                                     read_page_blocks=lambda _: blocks)
    with pytest.raises(RuntimeError, match='media verification'):
        pipeline._verify_shorts_output('page', '확인 영상', '비공개 업로드 완료', blocks,
                                      {'video': 'short.mp4', 'verification': {'pass': False}})


def test_scheduled_shorts_cannot_pass_without_required_video_and_upload():
    blocks = result_blocks('japan_shorts', {'title': '확인 영상'}, {'pass': True})
    pipeline = object.__new__(Pipeline)
    pipeline.notion = SimpleNamespace(retrieve_page=lambda _: make_page(),
                                     read_page_blocks=lambda _: blocks)
    with pytest.raises(RuntimeError, match='video required'):
        pipeline._verify_shorts_output('page', '확인 영상', '비공개 업로드 완료', blocks, {}, require_media=True)
    with pytest.raises(RuntimeError, match='YouTube upload required'):
        pipeline._verify_shorts_output('page', '확인 영상', '비공개 업로드 완료', blocks,
                                      {'video': 'short.mp4', 'verification': {'pass': True}},
                                      require_media=True, require_youtube=True)
