from __future__ import annotations

import time
from pathlib import Path

import pytest

from youtube_vault import downloader
from youtube_vault.download_queue import DownloadItem, DownloadQueue, ItemStatus
from youtube_vault.downloader import (
    DownloadFailedError,
    FormatInfo,
    MediaInfo,
    ProgressEvent,
    ResolutionOption,
)
from youtube_vault.gui import (
    bitrate_choices,
    format_row,
    item_percent,
    parse_urls,
    quality_choices,
)


# ------------------------------ hàm thuần ---------------------------------- #
def test_parse_urls():
    text = """
    https://youtu.be/a
    # ghi chú
    https://youtu.be/b   https://youtu.be/a
    not-a-url
    http://example.com/v
    """
    valid, invalid = parse_urls(text)
    assert valid == ["https://youtu.be/a", "https://youtu.be/b", "http://example.com/v"]
    assert invalid == ["not-a-url"]


def test_parse_urls_empty():
    assert parse_urls("  \n\n") == ([], [])


def _item(**kw) -> DownloadItem:
    defaults = dict(id=1, url="https://youtu.be/x", output_dir=Path("."))
    defaults.update(kw)
    return DownloadItem(**defaults)


def test_format_row_downloading():
    item = _item(status=ItemStatus.DOWNLOADING, title="Clip", quality="720")
    item.progress = ProgressEvent("downloading", "f", 512, 1024, speed=2048, eta=75)
    assert format_row(item) == ("Clip", "Video 720", "Đang tải", "50.0%", "2.0 KB/s", "01:15")


def test_format_row_queued_uses_url_and_audio_label():
    item = _item(kind="audio", audio_format="m4a")
    assert format_row(item) == ("https://youtu.be/x", "Audio m4a", "Đang chờ", "", "", "")


def test_format_row_audio_shows_bitrate():
    assert format_row(_item(kind="audio", audio_quality="320"))[1] == "Audio mp3 320k"
    assert format_row(_item(kind="audio", audio_quality="best"))[1] == "Audio mp3"
    # wav không có bitrate
    assert format_row(_item(kind="audio", audio_format="wav", audio_quality="192"))[1] == "Audio wav"


def test_bitrate_choices():
    choices = bitrate_choices()
    assert choices[0] == ("Tốt nhất (VBR)", "best")
    assert ("320 kbps", "320") in choices
    assert choices[-1] == ("128 kbps", "128")


def test_format_row_failed_shows_first_error_line():
    item = _item(status=ItemStatus.FAILED, error="ERROR: Video unavailable\nchi tiết")
    assert format_row(item)[2] == "Lỗi: ERROR: Video unavailable"


def test_format_row_retrying_and_playlist():
    item = _item(status=ItemStatus.RETRYING, attempts=2)
    item.progress = ProgressEvent("downloading", "f", 1, 4, playlist_index=2, playlist_count=5)
    row = format_row(item, max_retries=3)
    assert row[2] == "Đang thử lại (2/3)"
    assert row[3] == "[2/5] 25.0%"
    assert row[4] == "" and row[5] == ""  # chỉ hiện tốc độ khi đang tải


def test_format_row_completed():
    assert format_row(_item(status=ItemStatus.COMPLETED))[3] == "100%"


def test_item_percent():
    assert item_percent(_item(status=ItemStatus.SKIPPED)) == 100.0
    assert item_percent(_item()) == 0.0


def test_quality_choices_fixed_list():
    choices = quality_choices()
    assert choices[0] == ("Tốt nhất", "best")
    assert choices[1:4] == [("4320p (8K)", "4320"), ("2160p (4K)", "2160"), ("1440p", "1440")]
    assert choices[-1] == ("144p", "144")


def test_quality_choices_real_list_with_size():
    choices = quality_choices([
        ResolutionOption(1080, fps=60, filesize=85 * 1024 * 1024),
        ResolutionOption(804),
    ])
    assert choices == [("1080p60 · ~85.0 MB", "1080"), ("804p", "804")]


