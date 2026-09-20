from __future__ import annotations
import json
import logging
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from pathlib import Path
import typer
from rich import print

from .ai import AIClient
from .budget import BudgetGuard
from .config import load_channels
from .notion_client import NotionClient, extract_page_title
from .pipeline import Pipeline
from .settings import Settings
from .state import StateStore
from .youtube import YouTubePrivateUploader
from .naver import explain as naver_explain
from .media import compose_short_video
from .card_exploration import DEFAULT_SUBTITLE, DEFAULT_TITLE, render_design_exploration
from .notion_client import property_value
from .run_report import record_results

app = typer.Typer(help='햄쮸 블로그·쇼츠 자동화')
KST = timezone(timedelta(hours=9))


def build() -> tuple[Settings, NotionClient, AIClient, StateStore, Pipeline]:
    s = Settings()
    logging.basicConfig(level=getattr(logging, s.log_level.upper(), logging.INFO))
    if not s.notion_ready:
        raise typer.BadParameter('NOTION_ACCESS_TOKEN is required in .env')
    if not s.openai_ready:
        raise typer.BadParameter('OPENAI_API_KEY is required in .env')
    notion = NotionClient(s.notion_access_token)
    budget = BudgetGuard(
        s.openai_budget_ledger,
        s.openai_monthly_budget_usd,
        s.openai_budget_baseline_month,
        s.openai_budget_baseline_usd,
        s.openai_budget_require_existing_ledger,
    )
    ai = AIClient(
        s.openai_api_key,
        s.text_model,
        s.qa_model,
        s.enable_web_research,
        budget,
        s.openai_text_reserve_usd,
        s.openai_web_text_reserve_usd,
        s.max_generation_output_tokens,
        s.max_qa_output_tokens,
    )
    state = StateStore(s.state_db)
    return s, notion, ai, state, Pipeline(s, notion, ai, state, budget)


@app.command('seed-topics')
def seed_topics(blog_count: int = 3, ppojjugi_count: int = 1, japan_count: int = 1):
    """Research fresh topics, save them to Notion, and mark verified items ready."""
    s, notion, ai, _, _ = build()
    try:
        # Fill the existing ready queue rather than adding another three topics
        # every morning while older unprocessed topics are still waiting.
        requested_blog_count = max(blog_count, 0)
        blog_count = missing_blog_topics(notion, s.blog_data_source_id, requested_blog_count)
        if not any((blog_count, max(ppojjugi_count, 0), max(japan_count, 0))):
            print_json({'blog': [], 'ppojjugi': [], 'japan': [], 'reason': 'ready_backlog_sufficient'})
            return
        blog_pages = notion.query_recent(s.blog_data_source_id, 100)
        short_pages = notion.query_recent(s.shorts_data_source_id, 100)
        existing_blog = [extract_page_title(x) for x in blog_pages]
        existing_shorts = [extract_page_title(x) for x in short_pages]
        shorts_by_channel = {
            '햄찌 창작 쇼츠': [],
            '일본 유튜브 쇼츠': [],
        }
        for page in short_pages:
            channel_prop = (page.get('properties') or {}).get('채널', {})
            channel_name = property_value(channel_prop)
            if channel_name in shorts_by_channel:
                shorts_by_channel[channel_name].append(extract_page_title(page))
        planned, usage = ai.research_topics({
            'today_utc': datetime.now(timezone.utc).date().isoformat(),
            'requested_counts': {
                'blog': max(blog_count, 0),
                'ppojjugi': max(ppojjugi_count, 0),
                'japan': max(japan_count, 0),
            },
            'existing_blog_titles': existing_blog,
            'existing_shorts_titles': existing_shorts,
            # Keep personal-story evidence and Japanese-retro history strictly
            # separated. A combined title list previously let the topic model
            # treat a Japanese Shorts title as evidence for Ppojjugi.
            'existing_ppojjugi_titles': shorts_by_channel['햄찌 창작 쇼츠'],
            'existing_japan_titles': shorts_by_channel['일본 유튜브 쇼츠'],
        })
        known = {normalize_title(x) for x in existing_blog + existing_shorts}
        created = {'blog': [], 'ppojjugi': [], 'japan': [], 'usage': usage}
        base_order = int(datetime.now(timezone.utc).strftime('%Y%m%d')) * 100
        for i, topic in enumerate((planned.get('blog') or [])[:max(blog_count, 0)], 1):
            if normalize_title(topic.get('title')) in known:
                continue
            created['blog'].append(notion.create_blog_topic(s.blog_data_source_id, topic, base_order + i))
            known.add(normalize_title(topic.get('title')))
        for key, channel, count in (
            ('ppojjugi', '햄찌 창작 쇼츠', ppojjugi_count),
            ('japan', '일본 유튜브 쇼츠', japan_count),
        ):
            for topic in (planned.get(key) or [])[:max(count, 0)]:
                if normalize_title(topic.get('title')) in known:
                    continue
                created[key].append(notion.create_short_topic(s.shorts_data_source_id, topic, channel))
                known.add(normalize_title(topic.get('title')))
    finally:
        notion.close()
    print_json(created)


