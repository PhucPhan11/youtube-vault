from __future__ import annotations

import threading
from pathlib import Path

import pytest
from yt_dlp.cookies import CookieLoadError
from yt_dlp.utils import DownloadError

from youtube_vault import config, downloader
from youtube_vault.config import (
    OUTPUT_TEMPLATE,
    QUALITY_PRESETS,
    VIDEO_FORMAT,
    VIDEO_FORMAT_PREFERENCE,
)
from youtube_vault.downloader import (
    Cookies,
    DownloadCancelledError,
    DownloadFailedError,
    FFmpegNotFoundError,
    FormatInfo,
    MediaInfo,
    ProgressEvent,
    ResolutionOption,
    download_audio,
    download_video,
    ffmpeg_install_instructions,
    format_sort_for,
    friendly_error,
    get_info,
    list_resolutions,
    normalize_quality,
)
from youtube_vault.history import HistoryArchive, HistoryRecorder, make_archive_id


# ---------------------------- download_video ------------------------------- #
@pytest.mark.parametrize("quality", list(QUALITY_PRESETS))
def test_download_video_uses_quality_preset(fake_ydl, ffmpeg_present, tmp_path, quality):
    download_video("https://youtu.be/x", quality=quality, output_dir=tmp_path)

    opts = fake_ydl.instances[0].opts
    assert opts["format"] == VIDEO_FORMAT
    assert opts["format_sort"] == QUALITY_PRESETS[quality]
    assert opts["merge_output_format"] == "mp4"
    assert opts["outtmpl"] == str(tmp_path / OUTPUT_TEMPLATE)
    assert opts["noplaylist"] is False
    assert fake_ydl.instances[0].calls == [("https://youtu.be/x", True)]


@pytest.mark.parametrize(
    ("quality", "expected"),
    [
        ("804", ["res:804", VIDEO_FORMAT_PREFERENCE]),  # mức không chuẩn (video 21:9)
        ("720p", ["res:720", VIDEO_FORMAT_PREFERENCE]),
        ("best", ["res", VIDEO_FORMAT_PREFERENCE]),
    ],
)
def test_download_video_accepts_any_height(fake_ydl, ffmpeg_present, tmp_path, quality, expected):
    download_video("https://youtu.be/x", quality=quality, output_dir=tmp_path)
    assert fake_ydl.instances[0].opts["format_sort"] == expected


def test_format_sort_returns_copy():
    format_sort_for("720").append("x")
    assert QUALITY_PRESETS["720"] == ["res:720", VIDEO_FORMAT_PREFERENCE]


@pytest.mark.parametrize("bad", ["abc", "0", "-5", "", "720x", "p"])
def test_download_video_rejects_invalid_quality(fake_ydl, ffmpeg_present, tmp_path, bad):
    with pytest.raises(ValueError):
        download_video("https://youtu.be/x", quality=bad, output_dir=tmp_path)
    assert fake_ydl.instances == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("best", "best"), (" BEST ", "best"), ("1080", "1080"), ("720p", "720"), ("0144", "144")],
)
def test_normalize_quality(raw, expected):
    assert normalize_quality(raw) == expected


# ------------------- chọn định dạng thật của yt-dlp (offline) -------------- #
def _fmt(fid, w, h, ext, vcodec, acodec="none"):
    return dict(format_id=fid, url=f"https://example.invalid/{fid}", protocol="https", ext=ext,
                vcodec=vcodec, acodec=acodec, width=w, height=h, tbr=float(w or 1) * float(h or 1))


_AUDIO = [_fmt("140", None, None, "m4a", "none", "mp4a.40.2"),
          _fmt("251", None, None, "webm", "none", "opus")]
