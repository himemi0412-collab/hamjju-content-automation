import pytest

from app.main import validate_results


def test_require_item_rejects_empty_queue():
    with pytest.raises(RuntimeError, match='exactly one'):
        validate_results([], require_item=True, dry_run=True)


def test_require_item_rejects_failed_processing():
    with pytest.raises(RuntimeError, match='did not complete'):
        validate_results(
            [{'page_id': 'page-1', 'title': '테스트', 'status': 'failed'}],
            require_item=True,
            dry_run=False,
        )


def test_execute_requires_confirmed_notion_update():
    with pytest.raises(RuntimeError, match='was not confirmed'):
        validate_results(
            [{'page_id': 'page-1', 'title': '테스트', 'status': 'CODEX_HANDOFF_READY'}],
            require_item=True,
            dry_run=False,
        )


def test_verified_execute_result_passes():
    validate_results(
        [{
            'page_id': 'page-1',
            'title': '테스트',
            'status': 'CODEX_HANDOFF_READY',
            'qa_pass': True,
            'channel': 'naver_blog',
            'output_verified': True,
            'notion_page_updated': True,
        }],
        require_item=True,
        dry_run=False,
    )


def test_scheduled_empty_batch_cannot_be_success():
    with pytest.raises(RuntimeError, match='no Notion items'):
        validate_results([], expected_count=3)


def test_qa_rejection_does_not_pass_as_notion_success():
    with pytest.raises(RuntimeError, match='did not complete'):
        validate_results([{'status': '수정 필요', 'qa_pass': False, 'notion_page_updated': True}])


def test_missing_qa_pass_is_not_assumed():
    with pytest.raises(RuntimeError, match='Independent QA'):
        validate_results([{'status': 'CODEX_HANDOFF_READY', 'notion_page_updated': True}])


def test_blog_requires_output_readback():
    with pytest.raises(RuntimeError, match='read-back'):
        validate_results([{
            'channel': 'naver_blog', 'status': 'CODEX_HANDOFF_READY',
            'qa_pass': True, 'notion_page_updated': True,
        }])


def test_batch_with_fewer_than_requested_outputs_is_incomplete():
    with pytest.raises(RuntimeError, match='expected 3 items'):
        validate_results([{
            'status': '검토 대기', 'qa_pass': True, 'notion_page_updated': True,
        }], expected_count=3)


def test_channel_failure_does_not_skip_following_channels():
    from app.main import run_all_channels

    visited = []
    class Pipeline:
        def run_channel(self, channel, **kwargs):
            visited.append(channel)
            if channel == 'ppojjugi_shorts':
                raise RuntimeError('Notion unavailable for one channel')
            return [] if channel == 'naver_blog' else [{'status': '검토 대기'}]

    names = ('naver_blog', 'ppojjugi_shorts', 'japan_shorts')
    results = run_all_channels(Pipeline(), {x: x for x in names}, total_limit=3)
    assert visited == list(names)
    assert results[0]['status'] == 'failed'
    assert results[1]['status'] == '검토 대기'


@pytest.mark.parametrize(('backlog', 'needed'), [(0, 3), (1, 2), (3, 0)])
def test_blog_seeding_fills_only_ready_queue_gap(backlog, needed):
    from app.main import missing_blog_topics

    class Notion:
        def query_ready(self, data_source, status, channel, **kwargs):
            assert status == '작성 요청'
            assert kwargs['required_select_values']['선별 상태'] == '추천'
            assert kwargs['excluded_formula_value'] == '이전 주제 보관'
            assert kwargs['page_size'] == 3
            return [{'id': f'old-{i}'} for i in range(backlog)]

    assert missing_blog_topics(Notion(), 'blog-data-source', 3) == needed


def test_failed_channel_persists_evidence_before_raising(monkeypatch, tmp_path):
    import json
    from types import SimpleNamespace
    import app.main as main

    settings = SimpleNamespace(output_dir=tmp_path, max_jobs_per_run=1)
    notion = SimpleNamespace(close=lambda: None)
    pipeline = SimpleNamespace(run_channel=lambda *_a, **_k: [{
        'title': '검수에서 멈춘 기존 글', 'status': '수정 필요',
        'qa_pass': False, 'notion_page_updated': True,
    }])
    monkeypatch.setattr(main, 'build', lambda: (settings, notion, None, None, pipeline))
    with pytest.raises(RuntimeError, match='did not complete'):
        main.channel('naver_blog', limit=1)
    data = json.loads((tmp_path / 'production-results.json').read_text(encoding='utf-8'))
    assert data['batches'][0]['reviewed_outputs'] == 0
    assert data['batches'][0]['results'][0]['title'] == '검수에서 멈춘 기존 글'


def test_sufficient_blog_backlog_does_not_pay_for_new_topic_research(monkeypatch):
    from types import SimpleNamespace
    import app.main as main

    settings = SimpleNamespace(blog_data_source_id='blog-ds')
    notion = SimpleNamespace(close=lambda: None)
    ai = SimpleNamespace(research_topics=lambda *_a: pytest.fail('research should not run'))
    monkeypatch.setattr(main, 'build', lambda: (settings, notion, ai, None, None))
    monkeypatch.setattr(main, 'missing_blog_topics', lambda *_a: 0)
    main.seed_topics(blog_count=3, ppojjugi_count=0, japan_count=0)
