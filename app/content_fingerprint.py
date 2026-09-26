"""Canonical content identities shared by production and local handoff workers."""
from __future__ import annotations

import hashlib
import json
import unicodedata
from pathlib import Path
from typing import Any


def _canonical_value(value: Any) -> Any:
    if isinstance(value, str):
        normalized = unicodedata.normalize('NFC', value)
        return normalized.replace('\r\n', '\n').replace('\r', '\n')
    if isinstance(value, list):
        return [_canonical_value(item) for item in value]
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError('Canonical content keys must be strings')
            canonical_key = unicodedata.normalize('NFC', key)
            if canonical_key in normalized:
                raise ValueError('Canonical content has duplicate normalized keys')
            normalized[canonical_key] = _canonical_value(item)
        return normalized
    return value


def canonical_content_bytes(generated: dict[str, Any]) -> bytes:
    """Serialize exact manuscript data independent of JSON formatting, key order,
    Unicode composition, or platform line endings.

    Markdown and HTML are kept as source text: converting between them could
    change meaning or displayed content, so it is not a serialization-only step.
    """
    if not isinstance(generated, dict):
        raise TypeError('Canonical content must be an object')
    payload = json.dumps(
        _canonical_value(generated), ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False,
    )
    return payload.encode('utf-8')


def canonical_content_hash(generated: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_content_bytes(generated)).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
