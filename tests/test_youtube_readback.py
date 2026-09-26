from app.youtube import SCOPES, YouTubeIdentityReader


def test_youtube_production_code_has_read_only_identity_capability():
    assert SCOPES == ['https://www.googleapis.com/auth/youtube.readonly']
    assert hasattr(YouTubeIdentityReader, 'current_channel')
    assert not hasattr(YouTubeIdentityReader, 'upload_private')
    assert not hasattr(YouTubeIdentityReader, 'upload_reviewed')
