from __future__ import annotations
import sqlite3
from pathlib import Path
from datetime import datetime, timezone


class StateStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self._connect() as cx:
            cx.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    idempotency_key TEXT PRIMARY KEY,
                    page_id TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    detail TEXT
                )
            """)
            cx.execute("""
                CREATE TABLE IF NOT EXISTS content_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    page_id TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    source_version TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    detail TEXT
                )
            """)

    def _connect(self):
        return sqlite3.connect(self.path)

    def succeeded(self, key: str) -> bool:
        with self._connect() as cx:
            row = cx.execute('SELECT status FROM jobs WHERE idempotency_key=?', (key,)).fetchone()
            return bool(row and row[0] == 'success')

    def start(self, key: str, page_id: str, channel: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as cx:
            cx.execute(
                'INSERT OR REPLACE INTO jobs(idempotency_key,page_id,channel,started_at,status) VALUES(?,?,?,?,?)',
                (key, page_id, channel, now, 'running'),
            )

    def finish(self, key: str, status: str, detail: str = '') -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as cx:
            cx.execute(
                'UPDATE jobs SET finished_at=?, status=?, detail=? WHERE idempotency_key=?',
                (now, status, detail[:4000], key),
            )

    def record_stage(
        self,
        page_id: str,
        channel: str,
        source_version: str,
        owner: str,
        stage: str,
        run_id: str,
        detail: str = '',
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as cx:
            cx.execute(
                'INSERT INTO content_events(page_id,channel,source_version,owner,stage,run_id,created_at,detail) '
                'VALUES(?,?,?,?,?,?,?,?)',
                (page_id, channel, source_version, owner, stage, run_id, now, detail[:4000]),
            )
