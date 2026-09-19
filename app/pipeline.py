from __future__ import annotations
import hashlib
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
import httpx

from .ai import AIClient
from .budget import BudgetGuard
from .config import ChannelConfig
from .media import (
    MediaGenerator, compose_short_video, concat_scene_audio, make_srt,
    render_blog_cards, save_manifest, verify_short_artifacts,
)
from .notion_client import NotionClient, compact_page_context, extract_page_title, naver_handoff_blocks, result_blocks
from .settings import Settings
from .state import StateStore
from .youtube import YouTubePrivateUploader
from .manuscript_repair import apply_reviewed_corrections, manuscript_hash
from .blog_reference import load_blog_reference, validate_generated_reference_contract
from .card_design import apply_named_design_contract
from .ownership import load_operating_contract


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
        self.operating_contract = load_operating_contract(settings.operating_contract_path)

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

    def process_page(
        self, cfg: ChannelConfig, page_stub: dict[str, Any], dry_run: bool = False,
        resume_manifest: Path | None = None, reviewed_manifest: Path | None = None,
    ) -> dict[str, Any]:
        # Read/claim failures belong to this item, not to every remaining item.
        try:
            result = self._process_page(
                cfg, page_stub, dry_run=dry_run,
                resume_manifest=resume_manifest, reviewed_manifest=reviewed_manifest,
            )
        except Exception as exc:
            result = {'page_id': page_stub.get('id'), 'status': 'failed', 'error': repr(exc)}
        return {'channel': cfg.name, **result}

    def _process_page(
        self, cfg: ChannelConfig, page_stub: dict[str, Any], dry_run: bool = False,
        resume_manifest: Path | None = None, reviewed_manifest: Path | None = None,
    ) -> dict[str, Any]:
        page_id = page_stub['id']
        page = self.notion.retrieve_page(page_id)
        page_text = self.notion.read_page_text(page_id)
        context = compact_page_context(page, page_text)
        page_title = extract_page_title(page)
        source_version = str(page.get('last_edited_time') or 'unknown-source-version')
        run_id = os.getenv('GITHUB_RUN_ID') or 'local'
        ownership_cfg = self.operating_contract.channel(cfg.name)
        if cfg.name == 'naver_blog' and cfg.success_status != ownership_cfg.handoff_status:
            raise RuntimeError('Channel config and operating contract handoff status differ')
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
            'public_approval': context.get('properties', {}).get('공개 승인') is True,
            'public_upload_enabled': self.s.allow_public_youtube_upload,
        }
        if cfg.content_kind == 'blog':
            # GitHub-hosted runs do not inherit local Codex memories or user
            # skills. Load the versioned Hamzzu baseline on every blog run so
            # reference fidelity is an explicit input, not remembered context.
            context['reference_baseline'] = load_blog_reference()
        if not page_is_eligible(cfg, context):
            return {'page_id': page_id, 'status': 'skipped', 'reason': 'eligibility_changed'}
        previous = None
        reviewed_previous = None
        prior_card_receipt = None
        prior_reviewed_cards: list[Path] = []
        prior_files = page.get('properties', {}).get('생성 이미지', {}).get('files', [])
        if resume_manifest is not None:
            previous = json.loads(resume_manifest.read_text(encoding='utf-8'))
            if previous.get('page_id') != page_id or previous.get('channel') != 'naver_blog' or cfg.name != 'naver_blog':
                raise ValueError('Resume manifest does not match the exact blog page')
            if not previous.get('generated') or not previous.get('qa'):
                raise ValueError('Resume manifest is missing the original manuscript or review')
            reviewed_previous = previous
            reviewed_root = resume_manifest.parent
            if reviewed_manifest is not None:
                reviewed_previous = json.loads(reviewed_manifest.read_text(encoding='utf-8'))
                if (reviewed_previous.get('page_id') != page_id
                        or reviewed_previous.get('channel') != 'naver_blog'):
                    raise ValueError('Reviewed manifest does not match the exact blog page')
                if manuscript_hash(reviewed_previous.get('generated') or {}) != manuscript_hash(previous['generated']):
                    raise RuntimeError('Reviewed manuscript is not the same version as the current recovery source')
                reviewed_root = reviewed_manifest.parent
            self._match_prior_blog_blocks(page_id, previous)
            expected_names = [Path(p).name for p in previous.get('media', {}).get('cards', [])]
            source_cards = [resume_manifest.parent / 'cards' / name for name in expected_names]
            if any(not path.is_file() for path in source_cards):
                raise RuntimeError('Resume artifact is missing original image bytes')
            reviewed_names = [Path(p).name for p in reviewed_previous.get('media', {}).get('cards', [])]
            if reviewed_names != expected_names:
                raise RuntimeError('Reviewed card names/order differ from the current recovery source')
            prior_reviewed_cards = [reviewed_root / 'cards' / name for name in reviewed_names]
            if any(not path.is_file() for path in prior_reviewed_cards):
                raise RuntimeError('Reviewed artifact is missing original image bytes')
            if any(
                hashlib.sha256(source.read_bytes()).digest() != hashlib.sha256(reviewed.read_bytes()).digest()
                for source, reviewed in zip(source_cards, prior_reviewed_cards)
            ):
                raise RuntimeError('Reviewed card bytes differ from the current recovery source')
            if [f.get('name') for f in prior_files] != expected_names:
                # An upload may have completed before the body replacement failed.
                # Reconcile only a reviewed receipt bound to this exact manuscript,
                # checking downloaded bytes, never filenames alone.
                receipt_path = Path('repairs') / f'{page_id}.json'
                receipt = json.loads(receipt_path.read_text(encoding='utf-8')) if receipt_path.exists() else {}
                if receipt.get('page_id') != page_id or receipt.get('source_sha256') != manuscript_hash(previous['generated']):
                    raise RuntimeError('MANUAL_EDIT_CONFLICT: attached files differ from the previous automation output')
                prior_card_receipt = receipt.get('attached_cards', [])
                match_attached_cards(prior_files, prior_card_receipt)
            elif expected_names:
                prior_card_receipt = [
                    {'name': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                    for path in source_cards
                ]
                try:
                    match_attached_cards(prior_files, prior_card_receipt)
                except RuntimeError as exc:
                    # A reviewed interrupted-upload receipt may have the same
                    # filenames as the older cards, but different exact bytes.
                    receipt_path = Path('repairs') / f'{page_id}.json'
                    receipt = json.loads(receipt_path.read_text(encoding='utf-8')) if receipt_path.exists() else {}
                    if ('MANUAL_EDIT_CONFLICT' not in str(exc) or receipt.get('page_id') != page_id
                            or receipt.get('source_sha256') != manuscript_hash(previous['generated'])):
                        raise
                    prior_card_receipt = receipt.get('attached_cards', [])
                    match_attached_cards(prior_files, prior_card_receipt)
            # The matched page is a prior machine output, not new source notes.
            # Avoid feeding its obsolete QA verdict back into the fresh review.
            context['existing_page_text'] = ''
        context['named_design_language_required'] = not bool(previous)
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
        self.operating_contract.receipt(
            cfg.name,
            document_id=page_id,
            source_version=source_version,
            stage='CLAIMED',
        )
        self.state.record_stage(page_id, cfg.name, source_version, 'github_actions', 'CLAIMED', run_id)
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
                    generated = apply_named_design_contract(generated)
            content_version = manuscript_hash(generated)
            ownership_receipt = self.operating_contract.receipt(
                cfg.name,
                document_id=page_id,
                source_version=content_version,
                stage='CONTENT_READY',
            )
            self.state.record_stage(page_id, cfg.name, content_version, 'github_actions', 'CONTENT_READY', run_id)
            if cfg.content_kind == 'blog':
                validate_generated_reference_contract(
                    generated, context['reference_baseline'],
                    require_design_language=not bool(previous),
                )
                # Blog QA sees the finished images and manuscript together.
                qa, qa_usage = {'pass': False, 'status': 'PENDING_RENDER_REVIEW'}, {}
            else:
                qa, qa_usage = self.ai.qa(generated, {'channel': cfg.name, **context})
            passed = qa.get('pass') is True and not qa.get('blocking_issues')
            if cfg.content_kind == 'shorts' and passed:
                ownership_receipt = self.operating_contract.receipt(
                    cfg.name, document_id=page_id, source_version=content_version, stage='QA_PASS',
                )
                self.state.record_stage(page_id, cfg.name, content_version, 'github_actions', 'QA_PASS', run_id)

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
                'ownership': ownership_receipt,
            }
            if cfg.content_kind == 'blog':
                manifest['reference_baseline_id'] = context['reference_baseline']['id']
                manifest['reference_baseline_sha256'] = context['reference_baseline']['sha256']
                manifest['reference_source_urls'] = context['reference_baseline']['source_urls']
            if previous:
                manifest['resume_source_manuscript_sha256'] = manuscript_hash(previous['generated'])
            save_manifest(job_dir / 'manifest.json', manifest)

            media: dict[str, Any] = {}
            if cfg.content_kind == 'blog':
                card_news = generated.get('card_news') or []
                if len(card_news) != 5:
                    raise RuntimeError('Blog output did not contain exactly five card-news items')
                cards = render_blog_cards(
                    card_news, job_dir / 'cards', self.s.card_font_path,
                    card_format=generated['card_format'], visual_family=generated['visual_family'],
                    design_language=generated.get('design_language'),
                    design_blueprint=generated.get('design_blueprint'),
                )
                if len(cards) != 5:
                    raise RuntimeError('Blog card-news render did not produce exactly five images')
                media['cards'] = [str(x) for x in cards]
                self.state.record_stage(page_id, cfg.name, content_version, 'github_actions', 'MEDIA_READY', run_id)
                prior_qa = reviewed_previous.get('qa', {}) if reviewed_previous else {}
                unchanged_reviewed_resume = bool(
                    previous
                    and reviewed_previous
                    and manuscript_hash(generated) == manuscript_hash(reviewed_previous['generated'])
                    and prior_qa.get('pass') is True
                    and not prior_qa.get('blocking_issues')
                    and reviewed_previous.get('reference_baseline_id') == context['reference_baseline']['id']
                    and reviewed_previous.get('reference_baseline_sha256') == context['reference_baseline']['sha256']
                    and reviewed_previous.get('media', {}).get('rendered_card_qa_pass') is True
                    and len(prior_reviewed_cards) == 5
                    and all(
                        hashlib.sha256(current.read_bytes()).digest()
                        == hashlib.sha256(reviewed.read_bytes()).digest()
                        for current, reviewed in zip(cards, prior_reviewed_cards)
                    )
                )
                if unchanged_reviewed_resume:
                    # A migration must not turn an immutable, already-reviewed
                    # artifact into a revision merely because a stochastic AI
                    # reviewer gives the same bytes a different answer later.
                    qa = prior_qa
                    image_qa_usage = {
                        'reused_from_manifest': True,
                        'source_manuscript_sha256': manuscript_hash(reviewed_previous['generated']),
                    }
                else:
                    qa, image_qa_usage = self.ai.qa(generated, {
                        'channel': cfg.name, **context,
                        'automation_scope': {**context['automation_scope'], 'mode': 'blog_cards', 'stage': 'rendered_cards', 'media_expected': True,
                            'renderer': (
                                f"named design language={generated.get('design_language')}; "
                                f"direction={generated.get('design_direction')}; measured text; "
                                f"cover-first blueprint={generated.get('design_blueprint')}; "
                                'cover/flow/comparison/checklist/decision; Cafe24 title font'
                                if generated.get('design_language') else
                                'legacy reviewed renderer; preserve original bytes; measured text; '
                                'cover/flow/comparison/checklist/decision; Cafe24 title font'
                            )},
                    }, image_paths=cards)
                passed = qa.get('pass') is True and not qa.get('blocking_issues')
                if passed:
                    ownership_receipt = self.operating_contract.receipt(
                        cfg.name, document_id=page_id, source_version=content_version, stage='QA_PASS',
                    )
                    self.state.record_stage(page_id, cfg.name, content_version, 'github_actions', 'QA_PASS', run_id)
                media['rendered_card_qa_pass'] = passed
                manifest['qa'] = qa
                manifest['usage']['rendered_card_qa'] = image_qa_usage
                manifest['media'] = media
                save_manifest(job_dir / 'manifest.json', manifest)
                # Never attach cards that failed the independent rendered-image
                # review. Failed artifacts stay only in the Actions artifact so
                # a retry cannot be mistaken for an approved Notion handoff.
                if passed:
                    try:
                        if previous:
                            self._match_prior_blog_blocks(page_id, previous)
                            latest_files = self.notion.retrieve_page(page_id).get('properties', {}).get('생성 이미지', {}).get('files', [])
                            if prior_card_receipt is not None:
                                match_attached_cards(latest_files, prior_card_receipt)
                            elif latest_files:
                                raise RuntimeError('MANUAL_EDIT_CONFLICT: images were added during review')
                        card_upload_ids = self.notion.attach_files(page_id, '생성 이미지', cards)
                        if len(card_upload_ids) != 5:
                            raise RuntimeError('Notion did not return five card upload identities')
                        media['notion_cards_attached'] = True
                        media['notion_card_upload_ids'] = card_upload_ids
                        manifest['media'] = media
                        if previous:
                            manifest['resume_previous_output'] = previous.get('resume_previous_output') or previous
                        save_manifest(job_dir / 'manifest.json', manifest)
                    except Exception as exc:
                        media['notion_cards_attached'] = False
                        media['notion_cards_error'] = repr(exc)
                        raise RuntimeError('Failed to attach all blog card-news images to Notion') from exc
                else:
                    media['notion_cards_attached'] = False
                    media['notion_cards_skipped_reason'] = 'rendered_card_qa_failed'
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
                    ownership_receipt = self.operating_contract.receipt(
                        cfg.name, document_id=page_id, source_version=content_version, stage='MEDIA_READY',
                    )
                    self.state.record_stage(page_id, cfg.name, content_version, 'github_actions', 'MEDIA_READY', run_id)
                    if media.get('video'):
                        try:
                            self.notion.attach_files(page_id, '최종 영상', [Path(media['video'])])
                            media['notion_video_attached'] = True
                        except Exception as exc:
                            media['notion_video_attached'] = False
                            media['notion_video_error'] = repr(exc)
                    if self.s.auto_private_youtube_upload and media.get('video'):
                        public_approved = context.get('properties', {}).get('공개 승인') is True
                        privacy_status = (
                            'public'
                            if public_approved and self.s.allow_public_youtube_upload
                            else 'private'
                        )
                        media['youtube_url'] = self._upload_private(
                            cfg.name, generated, Path(media['video']),
                            privacy_status=privacy_status,
                        )
                        media['youtube_privacy'] = privacy_status
                        media['public_approval_confirmed'] = public_approved
                        self.notion.update_properties(page_id, {
                            'YouTube 비공개 주소': {'url': media['youtube_url']},
                        })

            naver_handoff = None
            if cfg.content_kind == 'blog' and passed:
                ownership_receipt = self.operating_contract.receipt(
                    cfg.name, document_id=page_id, source_version=content_version, stage='HANDOFF_READY',
                )
                naver_handoff = {
                    'document_id': page_id,
                    'source_version': manifest['manuscript_sha256'],
                    'card_upload_ids': list(media.get('notion_card_upload_ids') or []),
                    'card_names': [Path(x).name for x in media.get('cards', [])],
                    'ownership_receipt': ownership_receipt,
                }
            blocks = result_blocks(cfg.name, generated, qa, naver_handoff=naver_handoff) if passed else []
            if passed:
                if previous:
                    prior_blocks = self._match_prior_blog_blocks(page_id, previous)
                    # Only replace a byte-equivalent previous automation section.
                    # If a person edited the page, stop instead of overwriting it.
                    self.notion.archive_blocks([block['id'] for block in prior_blocks])
                self.notion.append_blocks(page_id, blocks)
            manifest.pop('resume_previous_output', None)
            save_manifest(job_dir / 'manifest.json', manifest)
            final_status = cfg.success_status if passed else cfg.revision_status
            if media.get('budget_blocked'):
                final_status = cfg.revision_status
            if passed and media.get('youtube_url'):
                final_status = (
                    '공개 업로드 완료'
                    if media.get('youtube_privacy') == 'public'
                    else '비공개 업로드 완료'
                )
                ownership_receipt = self.operating_contract.receipt(
                    cfg.name,
                    document_id=page_id,
                    source_version=content_version,
                    stage='PRIVATE_UPLOAD_VERIFIED',
                )
                self.state.record_stage(
                    page_id, cfg.name, content_version, 'github_actions', 'PRIVATE_UPLOAD_VERIFIED', run_id,
                    media['youtube_url'],
                )
            self.notion.update_status(page_id, final_status)
            output_verified = False
            if cfg.content_kind == 'blog' and passed:
                observation = self._verify_blog_output(page_id, page_title, final_status, blocks, [Path(x) for x in media.get('cards', [])])
                save_manifest(job_dir / 'notion-readback.json', observation)
                output_verified = True
            complete = passed and not media.get('budget_blocked')
            if cfg.content_kind == 'blog' and passed:
                self.state.record_stage(page_id, cfg.name, content_version, 'github_actions', 'HANDOFF_READY', run_id)
            manifest.update({
                'media': media,
                'final_status': final_status,
                'output_verified': output_verified,
                'ownership': ownership_receipt,
            })
            manifest['budget'] = self.budget.snapshot() if self.budget else None
            save_manifest(job_dir / 'manifest.json', manifest)
            self.state.finish(key, 'success' if complete else 'revision_required', json.dumps({'final_status': final_status, 'media': media}, ensure_ascii=False))
            return {
                'page_id': page_id,
                'title': page_title,
                'status': final_status,
                'qa_pass': passed,
                'blocking_issues': list(qa.get('blocking_issues') or []),
                'qa_score': qa.get('score'),
                'notion_page_updated': True,
                'output_verified': output_verified,
                'budget_blocked': bool(media.get('budget_blocked')),
                'media': media,
                'ownership': ownership_receipt,
            }
        except Exception as exc:
            try:
                self.notion.update_status(page_id, cfg.revision_status)
            except Exception:
                pass
            self.state.finish(key, 'failed', repr(exc))
            try:
                self.state.record_stage(
                    page_id, cfg.name, source_version, 'github_actions', 'FAILED', run_id, repr(exc),
                )
            except Exception:
                pass
            return {'page_id': page_id, 'title': page_title, 'status': 'failed', 'error': repr(exc)}

    def _match_prior_blog_blocks(self, page_id: str, previous: dict[str, Any]) -> list[dict[str, Any]]:
        observed = self.notion.read_page_blocks(page_id)
        prior_output = previous.get('resume_previous_output') or previous
        prior_media = prior_output.get('media') or {}
        prior_qa = prior_output['qa']
        prior_handoff = None
        if prior_qa.get('pass') is True and not prior_qa.get('blocking_issues') and prior_media.get('notion_card_upload_ids'):
            prior_handoff = {
                'document_id': prior_output['page_id'],
                'source_version': prior_output.get('manuscript_sha256') or manuscript_hash(prior_output['generated']),
                'card_upload_ids': list(prior_media['notion_card_upload_ids']),
                'card_names': [Path(x).name for x in prior_media.get('cards', [])],
            }
            prior_ownership = prior_output.get('ownership')
            if prior_ownership and prior_ownership.get('stage') == 'HANDOFF_READY':
                prior_handoff['ownership_receipt'] = prior_ownership
        expected = result_blocks('naver_blog', prior_output['generated'], prior_qa, naver_handoff=prior_handoff)
        expected_signatures = [block_signature(x) for x in expected]
        # Production appends the machine handoff after the user's planning
        # notes. Match and archive only that exact suffix; never treat the
        # source notes as automation-owned content.
        prior_section = observed[-len(expected):] if len(observed) >= len(expected) else []
        observed_signatures = [block_signature(x) for x in prior_section]
        if observed_signatures != expected_signatures:
            # Runs created before the final-section ordering fix placed a
            # paragraph-targeted decision card before the preceding section's
            # summary card. Accept only that exact historical machine output;
            # every other difference remains a manual-edit conflict.
            if prior_handoff is None:
                raise RuntimeError('MANUAL_EDIT_CONFLICT: current page does not match the previous automation output')
            legacy_expected = naver_handoff_blocks(
                prior_output['generated'], prior_qa, **prior_handoff,
                _legacy_paragraph_order=True,
            )
            prior_section = observed[-len(legacy_expected):] if len(observed) >= len(legacy_expected) else []
            observed_signatures = [block_signature(x) for x in prior_section]
            if observed_signatures != [block_signature(x) for x in legacy_expected]:
                raise RuntimeError('MANUAL_EDIT_CONFLICT: current page does not match the previous automation output')
        if any(not block.get('id') for block in prior_section):
            raise RuntimeError('Resume block identities are unavailable')
        return prior_section

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
        narrator_profile = generated.get('narrator_profile') or {}
        if channel_style == 'japan_shorts':
            profile_key = str(narrator_profile.get('profile') or 'older_woman')
            approved_voices = {
                'older_woman': self.s.tts_japan_voice,
                'young_woman': self.s.tts_japan_young_woman_voice,
                'young_man': self.s.tts_japan_young_man_voice,
                'older_man': self.s.tts_japan_older_man_voice,
            }
            if profile_key not in approved_voices:
                raise ValueError(f'Unknown Japanese narrator profile: {profile_key}')
            voice = approved_voices[profile_key]
            if not voice:
                raise RuntimeError(
                    f'Japanese narrator profile {profile_key} has no user-approved voice; '
                    'media generation stopped before TTS'
                )
            narration_instructions = (
                narration_instructions
                + ' Selected story narrator profile: '
                + json.dumps(narrator_profile, ensure_ascii=False)
            )
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
        verification = verify_short_artifacts(images, timed_scenes, audio, srt, video)
        return {
            'images': [str(x) for x in images],
            'audio': str(audio),
            'srt': str(srt),
            'video': str(video),
            'scene_durations': durations,
            'narrator_profile': narrator_profile,
            'tts_voice': voice,
            'verification': verification,
        }

    def _upload_private(
        self, channel_name: str, generated: dict[str, Any], video: Path,
        privacy_status: str = 'private',
    ) -> str:
        if self.budget:
            self.budget.require_below_limit('youtube_private_upload')
        if privacy_status == 'public' and not self.s.allow_public_youtube_upload:
            raise RuntimeError('Public YouTube upload is disabled by repository configuration')
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
        return uploader.upload_reviewed(
            video,
            title=str(meta.get('title') or generated.get('title') or 'Shorts draft'),
            description=str(meta.get('description') or ''),
            tags=list(meta.get('tags') or []),
            privacy_status=privacy_status,
        )


def match_attached_cards(files: list[dict], receipt: list[dict]) -> None:
    if not receipt or len(receipt) != 5 or [f.get('name') for f in files] != [r.get('name') for r in receipt]:
        raise RuntimeError('MANUAL_EDIT_CONFLICT: attached image receipt does not match')
    for file, expected in zip(files, receipt):
        url = file.get(file.get('type', ''), {}).get('url')
        if not url:
            raise RuntimeError('Attached image reconciliation URL is unavailable')
        try:
            response = httpx.get(url, follow_redirects=True, timeout=60)
            response.raise_for_status()
        except Exception:
            raise RuntimeError('Attached image reconciliation download failed') from None
        if hashlib.sha256(response.content).hexdigest() != expected.get('sha256'):
            raise RuntimeError('MANUAL_EDIT_CONFLICT: attached image bytes changed')


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
