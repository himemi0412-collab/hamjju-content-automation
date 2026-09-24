from __future__ import annotations
import hashlib
import json
import logging
import os
import re
import shutil
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
import httpx

from .ai import AIClient, parse_json
from .budget import BudgetGuard
from .config import ChannelConfig
from .media import (
    MediaGenerator, compose_short_video, concat_scene_audio, make_srt,
    render_blog_cards, save_manifest, verify_short_artifacts,
)
from .notion_client import (
    NotionClient, compact_page_context, extract_page_title, naver_handoff_blocks,
    property_value, result_blocks,
)
from .settings import Settings
from .state import StateStore
from .youtube import YouTubePrivateUploader
from .manuscript_repair import apply_reviewed_corrections, manuscript_hash
from .blog_reference import load_blog_reference, validate_generated_reference_contract
from .card_design import apply_named_design_contract
from .ownership import load_operating_contract
from .photographic_cards import generate_and_typeset_blog_cards, production_design_language


def _card_numbers_from_qa_issue(issue: Any) -> set[int]:
    """Extract card numbers from either legacy text or the structured visual-QA schema."""
    numbers: set[int] = set()
    if isinstance(issue, dict):
        raw_cards: list[Any] = []
        single_card = issue.get('card')
        if single_card is not None:
            raw_cards.append(single_card)
        many_cards = issue.get('cards')
        if isinstance(many_cards, (list, tuple, set)):
            raw_cards.extend(many_cards)
        elif many_cards is not None:
            raw_cards.append(many_cards)
        for value in raw_cards:
            if str(value).isdigit() and 1 <= int(value) <= 5:
                numbers.add(int(value))
        issue = ' '.join(
            str(issue.get(key) or '') for key in ('issue', 'evidence') if issue.get(key)
        )
    for group in re.findall(
        r'카드\s*([1-5](?:\s*(?:번|,|·|와|과|및|/|-)\s*[1-5])*)',
        str(issue),
    ):
        numbers.update(int(value) for value in re.findall(r'[1-5]', group))
    numbers.update(int(value) for value in re.findall(r'([1-5])번\s*카드', str(issue)))
    return numbers


def _visual_qa_passed(qa: dict[str, Any]) -> bool:
    try:
        ai_score = int(qa.get('ai_likeness_score', 100))
    except (TypeError, ValueError):
        ai_score = 100
    return qa.get('pass') is True and ai_score < 5 and not (qa.get('blocking_issues') or [])


def _shorts_script_qa_passed(qa: dict[str, Any]) -> bool:
    """Script QA precedes image creation; visual likeness is reviewed later."""
    return qa.get('pass') is True and not (qa.get('blocking_issues') or [])


