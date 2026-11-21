"""Cấu hình mặc định: thư mục lưu, mẫu tên file, preset chất lượng."""

from __future__ import annotations

from pathlib import Path

DEFAULT_OUTPUT_DIR: Path = Path.home() / "Downloads" / "youtube-vault"

# Tiêu đề bị cắt còn tối đa MAX_TITLE_BYTES byte (tiêu đề Facebook thường là cả bài đăng).
MAX_TITLE_BYTES: int = 150
OUTPUT_TEMPLATE: str = f"%(title).{MAX_TITLE_BYTES}B [%(id)s].%(ext)s"

# Khi chỉ tải audio, file nguồn mang đuôi phụ này để không bao giờ trùng (rồi bị xóa)
# với file video mp4 cùng tên; đuôi phụ được bỏ đi sau khi chuyển sang mp3/m4a...
AUDIO_SOURCE_MARKER: str = ".yv-audio"
AUDIO_SOURCE_TEMPLATE: str = f"%(title).{MAX_TITLE_BYTES}B [%(id)s]{AUDIO_SOURCE_MARKER}.%(ext)s"


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

# Ghi âm âm thanh đang phát trên máy (loopback của loa)
RECORD_SAMPLE_RATE: int = 48_000
RECORD_CHANNELS: int = 2
RECORD_BLOCK_SECONDS: float = 0.1  # mỗi lần đọc 100 ms
RECORD_FILENAME: str = "Ghi âm %Y-%m-%d %H-%M-%S"  # strftime, thêm .<đuôi>
# Bitrate (kbps) khi chọn "best" cho các định dạng không có chế độ VBR qua ffmpeg.
RECORD_BEST_BITRATE: dict[str, str] = {"m4a": "256", "opus": "192"}

# Dữ liệu ứng dụng (lịch sử tải)
DATA_DIR: Path = Path.home() / ".youtube-vault"
HISTORY_DB: Path = DATA_DIR / "history.db"

# Cookie đăng nhập (cho video Facebook riêng tư/nhóm...). File này được dùng tự động nếu tồn tại.
DEFAULT_COOKIES_FILE: Path = DATA_DIR / "cookies.txt"
COOKIES_BROWSERS: tuple[str, ...] = (
    "chrome", "edge", "firefox", "brave", "opera", "vivaldi", "chromium",
)

# Hàng đợi tải
DEFAULT_MAX_WORKERS: int = 2
MAX_WORKERS_LIMIT: int = 5
DEFAULT_MAX_RETRIES: int = 2
RETRY_DELAYS: tuple[float, ...] = (2, 5)  # giây chờ trước mỗi lần thử lại
