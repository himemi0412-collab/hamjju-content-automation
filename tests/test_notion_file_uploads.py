from pathlib import Path

import app.notion_client as notion_module
from app.notion_client import NotionClient


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeApiClient:
    def __init__(self):
        self.calls = []

    def post(self, url, json):
        self.calls.append((url, json))
        if url == '/file_uploads':
            return FakeResponse({'id': 'upload-123', 'status': 'pending'})
        if url == '/file_uploads/upload-123/complete':
            return FakeResponse({'id': 'upload-123', 'status': 'uploaded'})
        raise AssertionError(url)


def test_upload_file_uses_single_part_for_small_file(tmp_path, monkeypatch):
    path = tmp_path / 'clip.mp4'
    path.write_bytes(b'123456789')
    notion = NotionClient('token')
    notion.client.close()
    notion.client = FakeApiClient()
    sends = []

    def fake_post(url, **kwargs):
        sends.append((url, kwargs))
        return FakeResponse({'status': 'uploaded'})

    monkeypatch.setattr(notion_module.httpx, 'post', fake_post)
    monkeypatch.setattr(notion_module, 'NOTION_SINGLE_PART_LIMIT', 10)

    assert notion.upload_file(path) == 'upload-123'
    assert notion.client.calls == [('/file_uploads', {
        'mode': 'single_part',
        'filename': 'clip.mp4',
        'content_type': 'video/mp4',
    })]
    assert sends[0][1]['data'] is None


def test_upload_file_uses_numbered_parts_and_completes(tmp_path, monkeypatch):
    path = tmp_path / 'clip.mp4'
    path.write_bytes(b'abcdefghijklmnopqrstuvwxyz')
    notion = NotionClient('token')
    notion.client.close()
    notion.client = FakeApiClient()
    sends = []

    def fake_post(url, **kwargs):
        file_value = kwargs['files']['file'][1]
        sends.append((url, kwargs['data'], bytes(file_value)))
        return FakeResponse({'status': 'pending'})

    monkeypatch.setattr(notion_module.httpx, 'post', fake_post)
    monkeypatch.setattr(notion_module, 'NOTION_SINGLE_PART_LIMIT', 20)
    monkeypatch.setattr(notion_module, 'NOTION_PART_SIZE', 10)

    assert notion.upload_file(path) == 'upload-123'
    assert notion.client.calls == [
        ('/file_uploads', {
            'mode': 'multi_part',
            'filename': 'clip.mp4',
            'content_type': 'video/mp4',
            'number_of_parts': 3,
        }),
        ('/file_uploads/upload-123/complete', {}),
    ]
    assert [item[1] for item in sends] == [
        {'part_number': '1'},
        {'part_number': '2'},
        {'part_number': '3'},
    ]
    assert [len(item[2]) for item in sends] == [10, 10, 6]
