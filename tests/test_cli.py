from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from youtube_vault import downloader
from youtube_vault.cli import app
from youtube_vault.downloader import DownloadFailedError, MediaInfo, ProgressEvent

runner = CliRunner()


def test_help_lists_three_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("video", "audio", "info"):
        assert cmd in result.output


def test_info_prints_title(monkeypatch):
    monkeypatch.setattr(
        downloader,
        "get_info",
        lambda url: MediaInfo(id="jNQXAC9IVRw", title="Me at the zoo", duration=19, uploader="jawed"),
    )
    result = runner.invoke(app, ["info", "https://www.youtube.com/watch?v=jNQXAC9IVRw"])
    assert result.exit_code == 0
    assert "Me at the zoo" in result.output
    assert "00:19" in result.output


def test_info_playlist(monkeypatch):
    media = MediaInfo(
        id="PL",
        title="My list",
        is_playlist=True,
        entries=[MediaInfo(id="a", title="Clip A", duration=3725)],
    )
    monkeypatch.setattr(downloader, "get_info", lambda url: media)
    result = runner.invoke(app, ["info", "https://youtube.com/playlist?list=PL"])
    assert result.exit_code == 0
    assert "Clip A" in result.output
    assert "1:02:05" in result.output


def test_info_error_exits_cleanly(monkeypatch):
    def boom(url):
        raise DownloadFailedError("Video unavailable")

    monkeypatch.setattr(downloader, "get_info", boom)
    result = runner.invoke(app, ["info", "https://youtu.be/bad"])
    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_video_without_ffmpeg_shows_instructions(fake_ydl, ffmpeg_missing, tmp_path):
    result = runner.invoke(app, ["video", "https://youtu.be/x", "--out", str(tmp_path)])
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "ffmpeg" in result.output.lower()
    assert fake_ydl.instances == []


def test_audio_without_ffmpeg_shows_instructions(fake_ydl, ffmpeg_missing, tmp_path):
    result = runner.invoke(app, ["audio", "https://youtu.be/x", "--out", str(tmp_path)])
    assert result.exit_code == 1
    assert "ffmpeg" in result.output.lower()


def test_video_rejects_unknown_quality():
    result = runner.invoke(app, ["video", "https://youtu.be/x", "--quality", "360"])
    assert result.exit_code != 0


def test_video_passes_options_and_reports_files(monkeypatch, tmp_path):
    captured = {}

    def fake_download(url, quality, output_dir, progress_callback):
        captured.update(url=url, quality=quality, output_dir=output_dir)
        progress_callback(ProgressEvent("downloading", "v.mp4", 50, 100))
        progress_callback(ProgressEvent("finished", "v.mp4", 100, 100))
        return [Path(output_dir) / "v.mp4"]

    monkeypatch.setattr(downloader, "download_video", fake_download)
    result = runner.invoke(
        app, ["video", "https://youtu.be/x", "-q", "720", "-o", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert captured == {"url": "https://youtu.be/x", "quality": "720", "output_dir": tmp_path}
    assert "v.mp4" in result.output


def test_audio_passes_format(monkeypatch, tmp_path):
    captured = {}

    def fake_download(url, fmt, output_dir, progress_callback):
        captured.update(fmt=fmt)
        return []

    monkeypatch.setattr(downloader, "download_audio", fake_download)
    result = runner.invoke(app, ["audio", "https://youtu.be/x", "-f", "m4a", "-o", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert captured == {"fmt": "m4a"}
