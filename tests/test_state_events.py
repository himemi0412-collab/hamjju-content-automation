import sqlite3

from app.state import StateStore


def test_stage_events_are_append_only_receipts(tmp_path):
    path = tmp_path / 'state.db'
    state = StateStore(path)

    state.record_stage('page-1', 'naver_blog', 'v1', 'github_actions', 'CONTENT_READY', 'run-1')
    state.record_stage('page-1', 'naver_blog', 'v1', 'github_actions', 'QA_PASS', 'run-1')

    with sqlite3.connect(path) as cx:
        rows = cx.execute(
            'SELECT source_version, owner, stage, run_id FROM content_events ORDER BY id'
        ).fetchall()
    assert rows == [
        ('v1', 'github_actions', 'CONTENT_READY', 'run-1'),
        ('v1', 'github_actions', 'QA_PASS', 'run-1'),
    ]
