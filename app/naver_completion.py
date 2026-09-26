"""Wait for and read back the local worker's Naver draft completion in Notion."""
from __future__ import annotations

import time
from typing import Callable

import httpx

from .notion_client import extract_page_title, property_value

DONE = '임시저장 완료'
FAILED = {'수정 필요', '실패'}


def confirm_naver_results(
    data: dict,
    token: str,
    *,
    timeout_seconds: int = 900,
    interval_seconds: int = 60,
    client_factory: Callable = httpx.Client,
    sleep: Callable = time.sleep,
    monotonic: Callable = time.monotonic,
) -> dict:
    """Resolve pending blog handoffs from Notion, the shared completion source.

    A blog row completes only after the local worker has verified the Naver
    draft and written the terminal status back to that exact Notion page.
    """
    rows = [
        result
        for batch in data.get('batches', [])
        if batch.get('channel') == 'naver_blog'
        for result in batch.get('results', [])
    ]
    pending = {}
    for row in rows:
        row['naver_draft_verified'] = False
        row['naver_draft_state'] = 'pending'
        page_id = str(row.get('page_id') or '').strip()
        if not page_id:
            row['naver_draft_state'] = 'failed'
            row['naver_draft_reason'] = 'Notion 페이지 식별 정보 없음'
        else:
            pending[page_id] = row

    if not pending:
        return data
    if not token:
        for row in pending.values():
            row['naver_draft_state'] = 'read_failed'
            row['naver_draft_reason'] = 'Notion 완료 상태 확인 인증 정보 없음'
        return data

    deadline = monotonic() + max(timeout_seconds, 0)
    headers = {'Authorization': f'Bearer {token}', 'Notion-Version': '2025-09-03'}
    with client_factory(base_url='https://api.notion.com/v1', headers=headers, timeout=30) as client:
        while pending:
            for page_id, row in list(pending.items()):
                try:
                    response = client.get(f'/pages/{page_id}')
                    response.raise_for_status()
                    page = response.json()
                    props = page.get('properties') or {}
                    status = property_value(props.get('상태') or {})
                    title_matches = extract_page_title(page) == row.get('title')
                    url_prop = props.get('네이버 임시저장 주소')
                    url_matches = url_prop is None or bool(property_value(url_prop))
                except (httpx.HTTPError, KeyError, TypeError, ValueError):
                    row['naver_draft_state'] = 'read_failed'
                    row['naver_draft_reason'] = 'Notion 완료 상태 확인 실패'
                    pending.pop(page_id)
                    continue

                if status == DONE:
                    if title_matches and url_matches:
                        row['naver_draft_verified'] = True
                        row['naver_draft_state'] = 'verified'
                    else:
                        row['naver_draft_state'] = 'failed'
                        row['naver_draft_reason'] = '임시저장 완료 기록의 제목 또는 주소 불일치'
                    pending.pop(page_id)
                elif status in FAILED:
                    row['status'] = status
                    row['naver_draft_state'] = 'failed'
                    row['naver_draft_reason'] = '네이버 임시저장 확인 실패'
                    pending.pop(page_id)

            if pending:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    break
                sleep(min(max(interval_seconds, 1), remaining))

    for row in pending.values():
        row['naver_draft_state'] = 'timed_out'
        row['naver_draft_reason'] = '네이버 임시저장 확인 시간 초과'
    return data
