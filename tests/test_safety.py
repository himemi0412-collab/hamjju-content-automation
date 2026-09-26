from pathlib import Path
import pytest

from app.naver import save_draft, NaverDraftAutomationUnavailable


def test_youtube_identity_check_is_read_only():
    src = Path('app/youtube.py').read_text(encoding='utf-8')
    assert 'youtube.readonly' in src
    assert 'youtube.upload' not in src
    assert 'videos().insert' not in src


def test_naver_browser_automation_is_disabled():
    try:
        save_draft()
    except NaverDraftAutomationUnavailable:
        pass
    else:
        raise AssertionError('Naver automation should be disabled by design')


def test_github_action_keeps_blog_local_and_never_uploads_shorts():
    workflow = Path('.github/workflows/daily.yml').read_text(encoding='utf-8')
    assert 'actions/checkout@v7' in workflow
    assert 'actions/setup-python@v7' in workflow
    assert 'actions/upload-artifact@v7' in workflow
    assert 'actions/cache/restore@v6' in workflow
    assert 'actions/cache/save@v6' in workflow
    assert "OPENAI_MONTHLY_BUDGET_USD: '40'" in workflow
    assert 'IMAGE_QUALITY: medium' in workflow
    assert 'output/openai-cost-ledger.json' in workflow
    assert "cron: '45 0 * * *'" in workflow
    assert 'seed-topics --blog-count 3 --ppojjugi-count 1 --japan-count 1' in workflow
    assert "OPENAI_BUDGET_REQUIRE_EXISTING_LEDGER: 'true'" in workflow
    assert "cron: '0 1 * * *'" in workflow
    assert "cron: '0 12 * * *'" in workflow
    assert 'ENABLE_MEDIA_GENERATION=false python -m app.main channel naver_blog --limit 3' in workflow
    assert 'ENABLE_MEDIA_GENERATION=true python -m app.main channel ppojjugi_shorts --limit 1 --auto-retry' in workflow
    assert 'ENABLE_MEDIA_GENERATION=true python -m app.main channel japan_shorts --limit 1 --auto-retry' in workflow
    assert 'execute_media' in workflow
    assert 'prepare_topic' in workflow
    assert 'plan_month' in workflow
    assert 'plan-month --blog-count 90 --ppojjugi-count 30 --japan-count 30 --batch-size 10' in workflow
    assert 'Research and save one topic for the selected channel' in workflow
    assert 'seed-topics --blog-count 1 --ppojjugi-count 0 --japan-count 0' in workflow
    assert 'seed-topics --blog-count 0 --ppojjugi-count 1 --japan-count 0' in workflow
    assert 'seed-topics --blog-count 0 --ppojjugi-count 0 --japan-count 1' in workflow
    assert 'default: dry_run' in workflow
    assert '--dry-run --limit 1' in workflow
    assert 'AUTO_PRIVATE_YOUTUBE_UPLOAD' not in workflow
    assert 'upload_private' not in Path('app/pipeline.py').read_text(encoding='utf-8')
    assert 'verify_youtube_auth' in workflow
    assert 'explore_card_design' in workflow
    assert 'explore-card-design' in workflow
    assert 'YOUTUBE_CLIENT_SECRET_JSON_B64' in workflow
    assert 'secrets.BLOG_DATA_SOURCE_ID' not in workflow
    assert 'secrets.SHORTS_DATA_SOURCE_ID' not in workflow


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
    assert 'young_woman(20대 여성)' in japan
    assert 'young_man(20대 남성)' in japan
    assert 'older_woman(70대 여성)' in japan
    assert 'older_man(70대 남성)' in japan
    assert '한 scene 안에는 한 명만 말하게' in japan
    assert 'speaker_profile' in japan
    assert '자연스러운 한국어 반말' in ppojjugi
    assert '`です・ます`체의 딱딱한 존댓말은 기본 대본에 사용하지 않는다' in japan
    assert '자연스러운 한국어 반말' in qa
    assert '친근하고 따뜻한 보통체' in qa
    assert 'channel_style=channel_style' in pipeline
    assert '마스터 가이드' in qa
    assert "ImageOps.fit(source, (1080, 1920)" in media
    assert '"narration": "이 장면에서 들릴 내레이션"' in ppojjugi
    assert '"narration": "この場面で実際に流れるナレーション"' in japan
    assert 'concat_scene_audio' in media
    assert "'scene_durations': durations" in pipeline
