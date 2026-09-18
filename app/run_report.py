"""Small, credential-free production evidence for Actions and the status board."""
from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .manuscript_repair import manuscript_hash


def compact_result(channel: str, result: dict) -> dict:
    media = result.get('media') or {}
    return {
        'channel': result.get('channel') or channel,
        'page_id': result.get('page_id'),
        'title': result.get('title') or '(제목을 읽지 못함)',
        'status': result.get('status') or 'unknown',
        'qa_pass': result.get('qa_pass') is True,
        'notion_page_updated': result.get('notion_page_updated') is True,
        'output_verified': result.get('output_verified') is True,
        'budget_blocked': bool(result.get('budget_blocked') or media.get('budget_blocked')),
        'card_count': len(media.get('cards') or []),
        'cards_attached': media.get('notion_cards_attached') is True,
        'video_attached': media.get('notion_video_attached') is True,
        'naver_draft_verified': result.get('naver_draft_verified') is True,
    }


def reviewed_output(result: dict) -> bool:
    return bool(
        result['qa_pass'] and result['notion_page_updated']
        and (result['channel'] != 'naver_blog' or result['output_verified'])
        and not result['budget_blocked']
        and result['status'] not in {'failed', 'skipped', '수정 필요', 'unknown'}
    )


def record_results(output_dir: Path, channel: str, results: list[dict], expected: int) -> Path:
    """Write before validation raises, preserving evidence from both Shorts commands."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / 'production-results.json'
    data = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'schema_version': 1, 'batches': []}
    items = [compact_result(channel, result) for result in results]
    data['batches'].append({
        'channel': channel, 'expected': expected, 'processed': len(items),
        'reviewed_outputs': sum(reviewed_output(x) for x in items),
        'missing': max(expected - len(items), 0), 'results': items,
    })
    data['updated_at'] = datetime.now(timezone.utc).isoformat()
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)
    return path


def is_production_run(event: str, mode: str) -> bool:
    return event == 'schedule' or (
        event == 'workflow_dispatch' and mode not in {'dry_run', 'verify_youtube_auth', ''}
    )


def expected_channels(event: str, schedule: str, mode: str, channel: str) -> dict[str, int]:
    if event == 'schedule':
        scheduled = {
            '0 1 * * *': {'naver_blog': 3},
            '0 12 * * *': {'ppojjugi_shorts': 1, 'japan_shorts': 1},
            '0 4 18 9 *': {'naver_blog': 1},
        }
        return scheduled.get(schedule, {})
    if mode == 'produce_daily_shorts':
        return {'ppojjugi_shorts': 1, 'japan_shorts': 1}
    return {'naver_blog' if mode in {'recover_blog', 'resume_blog'} else channel: 1}


def _blog_rows(data: dict) -> tuple[list[dict], int]:
    batches = data.get('batches') or []
    if len(batches) != 1 or batches[0].get('channel') != 'naver_blog':
        raise ValueError('Original batch is not a single blog batch')
    batch = batches[0]
    rows = batch.get('results') or []
    expected = batch.get('expected')
    if type(expected) is not int or expected < 1 or not rows or len(rows) > expected:
        raise ValueError('Original blog batch count is inconsistent')
    ids = []
    for row in rows:
        page_id = str(row.get('page_id') or '')
        normalized = page_id.replace('-', '').lower()
        if row.get('channel') != 'naver_blog' or not re.fullmatch(r'[0-9a-f]{32}', normalized):
            raise ValueError('Original batch contains an invalid page or another channel')
        if normalized in ids:
            raise ValueError('Duplicate pages cannot be counted more than once')
        ids.append(normalized)
        for field in ('qa_pass', 'notion_page_updated', 'output_verified', 'budget_blocked', 'cards_attached', 'naver_draft_verified'):
            if type(row.get(field)) is not bool:
                raise ValueError('Original result flags are incomplete')
    return rows, expected


def _artifact_evidence(root: Path, row: dict) -> dict:
    """Trust only the exact artifact's internally consistent, hashed output."""
    folder = root / row['page_id'].replace('-', '')[:16]
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    generated = manifest.get('generated')
    if manifest.get('page_id') != row['page_id'] or manifest.get('channel') != 'naver_blog' or not isinstance(generated, dict):
        raise ValueError('Artifact page or channel does not match the result')
    if manifest.get('manuscript_sha256') != manuscript_hash(generated):
        raise ValueError('Manuscript hash changed or is missing')
    qa = manifest.get('qa') or {}
    passed = qa.get('pass') is True and not qa.get('blocking_issues')
    if passed != row['qa_pass'] or manifest.get('final_status') != row['status'] or manifest.get('output_verified') is not row['output_verified']:
        raise ValueError('Artifact review and result flags disagree')
    # A failed pre-write item may lack read-back evidence; it never counts as done.
    if not row['output_verified']:
        if reviewed_output(row):
            raise ValueError('Unverified output cannot count as complete')
        return manifest
    observation = json.loads((folder / 'notion-readback.json').read_text(encoding='utf-8'))
    if any(observation.get(key) != row[key] for key in ('page_id', 'title', 'status')):
        raise ValueError('Stored read-back identity does not match the result')
    if type(observation.get('body_blocks_verified')) is not int or observation['body_blocks_verified'] < 1:
        raise ValueError('Complete manuscript read-back evidence is missing')
    if observation.get('naver_draft_verified') is not row['naver_draft_verified']:
        raise ValueError('Naver verification evidence does not match')
    media = manifest.get('media') or {}
    names = [Path(str(x).replace('\\', '/')).name for x in media.get('cards', [])]
    observed_cards = observation.get('cards_verified') or []
    if (len(names) != 5 or len(set(names)) != 5 or row.get('card_count') != 5
            or row['cards_attached'] is not True or media.get('notion_cards_attached') is not True
            or [x.get('name') for x in observed_cards] != names):
        raise ValueError('Five ordered card attachments were not verified')
    for card in observed_cards:
        name = card['name']
        if name != Path(name).name or name in {'.', '..'}:
            raise ValueError('Invalid card artifact path')
        if hashlib.sha256((folder / 'cards' / name).read_bytes()).hexdigest() != card.get('sha256'):
            raise ValueError('Stored card hash changed')
    return manifest


