import hashlib
import json

from PIL import Image

from app.card_design import SUPPORTED_DESIGN_LANGUAGES
from app.card_exploration import (
    DEFAULT_SUBTITLE,
    DEFAULT_TITLE,
    render_design_exploration,
)


def test_concept_exploration_renders_twenty_independent_covers_and_five_sheets(tmp_path):
    result = render_design_exploration(DEFAULT_TITLE, tmp_path, DEFAULT_SUBTITLE)

    assert len(result['covers']) == 20
    assert len(result['comparison_sheets']) == 5
    hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in result['covers']]
    assert len(set(hashes)) == 20
    for path in result['covers']:
        with Image.open(path) as image:
            assert image.size == (1080, 1350)
    for path in result['comparison_sheets']:
        with Image.open(path) as image:
            assert image.size == (1800, 2300)

    manifest = json.loads(result['manifest'].read_text(encoding='utf-8'))
    assert manifest['mode'] == 'concept_exploration'
    assert manifest['selection_required_before_production'] is True
    assert [item['design_language'] for item in manifest['covers']] == list(SUPPORTED_DESIGN_LANGUAGES)
    assert manifest['fixed_copy'] == {'title': DEFAULT_TITLE, 'subtitle': DEFAULT_SUBTITLE}

