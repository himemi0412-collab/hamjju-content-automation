import json
import hashlib
import shutil
from pathlib import Path

import pytest

from app.run_report import is_production_run, markdown_report, record_results
from app.run_report import expected_channels
from app.run_report import merge_resumed_blog_batch, original_batch_run_id
from app.manuscript_repair import manuscript_hash


def good_blog():
    return {
        'page_id': 'existing-blog', 'title': '기존 미완성 글', 'status': 'CODEX_HANDOFF_READY',
        'qa_pass': True, 'notion_page_updated': True, 'output_verified': True,
        'media': {'cards': ['1.png', '2.png', '3.png', '4.png', '5.png'], 'notion_cards_attached': True},
    }


def test_results_survive_later_channel_failure_without_leaking_raw_details(tmp_path):
    path = record_results(tmp_path, 'naver_blog', [good_blog()], 1)
    record_results(tmp_path, 'japan_shorts', [{'status': 'failed', 'error': 'secret must not be retained'}], 1)
    data = json.loads(path.read_text(encoding='utf-8'))
    assert len(data['batches']) == 2
    assert data['batches'][0]['reviewed_outputs'] == 1
    assert data['batches'][1]['reviewed_outputs'] == 0
    assert 'secret' not in path.read_text(encoding='utf-8')
    report = markdown_report(data, {'naver_blog': 1, 'japan_shorts': 1}, 'failure', 'https://example.org/run')
    assert '제작 미완료' in report
    assert '**1/2편**' in report
    assert '네이버 임시저장·재열람 확인: **0편**' in report


def test_empty_green_job_does_not_claim_production_success():
    report = markdown_report({}, {'naver_blog': 3}, 'success', 'https://example.org/run')
    assert '제작 결과가 없습니다' in report
    assert '제작 미완료' in report
    assert '**0/3편**' in report


def test_blog_without_readback_does_not_count_as_reviewed_output(tmp_path):
    result = good_blog()
    result['output_verified'] = False
    path = record_results(tmp_path, 'naver_blog', [result], 1)
    data = json.loads(path.read_text(encoding='utf-8'))
    assert data['batches'][0]['reviewed_outputs'] == 0


def test_shorts_without_readback_does_not_count_as_reviewed_output(tmp_path):
    result = {'page_id': 'short-page', 'title': '검증 대기', 'status': '비공개 업로드 완료',
              'qa_pass': True, 'notion_page_updated': True, 'output_verified': False,
              'media': {'notion_video_attached': True, 'verification': {'pass': True}}}
    path = record_results(tmp_path, 'japan_shorts', [result], 1)
    data = json.loads(path.read_text(encoding='utf-8'))
    assert data['batches'][0]['reviewed_outputs'] == 0
    assert '제작 미완료' in markdown_report(data, {'japan_shorts': 1}, 'success', 'https://example.org/run')


@pytest.mark.parametrize(('event', 'mode', 'production'), [
    ('push', '', False), ('workflow_dispatch', 'dry_run', False),
    ('workflow_dispatch', 'verify_youtube_auth', False),
    ('workflow_dispatch', 'explore_card_design', False),
    ('workflow_dispatch', 'recover_blog', True), ('schedule', '', True),
])
def test_safety_checks_cannot_replace_production_state(event, mode, production):
    assert is_production_run(event, mode) is production


def test_workflow_recovery_has_no_topic_seeding_and_no_upload():
    workflow = Path('.github/workflows/daily.yml').read_text(encoding='utf-8')
    recovery = workflow.split('- name: Recover one existing blog', 1)[1].split('\n      - name:', 1)[0]
    assert 'seed-topics' not in recovery
    assert 'python -m app.main page naver_blog "$RECOVERY_PAGE_ID"' in recovery
    assert "AUTO_PRIVATE_YOUTUBE_UPLOAD: 'false'" in recovery
    status = workflow.split('- name: Update production status board', 1)[1]
    assert "github.event_name == 'schedule'" in status
    assert "github.event_name == 'workflow_dispatch'" in status
    assert "github.event_name == 'push'" not in status


