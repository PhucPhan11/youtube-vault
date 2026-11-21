"""Hàng đợi tải: chạy song song có giới hạn số luồng, tạm dừng/tiếp tục/hủy, tự thử lại.

Module này KHÔNG phụ thuộc giao diện. ``on_update(item)`` được gọi từ luồng tải,
nên giao diện phải tự chuyển việc cập nhật về luồng chính của nó.

- Tạm dừng: dừng tải nhưng giữ file ``.part``; tiếp tục thì yt-dlp tải nối tiếp.
- Hủy: dừng tải và xóa các file ``.part`` / ``.ytdl`` của item.
- Bước ghép/chuyển đổi bằng ffmpeg không ngắt được giữa chừng; lệnh dừng có
  hiệu lực sau bước đó.
"""

from __future__ import annotations

import glob
import itertools
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from . import downloader
from .config import (
    DEFAULT_AUDIO_FORMAT,
    DEFAULT_AUDIO_QUALITY,
    DEFAULT_EMBED_METADATA,
    DEFAULT_MAX_RETRIES,
    DEFAULT_MAX_WORKERS,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_QUALITY,
    MAX_WORKERS_LIMIT,
    RETRY_DELAYS,
)
from .downloader import (
    Cookies,
    DownloadCancelledError,
    DownloadFailedError,
    FFmpegNotFoundError,
    ProgressEvent,
    YoutubeVaultError,
)
from .history import History

__all__ = ["DownloadItem", "DownloadQueue", "ItemStatus"]


