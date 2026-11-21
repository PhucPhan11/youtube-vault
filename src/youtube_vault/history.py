"""Download history stored in SQLite, plus the glue into yt-dlp.

- ``History``: reads/writes the ``downloads`` table. Every operation opens its own
  connection, so it is safe to call from several download threads at once.
- ``HistoryArchive``: set-like object passed as yt-dlp's ``download_archive``
  option to skip videos that were already downloaded.
- ``HistoryRecorder``: yt-dlp postprocessor that records history as soon as a video
  finishes (even if a playlist fails halfway).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from yt_dlp.postprocessor.common import PostProcessor

from . import config

__all__ = [
    "History",
    "HistoryArchive",
    "HistoryRecord",
    "HistoryRecorder",
    "make_archive_id",
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS downloads (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    archive_id    TEXT NOT NULL,
    kind          TEXT NOT NULL,
    title         TEXT,
    url           TEXT,
    filepath      TEXT,
    downloaded_at TEXT NOT NULL,
    UNIQUE (archive_id, kind)
);
CREATE INDEX IF NOT EXISTS idx_downloads_time ON downloads (downloaded_at DESC);
"""


@dataclass
class HistoryRecord:
    id: int
    archive_id: str
    kind: str
    title: str | None
    url: str | None
    filepath: str | None
    downloaded_at: str


def make_archive_id(info: dict[str, Any]) -> str | None:
    """Identifier in yt-dlp's format: ``"<lower-case extractor_key> <id>"``."""
    extractor = info.get("extractor_key") or info.get("ie_key") or info.get("extractor")
    video_id = info.get("id")
    if not extractor or not video_id:
        return None
    return f"{str(extractor).lower()} {video_id}"


class History:
    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else config.HISTORY_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:  # commits/rolls back automatically
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _row(row: sqlite3.Row) -> HistoryRecord:
        return HistoryRecord(**dict(row))

    def add(
        self,
        archive_id: str,
        kind: str,
        title: str | None = None,
        url: str | None = None,
        filepath: str | Path | None = None,
    ) -> HistoryRecord:
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO downloads (archive_id, kind, title, url, filepath, downloaded_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (archive_id, kind) DO UPDATE SET
                    title = excluded.title,
                    url = excluded.url,
                    filepath = excluded.filepath,
                    downloaded_at = excluded.downloaded_at
                """,
                (archive_id, kind, title, url, str(filepath) if filepath else None, now),
            )
        record = self.get(archive_id, kind)
        assert record is not None
        return record

    def get(self, archive_id: str, kind: str) -> HistoryRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM downloads WHERE archive_id = ? AND kind = ?",
                (archive_id, kind),
            ).fetchone()
        return self._row(row) if row else None

    def get_by_id(self, record_id: int) -> HistoryRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM downloads WHERE id = ?", (record_id,)).fetchone()
        return self._row(row) if row else None

    def contains(self, archive_id: str, kind: str) -> bool:
        return self.get(archive_id, kind) is not None

    def list(self, limit: int | None = 100, search: str | None = None) -> list[HistoryRecord]:
        sql = "SELECT * FROM downloads"
        params: list[Any] = []
        if search:
            sql += " WHERE title LIKE ? OR url LIKE ?"
            params += [f"%{search}%", f"%{search}%"]
        sql += " ORDER BY downloaded_at DESC, id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row(r) for r in rows]

    def remove(self, record_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM downloads WHERE id = ?", (record_id,))
        return cur.rowcount > 0

    def clear(self) -> int:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM downloads")
        return cur.rowcount


class HistoryArchive:
    """Set-like object for yt-dlp's ``download_archive`` option.

    yt-dlp only uses ``in`` and ``add``. Recording history is ``HistoryRecorder``'s job
    (it has the title/filepath), so ``add`` does nothing.
    """

    def __init__(
        self,
        history: History,
        kind: str,
        on_skip: Callable[[HistoryRecord], None] | None = None,
    ) -> None:
        self.history = history
        self.kind = kind
        self.on_skip = on_skip

    def __contains__(self, archive_id: object) -> bool:
        if not isinstance(archive_id, str):
            return False
        record = self.history.get(archive_id, self.kind)
        if record is None:
            return False
        if self.on_skip is not None:
            self.on_skip(record)
        return True

    def add(self, archive_id: str) -> None:
        return None

    def __bool__(self) -> bool:  # yt-dlp ignores the archive if it is "empty"
        return True


class HistoryRecorder(PostProcessor):
    """Record history once the file has been moved to its final location."""

    def __init__(self, history: History, kind: str, downloader: Any = None) -> None:
        super().__init__(downloader)
        self.history = history
        self.kind = kind

    def run(self, info: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
        archive_id = make_archive_id(info)
        if archive_id:
            self.history.add(
                archive_id,
                self.kind,
                title=info.get("title"),
                url=info.get("webpage_url") or info.get("original_url"),
                filepath=info.get("filepath"),
            )
        return [], info
