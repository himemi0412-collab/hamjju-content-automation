from app.pipeline import _card_numbers_from_qa_issue


def test_extracts_conjoined_card_numbers_from_korean_qa_feedback():
    issue = '카드 4와 5의 실외기 장면이 덮개를 씌우는 동작으로 보입니다.'
    assert _card_numbers_from_qa_issue(issue) == {4, 5}


def test_extracts_several_card_reference_styles_without_percentages():
    issue = '카드 2, 3 및 5가 문제이며 한글 영역은 30~40%입니다. 2번 카드도 다시 확인하세요.'
    assert _card_numbers_from_qa_issue(issue) == {2, 3, 5}


def test_returns_empty_set_when_feedback_has_no_card_reference():
    assert _card_numbers_from_qa_issue('전체 이미지의 색조를 확인하세요.') == set()
