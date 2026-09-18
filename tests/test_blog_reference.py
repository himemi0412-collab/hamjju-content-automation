import pytest

from app.blog_reference import (
    REFERENCE_PROFILE_ID,
    REFERENCE_URLS,
    load_blog_reference,
    validate_generated_reference_contract,
)


def test_versioned_baseline_contains_all_four_user_references():
    baseline = load_blog_reference()

    assert baseline['id'] == REFERENCE_PROFILE_ID
    assert baseline['source_urls'] == list(REFERENCE_URLS)
    assert len(baseline['sha256']) == 64
    assert '같은 텍스트 상자 틀을 다섯 번 반복하지 않는다' in baseline['rules_markdown']
    assert '공식 확인 링크' in baseline['rules_markdown']


def test_generated_blog_must_acknowledge_reference_contract():
    baseline = load_blog_reference()
    generated = {
        'reference_profile_id': REFERENCE_PROFILE_ID,
        'card_format': 'landscape_4_3',
        'visual_family': 'contract_notebook',
        'card_news': [{'_': index} for index in range(5)],
    }

    validate_generated_reference_contract(generated, baseline)

    generated['reference_profile_id'] = 'forgotten'
    with pytest.raises(RuntimeError, match='REFERENCE_PROFILE_MISSING'):
        validate_generated_reference_contract(generated, baseline)


def test_reference_baseline_is_incomplete_without_every_url(tmp_path):
    source = tmp_path / 'baseline.md'
    source.write_text('# HAMZZU_NAVER_REFERENCE_V1\n' + '\n'.join(REFERENCE_URLS[:-1]), encoding='utf-8')

    with pytest.raises(RuntimeError, match='Incomplete'):
        load_blog_reference(source)
