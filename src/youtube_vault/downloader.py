"""Logic tải video/audio, gói yt-dlp.

Module này KHÔNG phụ thuộc giao diện (không import typer/rich) để CLI hiện tại
và GUI sau này đều dùng lại được. Tiến độ được báo qua ``progress_callback``.
"""

from __future__ import annotations

import platform
import shutil
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yt_dlp
from yt_dlp.utils import DownloadCancelled, DownloadError

from .history import History, HistoryArchive, HistoryRecord, HistoryRecorder
from .config import (
    AUDIO_FORMATS,
    DEFAULT_AUDIO_FORMAT,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_QUALITY,
    OUTPUT_TEMPLATE,
    QUALITY_PRESETS,
)

__all__ = [
    "DownloadCancelledError",
    "DownloadFailedError",
    "FFmpegNotFoundError",
    "FormatInfo",
    "MediaInfo",
    "ProgressCallback",
    "ProgressEvent",
    "YoutubeVaultError",
    "download_audio",
    "download_video",
    "ffmpeg_install_instructions",
    "get_info",
    "is_ffmpeg_available",
]


# --------------------------------------------------------------------------- #
# Lỗi
# --------------------------------------------------------------------------- #
class YoutubeVaultError(Exception):
    """Lỗi gốc của youtube-vault."""


class FFmpegNotFoundError(YoutubeVaultError):
    """Máy chưa cài ffmpeg (cần để ghép video+audio hoặc chuyển đổi audio)."""

    def __init__(self) -> None:
        super().__init__("Không tìm thấy ffmpeg trên máy.")
        self.instructions = ffmpeg_install_instructions()


class DownloadFailedError(YoutubeVaultError):
    """yt-dlp báo lỗi khi lấy thông tin hoặc tải."""


class DownloadCancelledError(YoutubeVaultError):
    """Việc tải bị dừng theo yêu cầu (qua ``cancel_event``)."""


# --------------------------------------------------------------------------- #
# Tiến độ
# --------------------------------------------------------------------------- #
@dataclass
class ProgressEvent:
    """Trạng thái tiến độ của một file, độc lập với yt-dlp."""

    status: str  # "downloading" | "finished" | "error" | "skipped"
    filename: str
    downloaded_bytes: int = 0
    total_bytes: int | None = None
    speed: float | None = None  # byte/giây
    eta: int | None = None  # giây
    playlist_index: int | None = None
    playlist_count: int | None = None
    title: str | None = None

    @property
    def percent(self) -> float | None:
        if not self.total_bytes:
            return None
        return min(100.0, self.downloaded_bytes * 100.0 / self.total_bytes)


ProgressCallback = Callable[[ProgressEvent], None]


def _to_progress_event(d: dict[str, Any]) -> ProgressEvent:
    info = d.get("info_dict") or {}
    return ProgressEvent(
        status=d.get("status", "downloading"),
        filename=str(d.get("filename") or d.get("tmpfilename") or ""),
        downloaded_bytes=int(d.get("downloaded_bytes") or 0),
        total_bytes=d.get("total_bytes") or d.get("total_bytes_estimate"),
        speed=d.get("speed"),
        eta=d.get("eta"),
        playlist_index=info.get("playlist_index"),
        playlist_count=info.get("n_entries") or info.get("playlist_count"),
        title=info.get("title"),
    )


def _make_hook(
    callback: ProgressCallback | None,
    cancel_event: threading.Event | None = None,
) -> Callable[[dict[str, Any]], None]:
    def hook(d: dict[str, Any]) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise DownloadCancelled("Đã dừng theo yêu cầu")
        if callback is not None:
            callback(_to_progress_event(d))

    return hook


