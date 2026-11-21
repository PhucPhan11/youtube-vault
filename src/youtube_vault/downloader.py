"""Video/audio download logic wrapping yt-dlp.

This module does NOT depend on any UI (no typer/rich/tkinter imports), so both the CLI
and the GUI reuse it. Progress is reported through ``progress_callback``.

Exception messages and hints are user-visible, so they stay Vietnamese.
"""

from __future__ import annotations

import glob
import platform
import re
import shutil
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yt_dlp
from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.utils import DownloadCancelled, YoutubeDLError

from . import config
from .history import History, HistoryArchive, HistoryRecord, HistoryRecorder
from .config import (
    AUDIO_FORMATS,
    AUDIO_SOURCE_MARKER,
    AUDIO_SOURCE_TEMPLATE,
    COOKIES_BROWSERS,
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
    "Cookies",
    "DownloadCancelledError",
    "DownloadFailedError",
    "FFmpegNotFoundError",
    "FormatInfo",
    "MediaInfo",
    "NoAudioStreamError",
    "ProgressCallback",
    "ProgressEvent",
    "ResolutionOption",
    "YoutubeVaultError",
    "download_audio",
    "download_video",
    "ffmpeg_install_instructions",
    "format_sort_for",
    "friendly_error",
    "get_info",
    "is_ffmpeg_available",
    "list_resolutions",
    "list_supported_sites",
    "normalize_quality",
    "site_name",
]


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class YoutubeVaultError(Exception):
    """Base error of youtube-vault."""


class FFmpegNotFoundError(YoutubeVaultError):
    """ffmpeg is not installed (needed to merge video+audio or convert audio)."""

    def __init__(self) -> None:
        super().__init__("Không tìm thấy ffmpeg trên máy.")
        self.instructions = ffmpeg_install_instructions()


class DownloadFailedError(YoutubeVaultError):
    """yt-dlp reported an error while fetching info or downloading."""


class DownloadCancelledError(YoutubeVaultError):
    """The download was stopped on request (via ``cancel_event``)."""


class NoAudioStreamError(YoutubeVaultError):
    """The video has no sound, so no audio can be extracted (never retried)."""


_LOGIN_HINT = (
    "Gợi ý: video này có thể cần đăng nhập {site}. Xuất cookie của trình duyệt đang đăng nhập ra "
    "file cookies.txt rồi lưu vào ~/.youtube-vault/cookies.txt hoặc dùng --cookies <file> "
    "(xem README)."
)
_BROWSER_COOKIE_HINT = (
    "Gợi ý: không đọc được cookie trực tiếp từ trình duyệt (Chrome/Edge mới mã hóa cookie và khóa "
    "file khi đang mở). Hãy xuất cookie ra file cookies.txt và dùng --cookies <file>, "
    "hoặc dùng --cookies-from-browser firefox."
)
# "Login needed" signals that yt-dlp reports on many sites (Instagram, X, YouTube, Facebook...).
_LOGIN_PATTERNS = (
    "login", "log in", "logged in", "sign in", "registered users", "use --cookies",
    "cookies-from-browser", "requires authentication", "authentication required",
    "rate-limit", "rate limit", "nsfw", "age-restricted", "age restricted",
    "private video", "protected",
)
# Facebook reports missing login with vaguer messages.
_FACEBOOK_LOGIN_PATTERNS = ("cannot parse data", "not available")
_BROWSER_COOKIE_PATTERNS = (
    "dpapi", "could not copy chrome cookie database", "app-bound", "app bound",
    "failed to decrypt", "could not find", "cookie database",
)
_SITE_TAG_RE = re.compile(r"\[([a-z0-9]+)(?::[^\]]*)?\]", re.IGNORECASE)
_NO_AUDIO_PATTERNS = ("unable to obtain file audio codec", "does not contain any stream")


def site_name(extractor: str) -> str:
    """Readable site name from a yt-dlp extractor name (``"twitter:amplify"`` -> ``"X (Twitter)"``)."""
    key = extractor.split(":", 1)[0].strip().lower()
    return config.SITE_NAMES.get(key, key.capitalize())


