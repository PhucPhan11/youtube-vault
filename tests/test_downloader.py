from __future__ import annotations

import threading
from pathlib import Path

import pytest
from yt_dlp.utils import DownloadError

from youtube_vault import downloader
from youtube_vault.config import OUTPUT_TEMPLATE, QUALITY_PRESETS
from youtube_vault.downloader import (
    DownloadCancelledError,
    DownloadFailedError,
    FFmpegNotFoundError,
    ProgressEvent,
    download_audio,
    download_video,
    ffmpeg_install_instructions,
    get_info,
)
from youtube_vault.history import HistoryArchive, HistoryRecorder


# ---------------------------- download_video ------------------------------- #
@pytest.mark.parametrize("quality", ["best", "1080", "720", "480"])
def test_download_video_uses_quality_preset(fake_ydl, ffmpeg_present, tmp_path, quality):
    download_video("https://youtu.be/x", quality=quality, output_dir=tmp_path)

    opts = fake_ydl.instances[0].opts
    assert opts["format"] == QUALITY_PRESETS[quality]
    assert opts["merge_output_format"] == "mp4"
    assert opts["outtmpl"] == str(tmp_path / OUTPUT_TEMPLATE)
    assert opts["noplaylist"] is False
    assert fake_ydl.instances[0].calls == [("https://youtu.be/x", True)]


def test_height_presets_limit_resolution():
    assert "height<=1080" in QUALITY_PRESETS["1080"]
    assert "height<=720" in QUALITY_PRESETS["720"]
    assert "height<=480" in QUALITY_PRESETS["480"]


def test_download_video_rejects_invalid_quality(fake_ydl, ffmpeg_present, tmp_path):
    with pytest.raises(ValueError):
        download_video("https://youtu.be/x", quality="360", output_dir=tmp_path)
    assert fake_ydl.instances == []


def test_download_video_creates_output_dir(fake_ydl, ffmpeg_present, tmp_path):
    out = tmp_path / "a" / "b"
    download_video("https://youtu.be/x", output_dir=out)
    assert out.is_dir()


def test_download_video_returns_file_paths(fake_ydl, ffmpeg_present, tmp_path):
    fake_ydl.result = {
        "id": "abc",
        "requested_downloads": [{"filepath": str(tmp_path / "Video [abc].mp4")}],
    }
    files = download_video("https://youtu.be/x", output_dir=tmp_path)
    assert files == [tmp_path / "Video [abc].mp4"]


def test_download_video_playlist_returns_all_files(fake_ydl, ffmpeg_present, tmp_path):
    fake_ydl.result = {
        "_type": "playlist",
        "entries": [
            {"requested_downloads": [{"filepath": "a [1].mp4"}]},
            None,
            {"requested_downloads": [{"filepath": "b [2].mp4"}]},
        ],
    }
    files = download_video("https://youtube.com/playlist?list=PL", output_dir=tmp_path)
    assert files == [Path("a [1].mp4"), Path("b [2].mp4")]


def test_download_video_without_ffmpeg_raises(fake_ydl, ffmpeg_missing, tmp_path):
    with pytest.raises(FFmpegNotFoundError) as excinfo:
        download_video("https://youtu.be/x", output_dir=tmp_path)
    assert "ffmpeg" in excinfo.value.instructions
    assert fake_ydl.instances == []


def test_download_error_is_wrapped(fake_ydl, ffmpeg_present, tmp_path):
    fake_ydl.error = DownloadError("Video unavailable")
    with pytest.raises(DownloadFailedError, match="Video unavailable"):
        download_video("https://youtu.be/x", output_dir=tmp_path)


# ---------------------------- download_audio ------------------------------- #
@pytest.mark.parametrize("fmt", ["mp3", "m4a"])
def test_download_audio_uses_extract_audio_postprocessor(fake_ydl, ffmpeg_present, tmp_path, fmt):
    download_audio("https://youtu.be/x", fmt=fmt, output_dir=tmp_path)

    opts = fake_ydl.instances[0].opts
    assert opts["format"] == "bestaudio/best"
    assert opts["postprocessors"][0]["key"] == "FFmpegExtractAudio"
    assert opts["postprocessors"][0]["preferredcodec"] == fmt
    assert opts["outtmpl"] == str(tmp_path / OUTPUT_TEMPLATE)


def test_download_audio_rejects_invalid_format(fake_ydl, ffmpeg_present, tmp_path):
    with pytest.raises(ValueError):
        download_audio("https://youtu.be/x", fmt="flacx", output_dir=tmp_path)


