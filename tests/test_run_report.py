import json
from pathlib import Path

import pytest

from app.run_report import is_production_run, markdown_report, record_results
from app.run_report import expected_channels


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


@pytest.mark.parametrize(('event', 'mode', 'production'), [
    ('push', '', False), ('workflow_dispatch', 'dry_run', False),
    ('workflow_dispatch', 'verify_youtube_auth', False),
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


def test_workflow_keeps_second_shorts_after_first_failure_and_preserves_budget():
    workflow = Path('.github/workflows/daily.yml').read_text(encoding='utf-8')
    assert 'channel ppojjugi_shorts --limit 1 || result=1' in workflow
    assert 'channel japan_shorts --limit 1 || result=1' in workflow
    budget_save = workflow.split('- name: Save charged budget', 1)[1].split('\n      - name:', 1)[0]
    assert 'always()' in budget_save
    assert 'actions/cache/save@v6' in budget_save


def test_resume_workflow_reuses_one_repository_artifact_without_seeding():
    workflow = Path('.github/workflows/daily.yml').read_text(encoding='utf-8')
    resume = workflow.split('- name: Resume one existing blog artifact', 1)[1].split('\n      - name:', 1)[0]
    assert 'seed-topics' not in resume
    assert 'SOURCE_RUN_ID: ${{ inputs.source_run_id }}' in resume
    assert 'RESUME_PAGE_ID: ${{ inputs.page_id }}' in resume
    assert '--repo "$GITHUB_REPOSITORY"' in resume
    assert 'resume-blog "$RESUME_PAGE_ID" "recovered/$page_folder/manifest.json"' in resume
    assert "AUTO_PRIVATE_YOUTUBE_UPLOAD: 'false'" in resume
    assert expected_channels('workflow_dispatch', '', 'resume_blog', 'japan_shorts') == {'naver_blog': 1}
