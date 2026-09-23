from __future__ import annotations
import json
import mimetypes
import re
import time
from pathlib import Path
from typing import Any
import httpx

NOTION_VERSION = '2026-03-11'
NOTION_SINGLE_PART_LIMIT = 20 * 1024 * 1024
NOTION_PART_SIZE = 10 * 1024 * 1024
NOTION_MAX_PARTS = 1000


class NotionClient:
    def _read_block_children(self, page_id: str, params: dict[str, Any]) -> dict[str, Any]:
        """Retry only transient failures on this safe, read-only request."""
        for attempt in range(4):
            try:
                response = self.client.get(f'/blocks/{page_id}/children', params=params)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in {429, 500, 502, 503, 504, 520, 522} or attempt == 3:
                    raise
            except httpx.TransportError:
                if attempt == 3:
                    raise
            time.sleep(2 ** attempt)
        raise AssertionError('unreachable')

    def __init__(self, token: str, multipart_upload_enabled: bool = False):
        self.token = token
        self.multipart_upload_enabled = multipart_upload_enabled
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
        reader_question = str(topic.get('reader_question') or '')
        faq = [str(x).strip() for x in (topic.get('faq_questions') or []) if str(x).strip()]
        if faq:
            reader_question = reader_question + ('\n' if reader_question else '') + '후속 질문: ' + ' / '.join(faq)
        sources = seo_geo_note(topic, include_sources=True)
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
            '독자 질문': {'rich_text': rich(reader_question)},
            '출처 목록': {'rich_text': rich(sources)},
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
            '작업 기록': {'rich_text': rich(
                str(topic.get('concept') or '') + '\n' + seo_geo_note(topic, include_sources=False)
            )},
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
            data = self._read_block_children(page_id, params)
            blocks.extend(data.get('results', []))
            if not data.get('has_more'):
                return blocks
            cursor = data.get('next_cursor')
            if not cursor:
                raise RuntimeError('Notion block pagination did not return a cursor')

    def read_latest_json_snapshot(self, page_id: str) -> dict[str, Any]:
        """Recover the newest complete automation snapshot from adjacent JSON code blocks."""
        groups: list[list[str]] = []
        current: list[str] = []
        for block in self.read_page_blocks(page_id):
            if block.get('type') == 'code' and block.get('code', {}).get('language') == 'json':
                text = ''.join(x.get('plain_text', '') for x in block['code'].get('rich_text', []))
                current.append(text)
            else:
                if current:
                    groups.append(current)
                    current = []
        if current:
            groups.append(current)
        for chunks in reversed(groups):
            try:
                value = json.loads(''.join(chunks))
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and isinstance(value.get('generated'), dict):
                return value
        raise RuntimeError('NOTION_AUTOMATION_SNAPSHOT_MISSING')

    def read_page_text(self, page_id: str, max_chars: int = 18000) -> str:
        out: list[str] = []
        cursor = None
        while True:
            params: dict[str, Any] = {'page_size': 100}
            if cursor:
                params['start_cursor'] = cursor
            data = self._read_block_children(page_id, params)
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

    def archive_blocks(self, block_ids: list[str]) -> None:
        """Move specified blocks to recoverable Notion Trash, not permanent deletion."""
        for block_id in block_ids:
            # Notion 2026-03-11 uses DELETE for this reversible operation.
            # PATCH archived=true is rejected by the block update endpoint.
            response = self.client.delete(f'/blocks/{block_id}')
            response.raise_for_status()

    def _send_file_part(
        self, upload_id: str, path: Path, content_type: str, content: Any,
        part_number: int | None = None,
    ) -> None:
        data = {'part_number': str(part_number)} if part_number is not None else None
        response = httpx.post(
            f'https://api.notion.com/v1/file_uploads/{upload_id}/send',
            headers={
                'Authorization': f'Bearer {self.token}',
                'Notion-Version': NOTION_VERSION,
            },
            data=data,
            files={'file': (path.name, content, content_type)},
            timeout=120.0,
        )
        response.raise_for_status()

    def can_upload_file(self, path: Path) -> bool:
        return path.stat().st_size <= NOTION_SINGLE_PART_LIMIT or self.multipart_upload_enabled

    def upload_file(self, path: Path) -> str:
        size = path.stat().st_size
        if size > NOTION_SINGLE_PART_LIMIT and not self.multipart_upload_enabled:
            raise ValueError('NOTION_FREE_WORKSPACE_FILE_LIMIT')
        content_type = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        if size <= NOTION_SINGLE_PART_LIMIT:
            create_payload = {
                'mode': 'single_part',
                'filename': path.name,
                'content_type': content_type,
            }
            number_of_parts = 1
        else:
            number_of_parts = (size + NOTION_PART_SIZE - 1) // NOTION_PART_SIZE
            if number_of_parts > NOTION_MAX_PARTS:
                raise ValueError(f'File exceeds Notion multipart upload limit: {path}')
            create_payload = {
                'mode': 'multi_part',
                'filename': path.name,
                'content_type': content_type,
                'number_of_parts': number_of_parts,
            }

        create = self.client.post('/file_uploads', json=create_payload)
        create.raise_for_status()
        upload_id = create.json()['id']

        with path.open('rb') as fh:
            if number_of_parts == 1:
                self._send_file_part(upload_id, path, content_type, fh)
            else:
                for part_number in range(1, number_of_parts + 1):
                    chunk = fh.read(NOTION_PART_SIZE)
                    if not chunk:
                        raise RuntimeError('Notion multipart upload ended before all parts were sent')
                    self._send_file_part(
                        upload_id, path, content_type, chunk, part_number=part_number,
                    )
                complete = self.client.post(f'/file_uploads/{upload_id}/complete', json={})
                complete.raise_for_status()
                if complete.json().get('status') != 'uploaded':
                    raise RuntimeError('Notion multipart upload did not reach uploaded status')
        return upload_id

    def upload_small_file(self, path: Path) -> str:
        """Backward-compatible entry point; automatically uses multipart when needed."""
        return self.upload_file(path)

    def attach_files(self, page_id: str, property_name: str, paths: list[Path]) -> list[str]:
        upload_ids = [self.upload_file(path) for path in paths]
        files = [
            {'type': 'file_upload', 'file_upload': {'id': upload_id}, 'name': path.name}
            for upload_id, path in zip(upload_ids, paths)
        ]
        self.update_properties(page_id, {property_name: {'files': files}})
        return upload_ids