def original_batch_run_id(prior_dir: Path, source_run_id: str, page_id: str) -> str:
    """Return a missing original artifact's run ID, never an arbitrary download path."""
    path = prior_dir / 'batch-recovery-results.json'
    if not path.exists():
        return ''
    history = json.loads(path.read_text(encoding='utf-8'))
    recovery = history.get('recovery') or {}
    original_id = str(recovery.get('source_run_id') or '')
    if (recovery.get('run_id') != source_run_id or recovery.get('page_id') != page_id
            or not original_id.isdigit() or original_id == source_run_id):
        raise ValueError('Recovery history does not identify the exact original batch')
    _blog_rows(history)
    return '' if (prior_dir / 'batch-origin/production-results.json').is_file() else original_id


def _without_provenance(row: dict) -> dict:
    return {key: value for key, value in row.items() if key != 'evidence_run_id'}


def _preserve_origin(origin_dir: Path, current_dir: Path, rows: list[dict]) -> None:
    """Keep only verified production evidence for later retries, not budget/state files."""
    target = current_dir / 'batch-origin'
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(origin_dir / 'production-results.json', target / 'production-results.json')
    for row in rows:
        folder = row['page_id'].replace('-', '')[:16]
        (target / folder).mkdir(exist_ok=True)
        for name in ('manifest.json', 'notion-readback.json'):
            source = origin_dir / folder / name
            if source.exists():
                shutil.copy2(source, target / folder / name)
        cards = origin_dir / folder / 'cards'
        if cards.is_dir():
            shutil.copytree(cards, target / folder / 'cards', dirs_exist_ok=True)


