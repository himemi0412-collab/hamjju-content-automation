import copy
import hashlib

import pytest
from PIL import Image, ImageDraw

from app.media import render_blog_cards


def sample_cards():
    """A rendering fixture, not a researched or publication-ready manuscript."""
    content = [
        ('cover', '식기세척기에\n주방세제 넣어도 될까?', '먼저 식기세척기 전용 표시부터 확인해요.',
         [('용도부터', '손설거지용인지 확인'), ('표시부터', '식기세척기 전용인지 확인')]),
        ('flow', '거품이 많으면\n생기는 문제', '거품이 넘치면 누수처럼 보일 수 있어요.',
         [('손설거지용 세제', '기기용 세제와 거품 특성이 달라요.'),
          ('과도한 거품', '기기 안에서 거품이 넘칠 수 있어요.'),
          ('넘침 확인', '거품과 물이 새는지 확인이 필요해요.')]),
        ('comparison', '둘 다 세제인데\n용도는 달라요', '포장에 적힌 사용 대상을 같은 기준으로 봐요.',
         [('손설거지용', '사람이 손으로 씻을 때 쓰는 용도인지 표시를 확인해요.'),
          ('식기세척기용', '기기 전용 표시와 모델별 권장 사용량을 함께 확인해요.')]),
        ('checklist', '넣기 전에\n네 가지만 확인', '세제 이름보다 포장과 설명서가 먼저예요.',
         [('사용 대상', '식기세척기 전용 표시를 찾아요.'),
          ('사용량', '세제 포장과 모델 설명서를 대조해요.'),
          ('모델 지침', '다른 기기의 관리법을 그대로 따르지 않아요.'),
          ('이상 징후', '문제가 있으면 공식 고객지원에 확인해요.')]),
        ('decision', '헷갈릴 때는\n이 순서로 확인', '확인된 지침 안에서 다음 행동을 정해요.',
         [('투입 전이라면', '전용 표시와 사용량부터 확인해요.'),
          ('이미 넣었다면', '모델별 설명서와 고객지원에 문의해요.'),
          ('방법이 불확실하면', '임의로 다른 세제를 섞거나 분해하지 않아요.')]),
    ]
    return [
        {'card': index, 'layout': layout, 'headline': headline, 'copy': summary,
         'illustration': 'bubbles', 'items': [{'label': label, 'detail': detail} for label, detail in items]}
        for index, (layout, headline, summary, items) in enumerate(content, 1)
    ]


def test_complete_five_card_set_has_distinct_square_layouts(tmp_path):
    paths = render_blog_cards(sample_cards(), tmp_path)

    assert len(paths) == 5
    assert len({hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}) == 5
    for path in paths:
        with Image.open(path) as im:
            assert im.size == (1080, 1080)
            assert im.getpixel((0, 0)) == (255, 255, 255)
            assert len(im.getcolors(1080 * 1080)) > 100


def test_text_overflow_fails_before_any_card_is_written(tmp_path):
    cards = sample_cards()
    cards[-1]['items'][-1]['detail'] = '긴 정보가 반복됩니다. ' * 80

    with pytest.raises(ValueError, match='overflow'):
        render_blog_cards(cards, tmp_path)

    assert list(tmp_path.glob('*.png')) == []


def test_extra_card_is_rejected_instead_of_silently_dropped(tmp_path):
    cards = sample_cards()
    cards.append(copy.deepcopy(cards[-1]))
    with pytest.raises(ValueError, match='exactly five'):
        render_blog_cards(cards, tmp_path)


def test_unstructured_legacy_card_cannot_claim_complete_diagrams(tmp_path):
    cards = sample_cards()
    cards[2].pop('items')
    with pytest.raises(ValueError, match='structured items'):
        render_blog_cards(cards, tmp_path)


def test_explicit_headline_lines_are_drawn_separately(tmp_path, monkeypatch):
    drawn_text = []
    original = ImageDraw.ImageDraw.text

    def capture(draw, xy, text, *args, **kwargs):
        drawn_text.append(text)
        return original(draw, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, 'text', capture)
    render_blog_cards(sample_cards(), tmp_path)

    assert '식기세척기에' in drawn_text
    assert '주방세제 넣어도 될까?' in drawn_text
    assert not any('\n' in text for text in drawn_text)
