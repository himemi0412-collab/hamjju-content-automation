"""Small, credential-free production evidence for Actions and the status board."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


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
        return {'naver_blog': 3} if schedule == '0 1 * * *' else {'ppojjugi_shorts': 1, 'japan_shorts': 1}
    if mode == 'produce_daily_shorts':
        return {'ppojjugi_shorts': 1, 'japan_shorts': 1}
    return {'naver_blog' if mode == 'recover_blog' else channel: 1}


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
    text = markdown_report(data, expected, os.getenv('RUN_STATUS', 'unknown'), url)
    Path('output').mkdir(exist_ok=True)
    Path('output/production-summary.md').write_text(text, encoding='utf-8')
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with Path(os.environ['GITHUB_STEP_SUMMARY']).open('a', encoding='utf-8') as summary:
            summary.write(text)


if __name__ == '__main__':
    main()
