"""Test hàng đợi bằng hàm tải giả (không gọi yt-dlp, không mạng)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from youtube_vault import downloader
from youtube_vault.download_queue import DownloadQueue, ItemStatus
from youtube_vault.downloader import (
    DownloadCancelledError,
    DownloadFailedError,
    FFmpegNotFoundError,
    ProgressEvent,
)


def wait_for(cond: Callable[[], bool], timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return
        time.sleep(0.01)
    raise AssertionError("Hết thời gian chờ điều kiện")


class FakeDownload:
    """Giả lập download_video/download_audio, điều khiển được từ test."""

    def __init__(self) -> None:
        self.gate = threading.Event()  # set → các lượt tải kết thúc thành công
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.calls: list[dict[str, Any]] = []
        self.fail_times = 0  # số lần đầu ném DownloadFailedError
        self.error: Exception | None = None  # ném lỗi này mỗi lần
        self.skip = False
        self.write_part: Path | None = None

    def __call__(self, url: str, **kwargs: Any) -> list[Path]:
        cb = kwargs["progress_callback"]
        cancel: threading.Event = kwargs["cancel_event"]
        with self.lock:
            self.calls.append({"url": url, **kwargs})
            call_no = len(self.calls)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if self.error is not None:
                raise self.error
            if call_no <= self.fail_times:
                raise DownloadFailedError(f"lỗi mạng lần {call_no}")
            if self.skip:
                cb(ProgressEvent("skipped", "old.mp4", title="Đã có"))
                return []
            out = Path(kwargs["output_dir"])
            final = out / f"Clip [{url}].mp4"
            if self.write_part is not None:
                self.write_part.write_bytes(b"x")
            cb(ProgressEvent("downloading", str(final), 1, 10, title=f"Title {url}"))
            while not self.gate.is_set():
                if cancel.is_set():
                    raise DownloadCancelledError("dừng")
                time.sleep(0.005)
            cb(ProgressEvent("finished", str(final), 10, 10, title=f"Title {url}"))
            return [final]
        finally:
            with self.lock:
                self.active -= 1


@pytest.fixture
def fake(monkeypatch) -> FakeDownload:
    f = FakeDownload()
    monkeypatch.setattr(downloader, "download_video", f)
    monkeypatch.setattr(downloader, "download_audio", f)
    return f


@pytest.fixture
def make_queue(tmp_path):
    queues: list[DownloadQueue] = []

    def factory(**kwargs: Any) -> DownloadQueue:
        kwargs.setdefault("retry_delays", (0,))
        q = DownloadQueue(**kwargs)
        queues.append(q)
        return q

    yield factory
    for q in queues:
        q.shutdown(timeout=2)


def test_respects_max_workers(fake, make_queue, tmp_path):
    q = make_queue(max_workers=2)
    items = [q.add(f"v{i}", output_dir=tmp_path) for i in range(4)]

    wait_for(lambda: fake.active == 2)
    time.sleep(0.05)
    assert fake.max_active == 2
    assert [i.status for i in items].count(ItemStatus.QUEUED) == 2

    fake.gate.set()
    wait_for(lambda: all(i.status == ItemStatus.COMPLETED for i in items))
    assert fake.max_active == 2
    assert items[0].files == [tmp_path / "Clip [v0].mp4"]
    assert items[0].title == "Title v0"


def test_pause_and_resume_running(fake, make_queue, tmp_path):
    q = make_queue()
    item = q.add("v1", output_dir=tmp_path)
    wait_for(lambda: item.progress is not None)

    assert q.pause(item.id)
    wait_for(lambda: item.status == ItemStatus.PAUSED)
    wait_for(lambda: q.running_count == 0)

    fake.gate.set()
    assert q.resume(item.id)
    wait_for(lambda: item.status == ItemStatus.COMPLETED)
    assert len(fake.calls) == 2


def test_pause_queued_item(fake, make_queue, tmp_path):
    q = make_queue(max_workers=1)
    first = q.add("a", output_dir=tmp_path)
    second = q.add("b", output_dir=tmp_path)
    wait_for(lambda: first.status == ItemStatus.DOWNLOADING)

    assert q.pause(second.id)
    assert second.status == ItemStatus.PAUSED
    fake.gate.set()
    wait_for(lambda: first.status == ItemStatus.COMPLETED)
    time.sleep(0.05)
    assert second.status == ItemStatus.PAUSED
    assert [c["url"] for c in fake.calls] == ["a"]


def test_cancel_running_removes_part_files(fake, make_queue, tmp_path):
    part = tmp_path / "Clip [v1].mp4.part"
    fake.write_part = part
    q = make_queue()
    item = q.add("v1", output_dir=tmp_path)
    wait_for(lambda: item.progress is not None)
    assert part.exists()

    assert q.cancel(item.id)
    wait_for(lambda: item.status == ItemStatus.CANCELLED)
    assert not part.exists()


def test_pause_keeps_part_files(fake, make_queue, tmp_path):
    part = tmp_path / "Clip [v1].mp4.part"
    fake.write_part = part
    q = make_queue()
    item = q.add("v1", output_dir=tmp_path)
    wait_for(lambda: item.progress is not None)
    q.pause(item.id)
    wait_for(lambda: item.status == ItemStatus.PAUSED)
    assert part.exists()


def test_cancel_queued_item(fake, make_queue, tmp_path):
    q = make_queue(max_workers=1)
    q.add("a", output_dir=tmp_path)
    second = q.add("b", output_dir=tmp_path)
    assert q.cancel(second.id)
    assert second.status == ItemStatus.CANCELLED


def test_transient_error_is_retried(fake, make_queue, tmp_path):
    fake.fail_times = 1
    fake.gate.set()
    q = make_queue(max_retries=2)
    item = q.add("v1", output_dir=tmp_path)
    wait_for(lambda: item.status == ItemStatus.COMPLETED)
    assert item.attempts == 2
    assert item.error is None


def test_permanent_error_fails_after_retries(fake, make_queue, tmp_path):
    fake.fail_times = 99
    q = make_queue(max_retries=2)
    item = q.add("v1", output_dir=tmp_path)
    wait_for(lambda: item.status == ItemStatus.FAILED)
    assert item.attempts == 3
    assert "lỗi mạng" in item.error


def test_failed_item_can_be_retried(fake, make_queue, tmp_path):
    fake.fail_times = 1
    fake.gate.set()
    q = make_queue(max_retries=0)
    item = q.add("v1", output_dir=tmp_path)
    wait_for(lambda: item.status == ItemStatus.FAILED)
    assert q.retry(item.id)
    wait_for(lambda: item.status == ItemStatus.COMPLETED)


def test_ffmpeg_missing_is_not_retried(fake, make_queue, tmp_path):
    fake.error = FFmpegNotFoundError()
    q = make_queue(max_retries=3)
    item = q.add("v1", output_dir=tmp_path)
    wait_for(lambda: item.status == ItemStatus.FAILED)
    assert item.attempts == 1
    assert "ffmpeg" in item.error


def test_pause_while_waiting_to_retry(fake, make_queue, tmp_path):
    fake.fail_times = 99
    q = make_queue(max_retries=3, retry_delays=(30,))
    item = q.add("v1", output_dir=tmp_path)
    wait_for(lambda: item.status == ItemStatus.RETRYING)
    q.pause(item.id)
    wait_for(lambda: item.status == ItemStatus.PAUSED, timeout=2)


def test_set_max_workers_starts_more(fake, make_queue, tmp_path):
    q = make_queue(max_workers=1)
    items = [q.add(f"v{i}", output_dir=tmp_path) for i in range(3)]
    wait_for(lambda: fake.active == 1)
    q.set_max_workers(3)
    wait_for(lambda: fake.active == 3)
    fake.gate.set()
    wait_for(lambda: all(i.status == ItemStatus.COMPLETED for i in items))


def test_set_max_workers_is_clamped(make_queue):
    q = make_queue()
    q.set_max_workers(0)
    assert q.max_workers == 1
    q.set_max_workers(99)
    assert q.max_workers == 5


def test_skipped_item(fake, make_queue, tmp_path):
    fake.skip = True
    q = make_queue()
    item = q.add("v1", output_dir=tmp_path)
    wait_for(lambda: item.status == ItemStatus.SKIPPED)
    assert item.title == "Đã có"


def test_audio_passes_format_and_options(fake, make_queue, tmp_path, history):
    fake.gate.set()
    q = make_queue(history=history)
    item = q.add("v1", kind="audio", audio_format="m4a", output_dir=tmp_path, force=True)
    wait_for(lambda: item.status == ItemStatus.COMPLETED)
    call = fake.calls[0]
    assert call["fmt"] == "m4a"
    assert call["force"] is True
    assert call["history"] is history


def test_video_passes_quality(fake, make_queue, tmp_path):
    fake.gate.set()
    q = make_queue()
    item = q.add("v1", quality="720", output_dir=tmp_path)
    wait_for(lambda: item.status == ItemStatus.COMPLETED)
    assert fake.calls[0]["quality"] == "720"


def test_invalid_kind(make_queue):
    with pytest.raises(ValueError):
        make_queue().add("x", kind="gif")


def test_remove_finished(fake, make_queue, tmp_path):
    fake.gate.set()
    q = make_queue()
    done = q.add("a", output_dir=tmp_path)
    wait_for(lambda: done.status == ItemStatus.COMPLETED)
    fake.gate.clear()
    running = q.add("b", output_dir=tmp_path)
    wait_for(lambda: running.status == ItemStatus.DOWNLOADING)

    assert q.remove_finished() == [done.id]
    assert [i.id for i in q.items()] == [running.id]


def test_on_update_called(fake, make_queue, tmp_path):
    fake.gate.set()
    seen: list[ItemStatus] = []
    q = make_queue(on_update=lambda item: seen.append(item.status))
    item = q.add("a", output_dir=tmp_path)
    wait_for(lambda: item.status == ItemStatus.COMPLETED)
    wait_for(lambda: seen and seen[-1] == ItemStatus.COMPLETED)
    assert ItemStatus.QUEUED in seen
    assert ItemStatus.DOWNLOADING in seen


def test_shutdown_pauses_running(fake, make_queue, tmp_path):
    q = make_queue()
    item = q.add("a", output_dir=tmp_path)
    wait_for(lambda: item.progress is not None)
    q.shutdown(timeout=2)
    assert item.status == ItemStatus.PAUSED
    # sau shutdown không khởi động việc mới
    late = q.add("b", output_dir=tmp_path)
    time.sleep(0.05)
    assert late.status == ItemStatus.QUEUED


def test_audio_options_passed_to_downloader(fake, make_queue, tmp_path):
    fake.gate.set()
    q = make_queue()
    item = q.add(
        "a1",
        kind="audio",
        audio_format="mp3",
        output_dir=tmp_path,
        audio_quality="320",
        embed_metadata=False,
    )
    wait_for(lambda: item.status == ItemStatus.COMPLETED)
    call = fake.calls[0]
    assert call["fmt"] == "mp3"
    assert call["audio_quality"] == "320"
    assert call["embed_metadata"] is False