FORMAT_SETS = {
    # 1440p chỉ có webm/vp9: quy tắc cũ (ưu tiên mp4 trước) sẽ tụt xuống 1080p.
    "landscape": _AUDIO + [
        _fmt("313", 3840, 2160, "webm", "vp9"), _fmt("271", 2560, 1440, "webm", "vp9"),
        _fmt("137", 1920, 1080, "mp4", "avc1"), _fmt("248", 1920, 1080, "webm", "vp9"),
        _fmt("136", 1280, 720, "mp4", "avc1"), _fmt("134", 640, 360, "mp4", "avc1"),
    ],
    # Shorts: video dọc, height > width.
    "vertical": _AUDIO + [
        _fmt("137", 1080, 1920, "mp4", "avc1"), _fmt("136", 720, 1280, "mp4", "avc1"),
        _fmt("134", 360, 640, "mp4", "avc1"),
    ],
    # Phim 21:9: "1080p" thực ra là 1920x804.
    "cinema": _AUDIO + [
        _fmt("137", 1920, 804, "mp4", "avc1"), _fmt("136", 1280, 536, "mp4", "avc1"),
    ],
}


def _select(formats, quality) -> dict:
    """Cho yt-dlp THẬT chọn định dạng (không mạng, không tải) với opts của download_video."""
    import yt_dlp

    opts = {"quiet": True, "format": VIDEO_FORMAT, "format_sort": format_sort_for(quality)}
    info = {"id": "x", "title": "t", "extractor": "youtube", "extractor_key": "Youtube",
            "webpage_url": "https://youtu.be/x", "formats": [dict(f) for f in formats]}
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.process_ie_result(info, download=False)


@pytest.mark.parametrize(
    ("format_set", "quality", "expected"),
    [
        ("landscape", "best", (3840, 2160)),
        ("landscape", "1440", (2560, 1440)),
        ("landscape", "1080", (1920, 1080)),
        ("landscape", "480", (640, 360)),    # không có 480 -> mức gần nhất bên dưới
        ("landscape", "144", (640, 360)),    # thấp hơn mức nhỏ nhất -> lấy mức nhỏ nhất
        ("vertical", "1080", (1080, 1920)),
        ("vertical", "720", (720, 1280)),
        ("cinema", "1080", (1920, 804)),
        ("cinema", "720", (1280, 536)),
    ],
)
def test_real_ytdlp_picks_requested_resolution(format_set, quality, expected):
    result = _select(FORMAT_SETS[format_set], quality)
    video, audio = result["requested_formats"]
    assert (video["width"], video["height"]) == expected
    assert audio["ext"] == "m4a"


def test_real_ytdlp_prefers_mp4_at_same_resolution():
    # 1080p có cả mp4/avc1 (137) và webm/vp9 (248): chọn mp4 để ghép mp4 tương thích hơn.
    assert _select(FORMAT_SETS["landscape"], "1080")["format_id"] == "137+140"


# ----------------------------- list_resolutions ----------------------------- #
def _media(formats, **kw):
    return MediaInfo(id="x", title="t", formats=formats, **kw)


def test_list_resolutions_groups_and_sorts():
    media = _media([
        FormatInfo("sb0", ext="mhtml", vcodec="none", acodec="none", width=160, height=90),
        FormatInfo("140", ext="m4a", vcodec="none", acodec="mp4a", filesize=3_000_000),
        FormatInfo("251", ext="webm", vcodec="none", acodec="opus", filesize=4_000_000),
        FormatInfo("299", ext="mp4", vcodec="avc1", width=1920, height=1080, fps=60,
                   filesize=90_000_000, format_note="1080p60"),
        FormatInfo("303", ext="webm", vcodec="vp9", width=1920, height=1080, fps=60,
                   filesize=70_000_000, format_note="1080p60"),
        FormatInfo("313", ext="webm", vcodec="vp9", width=3840, height=2160, fps=30,
                   filesize=None, format_note="2160p"),
        FormatInfo("136", ext="mp4", vcodec="avc1", width=1280, height=720, fps=30,
                   filesize=20_000_000, format_note="720p"),
    ])
    options = list_resolutions(media)

    assert [o.height for o in options] == [2160, 1080, 720]
    assert [o.label for o in options] == ["2160p (4K)", "1080p60", "720p"]
    by_height = {o.height: o for o in options}
    assert by_height[1080].filesize == 90_000_000 + 3_000_000  # video lớn nhất + audio m4a
    assert by_height[2160].filesize is None  # không biết dung lượng video -> không ước tính
    assert by_height[720].quality == "720"


