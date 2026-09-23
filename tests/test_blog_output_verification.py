from pathlib import Path
import json
import hashlib
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock
import logging
import httpx

import pytest

from app.ai import AIClient
from app.config import load_channels
from app.pipeline import Pipeline
from app.notion_client import naver_handoff_blocks, result_blocks
from app.settings import Settings
from app.state import StateStore
from app.manuscript_repair import manuscript_hash
from app.blog_reference import load_blog_reference
from app.card_design import apply_named_design_contract


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
        return [f'upload-{p.name}' for p in paths]

    def append_blocks(self, page_id, blocks):
        self.blocks.extend({**block, 'id': f'block-{len(self.blocks)}-{i}'} for i, block in enumerate(blocks))

    def archive_blocks(self, ids):
        self.blocks = [block for block in self.blocks if block['id'] not in ids]


def make_pipeline(tmp_path, monkeypatch, visual_pass=True):
    notion = FakeNotion()
    ai = Mock()
    generated = apply_named_design_contract({
        'reference_profile_id': 'HAMZZU_NAVER_REFERENCE_V1',
        'card_format': 'square',
        'visual_family': 'playful_diagram',
        'design_language': 'Swiss Typography',
        'title': '검증 원고',
        'body_markdown': '도입 문장\n\n## 구간 1\n본문 전체',
        'hashtags': ['#검증'],
        'card_news': [
            {
                'card': i,
                'caption': f'카드 {i} 설명',
                'ai_disclosure': 'AI가 정리한 정보를 코드로 조판한 설명 도식입니다.',
            }
            for i in range(1, 6)
        ],
        'image_placements': [
            {'card': 1, 'after_heading': '도입'},
            *[{'card': i, 'after_heading': '구간 1'} for i in range(2, 6)],
        ],
    })
    ai.generate.return_value = (generated, {})
    ai.qa.return_value = ({'pass': visual_pass, 'ai_likeness_score': 2, 'blocking_issues': [] if visual_pass else ['card 3 overlaps']}, {})
    paths = [tmp_path / f'card_{i:02d}.png' for i in range(1, 6)]
    for i, path in enumerate(paths):
        path.write_bytes(f'image-{i}'.encode())
    monkeypatch.setattr('app.pipeline.render_blog_cards', lambda *args, **kwargs: paths)
    monkeypatch.setattr('app.pipeline.generate_and_typeset_blog_cards', lambda *args, **kwargs: paths)
    monkeypatch.setattr('app.pipeline.httpx.get', lambda url, **kwargs: SimpleNamespace(content=notion.paths[url].read_bytes(), raise_for_status=lambda: None))
    settings = Settings(_env_file=None, output_dir=tmp_path / 'output')
    state = StateStore(tmp_path / 'state.db')
    return Pipeline(settings, notion, ai, state), paths


def test_success_requires_visual_qa_and_exact_remote_card_bytes(tmp_path, monkeypatch):
    pipeline, paths = make_pipeline(tmp_path, monkeypatch)
    result = pipeline.process_page(load_channels()['naver_blog'], {'id': 'one'})
    assert result['qa_pass'] is True
    assert result['output_verified'] is True
    assert result['status'] == '네이버 저장 요청'
    assert result['media']['rendered_card_qa_pass'] is True
    assert pipeline.ai.qa.call_args.kwargs['image_paths'] == paths
    qa_context = pipeline.ai.qa.call_args.args[1]
    assert qa_context['reference_baseline']['id'] == 'HAMZZU_NAVER_REFERENCE_V1'
    assert len(qa_context['reference_baseline']['source_urls']) == 4
    assert (tmp_path / 'output' / 'one' / 'notion-readback.json').exists()


def test_missing_reference_acknowledgement_fails_before_handoff(tmp_path, monkeypatch):
    pipeline, _ = make_pipeline(tmp_path, monkeypatch)
    pipeline.ai.generate.return_value[0].pop('reference_profile_id')

    result = pipeline.process_page(load_channels()['naver_blog'], {'id': 'one'})

    assert result['status'] == 'failed'
    assert 'REFERENCE_PROFILE_MISSING' in result['error']
    pipeline.ai.qa.assert_not_called()
    assert pipeline.notion.status != '네이버 저장 요청'


