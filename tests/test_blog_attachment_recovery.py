import hashlib
from types import SimpleNamespace

import pytest

from app.pipeline import match_attached_cards


@pytest.mark.parametrize('change', [None, 'bytes', 'order', 'count', 'missing_receipt'])
def test_reconcile_interrupted_upload_only_when_all_original_bytes_match(monkeypatch, change):
    data = {str(i): f'original-card-{i}'.encode() for i in range(5)}
    files = [{'name': f'card_{i}.png', 'type': 'file', 'file': {'url': str(i)}} for i in range(5)]
    receipt = [{'name': f['name'], 'sha256': hashlib.sha256(data[str(i)]).hexdigest()} for i, f in enumerate(files)]
    if change == 'bytes':
        data['2'] = b'user-edited-image'
    elif change == 'order':
        files.reverse()
    elif change == 'count':
        files.pop()
    elif change == 'missing_receipt':
        receipt = []
    monkeypatch.setattr('app.pipeline.httpx.get', lambda url, **kwargs: SimpleNamespace(content=data[url], raise_for_status=lambda: None))
    if change:
        with pytest.raises(RuntimeError, match='MANUAL_EDIT_CONFLICT'):
            match_attached_cards(files, receipt)
    else:
        match_attached_cards(files, receipt)