def align_single_speaker_profile(generated: dict[str, Any]) -> dict[str, Any]:
    """Correct contradictory metadata without changing dialogue or scene voices."""
    narrator = generated.get('narrator_profile')
    scenes = generated.get('scenes')
    if not isinstance(narrator, dict) or narrator.get('profile') != 'multiple':
        return generated
    if not isinstance(scenes, list) or not scenes or any(not isinstance(s, dict) for s in scenes):
        return generated
    speakers = {str(s.get('speaker_profile') or '').strip() for s in scenes}
    if len(speakers) == 1 and speakers <= {'older_woman', 'older_man', 'young_woman', 'young_man'}:
        speaker = speakers.pop()
        generated['narrator_profile'] = {**narrator, 'profile': speaker}
    return generated


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

    def rerender_existing_blog_cards(self, page_id: str) -> dict[str, Any]:
        """Replace only the file property for an unsaved blog; never regenerate its article."""
        page = self.notion.retrieve_page(page_id)
        props = page.get('properties', {})
        draft_url = props.get('네이버 임시저장 주소', {}).get('url')
        if draft_url:
            raise RuntimeError('NAVER_DRAFT_ALREADY_EXISTS')
        original_status = props.get('상태', {}).get('select', {}).get('name') or ''
        if original_status in {'임시저장 완료', 'VERIFIED_NAVER_DRAFT', '완료'}:
            raise RuntimeError('NAVER_DRAFT_STATUS_ALREADY_COMPLETE')
        try:
            snapshot = self.notion.read_latest_json_snapshot(page_id)
            generated = deepcopy(snapshot['generated'])
        except RuntimeError as exc:
            if 'NOTION_AUTOMATION_SNAPSHOT_MISSING' not in str(exc):
                raise
            article_text = self.notion.read_page_text(page_id)
            page_title = extract_page_title(page)
            recovery_prompt = {
                'title': page_title,
                'existing_article': article_text[:16000],
                'task': (
                    'Recover only a five-card visual plan from this already-written Korean blog article. '
                    'Do not rewrite the article. Return JSON with card_news only. '
                    'Cards must be numbered 1..5 with layouts cover, flow, comparison, checklist, decision. '
                    'Each card needs a Korean headline (max 18 characters), Korean copy (max 42 characters), '
                    'and items containing label and detail. Item counts must be 2,3,2,4,3 respectively. '
                    'Make every item concrete enough to generate a different appliance lifestyle photograph.'
                ),
            }
            response = self.ai._create_response({
                'model': self.s.text_model,
                'max_output_tokens': self.s.max_generation_output_tokens,
                'input': [
                    {'role': 'system', 'content': 'Return one valid JSON object only. Preserve the article facts.'},
                    {'role': 'user', 'content': json.dumps(recovery_prompt, ensure_ascii=False)},
                ],
            }, 'blog_card_plan_recovery', False)
            recovered = parse_json(response.output_text)
            generated = {
                'body_markdown': article_text,
                'card_news': recovered.get('card_news') or [],
                'card_format': 'square',
                'visual_family': 'soft_scene',
                'design_language': 'Bento Editorial',
                'reference_profile_id': 'LEGACY_ARTICLE_CARD_RECOVERY',
            }
        original_body_hash = hashlib.sha256(
            str(generated.get('body_markdown') or '').encode('utf-8')
        ).hexdigest()
        generated['design_language'] = production_design_language(
            generated.get('design_language')
        )
        generated = apply_named_design_contract(generated)
        if hashlib.sha256(str(generated.get('body_markdown') or '').encode('utf-8')).hexdigest() != original_body_hash:
            raise RuntimeError('ARTICLE_BODY_CHANGED_DURING_CARD_RERENDER')
        baseline = load_blog_reference()
        # The article is byte-frozen here; legacy reference metadata must not block image-only repair.
        out_dir = self.s.output_dir / page_id.replace('-', '')[:16] / 'cards'
        cards = generate_and_typeset_blog_cards(
            self.ai.client,
            list(generated.get('card_news') or []),
            out_dir,
            model=self.s.image_model,
            quality=self.s.image_quality,
            font_path=self.s.card_font_path,
            budget=self.budget,
            estimated_cost_usd=self.s.openai_image_estimated_cost_usd,
            design_language=str(generated.get('design_language') or 'Bento Editorial'),
            design_blueprint=generated.get('design_blueprint'),
            subject_hint=str(generated.get('title') or ''),
        )
        qa, usage = self.ai.qa(generated, {
            'channel': 'naver_blog',
            'reference_baseline': baseline,
            'source_context': {'named_design_language_required': True},
            'automation_scope': {
                'mode': 'rerender_existing_cards', 'stage': 'rendered_cards',
                'media_expected': True, 'article_body_frozen': True,
                'visual_ratio': '60-70', 'text_ratio': '30-40',
            },
        }, qa_prompt='prompts/qa_photographic_blog_cards.md', image_paths=cards)
        # This route deliberately freezes the approved article.  QA may still
        # report legacy manuscript issues, but only rendered-card defects are in
        # scope here.  Keep every EDITOR_FORMAT/REFERENCE blocker fail-closed.
        scoped_blockers = [
            issue for issue in (qa.get('blocking_issues') or [])
            if not str(issue).startswith('CONTENT_PASS=false')
        ]
        qa['blocking_issues'] = scoped_blockers
        qa['pass'] = _visual_qa_passed(qa) and not scoped_blockers
        retry_usages: list[dict[str, Any]] = []
        revision_notes_by_card: dict[int, list[str]] = {}
        for attempt in range(1, 3):
            if qa.get('pass') is True:
                break
            notes_by_card: dict[int, list[str]] = {}
            for issue in scoped_blockers:
                for card_number in _card_numbers_from_qa_issue(issue):
                    notes_by_card.setdefault(card_number, []).append(str(issue))
                    prior_notes = revision_notes_by_card.setdefault(card_number, [])
                    if str(issue) not in prior_notes:
                        prior_notes.append(str(issue))
            if not notes_by_card:
                break
            cards = generate_and_typeset_blog_cards(
                self.ai.client,
                list(generated.get('card_news') or []),
                out_dir,
                model=self.s.image_model,
                quality=self.s.image_quality,
                font_path=self.s.card_font_path,
                budget=self.budget,
                estimated_cost_usd=self.s.openai_image_estimated_cost_usd,
                only_indices=set(notes_by_card),
                revision_notes={
                    card_number: ' '.join(revision_notes_by_card[card_number])
                    for card_number in notes_by_card
                },
                design_language=str(generated.get('design_language') or 'Bento Editorial'),
                design_blueprint=generated.get('design_blueprint'),
                subject_hint=str(generated.get('title') or ''),
            )
            qa, retry_usage = self.ai.qa(generated, {
                'channel': 'naver_blog',
                'reference_baseline': baseline,
                'automation_scope': {
                    'mode': 'rerender_existing_cards', 'stage': 'rendered_cards',
                    'article_body_frozen': True, 'visual_mode': 'photographic_lifestyle',
                    'selective_retry_attempt': attempt,
                },
            }, qa_prompt='prompts/qa_photographic_blog_cards.md', image_paths=cards)
            retry_usages.append({'attempt': attempt, 'usage': retry_usage, 'cards': sorted(notes_by_card)})
            scoped_blockers = [
                issue for issue in (qa.get('blocking_issues') or [])
                if not str(issue).startswith('CONTENT_PASS=false')
            ]
            qa['blocking_issues'] = scoped_blockers
            qa['pass'] = _visual_qa_passed(qa) and not scoped_blockers
        if retry_usages:
            usage = {'initial': usage, 'selective_retries': retry_usages}
        if qa.get('pass') is not True:
            raise RuntimeError('RERENDERED_CARD_QA_FAILED: ' + json.dumps(qa, ensure_ascii=False))
        upload_ids = self.notion.attach_files(page_id, '생성 이미지', cards)
        if len(upload_ids) != 5:
            raise RuntimeError('NOTION_CARD_REPLACEMENT_INCOMPLETE')
        # Card-only QA cannot certify the frozen article or create the handoff marker.
        # Keep the page in the revision queue until full manuscript/card QA passes.
        self.notion.update_status(page_id, '수정 필요')
        image_blocks = []
        for index, (upload_id, card) in enumerate(zip(upload_ids, cards), 1):
            image_blocks.extend([
                {'object': 'block', 'type': 'heading_3', 'heading_3': {'rich_text': [
                    {'type': 'text', 'text': {'content': f'카드 {index:02d} · {card.name}'}}]}},
                {'object': 'block', 'type': 'image', 'image': {
                    'type': 'file_upload', 'file_upload': {'id': upload_id},
                }},
            ])
        self.notion.append_blocks(page_id, [
            {'object': 'block', 'type': 'heading_2', 'heading_2': {'rich_text': [
                {'type': 'text', 'text': {'content': '카드뉴스 실제 첨부 파일 · 고정 순서'}}]}},
            *image_blocks,
            {'object': 'block', 'type': 'heading_2', 'heading_2': {'rich_text': [
                {'type': 'text', 'text': {'content': '카드뉴스 최신 재제작본 · visual-first QA PASS'}}]}},
            {'object': 'block', 'type': 'paragraph', 'paragraph': {'rich_text': [
                {'type': 'text', 'text': {'content': '기존 원고는 변경하지 않았습니다. 생성 이미지 속성의 5장이 현재 정본이며 네이버 저장·공개·예약발행은 실행하지 않았습니다.'}}]}},
        ])
        return {
            'channel': 'naver_blog', 'page_id': page_id, 'title': extract_page_title(page),
            'status': 'cards_replaced', 'qa_pass': True, 'notion_page_updated': True,
            'output_verified': True, 'budget_blocked': False,
            'media': {'cards': [str(card) for card in cards], 'notion_cards_attached': True},
            'naver_draft_verified': False, 'card_count': len(cards),
            'article_body_sha256': original_body_hash, 'qa': qa, 'usage': usage,
            'previous_status': original_status, 'new_status': '수정 필요',
        }

    def run_channel(self, cfg: ChannelConfig, limit: int | None = None, dry_run: bool = False) -> list[dict[str, Any]]:
        ds = self.s.blog_data_source_id if cfg.source == 'blog' else self.s.shorts_data_source_id
        pages = self.notion.query_ready(
            ds,
            cfg.ready_status,
            cfg.notion_channel_value,
            # Read a bounded candidate window so a newly added manual-priority
            # item can jump ahead even when the normal daily limit is smaller.
            page_size=100,
            excluded_formula_property=cfg.excluded_formula_property,
            excluded_formula_value=cfg.excluded_formula_value,
            required_select_values=cfg.required_select_values,
            required_number_greater_than=cfg.required_number_greater_than,
            sort_property=cfg.sort_property,
        )
        # A user may add a sudden topic at any time. A page explicitly marked
        # as direct/user input jumps ahead of the automated queue while the
        # relative order of all normal candidates remains unchanged.
        pages = sorted(pages, key=lambda page: 0 if page_is_manual_priority(page) else 1)
        results = []
        for page in pages[: limit or self.s.max_jobs_per_run]:
            results.append(self.process_page(cfg, page, dry_run=dry_run))
        return results

    def process_page(
        self, cfg: ChannelConfig, page_stub: dict[str, Any], dry_run: bool = False,
        resume_manifest: Path | None = None, reviewed_manifest: Path | None = None,
        regenerate_failed_blog_cards: bool = False,
    ) -> dict[str, Any]:
        # Read/claim failures belong to this item, not to every remaining item.
        try:
            result = self._process_page(
                cfg, page_stub, dry_run=dry_run,
                resume_manifest=resume_manifest, reviewed_manifest=reviewed_manifest,
                regenerate_failed_blog_cards=regenerate_failed_blog_cards,
            )
        except Exception as exc:
            result = {'page_id': page_stub.get('id'), 'status': 'failed', 'error': repr(exc)}
        return {'channel': cfg.name, **result}

    def _process_page(
        self, cfg: ChannelConfig, page_stub: dict[str, Any], dry_run: bool = False,
        resume_manifest: Path | None = None, reviewed_manifest: Path | None = None,
        regenerate_failed_blog_cards: bool = False,
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
        if regenerate_failed_blog_cards and (resume_manifest is None or reviewed_manifest is not None or dry_run):
            raise ValueError('Failed-card recovery requires exactly one source manifest and a production run')
        if resume_manifest is not None:
            previous = json.loads(resume_manifest.read_text(encoding='utf-8'))
            if previous.get('page_id') != page_id or previous.get('channel') != 'naver_blog' or cfg.name != 'naver_blog':
                raise ValueError('Resume manifest does not match the exact blog page')
            if not previous.get('generated') or not previous.get('qa'):
                raise ValueError('Resume manifest is missing the original manuscript or review')
            if regenerate_failed_blog_cards:
                if previous.get('manuscript_sha256') != manuscript_hash(previous['generated']):
                    raise RuntimeError('SOURCE_MANUSCRIPT_HASH_MISMATCH')
                self._check_unattached_failed_blog(page_id, previous, cfg.revision_status)
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
            if not regenerate_failed_blog_cards:
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
            if regenerate_failed_blog_cards:
                if len(expected_names) != 5 or prior_files:
                    raise RuntimeError('MANUAL_EDIT_CONFLICT: failed-card source must have five artifact cards and no attached cards')
            elif [f.get('name') for f in prior_files] != expected_names:
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
        media: dict[str, Any] = {}
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
                    # The approved production contract is visual-first: generate a
                    # topic-specific text-free scene, then typeset Korean locally.
                    # Do not let the model route new daily work back to the legacy
                    # icon/diagram renderer by returning an older visual family.
                    generated['visual_family'] = 'photographic_lifestyle'
                    generated['design_language'] = production_design_language(
                        generated.get('design_language')
                    )
                    generated = apply_named_design_contract(generated)
            if cfg.name == 'japan_shorts':
                generated = align_single_speaker_profile(generated)
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
            passed = (_shorts_script_qa_passed(qa) if cfg.content_kind == 'shorts'
                      else _visual_qa_passed(qa))
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

            if cfg.content_kind == 'blog':
                card_news = generated.get('card_news') or []
                if len(card_news) != 5:
                    raise RuntimeError('Blog output did not contain exactly five card-news items')
                if previous and source_cards and not regenerate_failed_blog_cards:
                    # Resume means re-reviewing the exact approved artifact, not
                    # paying for five new stochastic images and then comparing
                    # those new bytes with the old set (which can never match).
                    card_dir = job_dir / 'cards'
                    card_dir.mkdir(parents=True, exist_ok=True)
                    cards = []
                    for source in source_cards:
                        destination = card_dir / source.name
                        if source.resolve() != destination.resolve():
                            shutil.copy2(source, destination)
                        cards.append(destination)
                else:
                    cards = generate_and_typeset_blog_cards(
                        self.ai.client,
                        card_news,
                        job_dir / 'cards',
                        model=self.s.image_model,
                        quality=self.s.image_quality,
                        font_path=self.s.card_font_path,
                        budget=self.budget,
                        estimated_cost_usd=self.s.openai_image_estimated_cost_usd,
                        design_language=str(generated.get('design_language') or 'Bento Editorial'),
                        design_blueprint=generated.get('design_blueprint'),
                        subject_hint=str(generated.get('title') or ''),
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
                    }, qa_prompt='prompts/qa_photographic_blog_cards.md', image_paths=cards)
                    retry_usages = []
                    if not previous or regenerate_failed_blog_cards:
                        for attempt in range(1, 3):
                            if _visual_qa_passed(qa):
                                break
                            retry_cards = set()
                            revision_notes: dict[int, list[str]] = {}
                            for issue in qa.get('blocking_issues') or []:
                                message = (
                                    json.dumps(issue, ensure_ascii=False)
                                    if isinstance(issue, dict) else str(issue)
                                )
                                for card_number in _card_numbers_from_qa_issue(issue):
                                    retry_cards.add(card_number)
                                    revision_notes.setdefault(card_number, []).append(message)
                            for value in qa.get('cards_to_regenerate') or []:
                                if str(value).isdigit() and 1 <= int(value) <= 5:
                                    retry_cards.add(int(value))
                            if not retry_cards:
                                retry_cards = {1, 2, 3, 4, 5}
                            cards = generate_and_typeset_blog_cards(
                                self.ai.client,
                                card_news,
                                job_dir / 'cards',
                                model=self.s.image_model,
                                quality=self.s.image_quality,
                                font_path=self.s.card_font_path,
                                budget=self.budget,
                                estimated_cost_usd=self.s.openai_image_estimated_cost_usd,
                                only_indices=retry_cards,
                                revision_notes={
                                    number: ' '.join(revision_notes.get(number) or [
                                        'Remove synthetic polish and repeated template rhythm. '
                                        'Use an ordinary lived-in documentary scene.'
                                    ])
                                    for number in retry_cards
                                },
                                design_language=str(generated.get('design_language') or 'Bento Editorial'),
                                design_blueprint=generated.get('design_blueprint'),
                                subject_hint=str(generated.get('title') or ''),
                            )
                            qa, retry_usage = self.ai.qa(generated, {
                                'channel': cfg.name, **context,
                                'automation_scope': {
                                    **context['automation_scope'],
                                    'mode': 'blog_cards',
                                    'stage': 'failed_source_visual_retry' if regenerate_failed_blog_cards else 'targeted_visual_retry',
                                    'selective_retry_attempt': attempt,
                                    'preserved_cards': sorted(set(range(1, 6)) - retry_cards),
                                },
                            }, qa_prompt='prompts/qa_photographic_blog_cards.md', image_paths=cards)
                            retry_usages.append({
                                'attempt': attempt,
                                'cards': sorted(retry_cards),
                                'usage': retry_usage,
                            })
                    if retry_usages:
                        image_qa_usage = {
                            'initial': image_qa_usage,
                            'selective_retries': retry_usages,
                        }
                passed = _visual_qa_passed(qa)
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
                            if regenerate_failed_blog_cards:
                                self._check_unattached_failed_blog(page_id, previous, cfg.revision_status,
                                                                   processing_status=cfg.processing_status)
                            else:
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
                        video_path = Path(media['video'])
                        if not self.notion.can_upload_file(video_path):
                            media['notion_video_attached'] = False
                            media['notion_video_skipped_reason'] = (
                                'notion_free_workspace_20mb_limit; youtube_private_url_saved_instead'
                            )
                        else:
                            try:
                                self.notion.attach_files(page_id, '최종 영상', [video_path])
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
                    prior_blocks = ([] if regenerate_failed_blog_cards else
                                    self._match_prior_blog_blocks(page_id, previous))
                    # Only replace a byte-equivalent previous automation section.
                    # If a person edited the page, stop instead of overwriting it.
                    if prior_blocks:
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
            elif cfg.content_kind == 'shorts' and passed:
                observation = self._verify_shorts_output(
                    page_id, page_title, final_status, blocks, media,
                    require_media=media_expected, require_youtube=bool(media_expected and self.s.auto_private_youtube_upload),
                    channel_name=cfg.name,
                )
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
                # A private upload cannot safely be undone or regenerated. Keep
                # its current Notion state for explicit reconciliation.
                if not (cfg.content_kind == 'shorts' and media.get('youtube_url')):
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

    def _verify_shorts_output(
        self, page_id: str, page_title: str, status: str,
        expected_blocks: list[dict[str, Any]], media: dict[str, Any],
        require_media: bool = False, require_youtube: bool = False, channel_name: str = '',
    ) -> dict[str, Any]:
        page = self.notion.retrieve_page(page_id)
        observed_blocks = self.notion.read_page_blocks(page_id)
        expected = [block_signature(block) for block in expected_blocks]
        observed = [block_signature(block) for block in observed_blocks[-len(expected):]] if expected else []
        props = page.get('properties') or {}
        if extract_page_title(page) != page_title:
            raise RuntimeError('Shorts read-back title changed during production')
        if compact_page_context(page, '').get('properties', {}).get('상태') != status:
            raise RuntimeError('Shorts read-back status did not match the saved result')
        if not expected or observed != expected:
            raise RuntimeError('Shorts read-back script blocks did not match generated result')
        if require_media and not media.get('video'):
            raise RuntimeError('Shorts video required but missing')
        if require_youtube and (not media.get('youtube_url') or media.get('youtube_privacy') not in {'private', 'public'}):
            raise RuntimeError('Shorts reviewed YouTube upload required but missing')
        if media.get('video'):
            if (media.get('verification') or {}).get('pass') is not True:
                raise RuntimeError('Shorts media verification did not pass')
            if media.get('notion_video_attached') is True:
                files = (props.get('최종 영상') or {}).get('files') or []
                if not any(f.get('name') == Path(media['video']).name for f in files):
                    raise RuntimeError('Shorts read-back video attachment was not found')
            if media.get('youtube_url'):
                saved_url = (props.get('YouTube 비공개 주소') or {}).get('url')
                if saved_url != media['youtube_url']:
                    raise RuntimeError('Shorts read-back YouTube URL did not match')
                if require_youtube:
                    token_file = {
                        'ppojjugi_shorts': self.s.youtube_ppojjugi_token_file,
                        'japan_shorts': self.s.youtube_japan_token_file,
                    }.get(channel_name)
                    if token_file is None:
                        raise RuntimeError('Shorts YouTube channel is not configured for read-back')
                    uploader = YouTubePrivateUploader(self.s.youtube_client_secrets_file, token_file)
                    if not uploader.verify_uploaded(media['youtube_url'], media['youtube_privacy']):
                        raise RuntimeError('Shorts YouTube video/privacy read-back did not match')
        return {'page_id': page_id, 'title': page_title, 'status': status,
                'body_blocks_verified': len(expected),
                'video_attachment_verified': media.get('notion_video_attached') is True,
                'youtube_url_verified': bool(media.get('youtube_url')),
                'youtube_video_verified': bool(require_youtube and media.get('youtube_url')),
                'verified_at': datetime.now(timezone.utc).isoformat()}

    def _check_unattached_failed_blog(
        self, page_id: str, previous: dict[str, Any], revision_status: str,
        *, processing_status: str | None = None,
    ) -> None:
        """Fail closed unless the rejected artifact has left this exact page untouched."""
        media = previous.get('media') or {}
        if (previous.get('qa', {}).get('pass') is not False
                or media.get('rendered_card_qa_pass') is not False
                or media.get('notion_cards_attached') is not False
                or previous.get('output_verified') is not False):
            raise RuntimeError('FAILED_BLOG_ARTIFACT_NOT_UNATTACHED')
        page = self.notion.retrieve_page(page_id)
        props = page.get('properties') or {}
        status = property_value(props.get('상태') or {})
        allowed_statuses = {revision_status}
        if processing_status:
            allowed_statuses.add(processing_status)
        if (status not in allowed_statuses
                or extract_page_title(page) != previous['generated'].get('title')
                or property_value(props.get('네이버 임시저장 주소') or {})
                or (props.get('생성 이미지') or {}).get('files')
                or self.notion.read_page_blocks(page_id)):
            raise RuntimeError('MANUAL_EDIT_CONFLICT: failed blog page changed or contains existing output')

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

    def _make_short_media(
        self, generated: dict[str, Any], job_dir: Path, channel_style: str,
        existing_images: list[Path] | None = None,
    ) -> dict[str, Any]:
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
        approved_voices: dict[str, str] = {}
        japan_delivery: dict[str, str] = {}
        if channel_style == 'japan_shorts':
            profile_key = str(narrator_profile.get('profile') or 'older_woman')
            approved_voices = {
                'older_woman': self.s.tts_japan_voice,
                'young_woman': self.s.tts_japan_young_woman_voice,
                'young_man': self.s.tts_japan_young_man_voice,
                'older_man': self.s.tts_japan_older_man_voice,
            }
            if profile_key not in {*approved_voices, 'multiple'}:
                raise ValueError(f'Unknown Japanese narrator profile: {profile_key}')
            voice = approved_voices.get(profile_key) or self.s.tts_japan_voice
            japan_delivery = {
                'young_woman': 'Japanese woman in her 20s. Speak naturally and intimately, with youthful clarity, real conversational breath, and restrained emotion.',
                'young_man': 'Japanese man in his 20s. Speak naturally and gently, with an unforced conversational rhythm, subtle hesitation, and sincere emotion.',
                'older_woman': 'Japanese woman in her 70s recalling an old memory. Speak slowly and warmly, with quiet nostalgia, small breaths, and softly falling sentence endings.',
                'older_man': 'Japanese man in his 70s recalling an old memory. Use a low, warm, lived-in tone, measured pauses, restrained sadness, and quiet acceptance.',
            }
        # Keep one provider throughout, while Japanese dialogue may route each
        # scene to its age/gender-matched character voice.
        production_tts_model = self.s.tts_model
        production_voice = voice
        media = MediaGenerator(
            self.s.openai_api_key,
            self.s.image_model,
            production_tts_model,
            production_voice,
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
        if existing_images is None:
            images = media.generate_scene_images(scenes, job_dir / 'scenes', channel_style)
        else:
            images = list(existing_images)
            if len(images) != len(scenes) or any(not path.exists() for path in images):
                raise RuntimeError('Preserved image set does not match the Shorts scenes')
        total_characters = max(sum(len(text) for text in scene_narrations), 1)
        audio_parts: list[Path] = []
        voices_used: list[str] = []
        for index, text in enumerate(scene_narrations, 1):
            share = self.s.openai_tts_estimated_cost_usd * len(text) / total_characters
            scene_instructions = narration_instructions
            if channel_style == 'japan_shorts':
                scene_profile = str(scenes[index - 1].get('speaker_profile') or profile_key)
                if scene_profile not in approved_voices:
                    raise RuntimeError(
                        f'Japanese scene {index} has no valid speaker_profile; '
                        'expected young_woman, young_man, older_woman, or older_man'
                    )
                scene_voice = approved_voices[scene_profile]
                if not scene_voice:
                    raise RuntimeError(f'Japanese voice profile {scene_profile} is not configured')
                media.voice = scene_voice
                scene_instructions = (
                    japan_delivery[scene_profile]
                    + ' Preserve the character as a real person, not an anime or announcer performance. '
                    + 'Follow the emotional meaning of this exact line without exaggeration.'
                )
                voices_used.append(f'{scene_profile}:{scene_voice}')
            else:
                voices_used.append(production_voice)
            audio_parts.append(media.generate_tts(
                text,
                job_dir / 'narration_scenes' / f'{index:02d}.mp3',
                estimated_cost_usd=share,
                instructions=scene_instructions,
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
            'tts_voice': production_voice,
            'tts_voices_used': list(dict.fromkeys(voices_used)),
            'tts_provider': 'fal' if production_tts_model.startswith('fal-ai/') else 'openai',
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



def page_is_manual_priority(page: dict[str, Any]) -> bool:
    properties = page.get('properties') or {}
    values = {
        name: property_value(prop)
        for name, prop in properties.items()
        if name in {'키워드 출처', '주제 출처', '다음 행동', '수동 우선'}
    }
    if values.get('수동 우선') is True:
        return True
    text = ' '.join(str(x or '') for x in values.values()).lower()
    return any(marker in text for marker in ('직접 입력', '사용자 추가', '사용자 직접', '수동 우선'))
