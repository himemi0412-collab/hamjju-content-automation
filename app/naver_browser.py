"""Browser adapter for saving an already-reviewed Naver draft.

Content generation, assets, and QA stay outside this module. Another browser
provider can implement ``DraftSaveAdapter`` without changing the content queue.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol


class DraftVerificationFailed(RuntimeError):
    pass


FORBIDDEN = ('발행', '예약발행', '공개')


class DraftSaveAdapter(Protocol):
    def save_and_verify(self, blog_id: str, title: str, body: str, cards: list[Path]) -> str: ...


def validate_draft_readback(
    listed_titles: list[str], reopened_title: str, reopened_body: str,
    expected_title: str, expected_body: str, image_count: int,
) -> None:
    if expected_title not in listed_titles:
        raise DraftVerificationFailed('Naver temporary-draft list did not contain the exact title')
    if reopened_title.strip() != expected_title:
        raise DraftVerificationFailed('Naver draft title did not match after reopening the list item')
    body_text = _canonical_editor_body(reopened_body, expected_markdown=expected_body)
    expected_text = _canonical_editor_body(expected_body)
    if not expected_text or expected_text != body_text:
        raise DraftVerificationFailed('Naver draft body did not match after reopening the list item')
    if image_count != 5:
        raise DraftVerificationFailed('Naver draft did not contain exactly five reviewed cards')


def _canonical_editor_body(value: str, *, expected_markdown: str | None = None) -> str:
    """Compare text while accounting for Markdown list markers rendered as editor lists."""
    if expected_markdown is not None:
        # SmartEditor exposes some Markdown bullets inline (e.g. ``pdf- 확인일``)
        # even though the source had a line break and ``- `` list marker. Remove
        # only a marker immediately before the exact QA-approved list item.
        for source_line in expected_markdown.splitlines():
            match = re.match(r'^\s*[-*]\s+(.+?)\s*$', source_line)
            if match:
                item_text = match.group(1)
                value = value.replace(f'- {item_text}', f' {item_text}', 1)
    lines = []
    for line in value.splitlines():
        line = re.sub(r'^\s*(?:\d+\.\s+|[-*]\s+)', '', line)
        if line.strip():
            lines.append(line.strip())
    return ' '.join(' '.join(lines).split())


class PlaywrightNaverAdapter:
    def __init__(self, page):
        self.page = page

    @staticmethod
    def _step(name: str, action):
        """Keep browser failures actionable without exposing page or account data."""
        try:
            return action()
        except DraftVerificationFailed:
            raise
        except Exception as exc:
            # Only retain a safe, structured timeout summary. Raw exception
            # messages can contain signed URLs or editor content.
            timeout = re.search(r'Timeout\s+\d+ms exceeded', str(exc))
            detail = f': {timeout.group(0)}' if timeout else ''
            raise DraftVerificationFailed(
                f'Naver browser step {name} failed ({type(exc).__name__}){detail}'
            ) from exc

    def _challenge_visible(self) -> bool:
        text = self.page.locator('body').inner_text(timeout=3000)
        return ('nid.naver.com' in self.page.url or
                any(x in text for x in ('보안 확인', '캡차', '로그인이 필요', '인증이 필요')))

    def _first_visible(self, selectors: list[str]):
        for selector in selectors:
            locator = self.page.locator(selector).first
            try:
                if locator.is_visible(timeout=1200):
                    return locator
            except Exception:
                pass
        raise DraftVerificationFailed('Naver editor controls changed; manual review is required')

    def _draft_count_control(self, save_control):
        for selector in (
            '[aria-label*="임시 저장"]', '[aria-label*="임시저장"]',
            '[title*="임시 저장"]', '[title*="임시저장"]',
        ):
            candidate = self.page.locator(selector).first
            try:
                if candidate.is_visible(timeout=500):
                    return candidate
            except Exception:
                pass
        for depth in (1, 2, 3):
            parent = save_control.locator('xpath=' + '/..' * depth)
            numbers = parent.get_by_text(re.compile(r'^\s*\d+\s*$'))
            if numbers.count() == 1:
                return numbers.first
        raise DraftVerificationFailed('Naver temporary-draft list control was not found')

    def save_and_verify(self, blog_id: str, title: str, body: str, cards: list[Path]) -> str:
        if len(cards) != 5:
            raise DraftVerificationFailed('Exactly five reviewed cards are required')
        page = self.page
        self._step('editor_navigation', lambda: page.goto(
            f'https://blog.naver.com/PostWriteForm.naver?blogId={blog_id}', wait_until='domcontentloaded'
        ))
        self._step('editor_load_wait', lambda: page.wait_for_timeout(2500))
        if self._challenge_visible():
            raise DraftVerificationFailed('Naver security check or sign-in is required')
        recovery_notice = page.get_by_text('작성 중인 글이 있습니다', exact=False).first
        if recovery_notice.is_visible(timeout=800):
            # A previous interrupted attempt can leave Naver's recoverable
            # autosave prompt. Resume only to compare its content to this exact
            # QA handoff; never overwrite a different in-progress draft.
            self._step('resume_autosave', lambda: page.get_by_role(
                'button', name='확인', exact=True,
            ).click())
            self._step('resume_autosave_wait', lambda: page.wait_for_timeout(1000))
        # SmartEditor ONE renders paragraph nodes that are not themselves
        # contenteditable. Clicking them focuses its hidden input-buffer iframe;
        # locator.fill() on those <p> nodes fails before any save can occur.
        title_box = self._first_visible(['.se-title-text'])
        body_box = self._first_visible(['.se-section-text .se-text-paragraph'])
        def enter_content():
            title_empty = title_box.evaluate(
                '(el) => el.classList.contains("se-is-empty")'
            )
            body_empty = body_box.evaluate(
                '(el) => Boolean(el.closest(".se-module")?.classList.contains("se-is-empty"))'
            )
            actual_title = title_box.inner_text().strip()
            actual_body = ' '.join(
                value.strip()
                for value in page.locator('.se-section-text').all_inner_texts()
                if value.strip()
            )
            if title_empty and body_empty:
                title_box.click()
                page.keyboard.insert_text(title)
                body_box.click()
                # Typing a full multi-paragraph string through insertText loses
                # most of the body in SmartEditor. Keyboard input preserves each
                # paragraph; insert the warning symbol as one text event.
                for index, part in enumerate(body.split('⚠️')):
                    if part:
                        page.keyboard.type(part, delay=0)
                    if index < body.count('⚠️'):
                        page.keyboard.insert_text('⚠️')
                actual_title = title_box.inner_text().strip()
                actual_body = ' '.join(
                    value.strip()
                    for value in page.locator('.se-section-text').all_inner_texts()
                    if value.strip()
                )
            elif (
                actual_title != title
                or _canonical_editor_body(actual_body, expected_markdown=body)
                != _canonical_editor_body(body)
            ):
                raise DraftVerificationFailed(
                    'Recovered Naver autosave did not match the QA-approved title and body'
                )
            if actual_title != title:
                raise DraftVerificationFailed('Naver editor title did not match before saving')
            if _canonical_editor_body(actual_body, expected_markdown=body) != _canonical_editor_body(body):
                raise DraftVerificationFailed('Naver editor body did not match before saving')
        self._step('title_body_input', enter_content)
        existing_images = page.locator('.se-component.se-image').count()
        if existing_images:
            raise DraftVerificationFailed(
                'Naver editor already contained unverified images; refusing to duplicate or reuse them'
            )
        photo = page.get_by_role('button', name='사진 추가', exact=True).first
        if not photo.is_visible(timeout=2000):
            raise DraftVerificationFailed('Naver card attachment control was not found')
        def attach_from_chooser():
            with page.expect_file_chooser() as chooser:
                photo.click()
            chooser.value.set_files([str(path.resolve()) for path in cards])
            # Naver asks how to arrange selected images after the file chooser.
            individual = page.get_by_text('개별사진', exact=True).first
            if not individual.is_visible(timeout=5000):
                raise DraftVerificationFailed('Naver individual-photo layout option was not found after file selection')
            individual.click()
        self._step('card_attachment', attach_from_chooser)
        self._step('card_upload_wait', lambda: page.wait_for_timeout(5000))
        image_components = page.locator('.se-component.se-image')
        # Image uploads can complete asynchronously; wait up to a minute
        # before failing closed rather than attempting Save while uploading.
        for _ in range(120):
            attached_count = self._step('card_upload_readback', image_components.count)
            if attached_count >= 5:
                break
            page.wait_for_timeout(500)
        if attached_count != 5:
            raise DraftVerificationFailed(
                f'Naver editor did not contain exactly five attached cards (found {attached_count})'
            )

        # Naver's documented editor action is "저장"; it saves a draft and does
        # not publish. No publication or reservation control is used here.
        # Resolve the actual editor button by role. A text locator can bind to
        # a nested/hidden copy of the label and then time out while the real
        # button is visible and enabled.
        save = page.get_by_role('button', name='저장', exact=True).first
        if not save.is_visible(timeout=3000):
            raise DraftVerificationFailed("Exact draft '저장' action was not found")
        if save.inner_text().strip() in FORBIDDEN:
            raise DraftVerificationFailed('Publication action blocked')
        count_control = self._draft_count_control(save)
        self._step('draft_save', save.click)
        self._step('draft_save_wait', lambda: page.wait_for_timeout(2500))
        self._step('draft_list_open', count_control.click)
        self._step('draft_list_wait', lambda: page.wait_for_timeout(1200))

        # SmartEditor exposes this drawer as an accessible region (not a
        # dialog), even though it visually overlays the editor.
        draft_list = page.get_by_role('region', name='임시저장 글 보기').last
        if not self._step('draft_list_visibility', lambda: draft_list.is_visible(timeout=3000)):
            raise DraftVerificationFailed('Naver temporary-draft list did not open')
        titles = self._step('draft_list_title_lookup', lambda: draft_list.get_by_text(title, exact=True))
        if self._step('draft_list_title_count', titles.count) != 1:
            raise DraftVerificationFailed('Naver temporary-draft list did not show one exact title')
        listed_titles = [self._step('draft_list_title_read', titles.first.inner_text).strip()]
        self._step('draft_reopen', titles.first.click)
        self._step('draft_reopen_wait', lambda: page.wait_for_timeout(1800))
        if self._challenge_visible():
            raise DraftVerificationFailed('Naver security check appeared during draft readback')
        reopened_title = self._step('reopened_title_read', lambda: self._first_visible([
            '[contenteditable=true][data-placeholder*="제목"]', '.se-title-text p', '.se-title-text',
        ]).inner_text().strip())
        reopened_body = self._step('reopened_body_read', lambda: self._first_visible([
            '.se-section-text',
            '[contenteditable=true][data-placeholder*="본문"]',
        ]).inner_text())
        image_count = self._step(
            'reopened_image_count', lambda: page.locator('.se-component.se-image').count()
        )
        validate_draft_readback(listed_titles, reopened_title, reopened_body, title, body, image_count)
        return page.url