def test_list_resolutions_uses_short_side_without_note():
    media = _media([
        FormatInfo("v1", vcodec="avc1", width=1080, height=1920),  # Shorts
        FormatInfo("v2", vcodec="avc1", width=720, height=1280),
        FormatInfo("v3", vcodec="avc1", width=1920, height=804),   # 21:9, không có note
    ])
    assert [o.height for o in list_resolutions(media)] == [1080, 804, 720]


def test_list_resolutions_prefers_format_note():
    media = _media([FormatInfo("v", vcodec="avc1", width=1920, height=804, format_note="1080p")])
    assert [o.height for o in list_resolutions(media)] == [1080]


def test_list_resolutions_empty_for_playlist_and_audio_only():
    assert list_resolutions(_media([], is_playlist=True)) == []
    assert list_resolutions(_media([FormatInfo("140", vcodec="none", acodec="mp4a")])) == []


def test_resolution_label():
    assert ResolutionOption(4320).label == "4320p (8K)"
    assert ResolutionOption(1440, fps=60).label == "1440p60"
    assert ResolutionOption(1080, fps=59.94).label == "1080p60"
    assert ResolutionOption(720, fps=30).label == "720p"


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
    # file nguồn có đuôi phụ, không trùng tên với file video mp4
    assert opts["outtmpl"] == str(tmp_path / config.AUDIO_SOURCE_TEMPLATE)
    pps = fake_ydl.instances[0].post_processors
    assert any(isinstance(pp, downloader.StripAudioSourceMarker) and when == "post_process"
               for pp, when in pps)


@pytest.mark.parametrize(("bitrate", "expected"), [("best", "0"), ("320", "320"), ("128", "128")])
def test_download_audio_bitrate(fake_ydl, ffmpeg_present, tmp_path, bitrate, expected):
    download_audio("https://youtu.be/x", output_dir=tmp_path, audio_quality=bitrate)
    pp = fake_ydl.instances[0].opts["postprocessors"][0]
    assert pp["preferredquality"] == expected


def test_download_audio_rejects_invalid_bitrate(fake_ydl, ffmpeg_present, tmp_path):
    with pytest.raises(ValueError, match="Bitrate"):
        download_audio("https://youtu.be/x", output_dir=tmp_path, audio_quality="999")
    assert fake_ydl.instances == []


@pytest.mark.parametrize("fmt", ["mp3", "m4a"])
def test_download_audio_embeds_metadata_and_cover(fake_ydl, ffmpeg_present, tmp_path, fmt):
    download_audio("https://youtu.be/x", fmt=fmt, output_dir=tmp_path)  # mặc định: có gắn
    opts = fake_ydl.instances[0].opts
    assert [pp["key"] for pp in opts["postprocessors"]] == [
        "FFmpegExtractAudio",
        "FFmpegMetadata",
        "EmbedThumbnail",
    ]
    assert opts["writethumbnail"] is True


@pytest.mark.parametrize("fmt", ["opus", "wav"])
def test_download_audio_embed_without_cover_for_other_formats(fake_ydl, ffmpeg_present, tmp_path, fmt):
    download_audio("https://youtu.be/x", fmt=fmt, output_dir=tmp_path)
    opts = fake_ydl.instances[0].opts
    assert [pp["key"] for pp in opts["postprocessors"]] == ["FFmpegExtractAudio", "FFmpegMetadata"]
    assert "writethumbnail" not in opts


def test_download_audio_no_embed(fake_ydl, ffmpeg_present, tmp_path):
    download_audio("https://youtu.be/x", output_dir=tmp_path, embed_metadata=False)
    opts = fake_ydl.instances[0].opts
    assert [pp["key"] for pp in opts["postprocessors"]] == ["FFmpegExtractAudio"]
    assert "writethumbnail" not in opts


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


# --------------------------- Facebook / cookie ----------------------------- #

FB_URL = "https://www.facebook.com/watch/?v=1234567890"


def _cookie_file(tmp_path: Path) -> Path:
    f = tmp_path / "cookies.txt"
    f.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    return f


def test_cookies_resolve_none_without_default(tmp_path):
    assert Cookies.resolve() is None  # conftest trỏ file mặc định vào tmp_path (chưa tồn tại)


