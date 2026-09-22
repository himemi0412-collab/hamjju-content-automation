from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .card_design import validate_named_design_contract


REFERENCE_PROFILE_ID = 'HAMZZU_NAVER_REFERENCE_V1'
REFERENCE_PATH = Path(__file__).resolve().parents[1] / 'references' / 'naver_blog_baseline.md'
REFERENCE_URLS = (
    'https://blog.naver.com/himemi0412/224412841652',
    'https://blog.naver.com/himemi0412/224408237891',
    'https://blog.naver.com/himemi0412/224404549601',
    'https://blog.naver.com/himemi0412/224398911774',
)
CARD_FORMATS = {'square', 'landscape_4_3'}
VISUAL_FAMILIES = {
    'photographic_lifestyle',
    'soft_scene', 'playful_diagram', 'contract_notebook', 'clipboard_checklist',
}


def load_blog_reference(path: str | Path = REFERENCE_PATH) -> dict[str, Any]:
    source = Path(path)
    if not source.is_file():
        raise RuntimeError(f'Missing mandatory Naver reference baseline: {source}')
    text = source.read_text(encoding='utf-8')
    missing = [value for value in (REFERENCE_PROFILE_ID, *REFERENCE_URLS) if value not in text]
    if missing:
        raise RuntimeError(f'Incomplete Naver reference baseline: {missing}')
    return {
        'id': REFERENCE_PROFILE_ID,
        'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
        'source_urls': list(REFERENCE_URLS),
        'rules_markdown': text,
    }


def validate_generated_reference_contract(
    generated: dict[str, Any], baseline: dict[str, Any], *, require_design_language: bool = True,
) -> None:
    if generated.get('reference_profile_id') != baseline.get('id'):
        raise RuntimeError('REFERENCE_PROFILE_MISSING: generated blog did not acknowledge the mandatory baseline')
    card_format = str(generated.get('card_format') or '').strip().lower()
    format_aliases = {
        '1:1': 'square',
        '1080x1080': 'square',
        '1080×1080': 'square',
        '4:3': 'landscape_4_3',
        '1448x1086': 'landscape_4_3',
        '1448×1086': 'landscape_4_3',
    }
    card_format = format_aliases.get(card_format, card_format)
    if card_format in {'', 'square 또는 landscape_4_3', 'square or landscape_4_3'}:
        card_format = 'square'
    if card_format not in CARD_FORMATS:
        raise RuntimeError('REFERENCE_FORMAT_INVALID: choose square or landscape_4_3')
    generated['card_format'] = card_format
    visual_family = str(generated.get('visual_family') or '').strip().lower()
    visual_family = {
        'photo': 'photographic_lifestyle',
        'photographic': 'photographic_lifestyle',
        'lifestyle_photo': 'photographic_lifestyle',
        'photo_lifestyle': 'photographic_lifestyle',
    }.get(visual_family, visual_family)
    generated['visual_family'] = visual_family
    if visual_family not in VISUAL_FAMILIES:
        raise RuntimeError('REFERENCE_VISUAL_FAMILY_INVALID')
    validate_named_design_contract(generated, required=require_design_language)
    if len(generated.get('card_news') or []) != 5:
        raise RuntimeError('REFERENCE_CARD_COUNT_INVALID')