def _site_from_message(message: str) -> str | None:
    for match in _SITE_TAG_RE.finditer(message):
        key = match.group(1).lower()
        if key not in ("download", "info", "error", "generic"):
            return site_name(key)
    return None


def friendly_error(message: str) -> str:
    """Append a Vietnamese hint to common login/cookie errors (Instagram, X, Facebook, Chrome...)."""
    low = message.lower()
    if "cookie" in low and any(p in low for p in _BROWSER_COOKIE_PATTERNS):
        return f"{message}\n{_BROWSER_COOKIE_HINT}"
    site = _site_from_message(message)
    needs_login = any(p in low for p in _LOGIN_PATTERNS) or (
        "facebook" in low and any(p in low for p in _FACEBOOK_LOGIN_PATTERNS)
    )
    if needs_login and (site is not None or "facebook" in low):
        return f"{message}\n{_LOGIN_HINT.format(site=site or 'Facebook')}"
    return message


class _SilentLogger:
    """Logger for yt-dlp that prints nothing. Errors still travel as exceptions and are
    shown once, in Vietnamese, by the CLI/GUI."""

    def debug(self, msg: str) -> None:
        pass

    info = warning = error = debug


def _error_message(exc: YoutubeDLError) -> str:
    """yt-dlp's error message; cookie-loading errors hide the real cause in ``__context__``."""
    msg = str(exc)
    cause = exc.__context__
    if cause is not None and not isinstance(exc, DownloadCancelled) and str(cause) not in msg:
        msg = f"{msg}: {cause}" if msg else str(cause)
    return friendly_error(msg)


# --------------------------------------------------------------------------- #
# Login cookies
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Cookies:
    """Source of login cookies: a cookies.txt file OR a browser name."""

    file: Path | None = None
    browser: str | None = None

    @classmethod
    def resolve(
        cls, file: Path | str | None = None, browser: str | None = None
    ) -> Cookies | None:
        """Pick the cookie source: ``file``/``browser`` if given, else the default file if it exists.

        Raises ``ValueError`` if the file does not exist or the browser is not supported.
        """
        if file and browser:
            raise ValueError("Chỉ chọn một nguồn cookie: file cookies.txt hoặc trình duyệt.")
        if browser:
            name = browser.strip().lower()
            if name not in COOKIES_BROWSERS:
                raise ValueError(
                    f"Trình duyệt không được hỗ trợ: {browser!r}. "
                    f"Chọn một trong: {', '.join(COOKIES_BROWSERS)}"
                )
            return cls(browser=name)
        if file:
            path = Path(file).expanduser()
            if not path.is_file():
                raise ValueError(f"Không tìm thấy file cookie: {path}")
            return cls(file=path)
        default = Path(config.DEFAULT_COOKIES_FILE)  # read at call time so tests can redirect it
        return cls(file=default) if default.is_file() else None

    def apply(self, opts: dict[str, Any]) -> None:
        if self.file is not None:
            opts["cookiefile"] = str(self.file)
        elif self.browser is not None:
            opts["cookiesfrombrowser"] = (self.browser,)

    def describe(self) -> str:
        return f"file {self.file}" if self.file is not None else f"trình duyệt {self.browser}"


# --------------------------------------------------------------------------- #
# Progress
# --------------------------------------------------------------------------- #
@dataclass
class ProgressEvent:
    """Progress state of one file, independent of yt-dlp."""

    status: str  # "downloading" | "finished" | "error" | "skipped"
    filename: str
    downloaded_bytes: int = 0
    total_bytes: int | None = None
    speed: float | None = None  # bytes/second
    eta: int | None = None  # seconds
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
    """ffmpeg installation instructions for the current OS (user-visible, Vietnamese)."""
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
# Video info
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