def test_cookies_resolve_uses_default_file_when_present():
    default = Path(config.DEFAULT_COOKIES_FILE)
    default.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    assert Cookies.resolve() == Cookies(file=default)


def test_cookies_resolve_explicit(tmp_path):
    f = _cookie_file(tmp_path)
    assert Cookies.resolve(file=f) == Cookies(file=f)
    assert Cookies.resolve(browser="Firefox") == Cookies(browser="firefox")


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"file": "khong-co.txt"}, "Không tìm thấy file cookie"),
        ({"browser": "netscape"}, "Trình duyệt không được hỗ trợ"),
        ({"file": "a.txt", "browser": "chrome"}, "Chỉ chọn một nguồn"),
    ],
)
def test_cookies_resolve_rejects_invalid(kwargs, match):
    with pytest.raises(ValueError, match=match):
        Cookies.resolve(**kwargs)


def test_video_passes_cookie_file(fake_ydl, ffmpeg_present, tmp_path):
    f = _cookie_file(tmp_path)
    download_video(FB_URL, output_dir=tmp_path, cookies=Cookies(file=f))
    opts = fake_ydl.instances[0].opts
    assert opts["cookiefile"] == str(f)
    assert "cookiesfrombrowser" not in opts


def test_audio_passes_cookies_from_browser(fake_ydl, ffmpeg_present, tmp_path):
    download_audio(FB_URL, output_dir=tmp_path, cookies=Cookies(browser="firefox"))
    opts = fake_ydl.instances[0].opts
    assert opts["cookiesfrombrowser"] == ("firefox",)
    assert "cookiefile" not in opts


def test_no_cookies_by_default(fake_ydl, ffmpeg_present, tmp_path):
    download_video(FB_URL, output_dir=tmp_path)
    get_info(FB_URL)
    for inst in fake_ydl.instances:
        assert "cookiefile" not in inst.opts and "cookiesfrombrowser" not in inst.opts


def test_get_info_passes_cookies(fake_ydl, tmp_path):
    f = _cookie_file(tmp_path)
    get_info(FB_URL, cookies=Cookies(file=f))
    assert fake_ydl.instances[0].opts["cookiefile"] == str(f)


def test_friendly_error_facebook_login():
    msg = "ERROR: [facebook] 123: This video is only available for registered users. Use --cookies"
    out = friendly_error(msg)
    assert out.startswith(msg)
    assert "cần đăng nhập" in out and "cookies.txt" in out


def test_friendly_error_chrome_cookie_decrypt():
    out = friendly_error("Failed to decrypt with DPAPI. See https://github.com/yt-dlp/yt-dlp/issues/10927 (cookie)")
    assert "Chrome/Edge" in out and "--cookies" in out


def test_friendly_error_leaves_other_messages():
    assert friendly_error("ERROR: Video unavailable") == "ERROR: Video unavailable"
    assert friendly_error("ERROR: [youtube] abc: Video unavailable") == "ERROR: [youtube] abc: Video unavailable"
    # có chữ "login" nhưng không rõ trang nào: giữ nguyên
    assert friendly_error("login required") == "login required"


def test_facebook_login_error_gets_hint(fake_ydl, ffmpeg_present, tmp_path):
    fake_ydl.error = DownloadError("ERROR: [facebook] 123: Cannot parse data; please report this issue")
    with pytest.raises(DownloadFailedError, match="cần đăng nhập"):
        download_video(FB_URL, output_dir=tmp_path)


def test_cookie_load_error_is_wrapped_with_cause(fake_ydl, ffmpeg_present, tmp_path):
    try:
        try:
            raise PermissionError("Could not copy Chrome cookie database")
        except PermissionError:
            raise CookieLoadError("failed to load cookies")
    except CookieLoadError as exc:
        fake_ydl.error = exc
    with pytest.raises(DownloadFailedError) as excinfo:
        download_video(FB_URL, output_dir=tmp_path, cookies=Cookies(browser="chrome"))
    msg = str(excinfo.value)
    assert "Could not copy Chrome cookie database" in msg
    assert "cookies.txt" in msg


