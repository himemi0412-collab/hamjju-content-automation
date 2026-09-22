from pathlib import Path
from app.naver_local_worker import payload_from_blocks, FORBIDDEN

def _block(kind, text):
    return {"type": kind, kind: {"rich_text": [{"plain_text": text}]}}

def test_handoff_requires_marker_and_snapshot():
    blocks = [
        _block("paragraph", "READY_FOR_NAVER_DRAFT"),
        _block("code", '{"generated":{"title":"제목","body_markdown":"본문"}}'),
    ]
    assert payload_from_blocks(blocks) == ("제목", "본문")

def test_local_worker_has_no_publication_action():
    src = Path("app/naver_local_worker.py").read_text(encoding="utf-8")
    assert 'get_by_text("임시저장", exact=True)' in src
    assert "get_by_text(\"발행\"" not in src
    assert {"발행", "예약발행", "공개"}.issubset(set(FORBIDDEN))
