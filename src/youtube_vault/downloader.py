"""Logic tải video/audio, gói yt-dlp.

Module này KHÔNG phụ thuộc giao diện (không import typer/rich) để CLI hiện tại
và GUI sau này đều dùng lại được. Tiến độ được báo qua ``progress_callback``.
"""

from __future__ import annotations

import platform
import re
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
    AUDIO_QUALITIES,
    DEFAULT_AUDIO_FORMAT,
    DEFAULT_AUDIO_QUALITY,
    DEFAULT_EMBED_METADATA,
    DEFAULT_OUTPUT_DIR,
    EMBED_THUMBNAIL_FORMATS,
    DEFAULT_QUALITY,
    OUTPUT_TEMPLATE,
    QUALITY_PRESETS,
    VIDEO_FORMAT,
    VIDEO_FORMAT_PREFERENCE,
)

__all__ = [
    "DownloadCancelledError",
    "DownloadFailedError",
    "FFmpegNotFoundError",
    "FormatInfo",
    "MediaInfo",
    "ProgressCallback",
    "ProgressEvent",
    "ResolutionOption",
    "YoutubeVaultError",
    "download_audio",
    "download_video",
    "ffmpeg_install_instructions",
    "format_sort_for",
    "get_info",
    "is_ffmpeg_available",
    "list_resolutions",
    "normalize_quality",
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
    width: int | None = None
    format_note: str | None = None  # YouTube: "1080p60", "720p", ...

    @property
    def has_video(self) -> bool:
        return self.vcodec not in (None, "none")

    @property
    def is_audio_only(self) -> bool:
        return not self.has_video and self.acodec not in (None, "none")


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
        width=f.get("width"),
        format_note=f.get("format_note"),
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
# Độ phân giải
# --------------------------------------------------------------------------- #
_QUALITY_RE = re.compile(r"^(\d+)p?$")
_NOTE_HEIGHT_RE = re.compile(r"^(\d{2,4})p")
_RESOLUTION_TAGS: dict[int, str] = {4320: "8K", 2160: "4K"}


def normalize_quality(quality: str) -> str:
    """Chuẩn hóa chất lượng: ``"best"`` hoặc số dòng dương (``"720p"`` -> ``"720"``).

    Ném ``ValueError`` nếu không hợp lệ.
    """
    q = str(quality).strip().lower()
    if q == "best":
        return q
    match = _QUALITY_RE.match(q)
    if match and int(match.group(1)) > 0:
        return str(int(match.group(1)))
    raise ValueError(
        f"Chất lượng không hợp lệ: {quality!r}. "
        f"Dùng 'best' hoặc số dòng, ví dụ: {', '.join(list(QUALITY_PRESETS)[1:])}."
    )


def format_sort_for(quality: str) -> list[str]:
    """``format_sort`` của yt-dlp cho một mức chất lượng (xem ``config.QUALITY_PRESETS``)."""
    q = normalize_quality(quality)
    preset = QUALITY_PRESETS.get(q)
    return list(preset) if preset else [f"res:{q}", VIDEO_FORMAT_PREFERENCE]


@dataclass
class ResolutionOption:
    """Một mức độ phân giải mà video thực sự có."""

    height: int  # số "p" danh nghĩa (1080, 720...), dùng làm giá trị chất lượng
    fps: float | None = None
    filesize: int | None = None  # ước tính: luồng video lớn nhất ở mức này + audio

    @property
    def quality(self) -> str:
        return str(self.height)

    @property
    def label(self) -> str:
        text = f"{self.height}p"
        if self.fps and self.fps > 30:
            text += str(round(self.fps))
        tag = _RESOLUTION_TAGS.get(self.height)
        return f"{text} ({tag})" if tag else text


def _nominal_height(f: FormatInfo) -> int | None:
    """Số "p" của định dạng: lấy từ format_note của YouTube, nếu không có thì min(rộng, cao)."""
    if f.format_note:
        match = _NOTE_HEIGHT_RE.match(f.format_note.strip())
        if match:
            return int(match.group(1))
    dims = [d for d in (f.width, f.height) if d]
    return min(dims) if dims else None


def _audio_size_estimate(formats: list[FormatInfo]) -> int:
    audio = [f for f in formats if f.is_audio_only and f.filesize]
    preferred = [f for f in audio if f.ext == "m4a"] or audio
    return max((f.filesize or 0 for f in preferred), default=0)


def list_resolutions(media: MediaInfo) -> list[ResolutionOption]:
    """Các độ phân giải video có sẵn, từ cao xuống thấp. Playlist -> ``[]``."""
    if media.is_playlist:
        return []
    groups: dict[int, ResolutionOption] = {}
    for f in media.formats:
        if not f.has_video:
            continue
        height = _nominal_height(f)
        if not height:
            continue
        opt = groups.setdefault(height, ResolutionOption(height=height))
        if f.fps and (opt.fps is None or f.fps > opt.fps):
            opt.fps = f.fps
        if f.filesize and (opt.filesize is None or f.filesize > opt.filesize):
            opt.filesize = f.filesize

    audio_size = _audio_size_estimate(media.formats)
    for opt in groups.values():
        if opt.filesize is not None:
            opt.filesize += audio_size
    return sorted(groups.values(), key=lambda o: o.height, reverse=True)


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

    - ``quality``: ``"best"`` hoặc số dòng (``"1080"``, ``"720p"``, ``"804"``...). Lấy độ
      phân giải lớn nhất không vượt quá mức đó; nếu video không có thì lấy mức nhỏ nhất.
    - ``history``: nếu có, ghi lịch sử và bỏ qua video đã tải (trừ khi ``force``).
      Video bị bỏ qua được báo qua callback với ``status="skipped"``.
    - ``cancel_event``: set event này để dừng → ``DownloadCancelledError``.

    Trả về danh sách đường dẫn các file đã lưu.
    """
    format_sort = format_sort_for(quality)  # ValueError nếu chất lượng không hợp lệ
    _require_ffmpeg()
    out = Path(output_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)

    opts = _base_opts(out, progress_callback, cancel_event)
    opts["format"] = VIDEO_FORMAT
    opts["format_sort"] = format_sort
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
    audio_quality: str = DEFAULT_AUDIO_QUALITY,
    embed_metadata: bool = DEFAULT_EMBED_METADATA,
) -> list[Path]:
    """Chỉ tải audio và chuyển sang ``fmt`` (mặc định mp3). Hỗ trợ URL playlist.

    ``history``/``force``/``cancel_event``: như ``download_video``.
    ``audio_quality``: ``"best"`` (VBR cao nhất) hoặc bitrate kbps, xem ``AUDIO_QUALITIES``
    (không áp dụng cho wav).
    ``embed_metadata``: gắn tiêu đề/nghệ sĩ/ngày đăng vào file; với mp3/m4a gắn thêm
    thumbnail làm ảnh bìa.
    """
    if fmt not in AUDIO_FORMATS:
        raise ValueError(
            f"Định dạng audio không hợp lệ: {fmt!r}. Chọn một trong: {', '.join(AUDIO_FORMATS)}"
        )
    if audio_quality not in AUDIO_QUALITIES:
        raise ValueError(
            f"Bitrate không hợp lệ: {audio_quality!r}. "
            f"Chọn một trong: {', '.join(AUDIO_QUALITIES)}"
        )
    _require_ffmpeg()
    out = Path(output_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)

    opts = _base_opts(out, progress_callback, cancel_event)
    opts["format"] = "bestaudio/best"
    preferred = "0" if audio_quality == "best" else audio_quality
    postprocessors: list[dict[str, Any]] = [
        {"key": "FFmpegExtractAudio", "preferredcodec": fmt, "preferredquality": preferred},
    ]
    if embed_metadata:
        postprocessors.append({"key": "FFmpegMetadata", "add_metadata": True})
        if fmt in EMBED_THUMBNAIL_FORMATS:
            opts["writethumbnail"] = True
            postprocessors.append({"key": "EmbedThumbnail", "already_have_thumbnail": False})
    opts["postprocessors"] = postprocessors
    return _run(
        url,
        opts,
        kind="audio",
        progress_callback=progress_callback,
        history=history,
        force=force,
        cancel_event=cancel_event,
    )
