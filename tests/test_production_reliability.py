import httpx

from app.notion_client import NotionClient
from app.pipeline import align_single_speaker_profile


def test_notion_block_read_recovers_after_transient_520(monkeypatch):
    attempts = []

    def handle(request):
        attempts.append(request)
        return httpx.Response(520 if len(attempts) < 3 else 200,
                              json={} if len(attempts) < 3 else {'results': [], 'has_more': False})

    notion = NotionClient('test-token')
    notion.client.close()
    notion.client = httpx.Client(base_url='https://api.notion.com/v1', transport=httpx.MockTransport(handle))
    monkeypatch.setattr('app.notion_client.time.sleep', lambda _: None)
    try:
        assert notion.read_page_blocks('test-page') == []
        assert len(attempts) == 3
    finally:
        notion.close()


def test_notion_block_read_does_not_retry_permission_error(monkeypatch):
    attempts = []

    def handle(request):
        attempts.append(request)
        return httpx.Response(401)

    notion = NotionClient('test-token')
    notion.client.close()
    notion.client = httpx.Client(base_url='https://api.notion.com/v1', transport=httpx.MockTransport(handle))
    monkeypatch.setattr('app.notion_client.time.sleep', lambda _: None)
    try:
        try:
            notion.read_page_text('test-page')
        except httpx.HTTPStatusError:
            pass
        else:
            raise AssertionError('401 must be reported')
        assert len(attempts) == 1
    finally:
        notion.close()


def test_japanese_single_speaker_metadata_is_aligned_without_changing_scenes():
    scenes = [{'speaker_profile': 'older_woman', 'narration': '一つ目'},
              {'speaker_profile': 'older_woman', 'narration': '二つ目'}]
    generated = {'narrator_profile': {'profile': 'multiple', 'reason': '回想'}, 'scenes': scenes}
    assert align_single_speaker_profile(generated)['narrator_profile']['profile'] == 'older_woman'
    assert generated['scenes'] == scenes
    mixed = {'narrator_profile': {'profile': 'multiple'},
             'scenes': [{'speaker_profile': 'older_woman'}, {'speaker_profile': 'young_man'}]}
    assert align_single_speaker_profile(mixed)['narrator_profile']['profile'] == 'multiple'
