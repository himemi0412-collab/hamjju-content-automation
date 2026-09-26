import httpx

from app.naver_completion import confirm_naver_results


def _data():
    return {'batches': [{
        'channel': 'naver_blog',
        'results': [{'page_id': 'page-1', 'title': '확인 글', 'status': '네이버 저장 요청'}],
    }]}


def _page(status, title='확인 글', draft_url='https://blog.naver.com/draft'):
    props = {
        '제목': {'type': 'title', 'title': [{'plain_text': title}]},
        '상태': {'type': 'select', 'select': {'name': status}},
        '네이버 임시저장 주소': {'type': 'url', 'url': draft_url},
    }
    return {'properties': props}


def test_naver_completion_is_confirmed_only_after_notion_worker_status_readback():
    replies = iter([
        httpx.Response(200, json=_page('네이버 저장 요청')),
        httpx.Response(200, json=_page('임시저장 완료')),
    ])
    sleeps = []

    def factory(**kwargs):
        return httpx.Client(transport=httpx.MockTransport(lambda _: next(replies)), **kwargs)

    ticks = iter([0.0, 1.0, 1.0, 2.0])
    result = confirm_naver_results(
        _data(), 'test-token', timeout_seconds=10, interval_seconds=1,
        client_factory=factory, sleep=sleeps.append, monotonic=lambda: next(ticks),
    )

    row = result['batches'][0]['results'][0]
    assert row['naver_draft_verified'] is True
    assert row['naver_draft_state'] == 'verified'
    assert sleeps == [1]


def test_naver_completion_does_not_pass_wrong_title_or_missing_draft_link():
    page = _page('임시저장 완료', title='다른 글', draft_url=None)
    factory = lambda **kwargs: httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=page)), **kwargs)
    result = confirm_naver_results(
        _data(), 'test-token', timeout_seconds=0, client_factory=factory,
    )

    row = result['batches'][0]['results'][0]
    assert row['naver_draft_verified'] is False
    assert row['naver_draft_state'] == 'failed'


def test_naver_completion_times_out_instead_of_counting_pending_as_pass():
    page = _page('네이버 저장 요청')
    factory = lambda **kwargs: httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=page)), **kwargs)
    result = confirm_naver_results(
        _data(), 'test-token', timeout_seconds=0, client_factory=factory,
    )

    row = result['batches'][0]['results'][0]
    assert row['naver_draft_verified'] is False
    assert row['naver_draft_state'] == 'timed_out'


def test_naver_completion_readback_error_is_not_passed():
    factory = lambda **kwargs: httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(403)), **kwargs)
    result = confirm_naver_results(_data(), 'test-token', client_factory=factory)

    row = result['batches'][0]['results'][0]
    assert row['naver_draft_verified'] is False
    assert row['naver_draft_state'] == 'read_failed'
