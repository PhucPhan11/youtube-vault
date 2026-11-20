"""Giao diện dòng lệnh (Typer + Rich)."""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
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
from rich.table import Table

from . import downloader
from .config import DEFAULT_OUTPUT_DIR
from .downloader import (
    FFmpegNotFoundError,
    MediaInfo,
    ProgressEvent,
    YoutubeVaultError,
)

app = typer.Typer(
    help="youtube-vault — tải video/audio YouTube về máy (dùng yt-dlp).",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
err_console = Console(stderr=True)


class Quality(str, Enum):
    best = "best"
    p1080 = "1080"
    p720 = "720"
    p480 = "480"


class AudioFormat(str, Enum):
    mp3 = "mp3"
    m4a = "m4a"
    opus = "opus"
    wav = "wav"


OutOption = Annotated[
    Path,
    typer.Option("--out", "-o", help="Thư mục lưu file.", file_okay=False),
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


def _run_download(action: Callable[[Callable[[ProgressEvent], None]], list[Path]]) -> None:
    try:
        with _make_progress() as progress:
            files = action(_progress_callback(progress))
    except FFmpegNotFoundError as exc:
        err_console.print(
            Panel(exc.instructions, title="[bold red]Chưa cài ffmpeg", border_style="red")
        )
        raise typer.Exit(1) from None
    except YoutubeVaultError as exc:
        err_console.print(f"[bold red]Lỗi:[/] {exc}")
        raise typer.Exit(1) from None

    if not files:
        console.print("[yellow]Không có file nào được tải.[/]")
        return
    console.print(f"[bold green]Đã lưu {len(files)} file:[/]")
    for f in files:
        console.print(f"  • {f}")


# --------------------------------------------------------------------------- #
# Lệnh
# --------------------------------------------------------------------------- #
@app.command()
def video(
    url: Annotated[str, typer.Argument(help="URL video hoặc playlist YouTube.")],
    quality: Annotated[
        Quality, typer.Option("--quality", "-q", help="Chất lượng video.")
    ] = Quality.best,
    out: OutOption = DEFAULT_OUTPUT_DIR,
) -> None:
    """Tải video mp4 (ghép video + audio)."""
    _run_download(
        lambda cb: downloader.download_video(
            url, quality=quality.value, output_dir=out, progress_callback=cb
        )
    )


@app.command()
def audio(
    url: Annotated[str, typer.Argument(help="URL video hoặc playlist YouTube.")],
    fmt: Annotated[
        AudioFormat, typer.Option("--format", "-f", help="Định dạng audio.")
    ] = AudioFormat.mp3,
    out: OutOption = DEFAULT_OUTPUT_DIR,
) -> None:
    """Chỉ tải audio."""
    _run_download(
        lambda cb: downloader.download_audio(
            url, fmt=fmt.value, output_dir=out, progress_callback=cb
        )
    )


@app.command()
def info(
    url: Annotated[str, typer.Argument(help="URL video hoặc playlist YouTube.")],
) -> None:
    """Xem thông tin video/playlist (không tải)."""
    try:
        with console.status("Đang lấy thông tin..."):
            media = downloader.get_info(url)
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


if __name__ == "__main__":
    app()