def merge_resumed_blog_batch(
    current: dict, prior_dir: Path, current_dir: Path, page_id: str,
    source_run_id: str, run_id: str, original_dir: Path | None = None,
) -> dict:
    """Merge reporting evidence only; no queue selection, generation or writes to Notion."""
    if not source_run_id.isdigit() or not run_id.isdigit() or source_run_id == run_id:
        raise ValueError('Distinct original and recovery run identities are required')
    prior = json.loads((prior_dir / 'production-results.json').read_text(encoding='utf-8'))
    previous_rows, expected = _blog_rows(prior)
    origin, origin_dir, original_run_id = prior, prior_dir, source_run_id
    chain = []
    history_path = prior_dir / 'batch-recovery-results.json'
    if history_path.exists():
        original_batch_run_id(prior_dir, source_run_id, page_id)
        history = json.loads(history_path.read_text(encoding='utf-8'))
        history_rows, history_expected = _blog_rows(history)
        recovery = history['recovery']
        original_run_id = recovery['source_run_id']
        origin_dir = prior_dir / 'batch-origin'
        if not (origin_dir / 'production-results.json').is_file():
            if original_dir is None:
                raise ValueError('Original three-item evidence is unavailable')
            origin_dir = original_dir
        origin = json.loads((origin_dir / 'production-results.json').read_text(encoding='utf-8'))
        origin_rows, origin_expected = _blog_rows(origin)
        if len(previous_rows) != 1 or expected != 1 or previous_rows[0]['page_id'] != page_id:
            raise ValueError('Previous recovery must contain only the same target page')
        expected_history = {row['page_id']: row for row in origin_rows}
        if page_id not in expected_history or reviewed_output(expected_history[page_id]):
            raise ValueError('Original batch did not contain this unfinished page')
        expected_history[page_id] = previous_rows[0]
        if (history_expected != origin_expected or set(expected_history) != {row['page_id'] for row in history_rows}
                or any(_without_provenance(row) != _without_provenance(expected_history[row['page_id']]) for row in history_rows)):
            raise ValueError('Prior recovery changed retained pages or duplicated completions')
        # Every retained completion remains bound to its original local evidence.
        origin_manifests = {row['page_id']: _artifact_evidence(origin_dir, row) for row in origin_rows}
        previous_manifest = _artifact_evidence(prior_dir, previous_rows[0])
        origin_hash = origin_manifests[page_id]['manuscript_sha256']
        chain = recovery.get('manuscript_chain') or [
            {'run_id': original_run_id, 'sha256': origin_hash},
            {'run_id': source_run_id, 'source_sha256': previous_manifest.get('resume_source_manuscript_sha256'),
             'sha256': previous_manifest['manuscript_sha256']},
        ]
        if not isinstance(chain, list) or len(chain) < 2 or chain[0] != {'run_id': original_run_id, 'sha256': origin_hash}:
            raise ValueError('Original manuscript lineage changed')
        seen = {original_run_id}
        for earlier, later in zip(chain, chain[1:]):
            if (not str(later.get('run_id', '')).isdigit() or later['run_id'] in seen
                    or later.get('source_sha256') != earlier.get('sha256')):
                raise ValueError('Recovery manuscript lineage is broken')
            seen.add(later['run_id'])
        if (chain[-1].get('run_id') != source_run_id
                or chain[-1].get('sha256') != previous_manifest['manuscript_sha256']
                or chain[-1].get('source_sha256') != previous_manifest.get('resume_source_manuscript_sha256')):
            raise ValueError('Most recent recovery manuscript does not match its lineage')
        previous_rows, expected = history_rows, origin_expected
    new_rows, new_expected = _blog_rows(current)
    if len(new_rows) != 1 or new_expected != 1 or new_rows[0]['page_id'] != page_id:
        raise ValueError('Recovery must contain exactly the requested existing page')
    previous = {row['page_id']: row for row in previous_rows}
    if page_id not in previous:
        raise ValueError('Recovered page is not in the original batch')
    if reviewed_output(previous[page_id]):
        raise ValueError('Already completed page must not be counted as a new recovery')
    previous_manifest = _artifact_evidence(prior_dir, previous[page_id])
    if not history_path.exists():
        for row in previous_rows:
            _artifact_evidence(origin_dir, row)
        chain = [{'run_id': original_run_id, 'sha256': previous_manifest['manuscript_sha256']}]
    resumed = new_rows[0]
    if resumed['output_verified']:
        manifest = _artifact_evidence(current_dir, resumed)
        source_hash = previous_manifest['manuscript_sha256']
        if manifest.get('resume_source_manuscript_sha256') != source_hash:
            raise ValueError('Recovery does not prove the exact original manuscript version')
        if run_id in {entry['run_id'] for entry in chain}:
            raise ValueError('A recovery run cannot appear twice in the lineage')
        chain.append({'run_id': run_id, 'source_sha256': source_hash, 'sha256': manifest['manuscript_sha256']})
    merged = [{**(resumed if row['page_id'] == page_id else row),
               'evidence_run_id': run_id if row['page_id'] == page_id else original_run_id}
              for row in previous_rows]
    source_time = datetime.fromisoformat(origin['updated_at'])
    if source_time.tzinfo is None:
        raise ValueError('Original batch time has no timezone')
    _preserve_origin(origin_dir, current_dir, _blog_rows(origin)[0])
    return {
        'schema_version': 1, 'updated_at': current.get('updated_at'),
        'batches': [{'channel': 'naver_blog', 'expected': expected, 'processed': len(merged),
                     'reviewed_outputs': sum(reviewed_output(row) for row in merged),
                     'missing': max(expected - len(merged), 0), 'results': merged}],
        'recovery': {'source_run_id': original_run_id, 'previous_run_id': source_run_id,
                     'run_id': run_id, 'page_id': page_id, 'manuscript_chain': chain,
                     'batch_date_kst': source_time.astimezone(timezone(timedelta(hours=9))).date().isoformat(),
                     'current_reviewed_outputs': int(reviewed_output(resumed)), 'current_expected': 1},
    }