def rich(text: str) -> list[dict[str, Any]]:
    return [{'type': 'text', 'text': {'content': text[:2000]}}]


def markdown_rich(text: str) -> list[dict[str, Any]]:
    """Convert the small inline Markdown subset used by reviewed blog bodies."""
    parts: list[dict[str, Any]] = []
    token = re.compile(
        r'\*\*(.+?)\*\*'  # bold
        r'|__(.+?)__'  # underline
        r'|==(.+?)=='  # non-yellow color emphasis
        r'|\[([^\]]+)\]\((https?://[^\s)]+)\)'  # link
    )
    cursor = 0
    for match in token.finditer(text):
        if match.start() > cursor:
            parts.extend(rich(text[cursor:match.start()]))
        if match.group(1) is not None:
            parts.append({
                'type': 'text',
                'text': {'content': match.group(1)[:2000]},
                'annotations': {'bold': True},
            })
        elif match.group(2) is not None:
            parts.append({
                'type': 'text',
                'text': {'content': match.group(2)[:2000]},
                'annotations': {'underline': True},
            })
        elif match.group(3) is not None:
            parts.append({
                'type': 'text',
                'text': {'content': match.group(3)[:2000]},
                'annotations': {'bold': True, 'color': 'purple_background'},
            })
        else:
            parts.append({
                'type': 'text',
                'text': {'content': match.group(4)[:2000], 'link': {'url': match.group(5)}},
            })
        cursor = match.end()
    if cursor < len(text):
        parts.extend(rich(text[cursor:]))
    return parts or rich('')


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


def _markdown_line_blocks(line: str) -> list[dict[str, Any]]:
    if not line:
        return [{'object': 'block', 'type': 'paragraph', 'paragraph': {'rich_text': []}}]
    heading = re.match(r'^(#{1,3})\s+(.+)$', line)
    if heading:
        kind = f'heading_{len(heading.group(1))}'
        return [{'object': 'block', 'type': kind, kind: {'rich_text': markdown_rich(heading.group(2))}}]
    bullet = re.match(r'^[-*]\s+(.+)$', line)
    if bullet:
        return [{'object': 'block', 'type': 'bulleted_list_item', 'bulleted_list_item': {'rich_text': markdown_rich(bullet.group(1))}}]
    numbered = re.match(r'^\d+\.\s+(.+)$', line)
    if numbered:
        return [{'object': 'block', 'type': 'numbered_list_item', 'numbered_list_item': {'rich_text': markdown_rich(numbered.group(1))}}]
    blocks = []
    for chunk in split_text(line, 1900):
        blocks.append({'object': 'block', 'type': 'paragraph', 'paragraph': {'rich_text': markdown_rich(chunk)}})
    return blocks


