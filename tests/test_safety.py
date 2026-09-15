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
    assert 'secrets.BLOG_DATA_SOURCE_ID' not in workflow
    assert 'secrets.SHORTS_DATA_SOURCE_ID' not in workflow


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
