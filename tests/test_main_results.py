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
            'notion_page_updated': True,
        }],
        require_item=True,
        dry_run=False,
    )
