from types import SimpleNamespace

from google.auth.exceptions import RefreshError
from typer.testing import CliRunner

from app import main
from app.youtube import YouTubePrivateUploader


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

    monkeypatch.setattr(YouTubePrivateUploader, 'current_channel', revoked)
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
    monkeypatch.setattr(YouTubePrivateUploader, 'current_channel',
                        lambda _self: {'id': 'unexpected', 'title': 'Unexpected'})
    result = CliRunner().invoke(main.app, ['verify-youtube-auth'])
    assert result.exit_code == 1
    assert 'AUTH_ERROR: ppojjugi_shorts is authorized as an unexpected YouTube channel' in result.output
    assert 'Traceback' not in result.output
