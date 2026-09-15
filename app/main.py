from __future__ import annotations
import json
import logging
import typer
from rich import print

from .ai import AIClient
from .config import load_channels
from .notion_client import NotionClient
from .pipeline import Pipeline
from .settings import Settings
from .state import StateStore
from .youtube import YouTubePrivateUploader
from .naver import explain as naver_explain

app = typer.Typer(help='햄쮸 블로그·쇼츠 자동화')


def build() -> tuple[Settings, NotionClient, AIClient, StateStore, Pipeline]:
    s = Settings()
    logging.basicConfig(level=getattr(logging, s.log_level.upper(), logging.INFO))
    if not s.notion_ready:
        raise typer.BadParameter('NOTION_ACCESS_TOKEN is required in .env')
    if not s.openai_ready:
        raise typer.BadParameter('OPENAI_API_KEY is required in .env')
    notion = NotionClient(s.notion_access_token)
    ai = AIClient(s.openai_api_key, s.text_model, s.qa_model, s.enable_web_research)
    state = StateStore(s.state_db)
    return s, notion, ai, state, Pipeline(s, notion, ai, state)


@app.command()
def run(limit: int | None = None, dry_run: bool = False):
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
def channel(name: str, limit: int | None = None, dry_run: bool = False):
    """Process a single configured channel."""
    s, notion, _, _, pipeline = build()
    channels = load_channels()
    if name not in channels:
        raise typer.BadParameter(f'Unknown channel: {name}')
    try:
        result = pipeline.run_channel(
            channels[name],
            limit=limit if limit is not None else s.max_jobs_per_run,
            dry_run=dry_run,
        )
    finally:
        notion.close()
    print_json(result)


@app.command('setup-youtube-auth')
def setup_youtube_auth():
    """One-time OAuth setup. This command never uploads a video."""
    s = Settings()
    uploader = YouTubePrivateUploader(s.youtube_client_secrets_file, s.youtube_token_file)
    uploader.authorize_interactively()
    print('[green]YouTube OAuth token saved.[/green]')


@app.command('naver-status')
def naver_status():
    print(naver_explain())


@app.command('doctor')
def doctor():
    """Check configuration without sending content anywhere."""
    s = Settings()
    checks = {
        'openai_key_present': bool(s.openai_api_key),
        'notion_token_present': bool(s.notion_access_token),
        'blog_data_source_id': s.blog_data_source_id,
        'shorts_data_source_id': s.shorts_data_source_id,
        'media_generation': s.enable_media_generation,
        'auto_private_youtube_upload': s.auto_private_youtube_upload,
        'youtube_client_secret_exists': s.youtube_client_secrets_file.exists(),
        'youtube_token_exists': s.youtube_token_file.exists(),
    }
    print_json(checks)


def print_json(value):
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


if __name__ == '__main__':
    app()
