"""Cấu hình mặc định: thư mục lưu, mẫu tên file, preset chất lượng."""

from __future__ import annotations

from pathlib import Path

DEFAULT_OUTPUT_DIR: Path = Path.home() / "Downloads" / "youtube-vault"

OUTPUT_TEMPLATE: str = "%(title)s [%(id)s].%(ext)s"


# Chọn định dạng video: luồng video + luồng audio tốt nhất, không có thì lấy file gộp sẵn.
# Độ phân giải được quyết định bằng ``format_sort`` của yt-dlp (không lọc theo height):
#   - "res:N": lấy độ phân giải lớn nhất <= N; nếu không có thì lấy cái nhỏ nhất > N.
#   - "res" tính theo min(rộng, cao) nên video dọc (Shorts) và video 21:9 vẫn đúng.
#   - "ext:mp4:m4a": cùng độ phân giải thì ưu tiên video mp4 + audio m4a.
VIDEO_FORMAT: str = "bv+ba/b"
VIDEO_FORMAT_PREFERENCE: str = "ext:mp4:m4a"

# Các mức độ phân giải chuẩn (danh sách cố định cho CLI/GUI), từ cao xuống thấp.
VIDEO_HEIGHTS: tuple[int, ...] = (4320, 2160, 1440, 1080, 720, 480, 360, 240, 144)

# Chất lượng -> format_sort. Thứ tự key = thứ tự hiển thị.
QUALITY_PRESETS: dict[str, list[str]] = {
    "best": ["res", VIDEO_FORMAT_PREFERENCE],
    **{str(h): [f"res:{h}", VIDEO_FORMAT_PREFERENCE] for h in VIDEO_HEIGHTS},
}

DEFAULT_QUALITY: str = "best"

AUDIO_FORMATS: tuple[str, ...] = ("mp3", "m4a", "opus", "wav")
DEFAULT_AUDIO_FORMAT: str = "mp3"

# Bitrate audio: "best" = chất lượng VBR cao nhất của ffmpeg, số = kbps (CBR).
AUDIO_QUALITIES: tuple[str, ...] = ("best", "320", "256", "192", "128")
DEFAULT_AUDIO_QUALITY: str = "best"

# Gắn thông tin (tiêu đề, nghệ sĩ...) và ảnh bìa (thumbnail) vào file audio.
DEFAULT_EMBED_METADATA: bool = True
# Định dạng mà ffmpeg nhúng được ảnh bìa (không cần cài thêm mutagen/AtomicParsley).
EMBED_THUMBNAIL_FORMATS: tuple[str, ...] = ("mp3", "m4a")

# Dữ liệu ứng dụng (lịch sử tải)
DATA_DIR: Path = Path.home() / ".youtube-vault"
HISTORY_DB: Path = DATA_DIR / "history.db"

# Hàng đợi tải
DEFAULT_MAX_WORKERS: int = 2
MAX_WORKERS_LIMIT: int = 5
DEFAULT_MAX_RETRIES: int = 2
RETRY_DELAYS: tuple[float, ...] = (2, 5)  # giây chờ trước mỗi lần thử lại
