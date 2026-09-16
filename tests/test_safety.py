from pathlib import Path
from app.naver import save_draft, NaverDraftAutomationUnavailable


def test_youtube_code_hardcodes_private():
    src = Path('app/youtube.py').read_text(encoding='utf-8')
    assert "'privacyStatus': 'private'" in src
    assert 'upload_public' not in src


def test_naver_browser_automation_is_disabled():
    try:
        save_draft()
    except NaverDraftAutomationUnavailable:
        pass
    else:
        raise AssertionError('Naver automation should be disabled by design')


def test_github_action_schedule_keeps_blog_draft_only_and_uploads_shorts_privately():
    workflow = Path('.github/workflows/daily.yml').read_text(encoding='utf-8')
    assert 'actions/checkout@v7' in workflow
    assert 'actions/setup-python@v7' in workflow
    assert 'actions/upload-artifact@v7' in workflow
    assert "cron: '0 1 * * *'" in workflow
    assert "cron: '0 12 * * *'" in workflow
    assert 'ENABLE_MEDIA_GENERATION=false python -m app.main channel naver_blog --limit 3' in workflow
    assert 'ENABLE_MEDIA_GENERATION=true AUTO_PRIVATE_YOUTUBE_UPLOAD=true python -m app.main channel ppojjugi_shorts --limit 1' in workflow
    assert 'ENABLE_MEDIA_GENERATION=true AUTO_PRIVATE_YOUTUBE_UPLOAD=true python -m app.main channel japan_shorts --limit 1' in workflow
    assert 'execute_media' in workflow
    assert 'default: dry_run' in workflow
    assert '--dry-run --limit 1' in workflow
    assert "AUTO_PRIVATE_YOUTUBE_UPLOAD: 'false'" in workflow
    assert "AUTO_PRIVATE_YOUTUBE_UPLOAD: 'true'" in workflow
    assert 'verify_youtube_auth' in workflow
    assert 'YOUTUBE_CLIENT_SECRET_JSON_B64' in workflow
    assert 'secrets.BLOG_DATA_SOURCE_ID' not in workflow
    assert 'secrets.SHORTS_DATA_SOURCE_ID' not in workflow


def test_channel_mismatch_stops_before_youtube_upload(monkeypatch, tmp_path):
    from app.pipeline import Pipeline
    from app.settings import Settings

    called = {'upload': False}

    class FakeUploader:
        def __init__(self, *_args, **_kwargs):
            pass

        def current_channel(self):
            return {'id': 'wrong-channel', 'title': 'Wrong account'}

        def upload_private(self, *_args, **_kwargs):
            called['upload'] = True
            return 'https://www.youtube.com/watch?v=should-not-exist'

    monkeypatch.setattr('app.pipeline.YouTubePrivateUploader', FakeUploader)
    pipeline = Pipeline(Settings(), None, None, None)

    try:
        pipeline._upload_private('ppojjugi_shorts', {'title': 'test'}, tmp_path / 'video.mp4')
    except RuntimeError as exc:
        assert 'channel mismatch' in str(exc)
    else:
        raise AssertionError('A mismatched YouTube channel must stop the upload')
    assert called['upload'] is False


def test_dry_run_does_not_print_notion_content():
    pipeline = Path('app/pipeline.py').read_text(encoding='utf-8')
    dry_run_branch = pipeline.split('if dry_run:', 1)[1].split('self.state.start', 1)[0]
    assert "'context': context" not in dry_run_branch
    assert "'content_loaded':" in dry_run_branch
    assert "'existing_page_text'" not in dry_run_branch


def test_blog_success_requires_five_cards_and_notion_attachment():
    pipeline = Path('app/pipeline.py').read_text(encoding='utf-8')
    assert "if len(card_news) != 5:" in pipeline
    assert "if len(cards) != 5:" in pipeline
    assert "raise RuntimeError('Failed to attach all blog card-news images to Notion')" in pipeline