def test_download_audio_without_ffmpeg_raises(fake_ydl, ffmpeg_missing, tmp_path):
    with pytest.raises(FFmpegNotFoundError):
        download_audio("https://youtu.be/x", output_dir=tmp_path)


# ------------------------------- progress ---------------------------------- #
def test_progress_callback_receives_events(fake_ydl, ffmpeg_present, tmp_path):
    fake_ydl.hook_events = [
        {
            "status": "downloading",
            "filename": "v.mp4",
            "downloaded_bytes": 50,
            "total_bytes_estimate": 200,
            "speed": 10.0,
            "eta": 15,
            "info_dict": {"playlist_index": 2, "n_entries": 5},
        },
        {"status": "finished", "filename": "v.mp4", "downloaded_bytes": 200, "total_bytes": 200},
    ]
    events: list[ProgressEvent] = []
    download_video("https://youtu.be/x", output_dir=tmp_path, progress_callback=events.append)

    assert [e.status for e in events] == ["downloading", "finished"]
    first = events[0]
    assert first.total_bytes == 200
    assert first.percent == pytest.approx(25.0)
    assert first.playlist_index == 2
    assert first.playlist_count == 5
    assert events[1].percent == pytest.approx(100.0)


def test_no_progress_hook_without_callback(fake_ydl, ffmpeg_present, tmp_path):
    download_video("https://youtu.be/x", output_dir=tmp_path)
    assert "progress_hooks" not in fake_ydl.instances[0].opts


def test_progress_event_percent_unknown_total():
    assert ProgressEvent(status="downloading", filename="x", downloaded_bytes=10).percent is None


# -------------------------------- get_info --------------------------------- #
def test_get_info_single_video(fake_ydl):
    fake_ydl.result = {
        "id": "jNQXAC9IVRw",
        "title": "Me at the zoo",
        "duration": 19,
        "uploader": "jawed",
        "webpage_url": "https://www.youtube.com/watch?v=jNQXAC9IVRw",
        "formats": [
            {"format_id": "18", "ext": "mp4", "resolution": "640x360", "height": 360,
             "vcodec": "avc1", "acodec": "mp4a", "filesize": 1000},
            {"format_id": "140", "ext": "m4a", "resolution": "audio only",
             "vcodec": "none", "acodec": "mp4a", "filesize_approx": 300},
        ],
    }
    info = get_info("https://www.youtube.com/watch?v=jNQXAC9IVRw")

    assert info.title == "Me at the zoo"
    assert info.duration == 19
    assert info.uploader == "jawed"
    assert info.is_playlist is False
    assert [f.format_id for f in info.formats] == ["18", "140"]
    assert info.formats[1].filesize == 300
    instance = fake_ydl.instances[0]
    assert instance.calls == [("https://www.youtube.com/watch?v=jNQXAC9IVRw", False)]
    assert instance.opts["skip_download"] is True


def test_get_info_playlist(fake_ydl):
    fake_ydl.result = {
        "_type": "playlist",
        "id": "PL1",
        "title": "My list",
        "entries": [
            {"id": "a", "title": "A", "duration": 60},
            {"id": "b", "title": "B", "duration": 3700},
        ],
    }
    info = get_info("https://youtube.com/playlist?list=PL1")

    assert info.is_playlist is True
    assert [e.title for e in info.entries] == ["A", "B"]


def test_get_info_error_is_wrapped(fake_ydl):
    fake_ydl.error = DownloadError("bad url")
    with pytest.raises(DownloadFailedError):
        get_info("https://youtu.be/bad")


def test_get_info_empty_result(fake_ydl):
    fake_ydl.result = None
    with pytest.raises(DownloadFailedError):
        get_info("https://youtu.be/x")


# ------------------------------ ffmpeg hint -------------------------------- #
@pytest.mark.parametrize(
    ("system", "expected"),
    [
        ("Windows", "winget install Gyan.FFmpeg"),
        ("Darwin", "brew install ffmpeg"),
        ("Linux", "sudo apt install ffmpeg"),
    ],
)
def test_ffmpeg_install_instructions_per_os(monkeypatch, system, expected):
    monkeypatch.setattr(downloader.platform, "system", lambda: system)
    text = ffmpeg_install_instructions()
    assert expected in text
    assert "https://ffmpeg.org/download.html" in text


