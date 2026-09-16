import base64
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.ai import estimate_response_cost_usd
from app.budget import BudgetGuard, BudgetLimitReached
from app.media import MediaGenerator


def test_budget_ledger_persists_settled_estimate_and_resets_next_month(tmp_path):
    ledger = tmp_path / 'ledger.json'
    september = datetime(2026, 9, 17, tzinfo=timezone.utc)
    guard = BudgetGuard(ledger, 1.0, '2026-09', 0.2, now=september)

    event_id = guard.reserve('text', 0.3)
    assert guard.snapshot()['estimated_spend_usd'] == 0.5
    guard.settle(event_id, 0.1)
    assert guard.snapshot()['estimated_spend_usd'] == 0.3

    restored = BudgetGuard(ledger, 1.0, now=september)
    assert restored.snapshot()['estimated_spend_usd'] == 0.3
    with pytest.raises(BudgetLimitReached):
        restored.reserve('image', 0.7)

    october = BudgetGuard(ledger, 1.0, '2026-09', 0.2, now=datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert october.snapshot()['estimated_spend_usd'] == 0.0


def test_corrupt_budget_ledger_fails_closed(tmp_path):
    ledger = tmp_path / 'ledger.json'
    ledger.write_text('{not-json', encoding='utf-8')
    with pytest.raises(RuntimeError, match='budget ledger is unreadable'):
        BudgetGuard(ledger, 22.0)


def test_missing_required_ledger_fails_closed_without_current_month_baseline(tmp_path):
    with pytest.raises(RuntimeError, match='budget ledger is missing'):
        BudgetGuard(
            tmp_path / 'ledger.json',
            22.0,
            baseline_month='2026-08',
            baseline_usd=0.0,
            require_existing=True,
            now=datetime(2026, 9, 17, tzinfo=timezone.utc),
        )


def test_current_month_baseline_can_bootstrap_required_ledger(tmp_path):
    guard = BudgetGuard(
        tmp_path / 'ledger.json',
        22.0,
        baseline_month='2026-09',
        baseline_usd=0.64,
        require_existing=True,
        now=datetime(2026, 9, 17, tzinfo=timezone.utc),
    )
    assert guard.snapshot()['estimated_spend_usd'] == 0.64


def test_text_cost_estimate_counts_tokens_and_web_search():
    response = SimpleNamespace(
        usage=SimpleNamespace(model_dump=lambda: {'input_tokens': 1000, 'output_tokens': 500}),
        output=[SimpleNamespace(type='web_search_call')],
    )
    estimated = estimate_response_cost_usd(response)
    assert estimated >= (1000 * 0.20 / 1_000_000 + 500 * 1.20 / 1_000_000 + 0.01)


def test_unknown_text_model_fails_closed_in_cost_estimator():
    response = SimpleNamespace(usage=None, output=[])
    with pytest.raises(RuntimeError, match='No conservative budget rate'):
        estimate_response_cost_usd(response, 'unpriced-model')


def test_image_generation_is_pinned_to_medium(monkeypatch, tmp_path):
    calls = []

    class FakeImages:
        def generate(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(b'image').decode())])

    fake_client = SimpleNamespace(images=FakeImages())
    monkeypatch.setattr('app.media.OpenAI', lambda **_kwargs: fake_client)

    media = MediaGenerator('test-key', 'gpt-image-2', 'gpt-4o-mini-tts', 'coral')
    paths = media.generate_scene_images([{'caption': 'test'}], tmp_path)

    assert len(paths) == 1
    assert calls[0]['quality'] == 'medium'
    assert calls[0]['size'] == '1024x1536'
