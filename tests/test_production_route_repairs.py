from pathlib import Path

from app.blog_reference import VISUAL_FAMILIES, validate_generated_reference_contract
from app.photographic_cards import _subject_lock


def _reference_ready(visual_family: str) -> dict:
    return {
        'reference_profile_id': 'baseline',
        'card_format': 'square',
        'visual_family': visual_family,
        'card_news': [{}, {}, {}, {}, {}],
    }


def test_photographic_visual_family_is_an_allowed_production_contract():
    assert 'photographic_lifestyle' in VISUAL_FAMILIES
    generated = _reference_ready('photographic')
    validate_generated_reference_contract(generated, {'id': 'baseline'}, require_design_language=False)
    assert generated['visual_family'] == 'photographic_lifestyle'


def test_subject_locks_name_the_exact_failed_appliance_parts():
    gasket = _subject_lock({'headline': '냉장고 고무패킹 곰팡이', 'items': []})
    aircon = _subject_lock({'headline': '에어컨 점검', 'items': [{'label': '배수호스', 'detail': '꺾임 확인'}]})
    tank = _subject_lock({'headline': '제습기 물통 말리기', 'items': []})
    assert 'door gasket' in gasket and 'washing machine' in gasket
    assert 'drain hose' in aircon and 'ruler' in aircon
    assert 'dehumidifier' in tank and 'water tank' in tank


def test_daily_blog_route_uses_generated_scene_then_local_korean_typesetting():
    pipeline = Path('app/pipeline.py').read_text(encoding='utf-8')
    assert "generated['visual_family'] = 'photographic_lifestyle'" in pipeline
    scheduled_route = pipeline.split("if cfg.content_kind == 'blog':\n                card_news", 1)[1]
    scheduled_route = scheduled_route.split("elif cfg.content_kind == 'shorts'", 1)[0]
    assert 'generate_and_typeset_blog_cards(' in scheduled_route
    assert "qa_prompt='prompts/qa_photographic_blog_cards.md'" in scheduled_route


def test_shorts_prompts_supply_pre_media_voice_and_timeline_contracts():
    ppijuk = Path('prompts/ppojjugi.md').read_text(encoding='utf-8')
    japan = Path('prompts/japan_shorts.md').read_text(encoding='utf-8')
    assert 'target_duration_seconds' in ppijuk
    assert '"profile": "young_woman"' in ppijuk
    assert '"speaker_profile": "young_woman"' in ppijuk
    assert 'time_period' in japan
    assert 'present_day' in japan and 'showa_past' in japan
