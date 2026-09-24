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


def _shorts_settings(tmp_path):
    s = settings(tmp_path)
    s.fal_key = 'test-fal-key'
    s.youtube_client_secrets_file = tmp_path / 'client.json'
    s.youtube_ppojjugi_token_file = tmp_path / 'ppojjugi-token.json'
    s.youtube_japan_token_file = tmp_path / 'japan-token.json'
    for path in (s.youtube_client_secrets_file, s.youtube_ppojjugi_token_file,
                 s.youtube_japan_token_file):
        path.write_text('{}', encoding='utf-8')
    return s


def test_scheduled_shorts_preflight_rejects_revoked_youtube_token_before_notion(
        tmp_path, monkeypatch):
    from google.auth.exceptions import RefreshError

    from app.preflight import YouTubePrivateUploader

    monkeypatch.setattr(YouTubePrivateUploader, 'current_channel',
                        lambda self: (_ for _ in ()).throw(RefreshError('invalid_grant')))
    with pytest.raises(PrecheckFailure) as error:
        check('schedule', '', '0 12 * * *', '', _shorts_settings(tmp_path))
    assert error.value.category == 'AUTH_ERROR'
    assert 'invalid_grant' not in str(error.value)


def test_scheduled_shorts_preflight_rejects_wrong_channel_identity_before_notion(
        tmp_path, monkeypatch):
    from app.preflight import YouTubePrivateUploader

    monkeypatch.setattr(YouTubePrivateUploader, 'current_channel',
                        lambda self: {'id': 'wrong-channel', 'title': 'Unexpected'})
    with pytest.raises(PrecheckFailure, match='does not match configured channel') as error:
        check('schedule', '', '0 12 * * *', '', _shorts_settings(tmp_path))
    assert error.value.category == 'AUTH_ERROR'


def test_scheduled_shorts_preflight_checks_both_channel_identities(
        tmp_path, monkeypatch):
    from app.preflight import YouTubePrivateUploader

    calls = []

    def current_channel(self):
        calls.append(self.token_file.name)
        return {'id': ('UCjCEzw6WQmQRZeOV-O8cYpg' if 'ppojjugi' in self.token_file.name
                       else 'UCXezqjpy6AgXEynvTxSjzmg'), 'title': 'Expected'}

    def handle(request):
        return httpx.Response(200, json={'id': 'ok'})

    class Client(httpx.Client):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(YouTubePrivateUploader, 'current_channel', current_channel)
    monkeypatch.setattr('app.preflight.httpx.Client', Client)
    result = check('schedule', '', '0 12 * * *', '', _shorts_settings(tmp_path))
    assert result['status'] == 'PASS'
    assert calls == ['ppojjugi-token.json', 'japan-token.json']
