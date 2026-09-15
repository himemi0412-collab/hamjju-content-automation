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


def test_github_action_starts_manual_and_safe():
    workflow = Path('.github/workflows/daily.yml').read_text(encoding='utf-8')
    assert 'schedule:' not in workflow
    assert 'default: dry_run' in workflow
    assert '--dry-run --limit 1' in workflow
    assert "ENABLE_MEDIA_GENERATION: 'false'" in workflow
    assert "AUTO_PRIVATE_YOUTUBE_UPLOAD: 'false'" in workflow
