from pathlib import Path

import pytest

from app.naver_browser import PlaywrightNaverAdapter, validate_draft_readback


class Locator:
    def __init__(self, page, selector, text=''):
        self.page = page
        self.selector = selector
        self.text = text
        self.first = self
        self.last = self

    def is_visible(self, **_kwargs):
        if self.selector == 'recovery':
            return self.page.recovering
        if self.selector == 'photo-mode':
            return self.page.photo_mode_visible
        return True

    def inner_text(self, **_kwargs):
        if self.selector == 'body':
            return ''
        if self.selector == 'save':
            return '저장'
        if 'ancestor::div[contains(@class' in self.selector:
            return self.page.body
        if '제목' in self.selector or 'se-title-text' in self.selector:
            return self.page.title
        if '본문' in self.selector or 'se-section-text' in self.selector or 'se-text-paragraph' in self.selector:
            return self.page.body
        return self.text

    def all_inner_texts(self):
        return [self.page.body]

    def click(self, **_kwargs):
        self.page.active_selector = self.selector
        self.page.actions.append(('click', self.selector))
        if self.selector == 'draft-title':
            self.page.editor_image_count = self.page.image_count

    def evaluate(self, _expression):
        return self.page.editor_empty

    def fill(self, value):
        self.text = value
        if '제목' in self.selector or 'se-title-text' in self.selector:
            self.page.title = value
        if '본문' in self.selector or 'se-section-text' in self.selector or 'se-text-paragraph' in self.selector:
            self.page.body = value
        self.page.actions.append(('fill', self.selector, value))

    def count(self):
        if self.selector == 'input[type=file]':
            return 1
        if self.selector == '.se-component.se-image':
            return self.page.editor_image_count
        if self.selector == 'draft-title':
            return 1
        if self.selector.startswith('[aria-label*="임시'):
            return 1
        return 0

    def set_input_files(self, files):
        if self.page.fail_at == 'card_attachment':
            raise RuntimeError('private signed URL must not appear in diagnostics')
        self.page.actions.append(('files', tuple(files)))
        self.page.editor_image_count = 5

    def locator(self, selector):
        return Locator(self.page, selector)

    def get_by_text(self, text, exact=False):
        if exact:
            return Locator(self.page, 'draft-title', text)
        return Locator(self.page, 'text', text)


class Page:
    def __init__(self, image_count=5, fail_at='', editor_empty=True, recovering=False):
        self.url = 'https://blog.naver.com/PostWriteForm.naver'
        self.image_count = image_count
        self.editor_image_count = 0
        self.fail_at = fail_at
        self.actions = []
        self.title = ''
        self.body = ''
        self.editor_empty = editor_empty
        self.recovering = recovering
        self.photo_mode_visible = False
        self.active_selector = ''
        self.keyboard = Keyboard(self)

    def goto(self, url, **_kwargs):
        if self.fail_at == 'editor_navigation':
            raise RuntimeError('private signed URL must not appear in diagnostics')
        self.url = url
        self.actions.append(('goto', url))

    def wait_for_timeout(self, _milliseconds):
        pass

    def locator(self, selector):
        return Locator(self, selector)

    def get_by_text(self, text, exact=False):
        if text == '작성 중인 글이 있습니다':
            return Locator(self, 'recovery', text)
        if text == '개별사진':
            return Locator(self, 'photo-mode', text)
        return Locator(self, 'save' if exact and text == '저장' else 'text', text)

    def get_by_role(self, role, name=None, exact=False):
        selector = 'save' if role == 'button' and name == '저장' and exact else role
        if role == 'button' and name == '사진 추가' and exact:
            selector = 'photo'
        return Locator(self, selector, name or '')

    def expect_file_chooser(self):
        return FileChooserContext(self)


class Keyboard:
    def __init__(self, page):
        self.page = page

    def insert_text(self, value):
        if 'se-title-text' in self.page.active_selector:
            self.page.title = value
        elif 'se-section-text' in self.page.active_selector:
            self.page.body = value
        self.page.actions.append(('insert_text', self.page.active_selector, value))

    def type(self, value, delay=0):
        if 'se-section-text' in self.page.active_selector:
            self.page.body = value
        self.page.actions.append(('type', value))


class FileChooserContext:
    def __init__(self, page):
        self.page = page
        self.value = self

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def set_files(self, files):
        if self.page.fail_at == 'card_attachment':
            raise RuntimeError('private signed URL must not appear in diagnostics')
        self.page.actions.append(('files', tuple(files)))
        self.page.photo_mode_visible = True
        self.page.editor_image_count = 5


