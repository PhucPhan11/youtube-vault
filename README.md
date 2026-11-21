# youtube-vault

A command-line tool and desktop app for downloading video and audio from **YouTube, Facebook, Instagram, X (Twitter), TikTok…** (more than 1,000 sites supported by yt-dlp), built on [yt-dlp](https://github.com/yt-dlp/yt-dlp).

> The app's interface (CLI help, GUI labels and messages) is in Vietnamese. This manual quotes those labels as they appear, with an English translation the first time.

- Download mp4 video (video and audio streams are merged automatically), choosing the resolution from `best`, 8K/4K, `1440` … `144`, or **from the list of resolutions the video actually has**
- Download audio only (`mp3`, `m4a`, `opus`, `wav`), choose the bitrate (`best`, `320` … `128` kbps), and automatically embed title/artist and cover art (thumbnail) in the file
- View video/playlist information without downloading
- Playlist URLs are supported
- **Facebook**: public videos/reels download directly; private, friends-only or group videos use login cookies (see [Downloading Facebook videos](#downloading-facebook-videos))
- **Instagram, X (Twitter), TikTok and many other sites**: just paste the link; the `sites` command tells you whether a site is supported (see [Other sites](#other-sites-instagram-x-tiktok))
- Progress bars rendered with Rich
- **Desktop GUI** (Tkinter): paste many URLs, parallel download queue, pause/resume/cancel, automatic retry on errors
- **Download history** (SQLite): skips videos that were already downloaded, lets you browse past downloads and open their folder
- **Record audio playing on your computer** (browser, livestream, web player…) to `mp3`/`m4a`/`opus`/`wav`, from the CLI or the *Ghi âm* (Record) tab in the GUI

> The core (`downloader.py`, `download_queue.py`, `history.py`, `recorder.py`) does not depend on any UI. The CLI and the GUI share the same core.

## Requirements

- Python 3.11 or newer
- [uv](https://docs.astral.sh/uv/)
- **ffmpeg**: needed to merge video + audio, convert audio, and encode recordings. The `info` command does not need ffmpeg.

### Installing ffmpeg

| OS | Command |
|---|---|
| Windows | `winget install Gyan.FFmpeg` (or `choco install ffmpeg` / `scoop install ffmpeg`) |
| macOS | `brew install ffmpeg` |
| Ubuntu/Debian | `sudo apt install ffmpeg` |
| Fedora | `sudo dnf install ffmpeg` |
| Arch | `sudo pacman -S ffmpeg` |

After installing, reopen the terminal and check with `ffmpeg -version`. Without ffmpeg, the `video`/`audio` commands print installation instructions and stop; they do not crash.

## Installation

```bash
uv sync
```

## Usage

```bash
# List the commands
uv run youtube-vault --help

# Show information (title, duration, available formats and resolutions)
uv run youtube-vault info "https://www.youtube.com/watch?v=jNQXAC9IVRw"

# Download a video at the best quality
uv run youtube-vault video "https://www.youtube.com/watch?v=jNQXAC9IVRw"

# Download a video at 720p into another folder
uv run youtube-vault video URL --quality 720 --out D:\Videos

# Show the resolutions the video actually has, then pick a number
uv run youtube-vault video URL --choose

# Audio only: mp3 (default) or m4a
uv run youtube-vault audio URL
uv run youtube-vault audio URL --format m4a

# mp3 at 320 kbps; or without embedded info/cover art
uv run youtube-vault audio URL --bitrate 320
uv run youtube-vault audio URL --no-embed

# Download a whole playlist
uv run youtube-vault video "https://www.youtube.com/playlist?list=..."

# Facebook video / reel (public)
uv run youtube-vault video "https://www.facebook.com/watch/?v=..."
uv run youtube-vault audio "https://www.facebook.com/reel/..."

# Facebook video that requires login: use a cookies file
uv run youtube-vault video "https://www.facebook.com/..." --cookies D:\cookies.txt

# Instagram (reel/post) and X (x.com or twitter.com)
uv run youtube-vault video "https://www.instagram.com/reel/..."
uv run youtube-vault audio "https://x.com/<account>/status/..."

# Is a site supported?
uv run youtube-vault sites -s tiktok
```

### Options

| Command | Option | Default | Values |
|---|---|---|---|
| `video` | `--quality`, `-q` | `best` | `best`, `4320`, `2160`, `1440`, `1080`, `720`, `480`, `360`, `240`, `144` or any number (`720p` works too) |
| `video` | `--choose`, `-c` | off | show the video's resolutions (with FPS and estimated size) and pick one; single videos only, not playlists |
| `audio` | `--format`, `-f` | `mp3` | `mp3`, `m4a`, `opus`, `wav` |
| `audio` | `--bitrate`, `-b` | `best` | `best` (highest-quality VBR), `320`, `256`, `192`, `128` (kbps); ignored for `wav` |
| `audio` | `--embed` / `--no-embed` | on | embed title, artist and upload date in the file; for `mp3`/`m4a` also embed the thumbnail as cover art |
| `video`, `audio` | `--out`, `-o` | `~/Downloads/youtube-vault/` | folder path |
| `video`, `audio` | `--force` | off | download again even if it is already in the history |
| `video`, `audio`, `info` | `--cookies` | `~/.youtube-vault/cookies.txt` if it exists | cookies.txt file used to log in (see [Downloading Facebook videos](#downloading-facebook-videos)) |
| `video`, `audio`, `info` | `--cookies-from-browser` | not used | `chrome`, `edge`, `firefox`, `brave`, `opera`, `vivaldi`, `chromium` (most reliable with `firefox`) |
| `sites` | `--search`, `-s` | all | filter the list of supported sites by name, e.g. `insta`, `twitter`, `tiktok` (works offline) |

**How the resolution is chosen:** the largest resolution not above the one you asked for (e.g. asking for `1440` on a video that only goes up to 1080p downloads 1080p). If the video has nothing that low, the smallest available resolution is used. Resolution is measured on the short side of the frame, so vertical videos (Shorts, 1080×1920) and 21:9 widescreen films (1920×804) both count as 1080p. At equal resolution, mp4 + m4a is preferred.

Files are named with the template `%(title).150B [%(id)s].%(ext)s`, e.g. `Me at the zoo [jNQXAC9IVRw].mp4`. Long titles (common on Facebook, where the title is the whole post text) are cut to at most 150 bytes.

## Downloading Facebook videos

**Public videos/reels** download directly, just like YouTube: `video`, `audio`, `info`, *Xem độ phân giải* (Show resolutions) and the *Tải MP3* (Download MP3) button all work. Facebook usually offers only 2 quality levels (`sd`, `hd`); whatever `--quality` you choose, the closest available one is used.

**Private, friends-only and group videos** (and some reels) require you to be logged in. The app uses the cookies of a browser that is logged into Facebook:

1. Open Facebook in your browser and log in.
2. Install an extension that exports cookies as `cookies.txt` (Netscape format), e.g. *Get cookies.txt LOCALLY*, and export the cookies while on facebook.com.
3. Save the file as `~/.youtube-vault/cookies.txt` (Windows: `C:\Users\<name>\.youtube-vault\cookies.txt`). This file is **used automatically** by the CLI and preselected in the GUI. Or keep the file elsewhere and pass `--cookies <path>`.

On **Chrome/Edge (Windows)**, `--cookies-from-browser chrome` usually **does not work**: recent versions encrypt cookies per application and lock the cookie file while the browser is open. That is why the `cookies.txt` file above is recommended. With **Firefox**, `--cookies-from-browser firefox` reads the cookies directly, no export needed.

When a video needs login and no cookies are available, or the browser's cookies cannot be read, the app shows the error together with a hint on how to fix it.

> ⚠️ **Security:** `cookies.txt` contains your logged-in Facebook session; anyone with this file can log into your account. Do not share it or upload it to Git/the cloud. When the cookies expire (login errors come back), export the file again. To stop using it, delete the file. The app never stores cookie contents in the history.

Only download content you have the right to use; respect copyright and Facebook's terms.

## Other sites (Instagram, X, TikTok…)

The app is not limited to YouTube/Facebook: every link is handed to yt-dlp, which supports more than 1,000 sites. Check a site with `youtube-vault sites -s <name>` (e.g. `sites -s insta`). A site that is not in the list may still work if the link points straight at a video file (`.mp4`, `.m3u8`).

- **Instagram:** paste a reel link (`instagram.com/reel/...`) or a post link (`instagram.com/p/...`). A post with several videos is downloaded like a playlist (one file per video). Titles are usually "Video by <account>", so file names always include the post ID to stay unique. Instagram often limits users who are not logged in (*rate-limit* / *login required* errors): in that case use cookies as described in the Facebook section, but export them while on instagram.com.
- **X (Twitter):** paste the post link; both `x.com/...` and `twitter.com/...` work. Sensitive (NSFW) posts, protected accounts and age-restricted posts need login cookies (exported while on x.com).
- **One cookies file for several sites:** export cookies on facebook.com, instagram.com, x.com… one after another, then paste the contents of those files into the same `~/.youtube-vault/cookies.txt` (the Netscape format allows concatenating them like this). The security note in the Facebook section applies to every site.
- For videos **without sound** (some Instagram reels), *Chỉ audio* (Audio only) / *Tải MP3* fails immediately with "Video này không có âm thanh…" (this video has no sound) and is not retried; downloading it as video still works.
- When a site requires login, the error names the site and suggests using cookies.
- If a site **cannot be downloaded** (unusual embedded player, live stream without a link), use [Recording audio playing on your computer](#recording-audio-playing-on-your-computer) to capture the sound.

## Desktop GUI

```bash
uv run youtube-vault gui
# or use the launcher without a console window (after `uv sync`):
.venv\Scripts\youtube-vault-gui.exe        # Windows
.venv/bin/youtube-vault-gui                # macOS/Linux
```

- **Thêm tải xuống (Add downloads):** paste one or more links (one per line, playlists allowed), choose Video/Audio, the quality or format and the output folder, then click *Thêm vào hàng đợi* (Add to queue).
  - *Xem độ phân giải* (Show resolutions): fetches the resolutions the video (first link) actually has, with estimated sizes, and shows them in the *Chất lượng* (Quality) box. For playlists the standard list is kept. After adding to the queue, the *Chất lượng* box goes back to the standard list.
  - *Bitrate audio* and *Gắn thông tin + ảnh bìa* (Embed info + cover art): apply to audio items (the bitrate is disabled when `wav` is selected).
  - *Tải MP3* (Download MP3): one click adds the links to the queue as mp3 audio, whether Video or another audio format is selected (it uses the current bitrate and cover-art settings).
  - *Cookie đăng nhập* (Login cookies): *Không dùng* (None), *File cookies.txt* (click *Chọn…* (Browse…) to pick the file) or cookies from a browser. Needed for private/group Facebook videos and similar. If `~/.youtube-vault/cookies.txt` exists, it is preselected.
- **Hàng đợi (Queue) tab:** shows the progress, speed and remaining time of each item.
  - *Số luồng* (Threads, 1–5): how many videos download at once; can be changed while downloading.
  - *Tạm dừng* (Pause): stops downloading but keeps the `.part` file; *Tiếp tục* (Resume) continues from where it stopped.
  - *Hủy* (Cancel): stops downloading and deletes the temporary `.part` file.
  - Network errors are retried automatically up to 2 times (waiting 2 seconds, then 5 seconds). Errors such as missing ffmpeg are not retried. Failed items can be retried manually with *Thử lại* (Retry).
  - Double-click an item to open its folder.
- **Lịch sử (History) tab:** search by title, open the file's folder, remove entries from the history (downloaded files are kept).
- **Ghi âm (Record) tab:** pick the speaker, format and bitrate, then click *● Bắt đầu ghi* (Start recording); click *■ Dừng & lưu* (Stop & save) to save the file into the folder in the *Lưu vào* (Save to) box. Shows a timer and a volume meter. See [Recording audio playing on your computer](#recording-audio-playing-on-your-computer).
- When the window is closed, running downloads are paused and keep their `.part` files; an active recording is saved.

> Note: the ffmpeg video/audio merge step cannot be interrupted, so pause/cancel only take effect after it.

## Recording audio playing on your computer

Use this to capture audio from the browser that cannot be downloaded through a link (livestreams, sites yt-dlp does not support, embedded players…). The app records **the sound going to your speakers** (WASAPI loopback on Windows, the PulseAudio monitor on Linux) and encodes it with ffmpeg.

```bash
# List the speakers that can be recorded
uv run youtube-vault record --list-devices

# Record until Ctrl+C is pressed (mp3 by default, best quality)
uv run youtube-vault record

# Record 90 seconds, m4a at 192 kbps, from a specific speaker
uv run youtube-vault record -t 90 -f m4a -b 192 -d "Speakers (Realtek High Definition Audio)"
```

| Option | Default | Values |
|---|---|---|
| `--format`, `-f` | `mp3` | `mp3`, `m4a`, `opus`, `wav` |
| `--bitrate`, `-b` | `best` | `best`, `320`, `256`, `192`, `128` (kbps); ignored for `wav` |
| `--device`, `-d` | default speaker | speaker name (see `--list-devices`) |
| `--duration`, `-t` | unlimited | number of seconds; stops and saves automatically when reached |
| `--out`, `-o` | `~/Downloads/youtube-vault/` | output folder |

Files are named after the time recording started, e.g. `Ghi âm 2026-11-21 09-05-07.mp3` ("Ghi âm" = "Recording"). Recordings are not added to the download history.

Notes:
- It records **all** of the computer's sound (including notification sounds from other apps), not a single tab. Mute other sounds while recording.
- Supported on Windows and Linux. macOS cannot record system audio out of the box; install a virtual device such as [BlackHole](https://github.com/ExistentialAudio/BlackHole).
- Only record content you have the right to use; respect copyright and the website's terms.

## Download history

Every finished file is recorded in `~/.youtube-vault/history.db` (SQLite), shared by the CLI and the GUI. Videos that were already downloaded are skipped next time (based on yt-dlp's `download_archive` mechanism). Video and audio are tracked separately: having downloaded the video does not prevent downloading the audio. To download again, add `--force`, or tick *Tải lại kể cả đã có trong lịch sử* (Download again even if in history) in the GUI.

```bash
uv run youtube-vault history list               # 20 most recent entries
uv run youtube-vault history list -n 50 -s zoo  # search by title/URL
uv run youtube-vault history open 3             # open the folder of entry ID 3
uv run youtube-vault history remove 3           # remove the entry from history (keeps the file)
uv run youtube-vault history clear --yes        # clear the whole history
```

## Using it as a library

```python
from youtube_vault.downloader import download_video, get_info, list_resolutions
from youtube_vault.download_queue import DownloadQueue
from youtube_vault.history import History

info = get_info("https://www.youtube.com/watch?v=jNQXAC9IVRw")
print(info.title, info.duration)
print([r.label for r in list_resolutions(info)])  # e.g. ['240p', '144p']

def on_progress(event):
    print(event.status, event.title, event.percent)

# Download one video, record it in the history, skip it if already downloaded
download_video(info.url, quality="720", progress_callback=on_progress, history=History())

# Queue: 3 parallel workers
queue = DownloadQueue(max_workers=3, history=History(), on_update=lambda item: print(item.status))
item = queue.add("https://www.youtube.com/watch?v=jNQXAC9IVRw", kind="audio", audio_format="mp3",
                 audio_quality="320", embed_metadata=True)
queue.pause(item.id); queue.resume(item.id)

# Record the audio playing on the computer for 10 seconds
import time
from youtube_vault.recorder import Recorder

rec = Recorder("~/Music", fmt="mp3", audio_quality="192")
rec.start(); time.sleep(10)
print(rec.stop())  # path of the saved file
```

## Project structure

```
src/youtube_vault/
├── config.py          # output folder, filename template, quality presets, queue settings
├── downloader.py      # download logic wrapping yt-dlp, UI-independent
├── history.py         # SQLite history + yt-dlp glue (download_archive, postprocessor)
├── download_queue.py  # queue: multi-threaded, pause/resume/cancel, automatic retry
├── recorder.py        # records the audio playing on the speakers (soundcard + ffmpeg)
├── platform_utils.py  # opens a file's folder on each OS
├── cli.py             # CLI built with Typer + Rich
└── gui.py             # desktop GUI built with Tkinter
tests/                 # unit tests; yt-dlp is mocked (nothing is really downloaded)
```

## Running the tests

```bash
uv run pytest
```