def test_naver_handoff_has_one_ready_contract_and_five_ordered_images():
    generated = {
        'body_markdown': '도입 문장\n\n## 첫 구간\n본문 **강조**와 [출처](https://example.com)',
        'hashtags': ['#하나', '#둘'],
        'card_news': [
            {'card': i, 'caption': f'카드 {i} 캡션', 'ai_disclosure': 'AI 설명 도식입니다.'}
            for i in range(1, 6)
        ],
        'image_placements': [
            {'card': 1, 'after_heading': '도입'},
            *[{'card': i, 'after_heading': '첫 구간'} for i in range(2, 6)],
        ],
    }
    blocks = naver_handoff_blocks(
        generated,
        {'pass': True, 'ai_likeness_score': 2, 'score': 96, 'blocking_issues': []},
        document_id='page-id',
        source_version='sha256-version',
        card_upload_ids=[f'upload-{i}' for i in range(1, 6)],
        card_names=[f'card_{i:02d}.png' for i in range(1, 6)],
        ownership_receipt={
            'contract_id': 'HAMZZU_OPERATING_CONTRACT_V1',
            'producer_owner': 'github_actions',
            'delivery_owner': 'codex_automation_3',
            'publication_owner': 'user',
            'stage': 'HANDOFF_READY',
            'source_version': 'sha256-version',
        },
    )
    texts = [
        ''.join(x.get('text', {}).get('content', '') for x in block.get(block['type'], {}).get('rich_text', []))
        for block in blocks
    ]
    assert texts.count('저장 준비 완료: READY_FOR_NAVER_DRAFT') == 1
    assert texts.count('네이버 본문 시작') == 1
    assert texts.count('네이버 본문 끝') == 1
    assert texts.index('네이버 본문 시작') < texts.index('네이버 본문 끝')
    assert sum(block['type'] == 'image' for block in blocks) == 5
    assert '#하나 #둘' in texts
    assert all('발행 금지' in text for text in texts if text.startswith('네이버 임시저장만 허용'))
    assert '운영 계약: HAMZZU_OPERATING_CONTRACT_V1' in texts
    assert '제작 소유자: github_actions' in texts
    assert '임시저장 소유자: codex_automation_3' in texts
    assert '공개 결정 소유자: user' in texts


def test_last_section_card_stays_before_paragraph_targeted_decision_card():
    generated = {
        'body_markdown': '도입\n\n## 구매 전 확인\n체크 본문\n\n정리하면 결론입니다.',
        'card_news': [
            {'card': i, 'caption': f'카드 {i} 캡션', 'ai_disclosure': 'AI 설명 도식입니다.'}
            for i in range(1, 6)
        ],
        'image_placements': [
            {'card': 1, 'after_heading': '도입'},
            {'card': 2, 'after_heading': '도입'},
            {'card': 3, 'after_heading': '도입'},
            {'card': 4, 'after_heading': '구매 전 확인'},
            {'card': 5, 'after_heading': '정리하면'},
        ],
    }

    blocks = naver_handoff_blocks(
        generated,
        {'pass': True, 'ai_likeness_score': 2, 'score': 96, 'blocking_issues': []},
        document_id='page-id',
        source_version='sha256-version',
        card_upload_ids=[f'upload-{i}' for i in range(1, 6)],
        card_names=[f'card_{i:02d}.png' for i in range(1, 6)],
    )

    observed_uploads = [
        block['image']['file_upload']['id']
        for block in blocks if block['type'] == 'image'
    ]
    assert observed_uploads == [f'upload-{i}' for i in range(1, 6)]