# --------------------------------------------------------------------------- #
# ffmpeg
# --------------------------------------------------------------------------- #
def is_ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def ffmpeg_install_instructions() -> str:
    """Hướng dẫn cài ffmpeg theo hệ điều hành hiện tại."""
    system = platform.system()
    if system == "Windows":
        steps = (
            "Windows:\n"
            "  • winget install Gyan.FFmpeg\n"
            "  • hoặc: choco install ffmpeg   (Chocolatey)\n"
            "  • hoặc: scoop install ffmpeg   (Scoop)\n"
            "  Sau khi cài, mở lại terminal để PATH được cập nhật."
        )
    elif system == "Darwin":
        steps = "macOS:\n  • brew install ffmpeg"
    elif system == "Linux":
        steps = (
            "Linux:\n"
            "  • Ubuntu/Debian: sudo apt install ffmpeg\n"
            "  • Fedora:        sudo dnf install ffmpeg\n"
            "  • Arch:          sudo pacman -S ffmpeg"
        )
    else:
        steps = f"Hệ điều hành {system or 'không xác định'}: cài ffmpeg bằng trình quản lý gói của bạn."
    return (
        "youtube-vault cần ffmpeg để ghép video + audio và chuyển đổi định dạng audio.\n\n"
        f"{steps}\n\n"
        "Tải thủ công: https://ffmpeg.org/download.html"
    )


def _require_ffmpeg() -> None:
    if not is_ffmpeg_available():
        raise FFmpegNotFoundError()


# --------------------------------------------------------------------------- #
# Thông tin video
# --------------------------------------------------------------------------- #
@dataclass
class FormatInfo:
    format_id: str
    ext: str | None = None
    resolution: str | None = None
    height: int | None = None
    fps: float | None = None
    vcodec: str | None = None
    acodec: str | None = None
    filesize: int | None = None


@dataclass
class MediaInfo:
    id: str | None
    title: str | None
    duration: float | None = None
    uploader: str | None = None
    url: str | None = None
    is_playlist: bool = False
    formats: list[FormatInfo] = field(default_factory=list)
    entries: list[MediaInfo] = field(default_factory=list)


def _parse_format(f: dict[str, Any]) -> FormatInfo:
    return FormatInfo(
        format_id=str(f.get("format_id", "")),
        ext=f.get("ext"),
        resolution=f.get("resolution"),
        height=f.get("height"),
        fps=f.get("fps"),
        vcodec=f.get("vcodec"),
        acodec=f.get("acodec"),
        filesize=f.get("filesize") or f.get("filesize_approx"),
    )


def _parse_info(info: dict[str, Any]) -> MediaInfo:
    is_playlist = info.get("_type") == "playlist" or "entries" in info
    entries = [_parse_info(e) for e in (info.get("entries") or []) if e]
    return MediaInfo(
        id=info.get("id"),
        title=info.get("title"),
        duration=info.get("duration"),
        uploader=info.get("uploader") or info.get("channel"),
        url=info.get("webpage_url") or info.get("url"),
        is_playlist=is_playlist,
        formats=[_parse_format(f) for f in (info.get("formats") or [])],
        entries=entries,
    )


def get_info(url: str) -> MediaInfo:
    """Lấy tiêu đề, thời lượng, các định dạng có sẵn — không tải."""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except DownloadError as exc:
        raise DownloadFailedError(str(exc)) from exc
    if not info:
        raise DownloadFailedError(f"Không lấy được thông tin cho: {url}")
    return _parse_info(info)


# --------------------------------------------------------------------------- #
# Tải
# --------------------------------------------------------------------------- #
def _base_opts(
    output_dir: Path,
    progress_callback: ProgressCallback | None,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "outtmpl": str(output_dir / OUTPUT_TEMPLATE),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": False,
    }
    if progress_callback is not None or cancel_event is not None:
        opts["progress_hooks"] = [_make_hook(progress_callback, cancel_event)]
    return opts


def _iter_filepaths(info: dict[str, Any] | None) -> Iterator[Path]:
    if not info:
        return
    for entry in info.get("entries") or []:
        yield from _iter_filepaths(entry)
    for req in info.get("requested_downloads") or []:
        path = req.get("filepath") or req.get("filename")
        if path:
            yield Path(path)


