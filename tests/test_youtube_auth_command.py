from types import SimpleNamespace
from pathlib import Path

from google.auth.exceptions import RefreshError
from typer.testing import CliRunner

from app import main
from app.youtube import YouTubeIdentityReader


def test_interactive_youtube_auth_pins_ipv4_callback_and_prompts_account_selection(tmp_path, monkeypatch):
    options = {}

    class FakeCredentials:
        def to_json(self):
            return '{"token":"test-only"}'

    class FakeFlow:
        def run_local_server(self, **kwargs):
            options.update(kwargs)
            return FakeCredentials()

    monkeypatch.setattr('app.youtube.InstalledAppFlow.from_client_secrets_file',
                        lambda *_args: FakeFlow())
    token_file = tmp_path / 'secrets' / 'japan.json'
    uploader = YouTubeIdentityReader(Path('client.json'), token_file)

    uploader.authorize_interactively()

    assert options == {
        'host': '127.0.0.1',
        'bind_addr': '127.0.0.1',
        'port': 8765,
        'open_browser': True,
        'timeout_seconds': 300,
        'prompt': 'select_account',
    }
    assert token_file.read_text(encoding='utf-8') == '{"token":"test-only"}'


def test_youtube_auth_command_reports_revoked_token_without_traceback(monkeypatch):
    monkeypatch.setattr(main, 'Settings', lambda: SimpleNamespace(
        youtube_ppojjugi_token_file='ppojjugi.json',
        youtube_japan_token_file='japan.json',
        youtube_client_secrets_file='client.json',
        youtube_ppojjugi_channel_id='expected-ppojjugi',
        youtube_japan_channel_id='expected-japan',
    ))

    def revoked(_self):
        raise RefreshError('invalid_grant: Token has been expired or revoked.')

    monkeypatch.setattr(YouTubeIdentityReader, 'current_channel', revoked)
    result = CliRunner().invoke(main.app, ['verify-youtube-auth'])
    assert result.exit_code == 1
    assert 'AUTH_ERROR: ppojjugi_shorts YouTube token could not be verified' in result.output
    assert 'invalid_grant' not in result.output
    assert 'Traceback' not in result.output


def test_youtube_auth_command_reports_channel_mismatch_without_traceback(monkeypatch):
    monkeypatch.setattr(main, 'Settings', lambda: SimpleNamespace(
        youtube_ppojjugi_token_file='ppojjugi.json',
        youtube_japan_token_file='japan.json',
        youtube_client_secrets_file='client.json',
        youtube_ppojjugi_channel_id='expected-ppojjugi',
        youtube_japan_channel_id='expected-japan',
    ))
    monkeypatch.setattr(YouTubeIdentityReader, 'current_channel',
                        lambda _self: {'id': 'unexpected', 'title': 'Unexpected'})
    result = CliRunner().invoke(main.app, ['verify-youtube-auth'])
    assert result.exit_code == 1
    assert 'AUTH_ERROR: ppojjugi_shorts is authorized as an unexpected YouTube channel' in result.output
    assert 'Traceback' not in result.output
