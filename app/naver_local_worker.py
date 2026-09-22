"""Windows-only Naver private-draft worker.

It consumes Notion pages in '네이버 저장 요청', uses a dedicated local Edge profile,
and can only click the exact '임시저장' action. Public/reserved publishing is absent.
"""
from __future__ import annotations
import argparse, ctypes, json, os, time
from pathlib import Path
from typing import Any
import httpx

NOTION_VERSION = "2025-09-03"
READY = "네이버 저장 요청"
DONE = "임시저장 완료"
MARKER = "READY_FOR_NAVER_DRAFT"
CHALLENGES = ("캡차", "captcha", "보안 확인", "본인 확인", "재로그인", "로그인")
FORBIDDEN = ("발행", "예약발행", "공개")


class AttentionRequired(RuntimeError):
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

    def update_done(self, page_id: str, draft_url: str) -> None:
        page = self.client.get(f"/pages/{page_id}").json()
        props = page.get("properties", {})
        update: dict[str, Any] = {"상태": {"select": {"name": DONE}}}
        if "네이버 임시저장 주소" in props:
            update["네이버 임시저장 주소"] = {"url": draft_url}
        response = self.client.patch(f"/pages/{page_id}", json={"properties": update})
        response.raise_for_status()


def rich_text(block: dict[str, Any]) -> str:
    kind = block.get("type", "")
    return "".join(x.get("plain_text", "") for x in block.get(kind, {}).get("rich_text", []))


def payload_from_blocks(blocks: list[dict[str, Any]]) -> tuple[str, str]:
    texts = [rich_text(b) for b in blocks]
    if not any(MARKER in text for text in texts):
        raise RuntimeError("READY_FOR_NAVER_DRAFT marker missing")
    for block in reversed(blocks):
        if block.get("type") == "code":
            raw = rich_text(block)
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            generated = data.get("generated", data)
            title = str(generated.get("title") or data.get("title") or "").strip()
            body = str(generated.get("body_markdown") or generated.get("body") or "").strip()
            if title and body:
                return title, body
    raise RuntimeError("verified title/body snapshot missing")


def challenge_visible(page) -> bool:
    text = page.locator("body").inner_text(timeout=5000).lower()
    return any(word.lower() in text for word in CHALLENGES) or "nid.naver.com" in page.url


def first_visible(page, selectors: list[str]):
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if locator.is_visible(timeout=1200):
                return locator
        except Exception:
            pass
    raise AttentionRequired("네이버 편집기 구조가 바뀌었습니다. 화면 확인이 필요합니다.")


def save_one(page, blog_id: str, title: str, body: str) -> str:
    page.goto(f"https://blog.naver.com/PostWriteForm.naver?blogId={blog_id}", wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    if challenge_visible(page):
        raise AttentionRequired("네이버 보안 확인·재로그인·캡차를 완료한 뒤 다시 실행해 주세요.")
    title_box = first_visible(page, [
        "[contenteditable=true][data-placeholder*='제목']", ".se-title-text p", ".se-title-text",
    ])
    body_box = first_visible(page, [
        ".se-section-text [contenteditable=true]", ".se-text-paragraph", "[contenteditable=true][data-placeholder*='본문']",
    ])
    title_box.click()
    title_box.fill(title)
    body_box.click()
    body_box.fill(body)
    # Safety invariant: only an exact temporary-save label is eligible.
    save = page.get_by_text("임시저장", exact=True).first
    if not save.is_visible(timeout=3000):
        raise AttentionRequired("정확한 '임시저장' 버튼을 찾지 못했습니다. 자동 작업을 중단했습니다.")
    if save.inner_text().strip() in FORBIDDEN:
        raise RuntimeError("publication action blocked")
    save.click()
    page.wait_for_timeout(2500)
    draft_url = page.url
    page.reload(wait_until="domcontentloaded")
    page.wait_for_timeout(2000)
    if challenge_visible(page):
        raise AttentionRequired("저장 확인 중 네이버 보안 확인·재로그인·캡차가 나타났습니다.")
    verify = first_visible(page, [
        "[contenteditable=true][data-placeholder*='제목']", ".se-title-text p", ".se-title-text",
    ]).inner_text().strip()
    if title not in verify and verify not in title:
        raise RuntimeError("draft reopen verification failed")
    return draft_url


def run_once(headless: bool = False) -> int:
    token = os.getenv("NOTION_ACCESS_TOKEN", "")
    ds = os.getenv("BLOG_DATA_SOURCE_ID", "21a60556-f086-4b7a-96b3-81995c77edef")
    blog_id = os.getenv("NAVER_BLOG_ID", "himemi0412")
    if not token:
        raise RuntimeError("NOTION_ACCESS_TOKEN is required")
    state_dir = Path(os.getenv("NAVER_LOCAL_STATE_DIR", "output/naver-local"))
    profile = Path(os.getenv("NAVER_EDGE_PROFILE", str(Path(os.getenv("LOCALAPPDATA", ".")) / "Hamjju" / "NaverEdgeProfile")))
    from playwright.sync_api import sync_playwright
    queue = NotionQueue(token, ds)
    jobs = queue.waiting()
    if not jobs:
        return 0
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            str(profile), channel="msedge", headless=headless, viewport={"width": 1440, "height": 1000}
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            for job in jobs:
                title, body = payload_from_blocks(queue.blocks(job["id"]))
                url = save_one(page, blog_id, title, body)
                queue.update_done(job["id"], url)
        finally:
            context.close()
    return len(jobs)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("once", "watch", "login"), default="once", nargs="?")
    parser.add_argument("--interval", type=int, default=300)
    args = parser.parse_args()
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
            run_once()
        except AttentionRequired as exc:
            notify(str(exc), state_dir)
        except Exception as exc:
            notify(f"자동 임시저장을 중단했습니다: {exc}", state_dir)
        if args.mode == "once":
            return
        time.sleep(max(args.interval, 60))


if __name__ == "__main__":
    main()
