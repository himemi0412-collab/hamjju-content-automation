from pathlib import Path
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock
import logging
import httpx

import pytest

from app.ai import AIClient
from app.config import load_channels
from app.pipeline import Pipeline
from app.notion_client import result_blocks
from app.settings import Settings
from app.state import StateStore


class FakeNotion:
    def __init__(self):
        self.status = '작성 요청'
        self.blocks = []
        self.files = []
        self.paths = {}

    def retrieve_page(self, page_id):
        def prop(kind, value):
            return {'type': kind, kind: value}
        return {'id': page_id, 'last_edited_time': 'test-source-version', 'properties': {
            '제목': prop('title', [{'plain_text': '검증 원고'}]),
            '상태': prop('select', {'name': self.status}),
            '목록 구분': prop('formula', {'type': 'string', 'string': '다음 5편'}),
            '선별 상태': prop('select', {'name': '추천'}),
            '모델 확인': prop('select', {'name': '공식 확인'}),
            '진행 순서': prop('number', 1),
            '생성 이미지': prop('files', self.files),
        }}

    def read_page_text(self, page_id):
        return ''

    def read_page_blocks(self, page_id):
        return self.blocks

    def update_status(self, page_id, status):
        self.status = status

    def attach_files(self, page_id, prop, paths):
        self.files = [{'type': 'file', 'name': p.name, 'file': {'url': f'https://asset.invalid/{p.name}'}} for p in paths]
        self.paths = {f'https://asset.invalid/{p.name}': p for p in paths}

    def append_blocks(self, page_id, blocks):
        self.blocks.extend({**block, 'id': f'block-{len(self.blocks)}-{i}'} for i, block in enumerate(blocks))

    def archive_blocks(self, ids):
        self.blocks = [block for block in self.blocks if block['id'] not in ids]


def make_pipeline(tmp_path, monkeypatch, visual_pass=True):
    notion = FakeNotion()
    ai = Mock()
    ai.generate.return_value = ({'title': '검증 원고', 'body_markdown': '본문 전체', 'card_news': [{'card': i} for i in range(1, 6)]}, {})
    ai.qa.return_value = ({'pass': visual_pass, 'blocking_issues': [] if visual_pass else ['card 3 overlaps']}, {})
    paths = [tmp_path / f'card_{i:02d}.png' for i in range(1, 6)]
    for i, path in enumerate(paths):
        path.write_bytes(f'image-{i}'.encode())
    monkeypatch.setattr('app.pipeline.render_blog_cards', lambda *args: paths)
    monkeypatch.setattr('app.pipeline.httpx.get', lambda url, **kwargs: SimpleNamespace(content=notion.paths[url].read_bytes(), raise_for_status=lambda: None))
    settings = Settings(_env_file=None, output_dir=tmp_path / 'output')
    state = StateStore(tmp_path / 'state.db')
    return Pipeline(settings, notion, ai, state), paths


def test_success_requires_visual_qa_and_exact_remote_card_bytes(tmp_path, monkeypatch):
    pipeline, paths = make_pipeline(tmp_path, monkeypatch)
    result = pipeline.process_page(load_channels()['naver_blog'], {'id': 'one'})
    assert result['qa_pass'] is True
    assert result['output_verified'] is True
    assert result['media']['rendered_card_qa_pass'] is True
    assert pipeline.ai.qa.call_args.kwargs['image_paths'] == paths
    assert (tmp_path / 'output' / 'one' / 'notion-readback.json').exists()


def test_visual_rejection_is_preserved_and_not_a_state_success(tmp_path, monkeypatch):
    pipeline, _ = make_pipeline(tmp_path, monkeypatch, visual_pass=False)
    result = pipeline.process_page(load_channels()['naver_blog'], {'id': 'one'})
    assert result['status'] == '수정 필요'
    assert result['qa_pass'] is False
    assert not pipeline.state.succeeded('one:test-source-version:naver_blog:v1')


@pytest.mark.parametrize('mismatch', ['body', 'card_order', 'card_bytes'])
def test_readback_mismatch_never_reports_success(tmp_path, monkeypatch, mismatch):
    pipeline, _ = make_pipeline(tmp_path, monkeypatch)
    if mismatch == 'body':
        pipeline.notion.read_page_blocks = lambda _: []
    elif mismatch == 'card_order':
        original = pipeline.notion.attach_files
        def attach(*args):
            original(*args)
            pipeline.notion.files.reverse()
        pipeline.notion.attach_files = attach
    else:
        monkeypatch.setattr('app.pipeline.httpx.get', lambda *args, **kwargs: SimpleNamespace(content=b'wrong-image', raise_for_status=lambda: None))
    result = pipeline.process_page(load_channels()['naver_blog'], {'id': 'one'})
    assert result['status'] == 'failed'
    assert pipeline.notion.status == '수정 필요'


