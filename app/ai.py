from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Any
from openai import OpenAI

from .budget import BudgetGuard


class AIClient:
    def __init__(
        self,
        api_key: str,
        text_model: str,
        qa_model: str,
        enable_web_research: bool = True,
        budget: BudgetGuard | None = None,
        text_reserve_usd: float = 0.04,
        web_text_reserve_usd: float = 0.08,
        max_generation_output_tokens: int = 12000,
        max_qa_output_tokens: int = 6000,
    ):
        self.client = OpenAI(api_key=api_key)
        self.text_model = text_model
        self.qa_model = qa_model
        self.enable_web_research = enable_web_research
        self.budget = budget
        self.text_reserve_usd = text_reserve_usd
        self.web_text_reserve_usd = web_text_reserve_usd
        self.max_generation_output_tokens = max_generation_output_tokens
        self.max_qa_output_tokens = max_qa_output_tokens

    def generate(self, prompt_path: str | Path, context: dict[str, Any], use_web: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
        system = Path(prompt_path).read_text(encoding='utf-8')
        kwargs: dict[str, Any] = {
            'model': self.text_model,
            'max_output_tokens': self.max_generation_output_tokens,
            'input': [
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': '현재 작업 데이터:\n' + json.dumps(context, ensure_ascii=False, indent=2)},
            ],
        }
        if use_web and self.enable_web_research:
            kwargs['tools'] = [{'type': 'web_search'}]
        response = self._create_response(kwargs, 'text_generation', bool(kwargs.get('tools')))
        return parse_json(response.output_text), usage_dict(response)

    def research_topics(self, context: dict[str, Any], prompt_path: str | Path = 'prompts/topic_radar.md') -> tuple[dict[str, Any], dict[str, Any]]:
        system = Path(prompt_path).read_text(encoding='utf-8')
        kwargs: dict[str, Any] = {
            'model': self.text_model,
            'max_output_tokens': self.max_generation_output_tokens,
            'input': [
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': '현재 대기열과 요청 수량:\n' + json.dumps(context, ensure_ascii=False, indent=2)},
            ],
        }
        if self.enable_web_research:
            kwargs['tools'] = [{'type': 'web_search'}]
        response = self._create_response(kwargs, 'topic_research', bool(kwargs.get('tools')))
        return parse_json(response.output_text), usage_dict(response)

    def qa(self, generated: dict[str, Any], context: dict[str, Any], qa_prompt: str | Path = 'prompts/qa.md') -> tuple[dict[str, Any], dict[str, Any]]:
        system = Path(qa_prompt).read_text(encoding='utf-8')
        response = self._create_response({
            'model': self.qa_model,
            'max_output_tokens': self.max_qa_output_tokens,
            'input': [
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': json.dumps({'source_context': context, 'generated': generated}, ensure_ascii=False)},
            ],
        }, 'qa', False)
        return parse_json(response.output_text), usage_dict(response)

    def _create_response(self, kwargs: dict[str, Any], category: str, uses_web: bool):
        reservation_id = None
        if self.budget:
            reservation_id = self.budget.reserve(
                category,
                self.web_text_reserve_usd if uses_web else self.text_reserve_usd,
                {'model': kwargs.get('model'), 'uses_web': uses_web},
            )
        response = self.client.responses.create(**kwargs)
        if self.budget and reservation_id:
            self.budget.settle(
                reservation_id,
                estimate_response_cost_usd(response, str(kwargs.get('model') or '')),
            )
        return response


def parse_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', text, flags=re.S)
        if match:
            return json.loads(match.group(0))
        raise ValueError('Model did not return valid JSON')


def usage_dict(response: Any) -> dict[str, Any]:
    usage = getattr(response, 'usage', None)
    if not usage:
        return {}
    if hasattr(usage, 'model_dump'):
        return usage.model_dump()
    try:
        return dict(usage)
    except Exception:
        return {'repr': repr(usage)}


def estimate_response_cost_usd(response: Any, model: str = 'gpt-5.6-luna') -> float:
    """Conservative text estimate including hosted web-search calls."""
    rates = {
        'gpt-5.6-luna': (0.20, 1.20),
    }
    if model not in rates:
        raise RuntimeError(f'No conservative budget rate is configured for model: {model}')
    input_rate, output_rate = rates[model]
    usage = usage_dict(response)
    input_tokens = int(usage.get('input_tokens') or 0)
    output_tokens = int(usage.get('output_tokens') or 0)
    web_calls = 0
    for item in getattr(response, 'output', None) or []:
        item_type = getattr(item, 'type', None)
        if item_type is None and isinstance(item, dict):
            item_type = item.get('type')
        if item_type == 'web_search_call':
            web_calls += 1
    # Full input rate is used even when caching may make the real charge lower.
    token_cost = input_tokens * input_rate / 1_000_000 + output_tokens * output_rate / 1_000_000
    return max((token_cost + web_calls * 0.01) * 1.10, 0.001)
