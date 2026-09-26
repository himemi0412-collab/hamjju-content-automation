import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

from app.ai import QAResponseParseError
from app.manuscript_repair import manuscript_hash
from app.naver_revalidation import (
    READY_STATUS, REVISION_STATUS, _qa_exception, revalidate_existing_naver_page,
)
from app.ownership import load_operating_contract


def _generated():
    return {
        'title': '확인용 제목',
        'body_markdown': '검증된 최종 본문입니다.\n\n## 공식 확인 출처\nhttps://example.org/source (확인일: 2026-09-25)',
        'card_news': [{'card': i, 'headline': f'헤드라인 {i}', 'copy': f'설명 {i}'} for i in range(1, 6)],
        'image_placements': [{'card': i, 'visual': f'장면 {i}'} for i in range(1, 6)],
    }


def _passing_cards():
    return [
        {'card_number': index, 'pass': True, 'failures': []}
        for index in range(1, 6)
    ]


class FakeNotion:
    def __init__(self, generated, image_urls):
        self.generated = copy.deepcopy(generated)
        self.page_data = {
            'last_edited_time': '2026-09-25T00:00:00Z',
            'properties': {
                '상태': {'type': 'select', 'select': {'name': REVISION_STATUS}},
                '제목': {'type': 'title', 'title': [{'plain_text': generated['title']}]},
                '생성 이미지': {
                    'files': [
                        {'name': f'card-{i}.png', 'type': 'external', 'external': {'url': url}}
                        for i, url in enumerate(image_urls, 1)
                    ],
                },
            },
        }
        self.snapshot = {'generated': self.generated}
        self.blocks = []

    def retrieve_page(self, _page_id):
        return copy.deepcopy(self.page_data)

    def read_latest_json_snapshot(self, _page_id):
        return copy.deepcopy(self.snapshot)

    def append_blocks(self, _page_id, blocks):
        self.blocks.extend(blocks)
        raw = ''.join(
            text.get('plain_text') or (text.get('text') or {}).get('content', '')
            for block in blocks if block.get('type') == 'code'
            for text in block['code']['rich_text']
        )
        self.snapshot = json.loads(raw)

    def update_status(self, _page_id, status):
        self.page_data['properties']['상태']['select']['name'] = status


def _setup(monkeypatch, tmp_path, content_qa=None, card_qa=None):
    generated = _generated()
    page_id = '3e015789-1194-81c8-afff-dd563a39984c'
    urls = [f'https://cards.example/{i}' for i in range(1, 6)]
    image_bytes = {url: f'png-{i}'.encode() for i, url in enumerate(urls, 1)}

    def get(url, **_kwargs):
        request = httpx.Request('GET', url)
        return httpx.Response(200, content=image_bytes[url], request=request)

    monkeypatch.setattr('app.naver_revalidation.httpx.get', get)
    ai = Mock()
    ai.qa.side_effect = [
        (content_qa or {'pass': True, 'blocking_issues': [], 'score': 94}, {}),
        (card_qa or {
            'pass': True, 'blocking_issues': [], 'ai_likeness_score': 3, 'score': 95,
            'card_results': _passing_cards(), 'failure_details': [],
        }, {}),
    ]
    notion = FakeNotion(generated, urls)
    result = revalidate_existing_naver_page(
        page_id,
        notion=notion,
        ai=ai,
        operating_contract=load_operating_contract(),
        output_dir=tmp_path,
        qa_run_id='fresh-run-1',
    )
    return result, notion, ai, generated, image_bytes