def test_legacy_last_section_order_can_be_reconstructed_for_exact_resume_matching():
    generated = {
        'body_markdown': '도입\n\n## 구매 전 확인\n체크 본문\n\n정리하면 결론입니다.',
        'card_news': [
            {'card': i, 'caption': f'카드 {i} 캡션', 'ai_disclosure': 'AI 설명 도식입니다.'}
            for i in range(1, 6)
        ],
        'image_placements': [
            {'card': 1, 'after_heading': '도입'},
            {'card': 2, 'after_heading': '도입'},
            {'card': 3, 'after_heading': '도입'},
            {'card': 4, 'after_heading': '구매 전 확인'},
            {'card': 5, 'after_heading': '정리하면'},
        ],
    }

    blocks = naver_handoff_blocks(
        generated,
        {'pass': True, 'ai_likeness_score': 2, 'score': 96, 'blocking_issues': []},
        document_id='page-id',
        source_version='sha256-version',
        card_upload_ids=[f'upload-{i}' for i in range(1, 6)],
        card_names=[f'card_{i:02d}.png' for i in range(1, 6)],
        _legacy_paragraph_order=True,
    )

    observed_uploads = [
        block['image']['file_upload']['id']
        for block in blocks if block['type'] == 'image'
    ]
    assert observed_uploads == ['upload-1', 'upload-2', 'upload-3', 'upload-5', 'upload-4']


def test_visual_rejection_is_preserved_and_not_a_state_success(tmp_path, monkeypatch):
    pipeline, _ = make_pipeline(tmp_path, monkeypatch, visual_pass=False)
    result = pipeline.process_page(load_channels()['naver_blog'], {'id': 'one'})
    assert result['status'] == '수정 필요'
    assert result['qa_pass'] is False
    assert result['blocking_issues'] == ['card 3 overlaps']
    assert result['output_verified'] is False
    assert result['media']['notion_cards_attached'] is False
    assert pipeline.notion.files == []
    assert pipeline.notion.blocks == []
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
    assert [x['status'] for x in results] == ['failed', '네이버 저장 요청']


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
        upload_ids = original_attach(*args)
        for item in pipeline.notion.files:
            original = item['file']['url']
            signed = original + '?signature=private-value'
            item['file']['url'] = signed
            pipeline.notion.paths[signed] = pipeline.notion.paths[original]
        return upload_ids
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


def test_resume_reuses_pass_for_identical_reviewed_manuscript_and_card_bytes(tmp_path, monkeypatch):
    pipeline, cards = make_pipeline(tmp_path, monkeypatch)
    generated = pipeline.ai.generate.return_value[0]
    prior_qa = {'pass': True, 'ai_likeness_score': 2, 'score': 94, 'blocking_issues': [], 'recommended_status': 'PASS'}
    prior = {
        'page_id': 'one', 'channel': 'naver_blog', 'generated': generated, 'qa': prior_qa,
        'reference_baseline_id': load_blog_reference()['id'],
        'reference_baseline_sha256': load_blog_reference()['sha256'],
        'media': {
            'cards': [str(p) for p in cards],
            'rendered_card_qa_pass': True,
        },
        'manuscript_sha256': manuscript_hash(generated),
    }
    saved_cards = tmp_path / 'cards'
    saved_cards.mkdir()
    for card in cards:
        (saved_cards / card.name).write_bytes(card.read_bytes())
    manifest = tmp_path / 'previous.json'
    manifest.write_text(json.dumps(prior), encoding='utf-8')
    pipeline.notion.status = 'CODEX_HANDOFF_READY'
    pipeline.notion.append_blocks('one', result_blocks('naver_blog', generated, prior_qa))
    pipeline.notion.attach_files('one', '생성 이미지', cards)
    cfg = replace(load_channels()['naver_blog'], ready_status='CODEX_HANDOFF_READY')

    result = pipeline.process_page(cfg, {'id': 'one'}, resume_manifest=manifest)

    assert result['qa_pass'] is True and result['status'] == '네이버 저장 요청'
    pipeline.ai.qa.assert_not_called()
    written = json.loads((tmp_path / 'output/one/manifest.json').read_text(encoding='utf-8'))
    assert written['qa'] == prior_qa
    assert written['usage']['rendered_card_qa']['reused_from_manifest'] is True


