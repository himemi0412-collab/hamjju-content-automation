import copy

import pytest

from app.config import load_channels
from app.notion_client import NotionClient


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


class BlogQueueHTTP:
    """Model the existing Notion queue formula, without a network request."""

    def __init__(self):
        self.pages = []
        self.creation_payload = None

    def post(self, path, json):
        if path == '/pages':
            self.creation_payload = copy.deepcopy(json)
            page = {'id': f'page-{len(self.pages) + 1}', **copy.deepcopy(json)}
            self.pages.append(page)
            return FakeResponse(page)

        assert path == '/data_sources/blog-data-source/query'
        filters = json['filter'].get('and', [json['filter']])
        return FakeResponse({
            'results': [page for page in self.pages if all(
                self.matches(page['properties'], clause) for clause in filters
            )][:json['page_size']],
        })

    @staticmethod
    def matches(properties, clause):
        name = clause['property']
        if name == '목록 구분':
            # Observed database formula: missing 원본 순서 archives an item,
            # even when 진행 순서 is positive and 상태 is 작성 요청.
            source_order = properties.get('원본 순서', {}).get('number')
            category = '다음 5편' if source_order else '이전 주제 보관'
            return category != clause['formula']['string']['does_not_equal']
        if 'select' in clause:
            return properties.get(name, {}).get('select', {}).get('name') == clause['select']['equals']
        if 'number' in clause:
            value = properties.get(name, {}).get('number')
            return value is not None and value > clause['number']['greater_than']
        raise AssertionError(f'Unexpected queue filter: {clause}')


def client_with_fake_queue():
    notion = object.__new__(NotionClient)
    notion.client = BlogQueueHTTP()
    return notion


@pytest.mark.parametrize('order', [1, 2026091701])
def test_new_blog_sets_both_orders_and_stays_unwritten(order):
    notion = client_with_fake_queue()

    page_id = notion.create_blog_topic('blog-data-source', {'title': '식기세척기 세제 선택'}, order)

    assert page_id == 'page-1'
    payload = notion.client.creation_payload
    assert payload['parent'] == {'type': 'data_source_id', 'data_source_id': 'blog-data-source'}
    assert payload['properties']['원본 순서'] == {'number': order}
    assert payload['properties']['진행 순서'] == {'number': order}
    assert payload['properties']['상태'] == {'select': {'name': '작성 요청'}}
    assert 'children' not in payload


def test_new_blog_is_selected_by_production_filter_without_unarchiving_old_topics():
    notion = client_with_fake_queue()
    created_id = notion.create_blog_topic('blog-data-source', {'title': '새로 작성할 주제'}, 2026091701)
    archived_page = copy.deepcopy(notion.client.pages[0])
    archived_page['id'] = 'previous-archive'
    archived_page['properties'].pop('원본 순서')
    notion.client.pages.append(archived_page)
    cfg = load_channels()['naver_blog']

    results = notion.query_ready(
        'blog-data-source',
        cfg.ready_status,
        page_size=3,
        excluded_formula_property=cfg.excluded_formula_property,
        excluded_formula_value=cfg.excluded_formula_value,
        required_select_values=cfg.required_select_values,
        required_number_greater_than=cfg.required_number_greater_than,
        sort_property=cfg.sort_property,
    )

    assert [page['id'] for page in results] == [created_id]


def test_blog_topic_persists_seo_geo_metadata_in_existing_fields():
    notion = client_with_fake_queue()
    notion.create_blog_topic('blog-data-source', {
        'title': '에어컨 전기요금 줄이는 법',
        'main_keyword': '에어컨 전기요금',
        'sub_keywords': ['인버터 에어컨', '절전 설정'],
        'reader_question': '하루 종일 켜도 될까?',
        'faq_questions': ['제습이 더 저렴할까?'],
        'search_intent': '비용 확인',
        'geo_answer': '인버터형은 짧게 반복해 끄는 것보다 설정 온도를 유지하는 편이 효율적일 수 있다.',
        'trend_reason': '여름철, 8월까지',
        'seo_score': 88,
        'geo_score': 84,
        'sources': '공식 에너지 자료 https://example.com 2026-09-23',
    }, 1)

    props = notion.client.creation_payload['properties']
    question = ''.join(x['text']['content'] for x in props['독자 질문']['rich_text'])
    sources = ''.join(x['text']['content'] for x in props['출처 목록']['rich_text'])
    assert '후속 질문: 제습이 더 저렴할까?' in question
    assert '검색 의도: 비용 확인' in sources
    assert 'GEO 핵심 답변:' in sources
    assert 'SEO/GEO 점수: 88/84' in sources
