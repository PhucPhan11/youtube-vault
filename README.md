# youtube-vault

Công cụ dòng lệnh để tải video và audio YouTube về máy, xây dựng trên [yt-dlp](https://github.com/yt-dlp/yt-dlp).

- Tải video mp4 (tự ghép luồng video + audio), chọn độ phân giải từ `best`, 8K/4K, `1440` … `144`, hoặc **chọn từ danh sách độ phân giải mà video thực sự có**
- Chỉ tải audio (`mp3`, `m4a`, `opus`, `wav`), chọn bitrate (`best`, `320` … `128` kbps), tự gắn tiêu đề/nghệ sĩ và ảnh bìa (thumbnail) vào file
- Xem thông tin video/playlist mà không cần tải
- Hỗ trợ URL playlist
- Thanh tiến độ hiển thị bằng Rich
- **Giao diện desktop** (Tkinter): dán nhiều URL, hàng đợi tải song song, tạm dừng/tiếp tục/hủy, tự thử lại khi lỗi
- **Lịch sử tải** (SQLite): tự bỏ qua video đã tải, xem lại, mở thư mục chứa file

> Phần lõi (`downloader.py`, `download_queue.py`, `history.py`) không phụ thuộc giao diện. CLI và GUI đều dùng chung phần lõi này.

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

# Xem thông tin (tiêu đề, thời lượng, định dạng và độ phân giải có sẵn)
uv run youtube-vault info "https://www.youtube.com/watch?v=jNQXAC9IVRw"

# Tải video chất lượng tốt nhất
uv run youtube-vault video "https://www.youtube.com/watch?v=jNQXAC9IVRw"

# Tải video 720p vào thư mục khác
uv run youtube-vault video URL --quality 720 --out D:\Videos

# Xem các độ phân giải video thực sự có rồi chọn số
uv run youtube-vault video URL --choose

# Chỉ tải audio mp3 (mặc định) hoặc m4a
uv run youtube-vault audio URL
uv run youtube-vault audio URL --format m4a

# mp3 320 kbps; hoặc không gắn thông tin/ảnh bìa
uv run youtube-vault audio URL --bitrate 320
uv run youtube-vault audio URL --no-embed

# Tải cả playlist
uv run youtube-vault video "https://www.youtube.com/playlist?list=..."
```

### Tùy chọn

| Lệnh | Tùy chọn | Mặc định | Giá trị |
|---|---|---|---|
| `video` | `--quality`, `-q` | `best` | `best`, `4320`, `2160`, `1440`, `1080`, `720`, `480`, `360`, `240`, `144` hoặc số bất kỳ (`720p` cũng được) |
| `video` | `--choose`, `-c` | tắt | hiện danh sách độ phân giải của video (kèm FPS, dung lượng ước tính) để chọn; chỉ dùng cho một video, không dùng cho playlist |
| `audio` | `--format`, `-f` | `mp3` | `mp3`, `m4a`, `opus`, `wav` |
| `audio` | `--bitrate`, `-b` | `best` | `best` (VBR chất lượng cao nhất), `320`, `256`, `192`, `128` (kbps); không áp dụng cho `wav` |
| `audio` | `--embed` / `--no-embed` | bật | gắn tiêu đề, nghệ sĩ, ngày đăng vào file; với `mp3`/`m4a` gắn thêm thumbnail làm ảnh bìa |
| `video`, `audio` | `--out`, `-o` | `~/Downloads/youtube-vault/` | đường dẫn thư mục |
| `video`, `audio` | `--force` | tắt | tải lại kể cả khi đã có trong lịch sử |

**Cách chọn độ phân giải:** lấy mức lớn nhất không vượt quá mức đã chọn (vd. chọn `1440` mà video chỉ có tới 1080p thì tải 1080p). Nếu video không có mức nào thấp như vậy thì lấy mức nhỏ nhất có sẵn. Độ phân giải tính theo cạnh ngắn của khung hình, nên video dọc (Shorts, 1080×1920) và phim màn ảnh rộng 21:9 (1920×804) đều được tính là 1080p. Cùng độ phân giải thì ưu tiên mp4 + m4a.

File được đặt tên theo mẫu `%(title)s [%(id)s].%(ext)s`, ví dụ `Me at the zoo [jNQXAC9IVRw].mp4`.

## Giao diện desktop (GUI)

```bash
uv run youtube-vault gui
# hoặc dùng lối tắt không kèm cửa sổ console (sau khi `uv sync`):
.venv\Scripts\youtube-vault-gui.exe        # Windows
.venv/bin/youtube-vault-gui                # macOS/Linux
```

- **Thêm tải xuống:** dán một hoặc nhiều link (mỗi dòng một link, có thể là playlist), chọn Video/Audio, chất lượng hoặc định dạng, thư mục lưu, rồi bấm *Thêm vào hàng đợi*.
  - *Xem độ phân giải*: lấy danh sách độ phân giải mà video (link đầu tiên) thực sự có, kèm dung lượng ước tính, rồi hiện trong ô *Chất lượng* để chọn. Với playlist thì giữ danh sách chuẩn. Sau khi thêm vào hàng đợi, ô *Chất lượng* trở lại danh sách chuẩn.
  - *Bitrate audio* và *Gắn thông tin + ảnh bìa*: áp dụng cho mục audio (bitrate bị khóa khi chọn `wav`).
  - *Tải MP3*: bấm một lần là thêm các link vào hàng đợi dưới dạng audio mp3, dù đang chọn Video hay định dạng audio khác (dùng bitrate và tùy chọn ảnh bìa đang chọn).
- **Tab Hàng đợi:** xem tiến độ, tốc độ, thời gian còn lại của từng mục.
  - *Số luồng* (1–5): số video tải cùng lúc, đổi được ngay khi đang tải.
  - *Tạm dừng*: dừng tải nhưng giữ file `.part`; *Tiếp tục* sẽ tải nối tiếp từ chỗ dừng.
  - *Hủy*: dừng tải và xóa file tạm `.part`.
  - Lỗi mạng được tự thử lại tối đa 2 lần (chờ 2 giây, rồi 5 giây). Lỗi như thiếu ffmpeg thì không thử lại. Mục bị lỗi có thể bấm *Thử lại* bằng tay.
  - Double-click một mục để mở thư mục chứa file.
- **Tab Lịch sử:** tìm theo tiêu đề, mở thư mục chứa file, xóa mục khỏi lịch sử (file đã tải vẫn được giữ).
- Khi đóng cửa sổ, các mục đang tải được tạm dừng và giữ file `.part`.

> Lưu ý: bước ghép video/audio bằng ffmpeg không ngắt được giữa chừng, nên lệnh tạm dừng/hủy chỉ có hiệu lực sau bước đó.

## Lịch sử tải

Mỗi file tải xong được ghi vào `~/.youtube-vault/history.db` (SQLite), dùng chung cho cả CLI và GUI. Video đã tải sẽ được bỏ qua ở lần sau (dựa trên cơ chế `download_archive` của yt-dlp). Video và audio được tính riêng: đã tải video thì vẫn tải audio được. Muốn tải lại thì thêm `--force`, hoặc tick ô *Tải lại kể cả đã có trong lịch sử* trên GUI.

```bash
uv run youtube-vault history list               # 20 mục gần nhất
uv run youtube-vault history list -n 50 -s zoo  # tìm theo tiêu đề/URL
uv run youtube-vault history open 3             # mở thư mục chứa file của mục ID 3
uv run youtube-vault history remove 3           # xóa mục khỏi lịch sử (không xóa file)
uv run youtube-vault history clear --yes        # xóa toàn bộ lịch sử
```

## Dùng như thư viện

```python
from youtube_vault.downloader import download_video, get_info, list_resolutions
from youtube_vault.download_queue import DownloadQueue
from youtube_vault.history import History

info = get_info("https://www.youtube.com/watch?v=jNQXAC9IVRw")
print(info.title, info.duration)
print([r.label for r in list_resolutions(info)])  # vd. ['240p', '144p']

def on_progress(event):
    print(event.status, event.title, event.percent)

# Tải một video, ghi lịch sử, bỏ qua nếu đã tải rồi
download_video(info.url, quality="720", progress_callback=on_progress, history=History())

# Hàng đợi: 3 luồng song song
queue = DownloadQueue(max_workers=3, history=History(), on_update=lambda item: print(item.status))
item = queue.add("https://www.youtube.com/watch?v=jNQXAC9IVRw", kind="audio", audio_format="mp3",
                 audio_quality="320", embed_metadata=True)
queue.pause(item.id); queue.resume(item.id)
```

## Cấu trúc

```
src/youtube_vault/
├── config.py          # thư mục lưu, mẫu tên file, preset chất lượng, cấu hình hàng đợi
├── downloader.py      # logic tải, gói yt-dlp, không phụ thuộc giao diện
├── history.py         # lịch sử SQLite + nối vào yt-dlp (download_archive, postprocessor)
├── download_queue.py  # hàng đợi: đa luồng, tạm dừng/tiếp tục/hủy, tự thử lại
├── platform_utils.py  # mở thư mục chứa file theo từng hệ điều hành
├── cli.py             # CLI bằng Typer + Rich
└── gui.py             # giao diện desktop bằng Tkinter
tests/                 # unit test, yt-dlp được mock (không tải thật)
```

## Chạy test

```bash
uv run pytest
```
