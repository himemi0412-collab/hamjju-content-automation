from pathlib import Path

from app.blog_reference import VISUAL_FAMILIES, validate_generated_reference_contract
from app.card_design import apply_named_design_contract, validate_named_design_contract
from app.pipeline import _card_numbers_from_qa_issue
from app.photographic_cards import (
    _scene_prompt, _subject_lock, _wrap_to_width, load_production_style_bundle, production_design_language,
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
    for name in ('Retro Tech UI', 'Screenshot Editorial', 'Prompt Playground', 'Terminal Noir', 'Technical Manual'):
        assert production_design_language(name) == 'Bento Editorial'
    assert production_design_language('Japanese Editorial') == 'Japanese Editorial'


def test_technical_manual_model_choice_binds_to_photographic_production_contract():
    # Run #404: a diagram blueprint reached a renderer that makes photographs,
    # so the independent card reviewer rejected the same images for missing diagrams.
    generated = apply_named_design_contract({
        'design_language': production_design_language('Technical Manual'),
        'visual_family': 'photographic_lifestyle',
    })
    validate_named_design_contract(generated, required=True)
    assert generated['design_language'] == 'Bento Editorial'
    assert '도면' not in str(generated['design_direction'])


def test_dryer_inspection_scene_does_not_suggest_disassembly_or_wet_parts():
    # Run #406: rejected cards 2-4 showed detached wet components despite
    # the manuscript explicitly requiring visible, non-invasive inspection.
    for index in (2, 3, 4):
        scene = _scene_prompt({
            'headline': '건조기 냄새 점검',
            'copy': '분해하지 말고 보이는 범위부터 확인',
            'items': [{'label': '필터 장착부', 'detail': '잔여 먼지 확인'}],
        }, index)
        assert 'intact front-loading tumble clothes dryer' in scene
        assert 'non-invasive inspection' in scene
        assert 'never depict disassembly, detached trays or tanks on the floor' in scene
        assert 'wet parts or water droplets' in scene


def test_dryer_article_title_controls_all_five_card_subjects_even_when_tank_is_named():
    # Run #409: cards 2-4 said "물통" but omitted "건조기"; the old keyword
    # order selected the dehumidifier prompt and cards 1/5 had no subject lock.
    cards = [
        {'headline': '필터 청소해도 냄새?', 'copy': '필터보다 먼저 볼 곳이 있어요', 'items': []},
        {'headline': '냄새 확인 순서', 'copy': '필터 표면에서 내부와 주변으로',
         'items': [{'label': '물통·통 내부', 'detail': '물통과 드럼 안쪽 확인'}]},
        {'headline': '필터 문제일까, 공간 문제일까', 'copy': '같은 냄새라도 확인할 곳은 달라요',
         'items': [{'label': '주변·내부 공간', 'detail': '물통·드럼·뒤쪽 통풍'}]},
        {'headline': '오늘 바로 확인할 것', 'copy': '분해하지 말고 보이는 범위부터',
         'items': [{'label': '물통과 드럼 확인', 'detail': '보이는 범위 확인'}]},
        {'headline': '계속 나면 이렇게 판단해요', 'copy': '증상을 봐요', 'items': []},
    ]
    for index, card in enumerate(cards, 1):
        scene = _scene_prompt(card, index, subject_hint='건조기에서 먼지 냄새가 날 때?')
        assert 'intact front-loading tumble clothes dryer' in scene
        assert 'removable transparent water tank pulled out' not in scene
        assert 'small natural dust or water traces' not in scene


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


def test_live_card_verification_is_no_save_and_workflow_isolated():
    main = Path('app/main.py').read_text(encoding='utf-8')
    workflow = Path('.github/workflows/daily.yml').read_text(encoding='utf-8')
    command = main.split("@app.command('verify-blog-card-style-live')", 1)[1]
    command = command.split('def print_json', 1)[0]
    assert 'TemporaryDirectory' in command
    assert "'notion_written': False" in command
    assert "'naver_written': False" in command
    assert "'artifact_saved': False" in command
    assert 'generate_and_typeset_blog_cards' in command
    assert "qa_prompt='prompts/qa_photographic_blog_cards.md'" in command
    assert "inputs.mode == 'verify_blog_card_style_live'" in workflow
    assert "inputs.mode != 'verify_blog_card_style_live'" in workflow


def test_five_card_typesetting_uses_distinct_spatial_structures():
    source = Path('app/photographic_cards.py').read_text(encoding='utf-8')
    assert "1: {'panel': (42, 510, 770, 1040)" in source
    assert "2: {'panel': (36, 82, 462, 998)" in source
    assert "3: {'panel': (72, 42, 1008, 490)" in source
    assert "4: {'panel': (532, 102, 1042, 1008)" in source
    assert "5: {'panel': (350, 530, 1038, 1038)" in source
    assert "AI 생성 설명 장면" not in source


def test_scene_prompt_requires_lived_in_photography_and_bans_callouts():
    scene = _scene_prompt({
        'layout': 'cover',
        'headline': '냉장고 고무패킹',
        'copy': '들뜸을 확인해요',
        'items': [{'label': '패킹', 'detail': '물기와 들뜸'}],
    }, 1)
    assert 'slight surface wear' in scene
    assert 'faint fingerprints' in scene
    assert 'perfect showroom cleanliness' in scene
    assert 'circles, arrows, badges' in scene


def test_visual_gate_and_targeted_retries_apply_to_normal_production():
    pipeline = Path('app/pipeline.py').read_text(encoding='utf-8')
    main = Path('app/main.py').read_text(encoding='utf-8')
    assert 'def _visual_qa_passed' in pipeline
    assert "ai_score < 5" in pipeline
    assert "'stage': 'targeted_visual_retry'" in pipeline
    assert 'only_indices=retry_cards' in pipeline
    assert 'for attempt in range(1, 3)' in main
    assert 'only_indices=retry_cards' in main



def test_visual_qa_retry_parser_accepts_singular_and_plural_card_fields():
    assert _card_numbers_from_qa_issue({'card': 1, 'issue': '보조 문구 잘림'}) == {1}
    assert _card_numbers_from_qa_issue({'cards': [2, '3'], 'issue': '피사체 오류'}) == {2, 3}
    assert _card_numbers_from_qa_issue({'cards': 4, 'issue': '점검 장면 오류'}) == {4}


def test_dryer_subject_lock_and_visual_qa_require_recognizable_dryer():
    card = {
        'headline': '건조기 먼지 냄새',
        'copy': '필터보다 먼저 확인해요',
        'items': [{'label': '필터 장착부', 'detail': '필터 홈을 확인'}],
    }
    for index in range(1, 6):
        prompt = _scene_prompt(card, index)
        assert 'FRONT-LOADING CLOTHES DRYER' in prompt
        assert 'recognizable by its real dryer controls, lint-filter location, drum opening or unmistakable laundry-room context' in prompt
        assert 'do not repeat the same centered straight-on full-front composition across cards' in prompt
        assert 'Never show an exterior exhaust hose, rear vent duct' in prompt
        assert 'Never substitute an air purifier' in prompt
    qa = Path('prompts/qa_photographic_blog_cards.md').read_text(encoding='utf-8')
    assert '글 제목과 일치' in qa
    assert '식별할 수 없으면 해당 카드를 blocking issue로 실패 처리' in qa


def test_refrigerator_card_roles_are_subject_locked_without_conflicting_blank_band():
    flow = _scene_prompt(
        {'headline': '냉장고 문틈', 'copy': '고무패킹 홈 오염', 'items': []}, 2
    )
    comparison = _scene_prompt(
        {'headline': '냉장고 문틈', 'copy': '고무패킹 비교', 'items': []}, 3
    )
    assert 'NEVER show a washing machine' in flow
    assert 'extreme documentary close-up' in flow
    assert 'LEFT gasket lies flat' in comparison
    assert 'Do not leave a blank lower area' in comparison
    assert 'lower 28 to 32 percent' not in comparison
    assert 'Never create a large empty lower band' in comparison


def test_renderer_omits_redundant_role_label_that_visual_qa_read_as_clipped_copy():
    renderer = Path('app/photographic_cards.py').read_text(encoding='utf-8')
    assert "draw.text(spec['role'], ROLES[index - 1]" not in renderer
    assert "draw.text(spec['number'], f'{index:02d} / 05'" not in renderer



def test_background_generation_is_photo_only_and_editorial_layers_are_local():
    prompt = _scene_prompt(
        {'headline': '냉장고 문틈', 'copy': '고무패킹 비교', 'items': []}, 3
    )
    assert 'photograph layer only' in prompt
    assert 'Do not design a card' in prompt
    assert 'zero printed material' in prompt
    assert 'pale lavender, muted mint' not in prompt
    renderer = Path('app/photographic_cards.py').read_text(encoding='utf-8')
    assert '(222, 241, 235, 255)' in renderer
    assert "if index == 3:" in renderer
    assert "item_anchor = (" in renderer



def test_comparison_scene_uses_visible_refrigerator_anchors_not_round_washer_parts():
    prompt = _scene_prompt(
        {'headline': '냉장고 문틈', 'copy': '고무패킹 비교', 'items': []}, 3
    )
    assert 'OPEN RECTANGULAR REFRIGERATOR' in prompt
    assert 'interior shelves, bottles and food containers' in prompt
    assert 'Absolutely no circular door' in prompt
    assert 'laundry appliance' in prompt



def test_flow_panel_preserves_more_than_half_the_frame_for_the_photograph():
    renderer = Path('app/photographic_cards.py').read_text(encoding='utf-8')
    assert "2: {'panel': (36, 82, 462, 998)" in renderer
    assert "2: {'panel': (36, 82, 550, 998)" not in renderer


def test_dryer_prompts_preserve_topic_identity_without_repeating_front_view_or_duct():
    card = {
        'headline': '건조기에서 먼지 냄새가 날 때',
        'copy': '필터보다 먼저 배기와 주변 공간을 확인해요',
        'items': [{'label': '측면 공간', 'detail': '주변 먼지와 통풍 여유 확인'}],
    }
    prompts = [_scene_prompt(card, index, subject_hint='건조기') for index in range(1, 6)]
    assert all('intact front-loading tumble clothes dryer' in prompt for prompt in prompts)
    assert all('do not repeat the same centered straight-on full-front composition across cards' in prompt for prompt in prompts)
    assert all('Never show an exterior exhaust hose, rear vent duct' in prompt for prompt in prompts)
    assert all('DRYER SCENE LOCK:' in prompt for prompt in prompts)
    assert len({prompt.split('DRYER SCENE LOCK: ', 1)[1].split('. ', 1)[0] for prompt in prompts}) == 5
    assert len({prompt.split('Production card role: ', 1)[1].split('. ', 1)[1].split(' Generate a plain', 1)[0]
                for prompt in prompts}) == 5
    qa = Path('prompts/qa_photographic_blog_cards.md').read_text(encoding='utf-8')
    assert '같은 제품을 반복하는 장면은 카드 역할별 크롭·각도·거리·행동·배경이 의미 있게 달라야 한다' in qa
    assert '외부 배기 호스·후면 덕트·분리된 환기 연결부' in qa


def test_photographic_card_text_wrap_uses_real_line_breaks():
    from PIL import Image, ImageDraw, ImageFont

    draw = ImageDraw.Draw(Image.new('RGB', (100, 100)))
    wrapped = _wrap_to_width(draw, 'abcdefgh', ImageFont.load_default(), 5)
    assert '\n' in wrapped
    assert '\\n' not in wrapped