def test_revalidation_runs_independent_manuscript_and_five_card_qa(monkeypatch, tmp_path):
    result, notion, ai, generated, images = _setup(monkeypatch, tmp_path)

    assert result['qa_pass'] is True
    assert result['manuscript_qa_pass'] is True
    assert result['card_qa_pass'] is True
    assert result['notion_status'] == READY_STATUS
    assert [call.kwargs['qa_prompt'] for call in ai.qa.call_args_list] == [
        'prompts/qa.md', 'prompts/qa_photographic_blog_cards.md',
    ]
    assert len(ai.qa.call_args_list[0].kwargs.get('image_paths') or []) == 0
    assert len(ai.qa.call_args_list[1].kwargs['image_paths']) == 5

    digest = manuscript_hash(generated)
    assert result['content_sha256'] == digest
    assert result['run_id'] == 'fresh-run-1'
    assert notion.snapshot['handoff']['page_id'] == '3e015789-1194-81c8-afff-dd563a39984c'
    assert notion.snapshot['handoff']['run_id'] == 'fresh-run-1'
    assert notion.snapshot['qa']['source_content_sha256'] == digest
    assert notion.snapshot['handoff']['card_sha256s'] == [
        hashlib.sha256(images[url]).hexdigest() for url in images
    ]
    report = json.loads((tmp_path / 'naver-revalidation' / result['page_id'] / result['run_id'] / 'qa-report.json').read_text(encoding='utf-8'))
    assert report['page_id'] == result['page_id']
    assert report['run_id'] == result['run_id']
    assert report['overall_status'] == 'PASS'
    assert [row['status'] for row in report['cards']] == ['PASS'] * 5
    assert report['card_qa_detail']['ai_likeness_score'] == 3
    assert all(row['qa_started_at'] and row['qa_finished_at'] for row in report['cards'])


def test_failed_manuscript_qa_still_runs_card_qa_and_does_not_handoff(monkeypatch, tmp_path):
    result, notion, ai, *_ = _setup(
        monkeypatch, tmp_path,
        content_qa={'pass': False, 'blocking_issues': ['content issue']},
    )

    assert ai.qa.call_count == 2
    assert result['qa_pass'] is False
    assert result['manuscript_qa_pass'] is False
    assert result['card_qa_pass'] is True
    assert result['handoff_written'] is False
    assert result['manuscript_failures'][0]['rule_id'] == 'UNCLASSIFIED_CONTENT_RULE'
    assert result['manuscript_failures'][0]['reason'] == 'content issue'
    failed_report = json.loads(Path(result['report_path']).read_text(encoding='utf-8'))
    assert failed_report['manuscript_hash_after_qa'] is None
    assert failed_report['card_hashes_after_qa'] is None
    assert failed_report['overall_status'] == 'FAIL'
    assert notion.blocks == []
    assert notion.page_data['properties']['상태']['select']['name'] == REVISION_STATUS


def test_failed_card_qa_does_not_handoff(monkeypatch, tmp_path):
    result, notion, ai, *_ = _setup(
        monkeypatch, tmp_path,
        card_qa={
            'pass': False, 'blocking_issues': ['card 4 text clipped'], 'ai_likeness_score': 2,
            'card_results': [
                {'card_number': index, 'pass': index != 4, 'failures': (
                    [{'rule_id': 'TEXT_RENDERING', 'reason': '본문 문구가 잘렸습니다.', 'retryable': False,
                      'recommended_fix': '카드 4의 본문 영역만 조정합니다.'}] if index == 4 else []
                )}
                for index in range(1, 6)
            ],
        },
    )

    assert ai.qa.call_count == 2
    assert result['qa_pass'] is False
    assert result['manuscript_qa_pass'] is True
    assert result['card_qa_pass'] is False
    assert result['handoff_written'] is False
    assert [row['status'] for row in result['card_results']] == ['PASS', 'PASS', 'PASS', 'FAIL', 'PASS']
    assert result['card_results'][3]['failures'][0]['rule_id'] == 'TEXT_RENDERING'
    failed_report = json.loads(Path(result['report_path']).read_text(encoding='utf-8'))
    assert failed_report['card_qa_detail']['blocking_issues'] == ['card 4 text clipped']
    assert failed_report['card_qa_detail']['ai_likeness_score'] == 2
    assert failed_report['cards'][3]['failures'][0]['reason'] == '본문 문구가 잘렸습니다.'
    assert notion.blocks == []
    assert notion.page_data['properties']['상태']['select']['name'] == REVISION_STATUS