@app.command('plan-month')
def plan_month(
    blog_count: int = 90,
    ppojjugi_count: int = 30,
    japan_count: int = 30,
    batch_size: int = 10,
):
    """Build a resumable 30-day topic inventory without producing media."""
    if batch_size < 1 or batch_size > 10:
        raise typer.BadParameter('batch-size must be between 1 and 10')
    requested = {
        'blog': max(blog_count, 0),
        'ppojjugi': max(ppojjugi_count, 0),
        'japan': max(japan_count, 0),
    }
    s, notion, ai, _, _ = build()
    month = datetime.now(KST).strftime('%Y%m')
    checkpoint = s.output_dir / f'topic-plan-{month}.json'
    try:
        blog_pages = notion.query_recent(s.blog_data_source_id, 100)
        short_pages = notion.query_recent(s.shorts_data_source_id, 100)
        existing_blog = [extract_page_title(x) for x in blog_pages]
        existing_shorts = [extract_page_title(x) for x in short_pages]
        existing_ids = {
            str(property_value((page.get('properties') or {}).get('콘텐츠 ID', {})) or '')
            for page in short_pages
        }
        existing_month = {
            'blog': sum(
                1 for page in blog_pages
                if _is_monthly_blog_order(
                    property_value((page.get('properties') or {}).get('원본 순서', {})), month,
                )
            ),
            'ppojjugi': sum(x.startswith(f'PPIJUK-{month}') for x in existing_ids),
            'japan': sum(x.startswith(f'JAPAN-{month}') for x in existing_ids),
        }
        remaining = {
            key: max(requested[key] - existing_month[key], 0)
            for key in requested
        }
        created = {'blog': [], 'ppojjugi': [], 'japan': []}
        known = {normalize_title(x) for x in existing_blog + existing_shorts}
        sequence = dict(existing_month)
        usage_batches: list[dict] = []
        while any(remaining.values()):
            counts = {
                key: min(remaining[key], batch_size)
                for key in remaining
            }
            planned, usage = ai.research_topics({
                'today_kst': datetime.now(KST).date().isoformat(),
                'planning_month': month,
                'planning_mode': 'monthly_inventory',
                'requested_counts': counts,
                'existing_blog_titles': existing_blog,
                'existing_shorts_titles': existing_shorts,
                'existing_ppojjugi_titles': [
                    extract_page_title(page) for page in short_pages
                    if property_value((page.get('properties') or {}).get('채널', {})) == '햄찌 창작 쇼츠'
                ],
                'existing_japan_titles': [
                    extract_page_title(page) for page in short_pages
                    if property_value((page.get('properties') or {}).get('채널', {})) == '일본 유튜브 쇼츠'
                ],
            })
            usage_batches.append(usage)
            made_this_batch = 0
            for topic in (planned.get('blog') or [])[:counts['blog']]:
                title_key = normalize_title(topic.get('title'))
                if not title_key or title_key in known:
                    continue
                sequence['blog'] += 1
                order = int(f'{month}0000') + sequence['blog']
                created['blog'].append(notion.create_blog_topic(s.blog_data_source_id, topic, order))
                existing_blog.append(str(topic.get('title') or ''))
                known.add(title_key)
                remaining['blog'] -= 1
                made_this_batch += 1
            for key, channel, prefix in (
                ('ppojjugi', '햄찌 창작 쇼츠', 'PPIJUK'),
                ('japan', '일본 유튜브 쇼츠', 'JAPAN'),
            ):
                for topic in (planned.get(key) or [])[:counts[key]]:
                    title_key = normalize_title(topic.get('title'))
                    if not title_key or title_key in known:
                        continue
                    sequence[key] += 1
                    topic = dict(topic)
                    topic['content_id'] = f'{prefix}-{month}-{sequence[key]:03d}'
                    created[key].append(notion.create_short_topic(s.shorts_data_source_id, topic, channel))
                    existing_shorts.append(str(topic.get('title') or ''))
                    known.add(title_key)
                    remaining[key] -= 1
                    made_this_batch += 1
            payload = {
                'schema_version': 1,
                'month': month,
                'requested': requested,
                'existing_before': existing_month,
                'created': created,
                'remaining': remaining,
                'usage_batches': usage_batches,
                'updated_at': datetime.now(timezone.utc).isoformat(),
            }
            checkpoint.parent.mkdir(parents=True, exist_ok=True)
            temp = checkpoint.with_suffix('.tmp')
            temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
            temp.replace(checkpoint)
            if made_this_batch == 0:
                raise RuntimeError(
                    'Monthly topic planning made no progress; the model returned too few unique topics. '
                    f'Remaining: {remaining}'
                )
    finally:
        notion.close()
    print_json(json.loads(checkpoint.read_text(encoding='utf-8')))


