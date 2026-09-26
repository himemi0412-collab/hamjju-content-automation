"""Windows-only Naver private-draft worker.

It consumes Notion pages in '네이버 저장 요청', uses a dedicated local Edge profile,
and can only click the exact '임시저장' action. Public/reserved publishing is absent.
"""
from __future__ import annotations
import argparse, ctypes, json, os, time
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import httpx
from .naver_browser import DraftSaveAdapter, DraftVerificationFailed, PlaywrightNaverAdapter
from .manuscript_repair import manuscript_hash
from .content_fingerprint import file_sha256

NOTION_VERSION = "2025-09-03"
READY = "네이버 저장 요청"
DONE = "임시저장 완료"
MARKER = "READY_FOR_NAVER_DRAFT"


class HandoffValidationFailed(RuntimeError):
    pass


class WorkerStepFailed(RuntimeError):
    pass


def notify(message: str, state_dir: Path) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "attention.json").write_text(
        json.dumps({"message": message, "time": time.strftime("%Y-%m-%d %H:%M:%S")}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(0, message, "햄쮸 네이버 임시저장 - 확인 필요", 0x30)


class NotionQueue:
    def __init__(self, token: str, data_source_id: str):
        self.ds = data_source_id
        self.client = httpx.Client(
            base_url="https://api.notion.com/v1",
            headers={"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION},
            timeout=45,
        )

    def waiting(self) -> list[dict[str, Any]]:
        response = self.client.post(f"/data_sources/{self.ds}/query", json={
            "filter": {"property": "상태", "select": {"equals": READY}},
            "page_size": 10,
        })
        response.raise_for_status()
        return response.json().get("results", [])

    def blocks(self, page_id: str) -> list[dict[str, Any]]:
        results, cursor = [], None
        while True:
            params = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            response = self.client.get(f"/blocks/{page_id}/children", params=params)
            response.raise_for_status()
            data = response.json()
            results.extend(data.get("results", []))
            if not data.get("has_more"):
                return results
            cursor = data.get("next_cursor")

    def page(self, page_id: str) -> dict[str, Any]:
        response = self.client.get(f"/pages/{page_id}")
        response.raise_for_status()
        return response.json()

    def card_urls(self, page_id: str) -> list[str]:
        response = self.client.get(f"/pages/{page_id}")
        response.raise_for_status()
        files = response.json().get("properties", {}).get("생성 이미지", {}).get("files", [])
        urls = []
        for item in files:
            kind = item.get("type")
            value = item.get(kind, {}) if kind else {}
            if value.get("url"):
                urls.append(value["url"])
        if len(urls) != 5:
            raise RuntimeError(f"exactly five blog cards required; found {len(urls)}")
        return urls

    def download_cards(self, page_id: str, target: Path) -> list[Path]:
        target.mkdir(parents=True, exist_ok=True)
        paths = []
        for index, url in enumerate(self.card_urls(page_id), 1):
            response = httpx.get(url, timeout=60, follow_redirects=True)
            response.raise_for_status()
            path = target / f"card-{index:02d}.png"
            path.write_bytes(response.content)
            paths.append(path)
        return paths

    def update_done(self, page_id: str, draft_url: str) -> None:
        page = self.client.get(f"/pages/{page_id}").json()
        props = page.get("properties", {})
        update: dict[str, Any] = {"상태": {"select": {"name": DONE}}}
        if "네이버 임시저장 주소" in props:
            update["네이버 임시저장 주소"] = {"url": draft_url}
        response = self.client.patch(f"/pages/{page_id}", json={"properties": update})
        response.raise_for_status()

    def update_failed(self, page_id: str) -> None:
        response = self.client.patch(f"/pages/{page_id}", json={
            "properties": {"상태": {"select": {"name": "수정 필요"}}},
        })
        response.raise_for_status()

    def update_failed_if_unchanged(self, page_id: str, expected_last_edited_time: str | None) -> bool:
        if not expected_last_edited_time:
            return False
        page = self.page(page_id)
        status = (page.get('properties') or {}).get('상태', {}).get('select', {}).get('name')
        if page.get('last_edited_time') != expected_last_edited_time or status != READY:
            return False
        self.update_failed(page_id)
        return True

    def close(self) -> None:
        self.client.close()


def rich_text(block: dict[str, Any]) -> str:
    kind = block.get("type", "")
    return "".join(x.get("plain_text", "") for x in block.get(kind, {}).get("rich_text", []))


@dataclass(frozen=True)
class VerifiedNaverPayload:
    title: str
    body: str
    page_id: str
    run_id: str
    qa_run_id: str
    content_hash: str
    card_sha256s: tuple[str, ...]


def payload_from_blocks(blocks: list[dict[str, Any]], expected_page_id: str) -> VerifiedNaverPayload:
    texts = [rich_text(b) for b in blocks]
    code_groups: list[tuple[int, int, str]] = []
    current_group: list[str] = []
    group_start = 0
    for index, block in enumerate(blocks):
        if block.get("type") == "code":
            if not current_group:
                group_start = index
            current_group.append(rich_text(block))
        elif current_group:
            code_groups.append((group_start, index - 1, "".join(current_group)))
            current_group = []
    if current_group:
        code_groups.append((group_start, len(blocks) - 1, "".join(current_group)))

    for start, _end, raw in reversed(code_groups):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        generated = data.get("generated", data)
        if not isinstance(generated, dict):
            continue
        qa = data.get("qa") or {}
        if not isinstance(qa, dict):
            raise HandoffValidationFailed('Naver handoff QA record is malformed')
        handoff = data.get('handoff') or {}
        if not isinstance(handoff, dict) or handoff.get('schema_version') != 2:
            raise HandoffValidationFailed('STALE_QA: legacy handoff has no run-bound QA receipt')
        ownership = data.get('ownership') or {}
        if qa.get('pass') is not True:
            raise HandoffValidationFailed('Naver handoff QA is not PASS')
        current_hash = manuscript_hash(generated)
        page_id = str(handoff.get('page_id') or '')
        run_id = str(handoff.get('run_id') or '')
        qa_run_id = str(handoff.get('qa_run_id') or '')
        content_version = str(handoff.get('content_version') or '')
        content_hash = str(handoff.get('content_sha256') or '')
        card_hashes = handoff.get('card_sha256s')
        if (
            not expected_page_id or page_id != expected_page_id
            or not run_id or not qa_run_id
        ):
            raise HandoffValidationFailed('STALE_QA: page or run identity is missing or mismatched')
        if current_hash != content_hash or current_hash != content_version:
            raise HandoffValidationFailed('HASH_MISMATCH: final manuscript differs from its QA handoff')
        if (
            qa.get('source_page_id') != page_id
            or qa.get('source_run_id') != qa_run_id
            or qa.get('source_content_sha256') != current_hash
            or qa.get('source_card_sha256s') != card_hashes
        ):
            raise HandoffValidationFailed('STALE_QA: QA receipt does not match this page, run, manuscript, and cards')
        if not isinstance(card_hashes, list) or len(card_hashes) != 5 or any(
            not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value)
            for value in card_hashes
        ):
            raise HandoffValidationFailed('STALE_QA: five valid QA card hashes are required')
        if not isinstance(ownership, dict) or not (
            ownership.get('document_id') == expected_page_id
            and ownership.get('source_version') == current_hash
            and ownership.get('stage') == 'HANDOFF_READY'
            and ownership.get('channel') == 'naver_blog'
        ):
            raise HandoffValidationFailed('STALE_QA: ownership receipt does not match the final manuscript')
        marker_positions = [i for i, text in enumerate(texts[:start]) if text == f'저장 준비 완료: {MARKER}']
        if not marker_positions:
            raise HandoffValidationFailed('Naver handoff marker missing')
        marker = marker_positions[-1]
        handoff_text = texts[marker:start]
        page_lines = [x.partition(':')[2].strip() for x in handoff_text if x.startswith('원고 ID:')]
        version_lines = [x.partition(':')[2].strip() for x in handoff_text if x.startswith('버전:')]
        run_lines = [x.partition(':')[2].strip() for x in handoff_text if x.startswith('실행 ID:')]
        qa_run_lines = [x.partition(':')[2].strip() for x in handoff_text if x.startswith('QA 실행 ID:')]
        if (
            page_lines != [page_id] or version_lines != [current_hash]
            or run_lines != [run_id] or qa_run_lines != [qa_run_id]
        ):
            raise HandoffValidationFailed('STALE_QA: visible handoff header and JSON snapshot differ')
        title = str(generated.get("title") or data.get("title") or "").strip()
        body = str(generated.get("body_markdown") or generated.get("body") or "").strip()
        if title and body:
            return VerifiedNaverPayload(
                title, body, page_id, run_id, qa_run_id, current_hash, tuple(card_hashes),
            )
        raise HandoffValidationFailed('Naver QA snapshot did not contain a title and full body')
    raise HandoffValidationFailed('STALE_QA: current page has no run-bound QA snapshot')


def process_jobs(
    queue: NotionQueue, browser: DraftSaveAdapter, jobs: list[dict[str, Any]],
    blog_id: str, state_dir: Path,
) -> int:
    for job in jobs:
        stage = 'handoff_readback'
        expected_page_edit = None
        try:
            page_id = job['id']
            page = queue.page(page_id)
            expected_page_edit = page.get('last_edited_time')
            payload = payload_from_blocks(queue.blocks(page_id), page_id)
            properties = page.get('properties') or {}
            status = properties.get('상태', {}).get('select', {}).get('name')
            page_title = ''.join(x.get('plain_text', '') for x in properties.get('제목', {}).get('title', []))
            if status != READY or page_title != payload.title:
                raise HandoffValidationFailed('STALE_QA: page status or title changed after the QA handoff')
            original_edit = job.get('last_edited_time')
            if original_edit and page.get('last_edited_time') != original_edit:
                raise HandoffValidationFailed('STALE_QA: Notion page changed after it entered the save queue')
            stage = 'card_download'
            cards = queue.download_cards(page_id, state_dir / page_id / payload.run_id / "cards")
            if len(cards) != 5 or tuple(file_sha256(path) for path in cards) != payload.card_sha256s:
                raise HandoffValidationFailed('HASH_MISMATCH: downloaded card bytes differ from the QA receipt')
            # Re-read both page state and handoff immediately before the browser write.
            latest_page = queue.page(page_id)
            latest_blocks = queue.blocks(page_id)
            latest_payload = payload_from_blocks(latest_blocks, page_id)
            if (
                latest_page.get('last_edited_time') != page.get('last_edited_time')
                or (latest_page.get('properties') or {}).get('상태', {}).get('select', {}).get('name') != READY
                or latest_payload != payload
            ):
                raise HandoffValidationFailed('STALE_QA: Notion page or handoff changed before the save')
            stage = 'browser_adapter'
            url = browser.save_and_verify(blog_id, payload.title, payload.body, cards)
            stage = 'notion_completion_write'
            queue.update_done(page_id, url)
        except HandoffValidationFailed as exc:
            try:
                safe_fail = getattr(queue, 'update_failed_if_unchanged', None)
                if safe_fail:
                    safe_fail(job['id'], expected_page_edit)
                else:
                    queue.update_failed(job['id'])
            except Exception as write_exc:
                raise WorkerStepFailed(
                    f"Notion page {job['id']}: QA handoff failed and failure status write failed ({type(write_exc).__name__})"
                ) from write_exc
            raise HandoffValidationFailed(f"Notion page {job['id']}: {exc}") from exc
        except Exception as exc:
            try:
                safe_fail = getattr(queue, 'update_failed_if_unchanged', None)
                if safe_fail:
                    safe_fail(job['id'], expected_page_edit)
                else:
                    queue.update_failed(job['id'])
            except Exception as write_exc:
                raise WorkerStepFailed(
                    f"Notion page {job['id']}: {stage} failed and failure status write failed ({type(write_exc).__name__})"
                ) from write_exc
            if isinstance(exc, DraftVerificationFailed):
                raise DraftVerificationFailed(f"Notion page {job['id']}: {exc}") from exc
            raise WorkerStepFailed(
                f"Notion page {job['id']}: {stage} failed ({type(exc).__name__})"
            ) from exc
    return len(jobs)


def run_once(
    headless: bool = False,
    browser_adapter: DraftSaveAdapter | None = None,
    page_id: str | None = None,
) -> int:
    token = os.getenv("NOTION_ACCESS_TOKEN", "")
    ds = os.getenv("BLOG_DATA_SOURCE_ID", "21a60556-f086-4b7a-96b3-81995c77edef")
    blog_id = os.getenv("NAVER_BLOG_ID", "himemi0412")
    if not token:
        raise RuntimeError("NOTION_ACCESS_TOKEN is required")
    state_dir = Path(os.getenv("NAVER_LOCAL_STATE_DIR", "output/naver-local"))
    profile = Path(os.getenv("NAVER_EDGE_PROFILE", str(Path(os.getenv("LOCALAPPDATA", ".")) / "Hamjju" / "NaverEdgeProfile")))
    queue = NotionQueue(token, ds)
    try:
        if page_id is not None:
            if not re.fullmatch(r'[0-9a-fA-F-]{32,36}', page_id):
                raise ValueError('A single exact Notion page ID is required')
            jobs = [queue.page(page_id)]
        else:
            jobs = queue.waiting()
        if not jobs:
            return 0
        if browser_adapter is not None:
            return process_jobs(queue, browser_adapter, jobs, blog_id, state_dir)
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(
                str(profile), channel="msedge", headless=headless, viewport={"width": 1440, "height": 1000}
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                browser = PlaywrightNaverAdapter(page)
                return process_jobs(queue, browser, jobs, blog_id, state_dir)
            finally:
                context.close()
    finally:
        queue.close()


def main() -> None:
    from dotenv import load_dotenv
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("once", "watch", "login"), default="once", nargs="?")
    parser.add_argument("--interval", type=int, default=300)
    parser.add_argument("--page-id", help="Process exactly one Notion page once")
    args = parser.parse_args()
    if args.page_id and args.mode != 'once':
        parser.error('--page-id can only be used with once')
    state_dir = Path(os.getenv("NAVER_LOCAL_STATE_DIR", "output/naver-local"))
    if args.mode == "login":
        from playwright.sync_api import sync_playwright
        profile = Path(os.getenv("NAVER_EDGE_PROFILE", str(Path(os.getenv("LOCALAPPDATA", ".")) / "Hamjju" / "NaverEdgeProfile")))
        with sync_playwright() as pw:
            ctx = pw.chromium.launch_persistent_context(str(profile), channel="msedge", headless=False)
            ctx.pages[0].goto("https://nid.naver.com/nidlogin.login")
            input("로그인을 완료한 뒤 Enter를 누르세요: ")
            ctx.close()
        return
    while True:
        try:
            run_once(page_id=args.page_id)
        except Exception as exc:
            if isinstance(exc, DraftVerificationFailed):
                message = f"네이버 임시저장 목록 확인 실패: {exc}"
            elif isinstance(exc, HandoffValidationFailed):
                message = f"네이버 검수 전달 자료 확인 실패: {exc}"
            elif isinstance(exc, WorkerStepFailed):
                message = f"네이버 자동화 실패: {exc}"
            else:
                message = f"네이버 작업 실패 ({type(exc).__name__}). 자세한 내용은 로그를 확인해 주세요."
            notify(message, state_dir)
        if args.mode == "once":
            return
        time.sleep(max(args.interval, 60))


if __name__ == "__main__":
    main()
