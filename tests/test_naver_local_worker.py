from pathlib import Path
import json

import httpx
import pytest

from app.naver_local_worker import HandoffValidationFailed, WorkerStepFailed, payload_from_blocks, process_jobs
from app.naver_browser import FORBIDDEN, validate_draft_readback
from app.notion_client import split_snapshot_json
from app.manuscript_repair import manuscript_hash
from app.content_fingerprint import canonical_content_hash

def _block(kind, text):
    return {"type": kind, kind: {"rich_text": [{"plain_text": text}]}}


def _snapshot_blocks(page_id, generated, qa_pass=True):
    version = manuscript_hash(generated)
    run_id = 'run-1'
    import hashlib
    card_hashes = [hashlib.sha256(f'card-{i}'.encode()).hexdigest() for i in range(5)]
    qa = {
        'pass': qa_pass,
        'source_page_id': page_id,
        'source_run_id': run_id,
        'source_content_sha256': version,
        'source_card_sha256s': card_hashes,
    }
    snapshot = json.dumps({
        'generated': generated,
        'qa': qa,
        'ownership': {
            'channel': 'naver_blog', 'document_id': page_id,
            'source_version': version, 'stage': 'HANDOFF_READY',
        },
        'handoff': {
            'schema_version': 2, 'page_id': page_id, 'run_id': 'delivery-1',
            'qa_run_id': run_id, 'content_version': version,
            'content_sha256': version, 'card_sha256s': card_hashes,
        },
    }, ensure_ascii=False, separators=(',', ':'))
    return [
        _block('paragraph', '저장 준비 완료: READY_FOR_NAVER_DRAFT'),
        _block('paragraph', f'원고 ID: {page_id}'),
        _block('paragraph', f'버전: {version}'),
        _block('paragraph', '실행 ID: delivery-1'),
        _block('paragraph', f'QA 실행 ID: {run_id}'),
        *[_block('code', chunk) for chunk in split_snapshot_json(snapshot, 1800)],
    ]


def test_handoff_requires_marker_and_snapshot():
    blocks = _snapshot_blocks('page-1', {'title': '제목', 'body_markdown': '본문'})
    payload = payload_from_blocks(blocks, 'page-1')
    assert (payload.title, payload.body, payload.page_id, payload.run_id) == (
        '제목', '본문', 'page-1', 'delivery-1',
    )


def test_handoff_reassembles_split_qa_snapshot_without_losing_spaces():
    body = ("긴 검수 본문 단어 경계 보존 " * 500).strip()
    generated = {'title': '긴 제목', 'body_markdown': body}
    snapshot_blocks = _snapshot_blocks('page-1', generated)
    chunks = [block['code']['rich_text'][0]['plain_text'] for block in snapshot_blocks if block.get('type') == 'code']

    assert len(chunks) > 1
    assert ''.join(chunks).startswith('{"generated":')
    payload = payload_from_blocks(snapshot_blocks, 'page-1')
    assert (payload.title, payload.body) == ('긴 제목', body)


def test_handoff_rejects_snapshot_without_passed_qa():
    blocks = _snapshot_blocks('page-1', {'title': '제목', 'body_markdown': '본문'}, qa_pass=False)
    with pytest.raises(RuntimeError, match='QA'):
        payload_from_blocks(blocks, 'page-1')


