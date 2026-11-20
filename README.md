# youtube-vault

Công cụ dòng lệnh để tải video và audio YouTube về máy, xây dựng trên [yt-dlp](https://github.com/yt-dlp/yt-dlp).

- Tải video mp4 (tự ghép luồng video + audio) với các mức chất lượng `best`, `1080`, `720`, `480`
- Chỉ tải audio (`mp3`, `m4a`, `opus`, `wav`)
- Xem thông tin video/playlist mà không cần tải
- Hỗ trợ URL playlist
- Thanh tiến độ hiển thị bằng Rich

> Phase 1: phần lõi + CLI. Phần lõi (`downloader.py`) không phụ thuộc giao diện và báo tiến độ qua callback, để sau này GUI dùng lại được.

## Yêu cầu

- Python 3.11 trở lên
- [uv](https://docs.astral.sh/uv/)
- **ffmpeg**: cần cho việc ghép video + audio và chuyển đổi audio. Lệnh `info` không cần ffmpeg.

### Cài ffmpeg

| Hệ điều hành | Lệnh |
|---|---|
| Windows | `winget install Gyan.FFmpeg` (hoặc `choco install ffmpeg` / `scoop install ffmpeg`) |
| macOS | `brew install ffmpeg` |
| Ubuntu/Debian | `sudo apt install ffmpeg` |
| Fedora | `sudo dnf install ffmpeg` |
| Arch | `sudo pacman -S ffmpeg` |

Cài xong, mở lại terminal rồi kiểm tra bằng `ffmpeg -version`. Nếu chưa có ffmpeg, lệnh `video`/`audio` sẽ in hướng dẫn cài và dừng lại, không bị crash.

## Cài đặt

```bash
uv sync
```

## Cách dùng

```bash
# Xem các lệnh
uv run youtube-vault --help

# Xem thông tin (tiêu đề, thời lượng, định dạng có sẵn)
uv run youtube-vault info "https://www.youtube.com/watch?v=jNQXAC9IVRw"

# Tải video chất lượng tốt nhất
uv run youtube-vault video "https://www.youtube.com/watch?v=jNQXAC9IVRw"

# Tải video 720p vào thư mục khác
uv run youtube-vault video URL --quality 720 --out D:\Videos

# Chỉ tải audio mp3 (mặc định) hoặc m4a
uv run youtube-vault audio URL
uv run youtube-vault audio URL --format m4a

# Tải cả playlist
uv run youtube-vault video "https://www.youtube.com/playlist?list=..."
```

### Tùy chọn

| Lệnh | Tùy chọn | Mặc định | Giá trị |
|---|---|---|---|
| `video` | `--quality`, `-q` | `best` | `best`, `1080`, `720`, `480` |
| `audio` | `--format`, `-f` | `mp3` | `mp3`, `m4a`, `opus`, `wav` |
| `video`, `audio` | `--out`, `-o` | `~/Downloads/youtube-vault/` | đường dẫn thư mục |

File được đặt tên theo mẫu `%(title)s [%(id)s].%(ext)s`, ví dụ `Me at the zoo [jNQXAC9IVRw].mp4`.

## Dùng như thư viện

```python
from youtube_vault.downloader import download_video, get_info

info = get_info("https://www.youtube.com/watch?v=jNQXAC9IVRw")
print(info.title, info.duration)

def on_progress(event):
    print(event.status, event.percent)

download_video(info.url, quality="720", progress_callback=on_progress)
```

## Cấu trúc

```
src/youtube_vault/
├── config.py       # thư mục lưu mặc định, mẫu tên file, preset chất lượng
├── downloader.py   # logic tải, gói yt-dlp, không phụ thuộc giao diện
└── cli.py          # CLI bằng Typer + Rich
tests/              # unit test, yt-dlp được mock (không tải thật)
```

## Chạy test

```bash
uv run pytest
```
