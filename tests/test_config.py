from app.config import load_channels
from app.main import run_all_channels


def test_channels_are_separated():
    c = load_channels()
    assert c['ppojjugi_shorts'].notion_channel_value == '햄찌 창작 쇼츠'
    assert c['japan_shorts'].notion_channel_value == '일본 유튜브 쇼츠'
    assert c['naver_blog'].publish_policy == 'draft_only'
    assert c['japan_shorts'].youtube_privacy == 'private'


def test_run_limit_is_global_across_channels():
    class FakePipeline:
        def __init__(self):
            self.calls = []

        def run_channel(self, channel, limit, dry_run):
            self.calls.append((channel, limit, dry_run))
            return [{'channel': channel}] if channel != 'empty' else []

    pipeline = FakePipeline()
    channels = {
        'naver_blog': 'empty',
        'ppojjugi_shorts': 'shorts-a',
        'japan_shorts': 'shorts-b',
    }
    results = run_all_channels(pipeline, channels, total_limit=1, dry_run=True)

    assert results == [{'channel': 'shorts-a'}]
    assert pipeline.calls == [
        ('empty', 1, True),
        ('shorts-a', 1, True),
    ]
