from app.notion_client import NotionClient


class FakeResponse:
    def raise_for_status(self):
        pass

    def json(self):
        return {'results': []}


class FakeHTTPClient:
    def __init__(self):
        self.path = None
        self.body = None

    def post(self, path, json):
        self.path = path
        self.body = json
        return FakeResponse()


def test_query_ready_excludes_archived_blog_items():
    notion = object.__new__(NotionClient)
    notion.client = FakeHTTPClient()

    notion.query_ready(
        'blog-data-source',
        '작성 요청',
        page_size=1,
        excluded_formula_property='목록 구분',
        excluded_formula_value='이전 주제 보관',
        required_select_values={
            '선별 상태': '추천',
            '모델 확인': '공식 확인',
        },
        required_number_greater_than={'진행 순서': 0},
        sort_property='진행 순서',
    )

    assert notion.client.path == '/data_sources/blog-data-source/query'
    assert notion.client.body['filter'] == {
        'and': [
            {'property': '상태', 'select': {'equals': '작성 요청'}},
            {
                'property': '목록 구분',
                'formula': {'string': {'does_not_equal': '이전 주제 보관'}},
            },
            {'property': '선별 상태', 'select': {'equals': '추천'}},
            {'property': '모델 확인', 'select': {'equals': '공식 확인'}},
            {'property': '진행 순서', 'number': {'greater_than': 0}},
        ],
    }
    assert notion.client.body['page_size'] == 1
    assert notion.client.body['sorts'] == [
        {'property': '진행 순서', 'direction': 'ascending'},
    ]


def test_formula_property_value_is_readable():
    from app.notion_client import property_value

    assert property_value({
        'type': 'formula',
        'formula': {'type': 'string', 'string': '다음 5편'},
    }) == '다음 5편'


def test_extract_page_title_reads_only_title_property():
    from app.notion_client import extract_page_title

    page = {
        'properties': {
            '상태': {'type': 'select', 'select': {'name': '작성 요청'}},
            '제목': {'type': 'title', 'title': [{'plain_text': '자동화 테스트 글'}]},
        },
    }
    assert extract_page_title(page) == '자동화 테스트 글'
    assert extract_page_title({'properties': {}}) == '(제목 없음)'
