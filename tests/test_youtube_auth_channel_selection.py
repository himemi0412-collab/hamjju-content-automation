from types import SimpleNamespace

from typer.testing import CliRunner

from app import main
from app.youtube import YouTubeIdentityReader


def test_youtube_auth_can_verify_japan_without_touching_ppojjugi(monkeypatch):
    monkeypatch.setattr(main, 'Settings', lambda: SimpleNamespace(
        youtube_ppojjugi_token_file='ppojjugi.json',
        youtube_japan_token_file='japan.json',
        youtube_client_secrets_file='client.json',
        youtube_ppojjugi_channel_id='expected-ppojjugi',
        youtube_japan_channel_id='expected-japan',
    ))
    calls = []

    def current_channel(uploader):
        calls.append(uploader.token_file)
        if uploader.token_file == 'ppojjugi.json':
            raise AssertionError('selected-channel verification must not inspect Ppijjugi')
        return {'id': 'expected-japan', 'title': 'Japan channel'}

    monkeypatch.setattr(YouTubeIdentityReader, 'current_channel', current_channel)
    result = CliRunner().invoke(main.app, ['verify-youtube-auth', 'japan_shorts'])

    assert result.exit_code == 0, result.output
    assert calls == ['japan.json']
    assert '"channel_key": "japan_shorts"' in result.output
    assert '"channel_match": true' in result.output
