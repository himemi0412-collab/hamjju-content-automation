from __future__ import annotations
import json
import mimetypes
from pathlib import Path
from typing import Any
import httpx

NOTION_VERSION = '2026-03-11'


class NotionClient:
    def __init__(self, token: str):
        self.token = token
        self.client = httpx.Client(
            base_url='https://api.notion.com/v1',
            headers={
                'Authorization': f'Bearer {token}',
                'Notion-Version': NOTION_VERSION,
                'Content-Type': 'application/json',
            },
            timeout=45.0,
        )

    def close(self):
        self.client.close()

    def query_ready(self, data_source_id: str, status: str, channel: str | None = None, page_size: int = 10) -> list[dict[str, Any]]:
        filters: list[dict[str, Any]] = [{'property': '상태', 'select': {'equals': status}}]
        if channel:
            filters.append({'property': '채널', 'select': {'equals': channel}})
        body: dict[str, Any] = {
            'filter': filters[0] if len(filters) == 1 else {'and': filters},
            'page_size': min(page_size, 100),
            'sorts': [{'timestamp': 'created_time', 'direction': 'ascending'}],
        }
        r = self.client.post(f'/data_sources/{data_source_id}/query', json=body)
        r.raise_for_status()
        return r.json().get('results', [])

    def retrieve_page(self, page_id: str) -> dict[str, Any]:
        r = self.client.get(f'/pages/{page_id}')
        r.raise_for_status()
        return r.json()

    def read_page_text(self, page_id: str, max_chars: int = 18000) -> str:
        out: list[str] = []
        cursor = None
        while True:
            params: dict[str, Any] = {'page_size': 100}
            if cursor:
                params['start_cursor'] = cursor
            r = self.client.get(f'/blocks/{page_id}/children', params=params)
            r.raise_for_status()
            data = r.json()
            for block in data.get('results', []):
                kind = block.get('type')
                payload = block.get(kind, {}) if kind else {}
                rich_text = payload.get('rich_text', [])
                text = ''.join(x.get('plain_text', '') for x in rich_text)
                if text:
                    out.append(text)
                if sum(map(len, out)) >= max_chars:
                    return '\n'.join(out)[:max_chars]
            if not data.get('has_more'):
                break
            cursor = data.get('next_cursor')
        return '\n'.join(out)[:max_chars]

    def update_status(self, page_id: str, status: str) -> None:
        self.update_properties(page_id, {'상태': {'select': {'name': status}}})

    def update_properties(self, page_id: str, properties: dict[str, Any]) -> None:
        r = self.client.patch(f'/pages/{page_id}', json={'properties': properties})
        r.raise_for_status()

    def append_blocks(self, page_id: str, blocks: list[dict[str, Any]]) -> None:
        for i in range(0, len(blocks), 100):
            r = self.client.patch(f'/blocks/{page_id}/children', json={'children': blocks[i:i+100]})
            r.raise_for_status()

    def upload_small_file(self, path: Path) -> str:
        if path.stat().st_size > 20 * 1024 * 1024:
            raise ValueError(f'File is larger than Notion single-part limit: {path}')
        content_type = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        create = self.client.post('/file_uploads', json={
            'mode': 'single_part',
            'filename': path.name,
            'content_type': content_type,
        })
        create.raise_for_status()
        upload_id = create.json()['id']
        with path.open('rb') as fh:
            send = httpx.post(
                f'https://api.notion.com/v1/file_uploads/{upload_id}/send',
                headers={
                    'Authorization': f'Bearer {self.token}',
                    'Notion-Version': NOTION_VERSION,
                },
                files={'file': (path.name, fh, content_type)},
                timeout=120.0,
            )
        send.raise_for_status()
        return upload_id

    def attach_files(self, page_id: str, property_name: str, paths: list[Path]) -> list[str]:
        upload_ids = [self.upload_small_file(path) for path in paths]
        files = [
            {'type': 'file_upload', 'file_upload': {'id': upload_id}, 'name': path.name}
            for upload_id, path in zip(upload_ids, paths)
        ]
        self.update_properties(page_id, {property_name: {'files': files}})
        return upload_ids


