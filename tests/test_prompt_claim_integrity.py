from pathlib import Path

from app.photographic_cards import _subject_lock


ROOT = Path(__file__).resolve().parents[1]


def test_japan_titles_must_be_supported_by_fact_check_notes():
    japan = (ROOT / 'prompts' / 'japan_shorts.md').read_text(encoding='utf-8')
    qa = (ROOT / 'prompts' / 'qa.md').read_text(encoding='utf-8')
    assert '`title`과 `youtube.title`' in japan
    assert 'テレビ録画の音を作った' in japan
    assert '`title`과 `youtube.title`' in qa
    assert 'fact_check_notes' in qa


def test_dryer_cover_requires_visible_filter_seat():
    instruction = _subject_lock({'headline': '건조기 보풀 필터 청소 방법'}, index=1)
    assert 'filter seat or lower filter opening clearly visible' in instruction
    assert 'attached to the dryer' in instruction


def test_dryer_comparison_is_like_for_like():
    instruction = _subject_lock({'headline': '건조기 보풀 필터 비교'}, index=3)
    assert 'same front-loading tumble clothes dryer' in instruction
    assert 'same camera height, distance, lens, scale, orientation' in instruction
    assert 'same opening' in instruction
    assert 'Do not compare a filter with the dryer side' in instruction


def test_blog_visual_qa_blocks_mismatched_dryer_comparisons():
    qa = (ROOT / 'prompts' / 'qa.md').read_text(encoding='utf-8')
    assert '건조기 옆면·세탁물 바구니·헐거운 보풀' in qa
    assert '같은 카메라 높이·거리·배율·방향' in qa
    assert '스톡 이미지처럼 보이는지' in qa
