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
            timeout=120.0,
        )

    def close(self):
        self.client.close()

    def query_ready(
        self,
        data_source_id: str,
        status: str,
        channel: str | None = None,
        page_size: int = 10,
        excluded_formula_property: str | None = None,
        excluded_formula_value: str | None = None,
        required_select_values: dict[str, str] | None = None,
        required_number_greater_than: dict[str, float] | None = None,
        sort_property: str | None = None,
    ) -> list[dict[str, Any]]:
        filters: list[dict[str, Any]] = [{'property': '상태', 'select': {'equals': status}}]
        if channel:
            filters.append({'property': '채널', 'select': {'equals': channel}})
        if excluded_formula_property and excluded_formula_value:
            filters.append({
                'property': excluded_formula_property,
                'formula': {'string': {'does_not_equal': excluded_formula_value}},
            })
        for property_name, expected_value in (required_select_values or {}).items():
            filters.append({
                'property': property_name,
                'select': {'equals': expected_value},
            })
        for property_name, minimum_value in (required_number_greater_than or {}).items():
            filters.append({
                'property': property_name,
                'number': {'greater_than': minimum_value},
            })
        body: dict[str, Any] = {
            'filter': filters[0] if len(filters) == 1 else {'and': filters},
            'page_size': min(page_size, 100),
            'sorts': [
                {'property': sort_property, 'direction': 'ascending'}
                if sort_property
                else {'timestamp': 'created_time', 'direction': 'ascending'}
            ],
        }
        r = self.client.post(f'/data_sources/{data_source_id}/query', json=body)
        r.raise_for_status()
        return r.json().get('results', [])

    def query_recent(self, data_source_id: str, page_size: int = 100) -> list[dict[str, Any]]:
        r = self.client.post(f'/data_sources/{data_source_id}/query', json={
            'page_size': min(page_size, 100),
            'sorts': [{'timestamp': 'created_time', 'direction': 'descending'}],
        })
        r.raise_for_status()
        return r.json().get('results', [])

    def create_blog_topic(self, data_source_id: str, topic: dict[str, Any], order: int) -> str:
        properties: dict[str, Any] = {
            '제목': {'title': [{'text': {'content': str(topic['title'])[:2000]}}]},
            '상태': {'select': {'name': '작성 요청'}},
            '선별 상태': {'select': {'name': '추천'}},
            '모델 확인': {'select': {'name': '공식 확인'}},
            # The Notion queue formula treats an empty source order as archived.
            '원본 순서': {'number': order},
            '진행 순서': {'number': order},
            '글 유형': {'select': {'name': '가전제품·인터넷 렌탈'}},
            '세부 주제': {'select': {'name': str(topic.get('detail_topic') or '기타')}},
            '대표 키워드': {'rich_text': rich(str(topic.get('main_keyword') or ''))},
            '보조 키워드': {'rich_text': rich(', '.join(topic.get('sub_keywords') or []))},
            '독자 질문': {'rich_text': rich(str(topic.get('reader_question') or ''))},
            '출처 목록': {'rich_text': rich(str(topic.get('sources') or ''))},
            '키워드 출처': {'select': {'name': '계절·시기'}},
            '말투': {'select': {'name': '햄쮸 톤 · 귀엽고 현실적'}},
        }
        r = self.client.post('/pages', json={
            'parent': {'type': 'data_source_id', 'data_source_id': data_source_id},
            'properties': properties,
        })
        r.raise_for_status()
        return r.json()['id']

    def create_short_topic(self, data_source_id: str, topic: dict[str, Any], channel: str) -> str:
        verified = bool(topic.get('verified_personal_source')) if channel == '햄찌 창작 쇼츠' else True
        status = '작성 요청' if verified else '아이디어'
        properties: dict[str, Any] = {
            '제목': {'title': [{'text': {'content': str(topic['title'])[:2000]}}]},
            '상태': {'select': {'name': status}},
            '채널': {'select': {'name': channel}},
            '콘텐츠 ID': {'rich_text': rich(str(topic.get('content_id') or ''))},
            '다음 행동': {'rich_text': rich(
                '자동 제작 진행' if verified else '사용자 실제 경험 근거 확인 후 제작'
            )},
            '작업 기록': {'rich_text': rich(str(topic.get('concept') or ''))},
            '출처·확인일': {'rich_text': rich(str(topic.get('sources') or ''))},
            '버전': {'number': 1},
            '공개 승인': {'checkbox': False},
        }
        r = self.client.post('/pages', json={
            'parent': {'type': 'data_source_id', 'data_source_id': data_source_id},
            'properties': properties,
        })
        r.raise_for_status()
        return r.json()['id']

    def retrieve_page(self, page_id: str) -> dict[str, Any]:
        r = self.client.get(f'/pages/{page_id}')
        r.raise_for_status()
        return r.json()

    def read_page_blocks(self, page_id: str) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        cursor = None
        while True:
            params: dict[str, Any] = {'page_size': 100}
            if cursor:
                params['start_cursor'] = cursor
            response = self.client.get(f'/blocks/{page_id}/children', params=params)
            response.raise_for_status()
            data = response.json()
            blocks.extend(data.get('results', []))
            if not data.get('has_more'):
                return blocks
            cursor = data.get('next_cursor')
            if not cursor:
                raise RuntimeError('Notion block pagination did not return a cursor')

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
    if typ == 'formula':
        formula_type = (value or {}).get('type')
        return (value or {}).get(formula_type) if formula_type else None
    return None


def extract_page_title(page: dict[str, Any]) -> str:
    """Return the first Notion title property without logging other content."""
    for prop in (page.get('properties') or {}).values():
        if prop.get('type') == 'title':
            title = property_value(prop)
            if title:
                return str(title)
    return '(제목 없음)'


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
