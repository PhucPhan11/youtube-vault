"""Fixture dùng chung: giả lập yt-dlp, không bao giờ gọi mạng."""

from __future__ import annotations

from typing import Any

import pytest

from youtube_vault import downloader


class FakeYoutubeDL:
    """Thay cho yt_dlp.YoutubeDL: ghi lại opts, trả về info giả."""

    instances: list[FakeYoutubeDL] = []
    result: dict[str, Any] | None = None
    error: Exception | None = None
    hook_events: list[dict[str, Any]] = []

    def __init__(self, opts: dict[str, Any]) -> None:
        self.opts = opts
        self.calls: list[tuple[str, bool]] = []
        FakeYoutubeDL.instances.append(self)

    def __enter__(self) -> FakeYoutubeDL:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def extract_info(self, url: str, download: bool = True) -> dict[str, Any] | None:
        self.calls.append((url, download))
        if FakeYoutubeDL.error is not None:
            raise FakeYoutubeDL.error
        for event in FakeYoutubeDL.hook_events:
            for hook in self.opts.get("progress_hooks", []):
                hook(event)
        return FakeYoutubeDL.result


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