def test_repeated_resume_restores_pass_only_from_identical_original_review(tmp_path, monkeypatch):
    pipeline, cards = make_pipeline(tmp_path, monkeypatch)
    generated = pipeline.ai.generate.return_value[0]
    current = {
        'page_id': 'one', 'channel': 'naver_blog', 'generated': generated,
        'qa': {'pass': False, 'blocking_issues': ['stochastic second review']},
        'media': {'cards': [str(p) for p in cards], 'rendered_card_qa_pass': False},
    }
    reviewed = {
        'page_id': 'one', 'channel': 'naver_blog', 'generated': generated,
        'qa': {'pass': True, 'ai_likeness_score': 2, 'score': 94, 'blocking_issues': []},
        'reference_baseline_id': load_blog_reference()['id'],
        'reference_baseline_sha256': load_blog_reference()['sha256'],
        'media': {'cards': [str(p) for p in cards], 'rendered_card_qa_pass': True},
    }
    current_dir = tmp_path / 'current'
    reviewed_dir = tmp_path / 'reviewed'
    for root, payload in ((current_dir, current), (reviewed_dir, reviewed)):
        (root / 'cards').mkdir(parents=True)
        for card in cards:
            (root / 'cards' / card.name).write_bytes(card.read_bytes())
        (root / 'manifest.json').write_text(json.dumps(payload), encoding='utf-8')
    pipeline.notion.status = '수정 필요'
    pipeline.notion.append_blocks('one', result_blocks('naver_blog', generated, current['qa']))
    pipeline.notion.attach_files('one', '생성 이미지', cards)
    cfg = replace(load_channels()['naver_blog'], ready_status='수정 필요')

    result = pipeline.process_page(
        cfg, {'id': 'one'}, resume_manifest=current_dir / 'manifest.json',
        reviewed_manifest=reviewed_dir / 'manifest.json',
    )

    assert result['qa_pass'] is True and result['status'] == '네이버 저장 요청'
    pipeline.ai.qa.assert_not_called()


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


def test_resume_preserves_user_planning_prefix_and_replaces_only_machine_suffix(tmp_path, monkeypatch):
    pipeline, cards = make_pipeline(tmp_path, monkeypatch)
    generated = pipeline.ai.generate.return_value[0]
    prior_qa = {'pass': False, 'blocking_issues': ['old visual review']}
    prior = {
        'page_id': 'one', 'channel': 'naver_blog', 'generated': generated,
        'qa': prior_qa, 'media': {'cards': [str(p) for p in cards]},
    }
    saved_cards = tmp_path / 'cards'
    saved_cards.mkdir()
    for card in cards:
        (saved_cards / card.name).write_bytes(card.read_bytes())
    manifest = tmp_path / 'previous.json'
    manifest.write_text(json.dumps(prior), encoding='utf-8')
    planning = {
        'type': 'paragraph',
        'paragraph': {'rich_text': [{'plain_text': '사용자 기획 메모'}]},
        'id': 'planning-prefix',
    }
    pipeline.notion.blocks.append(planning)
    pipeline.notion.append_blocks('one', result_blocks('naver_blog', generated, prior_qa))
    pipeline.notion.attach_files('one', '생성 이미지', cards)
    pipeline.notion.status = '수정 필요'
    cfg = replace(load_channels()['naver_blog'], ready_status='수정 필요')

    result = pipeline.process_page(cfg, {'id': 'one'}, resume_manifest=manifest)

    assert result['qa_pass'] is True and result['output_verified'] is True
    assert pipeline.notion.blocks[0] == planning
    assert sum(block.get('id') == 'planning-prefix' for block in pipeline.notion.blocks) == 1


@pytest.mark.parametrize('changed', [False, True])
def test_resume_checks_attached_bytes_even_when_filenames_match(tmp_path, monkeypatch, changed):
    pipeline, cards = make_pipeline(tmp_path, monkeypatch)
    generated = pipeline.ai.generate.return_value[0]
    prior = {'page_id': 'one', 'channel': 'naver_blog', 'generated': generated,
             'qa': {'pass': False}, 'media': {'cards': [str(p) for p in cards]}}
    saved_cards = tmp_path / 'cards'
    saved_cards.mkdir()
    for card in cards:
        (saved_cards / card.name).write_bytes(card.read_bytes())
    manifest = tmp_path / 'previous.json'
    manifest.write_text(json.dumps(prior), encoding='utf-8')
    pipeline.notion.status = '수정 필요'
    pipeline.notion.append_blocks('one', result_blocks('naver_blog', generated, prior['qa']))
    pipeline.notion.attach_files('one', '생성 이미지', cards)
    if changed:
        cards[2].write_bytes(b'user-edited-card-under-original-name')
    cfg = replace(load_channels()['naver_blog'], ready_status='수정 필요')
    result = pipeline.process_page(cfg, {'id': 'one'}, resume_manifest=manifest)
    if changed:
        assert result['status'] == 'failed' and 'MANUAL_EDIT_CONFLICT' in result['error']
        pipeline.ai.qa.assert_not_called()
        assert pipeline.notion.status == '수정 필요'
    else:
        assert result['output_verified'] is True
    pipeline.ai.generate.assert_not_called()