def test_revalidation_rejects_manuscript_changed_by_qa(monkeypatch, tmp_path):
    generated = _generated()
    page_id = '3e015789-1194-81c8-afff-dd563a39984c'
    urls = [f'https://cards.example/{i}' for i in range(1, 6)]
    notion = FakeNotion(generated, urls)

    def get(url, **_kwargs):
        return httpx.Response(200, content=url.encode(), request=httpx.Request('GET', url))

    monkeypatch.setattr('app.naver_revalidation.httpx.get', get)
    ai = Mock()

    def edit_during_content_qa(generated_arg, *_args, **_kwargs):
        generated_arg['body_markdown'] += '\n수정됨'
        return {'pass': True, 'blocking_issues': []}, {}

    ai.qa.side_effect = edit_during_content_qa
    with pytest.raises(RuntimeError, match='STALE_QA'):
        revalidate_existing_naver_page(
            page_id, notion=notion, ai=ai,
            operating_contract=load_operating_contract(), output_dir=tmp_path,
            qa_run_id='fresh-run-2',
        )
    assert ai.qa.call_count == 1
    assert notion.blocks == []
    assert notion.page_data['properties']['상태']['select']['name'] == REVISION_STATUS


def test_parse_failure_is_saved_separately_from_content_failure(monkeypatch, tmp_path):
    monkeypatch.setattr('app.naver_revalidation.httpx.get', lambda url, **kw: httpx.Response(
        200, content=url.encode(), request=httpx.Request('GET', url),
    ))
    ai = Mock()
    ai.qa.side_effect = [
        QAResponseParseError('invalid QA JSON'),
        ({
            'pass': True, 'blocking_issues': [], 'ai_likeness_score': 1,
            'card_results': _passing_cards(),
        }, {}),
    ]
    notion = FakeNotion(_generated(), [f'https://cards.example/{i}' for i in range(1, 6)])
    result = revalidate_existing_naver_page(
        '3e015789-1194-81c8-afff-dd563a39984c', notion=notion, ai=ai,
        operating_contract=load_operating_contract(), output_dir=tmp_path,
        qa_run_id='parse-fail-run',
    )
    saved = json.loads(Path(result['report_path']).read_text(encoding='utf-8'))
    assert saved['manuscript']['failure_code'] == 'QA_PARSE_FAIL'
    assert saved['manuscript']['failures'][0]['retryable'] is True
    assert saved['overall_status'] == 'FAIL'
    assert saved['handoff_written'] is False


def test_malformed_card_breakdown_is_parse_failure_for_each_unverifiable_card(monkeypatch, tmp_path):
    result, notion, *_ = _setup(
        monkeypatch, tmp_path,
        card_qa={'pass': False, 'blocking_issues': ['세트가 불일치합니다.'], 'ai_likeness_score': 4},
    )
    saved = json.loads(Path(result['report_path']).read_text(encoding='utf-8'))
    assert result['card_qa_pass'] is False
    assert [row['failure_code'] for row in saved['cards']] == ['QA_PARSE_FAIL'] * 5
    assert not notion.blocks


def test_ai_likeness_gate_fails_cards_without_lowering_threshold(monkeypatch, tmp_path):
    result, _notion, *_ = _setup(
        monkeypatch, tmp_path,
        card_qa={
            'pass': True, 'blocking_issues': [], 'ai_likeness_score': 5,
            'card_results': _passing_cards(),
        },
    )
    assert result['card_qa_pass'] is False
    assert [row['status'] for row in result['card_results']] == ['FAIL'] * 5
    assert all(row['failures'][0]['rule_id'] == 'AI_LIKENESS_SCORE' for row in result['card_results'])


def test_qa_error_categories_distinguish_timeout_and_api_failure():
    import httpx

    assert _qa_exception(TimeoutError())['code'] == 'QA_TIMEOUT'
    assert _qa_exception(QAResponseParseError('bad json'))['code'] == 'QA_PARSE_FAIL'
    assert _qa_exception(httpx.ConnectError('offline'))['code'] == 'QA_API_FAIL'
