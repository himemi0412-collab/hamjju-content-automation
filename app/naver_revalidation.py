"""Re-QA one existing Naver manuscript and hand off a fresh immutable receipt."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

import httpx
from openai import APIStatusError, APITimeoutError

from .blog_reference import load_blog_reference
from .ai import QAResponseParseError
from .content_fingerprint import file_sha256
from .manuscript_repair import manuscript_hash
from .notion_client import (
    NotionClient, extract_page_title, naver_handoff_receipt_blocks,
    property_value,
)
from .ownership import OperatingContract
from .pipeline import _visual_qa_passed


REVISION_STATUS = '수정 필요'
READY_STATUS = '네이버 저장 요청'


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_qa_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def _qa_exception(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, QAResponseParseError):
        return {
            'code': 'QA_PARSE_FAIL', 'reason': 'QA 응답을 JSON 객체로 파싱하지 못했습니다.',
            'retryable': True, 'exception_type': type(exc).__name__,
        }
    if isinstance(exc, (TimeoutError, httpx.TimeoutException, APITimeoutError)):
        return {
            'code': 'QA_TIMEOUT', 'reason': 'QA 제공자 응답 제한 시간 내에 완료되지 않았습니다.',
            'retryable': True, 'exception_type': type(exc).__name__,
        }
    status_code = getattr(exc, 'status_code', None)
    retryable = isinstance(exc, httpx.RequestError) or status_code == 429 or (
        isinstance(status_code, int) and status_code >= 500
    )
    if isinstance(exc, APIStatusError) or type(exc).__module__.startswith('openai'):
        reason = 'QA 제공자 API 호출이 실패했습니다.'
        if isinstance(status_code, int):
            reason += f' HTTP {status_code}.'
    else:
        reason = 'QA 실행 중 API 또는 런타임 호출 오류가 발생했습니다.'
    return {
        'code': 'QA_API_FAIL', 'reason': reason, 'retryable': bool(retryable),
        'exception_type': type(exc).__name__,
    }


def _failure_entry(value: Any, *, target: str, default_rule: str) -> dict[str, Any]:
    if isinstance(value, dict):
        rule_id = str(value.get('rule_id') or default_rule).strip()
        reason = str(value.get('reason') or value.get('issue') or '').strip()
        retryable = value.get('retryable') is True
        recommended_fix = str(value.get('recommended_fix') or '').strip()
        target = str(value.get('target') or target).strip()
    else:
        reason = str(value or '').strip()
        match = re.match(r'^([A-Z][A-Z0-9_]+)\s*(?:=|:)\s*(.*)$', reason)
        rule_id = match.group(1) if match else default_rule
        if match:
            reason = match.group(2).strip()
        retryable = False
        recommended_fix = '수정 후 해당 대상에 대해 독립 QA를 다시 실행합니다.'
    return {
        'rule_id': rule_id or default_rule,
        'reason': reason or 'QA에서 구체적 실패 사유를 반환하지 않았습니다.',
        'target': target,
        'retryable': retryable,
        'recommended_fix': recommended_fix or '문제가 확인된 부분만 수정한 뒤 해당 QA를 다시 실행합니다.',
    }


def _normalize_manuscript_qa(raw: Any) -> tuple[dict[str, Any], bool]:
    if not isinstance(raw, dict) or type(raw.get('pass')) is not bool:
        return ({'pass': False, 'blocking_issues': ['QA 응답의 pass 필드가 없거나 boolean이 아닙니다.']}, False)
    blockers = raw.get('blocking_issues', [])
    if not isinstance(blockers, list):
        return ({'pass': False, 'blocking_issues': ['QA 응답의 blocking_issues 형식이 잘못됐습니다.']}, False)
    details = raw.get('failure_details')
    if details is not None and not isinstance(details, list):
        return ({'pass': False, 'blocking_issues': ['QA 응답의 failure_details 형식이 잘못됐습니다.']}, False)
    normalized = dict(raw)
    failures = [_failure_entry(item, target='manuscript', default_rule='UNCLASSIFIED_CONTENT_RULE')
                for item in (details if details is not None else blockers)]
    if raw['pass'] and blockers:
        return ({**normalized, 'pass': False, 'blocking_issues': ['QA 응답에서 PASS와 blocking_issues가 모순됩니다.']}, False)
    if not raw['pass'] and not failures:
        return ({**normalized, 'pass': False, 'blocking_issues': ['FAIL 응답에 실패 규칙과 이유가 없습니다.']}, False)
    normalized['failure_details'] = failures
    return normalized, True


def _normalize_card_qa(raw: Any) -> tuple[dict[str, Any], list[dict[str, Any]], bool]:
    if not isinstance(raw, dict) or type(raw.get('pass')) is not bool:
        malformed = {'pass': False, 'blocking_issues': ['카드 QA 응답의 pass 필드가 없거나 boolean이 아닙니다.']}
        return malformed, [], False
    blockers = raw.get('blocking_issues', [])
    score = raw.get('ai_likeness_score')
    rows = raw.get('card_results')
    if not isinstance(blockers, list) or not isinstance(rows, list):
        malformed = {**raw, 'pass': False, 'blocking_issues': ['카드 QA 응답에 blocking_issues 또는 card_results가 없습니다.']}
        return malformed, [], False
    by_number: dict[int, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or type(row.get('card_number')) is not int or row['card_number'] not in range(1, 6):
            return ({**raw, 'pass': False}, [], False)
        number = row['card_number']
        if number in by_number or type(row.get('pass')) is not bool or not isinstance(row.get('failures'), list):
            return ({**raw, 'pass': False}, [], False)
        failures = [_failure_entry(item, target=f'card_{number}', default_rule='UNCLASSIFIED_CARD_RULE')
                    for item in row['failures']]
        if row['pass'] and failures:
            return ({**raw, 'pass': False}, [], False)
        if not row['pass'] and not failures:
            return ({**raw, 'pass': False}, [], False)
        by_number[number] = {'card_number': number, 'pass': row['pass'], 'failures': failures}
    if set(by_number) != set(range(1, 6)):
        return ({**raw, 'pass': False}, [], False)
    card_results = [by_number[number] for number in range(1, 6)]
    row_pass = all(row['pass'] for row in card_results)
    if raw['pass'] and (blockers or not row_pass):
        return ({**raw, 'pass': False}, card_results, False)
    score_valid = type(score) is int and 0 <= score <= 100
    if not score_valid:
        return ({**raw, 'pass': False}, card_results, False)
    if score >= 5:
        set_failure = _failure_entry({
            'rule_id': 'AI_LIKENESS_SCORE',
            'reason': f'세트 AI 티 점수 {score}가 통과 한계 5 이상입니다.',
            'target': 'set', 'retryable': False,
            'recommended_fix': '세트의 생성 흔적을 만든 구체적 요소를 줄이고 해당 카드만 수정합니다.',
        }, target='set', default_rule='AI_LIKENESS_SCORE')
        for row in card_results:
            if row['pass']:
                row['pass'] = False
                row['failures'].append({**set_failure, 'target': f"card_{row['card_number']}"})
    derived_pass = raw['pass'] and not blockers and score < 5 and all(row['pass'] for row in card_results)
    if not raw['pass'] and row_pass and score < 5:
        return ({**raw, 'pass': False}, card_results, False)
    normalized = dict(raw)
    normalized['card_results'] = card_results
    normalized['pass'] = derived_pass
    return normalized, card_results, True


def revalidate_existing_naver_page(
    page_id: str,
    *,
    notion: NotionClient,
    ai: Any,
    operating_contract: OperatingContract,
    output_dir: Path,
    qa_run_id: str | None = None,
) -> dict[str, Any]:
    """Run fresh text+image QA on the current Notion snapshot, then append a bound receipt.

    This recovery path never regenerates article text or images. It refuses to
    hand off if the page, attachments, or manuscript change during the review.
    """
    if not page_id or not isinstance(page_id, str):
        raise ValueError('A single exact Notion page ID is required')
    run_id = qa_run_id or f'local-reqa-{uuid4().hex}'
    initial_page = notion.retrieve_page(page_id)
    initial_properties = initial_page.get('properties') or {}
    initial_status = property_value(initial_properties.get('상태', {}))
    if initial_status != REVISION_STATUS:
        raise RuntimeError('Re-QA requires the exact failed Notion item in 수정 필요 state')
    initial_edit = initial_page.get('last_edited_time')

    snapshot = notion.read_latest_json_snapshot(page_id)
    generated = snapshot.get('generated')
    if not isinstance(generated, dict) or not generated.get('title') or not generated.get('body_markdown'):
        raise RuntimeError('Current failed page has no complete final manuscript snapshot')
    if extract_page_title(initial_page) != str(generated['title']).strip():
        raise RuntimeError('Current page title does not match its manuscript snapshot')
    if len(generated.get('card_news') or []) != 5 or len(generated.get('image_placements') or []) != 5:
        raise RuntimeError('Current manuscript does not contain exactly five reviewed card definitions')

    files = initial_properties.get('생성 이미지', {}).get('files', [])
    if len(files) != 5:
        raise RuntimeError('Current Notion page does not have exactly five attached cards')
    names: list[str] = []
    urls: list[str] = []
    for item in files:
        kind = item.get('type')
        data = item.get(kind, {}) if kind else {}
        url = data.get('url')
        name = str(item.get('name') or '')
        # Notion's hosted file objects do not consistently expose a stable
        # attachment ID. Bind QA to the downloaded bytes and ordered names.
        if not url or not name:
            raise RuntimeError('A Notion card attachment is missing its name or content URL')
        names.append(name)
        urls.append(str(url))

    run_dir = output_dir / 'naver-revalidation' / page_id / run_id
    cards_dir = run_dir / 'cards'
    cards_dir.mkdir(parents=True, exist_ok=False)
    report_path = run_dir / 'qa-report.json'
    report: dict[str, Any] = {
        'schema_version': 1,
        'page_id': page_id,
        'run_id': run_id,
        'started_at': _now_utc(),
        'page_last_edited_time': initial_edit,
        'overall_status': 'IN_PROGRESS',
        'manuscript': {
            'target': 'manuscript', 'status': 'PENDING', 'failure_code': None,
            'started_at': None, 'finished_at': None, 'failures': [],
        },
        'cards': [
            {
                'target': f'card_{index}', 'card_number': index,
                'status': 'PENDING', 'failure_code': None, 'failures': [],
                'qa_started_at': None, 'qa_finished_at': None,
            }
            for index in range(1, 6)
        ],
        'handoff_written': False,
        'naver_save_allowed': False,
    }
    _write_qa_report(report_path, report)
    card_paths: list[Path] = []
    for index, url in enumerate(urls, 1):
        try:
            response = httpx.get(url, timeout=60, follow_redirects=True)
            response.raise_for_status()
        except Exception:
            raise RuntimeError(f'Card {index} could not be read for fresh QA') from None
        path = cards_dir / f'card-{index:02d}.png'
        path.write_bytes(response.content)
        card_paths.append(path)
    generated_before_qa = deepcopy(generated)
    card_bytes_before_qa = [path.read_bytes() for path in card_paths]

    kst = timezone(timedelta(hours=9))
    qa_context = {
        'channel': 'naver_blog',
        'page_id': page_id,
        'current_date_kst': datetime.now(kst).date().isoformat(),
        'reference_baseline': load_blog_reference(),
        'automation_scope': {
            'mode': 'blog_cards',
            'stage': 'final_manuscript',
            'media_expected': True,
            'youtube_upload_expected': False,
        },
    }
    report['manuscript']['started_at'] = _now_utc()
    _write_qa_report(report_path, report)
    try:
        raw_manuscript_qa, manuscript_usage = ai.qa(
            generated, qa_context, qa_prompt='prompts/qa.md',
        )
        manuscript_qa, manuscript_shape_valid = _normalize_manuscript_qa(raw_manuscript_qa)
        if manuscript_shape_valid:
            manuscript_code = 'PASS' if manuscript_qa.get('pass') is True else 'QA_CONTENT_FAIL'
            manuscript_failures = manuscript_qa.get('failure_details') or []
        else:
            manuscript_code = 'QA_PARSE_FAIL'
            manuscript_failures = [_failure_entry(
                issue, target='manuscript', default_rule='QA_RESPONSE_SCHEMA',
            ) for issue in manuscript_qa.get('blocking_issues') or []]
    except Exception as exc:
        error = _qa_exception(exc)
        manuscript_usage = {}
        manuscript_qa = {'pass': False, 'blocking_issues': [error['reason']], 'failure_details': []}
        manuscript_code = error['code']
        manuscript_failures = [{
            'rule_id': error['code'], 'reason': error['reason'], 'target': 'manuscript',
            'retryable': error['retryable'], 'recommended_fix': '원인을 해결한 뒤 동일 원고로 QA를 다시 실행합니다.',
            'exception_type': error['exception_type'],
        }]
    report['manuscript'].update({
        'status': 'PASS' if manuscript_code == 'PASS' else 'FAIL',
        'failure_code': None if manuscript_code == 'PASS' else manuscript_code,
        'finished_at': _now_utc(),
        'failures': manuscript_failures,
    })
    _write_qa_report(report_path, report)
    if generated != generated_before_qa:
        raise RuntimeError('STALE_QA: manuscript changed while final manuscript QA was running')
    if [path.read_bytes() for path in card_paths] != card_bytes_before_qa:
        raise RuntimeError('STALE_QA: card images changed while final manuscript QA was running')

    image_qa_context = {
        **qa_context,
        'automation_scope': {
            **qa_context['automation_scope'],
            'stage': 'rendered_cards',
        },
    }
    report['cards_started_at'] = _now_utc()
    for item in report['cards']:
        item['qa_started_at'] = report['cards_started_at']
    _write_qa_report(report_path, report)
    try:
        raw_card_qa, card_usage = ai.qa(
            generated,
            image_qa_context,
            qa_prompt='prompts/qa_photographic_blog_cards.md',
            image_paths=card_paths,
        )
        card_qa, card_results, card_shape_valid = _normalize_card_qa(raw_card_qa)
        if card_shape_valid:
            cards_pass = card_qa.get('pass') is True
            card_code = 'PASS' if cards_pass else 'QA_CONTENT_FAIL'
            card_failures = {item['card_number']: item['failures'] for item in card_results}
            card_passes = {item['card_number']: item['pass'] for item in card_results}
        else:
            cards_pass = False
            card_code = 'QA_PARSE_FAIL'
            card_failures = {
                index: [{
                    'rule_id': card_code,
                    'reason': '카드 QA 응답에 유효한 카드별 1~5 판정이 없습니다.',
                    'target': f'card_{index}', 'retryable': True,
                    'recommended_fix': '동일한 카드 5장에 대해 카드별 결과를 포함한 QA를 다시 실행합니다.',
                }]
                for index in range(1, 6)
            }
            card_passes = {index: False for index in range(1, 6)}
    except Exception as exc:
        error = _qa_exception(exc)
        card_usage = {}
        card_qa = {'pass': False, 'blocking_issues': [error['reason']], 'card_results': []}
        cards_pass = False
        card_code = error['code']
        card_failures = {
            index: [{
                'rule_id': error['code'], 'reason': error['reason'], 'target': f'card_{index}',
                'retryable': error['retryable'],
                'recommended_fix': '원인을 해결한 뒤 카드 이미지 QA를 다시 실행합니다.',
                'exception_type': error['exception_type'],
            }]
            for index in range(1, 6)
        }
        card_passes = {index: False for index in range(1, 6)}
    report['cards_finished_at'] = _now_utc()
    # Keep the provider's set-level signals alongside normalized per-card
    # findings. This preserves the reason behind aggregate gates such as the
    # strict AI-likeness score instead of reducing them to a generic card FAIL.
    report['card_qa_detail'] = {
        key: card_qa.get(key)
        for key in (
            'pass', 'ai_likeness_score', 'blocking_issues', 'failure_details',
            'human_edit_signals_found', 'cards_to_regenerate',
        )
        if key in card_qa
    }
    for item in report['cards']:
        failures = card_failures[item['card_number']]
        card_pass = card_passes[item['card_number']] and not failures
        item.update({
            'status': 'PASS' if card_pass else 'FAIL',
            'failure_code': None if card_pass else card_code,
            'failures': failures,
            'qa_finished_at': report['cards_finished_at'],
        })
    _write_qa_report(report_path, report)
    if generated != generated_before_qa:
        raise RuntimeError('STALE_QA: manuscript changed while card image QA was running')
    if [path.read_bytes() for path in card_paths] != card_bytes_before_qa:
        raise RuntimeError('STALE_QA: card images changed while card image QA was running')

    manuscript_pass = (
        manuscript_qa.get('pass') is True
        and not (manuscript_qa.get('blocking_issues') or [])
    )
    cards_pass = cards_pass and _visual_qa_passed(card_qa)
    blocking_issues = [
        {'stage': 'final_manuscript', 'issue': issue}
        for issue in manuscript_qa.get('blocking_issues') or []
    ] + [
        {'stage': 'cards', 'issue': issue}
        for issue in card_qa.get('blocking_issues') or []
    ]
    if not manuscript_pass:
        blocking_issues.append({'stage': 'final_manuscript', 'issue': 'Manuscript QA did not PASS'})
    if not cards_pass:
        blocking_issues.append({'stage': 'cards', 'issue': 'Five-card visual QA did not PASS'})
    qa = {
        'pass': manuscript_pass and cards_pass,
        'recommended_status': 'PASS' if manuscript_pass and cards_pass else 'REVISION',
        'manuscript_qa': manuscript_qa,
        'card_qa': card_qa,
        'ai_likeness_score': card_qa.get('ai_likeness_score'),
        'blocking_issues': blocking_issues,
    }
    usage = {'final_manuscript_qa': manuscript_usage, 'five_card_qa': card_usage}
    report['overall_status'] = 'PASS' if qa['pass'] else 'FAIL'
    report['finished_at'] = _now_utc()
    report['qa_usage'] = usage
    report['manuscript_hash_after_qa'] = None
    report['card_hashes_after_qa'] = None
    report['blocking_issues'] = blocking_issues
    _write_qa_report(report_path, report)
    if not qa['pass']:
        return {
            'page_id': page_id,
            'run_id': run_id,
            'qa_pass': False,
            'manuscript_qa_pass': manuscript_pass,
            'card_qa_pass': cards_pass,
            'blocking_issues': blocking_issues,
            'manuscript_failures': manuscript_failures,
            'card_results': report['cards'],
            'report_path': str(report_path),
            'handoff_written': False,
            'naver_save_allowed': False,
            'usage': usage,
        }

    # This content identity is calculated immediately after QA on the exact
    # same immutable object. It is copied into the QA receipt and handoff.
    source_hash = manuscript_hash(generated)
    card_hashes = [file_sha256(path) for path in card_paths]
    qa.update({
        'source_page_id': page_id,
        'source_run_id': run_id,
        'source_content_sha256': source_hash,
        'source_card_sha256s': card_hashes,
    })
    if qa.get('source_card_sha256s') != card_hashes:
        raise RuntimeError('STALE_QA: fresh QA receipt does not bind all five card images')
    if manuscript_hash(generated) != source_hash:
        raise RuntimeError('STALE_QA: final manuscript changed after QA')
    report['manuscript_hash_after_qa'] = source_hash
    report['card_hashes_after_qa'] = card_hashes
    for item in report['cards']:
        item['image_sha256'] = card_hashes[item['card_number'] - 1]
    _write_qa_report(report_path, report)

    ownership = operating_contract.receipt(
        'naver_blog', document_id=page_id, source_version=source_hash, stage='HANDOFF_READY',
    )
    handoff_blocks = naver_handoff_receipt_blocks(
        generated,
        qa,
        document_id=page_id,
        source_version=source_hash,
        run_id=run_id,
        qa_run_id=run_id,
        card_sha256s=card_hashes,
        ownership_receipt=ownership,
    )

    current_page = notion.retrieve_page(page_id)
    current_snapshot = notion.read_latest_json_snapshot(page_id)
    current_files = (current_page.get('properties') or {}).get('생성 이미지', {}).get('files', [])
    current_names = [str(item.get('name') or '') for item in current_files]
    if (
        current_page.get('last_edited_time') != initial_edit
        or property_value((current_page.get('properties') or {}).get('상태', {})) != REVISION_STATUS
        or (current_snapshot.get('generated') or {}) != generated_before_qa
        or current_names != names
        or [file_sha256(path) for path in card_paths] != card_hashes
    ):
        raise RuntimeError('STALE_QA: Notion page or card attachments changed during review')

    notion.append_blocks(page_id, handoff_blocks)
    saved_snapshot = notion.read_latest_json_snapshot(page_id)
    saved_handoff = saved_snapshot.get('handoff') or {}
    if (
        manuscript_hash(saved_snapshot.get('generated') or {}) != source_hash
        or saved_snapshot.get('qa', {}).get('source_content_sha256') != source_hash
        or saved_handoff.get('page_id') != page_id
        or saved_handoff.get('run_id') != run_id
        or saved_handoff.get('content_version') != source_hash
        or saved_handoff.get('card_sha256s') != card_hashes
    ):
        raise RuntimeError('HASH_MISMATCH: Notion did not preserve the complete QA handoff')

    notion.update_status(page_id, READY_STATUS)
    readback = notion.retrieve_page(page_id)
    if property_value((readback.get('properties') or {}).get('상태', {})) != READY_STATUS:
        try:
            notion.update_status(page_id, REVISION_STATUS)
        finally:
            raise RuntimeError('Notion did not confirm the new QA handoff queue status')
    report['handoff_written'] = True
    report['naver_save_allowed'] = True
    report['notion_status'] = READY_STATUS
    report['finished_at'] = _now_utc()
    _write_qa_report(report_path, report)
    return {
        'page_id': page_id,
        'run_id': run_id,
        'qa_pass': True,
        'manuscript_qa_pass': True,
        'card_qa_pass': True,
        'content_sha256': source_hash,
        'card_sha256s': card_hashes,
        'report_path': str(report_path),
        'handoff_written': True,
        'notion_status': READY_STATUS,
        'usage': usage,
    }
