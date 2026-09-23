from pathlib import Path

import httpx
import pytest

from app.preflight import PrecheckFailure, check
from app.settings import Settings


def settings(tmp_path):
    return Settings(_env_file=None, openai_api_key='key', notion_access_token='token',
                    openai_budget_require_existing_ledger=False,
                    openai_budget_ledger=tmp_path / 'ledger.json',
                    output_dir=tmp_path / 'output', state_db=tmp_path / 'output' / 'state.db')


def test_preflight_rejects_missing_auth_and_bad_page_before_network(tmp_path):
    s = settings(tmp_path)
    s.notion_access_token = ''
    with pytest.raises(PrecheckFailure, match='credential'):
        check('execute_text', 'naver_blog', '', '', s)
    s.notion_access_token = 'token'
    with pytest.raises(PrecheckFailure, match='page ID'):
        check('execute_text', 'naver_blog', '', 'wrong-page', s)


def test_preflight_checks_notion_auth_and_source_without_writes(tmp_path, monkeypatch):
    calls = []

    def handle(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json={'id': 'integration'})

    class Client(httpx.Client):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr('app.preflight.httpx.Client', Client)
    result = check('execute_text', 'naver_blog', '', '', settings(tmp_path))
    assert result['status'] == 'PASS'
    assert calls == [('GET', '/v1/users/me'),
                     ('GET', '/v1/data_sources/21a60556-f086-4b7a-96b3-81995c77edef')]