# ------------------------------ smoke test --------------------------------- #
@pytest.fixture
def tk_root():
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Không có màn hình cho Tk: {exc}")
    root.withdraw()
    yield root
    try:
        root.destroy()
    except tk.TclError:
        pass


def _pump(root, cond, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root.update()
        if cond():
            return
        time.sleep(0.01)
    raise AssertionError("Hết thời gian chờ GUI")


def test_app_end_to_end_with_fake_download(tk_root, monkeypatch, tmp_path, history):
    from youtube_vault.gui import App

    def fake_download(url, **kwargs):
        final = Path(kwargs["output_dir"]) / "Clip [abc].mp4"
        kwargs["progress_callback"](ProgressEvent("downloading", str(final), 5, 10, title="Clip"))
        kwargs["history"].add("youtube abc", "video", title="Clip", filepath=str(final))
        kwargs["progress_callback"](ProgressEvent("finished", str(final), 10, 10, title="Clip"))
        return [final]

    monkeypatch.setattr(downloader, "download_video", fake_download)
    queue = DownloadQueue(history=history, retry_delays=(0,))
    app = App(tk_root, history=history, download_queue=queue, check_ffmpeg=False)
    app.pack()

    app.url_text.insert("1.0", "https://youtu.be/abc\n")
    app.out_var.set(str(tmp_path))
    items = app.add_from_input()
    assert len(items) == 1
    assert app.url_text.get("1.0", "end").strip() == ""

    _pump(tk_root, lambda: app.queue_tree.exists(str(items[0].id))
          and app.queue_tree.set(str(items[0].id), "status") == "Hoàn tất")
    assert app.queue_tree.set(str(items[0].id), "title") == "Clip"
    assert "Xong 1" in app.summary_var.get()
    # tab lịch sử được làm mới sau khi tải xong
    _pump(tk_root, lambda: len(app.history_tree.get_children()) == 1)

    app.search_var.set("không-khớp")
    app.refresh_history()
    assert app.history_tree.get_children() == ()

    app.remove_finished()
    assert app.queue_tree.get_children() == ()

    app.close()


def test_app_kind_switch_changes_options(tk_root, history):
    from youtube_vault.gui import App

    app = App(tk_root, history=history, download_queue=DownloadQueue(), check_ffmpeg=False)
    app.kind_var.set("audio")
    app._on_kind_change()
    assert "mp3" in app.option_combo.cget("values")
    assert str(app.res_button.cget("state")) == "disabled"  # không áp dụng cho audio
    app.kind_var.set("video")
    app._on_kind_change()
    assert "1080p" in app.option_combo.cget("values")
    assert str(app.res_button.cget("state")) == "normal"
    app.close()


def test_app_mp3_button_adds_audio_mp3_even_in_video_mode(tk_root, monkeypatch, tmp_path, history):
    from youtube_vault.gui import App

    calls: list[dict] = []

    def fake_audio(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return []

    def fail_video(url, **kwargs):  # không được gọi
        raise AssertionError("không được tải video")

    monkeypatch.setattr(downloader, "download_audio", fake_audio)
    monkeypatch.setattr(downloader, "download_video", fail_video)
    queue = DownloadQueue(history=history, retry_delays=(0,))
    app = App(tk_root, history=history, download_queue=queue, check_ffmpeg=False)
    assert app.kind_var.get() == "video"
    app.audio_var.set("m4a")  # nút "Tải MP3" bỏ qua định dạng đang chọn
    app.bitrate_var.set("192 kbps")
    app.embed_var.set(False)
    app.url_text.insert("1.0", "https://youtu.be/abc\n")
    app.out_var.set(str(tmp_path))

    items = app.add_mp3_from_input()
    assert len(items) == 1
    item = items[0]
    assert (item.kind, item.audio_format, item.audio_quality) == ("audio", "mp3", "192")
    assert item.embed_metadata is False
    _pump(tk_root, lambda: len(calls) == 1)
    assert calls[0]["fmt"] == "mp3"
    assert calls[0]["audio_quality"] == "192"
    assert calls[0]["embed_metadata"] is False
    app.close()


def test_app_bitrate_disabled_for_wav(tk_root, history):
    from youtube_vault.gui import App

    app = App(tk_root, history=history, download_queue=DownloadQueue(), check_ffmpeg=False)
    app.kind_var.set("audio")
    app._on_kind_change()
    assert str(app.bitrate_combo.cget("state")) == "readonly"
    app.audio_var.set("wav")
    app._update_audio_options()
    assert str(app.bitrate_combo.cget("state")) == "disabled"
    app.audio_var.set("mp3")
    app._update_audio_options()
    assert str(app.bitrate_combo.cget("state")) == "readonly"
    app.close()


def _media_1440_720() -> MediaInfo:
    return MediaInfo(
        id="x",
        title="Clip thật",
        formats=[
            FormatInfo("271", vcodec="vp9", width=2560, height=1440, format_note="1440p"),
            FormatInfo("136", vcodec="avc1", width=1280, height=720, format_note="720p",
                       filesize=10 * 1024 * 1024),
        ],
    )


def test_app_fetch_resolutions_fills_real_list(tk_root, monkeypatch, tmp_path, history):
    from youtube_vault.gui import App

    fetched = []

    def fake_get_info(url):
        fetched.append(url)
        return _media_1440_720()

    calls = []
    monkeypatch.setattr(downloader, "get_info", fake_get_info)
    monkeypatch.setattr(downloader, "download_video",
                        lambda url, **kw: calls.append(kw["quality"]) or [])
    queue = DownloadQueue(history=history, retry_delays=(0,))
    app = App(tk_root, history=history, download_queue=queue, check_ffmpeg=False)
    app.url_text.insert("1.0", "https://youtu.be/a\nhttps://youtu.be/b\n")
    app.out_var.set(str(tmp_path))

    app.fetch_resolutions()
    assert str(app.res_button.cget("state")) == "disabled"  # đang lấy
    _pump(tk_root, lambda: "Clip thật" in app.res_status_var.get())

    assert fetched == ["https://youtu.be/a"]  # chỉ video đầu tiên
    assert app.option_combo.cget("values") == ("1440p", "720p · ~10.0 MB")
    assert app.selected_quality() == "1440"
    assert "(video đầu tiên)" in app.res_status_var.get()
    assert str(app.res_button.cget("state")) == "normal"

    app.quality_var.set("720p · ~10.0 MB")
    items = app.add_from_input()
    _pump(tk_root, lambda: calls)
    assert [i.quality for i in items] == ["720", "720"]
    assert calls[0] == "720"
    # sau khi thêm: quay về danh sách chuẩn, giữ mức 720
    assert "4320p (8K)" in app.option_combo.cget("values")
    assert app.quality_var.get() == "720p"
    app.close()


def test_app_fetch_resolutions_error_reenables_button(tk_root, monkeypatch, history):
    from youtube_vault import gui
    from youtube_vault.gui import App

    def boom(url):
        raise DownloadFailedError("Video unavailable")

    errors = []
    monkeypatch.setattr(downloader, "get_info", boom)
    monkeypatch.setattr(gui.messagebox, "showerror", lambda title, msg, **kw: errors.append(msg))
    app = App(tk_root, history=history, download_queue=DownloadQueue(), check_ffmpeg=False)
    app.url_text.insert("1.0", "https://youtu.be/bad")

    app.fetch_resolutions()
    _pump(tk_root, lambda: errors)
    assert "Video unavailable" in errors[0]
    assert str(app.res_button.cget("state")) == "normal"
    assert app.selected_quality() == "best"
    app.close()