# --------------------------- history & cancel ------------------------------ #
_DONE_INFO = {
    "id": "abc",
    "extractor_key": "Youtube",
    "title": "Clip",
    "webpage_url": "https://youtu.be/abc",
    "requested_downloads": [{"filepath": "out/Clip [abc].mp4"}],
}


def test_history_wires_archive_and_recorder(fake_ydl, ffmpeg_present, tmp_path, history):
    fake_ydl.result = dict(_DONE_INFO)
    files = download_video("https://youtu.be/abc", output_dir=tmp_path, history=history)

    inst = fake_ydl.instances[0]
    assert isinstance(inst.opts["download_archive"], HistoryArchive)
    assert inst.opts["download_archive"].kind == "video"
    assert [(type(pp), when) for pp, when in inst.post_processors] == [
        (HistoryRecorder, "after_move")
    ]
    assert files == [Path("out/Clip [abc].mp4")]
    rec = history.get("youtube abc", "video")
    assert rec is not None and rec.title == "Clip"


def test_already_downloaded_is_skipped(fake_ydl, ffmpeg_present, tmp_path, history):
    history.add("youtube abc", "video", title="Clip", filepath="old.mp4")
    fake_ydl.result = dict(_DONE_INFO)
    events: list[ProgressEvent] = []

    files = download_video(
        "https://youtu.be/abc", output_dir=tmp_path, history=history,
        progress_callback=events.append,
    )

    assert files == []
    assert [(e.status, e.title, e.filename) for e in events] == [("skipped", "Clip", "old.mp4")]


def test_audio_not_skipped_when_only_video_in_history(fake_ydl, ffmpeg_present, tmp_path, history):
    history.add("youtube abc", "video")
    fake_ydl.result = dict(_DONE_INFO)
    files = download_audio("https://youtu.be/abc", output_dir=tmp_path, history=history)
    assert files  # vẫn tải
    assert history.contains("youtube abc", "audio")


def test_force_disables_archive_but_still_records(fake_ydl, ffmpeg_present, tmp_path, history):
    history.add("youtube abc", "video", title="Old")
    fake_ydl.result = dict(_DONE_INFO)
    files = download_video(
        "https://youtu.be/abc", output_dir=tmp_path, history=history, force=True
    )
    inst = fake_ydl.instances[0]
    assert "download_archive" not in inst.opts
    assert len(inst.post_processors) == 1
    assert files
    assert history.get("youtube abc", "video").title == "Clip"


def test_without_history_no_archive_no_recorder(fake_ydl, ffmpeg_present, tmp_path):
    download_video("https://youtu.be/x", output_dir=tmp_path)
    inst = fake_ydl.instances[0]
    assert "download_archive" not in inst.opts
    assert inst.post_processors == []


def test_cancel_event_already_set(fake_ydl, ffmpeg_present, tmp_path):
    ev = threading.Event()
    ev.set()
    with pytest.raises(DownloadCancelledError):
        download_video("https://youtu.be/x", output_dir=tmp_path, cancel_event=ev)
    assert fake_ydl.instances == []


def test_cancel_during_download_raises(fake_ydl, ffmpeg_present, tmp_path):
    ev = threading.Event()
    fake_ydl.hook_events = [
        {"status": "downloading", "filename": "v.mp4", "downloaded_bytes": 1},
        {"status": "downloading", "filename": "v.mp4", "downloaded_bytes": 2},
    ]
    seen: list[ProgressEvent] = []

    def cb(e: ProgressEvent) -> None:
        seen.append(e)
        ev.set()  # dừng sau sự kiện đầu tiên

    with pytest.raises(DownloadCancelledError):
        download_video("https://youtu.be/x", output_dir=tmp_path, progress_callback=cb, cancel_event=ev)
    assert len(seen) == 1


def test_cancel_hook_installed_without_callback(fake_ydl, ffmpeg_present, tmp_path):
    download_video("https://youtu.be/x", output_dir=tmp_path, cancel_event=threading.Event())
    assert len(fake_ydl.instances[0].opts["progress_hooks"]) == 1


def test_progress_event_has_title(fake_ydl, ffmpeg_present, tmp_path):
    fake_ydl.hook_events = [
        {"status": "downloading", "filename": "v.mp4", "info_dict": {"title": "Hello"}},
    ]
    events: list[ProgressEvent] = []
    download_video("https://youtu.be/x", output_dir=tmp_path, progress_callback=events.append)
    assert events[0].title == "Hello"
