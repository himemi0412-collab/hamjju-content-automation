from __future__ import annotations
import json
import logging
from dataclasses import replace
from datetime import datetime, timezone
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
from .notion_client import property_value

app = typer.Typer(help='햄쮸 블로그·쇼츠 자동화')


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
        blog_pages = notion.query_recent(s.blog_data_source_id, 100)
        short_pages = notion.query_recent(s.shorts_data_source_id, 100)
        existing_blog = [extract_page_title(x) for x in blog_pages]
        existing_shorts = [extract_page_title(x) for x in short_pages]
        planned, usage = ai.research_topics({
            'today_utc': datetime.now(timezone.utc).date().isoformat(),
            'requested_counts': {
                'blog': max(blog_count, 0),
                'ppojjugi': max(ppojjugi_count, 0),
                'japan': max(japan_count, 0),
            },
            'existing_blog_titles': existing_blog,
            'existing_shorts_titles': existing_shorts,
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


def normalize_title(value) -> str:
    return ''.join(str(value or '').lower().split())


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
    validate_results(all_results, require_item=require_item, dry_run=dry_run)


def run_all_channels(pipeline: Pipeline, channels: dict, total_limit: int, dry_run: bool = False):
    """Run at most ``total_limit`` jobs across all channels combined."""
    remaining = max(total_limit, 0)
    all_results = []
    for name in ('naver_blog', 'ppojjugi_shorts', 'japan_shorts'):
        if remaining == 0:
            break
        batch = pipeline.run_channel(channels[name], limit=remaining, dry_run=dry_run)
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
    finally:
        notion.close()
    print_json(result)
    validate_results(result, require_item=require_item, dry_run=dry_run)


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
    finally:
        notion.close()
    print_json(result)
    validate_results(result, require_item=True, dry_run=False)


def validate_results(results: list[dict], require_item: bool = False, dry_run: bool = False) -> None:
    if require_item and len(results) != 1:
        raise RuntimeError(f'Expected exactly one Notion item, found {len(results)}')
    failures = [
        x for x in results
        if x.get('status') in {'failed', 'skipped'} or x.get('budget_blocked')
    ]
    if failures:
        raise RuntimeError(f'Notion processing did not complete: {failures}')
    if require_item and not dry_run and not results[0].get('notion_page_updated'):
        raise RuntimeError('Notion page update was not confirmed')


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


@app.command('naver-status')
def naver_status():
    print(naver_explain())


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