class ItemStatus(str, Enum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    RETRYING = "retrying"
    PAUSED = "paused"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"
    CANCELLED = "cancelled"


RUNNING_STATUSES = frozenset({ItemStatus.DOWNLOADING, ItemStatus.RETRYING})
RESUMABLE_STATUSES = frozenset({ItemStatus.PAUSED, ItemStatus.FAILED, ItemStatus.CANCELLED})
FINISHED_STATUSES = frozenset({ItemStatus.COMPLETED, ItemStatus.SKIPPED, ItemStatus.CANCELLED})


@dataclass(eq=False)
class DownloadItem:
    id: int
    url: str
    kind: str = "video"  # "video" | "audio"
    quality: str = DEFAULT_QUALITY
    audio_format: str = DEFAULT_AUDIO_FORMAT
    audio_quality: str = DEFAULT_AUDIO_QUALITY  # bitrate: "best" hoặc kbps
    embed_metadata: bool = DEFAULT_EMBED_METADATA
    cookies: Cookies | None = None  # cookie đăng nhập (Facebook riêng tư/nhóm...)
    output_dir: Path = DEFAULT_OUTPUT_DIR
    force: bool = False
    status: ItemStatus = ItemStatus.QUEUED
    title: str | None = None
    progress: ProgressEvent | None = None
    attempts: int = 0
    error: str | None = None
    files: list[Path] = field(default_factory=list)
    skipped: bool = False
    _cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    _stop_reason: str | None = field(default=None, repr=False)  # "pause" | "cancel"
    _seen_files: set[str] = field(default_factory=set, repr=False)

    @property
    def display_title(self) -> str:
        return self.title or self.url


def _clamp_workers(n: int) -> int:
    return max(1, min(MAX_WORKERS_LIMIT, int(n)))


class DownloadQueue:
    def __init__(
        self,
        max_workers: int = DEFAULT_MAX_WORKERS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        history: History | None = None,
        on_update: Callable[[DownloadItem], None] | None = None,
        retry_delays: tuple[float, ...] = RETRY_DELAYS,
    ) -> None:
        self.max_retries = max(0, max_retries)
        self.history = history
        self.on_update = on_update
        self.retry_delays = retry_delays
        self._max_workers = _clamp_workers(max_workers)
        self._lock = threading.RLock()
        self._items: dict[int, DownloadItem] = {}
        self._pending: deque[int] = deque()
        self._threads: dict[int, threading.Thread] = {}
        self._ids = itertools.count(1)
        self._closed = False

    # ------------------------------------------------------------------ #
    # Truy vấn
    # ------------------------------------------------------------------ #
    @property
    def max_workers(self) -> int:
        return self._max_workers

    def items(self) -> list[DownloadItem]:
        with self._lock:
            return sorted(self._items.values(), key=lambda i: i.id)

    def get(self, item_id: int) -> DownloadItem | None:
        return self._items.get(item_id)

    @property
    def running_count(self) -> int:
        with self._lock:
            return len(self._threads)

    # ------------------------------------------------------------------ #
    # Điều khiển
    # ------------------------------------------------------------------ #
    def add(
        self,
        url: str,
        kind: str = "video",
        quality: str = DEFAULT_QUALITY,
        audio_format: str = DEFAULT_AUDIO_FORMAT,
        output_dir: Path | str = DEFAULT_OUTPUT_DIR,
        force: bool = False,
        audio_quality: str = DEFAULT_AUDIO_QUALITY,
        embed_metadata: bool = DEFAULT_EMBED_METADATA,
        cookies: Cookies | None = None,
    ) -> DownloadItem:
        if kind not in ("video", "audio"):
            raise ValueError(f"Loại không hợp lệ: {kind!r}")
        item = DownloadItem(
            id=next(self._ids),
            url=url,
            kind=kind,
            quality=quality,
            audio_format=audio_format,
            audio_quality=audio_quality,
            embed_metadata=embed_metadata,
            cookies=cookies,
            output_dir=Path(output_dir).expanduser(),
            force=force,
        )
        with self._lock:
            self._items[item.id] = item
            self._pending.append(item.id)
        self._notify(item)
        self._maybe_start()
        return item

    def set_max_workers(self, n: int) -> None:
        with self._lock:
            self._max_workers = _clamp_workers(n)
        self._maybe_start()

    def pause(self, item_id: int) -> bool:
        return self._stop(item_id, "pause")

    def cancel(self, item_id: int) -> bool:
        return self._stop(item_id, "cancel")

    def resume(self, item_id: int) -> bool:
        with self._lock:
            item = self._items.get(item_id)
            if item is None or item.status not in RESUMABLE_STATUSES or item_id in self._threads:
                return False
            item.status = ItemStatus.QUEUED
            item.attempts = 0
            item.error = None
            item._stop_reason = None
            self._pending.append(item_id)
        self._notify(item)
        self._maybe_start()
        return True

    retry = resume

    def remove_finished(self) -> list[int]:
        with self._lock:
            ids = [i.id for i in self._items.values() if i.status in FINISHED_STATUSES]
            for item_id in ids:
                del self._items[item_id]
        return ids

    def shutdown(self, timeout: float = 5.0) -> None:
        """Dừng nhận việc mới, tạm dừng các item đang tải (giữ .part), chờ luồng kết thúc."""
        with self._lock:
            self._closed = True
            threads = list(self._threads.items())
            for item_id, _ in threads:
                item = self._items.get(item_id)
                if item is not None:
                    item._stop_reason = "pause"
                    item._cancel_event.set()
        for _, thread in threads:
            thread.join(timeout)

    # ------------------------------------------------------------------ #
    # Nội bộ
    # ------------------------------------------------------------------ #
    def _notify(self, item: DownloadItem) -> None:
        if self.on_update is not None:
            try:
                self.on_update(item)
            except Exception:  # lỗi ở UI không được làm chết luồng tải
                pass

    def _stop(self, item_id: int, reason: str) -> bool:
        notify = False
        with self._lock:
            item = self._items.get(item_id)
            if item is None:
                return False
            if item.status in RUNNING_STATUSES:
                item._stop_reason = reason
                item._cancel_event.set()
                return True
            if item.status == ItemStatus.QUEUED:
                try:
                    self._pending.remove(item_id)
                except ValueError:
                    pass
                item.status = ItemStatus.PAUSED if reason == "pause" else ItemStatus.CANCELLED
                notify = True
            elif reason == "cancel" and item.status in (ItemStatus.PAUSED, ItemStatus.FAILED):
                item.status = ItemStatus.CANCELLED
                self._cleanup_partials(item)
                notify = True
        if notify:
            self._notify(item)
        return notify

    def _maybe_start(self) -> None:
        started: list[DownloadItem] = []
        with self._lock:
            if self._closed:
                return
            while self._pending and len(self._threads) < self._max_workers:
                item = self._items.get(self._pending.popleft())
                if item is None or item.status != ItemStatus.QUEUED:
                    continue
                item.status = ItemStatus.DOWNLOADING
                item._cancel_event = threading.Event()
                item._stop_reason = None
                thread = threading.Thread(
                    target=self._worker, args=(item,), daemon=True, name=f"yv-download-{item.id}"
                )
                self._threads[item.id] = thread
                started.append(item)
                thread.start()
        for item in started:
            self._notify(item)

    def _worker(self, item: DownloadItem) -> None:
        final = ItemStatus.FAILED
        try:
            final = self._run_item(item)
        except Exception as exc:  # phòng hờ: không để luồng chết im lặng
            item.error = f"Lỗi không mong muốn: {exc}"
        finally:
            # Đặt trạng thái cuối và gỡ luồng cùng lúc, để resume/retry ngay sau đó hợp lệ.
            with self._lock:
                item.status = final
                self._threads.pop(item.id, None)
            if final == ItemStatus.CANCELLED:
                self._cleanup_partials(item)
            self._notify(item)
            self._maybe_start()

    def _progress_callback(self, item: DownloadItem) -> Callable[[ProgressEvent], None]:
        def callback(event: ProgressEvent) -> None:
            if event.title:
                item.title = event.title
            if event.status == "skipped":
                item.skipped = True
            elif event.filename:
                item._seen_files.add(event.filename)
            item.progress = event
            self._notify(item)

        return callback

    def _run_item(self, item: DownloadItem) -> ItemStatus:
        """Chạy item (kèm thử lại); trả về trạng thái cuối, không tự gán."""
        callback = self._progress_callback(item)
        while True:
            item.attempts += 1
            try:
                if item.kind == "audio":
                    files = downloader.download_audio(
                        item.url,
                        fmt=item.audio_format,
                        output_dir=item.output_dir,
                        progress_callback=callback,
                        history=self.history,
                        force=item.force,
                        cancel_event=item._cancel_event,
                        audio_quality=item.audio_quality,
                        embed_metadata=item.embed_metadata,
                        cookies=item.cookies,
                    )
                else:
                    files = downloader.download_video(
                        item.url,
                        quality=item.quality,
                        output_dir=item.output_dir,
                        progress_callback=callback,
                        history=self.history,
                        force=item.force,
                        cancel_event=item._cancel_event,
                        cookies=item.cookies,
                    )
            except DownloadCancelledError:
                return self._stop_status(item)
            except DownloadFailedError as exc:
                if item._cancel_event.is_set():
                    return self._stop_status(item)
                retry_no = item.attempts  # lần thử lại thứ mấy (1-based)
                if retry_no > self.max_retries:
                    item.error = str(exc)
                    return ItemStatus.FAILED
                item.status = ItemStatus.RETRYING
                item.error = str(exc)
                self._notify(item)
                if item._cancel_event.wait(self._retry_delay(retry_no)):
                    return self._stop_status(item)
                item.status = ItemStatus.DOWNLOADING
                self._notify(item)
                continue
            except (YoutubeVaultError, ValueError) as exc:
                # Lỗi không thể tự khỏi (thiếu ffmpeg, tham số sai): không thử lại.
                item.error = str(exc)
                if isinstance(exc, FFmpegNotFoundError):
                    item.error += "\n" + exc.instructions
                return ItemStatus.FAILED

            item.files = files
            item.error = None
            return ItemStatus.SKIPPED if not files and item.skipped else ItemStatus.COMPLETED

    def _retry_delay(self, retry_no: int) -> float:
        if not self.retry_delays:
            return 0
        return self.retry_delays[min(retry_no, len(self.retry_delays)) - 1]

    @staticmethod
    def _stop_status(item: DownloadItem) -> ItemStatus:
        return ItemStatus.CANCELLED if item._stop_reason == "cancel" else ItemStatus.PAUSED

    @staticmethod
    def _cleanup_partials(item: DownloadItem) -> None:
        for name in item._seen_files:
            path = Path(name)
            pattern = glob.escape(path.name)
            for leftover in [
                *path.parent.glob(pattern + ".part*"),
                *path.parent.glob(pattern + ".ytdl"),
            ]:
                try:
                    leftover.unlink()
                except OSError:
                    pass