def test_cookie_load_error_in_get_info(fake_ydl):
    try:
        try:
            raise OSError("Failed to decrypt with DPAPI")
        except OSError:
            raise CookieLoadError("failed to load cookies")
    except CookieLoadError as exc:
        fake_ydl.error = exc
    with pytest.raises(DownloadFailedError, match="DPAPI"):
        get_info(FB_URL, cookies=Cookies(browser="edge"))


def test_facebook_history_key():
    assert make_archive_id({"extractor_key": "Facebook", "id": "1234567890"}) == "facebook 1234567890"


def test_title_is_capped_in_output_template():
    assert f"%(title).{config.MAX_TITLE_BYTES}B" in OUTPUT_TEMPLATE
    assert config.AUDIO_SOURCE_TEMPLATE.endswith(f"{config.AUDIO_SOURCE_MARKER}.%(ext)s")


def test_long_title_is_truncated_by_real_ytdlp(tmp_path):
    """Dùng YoutubeDL thật (không mạng) để kiểm tra mẫu tên file cắt tiêu đề dài."""
    import yt_dlp

    info = {"id": "123", "title": "Bài đăng Facebook rất dài " * 40, "ext": "mp4"}
    with yt_dlp.YoutubeDL({"outtmpl": str(tmp_path / OUTPUT_TEMPLATE), "quiet": True}) as ydl:
        name = Path(ydl.prepare_filename(info)).name
    assert name.endswith(" [123].mp4")  # id và đuôi file luôn còn nguyên
    title = name[: -len(" [123].mp4")]
    assert len(title.encode("utf-8")) <= config.MAX_TITLE_BYTES


@pytest.mark.parametrize("ext", ["mp3", "m4a"])
def test_strip_audio_source_marker_renames(tmp_path, ext):
    src = tmp_path / f"Clip [abc]{config.AUDIO_SOURCE_MARKER}.{ext}"
    src.write_bytes(b"audio")
    (tmp_path / f"Clip [abc].{ext}").write_bytes(b"old")  # file cũ cùng tên bị ghi đè
    files, info = downloader.StripAudioSourceMarker().run({"filepath": str(src)})
    target = tmp_path / f"Clip [abc].{ext}"
    assert files == []
    assert info["filepath"] == str(target)
    assert target.read_bytes() == b"audio"
    assert not src.exists()


def test_strip_audio_source_marker_ignores_other_files(tmp_path):
    video = tmp_path / "Clip [abc].mp4"
    video.write_bytes(b"v")
    _, info = downloader.StripAudioSourceMarker().run({"filepath": str(video)})
    assert info["filepath"] == str(video) and video.exists()


# ------------------------ Instagram / X / trang khác ----------------------- #
@pytest.mark.parametrize(
    ("message", "site"),
    [
        ("ERROR: [instagram] Chunk8: Requested content is not available, rate-limit reached or "
         "login required. Use --cookies", "Instagram"),
        ("ERROR: [Instagram] abc: login required", "Instagram"),
        ("ERROR: [twitter] 123: NSFW tweet requires authentication. Use --cookies", "X (Twitter)"),
        ("ERROR: [twitter:broadcast] 1: This content is protected", "X (Twitter)"),
        ("ERROR: [youtube] xyz: Sign in to confirm you're not a bot. Use --cookies", "YouTube"),
        ("ERROR: [vimeo] 5: login required", "Vimeo"),
        ("ERROR: [someblog] 5: login required", "Someblog"),
    ],
)
def test_friendly_error_login_hint_names_site(message, site):
    out = friendly_error(message)
    assert out.startswith(message)
    assert f"cần đăng nhập {site}" in out
    assert "cookies.txt" in out


def test_site_name():
    assert downloader.site_name("twitter:amplify") == "X (Twitter)"
    assert downloader.site_name("Instagram") == "Instagram"
    assert downloader.site_name("dailymotion") == "Dailymotion"
    assert downloader.site_name("newsite") == "Newsite"