def get_info(url: str, *, cookies: Cookies | None = None) -> MediaInfo:
    """Fetch the title, duration and available formats, without downloading.

    ``cookies``: login cookies (private/group Facebook videos...), see ``Cookies.resolve``.
    """
    opts: dict[str, Any] = {
        "quiet": True,
        "logger": _SilentLogger(),
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
    }
    if cookies is not None:
        cookies.apply(opts)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except YoutubeDLError as exc:
        raise DownloadFailedError(_error_message(exc)) from exc
    if not info:
        raise DownloadFailedError(f"Không lấy được thông tin cho: {url}")
    return _parse_info(info)


# --------------------------------------------------------------------------- #
# Resolutions
# --------------------------------------------------------------------------- #
_QUALITY_RE = re.compile(r"^(\d+)p?$")
_NOTE_HEIGHT_RE = re.compile(r"^(\d{2,4})p")
_RESOLUTION_TAGS: dict[int, str] = {4320: "8K", 2160: "4K"}


def normalize_quality(quality: str) -> str:
    """Normalise a quality: ``"best"`` or a positive line count (``"720p"`` -> ``"720"``).

    Raises ``ValueError`` if it is invalid.
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
    """yt-dlp ``format_sort`` for a quality level (see ``config.QUALITY_PRESETS``)."""
    q = normalize_quality(quality)
    preset = QUALITY_PRESETS.get(q)
    return list(preset) if preset else [f"res:{q}", VIDEO_FORMAT_PREFERENCE]


@dataclass
class ResolutionOption:
    """One resolution that the video actually has."""

    height: int  # nominal "p" number (1080, 720...), used as the quality value
    fps: float | None = None
    filesize: int | None = None  # estimate: largest video stream at this level + audio

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
    """A format's "p" number: from YouTube's format_note, else min(width, height)."""
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
    """Available video resolutions, highest first. Playlist -> ``[]``."""
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
# Downloading
# --------------------------------------------------------------------------- #
def _base_opts(
    output_dir: Path,
    progress_callback: ProgressCallback | None,
    cancel_event: threading.Event | None = None,
    cookies: Cookies | None = None,
) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "outtmpl": str(output_dir / OUTPUT_TEMPLATE),
        "quiet": True,
        "logger": _SilentLogger(),
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": False,
    }
    if cookies is not None:
        cookies.apply(opts)
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


class StripAudioSourceMarker(PostProcessor):
    """Rename ``Title [id].yv-audio.mp3`` -> ``Title [id].mp3`` after the audio conversion.

    The source file of an audio download carries the ``AUDIO_SOURCE_MARKER`` suffix so that
    yt-dlp never reuses (and then deletes) a same-named video mp4; see
    ``config.AUDIO_SOURCE_TEMPLATE``.
    """

    def run(self, info: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
        path = info.get("filepath")
        if path:
            p = Path(path)
            if p.stem.endswith(AUDIO_SOURCE_MARKER):
                target = p.with_name(p.stem[: -len(AUDIO_SOURCE_MARKER)] + p.suffix)
                if p.exists():
                    p.replace(target)  # overwrite an old file of the same name (e.g. re-download with --force)
                info["filepath"] = str(target)
        return [], info


def _run(
    url: str,
    opts: dict[str, Any],
    *,
    kind: str,
    progress_callback: ProgressCallback | None,
    history: History | None,
    force: bool,
    cancel_event: threading.Event | None,
    extra_postprocessors: list[PostProcessor] | None = None,
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
            # runs after the postprocessors in opts (audio conversion, metadata/cover art)
            for pp in extra_postprocessors or []:
                ydl.add_post_processor(pp, when="post_process")
            if history is not None:
                ydl.add_post_processor(HistoryRecorder(history, kind), when="after_move")
            info = ydl.extract_info(url, download=True)
    except DownloadCancelled as exc:
        raise DownloadCancelledError(str(exc)) from exc
    except YoutubeDLError as exc:  # DownloadError, CookieLoadError...
        if cancel_event is not None and cancel_event.is_set():
            raise DownloadCancelledError("Đã dừng theo yêu cầu") from exc
        raise DownloadFailedError(_error_message(exc)) from exc
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
    cookies: Cookies | None = None,
) -> list[Path]:
    """Download an mp4 video (video + audio merged). Supports playlist URLs and every site
    yt-dlp supports (YouTube, Facebook...).

    - ``quality``: ``"best"`` or a line count (``"1080"``, ``"720p"``, ``"804"``...). Picks the
      largest resolution not above it; if the video has none, the smallest one.
    - ``history``: if given, record history and skip videos already downloaded (unless ``force``).
      Skipped videos are reported to the callback with ``status="skipped"``.
    - ``cancel_event``: set this event to stop → ``DownloadCancelledError``.
    - ``cookies``: login cookies (private/group Facebook videos...), see ``Cookies.resolve``.

    Returns the paths of the saved files.
    """
    format_sort = format_sort_for(quality)  # ValueError if the quality is invalid
    _require_ffmpeg()
    out = Path(output_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)

    opts = _base_opts(out, progress_callback, cancel_event, cookies)
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
    cookies: Cookies | None = None,
) -> list[Path]:
    """Download audio only and convert it to ``fmt`` (mp3 by default). Supports playlist URLs.

    ``history``/``force``/``cancel_event``/``cookies``: as in ``download_video``.
    ``audio_quality``: ``"best"`` (highest VBR) or a bitrate in kbps, see ``AUDIO_QUALITIES``
    (ignored for wav).
    ``embed_metadata``: embed title/artist/upload date in the file; for mp3/m4a also embed
    the thumbnail as cover art.
    A video without sound raises ``NoAudioStreamError``.
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

    opts = _base_opts(out, progress_callback, cancel_event, cookies)
    # Source name differs from the video name: when a site only has muxed files (e.g. Facebook),
    # yt-dlp must not treat the mp4 from a "video" download as its source and delete it afterwards.
    opts["outtmpl"] = str(out / AUDIO_SOURCE_TEMPLATE)
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

    # Remember finished source files, to clean them up if the audio conversion fails.
    finished: list[str] = []

    def remember_finished(d: dict[str, Any]) -> None:
        if d.get("status") == "finished" and d.get("filename"):
            finished.append(str(d["filename"]))

    opts["progress_hooks"] = [*opts.get("progress_hooks", []), remember_finished]
    try:
        return _run(
            url,
            opts,
            kind="audio",
            progress_callback=progress_callback,
            history=history,
            force=force,
            cancel_event=cancel_event,
            extra_postprocessors=[StripAudioSourceMarker()],
        )
    except DownloadFailedError as exc:
        _remove_audio_sources(finished)
        if any(p in str(exc).lower() for p in _NO_AUDIO_PATTERNS):
            raise NoAudioStreamError(
                "Video này không có âm thanh nên không tách audio được. "
                "Hãy tải dạng video nếu cần."
            ) from exc
        raise


