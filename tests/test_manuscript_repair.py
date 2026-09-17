import json
import pytest
from app.manuscript_repair import apply_reviewed_corrections, manuscript_hash


def test_reviewed_replacement_preserves_other_text_and_requires_exact_source(tmp_path):
    generated = {'body_markdown': 'old source / keep', 'card_news': [{'source_urls': ['old source']}]}
    plan = {'page_id': 'one', 'source_sha256': manuscript_hash(generated),
            'replacements': [{'old': 'old source', 'new': 'direct official source', 'expected_count': 2}]}
    path = tmp_path / 'repair.json'
    path.write_text(json.dumps(plan), encoding='utf-8')
    fixed = apply_reviewed_corrections(generated, 'one', path)
    assert fixed['body_markdown'] == 'direct official source / keep'
    assert generated['body_markdown'] == 'old source / keep'
    with pytest.raises(ValueError, match='exact source'):
        apply_reviewed_corrections({'body_markdown': 'changed'}, 'one', path)
    plan['replacements'][0]['expected_count'] = 1
    path.write_text(json.dumps(plan), encoding='utf-8')
    with pytest.raises(ValueError, match='occurrence count'):
        apply_reviewed_corrections(generated, 'one', path)
