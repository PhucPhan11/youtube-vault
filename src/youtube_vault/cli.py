"""Giao diện dòng lệnh (Typer + Rich)."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TaskID,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.prompt import IntPrompt
from rich.table import Table

from . import downloader, recorder
from .config import (
    AUDIO_QUALITIES,
    COOKIES_BROWSERS,
    DEFAULT_AUDIO_QUALITY,
    DEFAULT_EMBED_METADATA,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_QUALITY,
    QUALITY_PRESETS,
)
from .downloader import (
    Cookies,
    FFmpegNotFoundError,
    MediaInfo,
    ProgressEvent,
    YoutubeVaultError,
)
from .history import History
from .platform_utils import open_folder

app = typer.Typer(
    help="youtube-vault — tải video/audio YouTube, Facebook… về máy (dùng yt-dlp).",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
err_console = Console(stderr=True)


class AudioFormat(str, Enum):
    mp3 = "mp3"
    m4a = "m4a"
    opus = "opus"
    wav = "wav"


# Bitrate audio, lấy từ config ("best", "320", ...). Tên thành viên không được bắt đầu bằng số.
AudioQuality = Enum(  # type: ignore[misc]
    "AudioQuality",
    {(q if q == "best" else f"k{q}"): q for q in AUDIO_QUALITIES},
    type=str,
)


OutOption = Annotated[
    Path,
    typer.Option("--out", "-o", help="Thư mục lưu file.", file_okay=False),
]
ForceOption = Annotated[
    bool,
    typer.Option("--force", help="Tải lại kể cả khi đã có trong lịch sử."),
]
UrlArgument = Annotated[
    str, typer.Argument(help="URL video hoặc playlist (YouTube, Facebook, và các trang yt-dlp hỗ trợ).")
]

# Trình duyệt lấy cookie, lấy từ config.
CookieBrowser = Enum(  # type: ignore[misc]
    "CookieBrowser", {b: b for b in COOKIES_BROWSERS}, type=str
)
CookiesOption = Annotated[
    Path | None,
    typer.Option(
        "--cookies",
        exists=True,
        dir_okay=False,
        help=(
            "File cookies.txt để đăng nhập (vd. video Facebook riêng tư/nhóm). "
            "Mặc định dùng ~/.youtube-vault/cookies.txt nếu có."
        ),
    ),
]
BrowserCookiesOption = Annotated[
    CookieBrowser | None,
    typer.Option(
        "--cookies-from-browser",
        help="Lấy cookie đăng nhập trực tiếp từ trình duyệt (ổn định nhất với firefox).",
    ),
]


def _resolve_cookies(file: Path | None, browser: Enum | None) -> Cookies | None:
    try:
        cookies = Cookies.resolve(file, browser.value if browser is not None else None)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from None
    if cookies is not None and file is None and browser is None:
        console.print(f"[dim]Dùng cookie đăng nhập từ {cookies.file}[/]")
    return cookies


def _validate_quality(value: str) -> str:
    try:
        return downloader.normalize_quality(value)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from None


QualityOption = Annotated[
    str,
    typer.Option(
        "--quality",
        "-q",
        callback=_validate_quality,
        help=(
            f"Độ phân giải: {', '.join(QUALITY_PRESETS)} hoặc số bất kỳ (vd. 720p). "
            "Video không có mức này thì lấy mức gần nhất bên dưới."
        ),
    ),
]
ChooseOption = Annotated[
    bool,
    typer.Option(
        "--choose",
        "-c",
        help="Xem các độ phân giải video thực sự có rồi chọn (chỉ dùng cho một video).",
    ),
]


# --------------------------------------------------------------------------- #
# Tiện ích hiển thị
# --------------------------------------------------------------------------- #
def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _format_size(size: int | None) -> str:
    if not size:
        return "-"
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def _make_progress() -> Progress:
    return Progress(
        TextColumn("[bold blue]{task.description}", justify="left"),
        BarColumn(),
        TextColumn("{task.percentage:>5.1f}%"),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=False,
    )


def _progress_callback(progress: Progress) -> Callable[[ProgressEvent], None]:
    tasks: dict[str, TaskID] = {}

    def callback(event: ProgressEvent) -> None:
        if event.status == "skipped":
            progress.console.print(
                f"[yellow]⏭ Đã có trong lịch sử, bỏ qua:[/] {event.title or event.filename}"
                " [dim](dùng --force để tải lại)[/]"
            )
            return
        key = event.filename
        if key not in tasks:
            name = Path(key).name or "đang tải"
            if event.playlist_index and event.playlist_count:
                name = f"[{event.playlist_index}/{event.playlist_count}] {name}"
            if len(name) > 50:
                name = name[:47] + "..."
            tasks[key] = progress.add_task(name, total=event.total_bytes)
        task_id = tasks[key]
        if event.status == "finished":
            done = event.total_bytes or event.downloaded_bytes
            progress.update(task_id, total=done, completed=done)
        else:
            progress.update(
                task_id, total=event.total_bytes, completed=event.downloaded_bytes
            )

    return callback


def _exit_ffmpeg_missing(instructions: str) -> NoReturn:
    err_console.print(Panel(instructions, title="[bold red]Chưa cài ffmpeg", border_style="red"))
    raise typer.Exit(1)


def _choose_quality(url: str, cookies: Cookies | None = None) -> str:
    """Lấy các độ phân giải video thực sự có, cho người dùng chọn; trả về giá trị chất lượng."""
    try:
        with console.status("Đang lấy danh sách độ phân giải..."):
            media = downloader.get_info(url, cookies=cookies)
    except YoutubeVaultError as exc:
        err_console.print(f"[bold red]Lỗi:[/] {exc}")
        raise typer.Exit(1) from None
    if media.is_playlist:
        err_console.print(
            "[bold red]Lỗi:[/] --choose chỉ dùng cho một video, không dùng cho playlist. "
            "Hãy dùng --quality."
        )
        raise typer.Exit(1)

    options = downloader.list_resolutions(media)
    if not options:
        console.print("[yellow]Không tìm thấy danh sách độ phân giải, dùng chất lượng tốt nhất.[/]")
        return DEFAULT_QUALITY

    console.print(f"[bold]{media.title or url}[/]")
    table = Table("#", "Độ phân giải", "FPS", "Dung lượng (ước tính)")
    for i, opt in enumerate(options, start=1):
        table.add_row(
            str(i), opt.label, f"{opt.fps:g}" if opt.fps else "", _format_size(opt.filesize)
        )
    console.print(table)
    choice = IntPrompt.ask(
        "Chọn số",
        choices=[str(i) for i in range(1, len(options) + 1)],
        default=1,
        console=console,
    )
    return options[choice - 1].quality


def _run_download(action: Callable[[Callable[[ProgressEvent], None]], list[Path]]) -> None:
    try:
        with _make_progress() as progress:
            files = action(_progress_callback(progress))
    except FFmpegNotFoundError as exc:
        _exit_ffmpeg_missing(exc.instructions)
    except YoutubeVaultError as exc:
        err_console.print(f"[bold red]Lỗi:[/] {exc}")
        raise typer.Exit(1) from None

    if not files:
        console.print("[yellow]Không có file mới nào được tải.[/]")
        return
    console.print(f"[bold green]Đã lưu {len(files)} file:[/]")
    for f in files:
        console.print(f"  • {f}")


# --------------------------------------------------------------------------- #
# Lệnh
# --------------------------------------------------------------------------- #
@app.command()
def video(
    url: UrlArgument,
    quality: QualityOption = DEFAULT_QUALITY,
    choose: ChooseOption = False,
    out: OutOption = DEFAULT_OUTPUT_DIR,
    force: ForceOption = False,
    cookies_file: CookiesOption = None,
    cookies_browser: BrowserCookiesOption = None,
) -> None:
    """Tải video mp4 (ghép video + audio)."""
    cookies = _resolve_cookies(cookies_file, cookies_browser)
    if choose:
        if not downloader.is_ffmpeg_available():
            _exit_ffmpeg_missing(downloader.ffmpeg_install_instructions())
        quality = _choose_quality(url, cookies)
    hist = _open_history()
    _run_download(
        lambda cb: downloader.download_video(
            url,
            quality=quality,
            output_dir=out,
            progress_callback=cb,
            history=hist,
            force=force,
            cookies=cookies,
        )
    )


@app.command()
def audio(
    url: UrlArgument,
    fmt: Annotated[
        AudioFormat, typer.Option("--format", "-f", help="Định dạng audio.")
    ] = AudioFormat.mp3,
    bitrate: Annotated[
        AudioQuality,
        typer.Option(
            "--bitrate",
            "-b",
            help="Bitrate audio (kbps); 'best' = chất lượng VBR cao nhất. Không áp dụng cho wav.",
        ),
    ] = AudioQuality(DEFAULT_AUDIO_QUALITY),
    embed: Annotated[
        bool,
        typer.Option(
            "--embed/--no-embed",
            help="Gắn tiêu đề, nghệ sĩ và ảnh bìa (thumbnail, chỉ mp3/m4a) vào file.",
        ),
    ] = DEFAULT_EMBED_METADATA,
    out: OutOption = DEFAULT_OUTPUT_DIR,
    force: ForceOption = False,
    cookies_file: CookiesOption = None,
    cookies_browser: BrowserCookiesOption = None,
) -> None:
    """Chỉ tải audio (mặc định mp3)."""
    cookies = _resolve_cookies(cookies_file, cookies_browser)
    hist = _open_history()
    _run_download(
        lambda cb: downloader.download_audio(
            url,
            fmt=fmt.value,
            output_dir=out,
            progress_callback=cb,
            history=hist,
            force=force,
            audio_quality=bitrate.value,
            embed_metadata=embed,
            cookies=cookies,
        )
    )


def _level_bar(level: float, width: int = 10) -> str:
    filled = max(0, min(width, round(level * width)))
    return "▮" * filled + "▯" * (width - filled)


def _fail(exc: YoutubeVaultError) -> NoReturn:
    if isinstance(exc, FFmpegNotFoundError):
        _exit_ffmpeg_missing(exc.instructions)
    err_console.print(f"[bold red]Lỗi:[/] {exc}")
    raise typer.Exit(1)


@app.command()
def record(
    out: OutOption = DEFAULT_OUTPUT_DIR,
    fmt: Annotated[
        AudioFormat, typer.Option("--format", "-f", help="Định dạng file ghi âm.")
    ] = AudioFormat.mp3,
    bitrate: Annotated[
        AudioQuality,
        typer.Option(
            "--bitrate",
            "-b",
            help="Bitrate (kbps); 'best' = chất lượng cao nhất. Không áp dụng cho wav.",
        ),
    ] = AudioQuality(DEFAULT_AUDIO_QUALITY),
    device: Annotated[
        str | None,
        typer.Option(
            "--device", "-d", help="Tên loa cần ghi (xem --list-devices). Mặc định: loa hệ thống."
        ),
    ] = None,
    duration: Annotated[
        float | None,
        typer.Option(
            "--duration",
            "-t",
            min=0.1,
            help="Tự dừng sau số giây này. Không đặt thì ghi tới khi bấm Ctrl+C.",
        ),
    ] = None,
    list_devices: Annotated[
        bool, typer.Option("--list-devices", help="Liệt kê các loa ghi được rồi thoát.")
    ] = False,
) -> None:
    """Ghi âm âm thanh đang phát trên máy (vd. trình duyệt). Bấm Ctrl+C để dừng và lưu."""
    if list_devices:
        try:
            names = recorder.list_output_devices()
        except YoutubeVaultError as exc:
            _fail(exc)
        for i, name in enumerate(names):
            console.print(f"  • {name}" + (" [dim](mặc định)[/]" if i == 0 else ""))
        return

    rec = recorder.Recorder(out, fmt.value, bitrate.value, device)
    try:
        rec.start()
    except YoutubeVaultError as exc:
        _fail(exc)
    console.print(f"Đang ghi vào [bold]{rec.path}[/] — bấm Ctrl+C để dừng.")
    try:
        with Live(console=console, refresh_per_second=8, transient=True) as live:
            while rec.is_recording and (duration is None or rec.elapsed < duration):
                live.update(
                    f"[red]●[/] Đang ghi {_format_duration(rec.elapsed)} {_level_bar(rec.level)}"
                )
                time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    with console.status("Đang lưu file..."):
        try:
            path = rec.stop()
        except YoutubeVaultError as exc:
            _fail(exc)
    console.print(f"[bold green]Đã lưu ({_format_duration(rec.elapsed)}):[/] {path}")


@app.command()
def gui() -> None:
    """Mở giao diện desktop (hàng đợi tải + lịch sử)."""
    from .gui import main as gui_main  # import lười: CLI không cần tkinter

    gui_main()


# --------------------------------------------------------------------------- #
# Lịch sử
# --------------------------------------------------------------------------- #
history_app = typer.Typer(help="Xem và quản lý lịch sử tải.", no_args_is_help=True)
app.add_typer(history_app, name="history")


def _open_history() -> History | None:
    try:
        return History()
    except (OSError, sqlite3.Error) as exc:
        err_console.print(f"[yellow]Không mở được lịch sử ({exc}); tiếp tục mà không ghi lịch sử.[/]")
        return None


def _require_history() -> History:
    hist = _open_history()
    if hist is None:
        raise typer.Exit(1)
    return hist


@history_app.command("list")
def history_list(
    limit: Annotated[int, typer.Option("--limit", "-n", min=1, help="Số mục tối đa.")] = 20,
    search: Annotated[
        str | None, typer.Option("--search", "-s", help="Tìm theo tiêu đề hoặc URL.")
    ] = None,
) -> None:
    """Liệt kê các lần tải gần nhất."""
    records = _require_history().list(limit=limit, search=search)
    if not records:
        console.print("[yellow]Lịch sử trống.[/]" if not search else "[yellow]Không có kết quả.[/]")
        return
    table = Table("ID", "Thời gian", "Loại", "Tiêu đề", "File")
    for r in records:
        table.add_row(
            str(r.id),
            r.downloaded_at.replace("T", " "),
            r.kind,
            r.title or "?",
            r.filepath or "",
        )
    console.print(table)


@history_app.command("remove")
def history_remove(
    record_id: Annotated[int, typer.Argument(help="ID trong lịch sử (xem bằng `history list`).")],
) -> None:
    """Xóa một mục khỏi lịch sử (không xóa file đã tải)."""
    if _require_history().remove(record_id):
        console.print(f"[green]Đã xóa mục {record_id} khỏi lịch sử.[/]")
    else:
        err_console.print(f"[red]Không có mục {record_id}.[/]")
        raise typer.Exit(1)


@history_app.command("clear")
def history_clear(
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Không hỏi xác nhận.")] = False,
) -> None:
    """Xóa toàn bộ lịch sử (không xóa file đã tải)."""
    if not yes and not typer.confirm("Xóa toàn bộ lịch sử tải?"):
        raise typer.Abort()
    count = _require_history().clear()
    console.print(f"[green]Đã xóa {count} mục.[/]")


@history_app.command("open")
def history_open(
    record_id: Annotated[int, typer.Argument(help="ID trong lịch sử.")],
) -> None:
    """Mở thư mục chứa file đã tải."""
    record = _require_history().get_by_id(record_id)
    if record is None or not record.filepath:
        err_console.print(f"[red]Không có mục {record_id} hoặc mục này không có đường dẫn file.[/]")
        raise typer.Exit(1)
    try:
        open_folder(record.filepath)
    except FileNotFoundError as exc:
        err_console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from None


@app.command()
def info(
    url: UrlArgument,
    cookies_file: CookiesOption = None,
    cookies_browser: BrowserCookiesOption = None,
) -> None:
    """Xem thông tin video/playlist (không tải)."""
    cookies = _resolve_cookies(cookies_file, cookies_browser)
    try:
        with console.status("Đang lấy thông tin..."):
            media = downloader.get_info(url, cookies=cookies)
    except YoutubeVaultError as exc:
        err_console.print(f"[bold red]Lỗi:[/] {exc}")
        raise typer.Exit(1) from None
    _print_info(media)


def _print_info(media: MediaInfo) -> None:
    console.print(f"[bold]Tiêu đề:[/] {media.title}")
    if media.uploader:
        console.print(f"[bold]Kênh:[/] {media.uploader}")

    if media.is_playlist:
        console.print(f"[bold]Playlist:[/] {len(media.entries)} video")
        table = Table("#", "Tiêu đề", "Thời lượng", "ID")
        for i, entry in enumerate(media.entries, start=1):
            table.add_row(str(i), entry.title or "?", _format_duration(entry.duration), entry.id or "")
        console.print(table)
        return

    console.print(f"[bold]Thời lượng:[/] {_format_duration(media.duration)}")
    if media.formats:
        table = Table("ID", "Ext", "Độ phân giải", "FPS", "Video", "Audio", "Dung lượng")
        for f in media.formats:
            table.add_row(
                f.format_id,
                f.ext or "",
                f.resolution or "",
                f"{f.fps:g}" if f.fps else "",
                f.vcodec or "",
                f.acodec or "",
                _format_size(f.filesize),
            )
        console.print(table)

    resolutions = downloader.list_resolutions(media)
    if resolutions:
        labels = ", ".join(r.label for r in resolutions)
        console.print(f"[bold]Độ phân giải có sẵn:[/] {labels}")


if __name__ == "__main__":
    app()