def _remove_audio_sources(filenames: list[str]) -> None:
    """Delete leftover ``….yv-audio.*`` source files (and their thumbnail) of a failed audio download.

    Only touches files carrying ``AUDIO_SOURCE_MARKER`` for this very video; ``.part`` files
    are never in the list (finished files only), so resuming is not affected.
    """
    for name in filenames:
        src = Path(name)
        if not src.stem.endswith(AUDIO_SOURCE_MARKER):
            continue
        for leftover in src.parent.glob(glob.escape(src.stem) + ".*"):
            if not leftover.name.endswith(".part"):
                leftover.unlink(missing_ok=True)


def list_supported_sites(search: str | None = None) -> list[str]:
    """Names of the sites yt-dlp supports (deduplicated by the part before ``:``), filtered by ``search``."""
    names: dict[str, str] = {}  # lower case -> display name (prefer the capitalised one: "Instagram")
    for ie in yt_dlp.extractor.gen_extractor_classes():
        name = ie.IE_NAME.split(":", 1)[0]
        key = name.lower()
        if not ie.working() or key == "generic":  # "generic" is the fallback extractor, not a site
            continue
        if key not in names or names[key] == key:
            names[key] = name
    term = (search or "").strip().lower()
    return sorted((n for k, n in names.items() if term in k), key=str.lower)
