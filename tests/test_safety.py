from pathlib import Path
import pytest

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


def test_github_action_keeps_blog_local_and_uploads_review_shorts_privately():
    workflow = Path('.github/workflows/daily.yml').read_text(encoding='utf-8')
    assert 'actions/checkout@v7' in workflow
    assert 'actions/setup-python@v7' in workflow
    assert 'actions/upload-artifact@v7' in workflow
    assert 'actions/cache/restore@v6' in workflow
    assert 'actions/cache/save@v6' in workflow
    assert "OPENAI_MONTHLY_BUDGET_USD: '22'" in workflow
    assert 'IMAGE_QUALITY: medium' in workflow
    assert 'output/openai-cost-ledger.json' in workflow
    assert "OPENAI_BUDGET_REQUIRE_EXISTING_LEDGER: 'true'" in workflow
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


def test_internal_budget_stops_before_youtube_upload(monkeypatch, tmp_path):
    from datetime import datetime, timezone

    from app.budget import BudgetGuard, BudgetLimitReached
    from app.pipeline import Pipeline
    from app.settings import Settings

    called = {'uploader_created': False}

    class FakeUploader:
        def __init__(self, *_args, **_kwargs):
            called['uploader_created'] = True

    monkeypatch.setattr('app.pipeline.YouTubePrivateUploader', FakeUploader)
    month = datetime.now(timezone.utc).strftime('%Y-%m')
    budget = BudgetGuard(tmp_path / 'ledger.json', 0.5, month, 0.5)
    pipeline = Pipeline(Settings(), None, None, None, budget)

    with pytest.raises(BudgetLimitReached):
        pipeline._upload_private('ppojjugi_shorts', {'title': 'test'}, tmp_path / 'video.mp4')
    assert called['uploader_created'] is False


def test_budget_blocked_result_fails_workflow_validation():
    from app.main import validate_results

    with pytest.raises(RuntimeError, match='did not complete'):
        validate_results([{
            'status': '수정 필요',
            'notion_page_updated': True,
            'budget_blocked': True,
        }])


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


def test_shorts_master_video_guides_are_enforced_in_prompts_and_composer():
    ppojjugi = Path('prompts/ppojjugi.md').read_text(encoding='utf-8')
    japan = Path('prompts/japan_shorts.md').read_text(encoding='utf-8')
    qa = Path('prompts/qa.md').read_text(encoding='utf-8')
    media = Path('app/media.py').read_text(encoding='utf-8')
    pipeline = Path('app/pipeline.py').read_text(encoding='utf-8')

    assert '삐죽이 쇼츠 영상.mp4' in ppojjugi
    assert '흐린 전체 화면 배경' in ppojjugi
    assert "'삐죽이의 오늘'" in media
    assert 'PPOJJUGI_REFERENCE_LAYOUT' in media
    assert '일본 쇼츠 영상.mp4' in japan
    assert '첫 장면 상단' in japan
    assert 'JAPAN_REFERENCE_LAYOUT' in media
    assert '20대 여성·60~70대 여성·20대 남성·60~70대 남성' in japan
    assert '한 편 안에서는 선택한 단일 화자를 끝까지 유지' in japan
    assert 'docs/japan_voice_reference.md' in japan
    assert 'channel_style=channel_style' in pipeline
    assert '마스터 가이드' in qa
    assert "ImageOps.fit(source, (1080, 1920)" in media
    assert '"narration": "이 장면에서 들릴 내레이션"' in ppojjugi
    assert '"narration": "この場面で実際に流れるナレーション"' in japan
    assert 'concat_scene_audio' in media
    assert "'scene_durations': durations" in pipeline
