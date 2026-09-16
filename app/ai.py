from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Any
from openai import OpenAI


class AIClient:
    def __init__(self, api_key: str, text_model: str, qa_model: str, enable_web_research: bool = True):
        self.client = OpenAI(api_key=api_key)
        self.text_model = text_model
        self.qa_model = qa_model
        self.enable_web_research = enable_web_research

    def generate(self, prompt_path: str | Path, context: dict[str, Any], use_web: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
        system = Path(prompt_path).read_text(encoding='utf-8')
        kwargs: dict[str, Any] = {
            'model': self.text_model,
            'input': [
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': '현재 작업 데이터:\n' + json.dumps(context, ensure_ascii=False, indent=2)},
            ],
        }
        if use_web and self.enable_web_research:
            kwargs['tools'] = [{'type': 'web_search'}]
        response = self.client.responses.create(**kwargs)
        return parse_json(response.output_text), usage_dict(response)

    def research_topics(self, context: dict[str, Any], prompt_path: str | Path = 'prompts/topic_radar.md') -> tuple[dict[str, Any], dict[str, Any]]:
        system = Path(prompt_path).read_text(encoding='utf-8')
        kwargs: dict[str, Any] = {
            'model': self.text_model,
            'input': [
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': '현재 대기열과 요청 수량:\n' + json.dumps(context, ensure_ascii=False, indent=2)},
            ],
        }
        if self.enable_web_research:
            kwargs['tools'] = [{'type': 'web_search'}]
        response = self.client.responses.create(**kwargs)
        return parse_json(response.output_text), usage_dict(response)

    def qa(self, generated: dict[str, Any], context: dict[str, Any], qa_prompt: str | Path = 'prompts/qa.md') -> tuple[dict[str, Any], dict[str, Any]]:
        system = Path(qa_prompt).read_text(encoding='utf-8')
        response = self.client.responses.create(
            model=self.qa_model,
            input=[
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': json.dumps({'source_context': context, 'generated': generated}, ensure_ascii=False)},
            ],
        )
        return parse_json(response.output_text), usage_dict(response)


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
