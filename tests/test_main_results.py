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


def test_automatic_retry_reprocesses_only_qa_rejections():
    from app.main import retry_qa_rejections_once
    from app.config import load_channels

    class Notion:
        def retrieve_page(self, page_id):
            return {'id': page_id}

    class Pipeline:
        notion = Notion()

        def process_page(self, cfg, page):
            assert cfg.ready_status == '수정 필요'
            return {
                'page_id': page['id'], 'status': '검토 대기',
                'qa_pass': True, 'notion_page_updated': True,
            }

    initial = [
        {
            'page_id': 'retry-me', 'status': '수정 필요', 'qa_pass': False,
            'blocking_issues': ['channel style mixed'], 'budget_blocked': False,
        },
        {'page_id': 'leave-me', 'status': 'failed', 'error': 'network'},
    ]
    results = retry_qa_rejections_once(
        Pipeline(), load_channels()['ppojjugi_shorts'], initial,
    )

    assert results[0]['qa_pass'] is True
    assert results[0]['automatic_retry']['attempted'] is True
    assert results[0]['automatic_retry']['previous_blocking_issues'] == ['channel style mixed']
    assert results[1] == initial[1]


def test_automatic_retry_defaults_to_one_paid_retry():
    from app.main import retry_qa_rejections_once
    from app.config import load_channels

    class Notion:
        def retrieve_page(self, page_id):
            return {'id': page_id}

    class Pipeline:
        notion = Notion()
        calls = 0

        def process_page(self, cfg, page):
            self.calls += 1
            return {
                'page_id': page['id'], 'status': '수정 필요',
                'qa_pass': False, 'blocking_issues': ['still rejected'],
                'budget_blocked': False,
            }

    pipeline = Pipeline()
    retry_qa_rejections_once(
        pipeline,
        load_channels()['japan_shorts'],
        [{
            'page_id': 'one-retry-only', 'status': '수정 필요',
            'qa_pass': False, 'blocking_issues': ['initial'],
            'budget_blocked': False,
        }],
    )

    assert pipeline.calls == 1


def test_automatic_retry_uses_one_bounded_second_attempt_for_new_qa_issue():
    from app.main import retry_qa_rejections_once
    from app.config import load_channels

    class Notion:
        def retrieve_page(self, page_id):
            return {'id': page_id}

    class Pipeline:
        notion = Notion()
        calls = 0

        def process_page(self, cfg, page):
            self.calls += 1
            if self.calls == 1:
                return {
                    'page_id': page['id'], 'status': '수정 필요',
                    'qa_pass': False, 'blocking_issues': ['time axis mismatch'],
                    'budget_blocked': False,
                }
            return {
                'page_id': page['id'], 'status': '검토 대기',
                'qa_pass': True, 'notion_page_updated': True,
            }

    pipeline = Pipeline()
    results = retry_qa_rejections_once(
        pipeline,
        load_channels()['japan_shorts'],
        [{
            'page_id': 'retry-twice', 'status': '수정 필요',
            'qa_pass': False, 'blocking_issues': ['unsupported memory'],
            'budget_blocked': False,
        }],
        max_attempts=2,
    )

    assert pipeline.calls == 2
    assert results[0]['qa_pass'] is True
    assert results[0]['automatic_retry']['attempt_count'] == 2
    assert results[0]['automatic_retry']['history'] == [
        {'attempt': 1, 'previous_blocking_issues': ['unsupported memory']},
        {'attempt': 2, 'previous_blocking_issues': ['time axis mismatch']},
    ]


def test_automatic_retry_stops_after_two_qa_failures():
    from app.main import retry_qa_rejections_once
    from app.config import load_channels

    class Notion:
        def retrieve_page(self, page_id):
            return {'id': page_id}

    class Pipeline:
        notion = Notion()
        calls = 0

        def process_page(self, cfg, page):
            self.calls += 1
            return {
                'page_id': page['id'], 'status': '수정 필요',
                'qa_pass': False, 'blocking_issues': [f'issue-{self.calls}'],
                'budget_blocked': False,
            }

    pipeline = Pipeline()
    results = retry_qa_rejections_once(
        pipeline,
        load_channels()['japan_shorts'],
        [{
            'page_id': 'still-bad', 'status': '수정 필요',
            'qa_pass': False, 'blocking_issues': ['initial'],
            'budget_blocked': False,
        }],
        max_attempts=2,
    )

    assert pipeline.calls == 2
    assert results[0]['qa_pass'] is False
    assert results[0]['automatic_retry']['attempt_count'] == 2