@pytest.mark.parametrize('receipt_valid', [True, False])
def test_same_name_interrupted_upload_requires_source_bound_receipt(tmp_path, monkeypatch, receipt_valid):
    pipeline, cards = make_pipeline(tmp_path, monkeypatch)
    generated = pipeline.ai.generate.return_value[0]
    prior = {'page_id': 'one', 'channel': 'naver_blog', 'generated': generated,
             'qa': {'pass': False}, 'media': {'cards': [str(p) for p in cards]}}
    saved = tmp_path / 'cards'
    saved.mkdir()
    for card in cards:
        (saved / card.name).write_bytes(card.read_bytes())
    cards[2].write_bytes(b'reviewed-interrupted-upload')
    pipeline.notion.status = '수정 필요'
    pipeline.notion.append_blocks('one', result_blocks('naver_blog', generated, prior['qa']))
    pipeline.notion.attach_files('one', '생성 이미지', cards)
    source = tmp_path / 'previous.json'
    source.write_text(json.dumps(prior), encoding='utf-8')
    repairs = tmp_path / 'repairs'
    repairs.mkdir()
    (repairs / 'one.json').write_text(json.dumps({
        'page_id': 'one', 'source_sha256': manuscript_hash(generated) if receipt_valid else 'wrong-source',
        'attached_cards': [{'name': p.name, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in cards],
    }), encoding='utf-8')
    cfg = replace(load_channels()['naver_blog'], ready_status='수정 필요')
    monkeypatch.chdir(tmp_path)
    result = pipeline.process_page(cfg, {'id': 'one'}, resume_manifest=source)
    if receipt_valid:
        assert result['output_verified'] is True
        written = json.loads((tmp_path / 'output/one/manifest.json').read_text(encoding='utf-8'))
        assert written['resume_source_manuscript_sha256'] == manuscript_hash(generated)
    else:
        assert result['status'] == 'failed'
        pipeline.ai.qa.assert_not_called()


def test_resume_rechecks_image_bytes_after_qa_before_overwriting(tmp_path, monkeypatch):
    pipeline, cards = make_pipeline(tmp_path, monkeypatch)
    generated = pipeline.ai.generate.return_value[0]
    prior = {'page_id': 'one', 'channel': 'naver_blog', 'generated': generated,
             'qa': {'pass': False}, 'media': {'cards': [str(p) for p in cards]}}
    saved = tmp_path / 'cards'
    saved.mkdir()
    for card in cards:
        (saved / card.name).write_bytes(card.read_bytes())
    pipeline.notion.status = '수정 필요'
    pipeline.notion.append_blocks('one', result_blocks('naver_blog', generated, prior['qa']))
    pipeline.notion.attach_files('one', '생성 이미지', cards)
    pipeline.notion.attach_files = Mock()
    def edit_during_qa(*args, **kwargs):
        cards[2].write_bytes(b'manual-edit-during-review')
        return {'pass': True, 'ai_likeness_score': 2}, {}
    pipeline.ai.qa.side_effect = edit_during_qa
    source = tmp_path / 'previous.json'
    source.write_text(json.dumps(prior), encoding='utf-8')
    cfg = replace(load_channels()['naver_blog'], ready_status='수정 필요')
    result = pipeline.process_page(cfg, {'id': 'one'}, resume_manifest=source)
    assert result['status'] == 'failed'
    pipeline.notion.attach_files.assert_not_called()
    assert cards[2].read_bytes() == b'manual-edit-during-review'