def naver_handoff_blocks(
    generated: dict[str, Any],
    qa: dict[str, Any],
    *,
    document_id: str,
    source_version: str,
    card_upload_ids: list[str],
    card_names: list[str],
    ownership_receipt: dict[str, str] | None = None,
    _legacy_paragraph_order: bool = False,
) -> list[dict[str, Any]]:
    """Build the strict, reviewable contract consumed by the local Naver draft saver."""
    cards = list(generated.get('card_news') or [])
    placements = list(generated.get('image_placements') or [])
    if qa.get('pass') is not True or qa.get('blocking_issues'):
        raise ValueError('Naver handoff requires an independent QA pass')
    if not document_id or not source_version:
        raise ValueError('Naver handoff identity is incomplete')
    if not (len(cards) == len(placements) == len(card_upload_ids) == len(card_names) == 5):
        raise ValueError('Naver handoff requires five ordered reviewed cards')

    body = str(generated.get('body_markdown') or '').strip()
    if not body:
        raise ValueError('Naver handoff body is empty')
    body_lines = body.splitlines()
    headings = [
        (line_index, match.group(1).strip())
        for line_index, line in enumerate(body_lines)
        if (match := re.match(r'^#{1,3}\s+(.+)$', line))
    ]
    by_section: dict[str, list[tuple[int, dict[str, Any], str, str]]] = {}
    by_line: dict[int, list[tuple[int, dict[str, Any], str, str]]] = {}
    for index, (card, placement, upload_id, name) in enumerate(
        zip(cards, placements, card_upload_ids, card_names), 1
    ):
        if int(card.get('card') or 0) != index or int(placement.get('card') or 0) != index:
            raise ValueError('Naver handoff card order does not match the reviewed manuscript')
        target = str(placement.get('after_heading') or '').strip()
        if not target:
            raise ValueError('Naver handoff image placement is missing')
        item = (index, card, upload_id, name)
        if target == '도입':
            by_section.setdefault('도입', []).append(item)
            continue
        exact = [heading for _, heading in headings if heading == target]
        target_words = set(re.findall(r'[0-9A-Za-z가-힣.]+', target))
        fuzzy = []
        for _, heading in headings:
            heading_words = set(re.findall(r'[0-9A-Za-z가-힣.]+', heading))
            shared = target_words & heading_words
            if (
                target in heading
                or heading in target
                or (len(shared) >= 2 and len(shared) / max(len(target_words), 1) >= 0.5)
            ):
                fuzzy.append(heading)
        section_matches = exact or fuzzy
        if len(section_matches) == 1:
            by_section.setdefault(section_matches[0], []).append(item)
            continue
        paragraph_matches = [
            line_index for line_index, line in enumerate(body_lines)
            if line.strip().startswith(target)
        ]
        if len(paragraph_matches) == 1:
            by_line.setdefault(paragraph_matches[0], []).append(item)
            continue
        raise ValueError(f'Naver handoff image placement target is missing or ambiguous: {target}')

    def image_blocks(item: tuple[int, dict[str, Any], str, str]) -> list[dict[str, Any]]:
        _, card, upload_id, name = item
        caption = str(card.get('caption') or '').strip()
        disclosure = str(card.get('ai_disclosure') or '').strip()
        if not caption or not disclosure or not upload_id or not name:
            raise ValueError('Naver handoff card caption, disclosure, upload, or name is missing')
        return [
            {
                'object': 'block',
                'type': 'image',
                'image': {
                    'type': 'file_upload',
                    'file_upload': {'id': upload_id},
                    'caption': [],
                },
            },
            {'object': 'block', 'type': 'paragraph', 'paragraph': {'rich_text': markdown_rich(caption)}},
            {'object': 'block', 'type': 'paragraph', 'paragraph': {'rich_text': markdown_rich(disclosure)}},
        ]

    body_blocks: list[dict[str, Any]] = []
    current_section = '도입'
    used_cards: set[int] = set()

    def append_section_images(section: str) -> None:
        for item in by_section.pop(section, []):
            body_blocks.extend(image_blocks(item))
            used_cards.add(item[0])

    for line_index, line in enumerate(body_lines):
        heading = re.match(r'^#{1,3}\s+(.+)$', line)
        if heading:
            append_section_images(current_section)
            current_section = heading.group(1).strip()
        # A paragraph-targeted card (for example the final decision card at
        # "정리하면") is a section boundary too. Flush the current section's
        # summary card before that paragraph so card order cannot become 5, 4
        # when the preceding section is also the document's final heading.
        if line_index in by_line and not _legacy_paragraph_order:
            append_section_images(current_section)
        body_blocks.extend(_markdown_line_blocks(line))
        if line_index in by_line:
            for item in by_line[line_index]:
                body_blocks.extend(image_blocks(item))
                used_cards.add(item[0])
    append_section_images(current_section)
    missing = sorted(set(range(1, 6)) - used_cards)
    if missing:
        raise ValueError('Naver handoff image placements were not applied: ' + ', '.join(map(str, missing)))

    hashtags = [str(x).strip() for x in (generated.get('hashtags') or []) if str(x).strip()]
    if hashtags:
        body_blocks.append({'object': 'block', 'type': 'paragraph', 'paragraph': {'rich_text': rich(' '.join(hashtags))}})

    qa_text = (
        f"PASS={qa.get('pass')} / score={qa.get('score')} / "
        f"blocking={qa.get('blocking_issues', [])} / notes={qa.get('non_blocking_notes', [])}"
    )
    blocks: list[dict[str, Any]] = [
        {'object': 'block', 'type': 'divider', 'divider': {}},
        {'object': 'block', 'type': 'heading_2', 'heading_2': {'rich_text': rich('네이버 임시저장 전달 자료')}},
        {'object': 'block', 'type': 'paragraph', 'paragraph': {'rich_text': rich('저장 준비 완료: READY_FOR_NAVER_DRAFT')}},
        {'object': 'block', 'type': 'paragraph', 'paragraph': {'rich_text': rich(f'원고 ID: {document_id}')}},
        {'object': 'block', 'type': 'paragraph', 'paragraph': {'rich_text': rich(f'버전: {source_version}')}},
        {'object': 'block', 'type': 'paragraph', 'paragraph': {'rich_text': rich('검수: PASS')}},
        {'object': 'block', 'type': 'paragraph', 'paragraph': {'rich_text': rich('이미지 수: 5')}},
        {'object': 'block', 'type': 'paragraph', 'paragraph': {'rich_text': rich('네이버 임시저장만 허용 · 공개/예약발행 금지')}},
        {'object': 'block', 'type': 'heading_3', 'heading_3': {'rich_text': rich('독립 QA')}},
        {'object': 'block', 'type': 'paragraph', 'paragraph': {'rich_text': rich(qa_text)}},
        {'object': 'block', 'type': 'paragraph', 'paragraph': {'rich_text': rich('네이버 본문 시작')}},
        *body_blocks,
        {'object': 'block', 'type': 'paragraph', 'paragraph': {'rich_text': rich('네이버 본문 끝')}},
    ]
    if ownership_receipt:
        required_ownership = {
            'contract_id', 'producer_owner', 'delivery_owner', 'publication_owner', 'stage', 'source_version',
        }
        if not required_ownership.issubset(ownership_receipt):
            raise ValueError('Naver handoff ownership receipt is incomplete')
        if ownership_receipt['source_version'] != source_version or ownership_receipt['stage'] != 'HANDOFF_READY':
            raise ValueError('Naver handoff ownership receipt does not match the reviewed version')
        ownership_blocks = [
            {'object': 'block', 'type': 'paragraph', 'paragraph': {'rich_text': rich(f"운영 계약: {ownership_receipt['contract_id']}")}},
            {'object': 'block', 'type': 'paragraph', 'paragraph': {'rich_text': rich(f"제작 소유자: {ownership_receipt['producer_owner']}")}},
            {'object': 'block', 'type': 'paragraph', 'paragraph': {'rich_text': rich(f"임시저장 소유자: {ownership_receipt['delivery_owner']}")}},
            {'object': 'block', 'type': 'paragraph', 'paragraph': {'rich_text': rich(f"공개 결정 소유자: {ownership_receipt['publication_owner']}")}},
        ]
        blocks[7:7] = ownership_blocks
    snapshot = json.dumps(
        {'generated': generated, 'qa': qa, 'ownership': ownership_receipt},
        ensure_ascii=False,
        indent=2,
    )
    for chunk in split_text(snapshot, 1800):
        blocks.append({'object': 'block', 'type': 'code', 'code': {'language': 'json', 'rich_text': rich(chunk)}})
    return blocks