def _is_monthly_blog_order(value, month: str) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    number = int(value)
    base = int(f'{month}0000')
    return base < number <= base + 9999


def normalize_title(value) -> str:
    return ''.join(str(value or '').lower().split())


def missing_blog_topics(notion: NotionClient, data_source_id: str, target: int) -> int:
    if target <= 0:
        return 0
    cfg = load_channels()['naver_blog']
    ready = notion.query_ready(
        data_source_id, cfg.ready_status, cfg.notion_channel_value,
        page_size=target,
        excluded_formula_property=cfg.excluded_formula_property,
        excluded_formula_value=cfg.excluded_formula_value,
        required_select_values=cfg.required_select_values,
        required_number_greater_than=cfg.required_number_greater_than,
        sort_property=cfg.sort_property,
    )
    return max(target - len(ready), 0)


@app.command()
def run(limit: int | None = None, dry_run: bool = False, require_item: bool = False):
    """Process all three channels using the existing Notion queues."""
    s, notion, _, _, pipeline = build()
    channels = load_channels()
    try:
        all_results = run_all_channels(
            pipeline,
            channels,
            total_limit=limit if limit is not None else s.max_jobs_per_run,
            dry_run=dry_run,
        )
    finally:
        notion.close()
    print_json(all_results)
    expected = limit if limit is not None else s.max_jobs_per_run
    if not dry_run:
        record_results(s.output_dir, 'all_channels', all_results, expected)
    validate_results(all_results, require_item=require_item, dry_run=dry_run, expected_count=expected)


def run_all_channels(pipeline: Pipeline, channels: dict, total_limit: int, dry_run: bool = False):
    """Run at most ``total_limit`` jobs across all channels combined."""
    remaining = max(total_limit, 0)
    all_results = []
    for name in ('naver_blog', 'ppojjugi_shorts', 'japan_shorts'):
        if remaining == 0:
            break
        try:
            batch = pipeline.run_channel(channels[name], limit=remaining, dry_run=dry_run)
        except Exception as exc:
            batch = [{'channel': name, 'status': 'failed', 'error': repr(exc)}]
        all_results += batch
        remaining -= len(batch)
    return all_results


@app.command()
def channel(
    name: str,
    limit: int | None = None,
    dry_run: bool = False,
    require_item: bool = False,
    retry_revision: bool = False,
    auto_retry: bool = False,
):
    """Process a single configured channel."""
    s, notion, _, _, pipeline = build()
    channels = load_channels()
    if name not in channels:
        raise typer.BadParameter(f'Unknown channel: {name}')
    cfg = channels[name]
    if retry_revision:
        cfg = replace(cfg, ready_status=cfg.revision_status)
    try:
        result = pipeline.run_channel(
            cfg,
            limit=limit if limit is not None else s.max_jobs_per_run,
            dry_run=dry_run,
        )
        if auto_retry and not dry_run and not retry_revision:
            result = retry_qa_rejections_once(pipeline, cfg, result)
    except Exception as exc:
        result = [{'channel': name, 'status': 'failed', 'error': repr(exc)}]
    finally:
        notion.close()
    print_json(result)
    expected = limit if limit is not None else s.max_jobs_per_run
    if not dry_run:
        record_results(s.output_dir, name, result, expected)
    validate_results(result, require_item=require_item, dry_run=dry_run, expected_count=expected)


