"""Read the existing Notion Shorts queue and mirror its current state to Life OS."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

DATA_SOURCE_ID = "e7ad07b7-0652-4747-956d-e7b071d4bde9"
NOTION_VERSION = "2026-03-11"
SITE_ENDPOINT = "https://hamjji-life-os.himemi0412.chatgpt.site/api/notion-sync"
MAX_ITEMS = 500


def request_json(url: str, token: str, payload: dict | None = None, *, notion: bool = False, site_bypass_token: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if site_bypass_token:
        headers["OAI-Sites-Authorization"] = f"Bearer {site_bypass_token}"
    if notion:
        headers.update({"Notion-Version": NOTION_VERSION, "Content-Type": "application/json"})
    if payload is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers=headers,
        method="POST" if payload is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Sync request failed with HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Sync request failed: {type(exc).__name__}") from None


def rich_text(prop: dict) -> str:
    pieces = prop.get("rich_text") or []
    return "".join(piece.get("plain_text", "") for piece in pieces)[:1200]


def title_text(prop: dict) -> str:
    pieces = prop.get("title") or []
    return "".join(piece.get("plain_text", "") for piece in pieces)[:300]


def select_name(prop: dict) -> str:
    return (prop.get("select") or {}).get("name", "")


def url_value(prop: dict) -> str:
    return (prop.get("url") or "")[:500]


def normalize(page: dict) -> dict:
    properties = page.get("properties") or {}
    created = page.get("created_time")
    edited = page.get("last_edited_time")
    if not created or not edited:
        raise RuntimeError("Notion returned a row without timestamps")
    video_files = (properties.get("최종 영상") or {}).get("files") or []
    return {
        "id": page.get("id", ""),
        "title": title_text(properties.get("제목") or {}),
        "channel": select_name(properties.get("채널") or {}),
        "status": select_name(properties.get("상태") or {}),
        "contentId": rich_text(properties.get("콘텐츠 ID") or {}),
        "pageUrl": page.get("url", ""),
        "privateUrl": url_value(properties.get("YouTube 비공개 주소") or {}),
        "publicApproved": bool((properties.get("공개 승인") or {}).get("checkbox")),
        "nextAction": rich_text(properties.get("다음 행동") or {}),
        "workLog": rich_text(properties.get("작업 기록") or {}),
        "createdAt": created,
        "editedAt": edited,
        "hasFinalVideo": bool(video_files),
    }


def main() -> int:
    notion_token = os.environ.get("NOTION_ACCESS_TOKEN", "")
    sync_token = os.environ.get("LIFEOS_SYNC_TOKEN", "")
    site_bypass_token = os.environ.get("SITES_SIWC_BYPASS_TOKEN", "")
    if not notion_token or not sync_token or not site_bypass_token:
        print("Life OS sync credentials are not configured", file=sys.stderr)
        return 2

    rows: list[dict] = []
    cursor = None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        response = request_json(
            f"https://api.notion.com/v1/data_sources/{DATA_SOURCE_ID}/query",
            notion_token,
            body,
            notion=True,
        )
        rows.extend(normalize(page) for page in response.get("results", []))
        if len(rows) > MAX_ITEMS:
            raise RuntimeError("Notion queue exceeds the configured sync limit")
        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")
        if not cursor:
            raise RuntimeError("Notion pagination cursor is missing")

    if not rows:
        raise RuntimeError("Notion returned an empty queue; keeping the previous Life OS snapshot")

    payload = {"syncedAt": datetime.now(timezone.utc).isoformat(), "items": rows}
    result = request_json(SITE_ENDPOINT, sync_token, payload, site_bypass_token=site_bypass_token)
    print(f"Life OS Notion sync completed: {result.get('itemCount', 0)} records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