def test_rerender_records_verified_notion_output_and_uses_configured_budget(monkeypatch):
    main = Path('app/main.py').read_text(encoding='utf-8')
    pipeline = Path('app/pipeline.py').read_text(encoding='utf-8')
    assert "record_results(s.output_dir, 'naver_blog', [result], 1)" in main
    assert "'status': 'cards_replaced', 'qa_pass': True" in pipeline
    assert "'notion_page_updated': True" in pipeline
    assert "'output_verified': True" in pipeline
    assert "'type': 'file_upload', 'file_upload': {'id': upload_id}" in pipeline
    monkeypatch.setenv('OPENAI_MONTHLY_BUDGET_USD', '40')
    report = markdown_report({}, {'naver_blog': 1}, 'success', 'https://example.org/run')
    assert 'OpenAI 내부 월 한도: $40' in report


def test_workflow_keeps_second_shorts_after_first_failure_and_preserves_budget():
    workflow = Path('.github/workflows/daily.yml').read_text(encoding='utf-8')
    assert 'channel ppojjugi_shorts --limit 1 || result=1' in workflow
    assert 'channel japan_shorts --limit 1 || result=1' in workflow
    budget_save = workflow.split('- name: Save charged budget', 1)[1].split('\n      - name:', 1)[0]
    assert 'always()' in budget_save
    assert 'actions/cache/save@v6' in budget_save


def test_temporary_schedule_is_removed_and_unknown_schedule_fails_closed():
    workflow = Path('.github/workflows/daily.yml').read_text(encoding='utf-8')
    assert "cron: '*/5 4-5 18 9 *'" not in workflow
    assert 'scheduler-probe:' not in workflow
    assert "cron: '0 1 * * *'" in workflow
    assert "cron: '0 12 * * *'" in workflow
    assert 'Unsupported schedule expression' in workflow
    assert expected_channels('schedule', '*/5 4-5 18 9 *', '', '') == {}
    assert expected_channels('schedule', 'unexpected', '', '') == {}


def test_push_safety_checks_cannot_publish_fixture_summary_or_artifacts():
    workflow = Path('.github/workflows/daily.yml').read_text(encoding='utf-8')
    safety = workflow.split('- name: Run safety tests', 1)[1].split('\n      - name:', 1)[0]
    artifacts = workflow.split('- name: Keep generated files for inspection', 1)[1].split('\n      - name:', 1)[0]

    assert "GITHUB_STEP_SUMMARY: ''" in safety
    assert "github.event_name == 'schedule'" in artifacts
    assert "github.event_name == 'workflow_dispatch'" in artifacts
    assert "github.event_name == 'push'" not in artifacts


def test_resume_workflow_reuses_one_repository_artifact_without_seeding():
    workflow = Path('.github/workflows/daily.yml').read_text(encoding='utf-8')
    resume = workflow.split('- name: Resume one existing blog artifact', 1)[1].split('\n      - name:', 1)[0]
    assert 'seed-topics' not in resume
    assert 'SOURCE_RUN_ID: ${{ inputs.source_run_id }}' in resume
    assert 'REVIEWED_RUN_ID: ${{ inputs.reviewed_run_id }}' in resume
    assert 'RESUME_PAGE_ID: ${{ inputs.page_id }}' in resume
    assert '--repo "$GITHUB_REPOSITORY"' in resume
    assert 'resume-blog "$RESUME_PAGE_ID" "recovered/$page_folder/manifest.json"' in resume
    assert "AUTO_PRIVATE_YOUTUBE_UPLOAD: 'false'" in resume
    assert expected_channels('workflow_dispatch', '', 'resume_blog', 'japan_shorts') == {'naver_blog': 1}