def retry_qa_rejections_once(
    pipeline: Pipeline,
    cfg,
    results: list[dict],
) -> list[dict]:
    """Retry only QA-rejected items once, without hiding infrastructure errors."""
    retry_cfg = replace(cfg, ready_status=cfg.revision_status)
    final_results: list[dict] = []
    for result in results:
        page_id = result.get('page_id')
        should_retry = (
            bool(page_id)
            and result.get('status') == cfg.revision_status
            and result.get('qa_pass') is False
            and not result.get('budget_blocked')
        )
        if not should_retry:
            final_results.append(result)
            continue
        page = pipeline.notion.retrieve_page(str(page_id))
        retried = pipeline.process_page(retry_cfg, page)
        retried['automatic_retry'] = {
            'attempted': True,
            'previous_blocking_issues': list(result.get('blocking_issues') or []),
        }
        final_results.append(retried)
    return final_results


@app.command('page')
def process_exact_page(name: str, page_id: str, retry_revision: bool = False):
    """Process one exact Notion page without selecting another queue item."""
    s, notion, _, _, pipeline = build()
    channels = load_channels()
    if name not in channels:
        raise typer.BadParameter(f'Unknown channel: {name}')
    cfg = channels[name]
    if retry_revision:
        cfg = replace(cfg, ready_status=cfg.revision_status)
    try:
        page = notion.retrieve_page(page_id)
        result = [pipeline.process_page(cfg, page)]
    except Exception as exc:
        result = [{'page_id': page_id, 'channel': name, 'status': 'failed', 'error': repr(exc)}]
    finally:
        notion.close()
    print_json(result)
    record_results(s.output_dir, name, result, 1)
    validate_results(result, require_item=True, dry_run=False)


@app.command('resume-blog')
def resume_blog(
    page_id: str,
    manifest: Path,
    reviewed_manifest: Path | None = None,
):
    """Resume one existing blog artifact without researching or rewriting it."""
    source = json.loads(manifest.read_text(encoding='utf-8'))
    if source.get('page_id') != page_id or source.get('channel') != 'naver_blog':
        raise typer.BadParameter('Resume manifest does not match the exact blog page and channel')
    if not isinstance(source.get('generated'), dict) or not source['generated'].get('body_markdown'):
        raise typer.BadParameter('Resume manifest has no generated blog body')
    if reviewed_manifest is not None:
        reviewed = json.loads(reviewed_manifest.read_text(encoding='utf-8'))
        if reviewed.get('page_id') != page_id or reviewed.get('channel') != 'naver_blog':
            raise typer.BadParameter('Reviewed manifest does not match the exact blog page and channel')
    s, notion, _, _, pipeline = build()
    cfg = load_channels()['naver_blog']
    try:
        page = notion.retrieve_page(page_id)
        current_status = property_value((page.get('properties') or {}).get('상태', {}))
        if current_status not in {cfg.revision_status, 'CODEX_HANDOFF_READY'}:
            raise RuntimeError('Resume requires 수정 필요 or the legacy CODEX_HANDOFF_READY status')
        cfg = replace(cfg, ready_status=current_status)
        kwargs = {'resume_manifest': manifest}
        if reviewed_manifest is not None:
            kwargs['reviewed_manifest'] = reviewed_manifest
        result = [pipeline.process_page(cfg, page, **kwargs)]
    except Exception as exc:
        result = [{'page_id': page_id, 'channel': 'naver_blog', 'status': 'failed', 'error': repr(exc)}]
    finally:
        notion.close()
    print_json(result)
    record_results(s.output_dir, 'naver_blog', result, 1)
    validate_results(result, require_item=True, dry_run=False)


def validate_results(
    results: list[dict], require_item: bool = False, dry_run: bool = False,
    expected_count: int | None = None,
) -> None:
    if require_item and len(results) != 1:
        raise RuntimeError(f'Expected exactly one Notion item, found {len(results)}')
    if not dry_run and not results:
        raise RuntimeError('Production did not complete: no Notion items were processed')
    failures = [
        x for x in results
        if x.get('status') in {'failed', 'skipped', '수정 필요'}
        or x.get('budget_blocked') or x.get('qa_pass') is False
    ]
    if failures:
        raise RuntimeError(f'Notion processing did not complete: {failures}')
    if not dry_run and any(not x.get('notion_page_updated') for x in results):
        raise RuntimeError('Notion page update was not confirmed')
    if not dry_run and any(x.get('qa_pass') is not True for x in results):
        raise RuntimeError('Independent QA pass was not confirmed')
    if not dry_run and any(x.get('channel') == 'naver_blog' and x.get('output_verified') is not True for x in results):
        raise RuntimeError('Blog output read-back verification was not confirmed')
    if not dry_run and expected_count is not None and len(results) < expected_count:
        raise RuntimeError(f'Production incomplete: expected {expected_count} items, processed {len(results)}')


