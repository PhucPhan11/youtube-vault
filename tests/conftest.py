"""Fixture dùng chung: giả lập yt-dlp, không bao giờ gọi mạng."""

from __future__ import annotations

from typing import Any

import pytest

from youtube_vault import config, downloader
from youtube_vault.history import History, make_archive_id


class FakeYoutubeDL:
    """Thay cho yt_dlp.YoutubeDL: ghi lại opts, trả về info giả."""

    instances: list[FakeYoutubeDL] = []
    result: dict[str, Any] | None = None
    error: Exception | None = None
    hook_events: list[dict[str, Any]] = []

    def __init__(self, opts: dict[str, Any]) -> None:
        self.opts = opts
        self.calls: list[tuple[str, bool]] = []
        self.post_processors: list[tuple[Any, str]] = []
        FakeYoutubeDL.instances.append(self)

    def __enter__(self) -> FakeYoutubeDL:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def add_post_processor(self, pp: Any, when: str = "post_process") -> None:
        self.post_processors.append((pp, when))

    def extract_info(self, url: str, download: bool = True) -> dict[str, Any] | None:
        self.calls.append((url, download))
        if FakeYoutubeDL.error is not None:
            raise FakeYoutubeDL.error
        result = FakeYoutubeDL.result

        # Giả lập download_archive: video đã có thì bỏ qua, không tải.
        archive = self.opts.get("download_archive")
        if download and archive is not None and result:
            archive_id = make_archive_id(result)
            if archive and archive_id and archive_id in archive:
                return {**result, "requested_downloads": []}

        for event in FakeYoutubeDL.hook_events:
            for hook in self.opts.get("progress_hooks", []):
                hook(event)

        # Giả lập postprocessor "after_move" chạy với từng file đã tải.
        if download and result:
            for req in result.get("requested_downloads") or []:
                for pp, when in self.post_processors:
                    if when == "after_move":
                        pp.run({**result, "filepath": req.get("filepath")})
        return result


@pytest.fixture(autouse=True)
def _isolated_history_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Không bao giờ đụng tới ~/.youtube-vault thật khi chạy test."""
    monkeypatch.setattr(config, "HISTORY_DB", tmp_path / "default-history.db")


@pytest.fixture
def history(tmp_path) -> History:
    return History(tmp_path / "history.db")


@pytest.fixture
def fake_ydl(monkeypatch: pytest.MonkeyPatch) -> type[FakeYoutubeDL]:
    FakeYoutubeDL.instances = []
    FakeYoutubeDL.result = {"id": "abc", "title": "Video", "requested_downloads": []}
    FakeYoutubeDL.error = None
    FakeYoutubeDL.hook_events = []
    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", FakeYoutubeDL)
    return FakeYoutubeDL


@pytest.fixture
def ffmpeg_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(downloader.shutil, "which", lambda name: f"/usr/bin/{name}")


@pytest.fixture
def ffmpeg_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(downloader.shutil, "which", lambda name: None)