def rich(text: str) -> list[dict[str, Any]]:
    return [{'type': 'text', 'text': {'content': text[:2000]}}]


def split_text(text: str, limit: int) -> list[str]:
    text = text.strip()
    if not text:
        return []
    chunks: list[str] = []
    while len(text) > limit:
        cut = text.rfind('\n', 0, limit)
        if cut < limit // 2:
            cut = text.rfind(' ', 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        chunks.append(text)
    return chunks


def result_blocks(channel: str, generated: dict[str, Any], qa: dict[str, Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [
        {'object': 'block', 'type': 'divider', 'divider': {}},
        {'object': 'block', 'type': 'heading_2', 'heading_2': {'rich_text': rich('자동화 생성 결과')}},
        {'object': 'block', 'type': 'paragraph', 'paragraph': {'rich_text': rich(f'채널: {channel}')}},
    ]
    title = generated.get('title')
    if title:
        blocks.append({'object': 'block', 'type': 'heading_3', 'heading_3': {'rich_text': rich(str(title))}})

    body = generated.get('body_markdown') or generated.get('narration') or ''
    for paragraph in split_text(str(body), 1800):
        blocks.append({'object': 'block', 'type': 'paragraph', 'paragraph': {'rich_text': rich(paragraph)}})

    cards = generated.get('card_news') or []
    if cards:
        blocks.append({'object': 'block', 'type': 'heading_3', 'heading_3': {'rich_text': rich('카드뉴스 구성')}})
        for card in cards:
            txt = f"{card.get('card', '')}. {card.get('headline', '')} — {card.get('copy', '')} / {card.get('visual', '')}"
            for chunk in split_text(txt, 1800):
                blocks.append({'object': 'block', 'type': 'bulleted_list_item', 'bulleted_list_item': {'rich_text': rich(chunk)}})

    scenes = generated.get('scenes') or []
    if scenes:
        blocks.append({'object': 'block', 'type': 'heading_3', 'heading_3': {'rich_text': rich('장면표')}})
        for scene in scenes:
            txt = f"장면 {scene.get('scene', '')} · {scene.get('seconds', '')}초 · {scene.get('caption', '')} · {scene.get('image_prompt', '')}"
            for chunk in split_text(txt, 1800):
                blocks.append({'object': 'block', 'type': 'bulleted_list_item', 'bulleted_list_item': {'rich_text': rich(chunk)}})

    blocks.append({'object': 'block', 'type': 'heading_3', 'heading_3': {'rich_text': rich('독립 QA')}})
    qa_txt = f"PASS={qa.get('pass')} / score={qa.get('score')} / blocking={qa.get('blocking_issues', [])} / notes={qa.get('non_blocking_notes', [])}"
    for chunk in split_text(qa_txt, 1800):
        blocks.append({'object': 'block', 'type': 'paragraph', 'paragraph': {'rich_text': rich(chunk)}})

    snapshot = json.dumps({'generated': generated, 'qa': qa}, ensure_ascii=False, indent=2)
    for chunk in split_text(snapshot, 1800):
        blocks.append({'object': 'block', 'type': 'code', 'code': {'language': 'json', 'rich_text': rich(chunk)}})
    return blocks


def property_value(prop: dict[str, Any]) -> Any:
    typ = prop.get('type')
    value = prop.get(typ)
    if typ in ('title', 'rich_text'):
        return ''.join(x.get('plain_text', '') for x in (value or []))
    if typ in ('select', 'status'):
        return (value or {}).get('name')
    if typ in ('number', 'url', 'checkbox', 'created_time', 'last_edited_time'):
        return value
    if typ == 'multi_select':
        return [x.get('name') for x in (value or [])]
    if typ == 'date':
        return value
    return None


def compact_page_context(page: dict[str, Any], page_text: str) -> dict[str, Any]:
    props = {}
    for name, prop in (page.get('properties') or {}).items():
        val = property_value(prop)
        if val not in (None, '', [], False):
            props[name] = val
    return {
        'page_id': page.get('id'),
        'url': page.get('url'),
        'last_edited_time': page.get('last_edited_time'),
        'properties': props,
        'existing_page_text': page_text,
    }