def test_handoff_rejects_wrong_page_and_changed_body():
    generated = {'title': '제목', 'body_markdown': '원문'}
    blocks = _snapshot_blocks('page-1', generated)
    with pytest.raises(RuntimeError, match='identity'):
        payload_from_blocks(blocks, 'different-page')

    changed = {'title': '제목', 'body_markdown': '수정된 원문'}
    changed_snapshot = _snapshot_blocks('page-1', changed)
    # Keep the displayed checksum from the reviewed handoff while changing the JSON payload.
    changed_snapshot[-1] = _block(
        'code',
        json.dumps({
            'generated': changed, 'qa': {
                'pass': True, 'source_page_id': 'page-1', 'source_run_id': 'run-1',
                'source_content_sha256': manuscript_hash(generated),
                'source_card_sha256s': [f'{i:064x}' for i in range(1, 6)],
            },
            'ownership': {
                'channel': 'naver_blog', 'document_id': 'page-1',
                'source_version': manuscript_hash(generated), 'stage': 'HANDOFF_READY',
            },
            'handoff': {
                'schema_version': 2, 'page_id': 'page-1', 'run_id': 'delivery-1',
                'qa_run_id': 'run-1', 'content_version': manuscript_hash(generated),
                'content_sha256': manuscript_hash(generated),
                'card_sha256s': [f'{i:064x}' for i in range(1, 6)],
            },
        }, ensure_ascii=False, separators=(',', ':')),
    )
    changed_snapshot[2] = _block('paragraph', f'버전: {manuscript_hash(generated)}')
    with pytest.raises(RuntimeError, match='HASH_MISMATCH'):
        payload_from_blocks(changed_snapshot, 'page-1')


def test_canonical_hash_ignores_json_layout_unicode_composition_and_line_endings():
    composed = {'title': 'café', 'body_markdown': '첫 줄\n둘째 줄'}
    decomposed = {'body_markdown': '첫 줄\r\n둘째 줄', 'title': 'cafe\u0301'}
    assert canonical_content_hash(composed) == canonical_content_hash(decomposed)


def test_handoff_requires_page_and_qa_run_identity():
    blocks = _snapshot_blocks('page-1', {'title': '제목', 'body_markdown': '본문'})
    blocks[4] = _block('paragraph', 'QA 실행 ID: stale-run')
    with pytest.raises(RuntimeError, match='STALE_QA'):
        payload_from_blocks(blocks, 'page-1')

def test_local_worker_has_no_publication_action():
    src = Path("app/naver_browser.py").read_text(encoding="utf-8")
    assert "get_by_role('button', name='저장', exact=True)" in src
    assert "get_by_role('region', name='임시저장 글 보기')" in src
    assert "get_by_text(\"발행\"" not in src
    assert {"발행", "예약발행", "공개"}.issubset(set(FORBIDDEN))


def test_worker_installer_preserves_an_existing_different_runtime():
    src = Path('scripts/install_naver_local_worker.ps1').read_text(encoding='utf-8')
    assert 'Missing .env' in src
    assert 'The existing Naver worker uses another project path' in src
    assert src.index('The existing Naver worker uses another project path') < src.index('Register-ScheduledTask')


def test_naver_draft_readback_requires_list_title_and_matching_body():
    validate_draft_readback(["검증 글"], "검증 글", "첫 문장\n둘째 문장", "검증 글", "첫 문장 둘째 문장", 5)
    with pytest.raises(RuntimeError, match="list"):
        validate_draft_readback([], "검증 글", "본문", "검증 글", "본문", 5)
    with pytest.raises(RuntimeError, match="body"):
        validate_draft_readback(["검증 글"], "검증 글", "다른 본문", "검증 글", "본문", 5)
    with pytest.raises(RuntimeError, match="five"):
        validate_draft_readback(["검증 글"], "검증 글", "본문", "검증 글", "본문", 4)


