from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from youtube_vault import cli as cli_module
from youtube_vault import downloader, recorder
from youtube_vault.cli import app
from youtube_vault.downloader import DownloadFailedError, FormatInfo, MediaInfo, ProgressEvent
from youtube_vault.history import History

runner = CliRunner()


def test_help_lists_all_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("video", "audio", "record", "info", "history", "gui"):
        assert cmd in result.output


def test_info_prints_title(monkeypatch):
    monkeypatch.setattr(
        downloader,
        "get_info",
        lambda url, **kw: MediaInfo(id="jNQXAC9IVRw", title="Me at the zoo", duration=19, uploader="jawed"),
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
    monkeypatch.setattr(downloader, "get_info", lambda url, **kw: media)
    result = runner.invoke(app, ["info", "https://youtube.com/playlist?list=PL"])
    assert result.exit_code == 0
    assert "Clip A" in result.output
    assert "1:02:05" in result.output


def test_info_error_exits_cleanly(monkeypatch):
    def boom(url, **kw):
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
    result = runner.invoke(app, ["video", "https://youtu.be/x", "--quality", "abc"])
    assert result.exit_code == 2  # lỗi tham số của Typer


@pytest.mark.parametrize(("arg", "expected"), [("1440", "1440"), ("720p", "720"), ("360", "360")])
def test_video_accepts_any_height(monkeypatch, tmp_path, arg, expected):
    captured = {}

    def fake_download(url, quality, output_dir, progress_callback, **kwargs):
        captured["quality"] = quality
        return []

    monkeypatch.setattr(downloader, "download_video", fake_download)
    result = runner.invoke(app, ["video", "https://youtu.be/x", "-q", arg, "-o", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert captured["quality"] == expected


def _media_with_resolutions() -> MediaInfo:
    return MediaInfo(
        id="x",
        title="Clip",
        formats=[
            FormatInfo("140", ext="m4a", vcodec="none", acodec="mp4a", filesize=1_000_000),
            FormatInfo("299", vcodec="avc1", width=1920, height=1080, fps=60,
                       filesize=50_000_000, format_note="1080p60"),
            FormatInfo("136", vcodec="avc1", width=1280, height=720, fps=30,
                       filesize=20_000_000, format_note="720p"),
            FormatInfo("160", vcodec="avc1", width=256, height=144, format_note="144p"),
        ],
    )


def test_video_choose_prompts_and_uses_selection(monkeypatch, ffmpeg_present, tmp_path):
    captured = {}

    def fake_download(url, quality, output_dir, progress_callback, **kwargs):
        captured["quality"] = quality
        return []

    monkeypatch.setattr(downloader, "get_info", lambda url, **kw: _media_with_resolutions())
    monkeypatch.setattr(downloader, "download_video", fake_download)
    result = runner.invoke(
        app, ["video", "https://youtu.be/x", "--choose", "-q", "1080", "-o", str(tmp_path)],
        input="2\n",
    )
    assert result.exit_code == 0, result.output
    assert "1080p60" in result.output and "720p" in result.output and "144p" in result.output
    assert captured["quality"] == "720"  # --choose thắng -q


def test_video_choose_default_is_highest(monkeypatch, ffmpeg_present, tmp_path):
    captured = {}
    monkeypatch.setattr(downloader, "get_info", lambda url, **kw: _media_with_resolutions())
    monkeypatch.setattr(
        downloader, "download_video",
        lambda url, quality, output_dir, progress_callback, **kw: captured.update(q=quality) or [],
    )
    result = runner.invoke(app, ["video", "u", "-c", "-o", str(tmp_path)], input="\n")
    assert result.exit_code == 0, result.output
    assert captured["q"] == "1080"


def test_video_choose_rejects_playlist(monkeypatch, ffmpeg_present, tmp_path):
    called = []
    monkeypatch.setattr(
        downloader, "get_info", lambda url, **kw: MediaInfo(id="PL", title="L", is_playlist=True)
    )
    monkeypatch.setattr(downloader, "download_video", lambda *a, **k: called.append(1) or [])
    result = runner.invoke(app, ["video", "https://youtube.com/playlist?list=PL", "--choose"])
    assert result.exit_code == 1
    assert "playlist" in result.output.lower()
    assert called == []


def test_video_choose_without_formats_uses_best(monkeypatch, ffmpeg_present, tmp_path):
    captured = {}
    monkeypatch.setattr(downloader, "get_info", lambda url, **kw: MediaInfo(id="x", title="t"))
    monkeypatch.setattr(
        downloader, "download_video",
        lambda url, quality, output_dir, progress_callback, **kw: captured.update(q=quality) or [],
    )
    result = runner.invoke(app, ["video", "u", "--choose", "-o", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert captured["q"] == "best"


def test_video_choose_without_ffmpeg_fails_before_fetching(monkeypatch, ffmpeg_missing):
    fetched = []
    monkeypatch.setattr(downloader, "get_info", lambda url, **kw: fetched.append(url))
    result = runner.invoke(app, ["video", "https://youtu.be/x", "--choose"])
    assert result.exit_code == 1
    assert "ffmpeg" in result.output.lower()
    assert fetched == []


def test_info_lists_resolutions(monkeypatch):
    monkeypatch.setattr(downloader, "get_info", lambda url, **kw: _media_with_resolutions())
    result = runner.invoke(app, ["info", "https://youtu.be/x"])
    assert result.exit_code == 0
    assert "Độ phân giải có sẵn" in result.output
    assert "1080p60, 720p, 144p" in result.output


def test_video_passes_options_and_reports_files(monkeypatch, tmp_path):
    captured = {}

    def fake_download(url, quality, output_dir, progress_callback, **kwargs):
        captured.update(url=url, quality=quality, output_dir=output_dir, force=kwargs["force"])
        progress_callback(ProgressEvent("downloading", "v.mp4", 50, 100))
        progress_callback(ProgressEvent("finished", "v.mp4", 100, 100))
        return [Path(output_dir) / "v.mp4"]

    monkeypatch.setattr(downloader, "download_video", fake_download)
    result = runner.invoke(
        app, ["video", "https://youtu.be/x", "-q", "720", "-o", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert captured == {
        "url": "https://youtu.be/x", "quality": "720", "output_dir": tmp_path, "force": False,
    }
    assert "v.mp4" in result.output


def test_audio_passes_format(monkeypatch, tmp_path):
    captured = {}

    def fake_download(url, fmt, output_dir, progress_callback, **kwargs):
        captured.update(fmt=fmt)
        return []

    monkeypatch.setattr(downloader, "download_audio", fake_download)
    result = runner.invoke(app, ["audio", "https://youtu.be/x", "-f", "m4a", "-o", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert captured == {"fmt": "m4a"}


def test_audio_defaults_best_bitrate_and_embed(monkeypatch, tmp_path):
    captured = {}

    def fake_download(url, fmt, output_dir, progress_callback, **kwargs):
        captured.update(fmt=fmt, **kwargs)
        return []

    monkeypatch.setattr(downloader, "download_audio", fake_download)
    result = runner.invoke(app, ["audio", "https://youtu.be/x", "-o", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert captured["fmt"] == "mp3"
    assert captured["audio_quality"] == "best"
    assert captured["embed_metadata"] is True


def test_audio_passes_bitrate_and_no_embed(monkeypatch, tmp_path):
    captured = {}

    def fake_download(url, fmt, output_dir, progress_callback, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(downloader, "download_audio", fake_download)
    result = runner.invoke(
        app, ["audio", "https://youtu.be/x", "-b", "192", "--no-embed", "-o", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert captured["audio_quality"] == "192"
    assert captured["embed_metadata"] is False


def test_audio_rejects_invalid_bitrate(monkeypatch, tmp_path):
    monkeypatch.setattr(downloader, "download_audio", lambda *a, **k: [])
    result = runner.invoke(app, ["audio", "https://youtu.be/x", "-b", "999", "-o", str(tmp_path)])
    assert result.exit_code != 0


# ------------------------------- history ---------------------------------- #

def _seed_history():
    hist = History()  # dùng DB tạm (fixture autouse trong conftest)
    a = hist.add("youtube a", "video", title="Me at the zoo", filepath="C:/x/zoo.mp4")
    b = hist.add("youtube b", "audio", title="Song", filepath="C:/x/song.mp3")
    return hist, a, b


def test_history_list_empty():
    result = runner.invoke(app, ["history", "list"])
    assert result.exit_code == 0
    assert "trống" in result.output


def test_history_list_and_search():
    _seed_history()
    result = runner.invoke(app, ["history", "list"])
    assert result.exit_code == 0
    assert "Me at the zoo" in result.output and "Song" in result.output

    result = runner.invoke(app, ["history", "list", "--search", "zoo"])
    assert "Me at the zoo" in result.output
    assert "Song" not in result.output


def test_history_remove():
    hist, a, _ = _seed_history()
    result = runner.invoke(app, ["history", "remove", str(a.id)])
    assert result.exit_code == 0
    assert hist.get_by_id(a.id) is None

    result = runner.invoke(app, ["history", "remove", "9999"])
    assert result.exit_code == 1


def test_history_clear_requires_confirmation():
    hist, _, _ = _seed_history()
    result = runner.invoke(app, ["history", "clear"], input="n\n")
    assert result.exit_code != 0
    assert len(hist.list()) == 2

    result = runner.invoke(app, ["history", "clear", "--yes"])
    assert result.exit_code == 0
    assert hist.list() == []


def test_history_open_calls_open_folder(monkeypatch):
    _, a, _ = _seed_history()
    opened = []
    monkeypatch.setattr(cli_module, "open_folder", opened.append)
    result = runner.invoke(app, ["history", "open", str(a.id)])
    assert result.exit_code == 0
    assert opened == ["C:/x/zoo.mp4"]


def test_history_open_unknown_id():
    result = runner.invoke(app, ["history", "open", "42"])
    assert result.exit_code == 1


def test_video_reports_skipped(monkeypatch, tmp_path):
    def fake_download(url, quality, output_dir, progress_callback, **kwargs):
        assert kwargs["history"] is not None
        progress_callback(ProgressEvent("skipped", "old.mp4", title="Me at the zoo"))
        return []

    monkeypatch.setattr(downloader, "download_video", fake_download)
    result = runner.invoke(app, ["video", "https://youtu.be/x", "-o", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "Đã có trong lịch sử" in result.output
    assert "Me at the zoo" in result.output


def test_force_flag_is_passed(monkeypatch, tmp_path):
    captured = {}

    def fake_download(url, fmt, output_dir, progress_callback, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(downloader, "download_audio", fake_download)
    result = runner.invoke(app, ["audio", "https://youtu.be/x", "--force", "-o", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert captured["force"] is True


# -------------------------------- record ----------------------------------- #
class FakeRecorder:
    instances: list[FakeRecorder] = []

    def __init__(self, output_dir, fmt, audio_quality, device, start_error=None):
        self.args = (Path(output_dir), fmt, audio_quality, device)
        self.path = Path(output_dir) / f"Ghi âm test.{fmt}"
        self.level = 0.5
        self._elapsed = 0.0
        self.stopped = False
        self.start_error = start_error
        FakeRecorder.instances.append(self)

    def start(self):
        if self.start_error is not None:
            raise self.start_error
        return self.path

    @property
    def elapsed(self):
        self._elapsed += 0.5  # mỗi lần đọc giả như đã ghi thêm nửa giây
        return self._elapsed

    @property
    def is_recording(self):
        return not self.stopped

    def stop(self):
        self.stopped = True
        return self.path


@pytest.fixture
def fake_recorder(monkeypatch):
    FakeRecorder.instances = []
    monkeypatch.setattr(recorder, "Recorder", FakeRecorder)
    return FakeRecorder


def test_record_with_duration_saves_file(fake_recorder, tmp_path):
    result = runner.invoke(
        app, ["record", "-t", "1", "-f", "m4a", "-b", "192", "-d", "Loa A", "-o", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    rec = fake_recorder.instances[0]
    assert rec.args == (tmp_path, "m4a", "192", "Loa A")
    assert rec.stopped
    assert "Đã lưu" in result.output
    assert "Ghi âm test.m4a" in result.output.replace("\n", "")


def test_record_defaults(fake_recorder, tmp_path):
    result = runner.invoke(app, ["record", "-t", "0.5", "-o", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert fake_recorder.instances[0].args == (tmp_path, "mp3", "best", None)


def test_record_list_devices(monkeypatch):
    monkeypatch.setattr(recorder, "list_output_devices", lambda: ["Loa A", "Tai nghe B"])
    result = runner.invoke(app, ["record", "--list-devices"])
    assert result.exit_code == 0, result.output
    assert "Loa A" in result.output and "(mặc định)" in result.output
    assert "Tai nghe B" in result.output


def test_record_unavailable_exits_1(monkeypatch, tmp_path):
    def broken(*args, **kwargs):
        return FakeRecorder(*args, start_error=recorder.RecorderUnavailableError("Không có loa"))

    monkeypatch.setattr(recorder, "Recorder", broken)
    result = runner.invoke(app, ["record", "-o", str(tmp_path)])
    assert result.exit_code == 1
    assert "Không có loa" in result.output


def test_record_without_ffmpeg_shows_instructions(monkeypatch, tmp_path):
    def broken(*args, **kwargs):
        return FakeRecorder(*args, start_error=downloader.FFmpegNotFoundError())

    monkeypatch.setattr(recorder, "Recorder", broken)
    result = runner.invoke(app, ["record", "-o", str(tmp_path)])
    assert result.exit_code == 1
    assert "ffmpeg" in result.output


# ------------------------------ Facebook/cookie ---------------------------- #
FB_URL = "https://www.facebook.com/reel/1234567890"


def _capture(monkeypatch, name):
    captured: dict = {}

    def fake(url, *args, **kwargs):
        captured.update(url=url, **kwargs)
        return [] if name != "get_info" else MediaInfo(id="1", title="FB video")

    monkeypatch.setattr(downloader, name, fake)
    return captured


def test_video_facebook_with_cookie_file(monkeypatch, tmp_path):
    captured = _capture(monkeypatch, "download_video")
    cookie = tmp_path / "c.txt"
    cookie.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    result = runner.invoke(app, ["video", FB_URL, "--cookies", str(cookie), "-o", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert captured["url"] == FB_URL
    assert captured["cookies"] == downloader.Cookies(file=cookie)


def test_audio_cookies_from_browser(monkeypatch, tmp_path):
    captured = _capture(monkeypatch, "download_audio")
    result = runner.invoke(
        app, ["audio", FB_URL, "--cookies-from-browser", "firefox", "-o", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert captured["cookies"] == downloader.Cookies(browser="firefox")


def test_default_cookie_file_is_used_and_announced(monkeypatch, tmp_path):
    from youtube_vault import config

    default = tmp_path / "default-cookies.txt"
    default.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    monkeypatch.setattr(config, "DEFAULT_COOKIES_FILE", default)
    captured = _capture(monkeypatch, "download_video")
    result = runner.invoke(app, ["video", FB_URL, "-o", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert captured["cookies"] == downloader.Cookies(file=default)
    assert "Dùng cookie đăng nhập" in result.output


def test_no_cookies_by_default(monkeypatch, tmp_path):
    captured = _capture(monkeypatch, "download_video")
    result = runner.invoke(app, ["video", FB_URL, "-o", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert captured["cookies"] is None


def test_missing_cookie_file_is_rejected(monkeypatch, tmp_path):
    _capture(monkeypatch, "download_video")
    result = runner.invoke(app, ["video", FB_URL, "--cookies", str(tmp_path / "nope.txt")])
    assert result.exit_code != 0


def test_both_cookie_sources_rejected(monkeypatch, tmp_path):
    _capture(monkeypatch, "download_video")
    cookie = tmp_path / "c.txt"
    cookie.write_text("", encoding="utf-8")
    result = runner.invoke(
        app, ["video", FB_URL, "--cookies", str(cookie), "--cookies-from-browser", "chrome"]
    )
    assert result.exit_code != 0
    assert "Chỉ chọn một nguồn" in result.output


def test_info_passes_cookies(monkeypatch):
    captured = _capture(monkeypatch, "get_info")
    result = runner.invoke(app, ["info", FB_URL, "--cookies-from-browser", "edge"])
    assert result.exit_code == 0, result.output
    assert captured["cookies"] == downloader.Cookies(browser="edge")
    assert "FB video" in result.output
