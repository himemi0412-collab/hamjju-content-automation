from __future__ import annotations
import hashlib
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
import httpx

from .ai import AIClient
from .budget import BudgetGuard
from .config import ChannelConfig
from .media import MediaGenerator, compose_short_video, concat_scene_audio, make_srt, render_blog_cards, save_manifest
from .notion_client import NotionClient, compact_page_context, extract_page_title, result_blocks
from .settings import Settings
from .state import StateStore
from .youtube import YouTubePrivateUploader
from .manuscript_repair import apply_reviewed_corrections, manuscript_hash


class Pipeline:
    def __init__(
        self,
        settings: Settings,
        notion: NotionClient,
        ai: AIClient,
        state: StateStore,
        budget: BudgetGuard | None = None,
    ):
        # httpx INFO includes full request URLs; Notion files use signed URLs.
        for logger_name in ('httpx', 'httpcore'):
            logging.getLogger(logger_name).setLevel(logging.WARNING)
        self.s = settings
        self.notion = notion
        self.ai = ai
        self.state = state
        self.budget = budget

    def run_channel(self, cfg: ChannelConfig, limit: int | None = None, dry_run: bool = False) -> list[dict[str, Any]]:
        ds = self.s.blog_data_source_id if cfg.source == 'blog' else self.s.shorts_data_source_id
        pages = self.notion.query_ready(
            ds,
            cfg.ready_status,
            cfg.notion_channel_value,
            page_size=limit or self.s.max_jobs_per_run,
            excluded_formula_property=cfg.excluded_formula_property,
            excluded_formula_value=cfg.excluded_formula_value,
            required_select_values=cfg.required_select_values,
            required_number_greater_than=cfg.required_number_greater_than,
            sort_property=cfg.sort_property,
        )
        results = []
        for page in pages[: limit or self.s.max_jobs_per_run]:
            results.append(self.process_page(cfg, page, dry_run=dry_run))
        return results

    def process_page(self, cfg: ChannelConfig, page_stub: dict[str, Any], dry_run: bool = False, resume_manifest: Path | None = None) -> dict[str, Any]:
        # Read/claim failures belong to this item, not to every remaining item.
        try:
            result = self._process_page(cfg, page_stub, dry_run=dry_run, resume_manifest=resume_manifest)
        except Exception as exc:
            result = {'page_id': page_stub.get('id'), 'status': 'failed', 'error': repr(exc)}
        return {'channel': cfg.name, **result}

    def _process_page(self, cfg: ChannelConfig, page_stub: dict[str, Any], dry_run: bool = False, resume_manifest: Path | None = None) -> dict[str, Any]:
        page_id = page_stub['id']
        page = self.notion.retrieve_page(page_id)
        page_text = self.notion.read_page_text(page_id)
        context = compact_page_context(page, page_text)
        page_title = extract_page_title(page)
        media_expected = bool(
            cfg.content_kind == 'shorts'
            and cfg.media_generation
            and self.s.enable_media_generation
        )
        context['automation_scope'] = {
            'current_date_kst': datetime.now(timezone(timedelta(hours=9))).date().isoformat(),
            'mode': 'media' if media_expected else 'text_only',
            'media_expected': media_expected,
            'youtube_upload_expected': bool(media_expected and self.s.auto_private_youtube_upload),
        }
        if not page_is_eligible(cfg, context):
            return {'page_id': page_id, 'status': 'skipped', 'reason': 'eligibility_changed'}
        previous = None
        prior_files = page.get('properties', {}).get('생성 이미지', {}).get('files', [])
        if resume_manifest is not None:
            previous = json.loads(resume_manifest.read_text(encoding='utf-8'))
            if previous.get('page_id') != page_id or previous.get('channel') != 'naver_blog' or cfg.name != 'naver_blog':
                raise ValueError('Resume manifest does not match the exact blog page')
            if not previous.get('generated') or not previous.get('qa'):
                raise ValueError('Resume manifest is missing the original manuscript or review')
            self._match_prior_blog_blocks(page_id, previous)
            expected_names = [Path(p).name for p in previous.get('media', {}).get('cards', [])]
            if [f.get('name') for f in prior_files] != expected_names:
                raise RuntimeError('MANUAL_EDIT_CONFLICT: attached files differ from the previous automation output')
            # The matched page is a prior machine output, not new source notes.
            # Avoid feeding its obsolete QA verdict back into the fresh review.
            context['existing_page_text'] = ''
        key = f"{page_id}:{page.get('last_edited_time')}:{cfg.name}:v1"
        if self.state.succeeded(key):
            return {'page_id': page_id, 'status': 'skipped', 'reason': 'idempotency'}
        if dry_run:
            # GitHub Actions logs are durable. Confirm that the page was read
            # without printing its properties or body into the workflow log.
            return {
                'page_id': page_id,
                'title': page_title,
                'status': 'dry_run',
                'channel': cfg.name,
                'last_edited_time': page.get('last_edited_time'),
                'content_loaded': bool(page_text or page.get('properties')),
            }

        self.state.start(key, page_id, cfg.name)
        try:
            self.notion.update_status(page_id, cfg.processing_status)
            use_web = cfg.name in {'naver_blog', 'japan_shorts'}
            if previous:
                generated = previous['generated']
                corrections = Path('repairs') / f'{page_id}.json'
                if corrections.exists():
                    generated = apply_reviewed_corrections(generated, page_id, corrections)
                gen_usage = {'reused_from_manifest': True}
            else:
                generated, gen_usage = self.ai.generate(cfg.prompt_file, context, use_web=use_web)
            if cfg.content_kind == 'blog':
                # Blog QA sees the finished images and manuscript together.
                qa, qa_usage = {'pass': False, 'status': 'PENDING_RENDER_REVIEW'}, {}
            else:
                qa, qa_usage = self.ai.qa(generated, {'channel': cfg.name, **context})
            passed = qa.get('pass') is True and not qa.get('blocking_issues')

            job_dir = self.s.output_dir / page_id.replace('-', '')[:16]
            job_dir.mkdir(parents=True, exist_ok=True)
            manifest = {
                'page_id': page_id,
                'channel': cfg.name,
                'generated': generated,
                'qa': qa,
                'usage': {'generation': gen_usage, 'qa': qa_usage},
                'budget': self.budget.snapshot() if self.budget else None,
                'created_at': datetime.now(timezone.utc).isoformat(),
                'manuscript_sha256': manuscript_hash(generated),
            }
            save_manifest(job_dir / 'manifest.json', manifest)

            media: dict[str, Any] = {}
            if cfg.content_kind == 'blog':
                card_news = generated.get('card_news') or []
                if len(card_news) != 5:
                    raise RuntimeError('Blog output did not contain exactly five card-news items')
                cards = render_blog_cards(card_news, job_dir / 'cards', self.s.card_font_path)
                if len(cards) != 5:
                    raise RuntimeError('Blog card-news render did not produce exactly five images')
                media['cards'] = [str(x) for x in cards]
                qa, image_qa_usage = self.ai.qa(generated, {
                    'channel': cfg.name, **context,
                    'automation_scope': {**context['automation_scope'], 'mode': 'blog_cards', 'stage': 'rendered_cards', 'media_expected': True,
                        'renderer': '1080 square; 3 pastel colors plus dark ink; white background; measured text boxes; cover/flow/comparison/checklist/decision; Cafe24 title font'},
                }, image_paths=cards)
                passed = qa.get('pass') is True and not qa.get('blocking_issues')
                media['rendered_card_qa_pass'] = passed
                manifest['qa'] = qa
                manifest['usage']['rendered_card_qa'] = image_qa_usage
                save_manifest(job_dir / 'manifest.json', manifest)
                try:
                    if previous:
                        self._match_prior_blog_blocks(page_id, previous)
                    self.notion.attach_files(page_id, '생성 이미지', cards)
                    media['notion_cards_attached'] = True
                except Exception as exc:
                    media['notion_cards_attached'] = False
                    media['notion_cards_error'] = repr(exc)
                    raise RuntimeError('Failed to attach all blog card-news images to Notion') from exc
            elif cfg.content_kind == 'shorts' and passed and cfg.media_generation and self.s.enable_media_generation:
                scenes = generated.get('scenes') or []
                media_cost = len(scenes) * self.s.openai_image_estimated_cost_usd + self.s.openai_tts_estimated_cost_usd
                if self.budget and not self.budget.can_spend(media_cost):
                    media = {
                        'budget_blocked': True,
                        'reason': 'monthly_internal_budget_guard',
                        'required_media_estimate_usd': round(media_cost, 6),
                        'budget': self.budget.snapshot(),
                    }
                else:
                    media = self._make_short_media(generated, job_dir, cfg.name)
                    if media.get('video'):
                        try:
                            self.notion.attach_files(page_id, '최종 영상', [Path(media['video'])])
                            media['notion_video_attached'] = True
                        except Exception as exc:
                            media['notion_video_attached'] = False
                            media['notion_video_error'] = repr(exc)
                    if self.s.auto_private_youtube_upload and media.get('video'):
                        media['youtube_url'] = self._upload_private(cfg.name, generated, Path(media['video']))
                        self.notion.update_properties(page_id, {
                            'YouTube 비공개 주소': {'url': media['youtube_url']},
                        })

            blocks = result_blocks(cfg.name, generated, qa)
            if previous:
                prior_blocks = self._match_prior_blog_blocks(page_id, previous)
                # Only replace a byte-equivalent previous automation section.
                # If a person edited the page, stop instead of overwriting it.
                self.notion.archive_blocks([block['id'] for block in prior_blocks])
            self.notion.append_blocks(page_id, blocks)
            final_status = cfg.success_status if passed else cfg.revision_status
            if media.get('budget_blocked'):
                final_status = cfg.revision_status
            if passed and media.get('youtube_url'):
                final_status = '비공개 업로드 완료'
            self.notion.update_status(page_id, final_status)
            output_verified = False
            if cfg.content_kind == 'blog':
                observation = self._verify_blog_output(page_id, page_title, final_status, blocks, [Path(x) for x in media.get('cards', [])])
                save_manifest(job_dir / 'notion-readback.json', observation)
                output_verified = True
            complete = passed and not media.get('budget_blocked')
            manifest.update({'media': media, 'final_status': final_status, 'output_verified': output_verified})
            manifest['budget'] = self.budget.snapshot() if self.budget else None
            save_manifest(job_dir / 'manifest.json', manifest)
            self.state.finish(key, 'success' if complete else 'revision_required', json.dumps({'final_status': final_status, 'media': media}, ensure_ascii=False))
            return {
                'page_id': page_id,
                'title': page_title,
                'status': final_status,
                'qa_pass': passed,
                'notion_page_updated': True,
                'output_verified': output_verified,
                'budget_blocked': bool(media.get('budget_blocked')),
                'media': media,
            }
        except Exception as exc:
            try:
                self.notion.update_status(page_id, cfg.revision_status)
            except Exception:
                pass
            self.state.finish(key, 'failed', repr(exc))
            return {'page_id': page_id, 'title': page_title, 'status': 'failed', 'error': repr(exc)}

    def _match_prior_blog_blocks(self, page_id: str, previous: dict[str, Any]) -> list[dict[str, Any]]:
        observed = self.notion.read_page_blocks(page_id)
        expected = result_blocks('naver_blog', previous['generated'], previous['qa'])
        if [block_signature(x) for x in observed] != [block_signature(x) for x in expected]:
            raise RuntimeError('MANUAL_EDIT_CONFLICT: current page does not match the previous automation output')
        if any(not block.get('id') for block in observed):
            raise RuntimeError('Resume block identities are unavailable')
        return observed

    def _verify_blog_output(self, page_id: str, page_title: str, status: str, expected_blocks: list[dict[str, Any]], cards: list[Path]) -> dict[str, Any]:
        page = self.notion.retrieve_page(page_id)
        observed_blocks = self.notion.read_page_blocks(page_id)
        expected = [block_signature(block) for block in expected_blocks]
        observed = [block_signature(block) for block in observed_blocks[-len(expected):]]
        if extract_page_title(page) != page_title:
            raise RuntimeError('Notion read-back title changed during production')
        if compact_page_context(page, '').get('properties', {}).get('상태') != status:
            raise RuntimeError('Notion read-back status did not match the saved result')
        if expected != observed:
            raise RuntimeError('Notion read-back body blocks did not match the complete generated result')
        files = page.get('properties', {}).get('생성 이미지', {}).get('files', [])
        if cards and [f.get('name') for f in files] != [p.name for p in cards]:
            raise RuntimeError('Notion read-back card count/order did not match all five images')
        hashes = []
        for file, path in zip(files, cards):
            url = file.get(file.get('type', ''), {}).get('url')
            if not url:
                raise RuntimeError('Notion read-back card URL is unavailable')
            try:
                response = httpx.get(url, follow_redirects=True, timeout=60)
                response.raise_for_status()
            except Exception:
                # Signed asset URLs must never enter durable logs.
                raise RuntimeError('Notion read-back card download failed') from None
            digest = hashlib.sha256(response.content).hexdigest()
            if digest != hashlib.sha256(path.read_bytes()).hexdigest():
                raise RuntimeError('Notion read-back image bytes did not match the rendered original')
            hashes.append({'name': path.name, 'sha256': digest})
        return {'page_id': page_id, 'title': page_title, 'status': status,
                'body_blocks_verified': len(expected), 'cards_verified': hashes,
                'verified_at': datetime.now(timezone.utc).isoformat(), 'naver_draft_verified': False}

    def _make_short_media(self, generated: dict[str, Any], job_dir: Path, channel_style: str) -> dict[str, Any]:
        narration_profiles = {
            'ppojjugi_shorts': (
                self.s.tts_ppojjugi_voice,
                'Young adult Korean woman speaking in a soft, dry, intimate diary monologue. '
                'Low-energy and slightly weary, with restrained emotion, gentle downward sentence endings, '
                'small natural sighs, and clear but unforced Korean. Keep a steady conversational pace and '
                'leave roughly half a second of reflective space between thoughts. No bright smile, cute acting, '
                'announcer projection, advertisement rhythm, or melodrama.',
            ),
            'japan_shorts': (
                self.s.tts_japan_voice,
                'Japanese woman in her 60s or 70s recalling an old memory with warmth, nostalgia, a little sadness, '
                'and quiet acceptance. Read in natural standard Japanese at a measured, human pace, with softly '
                'falling sentence endings and short reflective pauses. No mechanical TTS, broadcast narration, '
                'advertising tone, anime acting, or theatrical overperformance.',
            ),
        }
        if channel_style not in narration_profiles:
            raise ValueError(f'Unknown Shorts narration style: {channel_style}')
        voice, narration_instructions = narration_profiles[channel_style]
        media = MediaGenerator(
            self.s.openai_api_key,
            self.s.image_model,
            self.s.tts_model,
            voice,
            self.s.card_font_path,
            self.s.image_quality,
            self.budget,
            self.s.openai_image_estimated_cost_usd,
            self.s.openai_tts_estimated_cost_usd,
            self.s.fal_key,
            (
                self.s.fal_tts_ppojjugi_language
                if channel_style == 'ppojjugi_shorts'
                else self.s.fal_tts_japan_language
            ),
            self.s.fal_tts_temperature,
        )
        scenes = generated.get('scenes') or []
        if not scenes:
            raise ValueError('No scenes to render')
        scene_narrations = [str(scene.get('narration') or '').strip() for scene in scenes]
        if any(not text for text in scene_narrations):
            raise ValueError('Every Shorts scene must include its exact narration segment')
        images = media.generate_scene_images(scenes, job_dir / 'scenes', channel_style)
        total_characters = max(sum(len(text) for text in scene_narrations), 1)
        audio_parts: list[Path] = []
        for index, text in enumerate(scene_narrations, 1):
            share = self.s.openai_tts_estimated_cost_usd * len(text) / total_characters
            audio_parts.append(media.generate_tts(
                text,
                job_dir / 'narration_scenes' / f'{index:02d}.mp3',
                estimated_cost_usd=share,
                instructions=narration_instructions,
            ))
        audio, durations = concat_scene_audio(audio_parts, job_dir / 'narration.mp3')
        timed_scenes = [
            {**scene, 'seconds': round(duration, 3)}
            for scene, duration in zip(scenes, durations)
        ]
        generated['scenes'] = timed_scenes
        srt = make_srt(timed_scenes, job_dir / 'captions.srt')
        video = compose_short_video(
            images, timed_scenes, audio, srt, job_dir / 'short.mp4',
            channel_style=channel_style,
            hook=str(generated.get('hook') or ''),
            font_path=self.s.card_font_path,
        )
        return {
            'images': [str(x) for x in images],
            'audio': str(audio),
            'srt': str(srt),
            'video': str(video),
            'scene_durations': durations,
        }

    def _upload_private(self, channel_name: str, generated: dict[str, Any], video: Path) -> str:
        if self.budget:
            self.budget.require_below_limit('youtube_private_upload')
        channel_auth = {
            'ppojjugi_shorts': (
                self.s.youtube_ppojjugi_token_file,
                self.s.youtube_ppojjugi_channel_id,
            ),
            'japan_shorts': (
                self.s.youtube_japan_token_file,
                self.s.youtube_japan_channel_id,
            ),
        }
        if channel_name not in channel_auth:
            raise RuntimeError(f'YouTube upload is not configured for channel: {channel_name}')
        token_file, expected_channel_id = channel_auth[channel_name]
        uploader = YouTubePrivateUploader(self.s.youtube_client_secrets_file, token_file)
        authorized_channel = uploader.current_channel()
        if authorized_channel.get('id') != expected_channel_id:
            raise RuntimeError(
                f'YouTube channel mismatch for {channel_name}; upload stopped before transfer'
            )
        meta = generated.get('youtube') or {}
        return uploader.upload_private(
            video,
            title=str(meta.get('title') or generated.get('title') or 'Shorts draft'),
            description=str(meta.get('description') or ''),
            tags=list(meta.get('tags') or []),
        )


def block_signature(block: dict[str, Any]) -> tuple[str, str]:
    kind = block.get('type', '')
    rich_text = block.get(kind, {}).get('rich_text', [])
    return kind, ''.join(item.get('plain_text', item.get('text', {}).get('content', '')) for item in rich_text)


def page_is_eligible(cfg: ChannelConfig, context: dict[str, Any]) -> bool:
    properties = context.get('properties') or {}
    if properties.get('상태') != cfg.ready_status:
        return False
    if cfg.notion_channel_value and properties.get('채널') != cfg.notion_channel_value:
        return False
    if (
        cfg.excluded_formula_property
        and cfg.excluded_formula_value
        and properties.get(cfg.excluded_formula_property) == cfg.excluded_formula_value
    ):
        return False
    if not all(
        properties.get(property_name) == expected_value
        for property_name, expected_value in (cfg.required_select_values or {}).items()
    ):
        return False
    return all(
        isinstance(properties.get(property_name), (int, float))
        and not isinstance(properties[property_name], bool)
        and properties[property_name] > minimum_value
        for property_name, minimum_value in (cfg.required_number_greater_than or {}).items()
    )