def test_monthly_blog_order_is_resumable_and_month_scoped():
    from app.main import _is_monthly_blog_order

    assert _is_monthly_blog_order(2026090001, '202609')
    assert _is_monthly_blog_order(2026099999, '202609')
    assert not _is_monthly_blog_order(2026089999, '202609')
    assert not _is_monthly_blog_order(None, '202609')


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


@pytest.mark.parametrize('change', [{'page_id': 'other-page'}, {'channel': 'japan_shorts'}, {'generated': {}}])
def test_resume_blog_rejects_wrong_artifact_before_any_connection(monkeypatch, tmp_path, change):
    import json
    import typer
    import app.main as main

    manifest = tmp_path / 'manifest.json'
    source = {'page_id': 'exact-page', 'channel': 'naver_blog', 'generated': {'body_markdown': 'Saved original'}}
    source.update(change)
    manifest.write_text(json.dumps(source), encoding='utf-8')
    monkeypatch.setattr(main, 'build', lambda: pytest.fail('Invalid artifact must not connect or write'))
    with pytest.raises(typer.BadParameter):
        main.resume_blog('exact-page', manifest)


@pytest.mark.parametrize('resume_status', ['수정 필요', 'CODEX_HANDOFF_READY'])
def test_resume_blog_processes_only_original_manifest_and_records_readback(monkeypatch, tmp_path, resume_status):
    import json
    from types import SimpleNamespace
    import app.main as main

    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({
        'page_id': 'exact-page', 'channel': 'naver_blog', 'generated': {'body_markdown': 'Saved original'},
    }), encoding='utf-8')
    settings = SimpleNamespace(output_dir=tmp_path / 'output')
    notion = SimpleNamespace(retrieve_page=lambda page_id: {
        'id': page_id,
        'properties': {'상태': {'type': 'select', 'select': {'name': resume_status}}},
    }, close=lambda: None)
    def process(cfg, page, *, resume_manifest):
        assert cfg.ready_status == resume_status
        assert page['id'] == 'exact-page'
        assert resume_manifest == manifest
        return {
            'channel': 'naver_blog', 'page_id': page['id'], 'qa_pass': True,
            'notion_page_updated': True, 'output_verified': True, 'status': '네이버 저장 요청',
        }
    pipeline = SimpleNamespace(process_page=process)
    monkeypatch.setattr(main, 'build', lambda: (settings, notion, None, None, pipeline))
    main.resume_blog('exact-page', manifest)
    data = json.loads((settings.output_dir / 'production-results.json').read_text(encoding='utf-8'))
    assert data['batches'][0]['reviewed_outputs'] == 1


def test_resume_blog_refuses_ready_or_completed_page(monkeypatch, tmp_path):
    import json
    from types import SimpleNamespace
    import app.main as main

    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({
        'page_id': 'exact-page', 'channel': 'naver_blog', 'generated': {'body_markdown': 'Saved original'},
    }), encoding='utf-8')
    settings = SimpleNamespace(output_dir=tmp_path / 'output')
    notion = SimpleNamespace(retrieve_page=lambda page_id: {
        'id': page_id,
        'properties': {'상태': {'type': 'select', 'select': {'name': '네이버 저장 요청'}}},
    }, close=lambda: None)
    pipeline = SimpleNamespace(process_page=lambda *_a, **_k: pytest.fail('Already-ready page must not be rewritten'))
    monkeypatch.setattr(main, 'build', lambda: (settings, notion, None, None, pipeline))
    with pytest.raises(RuntimeError, match='did not complete'):
        main.resume_blog('exact-page', manifest)
