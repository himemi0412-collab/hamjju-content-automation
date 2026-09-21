from pathlib import Path

from app.card_design import DESIGN_BLUEPRINT_KEYS, DESIGN_DIRECTION_KEYS, SUPPORTED_DESIGN_LANGUAGES


ROOT = Path(__file__).resolve().parents[1]


def test_generation_prompt_names_every_supported_design_language():
    prompt = (ROOT / 'prompts' / 'blog.md').read_text(encoding='utf-8')

    assert '`예쁘게`, `깔끔하게` 같은 추상 표현으로 끝내지 않는다' in prompt
    assert '"design_language"' in prompt
    assert '표지 콘셉트를 먼저 설계' in prompt
    assert 'design_blueprint' in prompt
    for name in SUPPORTED_DESIGN_LANGUAGES:
        assert f'`{name}`' in prompt


def test_independent_qa_checks_declared_style_against_actual_png():
    prompt = (ROOT / 'prompts' / 'qa.md').read_text(encoding='utf-8')

    assert 'generated.design_language' in prompt
    assert '이름표에 그치지 않고 실제 PNG' in prompt
    assert '화이트 배경+둥근 박스+아이콘 기본형' in prompt
    assert 'generated.design_blueprint' in prompt
    for field in DESIGN_DIRECTION_KEYS:
        assert f'`{field}`' in prompt
    for field in DESIGN_BLUEPRINT_KEYS:
        assert f'`{field}`' in prompt


def test_visual_first_contract_is_enforced_in_generation_and_qa():
    generation = (ROOT / 'prompts' / 'blog.md').read_text(encoding='utf-8')
    qa = (ROOT / 'prompts' / 'qa.md').read_text(encoding='utf-8')
    renderer = (ROOT / 'app' / 'media.py').read_text(encoding='utf-8')

    assert '60~70%' in generation
    assert '30~40%' in generation
    assert '공백 포함 18자' in generation
    assert '코드 렌더러가 별도 타이포그래피' in generation
    assert '큰 한글이 첫인상을 독점' in qa
    assert "title_size = min(named_style['title_size'], 54)" in renderer
    assert "copy_size = min(named_style['copy_size'], 25)" in renderer