def test_browser_adapter_saves_then_reopens_and_checks_all_five_cards():
    page = Page()
    cards = [Path(f'card-{index}.png') for index in range(1, 6)]

    url = PlaywrightNaverAdapter(page).save_and_verify(
        'himemi0412', '확인 제목', '검증한 전체 본문', cards,
    )

    assert url == page.url
    assert any(action[0] == 'files' and len(action[1]) == 5 for action in page.actions)
    assert ('click', 'photo-mode') in page.actions
    assert [action[1] for action in page.actions if action[0] == 'insert_text'] == [
        '.se-title-text',
    ]
    assert ('type', '검증한 전체 본문') in page.actions
    clicked = [action[1] for action in page.actions if action[0] == 'click']
    assert 'save' in clicked
    assert '[aria-label*="임시 저장"]' in clicked
    assert 'draft-title' in clicked
    assert not any(any(word in selector for word in ('발행', '예약')) for selector in clicked)


def test_browser_adapter_fails_closed_when_reopened_draft_has_fewer_than_five_images():
    page = Page(image_count=4)
    cards = [Path(f'card-{index}.png') for index in range(1, 6)]

    try:
        PlaywrightNaverAdapter(page).save_and_verify(
            'himemi0412', '확인 제목', '검증한 전체 본문', cards,
        )
    except RuntimeError as exc:
        assert 'five' in str(exc)
    else:
        raise AssertionError('adapter accepted fewer than five reopened images')


@pytest.mark.parametrize('failed_step', ['editor_navigation', 'card_attachment'])
def test_browser_adapter_reports_safe_failure_step_without_exception_details(failed_step):
    page = Page(fail_at=failed_step)
    cards = [Path(f'card-{index}.png') for index in range(1, 6)]

    with pytest.raises(RuntimeError, match=failed_step) as exc:
        PlaywrightNaverAdapter(page).save_and_verify(
            'himemi0412', '확인 제목', '검증한 전체 본문', cards,
        )

    assert 'private signed URL' not in str(exc.value)


def test_browser_adapter_refuses_nonempty_editor_before_save():
    page = Page(editor_empty=False)
    cards = [Path(f'card-{index}.png') for index in range(1, 6)]

    with pytest.raises(RuntimeError, match='did not match the QA-approved'):
        PlaywrightNaverAdapter(page).save_and_verify(
            'himemi0412', '확인 제목', '검증한 전체 본문', cards,
        )

    assert not any(action[0] == 'files' for action in page.actions)
    assert not any(action == ('click', 'save') for action in page.actions)


def test_browser_adapter_resumes_only_matching_naver_autosave_and_attaches_cards():
    page = Page(editor_empty=False, recovering=True)
    page.title = '확인 제목'
    page.body = '검증한 전체 본문'
    cards = [Path(f'card-{index}.png') for index in range(1, 6)]

    PlaywrightNaverAdapter(page).save_and_verify(
        'himemi0412', page.title, page.body, cards,
    )

    assert ('click', 'button') in page.actions
    assert any(action[0] == 'files' and len(action[1]) == 5 for action in page.actions)


def test_browser_adapter_refuses_preexisting_unverified_recovery_images():
    page = Page(editor_empty=False, recovering=True)
    page.title = '확인 제목'
    page.body = '검증한 전체 본문'
    page.editor_image_count = 5
    cards = [Path(f'card-{index}.png') for index in range(1, 6)]

    with pytest.raises(RuntimeError, match='unverified images'):
        PlaywrightNaverAdapter(page).save_and_verify(
            'himemi0412', page.title, page.body, cards,
        )

    assert not any(action[0] == 'files' for action in page.actions)


def test_readback_compares_markdown_lists_to_their_rendered_text_without_dropping_words():
    validate_draft_readback(
        ['확인 제목'], '확인 제목',
        '도입 내용 첫 단계 두 번째 단계 공식 출처',
        '확인 제목',
        '도입 내용\n1. 첫 단계\n2. 두 번째 단계\n- 공식 출처',
        5,
    )


def test_readback_accounts_for_inline_bullet_serialization_but_not_missing_words():
    validate_draft_readback(
        ['확인 제목'], '확인 제목',
        '본문 내용 공식 출처.pdf 확인일: 2026-09-25',
        '확인 제목',
        '본문 내용\n- 공식 출처.pdf\n- 확인일: 2026-09-25',
        5,
    )
    with pytest.raises(RuntimeError, match='body did not match'):
        validate_draft_readback(
            ['확인 제목'], '확인 제목',
            '본문 내용 출처.pdf 확인일: 2026-09-25',
            '확인 제목',
            '본문 내용\n- 공식 출처.pdf\n- 확인일: 2026-09-25',
            5,
        )