def test_failed_naver_verification_is_not_left_in_the_ready_queue():
    requests = []

    def respond(request):
        requests.append((request.method, request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={})

    from app.naver_local_worker import NotionQueue
    queue = NotionQueue('test-token', 'source-id')
    queue.client = httpx.Client(transport=httpx.MockTransport(respond), base_url='https://api.notion.com/v1')
    queue.update_failed('page-id')
    assert requests == [('PATCH', '/v1/pages/page-id', {
        'properties': {'상태': {'select': {'name': '수정 필요'}}},
    })]
    queue.client.close()


def test_naver_queue_can_use_any_draft_save_adapter(tmp_path):
    events = []
    blocks = _snapshot_blocks('page-1', {'title': '교체 글', 'body_markdown': '본문 전체'})

    class Queue:
        def page(self, _page_id):
            return {
                'last_edited_time': '2026-01-01T00:00:00Z',
                'properties': {
                    '상태': {'select': {'name': '네이버 저장 요청'}},
                    '제목': {'title': [{'plain_text': '교체 글'}]},
                },
            }

        def blocks(self, _page_id):
            return blocks

        def download_cards(self, _page_id, target):
            target.mkdir(parents=True, exist_ok=True)
            paths = []
            for index in range(5):
                path = target / f'{index}.png'
                path.write_bytes(f'card-{index}'.encode())
                paths.append(path)
            return paths

        def update_done(self, page_id, url):
            events.append(('done', page_id, url))

        def update_failed(self, page_id):
            events.append(('failed', page_id))

    class OtherBrowserProvider:
        def save_and_verify(self, blog_id, title, body, cards):
            assert (blog_id, title, body, len(cards)) == ('himemi0412', '교체 글', '본문 전체', 5)
            return 'https://blog.naver.com/draft'

    assert process_jobs(Queue(), OtherBrowserProvider(), [{'id': 'page-1'}], 'himemi0412', tmp_path) == 1
    assert events == [('done', 'page-1', 'https://blog.naver.com/draft')]


def test_worker_failure_names_safe_stage_without_leaking_exception_details(tmp_path):
    blocks = _snapshot_blocks('page-1', {'title': '검증 글', 'body_markdown': '본문'})
    events = []

    class Queue:
        def page(self, _page_id):
            return {
                'last_edited_time': '2026-01-01T00:00:00Z',
                'properties': {
                    '상태': {'select': {'name': '네이버 저장 요청'}},
                    '제목': {'title': [{'plain_text': '검증 글'}]},
                },
            }

        def blocks(self, _page_id):
            return blocks

        def download_cards(self, _page_id, target):
            target.mkdir(parents=True, exist_ok=True)
            paths = []
            for index in range(5):
                path = target / f'{index}.png'
                path.write_bytes(f'card-{index}'.encode())
                paths.append(path)
            return paths

        def update_failed(self, page_id):
            events.append(('failed', page_id))

    class BrokenAdapter:
        def save_and_verify(self, *_args):
            raise RuntimeError('private signed URL must not appear in user alert')

    with pytest.raises(WorkerStepFailed, match='browser_adapter') as exc:
        process_jobs(Queue(), BrokenAdapter(), [{'id': 'page-1'}], 'himemi0412', tmp_path)
    assert 'signed URL' not in str(exc.value)
    assert events == [('failed', 'page-1')]


def test_worker_rejects_changed_card_bytes_before_browser_save(tmp_path):
    blocks = _snapshot_blocks('page-1', {'title': '검증 글', 'body_markdown': '본문'})
    events = []

    class Queue:
        def page(self, _page_id):
            return {
                'last_edited_time': '2026-01-01T00:00:00Z',
                'properties': {
                    '상태': {'select': {'name': '네이버 저장 요청'}},
                    '제목': {'title': [{'plain_text': '검증 글'}]},
                },
            }

        def blocks(self, _page_id):
            return blocks

        def download_cards(self, _page_id, target):
            target.mkdir(parents=True, exist_ok=True)
            paths = []
            for index in range(5):
                path = target / f'{index}.png'
                path.write_bytes(f'changed-card-{index}'.encode())
                paths.append(path)
            return paths

        def update_failed(self, page_id):
            events.append(('failed', page_id))

    class MustNotSave:
        def save_and_verify(self, *_args):
            pytest.fail('browser save must not run when a reviewed card changed')

    with pytest.raises(HandoffValidationFailed, match='HASH_MISMATCH'):
        process_jobs(Queue(), MustNotSave(), [{'id': 'page-1'}], 'himemi0412', tmp_path)
    assert events == [('failed', 'page-1')]
