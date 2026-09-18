from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


REFERENCE_PROFILE_ID = 'HAMZZU_NAVER_REFERENCE_V1'
REFERENCE_PATH = Path(__file__).resolve().parents[1] / 'references' / 'naver_blog_baseline.md'
REFERENCE_URLS = (
    'https://blog.naver.com/himemi0412/224412841652',
    'https://blog.naver.com/himemi0412/224408237891',
    'https://blog.naver.com/himemi0412/224404549601',
    'https://blog.naver.com/himemi0412/224398911774',
)
CARD_FORMATS = {'square', 'landscape_4_3'}
VISUAL_FAMILIES = {'soft_scene', 'playful_diagram', 'contract_notebook', 'clipboard_checklist'}


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


def validate_generated_reference_contract(generated: dict[str, Any], baseline: dict[str, Any]) -> None:
    if generated.get('reference_profile_id') != baseline.get('id'):
        raise RuntimeError('REFERENCE_PROFILE_MISSING: generated blog did not acknowledge the mandatory baseline')
    if generated.get('card_format') not in CARD_FORMATS:
        raise RuntimeError('REFERENCE_FORMAT_INVALID: choose square or landscape_4_3')
    if generated.get('visual_family') not in VISUAL_FAMILIES:
        raise RuntimeError('REFERENCE_VISUAL_FAMILY_INVALID')
    if len(generated.get('card_news') or []) != 5:
        raise RuntimeError('REFERENCE_CARD_COUNT_INVALID')
