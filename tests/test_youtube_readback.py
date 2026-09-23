from pathlib import Path
from types import SimpleNamespace

import pytest

from app.youtube import YouTubePrivateUploader


def test_youtube_readback_checks_exact_id_and_privacy(monkeypatch):
    uploader = YouTubePrivateUploader(Path('client.json'), Path('token.json'))
    monkeypatch.setattr(uploader, '_credentials', lambda: object())
    video_id = 'abcDEF12345'
    payload = {'items': [{'id': video_id, 'status': {'privacyStatus': 'private'}}]}
    calls = []

    class Videos:
        def list(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(execute=lambda: payload)

    monkeypatch.setattr('app.youtube.build', lambda *args, **kwargs: SimpleNamespace(videos=lambda: Videos()))
    url = f'https://www.youtube.com/watch?v={video_id}'
    assert uploader.verify_uploaded(url, 'private') is True
    assert calls == [{'part': 'status', 'id': video_id}]
    assert uploader.verify_uploaded(url, 'public') is False
    payload['items'] = []
    assert uploader.verify_uploaded(url, 'private') is False
    with pytest.raises(ValueError, match='Invalid reviewed'):
        uploader.verify_uploaded('https://example.org/watch?v=' + video_id, 'private')