def test_silent_logger_in_all_opts(fake_ydl, ffmpeg_present, tmp_path):
    download_video("https://x.com/a/status/1", output_dir=tmp_path)
    download_audio("https://x.com/a/status/1", output_dir=tmp_path)
    get_info("https://x.com/a/status/1")
    for inst in fake_ydl.instances:
        logger = inst.opts["logger"]
        for level in ("debug", "info", "warning", "error"):
            assert getattr(logger, level)("ERROR: something") is None  # không in, không lỗi


def test_ytdlp_does_not_print_errors_itself(capsys, tmp_path):
    """Với YoutubeDL thật: lỗi chỉ đi theo exception, không bị yt-dlp tự in ra console."""
    import yt_dlp

    opts = downloader._base_opts(tmp_path, None)
    with yt_dlp.YoutubeDL(opts) as ydl, pytest.raises(DownloadError):
        ydl.report_error("[twitter] 1: boom")
    captured = capsys.readouterr()
    assert "boom" not in captured.out and "boom" not in captured.err


def _audio_source(tmp_path: Path, ext: str) -> Path:
    return tmp_path / f"Video [abc]{config.AUDIO_SOURCE_MARKER}.{ext}"


def test_audio_without_sound_raises_no_audio_and_cleans_up(fake_ydl, ffmpeg_present, tmp_path):
    src = _audio_source(tmp_path, "mp4")
    thumb = _audio_source(tmp_path, "jpg")
    part = tmp_path / f"Other [zzz]{config.AUDIO_SOURCE_MARKER}.mp4.part"  # lượt tải khác: giữ nguyên
    video = tmp_path / "Video [abc].mp4"  # file video đã tải trước đó: giữ nguyên
    for f in (src, thumb, part, video):
        f.write_bytes(b"x")
    fake_ydl.hook_events = [{"status": "finished", "filename": str(src), "downloaded_bytes": 1}]
    fake_ydl.postprocess_error = DownloadError(
        "ERROR: Postprocessing: WARNING: unable to obtain file audio codec with ffprobe"
    )
    with pytest.raises(downloader.NoAudioStreamError, match="không có âm thanh"):
        download_audio("https://www.instagram.com/reel/abc/", output_dir=tmp_path)
    assert not src.exists() and not thumb.exists()
    assert part.exists() and video.exists()


def test_no_audio_error_is_not_retried_by_queue(monkeypatch, tmp_path):
    import time as _time

    from youtube_vault.download_queue import DownloadQueue, ItemStatus

    calls = []

    def fake_audio(url, **kw):
        calls.append(url)
        raise downloader.NoAudioStreamError("Video này không có âm thanh")

    monkeypatch.setattr(downloader, "download_audio", fake_audio)
    q = DownloadQueue(retry_delays=(0,))
    item = q.add("u", kind="audio", output_dir=tmp_path)
    deadline = _time.monotonic() + 3
    while item.status != ItemStatus.FAILED and _time.monotonic() < deadline:
        _time.sleep(0.01)
    q.shutdown(timeout=1)
    assert item.status == ItemStatus.FAILED
    assert calls == ["u"]  # lỗi không tự khỏi -> không thử lại
    assert "không có âm thanh" in item.error


def test_other_audio_failure_keeps_partials_and_is_download_failed(fake_ydl, ffmpeg_present, tmp_path):
    part = _audio_source(tmp_path, "m4a.part")
    part.write_bytes(b"x")
    fake_ydl.error = DownloadError("ERROR: [instagram] abc: HTTP Error 503")
    with pytest.raises(DownloadFailedError):
        download_audio("https://www.instagram.com/reel/abc/", output_dir=tmp_path)
    assert part.exists()  # lỗi mạng: giữ .part để tải tiếp


def test_list_supported_sites():
    all_sites = downloader.list_supported_sites()
    assert len(all_sites) > 500
    assert "generic" not in {s.lower() for s in all_sites}
    assert len({s.lower() for s in all_sites}) == len(all_sites)  # không trùng hoa/thường
    insta = downloader.list_supported_sites("INSTA")
    assert "Instagram" in insta and all("insta" in s.lower() for s in insta)
    assert "twitter" in downloader.list_supported_sites("twitter")
    assert downloader.list_supported_sites("zzz-khong-co") == []
