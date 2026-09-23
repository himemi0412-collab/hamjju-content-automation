from pathlib import Path

from app.blog_reference import VISUAL_FAMILIES, validate_generated_reference_contract
from app.photographic_cards import (
    _scene_prompt, _subject_lock, load_production_style_bundle, production_design_language,
)


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


def test_cover_exploration_technology_styles_are_blocked_from_production():
    for name in ('Retro Tech UI', 'Screenshot Editorial', 'Prompt Playground', 'Terminal Noir'):
        assert production_design_language(name) == 'Bento Editorial'
    assert production_design_language('Japanese Editorial') == 'Japanese Editorial'


def test_production_scene_prompt_uses_real_laptop_and_rejects_ai_ui_motifs():
    card = {
        'layout': 'cover',
        'headline': '노트북 발열 점검',
        'copy': 'RAM 추가 뒤 통풍과 팬을 확인해요',
        'items': [{'label': '통풍구', 'detail': '막힘과 먼지 확인'}],
    }
    prompt = _scene_prompt(card, 1, design_language='Screenshot Editorial')
    assert 'real open laptop' in prompt
    assert 'Do not copy cover-exploration motifs' in prompt
    assert 'ABSOLUTELY NO' in prompt
    assert 'Codex' in prompt and 'ChatGPT' in prompt and 'terminal' in prompt
    assert 'specific code, chat, file' not in prompt


def test_production_route_normalises_exploration_style_before_manifest():
    pipeline = Path('app/pipeline.py').read_text(encoding='utf-8')
    assert "generated['design_language'] = production_design_language(" in pipeline
    assert 'from .photographic_cards import generate_and_typeset_blog_cards, production_design_language' in pipeline



def test_v3_production_prompt_and_contract_are_loaded_by_every_scene():
    prompt_text, contract = load_production_style_bundle()
    assert contract['version'].endswith('-v3')
    assert contract['qa']['ai_likeness_max_exclusive'] == 5
    assert contract['qa']['fail_when_score_gte'] == 5
    assert '사람이 편집한' in prompt_text
    card = {
        'layout': 'cover',
        'headline': '냉장고 문틈 점검',
        'copy': '고무패킹 상태를 확인해요',
        'items': [{'label': '고무패킹', 'detail': '들뜸 확인'}],
    }
    scene = _scene_prompt(card, 1)
    assert 'Canonical v3 human-edit signals' in scene
    assert 'strict AI-likeness gate below 5/100' in scene
    assert 'Apply the following canonical production prompt as binding art direction' in scene


def test_production_bundle_is_a_fail_closed_runtime_dependency():
    source = Path('app/photographic_cards.py').read_text(encoding='utf-8')
    assert 'load_production_style_bundle()' in source
    assert 'BLOG_CARD_PRODUCTION_STYLE_BUNDLE_UNAVAILABLE' in source
    assert 'BLOG_CARD_AI_LIKENESS_GATE_INVALID' in source
