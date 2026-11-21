"""Defaults: output folder, filename template, quality presets, tunables."""

from __future__ import annotations

from pathlib import Path

DEFAULT_OUTPUT_DIR: Path = Path.home() / "Downloads" / "youtube-vault"

# Titles are cut to at most MAX_TITLE_BYTES bytes (a Facebook title is often the whole post).
MAX_TITLE_BYTES: int = 150
OUTPUT_TEMPLATE: str = f"%(title).{MAX_TITLE_BYTES}B [%(id)s].%(ext)s"

# Audio-only downloads give the source file this extra suffix so it can never collide with (and
# then delete) a same-named video mp4; the suffix is removed after converting to mp3/m4a...
AUDIO_SOURCE_MARKER: str = ".yv-audio"
AUDIO_SOURCE_TEMPLATE: str = f"%(title).{MAX_TITLE_BYTES}B [%(id)s]{AUDIO_SOURCE_MARKER}.%(ext)s"


# Video format: best video stream + best audio stream, else a pre-merged file.
# The resolution is decided by yt-dlp's ``format_sort`` (no height filter):
#   - "res:N": largest resolution <= N; if there is none, the smallest one > N.
#   - "res" uses min(width, height), so vertical videos (Shorts) and 21:9 videos work.
#   - "ext:mp4:m4a": at equal resolution, prefer mp4 video + m4a audio.
VIDEO_FORMAT: str = "bv+ba/b"
VIDEO_FORMAT_PREFERENCE: str = "ext:mp4:m4a"

# Standard resolutions (fixed list for the CLI/GUI), highest first.
VIDEO_HEIGHTS: tuple[int, ...] = (4320, 2160, 1440, 1080, 720, 480, 360, 240, 144)

# Quality -> format_sort. Key order = display order.
QUALITY_PRESETS: dict[str, list[str]] = {
    "best": ["res", VIDEO_FORMAT_PREFERENCE],
    **{str(h): [f"res:{h}", VIDEO_FORMAT_PREFERENCE] for h in VIDEO_HEIGHTS},
}

DEFAULT_QUALITY: str = "best"

AUDIO_FORMATS: tuple[str, ...] = ("mp3", "m4a", "opus", "wav")
DEFAULT_AUDIO_FORMAT: str = "mp3"

# Audio bitrate: "best" = ffmpeg's highest-quality VBR, a number = kbps (CBR).
AUDIO_QUALITIES: tuple[str, ...] = ("best", "320", "256", "192", "128")
DEFAULT_AUDIO_QUALITY: str = "best"

# Embed metadata (title, artist...) and cover art (thumbnail) in audio files.
DEFAULT_EMBED_METADATA: bool = True
# Formats where ffmpeg can embed cover art (no mutagen/AtomicParsley needed).
EMBED_THUMBNAIL_FORMATS: tuple[str, ...] = ("mp3", "m4a")

# Recording the audio playing on the computer (speaker loopback)
RECORD_SAMPLE_RATE: int = 48_000
RECORD_CHANNELS: int = 2
RECORD_BLOCK_SECONDS: float = 0.1  # read 100 ms at a time
RECORD_FILENAME: str = "Ghi âm %Y-%m-%d %H-%M-%S"  # strftime, + .<ext>; user-visible, Vietnamese
# Bitrate (kbps) used for "best" with formats that have no VBR mode via ffmpeg.
RECORD_BEST_BITRATE: dict[str, str] = {"m4a": "256", "opus": "192"}

# App data (download history)
DATA_DIR: Path = Path.home() / ".youtube-vault"
HISTORY_DB: Path = DATA_DIR / "history.db"

# Login cookies (private/group Facebook videos...). This file is used automatically if it exists.
DEFAULT_COOKIES_FILE: Path = DATA_DIR / "cookies.txt"
# yt-dlp extractor name -> readable site name (used in error hints). Others: capitalised.
SITE_NAMES: dict[str, str] = {
    "youtube": "YouTube",
    "facebook": "Facebook",
    "instagram": "Instagram",
    "twitter": "X (Twitter)",
    "tiktok": "TikTok",
    "vimeo": "Vimeo",
    "dailymotion": "Dailymotion",
    "soundcloud": "SoundCloud",
    "bilibili": "Bilibili",
    "reddit": "Reddit",
    "twitch": "Twitch",
}
COOKIES_BROWSERS: tuple[str, ...] = (
    "chrome", "edge", "firefox", "brave", "opera", "vivaldi", "chromium",
)

# Download queue
DEFAULT_MAX_WORKERS: int = 2
MAX_WORKERS_LIMIT: int = 5
DEFAULT_MAX_RETRIES: int = 2
RETRY_DELAYS: tuple[float, ...] = (2, 5)  # seconds to wait before each retry