def markdown_report(data: dict, expected: dict[str, int], run_status: str, run_url: str) -> str:
    batches = data.get('batches') or []
    rows = [x for batch in batches for x in batch.get('results', [])]
    by_channel = {name: [x for x in rows if x['channel'] == name] for name in expected}
    wanted = sum(expected.values())
    reviewed = sum(reviewed_output(x) for x in rows)
    complete = run_status == 'success' and all(
        sum(reviewed_output(x) for x in by_channel[name]) >= count for name, count in expected.items()
    )
    state = '✅ 검수한 Notion 결과 준비' if complete else '⚠️ 제작 미완료 · 확인 필요'
    lines = [
        '## 햄쮸 자동화 운영 상태', '', f'- 현재 상태: **{state}**',
        f'- 검수 통과·Notion 반영: **{reviewed}/{wanted}편**',
        f'- 실제 처리 기록: **{len(rows)}편**',
        f'- 실행 상태: **{run_status}**', f'- 실행 상세: {run_url}',
        f'- 확인 시각: {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}', '',
        '| 채널 | 목표 | 처리 | 검수 통과·Notion 반영 |',
        '| --- | ---: | ---: | ---: |',
    ]
    recovery = data.get('recovery')
    if recovery:
        source_url = run_url.rsplit('/', 1)[0] + '/' + recovery['source_run_id']
        today_kst = datetime.now(timezone(timedelta(hours=9))).date().isoformat()
        batch_label = '오늘' if recovery['batch_date_kst'] == today_kst else recovery['batch_date_kst']
        lines[3:3] = [
            f"- {batch_label} 블로그 합계: **{reviewed}/{wanted}편** · 이번 재개: **{recovery['current_reviewed_outputs']}/1편**",
            f'- 원본 예약 실행: {source_url}',
            '- 원본 실행의 실패 기록은 유지하며, 동일 원고의 재검수 결과만 반영했습니다.',
        ]
        if recovery.get('previous_run_id') and recovery['previous_run_id'] != recovery['source_run_id']:
            previous_url = run_url.rsplit('/', 1)[0] + '/' + recovery['previous_run_id']
            lines.insert(6, f'- 직전 재개 실행: {previous_url}')
    for name, count in expected.items():
        items = by_channel[name]
        lines.append(f'| {name} | {count} | {len(items)} | {sum(reviewed_output(x) for x in items)} |')
    lines += ['', '| 제목 | 검수 | Notion 반영 | 이미지·영상 | 재열람 검증 |', '| --- | --- | --- | --- | --- |']
    for row in rows:
        title = str(row['title']).replace('|', '\\|').replace('\n', ' ')
        qa = '통과' if row['qa_pass'] else '미통과·미확인'
        saved = '확인' if row['notion_page_updated'] else '미확인'
        media = f"카드 {row['card_count']}장 첨부 확인" if row['cards_attached'] else ('영상 첨부 확인' if row['video_attached'] else '미확인')
        lines.append(f"| {title} | {qa} | {saved} ({row['status']}) | {media} | {'확인' if row['output_verified'] else '미확인'} |")
    if not rows:
        lines += ['', '**제작 결과가 없습니다. 실행 성공 표시만으로 원고 제작 완료를 판단하지 않습니다.**']
    naver_verified = sum(x['naver_draft_verified'] for x in rows)
    lines += [
        '', f'- 네이버 임시저장·재열람 확인: **{naver_verified}편**. Notion 준비와 네이버 저장은 별도입니다.',
        '- 자동 실행: 매일 10:00 KST 블로그 / 21:00 KST 쇼츠',
        '- 네이버 공개·예약 발행: 안 함', '- YouTube 자동 업로드: 꺼짐',
        '- OpenAI 내부 월 한도: $22',
        '- 코드 변경 검사와 읽기 전용 점검은 이 제작 상태판을 덮어쓰지 않습니다.', '',
    ]
    return '\n'.join(lines)