@app.command('setup-youtube-auth')
def setup_youtube_auth(channel: str):
    """One-time OAuth setup for one channel. This command never uploads a video."""
    s = Settings()
    token_files = {
        'ppojjugi_shorts': s.youtube_ppojjugi_token_file,
        'japan_shorts': s.youtube_japan_token_file,
    }
    expected_channel_ids = {
        'ppojjugi_shorts': s.youtube_ppojjugi_channel_id,
        'japan_shorts': s.youtube_japan_channel_id,
    }
    if channel not in token_files:
        raise typer.BadParameter('channel must be ppojjugi_shorts or japan_shorts')
    uploader = YouTubePrivateUploader(s.youtube_client_secrets_file, token_files[channel])
    uploader.authorize_interactively()
    info = uploader.current_channel()
    if info.get('id') != expected_channel_ids[channel]:
        raise RuntimeError(f'Authorized YouTube channel does not match {channel}')
    print_json({
        'channel_key': channel,
        'youtube_channel_id': info.get('id'),
        'youtube_channel_title': info.get('title'),
        'token_file': str(token_files[channel]),
        'upload_performed': False,
    })


@app.command('verify-youtube-auth')
def verify_youtube_auth():
    """Verify both OAuth tokens against their fixed channel IDs without uploading."""
    s = Settings()
    channels = {
        'ppojjugi_shorts': (
            s.youtube_ppojjugi_token_file,
            s.youtube_ppojjugi_channel_id,
        ),
        'japan_shorts': (
            s.youtube_japan_token_file,
            s.youtube_japan_channel_id,
        ),
    }
    results = []
    for channel_key, (token_file, expected_channel_id) in channels.items():
        uploader = YouTubePrivateUploader(s.youtube_client_secrets_file, token_file)
        info = uploader.current_channel()
        channel_match = info.get('id') == expected_channel_id
        results.append({
            'channel_key': channel_key,
            'youtube_channel_id': info.get('id'),
            'youtube_channel_title': info.get('title'),
            'channel_match': channel_match,
            'upload_performed': False,
        })
        if not channel_match:
            raise RuntimeError(f'Authorized YouTube channel does not match {channel_key}')
    print_json(results)


@app.command('repair-short-video')
def repair_short_video(channel: str, page_id: str, source_dir: Path):
    """Recompose a generated Short to its full scene duration and replace its Notion review file."""
    s = Settings()
    if not s.notion_ready:
        raise typer.BadParameter('NOTION_ACCESS_TOKEN is required in .env')
    channels = load_channels()
    if channel not in {'ppojjugi_shorts', 'japan_shorts'}:
        raise typer.BadParameter('channel must be ppojjugi_shorts or japan_shorts')
    cfg = channels[channel]
    manifest_path = source_dir / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('page_id') != page_id or manifest.get('channel') != channel:
        raise RuntimeError('Artifact manifest does not match the requested page and channel')
    scenes = list((manifest.get('generated') or {}).get('scenes') or [])
    images = sorted((source_dir / 'scenes').glob('scene_*.png'))
    audio = source_dir / 'narration.mp3'
    srt = source_dir / 'captions.srt'
    if not scenes or len(images) != len(scenes) or not audio.exists() or not srt.exists():
        raise RuntimeError('Artifact is missing scenes, images, narration, or captions')

    notion = NotionClient(s.notion_access_token)
    try:
        page = notion.retrieve_page(page_id)
        channel_value = property_value((page.get('properties') or {}).get('채널', {}))
        if channel_value != cfg.notion_channel_value:
            raise RuntimeError('Notion page channel does not match the requested channel')
        output_dir = s.output_dir / page_id.replace('-', '')[:16]
        output_dir.mkdir(parents=True, exist_ok=True)
        video = compose_short_video(
            images, scenes, audio, srt, output_dir / 'short.mp4',
            channel_style=channel,
            hook=str(manifest.get('generated', {}).get('hook') or ''),
            font_path=s.card_font_path,
        )
        notion.attach_files(page_id, '최종 영상', [video])
        notion.update_status(page_id, cfg.success_status)
    finally:
        notion.close()
    print_json({
        'page_id': page_id,
        'channel': channel,
        'status': cfg.success_status,
        'duration_seconds': sum(max(float(x.get('seconds') or 5), 1.0) for x in scenes),
        'notion_video_attached': True,
        'youtube_upload_performed': False,
    })