def result_blocks(
    channel: str,
    generated: dict[str, Any],
    qa: dict[str, Any],
    *,
    naver_handoff: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if channel == 'naver_blog' and naver_handoff is not None:
        return naver_handoff_blocks(generated, qa, **naver_handoff)
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


def seo_geo_note(topic: dict[str, Any], include_sources: bool) -> str:
    """Serialize research metadata into existing Notion fields without schema changes."""
    parts = []
    if include_sources:
        parts.append(str(topic.get('sources') or '').strip())
    for label, value in (
        ('검색 의도', topic.get('search_intent')),
        ('GEO 핵심 답변', topic.get('geo_answer')),
        ('선정 이유·유효기간', topic.get('trend_reason')),
        ('SEO/GEO 점수', f"{int(topic.get('seo_score') or 0)}/{int(topic.get('geo_score') or 0)}"),
    ):
        text = str(value or '').strip()
        if text:
            parts.append(f'{label}: {text}')
    keywords = [str(x).strip() for x in (topic.get('sub_keywords') or []) if str(x).strip()]
    main_keyword = str(topic.get('main_keyword') or '').strip()
    if main_keyword or keywords:
        parts.append('검색 키워드: ' + ', '.join([x for x in [main_keyword, *keywords] if x]))
    return '\n'.join(x for x in parts if x)[:2000]


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