def _run(
    url: str,
    opts: dict[str, Any],
    *,
    kind: str,
    progress_callback: ProgressCallback | None,
    history: History | None,
    force: bool,
    cancel_event: threading.Event | None,
) -> list[Path]:
    if cancel_event is not None and cancel_event.is_set():
        raise DownloadCancelledError("Đã dừng theo yêu cầu")

    if history is not None and not force:

        def on_skip(record: HistoryRecord) -> None:
            if progress_callback is not None:
                progress_callback(
                    ProgressEvent(
                        status="skipped",
                        filename=record.filepath or "",
                        title=record.title,
                    )
                )

        opts["download_archive"] = HistoryArchive(history, kind, on_skip=on_skip)

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            if history is not None:
                ydl.add_post_processor(HistoryRecorder(history, kind), when="after_move")
            info = ydl.extract_info(url, download=True)
    except DownloadCancelled as exc:
        raise DownloadCancelledError(str(exc)) from exc
    except DownloadError as exc:
        if cancel_event is not None and cancel_event.is_set():
            raise DownloadCancelledError("Đã dừng theo yêu cầu") from exc
        raise DownloadFailedError(str(exc)) from exc
    return list(dict.fromkeys(_iter_filepaths(info)))


def download_video(
    url: str,
    quality: str = DEFAULT_QUALITY,
    output_dir: Path | str = DEFAULT_OUTPUT_DIR,
    progress_callback: ProgressCallback | None = None,
    *,
    history: History | None = None,
    force: bool = False,
    cancel_event: threading.Event | None = None,
) -> list[Path]:
    """Tải video mp4 (ghép video + audio). Hỗ trợ URL playlist.

    - ``history``: nếu có, ghi lịch sử và bỏ qua video đã tải (trừ khi ``force``).
      Video bị bỏ qua được báo qua callback với ``status="skipped"``.
    - ``cancel_event``: set event này để dừng → ``DownloadCancelledError``.

    Trả về danh sách đường dẫn các file đã lưu.
    """
    if quality not in QUALITY_PRESETS:
        raise ValueError(
            f"Chất lượng không hợp lệ: {quality!r}. Chọn một trong: {', '.join(QUALITY_PRESETS)}"
        )
    _require_ffmpeg()
    out = Path(output_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)

    opts = _base_opts(out, progress_callback, cancel_event)
    opts["format"] = QUALITY_PRESETS[quality]
    opts["merge_output_format"] = "mp4"
    return _run(
        url,
        opts,
        kind="video",
        progress_callback=progress_callback,
        history=history,
        force=force,
        cancel_event=cancel_event,
    )


def download_audio(
    url: str,
    fmt: str = DEFAULT_AUDIO_FORMAT,
    output_dir: Path | str = DEFAULT_OUTPUT_DIR,
    progress_callback: ProgressCallback | None = None,
    *,
    history: History | None = None,
    force: bool = False,
    cancel_event: threading.Event | None = None,
) -> list[Path]:
    """Chỉ tải audio và chuyển sang ``fmt`` (mặc định mp3). Hỗ trợ URL playlist.

    ``history``/``force``/``cancel_event``: như ``download_video``.
    """
    if fmt not in AUDIO_FORMATS:
        raise ValueError(
            f"Định dạng audio không hợp lệ: {fmt!r}. Chọn một trong: {', '.join(AUDIO_FORMATS)}"
        )
    _require_ffmpeg()
    out = Path(output_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)

    opts = _base_opts(out, progress_callback, cancel_event)
    opts["format"] = "bestaudio/best"
    opts["postprocessors"] = [
        {"key": "FFmpegExtractAudio", "preferredcodec": fmt, "preferredquality": "0"},
    ]
    return _run(
        url,
        opts,
        kind="audio",
        progress_callback=progress_callback,
        history=history,
        force=force,
        cancel_event=cancel_event,
    )