@app.command('repair-short-av')
def repair_short_av(channel: str, page_id: str, source_dir: Path):
    """Preserve approved images while rebuilding voice, timed subtitles, and MP4."""
    s, notion, _, _, pipeline = build()
    channels = load_channels()
    if channel not in {'ppojjugi_shorts', 'japan_shorts'}:
        raise typer.BadParameter('channel must be ppojjugi_shorts or japan_shorts')
    cfg = channels[channel]
    manifest_path = source_dir / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('page_id') != page_id or manifest.get('channel') != channel:
        raise RuntimeError('Artifact manifest does not match the requested page and channel')
    generated = dict(manifest.get('generated') or {})
    scenes = list(generated.get('scenes') or [])
    images = sorted((source_dir / 'scenes').glob('scene_*.png'))
    if not scenes or len(images) != len(scenes):
        raise RuntimeError('Artifact is missing the preserved scene images')
    try:
        page = notion.retrieve_page(page_id)
        channel_value = property_value((page.get('properties') or {}).get('채널', {}))
        if channel_value != cfg.notion_channel_value:
            raise RuntimeError('Notion page channel does not match the requested channel')
        output_dir = s.output_dir / page_id.replace('-', '')[:16]
        output_dir.mkdir(parents=True, exist_ok=True)
        media = pipeline._make_short_media(
            generated, output_dir, channel, existing_images=images,
        )
        video = Path(media['video'])
        notion_video_attached = True
        try:
            notion.attach_files(page_id, '최종 영상', [video])
        except Exception:
            # Notion's small-file endpoint rejects some otherwise valid long
            # MP4 files. The verified video and private YouTube delivery must
            # not be discarded because the optional review attachment failed.
            notion_video_attached = False
        youtube_url = pipeline._upload_private(channel, generated, video, privacy_status='private')
        notion.update_status(page_id, cfg.success_status)
    finally:
        notion.close()
    print_json({
        'page_id': page_id,
        'channel': channel,
        'status': cfg.success_status,
        'images_preserved': len(images),
        'notion_video_attached': notion_video_attached,
        'media': media,
        'youtube_url': youtube_url,
        'youtube_privacy': 'private',
    })


@app.command('naver-status')
def naver_status():
    print(naver_explain())


@app.command('explore-card-design')
def explore_card_design(
    title: str = DEFAULT_TITLE,
    subtitle: str = DEFAULT_SUBTITLE,
    output: Path = Path('output/card-design-exploration'),
):
    """Render 20 independent covers and stop for a human style selection."""
    result = render_design_exploration(title, output, subtitle)
    print_json({
        'mode': 'concept_exploration',
        'status': 'READY_FOR_USER_REVIEW',
        'title': title,
        'subtitle': subtitle,
        'cover_count': len(result['covers']),
        'comparison_sheet_count': len(result['comparison_sheets']),
        'overview': result['overview'],
        'manifest': result['manifest'],
        'production_started': False,
        'selection_required': True,
    })


@app.command('doctor')
def doctor():
    """Check configuration without sending content anywhere."""
    s = Settings()
    budget = BudgetGuard(
        s.openai_budget_ledger,
        s.openai_monthly_budget_usd,
        s.openai_budget_baseline_month,
        s.openai_budget_baseline_usd,
        s.openai_budget_require_existing_ledger,
    )
    checks = {
        'openai_key_present': bool(s.openai_api_key),
        'fal_key_present': bool(s.fal_key),
        'tts_model': s.tts_model,
        'tts_ppojjugi_voice': s.tts_ppojjugi_voice,
        'tts_japan_voice': s.tts_japan_voice,
        'notion_token_present': bool(s.notion_access_token),
        'blog_data_source_id': s.blog_data_source_id,
        'shorts_data_source_id': s.shorts_data_source_id,
        'media_generation': s.enable_media_generation,
        'auto_private_youtube_upload': s.auto_private_youtube_upload,
        'image_quality': s.image_quality,
        'openai_monthly_internal_budget_usd': s.openai_monthly_budget_usd,
        'openai_budget': budget.snapshot(),
        'youtube_client_secret_exists': s.youtube_client_secrets_file.exists(),
        'youtube_ppojjugi_token_exists': s.youtube_ppojjugi_token_file.exists(),
        'youtube_japan_token_exists': s.youtube_japan_token_file.exists(),
    }
    print_json(checks)


def print_json(value):
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


if __name__ == '__main__':
    app()