def artifact_row(root, page_id, passed, source_hash=None):
    folder = root / page_id.replace('-', '')[:16]
    (folder / 'cards').mkdir(parents=True)
    names = [f'card_{i:02d}.png' for i in range(1, 6)]
    cards = []
    for name in names:
        content = (page_id + name).encode()
        (folder / 'cards' / name).write_bytes(content)
        cards.append({'name': name, 'sha256': hashlib.sha256(content).hexdigest()})
    row = {
        'channel': 'naver_blog', 'page_id': page_id, 'title': f'원고 {page_id[:8]}',
        'status': 'CODEX_HANDOFF_READY' if passed else '수정 필요',
        'qa_pass': passed, 'notion_page_updated': True, 'output_verified': True,
        'budget_blocked': False, 'card_count': 5, 'cards_attached': True,
        'video_attached': False, 'naver_draft_verified': False,
    }
    generated = {'title': row['title'], 'body_markdown': '원문' if not source_hash else '검수한 수정 원문'}
    manifest = {
        'page_id': page_id, 'channel': 'naver_blog', 'generated': generated,
        'manuscript_sha256': manuscript_hash(generated),
        'qa': {'pass': passed, 'blocking_issues': [] if passed else ['수정 필요']},
        'final_status': row['status'], 'output_verified': True,
        'media': {'cards': [f'output/{folder.name}/cards/{name}' for name in names], 'notion_cards_attached': True},
    }
    if source_hash:
        manifest['resume_source_manuscript_sha256'] = source_hash
    (folder / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    observation = {key: row[key] for key in ('page_id', 'title', 'status')}
    observation.update(body_blocks_verified=20, cards_verified=cards, naver_draft_verified=False)
    (folder / 'notion-readback.json').write_text(json.dumps(observation), encoding='utf-8')
    return row, manifest


def recovery_fixture(tmp_path):
    prior_dir, current_dir = tmp_path / 'recovered', tmp_path / 'output'
    ids = [f'{i:08d}-1111-4111-8111-111111111111' for i in range(1, 4)]
    items = [artifact_row(prior_dir, page_id, i < 2) for i, page_id in enumerate(ids)]
    prior = {'batches': [{'channel': 'naver_blog', 'expected': 3, 'results': [x[0] for x in items]}],
             'updated_at': '2026-09-18T01:12:48+00:00'}
    (prior_dir / 'production-results.json').write_text(json.dumps(prior), encoding='utf-8')
    current_row, _ = artifact_row(current_dir, ids[2], True, items[2][1]['manuscript_sha256'])
    current = {'batches': [{'channel': 'naver_blog', 'expected': 1, 'results': [current_row]}],
               'updated_at': '2026-09-18T04:00:00+00:00'}
    return prior_dir, current_dir, ids, prior, current


def test_resume_summary_combines_two_old_passes_and_one_recovery_without_double_counting(tmp_path):
    prior_dir, current_dir, ids, prior, current = recovery_fixture(tmp_path)
    merged = merge_resumed_blog_batch(current, prior_dir, current_dir, ids[2], '35294118274', '35300000000')
    batch = merged['batches'][0]
    assert batch['expected'] == 3 and batch['reviewed_outputs'] == 3
    assert len(batch['results']) == 3
    assert [x['evidence_run_id'] for x in batch['results']] == ['35294118274', '35294118274', '35300000000']
    assert current['batches'][0]['expected'] == 1  # The CLI result is untouched.
    assert prior['batches'][0]['results'][2]['qa_pass'] is False
    report = markdown_report(merged, {'naver_blog': 3}, 'success', 'https://github.com/org/repo/actions/runs/35300000000')
    assert '블로그 합계: **3/3편** · 이번 재개: **1/1편**' in report
    assert 'https://github.com/org/repo/actions/runs/35294118274' in report
    assert '네이버 임시저장·재열람 확인: **0편**' in report


@pytest.mark.parametrize('damage', ['duplicate', 'other_channel', 'manuscript_hash', 'card_hash', 'source_hash', 'already_done'])
def test_recovery_aggregation_rejects_unproven_or_duplicate_prior_completions(tmp_path, damage):
    prior_dir, current_dir, ids, prior, current = recovery_fixture(tmp_path)
    if damage == 'duplicate':
        prior['batches'][0]['results'][1] = dict(prior['batches'][0]['results'][0])
    elif damage == 'other_channel':
        prior['batches'][0]['results'][0]['channel'] = 'japan_shorts'
    elif damage == 'already_done':
        prior['batches'][0]['results'][2]['qa_pass'] = True
        prior['batches'][0]['results'][2]['status'] = 'CODEX_HANDOFF_READY'
    elif damage in {'manuscript_hash', 'source_hash'}:
        folder = (prior_dir / ids[0].replace('-', '')[:16]) if damage == 'manuscript_hash' else (current_dir / ids[2].replace('-', '')[:16])
        path = folder / 'manifest.json'
        manifest = json.loads(path.read_text(encoding='utf-8'))
        manifest['manuscript_sha256' if damage == 'manuscript_hash' else 'resume_source_manuscript_sha256'] = 'wrong-hash'
        path.write_text(json.dumps(manifest), encoding='utf-8')
    else:
        (prior_dir / ids[0].replace('-', '')[:16] / 'cards' / 'card_01.png').write_bytes(b'changed')
    (prior_dir / 'production-results.json').write_text(json.dumps(prior), encoding='utf-8')
    with pytest.raises(ValueError):
        merge_resumed_blog_batch(current, prior_dir, current_dir, ids[2], '35294118274', '35300000000')


def test_failed_recovery_preserves_two_prior_successes_without_calling_batch_complete(tmp_path):
    prior_dir, current_dir, ids, prior, current = recovery_fixture(tmp_path)
    row = current['batches'][0]['results'][0]
    row.update(status='failed', qa_pass=False, notion_page_updated=False, output_verified=False)
    merged = merge_resumed_blog_batch(current, prior_dir, current_dir, ids[2], '35294118274', '35300000000')
    assert merged['batches'][0]['reviewed_outputs'] == 2
    report = markdown_report(merged, {'naver_blog': 3}, 'failure', 'https://example.org/runs/35300000000')
    assert '블로그 합계: **2/3편** · 이번 재개: **0/1편**' in report
    assert '제작 미완료' in report


def test_invalid_prior_evidence_falls_back_to_current_run_without_daily_completion_claim(monkeypatch, tmp_path):
    import app.run_report as module
    prior_dir, current_dir, ids, _, current = recovery_fixture(tmp_path)
    (prior_dir / 'production-results.json').write_text('{invalid', encoding='utf-8')
    (current_dir / 'production-results.json').write_text(json.dumps(current), encoding='utf-8')
    monkeypatch.chdir(tmp_path)
    for key, value in {
        'EVENT_NAME': 'workflow_dispatch', 'DISPATCH_MODE': 'resume_blog',
        'RESUME_PAGE_ID': ids[2], 'SOURCE_RUN_ID': '35294118274',
        'GITHUB_RUN_ID': '35300000000', 'GITHUB_REPOSITORY': 'org/repo', 'RUN_STATUS': 'success',
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv('GITHUB_STEP_SUMMARY', raising=False)
    module.main()
    report = (current_dir / 'production-summary.md').read_text(encoding='utf-8')
    assert '**1/1편**' in report
    assert '오늘 전체 완료 수는 미확인' in report
    assert '**3/3편**' not in report
    assert not (current_dir / 'batch-recovery-results.json').exists()


def multi_recovery_fixture(tmp_path, legacy=False):
    original, first, ids, original_data, first_data = recovery_fixture(tmp_path)
    row = first_data['batches'][0]['results'][0]
    row.update(status='수정 필요', qa_pass=False)
    folder = first / ids[2].replace('-', '')[:16]
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    manifest.update(qa={'pass': False, 'blocking_issues': ['card needs clarification']}, final_status='수정 필요')
    (folder / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    readback = json.loads((folder / 'notion-readback.json').read_text(encoding='utf-8'))
    readback['status'] = '수정 필요'
    (folder / 'notion-readback.json').write_text(json.dumps(readback), encoding='utf-8')
    first_merged = merge_resumed_blog_batch(first_data, original, first, ids[2], '35294118274', '35301346758')
    if legacy:
        first_merged['recovery'].pop('manuscript_chain')
        first_merged['recovery'].pop('previous_run_id')
        shutil.rmtree(first / 'batch-origin')
    (first / 'production-results.json').write_text(json.dumps(first_data), encoding='utf-8')
    (first / 'batch-recovery-results.json').write_text(json.dumps(first_merged), encoding='utf-8')
    second = tmp_path / 'second-output'
    second_row, _ = artifact_row(second, ids[2], True, manifest['manuscript_sha256'])
    second_data = {'batches': [{'channel': 'naver_blog', 'expected': 1, 'results': [second_row]}],
                   'updated_at': '2026-09-18T05:00:00+00:00'}
    return original, first, second, ids, second_data


def test_second_resume_restores_original_batch_and_binds_to_latest_failed_manuscript(tmp_path):
    original, first, second, ids, current = multi_recovery_fixture(tmp_path, legacy=True)
    assert original_batch_run_id(first, '35301346758', ids[2]) == '35294118274'
    merged = merge_resumed_blog_batch(current, first, second, ids[2], '35301346758', '35302000000', original_dir=original)
    assert merged['batches'][0]['expected'] == 3
    assert merged['batches'][0]['reviewed_outputs'] == 3
    assert [row['evidence_run_id'] for row in merged['batches'][0]['results']] == ['35294118274', '35294118274', '35302000000']
    assert [link['run_id'] for link in merged['recovery']['manuscript_chain']] == ['35294118274', '35301346758', '35302000000']
    assert (second / 'batch-origin' / ids[0].replace('-', '')[:16] / 'cards/card_01.png').exists()
    assert not (second / 'batch-origin/state.db').exists()
    report = markdown_report(merged, {'naver_blog': 3}, 'success', 'https://example.org/runs/35302000000')
    assert 'https://example.org/runs/35294118274' in report
    assert '직전 재개 실행: https://example.org/runs/35301346758' in report


def test_later_resume_uses_bundled_original_evidence_without_old_artifact_download(tmp_path):
    original, first, second, ids, current = multi_recovery_fixture(tmp_path)
    assert original_batch_run_id(first, '35301346758', ids[2]) == ''
    shutil.rmtree(original)
    merged = merge_resumed_blog_batch(current, first, second, ids[2], '35301346758', '35302000000')
    assert merged['batches'][0]['reviewed_outputs'] == 3


@pytest.mark.parametrize('damage', ['retained_row', 'target_page', 'lineage', 'latest_source_hash', 'original_hash', 'duplicate'])
def test_second_resume_rejects_changed_history_or_original_evidence(tmp_path, damage):
    original, first, second, ids, current = multi_recovery_fixture(tmp_path)
    history_path = first / 'batch-recovery-results.json'
    history = json.loads(history_path.read_text(encoding='utf-8'))
    if damage == 'retained_row':
        history['batches'][0]['results'][0]['title'] = 'another manuscript'
    elif damage == 'target_page':
        history['recovery']['page_id'] = ids[0]
    elif damage == 'lineage':
        history['recovery']['manuscript_chain'][-1]['source_sha256'] = 'changed'
    elif damage == 'duplicate':
        history['batches'][0]['results'][1] = history['batches'][0]['results'][0]
    else:
        root = second if damage == 'latest_source_hash' else first / 'batch-origin'
        page_id = ids[2] if damage == 'latest_source_hash' else ids[0]
        manifest_path = root / page_id.replace('-', '')[:16] / 'manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        field = 'resume_source_manuscript_sha256' if damage == 'latest_source_hash' else 'manuscript_sha256'
        manifest[field] = 'changed'
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    history_path.write_text(json.dumps(history), encoding='utf-8')
    with pytest.raises(ValueError):
        merge_resumed_blog_batch(current, first, second, ids[2], '35301346758', '35302000000')


def test_workflow_restores_original_batch_evidence_before_repeated_resume():
    workflow = Path('.github/workflows/daily.yml').read_text(encoding='utf-8')
    resume = workflow.split('- name: Resume one existing blog artifact', 1)[1].split('\n      - name:', 1)[0]
    assert 'original_batch_run_id' in resume
    assert 'gh run download "$original_run_id" --repo "$GITHUB_REPOSITORY"' in resume
    assert '--dir recovered-original' in resume
    assert '--reviewed-manifest "recovered-original/$page_folder/manifest.json"' in resume
    assert 'original_run_id="$REVIEWED_RUN_ID"' in resume
