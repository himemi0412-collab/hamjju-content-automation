"""Read-only external checks before an Actions production command runs."""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

import httpx

from .budget import BudgetGuard
from .config import load_channels
from .settings import Settings


class PrecheckFailure(RuntimeError):
    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category


def check(mode: str, channel: str, schedule: str, page_id: str, settings: Settings) -> dict:
    channels = load_channels()
    allowed_modes = {'schedule', 'plan_month', 'prepare_topic', 'dry_run', 'execute_text',
                     'recover_blog', 'resume_blog', 'recover_failed_blog_cards', 'execute_media', 'produce_daily_shorts',
                     'retry_revision', 'regenerate_review', 'rerender_blog_cards',
                     'repair_video', 'repair_av_sync'}
    if mode not in allowed_modes:
        raise PrecheckFailure('CONFIG_ERROR', 'Unknown workflow mode')
    if mode == 'schedule':
        if schedule not in {'45 0 * * *', '0 1 * * *', '0 12 * * *'}:
            raise PrecheckFailure('CONFIG_ERROR', 'Unknown scheduled trigger')
        channels_needed = (('naver_blog',) if schedule == '0 1 * * *' else
                           ('ppojjugi_shorts', 'japan_shorts') if schedule == '0 12 * * *' else ())
    else:
        if channel not in channels:
            raise PrecheckFailure('CONFIG_ERROR', 'Unknown workflow channel')
        channels_needed = (('ppojjugi_shorts', 'japan_shorts') if mode == 'produce_daily_shorts' else
                           ('naver_blog', 'japan_shorts') if mode in {'plan_month', 'prepare_topic'} else (channel,))
        page_ids = page_id.split(',') if mode == 'rerender_blog_cards' else [page_id]
        if page_id and (len(page_ids) > 25 or any(not re.fullmatch(
                r'(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})', item)
                for item in page_ids)):
            raise PrecheckFailure('CONFIG_ERROR', 'Invalid Notion page ID')
        if mode in {'recover_blog', 'resume_blog', 'recover_failed_blog_cards', 'regenerate_review', 'rerender_blog_cards', 'repair_video', 'repair_av_sync'} and not page_id:
            raise PrecheckFailure('CONFIG_ERROR', 'This workflow mode needs an exact page ID')
    if not settings.openai_api_key or not settings.notion_access_token:
        raise PrecheckFailure('CONFIG_ERROR', 'OpenAI or Notion credential is missing')
    media_needed = (mode == 'schedule' and schedule == '0 12 * * *') or mode in {
        'execute_media', 'produce_daily_shorts', 'retry_revision', 'regenerate_review', 'repair_av_sync',
    } and any(channels[name].content_kind == 'shorts' for name in channels_needed)
    if media_needed:
        if not settings.fal_key:
            raise PrecheckFailure('CONFIG_ERROR', 'FAL_KEY is missing for Shorts media')
        if mode != 'regenerate_review':
            for path in (settings.youtube_client_secrets_file,
                         settings.youtube_ppojjugi_token_file, settings.youtube_japan_token_file):
                if not path.is_file():
                    raise PrecheckFailure('CONFIG_ERROR', 'YouTube OAuth file is missing')
    for name in channels_needed:
        if not Path(channels[name].prompt_file).is_file():
            raise PrecheckFailure('CONFIG_ERROR', f'Prompt file missing for {name}')
    for path in (settings.output_dir, settings.state_db.parent):
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=path):
            pass
    budget = BudgetGuard(settings.openai_budget_ledger, settings.openai_monthly_budget_usd,
                         settings.openai_budget_baseline_month, settings.openai_budget_baseline_usd,
                         settings.openai_budget_require_existing_ledger)
    if budget.remaining <= 0:
        raise PrecheckFailure('LIMIT_ERROR', 'OpenAI internal monthly budget exhausted')
    with httpx.Client(base_url='https://api.notion.com/v1', timeout=20, headers={
        'Authorization': f'Bearer {settings.notion_access_token}',
        'Notion-Version': '2026-03-11',
    }) as client:
        response = client.get('/users/me')
        response.raise_for_status()
        for source in ({channels[name].source for name in channels_needed} or {'blog', 'shorts'}):
            data_source_id = settings.blog_data_source_id if source == 'blog' else settings.shorts_data_source_id
            if not re.fullmatch(r'[0-9a-fA-F-]{32,36}', data_source_id):
                raise PrecheckFailure('CONFIG_ERROR', f'Invalid {source} data source ID')
            client.get(f'/data_sources/{data_source_id}').raise_for_status()
        if page_id and len(page_ids) == 1:
            page = client.get(f'/pages/{page_id}')
            page.raise_for_status()
            status = (page.json().get('properties', {}).get('상태', {}).get('select') or {}).get('name')
            if not status:
                raise PrecheckFailure('DATA_ERROR', 'Target page has no status')
    return {'status': 'PASS', 'channels': channels_needed, 'mode': mode,
            'notion_auth': True, 'notion_sources': True, 'budget_remaining_usd': float(budget.remaining)}


def main() -> None:
    try:
        report = check(os.getenv('PREFLIGHT_MODE', ''), os.getenv('PREFLIGHT_CHANNEL', ''),
                       os.getenv('PREFLIGHT_SCHEDULE', ''), os.getenv('PREFLIGHT_PAGE_ID', ''), Settings())
    except PrecheckFailure as exc:
        report = {'status': 'PRECHECK_FAILED', 'error_type': exc.category, 'reason': str(exc)}
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        category = ('AUTH_ERROR' if code in {401, 403} else 'LIMIT_ERROR' if code == 429
                    else 'TRANSIENT_ERROR' if code >= 500 else 'CONFIG_ERROR')
        report = {'status': 'PRECHECK_FAILED', 'error_type': category,
                  'reason': f'External API returned HTTP {code}'}
    except httpx.TransportError:
        report = {'status': 'PRECHECK_FAILED', 'error_type': 'TRANSIENT_ERROR',
                  'reason': 'External API connection failed'}
    except (OSError, ValueError, RuntimeError) as exc:
        # Keep raw paths, response bodies and credentials out of Actions logs.
        report = {'status': 'PRECHECK_FAILED', 'error_type': 'CONFIG_ERROR',
                  'reason': type(exc).__name__}
    print(json.dumps(report, ensure_ascii=False))
    if report['status'] != 'PASS':
        sys.exit(1)


if __name__ == '__main__':
    main()
