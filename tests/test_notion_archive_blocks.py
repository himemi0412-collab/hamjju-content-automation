import httpx
import pytest

from app.notion_client import NOTION_VERSION, NotionClient


def client_with_transport(handler):
    notion = object.__new__(NotionClient)
    notion.client = httpx.Client(
        base_url='https://api.notion.com/v1',
        headers={'Notion-Version': NOTION_VERSION},
        transport=httpx.MockTransport(handler),
    )
    return notion


def test_archive_uses_documented_reversible_delete_for_exact_blocks():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={'object': 'block', 'id': request.url.path.split('/')[-1]})

    notion = client_with_transport(handle)
    try:
        notion.archive_blocks(['existing-result-a', 'existing-result-b'])
    finally:
        notion.close()

    assert [(r.method, r.url.path) for r in requests] == [
        ('DELETE', '/v1/blocks/existing-result-a'),
        ('DELETE', '/v1/blocks/existing-result-b'),
    ]
    assert all(r.content == b'' for r in requests)
    assert all(r.headers['Notion-Version'] == '2026-03-11' for r in requests)


def test_archive_failure_stops_before_removing_later_blocks():
    paths = []

    def handle(request):
        paths.append(request.url.path)
        if request.url.path.endswith('/blocked'):
            return httpx.Response(403, json={'code': 'restricted_resource'})
        return httpx.Response(200, json={'object': 'block'})

    notion = client_with_transport(handle)
    try:
        with pytest.raises(httpx.HTTPStatusError):
            notion.archive_blocks(['first', 'blocked', 'must-remain'])
    finally:
        notion.close()

    assert paths == ['/v1/blocks/first', '/v1/blocks/blocked']


def test_archive_empty_selection_does_not_touch_notion():
    def unexpected_request(request):
        raise AssertionError('No external request should be made for an empty selection')

    notion = client_with_transport(unexpected_request)
    try:
        notion.archive_blocks([])
    finally:
        notion.close()