def test_read_failure_does_not_abort_next_page(tmp_path, monkeypatch):
    pipeline, _ = make_pipeline(tmp_path, monkeypatch)
    pipeline.notion.query_ready = lambda *args, **kwargs: [{'id': 'bad'}, {'id': 'good'}]
    original = pipeline.notion.retrieve_page
    def retrieve(page_id):
        if page_id == 'bad':
            raise RuntimeError('Notion temporarily unavailable')
        return original(page_id)
    pipeline.notion.retrieve_page = retrieve
    results = pipeline.run_channel(load_channels()['naver_blog'], limit=2)
    assert [x['status'] for x in results] == ['failed', 'CODEX_HANDOFF_READY']


def test_image_qa_sends_actual_png_and_uses_budgeted_response_path(tmp_path):
    client = AIClient('test-key', 'gpt-5.6-luna', 'gpt-5.6-luna')
    client._create_response = Mock(return_value=SimpleNamespace(output_text='{"pass":true}', usage=None))
    path = tmp_path / 'card.png'
    path.write_bytes(b'png-content')
    client.qa({}, {}, image_paths=[path])
    request, category, uses_web = client._create_response.call_args.args
    content = request['input'][1]['content']
    assert content[-1]['image_url'] == 'data:image/png;base64,cG5nLWNvbnRlbnQ='
    assert category == 'rendered_card_qa'
    assert uses_web is False


def test_successful_readback_does_not_log_signed_asset_urls(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    caplog.set_level(logging.INFO, logger='httpx')
    pipeline, _ = make_pipeline(tmp_path, monkeypatch)
    original_attach = pipeline.notion.attach_files
    def attach(*args):
        original_attach(*args)
        for item in pipeline.notion.files:
            original = item['file']['url']
            signed = original + '?signature=private-value'
            item['file']['url'] = signed
            pipeline.notion.paths[signed] = pipeline.notion.paths[original]
    pipeline.notion.attach_files = attach
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=pipeline.notion.paths[str(request.url)].read_bytes()))
    with httpx.Client(transport=transport) as client:
        monkeypatch.setattr('app.pipeline.httpx.get', client.get)
        result = pipeline.process_page(load_channels()['naver_blog'], {'id': 'one'})
    assert result['output_verified'] is True
    assert 'private-value' not in caplog.text
    assert 'asset.invalid' not in caplog.text


def test_resume_reuses_text_and_replaces_only_matching_failed_output(tmp_path, monkeypatch):
    pipeline, _ = make_pipeline(tmp_path, monkeypatch)
    generated = pipeline.ai.generate.return_value[0]
    prior = {'page_id': 'one', 'channel': 'naver_blog', 'generated': generated,
             'qa': {'pass': False, 'blocking_issues': ['missing old style metadata']}}
    manifest = tmp_path / 'previous.json'
    manifest.write_text(json.dumps(prior), encoding='utf-8')
    pipeline.notion.status = '수정 필요'
    pipeline.notion.append_blocks('one', result_blocks('naver_blog', generated, prior['qa']))
    cfg = replace(load_channels()['naver_blog'], ready_status='수정 필요')
    result = pipeline.process_page(cfg, {'id': 'one'}, resume_manifest=manifest)
    assert result['qa_pass'] is True and result['output_verified'] is True
    pipeline.ai.generate.assert_not_called()
    assert sum(x['type'] == 'divider' for x in pipeline.notion.blocks) == 1


def test_resume_does_not_overwrite_manual_edit_or_call_ai(tmp_path, monkeypatch):
    pipeline, _ = make_pipeline(tmp_path, monkeypatch)
    prior = {'page_id': 'one', 'channel': 'naver_blog',
             'generated': pipeline.ai.generate.return_value[0], 'qa': {'pass': False}}
    manifest = tmp_path / 'previous.json'
    manifest.write_text(json.dumps(prior), encoding='utf-8')
    pipeline.notion.status = '수정 필요'
    pipeline.notion.append_blocks('one', result_blocks('naver_blog', prior['generated'], prior['qa']))
    pipeline.notion.blocks.append({'type': 'paragraph', 'paragraph': {'rich_text': [{'plain_text': 'user edit'}]}, 'id': 'manual'})
    cfg = replace(load_channels()['naver_blog'], ready_status='수정 필요')
    result = pipeline.process_page(cfg, {'id': 'one'}, resume_manifest=manifest)
    assert result['status'] == 'failed' and 'MANUAL_EDIT_CONFLICT' in result['error']
    pipeline.ai.generate.assert_not_called()
    pipeline.ai.qa.assert_not_called()
    assert pipeline.notion.blocks[-1]['id'] == 'manual'