def main() -> None:
    event = os.getenv('EVENT_NAME', '')
    mode = os.getenv('DISPATCH_MODE', '')
    if not is_production_run(event, mode):
        return
    path = Path('output/production-results.json')
    data = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    expected = expected_channels(event, os.getenv('SCHEDULE_EXPR', ''), mode, os.getenv('DISPATCH_CHANNEL', ''))
    url = f"https://github.com/{os.getenv('GITHUB_REPOSITORY', '')}/actions/runs/{os.getenv('GITHUB_RUN_ID', '')}"
    aggregate_warning = False
    if mode == 'resume_blog':
        try:
            data = merge_resumed_blog_batch(
                data, Path('recovered'), Path('output'), os.getenv('RESUME_PAGE_ID', ''),
                os.getenv('SOURCE_RUN_ID', ''), os.getenv('GITHUB_RUN_ID', ''),
                original_dir=Path('recovered-original'),
            )
            expected = {'naver_blog': data['batches'][0]['expected']}
            Path('output/batch-recovery-results.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        except (ValueError, OSError, KeyError, TypeError, AttributeError):
            # Keep the current run truthful without guessing at prior completions.
            aggregate_warning = True
    text = markdown_report(data, expected, os.getenv('RUN_STATUS', 'unknown'), url)
    if aggregate_warning:
        text += '\n- 원본 배치의 합산 증거가 불완전하여 **오늘 전체 완료 수는 미확인**입니다. 위 수량은 이번 실행만 표시합니다.\n'
    Path('output').mkdir(exist_ok=True)
    Path('output/production-summary.md').write_text(text, encoding='utf-8')
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with Path(os.environ['GITHUB_STEP_SUMMARY']).open('a', encoding='utf-8') as summary:
            summary.write(text)


if __name__ == '__main__':
    main()
