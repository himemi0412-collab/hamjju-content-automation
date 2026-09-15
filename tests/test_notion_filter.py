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
    )

    assert notion.client.path == '/data_sources/blog-data-source/query'
    assert notion.client.body['filter'] == {
        'and': [
            {'property': '상태', 'select': {'equals': '작성 요청'}},
            {
                'property': '목록 구분',
                'formula': {'string': {'does_not_equal': '이전 주제 보관'}},
            },
        ],
    }
    assert notion.client.body['page_size'] == 1
