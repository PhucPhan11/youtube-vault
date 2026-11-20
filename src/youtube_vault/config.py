"""Cấu hình mặc định: thư mục lưu, mẫu tên file, preset chất lượng."""

from __future__ import annotations

from pathlib import Path

DEFAULT_OUTPUT_DIR: Path = Path.home() / "Downloads" / "youtube-vault"

OUTPUT_TEMPLATE: str = "%(title)s [%(id)s].%(ext)s"


def _height_preset(height: int) -> str:
    return (
        f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]"
        f"/bestvideo[height<={height}]+bestaudio"
        f"/best[height<={height}]"
    )


QUALITY_PRESETS: dict[str, str] = {
    "best": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
    "1080": _height_preset(1080),
    "720": _height_preset(720),
    "480": _height_preset(480),
}

DEFAULT_QUALITY: str = "best"

AUDIO_FORMATS: tuple[str, ...] = ("mp3", "m4a", "opus", "wav")
DEFAULT_AUDIO_FORMAT: str = "mp3"

# Dữ liệu ứng dụng (lịch sử tải)
DATA_DIR: Path = Path.home() / ".youtube-vault"
HISTORY_DB: Path = DATA_DIR / "history.db"

# Hàng đợi tải
DEFAULT_MAX_WORKERS: int = 2
MAX_WORKERS_LIMIT: int = 5
DEFAULT_MAX_RETRIES: int = 2
RETRY_DELAYS: tuple[float, ...] = (2, 5)  # giây chờ trước mỗi lần thử lại
