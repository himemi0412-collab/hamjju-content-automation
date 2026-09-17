from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .ai import AIClient
from .budget import BudgetGuard
from .config import ChannelConfig
from .media import MediaGenerator, compose_short_video, concat_scene_audio, make_srt, render_blog_cards, save_manifest
from .notion_client import NotionClient, compact_page_context, extract_page_title, result_blocks
from .settings import Settings
from .state import StateStore
from .youtube import YouTubePrivateUploader


class Pipeline:
    def __init__(
        self,
        settings: Settings,
        notion: NotionClient,
        ai: AIClient,
        state: StateStore,
        budget: BudgetGuard | None = None,
    ):
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

    def process_page(self, cfg: ChannelConfig, page_stub: dict[str, Any], dry_run: bool = False) -> dict[str, Any]:
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
            'mode': 'media' if media_expected else 'text_only',
            'media_expected': media_expected,
            'youtube_upload_expected': bool(media_expected and self.s.auto_private_youtube_upload),
        }
        if not page_is_eligible(cfg, context):
            return {'page_id': page_id, 'status': 'skipped', 'reason': 'eligibility_changed'}
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
        self.notion.update_status(page_id, cfg.processing_status)
        try:
            use_web = cfg.name in {'naver_blog', 'japan_shorts'}
            generated, gen_usage = self.ai.generate(cfg.prompt_file, context, use_web=use_web)
            qa, qa_usage = self.ai.qa(generated, {'channel': cfg.name, **context})
            passed = bool(qa.get('pass')) and not qa.get('blocking_issues')

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
            }
            save_manifest(job_dir / 'manifest.json', manifest)

            media: dict[str, Any] = {}
            if cfg.content_kind == 'blog' and passed:
                card_news = generated.get('card_news') or []
                if len(card_news) != 5:
                    raise RuntimeError('Blog output did not contain exactly five card-news items')
                cards = render_blog_cards(card_news, job_dir / 'cards', self.s.card_font_path)
                if len(cards) != 5:
                    raise RuntimeError('Blog card-news render did not produce exactly five images')
                media['cards'] = [str(x) for x in cards]
                try:
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

            self.notion.append_blocks(page_id, result_blocks(cfg.name, generated, qa))
            final_status = cfg.success_status if passed else cfg.revision_status
            if media.get('budget_blocked'):
                final_status = cfg.revision_status
            if passed and media.get('youtube_url'):
                final_status = '비공개 업로드 완료'
            self.notion.update_status(page_id, final_status)
            self.state.finish(key, 'success', json.dumps({'final_status': final_status, 'media': media}, ensure_ascii=False))
            return {
                'page_id': page_id,
                'title': page_title,
                'status': final_status,
                'qa_pass': passed,
                'notion_page_updated': True,
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

    def _make_short_media(self, generated: dict[str, Any], job_dir: Path, channel_style: str) -> dict[str, Any]:
        media = MediaGenerator(
            self.s.openai_api_key,
            self.s.image_model,
            self.s.tts_model,
            self.s.tts_voice,
            self.s.card_font_path,
            self.s.image_quality,
            self.budget,
            self.s.openai_image_estimated_cost_usd,
            self.s.openai_tts_estimated_cost_usd,
        )
        scenes = generated.get('scenes') or []
        if not scenes:
            raise ValueError('No scenes to render')
        scene_narrations = [str(scene.get('narration') or '').strip() for scene in scenes]
        if any(not text for text in scene_narrations):
            raise ValueError('Every Shorts scene must include its exact narration segment')
        images = media.generate_scene_images(scenes, job_dir / 'scenes')
        total_characters = max(sum(len(text) for text in scene_narrations), 1)
        audio_parts: list[Path] = []
        for index, text in enumerate(scene_narrations, 1):
            share = self.s.openai_tts_estimated_cost_usd * len(text) / total_characters
            audio_parts.append(media.generate_tts(
                text,
                job_dir / 'narration_scenes' / f'{index:02d}.mp3',
                estimated_cost_usd=share,
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
