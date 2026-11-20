from __future__ import annotations

import time
from pathlib import Path

import pytest

from youtube_vault import downloader
from youtube_vault.download_queue import DownloadItem, DownloadQueue, ItemStatus
from youtube_vault.downloader import ProgressEvent
from youtube_vault.gui import format_row, item_percent, parse_urls


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
    app.kind_var.set("video")
    app._on_kind_change()
    assert "1080" in app.option_combo.cget("values")
    app.close()
