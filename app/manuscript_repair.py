"""Apply reviewed, source-hash-bound text corrections before a fresh QA."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from typing import Any


def manuscript_hash(generated: dict) -> str:
    return hashlib.sha256(json.dumps(generated, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def apply_reviewed_corrections(generated: dict, page_id: str, path: Path) -> dict:
    plan = json.loads(path.read_text(encoding='utf-8'))
    if plan.get('page_id') != page_id or plan.get('source_sha256') != manuscript_hash(generated):
        raise ValueError('Reviewed corrections do not match the exact source manuscript')
    output = generated
    for correction in plan.get('replacements', []):
        old, new = correction['old'], correction['new']
        if not isinstance(old, str) or not old or not isinstance(new, str):
            raise ValueError('Correction must be a nonempty exact text replacement')
        count = 0
        def replace(value: Any) -> Any:
            nonlocal count
            if isinstance(value, str):
                count += value.count(old)
                return value.replace(old, new)
            if isinstance(value, dict):
                return {key: replace(item) for key, item in value.items()}
            if isinstance(value, list):
                return [replace(item) for item in value]
            return value
        output = replace(output)
        if count != correction['expected_count']:
            raise ValueError('Reviewed correction occurrence count changed')
    return output
