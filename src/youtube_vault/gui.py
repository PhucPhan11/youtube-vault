"""Desktop GUI (Tkinter + ttk): download queue, history and recording.

All downloads run in ``DownloadQueue`` (background threads). Those threads only push
item ids into a ``SimpleQueue``; the Tk main thread drains it periodically (``after``)
and updates the widgets, because Tkinter is not safe to call from other threads.

All labels and messages shown to the user are Vietnamese.
"""

from __future__ import annotations

import sqlite3
import sys
import threading
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from queue import Empty, SimpleQueue
from tkinter import filedialog, messagebox, ttk

from . import config, downloader
from . import recorder as recorder_mod
from .config import (
    AUDIO_FORMATS,
    AUDIO_QUALITIES,
    COOKIES_BROWSERS,
    DEFAULT_AUDIO_FORMAT,
    DEFAULT_AUDIO_QUALITY,
    DEFAULT_EMBED_METADATA,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_QUALITY,
    MAX_WORKERS_LIMIT,
    QUALITY_PRESETS,
)
from .download_queue import DownloadItem, DownloadQueue, ItemStatus
from .downloader import (
    Cookies,
    FFmpegNotFoundError,
    MediaInfo,
    ResolutionOption,
    YoutubeVaultError,
    ffmpeg_install_instructions,
    is_ffmpeg_available,
)
from .recorder import Recorder
from .history import History
from .platform_utils import open_folder

__all__ = [
    "App",
    "bitrate_choices",
    "format_elapsed",
    "format_row",
    "main",
    "parse_urls",
    "quality_choices",
]

BEST_QUALITY_LABEL = "Tốt nhất"
BEST_BITRATE_LABEL = "Tốt nhất (VBR)"
DEFAULT_DEVICE_LABEL = "Loa mặc định"
RECORD_START_TEXT = "● Bắt đầu ghi"
RECORD_STOP_TEXT = "■ Dừng & lưu"
COOKIES_NONE_LABEL = "Không dùng"
COOKIES_FILE_LABEL = "File cookies.txt"
# Label -> browser name for yt-dlp, e.g. "Trình duyệt: firefox" -> "firefox".
COOKIES_BROWSER_LABELS: dict[str, str] = {f"Trình duyệt: {b}": b for b in COOKIES_BROWSERS}

STATUS_LABELS: dict[ItemStatus, str] = {
    ItemStatus.QUEUED: "Đang chờ",
    ItemStatus.DOWNLOADING: "Đang tải",
    ItemStatus.RETRYING: "Đang thử lại",
    ItemStatus.PAUSED: "Tạm dừng",
    ItemStatus.COMPLETED: "Hoàn tất",
    ItemStatus.SKIPPED: "Đã có (bỏ qua)",
    ItemStatus.FAILED: "Lỗi",
    ItemStatus.CANCELLED: "Đã hủy",
}

QUEUE_COLUMNS = (
    ("title", "Tiêu đề", 330),
    ("kind", "Loại", 90),
    ("status", "Trạng thái", 150),
    ("progress", "Tiến độ", 80),
    ("speed", "Tốc độ", 90),
    ("eta", "Còn lại", 70),
)
HISTORY_COLUMNS = (
    ("time", "Thời gian", 140),
    ("title", "Tiêu đề", 300),
    ("kind", "Loại", 60),
    ("file", "File", 320),
)


# --------------------------------------------------------------------------- #
# Pure helpers (easy to test, no Tk needed)
# --------------------------------------------------------------------------- #
def parse_urls(text: str) -> tuple[list[str], list[str]]:
    """Split URLs from the input box (one link per line/whitespace).

    Returns ``(valid, invalid)``; deduplicated, order kept, ``#`` lines skipped.
    """
    valid: list[str] = []
    invalid: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for token in line.split():
            target = valid if token.startswith(("http://", "https://")) else invalid
            if token not in target:
                target.append(token)
    return valid, invalid


def _format_size(n: float | None) -> str:
    if not n:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def quality_choices(
    resolutions: list[ResolutionOption] | None = None,
) -> list[tuple[str, str]]:
    """``(label, quality_value)`` choices for the "Chất lượng" (Quality) box.

    - ``None``: the standard list ("Tốt nhất", 4320p (8K), ..., 144p).
    - A video's real resolutions: labels include the estimated size.
    """
    if resolutions is None:
        return [
            (BEST_QUALITY_LABEL if key == "best" else ResolutionOption(int(key)).label, key)
            for key in QUALITY_PRESETS
        ]
    choices = []
    for opt in resolutions:
        size = _format_size(opt.filesize)
        choices.append((f"{opt.label} · ~{size}" if size else opt.label, opt.quality))
    return choices


def bitrate_choices() -> list[tuple[str, str]]:
    """``(label, value)`` choices for the "Bitrate" box (e.g. ``("320 kbps", "320")``)."""
    return [
        (BEST_BITRATE_LABEL if q == "best" else f"{q} kbps", q) for q in AUDIO_QUALITIES
    ]


def _shorten(text: str, limit: int = 45) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _format_eta(seconds: int | None) -> str:
    if seconds is None:
        return ""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def format_elapsed(seconds: float) -> str:
    """Recording duration as ``MM:SS`` (or ``H:MM:SS``)."""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def item_percent(item: DownloadItem) -> float:
    if item.status in (ItemStatus.COMPLETED, ItemStatus.SKIPPED):
        return 100.0
    if item.progress is not None and item.progress.percent is not None:
        return item.progress.percent
    return 0.0


def format_row(item: DownloadItem, max_retries: int | None = None) -> tuple[str, ...]:
    """One row of the queue table."""
    if item.kind == "video":
        kind = f"Video {item.quality}"
    else:
        kind = f"Audio {item.audio_format}"
        if item.audio_quality != "best" and item.audio_format != "wav":
            kind += f" {item.audio_quality}k"

    status = STATUS_LABELS[item.status]
    if item.status == ItemStatus.RETRYING and max_retries is not None:
        status = f"{status} ({item.attempts}/{max_retries})"
    elif item.status == ItemStatus.FAILED and item.error:
        first_line = item.error.strip().splitlines()[0] if item.error.strip() else ""
        status = f"Lỗi: {first_line}"

    progress = speed = eta = ""
    ev = item.progress
    if item.status in (ItemStatus.COMPLETED, ItemStatus.SKIPPED):
        progress = "100%"
    elif ev is not None and ev.percent is not None:
        progress = f"{ev.percent:.1f}%"
    if ev is not None and ev.playlist_index and ev.playlist_count:
        progress = f"[{ev.playlist_index}/{ev.playlist_count}] {progress}".strip()
    if item.status == ItemStatus.DOWNLOADING and ev is not None:
        speed = f"{_format_size(ev.speed)}/s" if ev.speed else ""
        eta = _format_eta(ev.eta)

    return (item.display_title, kind, status, progress, speed, eta)


# --------------------------------------------------------------------------- #
# Application
# --------------------------------------------------------------------------- #
class App(ttk.Frame):
    POLL_MS = 100

    def __init__(
        self,
        master: tk.Tk,
        *,
        history: History | None = None,
        download_queue: DownloadQueue | None = None,
        check_ffmpeg: bool = True,
        recorder_factory: Callable[..., Recorder] | None = None,
        list_devices: Callable[[], list[str]] | None = None,
    ) -> None:
        super().__init__(master, padding=10)
        self.root = master
        # Recording: both callables can be swapped in tests (no real speaker).
        self._recorder_factory = recorder_factory or recorder_mod.Recorder
        self._list_devices = list_devices or recorder_mod.list_output_devices
        self.recorder: Recorder | None = None
        self._record_stopping = False
        self._devices_loaded = False
        self.last_record_path: Path | None = None
        self.history = history if history is not None else self._open_history()
        self._updates: SimpleQueue[int] = SimpleQueue()
        # Work to run on the Tk thread, sent by background threads (drained in ``_poll``).
        self._ui_calls: SimpleQueue[Callable[[], None]] = SimpleQueue()
        self._quality_map: dict[str, str] = {}
        self.queue = download_queue or DownloadQueue(history=self.history)
        self.queue.on_update = lambda item: self._updates.put(item.id)
        self._history_refresh_needed: set[int] = set()
        self._poll_job: str | None = None
        self._closed = False

        self._build_input()
        self._build_notebook()
        self.refresh_history()
        self._poll_job = self.after(self.POLL_MS, self._poll)
        if check_ffmpeg and not is_ffmpeg_available():
            self.after(300, self._warn_ffmpeg)

    # ------------------------------ building ----------------------------- #
    def _build_input(self) -> None:
        box = ttk.LabelFrame(self, text="Thêm tải xuống", padding=8)
        box.pack(fill="x")
        box.columnconfigure(1, weight=1)

        ttk.Label(
            box,
            text="URL YouTube, Facebook,\nInstagram, X…\n(mỗi dòng một link,\ncó thể là playlist):",
        ).grid(
            row=0, column=0, sticky="nw", padx=(0, 8)
        )
        self.url_text = tk.Text(box, height=4, wrap="none", undo=True)
        self.url_text.grid(row=0, column=1, columnspan=4, sticky="ew")

        opts = ttk.Frame(box)
        opts.grid(row=1, column=1, columnspan=4, sticky="w", pady=(6, 0))
        self.kind_var = tk.StringVar(value="video")
        ttk.Radiobutton(opts, text="Video (mp4)", value="video", variable=self.kind_var,
                        command=self._on_kind_change).pack(side="left")
        ttk.Radiobutton(opts, text="Chỉ audio", value="audio", variable=self.kind_var,
                        command=self._on_kind_change).pack(side="left", padx=(8, 16))
        self.option_label = ttk.Label(opts, text="Chất lượng:")
        self.option_label.pack(side="left")
        self.quality_var = tk.StringVar()  # holds the selected LABEL; value via _quality_map
        self.audio_var = tk.StringVar(value=DEFAULT_AUDIO_FORMAT)
        self.option_combo = ttk.Combobox(opts, width=20, state="readonly",
                                         textvariable=self.quality_var)
        self.option_combo.pack(side="left", padx=(4, 4))
        self.option_combo.bind("<<ComboboxSelected>>", lambda e: self._update_audio_options())
        self._set_quality_choices(quality_choices(), select=DEFAULT_QUALITY)
        self.res_button = ttk.Button(opts, text="Xem độ phân giải",
                                     command=self.fetch_resolutions)
        self.res_button.pack(side="left", padx=(0, 16))
        self.force_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Tải lại kể cả đã có trong lịch sử",
                        variable=self.force_var).pack(side="left")

        # Audio-only options (bitrate, metadata/cover art): used by "Chỉ audio" (Audio only)
        # and the "Tải MP3" button; always shown because "Tải MP3" also works in video mode.
        audio_opts = ttk.Frame(box)
        audio_opts.grid(row=2, column=1, columnspan=4, sticky="w", pady=(6, 0))
        ttk.Label(audio_opts, text="Bitrate audio:").pack(side="left")
        self._bitrate_map = dict(bitrate_choices())
        default_bitrate = next(
            lbl for lbl, q in bitrate_choices() if q == DEFAULT_AUDIO_QUALITY)
        self.bitrate_var = tk.StringVar(value=default_bitrate)
        self.bitrate_combo = ttk.Combobox(audio_opts, width=16, state="readonly",
                                          values=list(self._bitrate_map),
                                          textvariable=self.bitrate_var)
        self.bitrate_combo.pack(side="left", padx=(4, 16))
        self.embed_var = tk.BooleanVar(value=DEFAULT_EMBED_METADATA)
        ttk.Checkbutton(audio_opts, text="Gắn thông tin + ảnh bìa",
                        variable=self.embed_var).pack(side="left")

        self.res_status_var = tk.StringVar()
        ttk.Label(box, textvariable=self.res_status_var, foreground="#555").grid(
            row=3, column=1, columnspan=4, sticky="w", pady=(2, 0))

        ttk.Label(box, text="Lưu vào:").grid(row=4, column=0, sticky="w", pady=(6, 0))
        self.out_var = tk.StringVar(value=str(DEFAULT_OUTPUT_DIR))
        ttk.Entry(box, textvariable=self.out_var).grid(row=4, column=1, sticky="ew", pady=(6, 0))
        ttk.Button(box, text="Chọn…", command=self._choose_dir).grid(
            row=4, column=2, padx=(6, 0), pady=(6, 0))
        self.add_button = ttk.Button(box, text="Thêm vào hàng đợi", command=self.add_from_input)
        self.add_button.grid(row=4, column=3, padx=(6, 0), pady=(6, 0))
        self.mp3_button = ttk.Button(box, text="Tải MP3", command=self.add_mp3_from_input)
        self.mp3_button.grid(row=4, column=4, padx=(6, 0), pady=(6, 0))

        # Login cookies: needed for private/group Facebook videos...
        ttk.Label(box, text="Cookie đăng nhập:").grid(row=5, column=0, sticky="w", pady=(6, 0))
        cookie_row = ttk.Frame(box)
        cookie_row.grid(row=5, column=1, sticky="ew", pady=(6, 0))
        cookie_row.columnconfigure(1, weight=1)
        default_file = Path(config.DEFAULT_COOKIES_FILE)
        has_default = default_file.is_file()
        self.cookies_mode_var = tk.StringVar(
            value=COOKIES_FILE_LABEL if has_default else COOKIES_NONE_LABEL)
        mode_combo = ttk.Combobox(
            cookie_row, width=22, state="readonly", textvariable=self.cookies_mode_var,
            values=[COOKIES_NONE_LABEL, COOKIES_FILE_LABEL, *COOKIES_BROWSER_LABELS])
        mode_combo.grid(row=0, column=0, sticky="w")
        mode_combo.bind("<<ComboboxSelected>>", lambda e: self._on_cookies_mode_change())
        self.cookies_file_var = tk.StringVar(value=str(default_file) if has_default else "")
        self.cookies_entry = ttk.Entry(cookie_row, textvariable=self.cookies_file_var)
        self.cookies_entry.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        ttk.Button(box, text="Chọn…", command=self._choose_cookies_file).grid(
            row=5, column=2, padx=(6, 0), pady=(6, 0))
        self._on_cookies_mode_change()

    def _build_notebook(self) -> None:
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, pady=(10, 0))
        self.queue_tab = ttk.Frame(self.notebook, padding=6)
        self.history_tab = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(self.queue_tab, text="Hàng đợi")
        self.notebook.add(self.history_tab, text="Lịch sử")
        self.record_tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.record_tab, text="Ghi âm")
        self._build_queue_tab()
        self._build_history_tab()
        self._build_record_tab()
        self.notebook.bind("<<NotebookTabChanged>>", lambda e: self._on_tab_changed())

    @staticmethod
    def _make_tree(parent: ttk.Frame, columns: tuple[tuple[str, str, int], ...]) -> ttk.Treeview:
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show="headings",
                            selectmode="extended")
        for key, label, width in columns:
            tree.heading(key, text=label)
            tree.column(key, width=width, stretch=key in ("title", "file"))
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        return tree

    def _build_queue_tab(self) -> None:
        self.queue_tree = self._make_tree(self.queue_tab, QUEUE_COLUMNS)
        self.queue_tree.tag_configure("failed", foreground="#c62828")
        self.queue_tree.tag_configure("completed", foreground="#2e7d32")
        self.queue_tree.tag_configure("muted", foreground="#757575")
        self.queue_tree.bind("<<TreeviewSelect>>", lambda e: self._update_detail())
        self.queue_tree.bind("<Double-1>", lambda e: self.open_selected_queue_folder())

        self.detail_var = tk.StringVar()
        ttk.Label(self.queue_tab, textvariable=self.detail_var, foreground="#555",
                  wraplength=820, justify="left").pack(fill="x", pady=(4, 0))

        bar = ttk.Frame(self.queue_tab)
        bar.pack(fill="x", pady=(6, 0))
        for text, cmd in (
            ("Tạm dừng", self.pause_selected),
            ("Tiếp tục", self.resume_selected),
            ("Hủy", self.cancel_selected),
            ("Thử lại", self.retry_selected),
            ("Xóa mục đã xong", self.remove_finished),
            ("Mở thư mục", self.open_selected_queue_folder),
        ):
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=(0, 4))
        self.workers_var = tk.IntVar(value=self.queue.max_workers)
        ttk.Spinbox(bar, from_=1, to=MAX_WORKERS_LIMIT, width=3, state="readonly",
                    textvariable=self.workers_var, command=self._on_workers_change).pack(side="right")
        ttk.Label(bar, text="Số luồng:").pack(side="right", padx=(0, 4))

        bottom = ttk.Frame(self.queue_tab)
        bottom.pack(fill="x", pady=(6, 0))
        self.total_progress = ttk.Progressbar(bottom, mode="determinate", maximum=100)
        self.total_progress.pack(side="left", fill="x", expand=True)
        self.summary_var = tk.StringVar(value="Hàng đợi trống")
        ttk.Label(bottom, textvariable=self.summary_var).pack(side="right", padx=(8, 0))

    def _build_history_tab(self) -> None:
        top = ttk.Frame(self.history_tab)
        top.pack(fill="x", pady=(0, 6))
        ttk.Label(top, text="Tìm:").pack(side="left")
        self.search_var = tk.StringVar()
        search = ttk.Entry(top, textvariable=self.search_var, width=40)
        search.pack(side="left", padx=(4, 0))
        search.bind("<KeyRelease>", lambda e: self.refresh_history())

        self.history_tree = self._make_tree(self.history_tab, HISTORY_COLUMNS)
        self.history_tree.bind("<Double-1>", lambda e: self.open_selected_history_folder())

        bar = ttk.Frame(self.history_tab)
        bar.pack(fill="x", pady=(6, 0))
        ttk.Button(bar, text="Mở thư mục", command=self.open_selected_history_folder).pack(
            side="left", padx=(0, 4))
        ttk.Button(bar, text="Xóa khỏi lịch sử", command=self.remove_selected_history).pack(
            side="left", padx=(0, 4))
        ttk.Button(bar, text="Làm mới", command=self.refresh_history).pack(side="left")
        self.history_info_var = tk.StringVar()
        ttk.Label(bar, textvariable=self.history_info_var).pack(side="right")

    def _build_record_tab(self) -> None:
        tab = self.record_tab
        tab.columnconfigure(1, weight=1)
        ttk.Label(
            tab,
            text=("Ghi lại âm thanh đang phát ra loa (trình duyệt, livestream, trình phát web…).\n"
                  "Lưu ý: ghi TOÀN BỘ âm thanh của máy, kể cả thông báo của ứng dụng khác."),
            foreground="#555", justify="left",
        ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))

        ttk.Label(tab, text="Loa:").grid(row=1, column=0, sticky="w")
        self.device_var = tk.StringVar(value=DEFAULT_DEVICE_LABEL)
        self.device_combo = ttk.Combobox(tab, state="readonly", textvariable=self.device_var,
                                         values=[DEFAULT_DEVICE_LABEL])
        self.device_combo.grid(row=1, column=1, sticky="ew", padx=(4, 4))
        ttk.Button(tab, text="Làm mới", command=self.refresh_devices).grid(row=1, column=2)
        self.device_status_var = tk.StringVar()
        ttk.Label(tab, textvariable=self.device_status_var, foreground="#c62828").grid(
            row=2, column=1, columnspan=3, sticky="w")

        fmt_row = ttk.Frame(tab)
        fmt_row.grid(row=3, column=1, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Label(tab, text="Định dạng:").grid(row=3, column=0, sticky="w", pady=(6, 0))
        self.rec_format_var = tk.StringVar(value=DEFAULT_AUDIO_FORMAT)
        ttk.Combobox(fmt_row, width=8, state="readonly", values=list(AUDIO_FORMATS),
                     textvariable=self.rec_format_var).pack(side="left", padx=(4, 16))
        ttk.Label(fmt_row, text="Bitrate:").pack(side="left")
        self.rec_bitrate_var = tk.StringVar(value=self.bitrate_var.get())
        ttk.Combobox(fmt_row, width=16, state="readonly", values=list(self._bitrate_map),
                     textvariable=self.rec_bitrate_var).pack(side="left", padx=(4, 16))
        ttk.Label(fmt_row, text="Lưu vào thư mục ở ô \"Lưu vào\" phía trên.",
                  foreground="#555").pack(side="left")

        ctrl = ttk.Frame(tab)
        ctrl.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(12, 0))
        self.record_button = ttk.Button(ctrl, text=RECORD_START_TEXT, width=16,
                                        command=self.toggle_recording)
        self.record_button.pack(side="left")
        self.record_time_var = tk.StringVar(value=format_elapsed(0))
        ttk.Label(ctrl, textvariable=self.record_time_var, font=("TkDefaultFont", 16)).pack(
            side="left", padx=(12, 12))
        self.record_level = ttk.Progressbar(ctrl, mode="determinate", maximum=100, length=220)
        self.record_level.pack(side="left")

        self.record_status_var = tk.StringVar()
        ttk.Label(tab, textvariable=self.record_status_var, wraplength=760, justify="left").grid(
            row=5, column=0, columnspan=4, sticky="w", pady=(10, 0))
        self.record_open_button = ttk.Button(tab, text="Mở thư mục", state="disabled",
                                             command=self.open_last_record)
        self.record_open_button.grid(row=6, column=0, sticky="w", pady=(6, 0))

    # ------------------------------ recording ---------------------------- #
    def _on_tab_changed(self) -> None:
        if self.notebook.select() == str(self.record_tab) and not self._devices_loaded:
            self.refresh_devices()

    def refresh_devices(self) -> None:
        """Load the speaker list on a background thread (loading the audio library can be slow)."""
        self._devices_loaded = True
        self.device_status_var.set("")

        def work() -> None:  # background thread: only send results through _ui_calls
            try:
                names = self._list_devices()
            except Exception as exc:
                self._ui_calls.put(lambda e=exc: self.device_status_var.set(str(e)))
            else:
                self._ui_calls.put(lambda: self._on_devices_loaded(names))

        threading.Thread(target=work, daemon=True, name="yv-devices").start()

    def _on_devices_loaded(self, names: list[str]) -> None:
        values = [DEFAULT_DEVICE_LABEL, *names]
        self.device_combo.configure(values=values)
        if self.device_var.get() not in values:
            self.device_var.set(DEFAULT_DEVICE_LABEL)

    def selected_device(self) -> str | None:
        name = self.device_var.get()
        return None if name in ("", DEFAULT_DEVICE_LABEL) else name

    @property
    def is_recording(self) -> bool:
        return self.recorder is not None and not self._record_stopping

    def toggle_recording(self) -> None:
        if self._record_stopping:
            return
        if self.recorder is None:
            self.start_recording()
        else:
            self.stop_recording()

    def start_recording(self) -> None:
        out = self.out_var.get().strip() or str(DEFAULT_OUTPUT_DIR)
        quality = self._bitrate_map.get(self.rec_bitrate_var.get(), DEFAULT_AUDIO_QUALITY)
        try:
            rec = self._recorder_factory(out, self.rec_format_var.get(), quality,
                                         self.selected_device())
            path = rec.start()
        except FFmpegNotFoundError:
            self._warn_ffmpeg()
            return
        except (YoutubeVaultError, ValueError, OSError) as exc:
            messagebox.showerror("Không ghi âm được", str(exc), parent=self.root)
            return
        self.recorder = rec
        self.record_button.configure(text=RECORD_STOP_TEXT)
        self.record_status_var.set(f"Đang ghi vào: {path}")

    def stop_recording(self) -> None:
        """Stop recording; ffmpeg saves the file on a background thread and reports via ``_ui_calls``."""
        rec = self.recorder
        if rec is None or self._record_stopping:
            return
        self._record_stopping = True
        self.record_button.configure(text="Đang lưu…", state="disabled")

        def work() -> None:
            try:
                path = rec.stop()
            except Exception as exc:
                self._ui_calls.put(lambda e=exc: self._on_record_finished(None, e))
            else:
                self._ui_calls.put(lambda: self._on_record_finished(path, None))

        threading.Thread(target=work, daemon=True, name="yv-record-stop").start()

    def _on_record_finished(self, path: Path | None, error: Exception | None) -> None:
        elapsed = self.recorder.elapsed if self.recorder is not None else 0.0
        self.recorder = None
        self._record_stopping = False
        self.record_button.configure(text=RECORD_START_TEXT, state="normal")
        self.record_level["value"] = 0
        if error is not None:
            self.record_status_var.set(f"Lỗi khi ghi âm: {error}")
            messagebox.showerror("Ghi âm thất bại", str(error), parent=self.root)
            return
        self.last_record_path = path
        self.record_status_var.set(f"Đã lưu ({format_elapsed(elapsed)}): {path}")
        self.record_open_button.configure(state="normal")

    def _update_record_status(self) -> None:
        rec = self.recorder
        if rec is None or self._record_stopping:
            return
        self.record_time_var.set(format_elapsed(rec.elapsed))
        self.record_level["value"] = rec.level * 100
        if rec.error is not None:  # device failed midway -> stop; stop() reports the error
            self.stop_recording()

    def open_last_record(self) -> None:
        if self.last_record_path is not None:
            self._open(self.last_record_path)

    # -------------------------------- input ------------------------------ #
    def _on_kind_change(self) -> None:
        if self.kind_var.get() == "audio":
            self.option_label.configure(text="Định dạng:")
            self.option_combo.configure(values=list(AUDIO_FORMATS), textvariable=self.audio_var)
            self.res_button.configure(state="disabled")
        else:
            self.option_label.configure(text="Chất lượng:")
            self.option_combo.configure(values=list(self._quality_map),
                                        textvariable=self.quality_var)
            self.res_button.configure(state="normal")
        self._update_audio_options()

    def _update_audio_options(self) -> None:
        """wav has no bitrate -> disable the "Bitrate" box while audio wav is selected."""
        is_wav = self.kind_var.get() == "audio" and self.audio_var.get() == "wav"
        self.bitrate_combo.configure(state="disabled" if is_wav else "readonly")

    def selected_bitrate(self) -> str:
        """Bitrate value ("best", "320"...) for the selected label."""
        return self._bitrate_map.get(self.bitrate_var.get(), DEFAULT_AUDIO_QUALITY)

    def selected_quality(self) -> str:
        """Quality value ("best", "1080"...) for the selected label."""
        return self._quality_map.get(self.quality_var.get(), DEFAULT_QUALITY)

    def _set_quality_choices(self, choices: list[tuple[str, str]], select: str) -> None:
        """Replace the "Chất lượng" (Quality) list and select the entry whose value is ``select``."""
        self._quality_map = dict(choices)
        label = next((lbl for lbl, value in choices if value == select), choices[0][0])
        self.quality_var.set(label)
        if self.kind_var.get() == "video":
            self.option_combo.configure(values=list(self._quality_map))

    def fetch_resolutions(self) -> None:
        """Fetch the real resolutions of the first video in the URL box (on a background thread)."""
        urls, _ = parse_urls(self.url_text.get("1.0", "end"))
        if not urls:
            messagebox.showinfo("Chưa có URL", "Hãy dán link video cần xem độ phân giải.",
                                parent=self.root)
            return
        try:
            cookies = self.selected_cookies()
        except ValueError as exc:
            messagebox.showwarning("Cookie đăng nhập", str(exc), parent=self.root)
            return
        url = urls[0]
        note = " (video đầu tiên)" if len(urls) > 1 else ""
        self.res_button.configure(state="disabled")
        self.res_status_var.set("Đang lấy độ phân giải…")

        def work() -> None:  # background thread: NEVER touch widgets, only send results via _ui_calls
            try:
                media = downloader.get_info(url, cookies=cookies)
                options = downloader.list_resolutions(media)
            except Exception as exc:
                self._ui_calls.put(lambda e=exc: self._on_resolutions_failed(e))
            else:
                self._ui_calls.put(
                    lambda: self._on_resolutions_loaded(url, media, options, note))

        threading.Thread(target=work, daemon=True, name="yv-resolutions").start()

    def _on_resolutions_loaded(
        self, url: str, media: MediaInfo, options: list[ResolutionOption], note: str
    ) -> None:
        self._on_kind_change()  # re-enable the button (if in video mode)
        if not options:
            reason = "Playlist" if media.is_playlist else "Không có thông tin độ phân giải"
            self._set_quality_choices(quality_choices(), select=self.selected_quality())
            self.res_status_var.set(f"{reason} — dùng danh sách chuẩn")
            return
        self._set_quality_choices(quality_choices(options), select=options[0].quality)
        self.res_status_var.set(f"Độ phân giải theo: {_shorten(media.title or url)}{note}")

    def _on_resolutions_failed(self, exc: Exception) -> None:
        self._on_kind_change()
        self.res_status_var.set("")
        messagebox.showerror("Không lấy được độ phân giải", str(exc), parent=self.root)

    def _choose_dir(self) -> None:
        chosen = filedialog.askdirectory(parent=self.root, initialdir=self.out_var.get() or None)
        if chosen:
            self.out_var.set(chosen)

    def _choose_cookies_file(self) -> None:
        chosen = filedialog.askopenfilename(
            parent=self.root,
            title="Chọn file cookies.txt",
            filetypes=[("cookies.txt", "*.txt"), ("Tất cả", "*.*")],
        )
        if chosen:
            self.cookies_file_var.set(chosen)
            self.cookies_mode_var.set(COOKIES_FILE_LABEL)
            self._on_cookies_mode_change()

    def _on_cookies_mode_change(self) -> None:
        state = "normal" if self.cookies_mode_var.get() == COOKIES_FILE_LABEL else "disabled"
        self.cookies_entry.configure(state=state)

    def selected_cookies(self) -> Cookies | None:
        """Selected cookie source. Raises ``ValueError`` if the file does not exist."""
        mode = self.cookies_mode_var.get()
        if mode == COOKIES_FILE_LABEL:
            path = self.cookies_file_var.get().strip()
            if not path:
                raise ValueError("Hãy chọn file cookies.txt.")
            return Cookies.resolve(file=path)
        if mode in COOKIES_BROWSER_LABELS:
            return Cookies.resolve(browser=COOKIES_BROWSER_LABELS[mode])
        return None

    def add_from_input(self) -> list[DownloadItem]:
        """The "Thêm vào hàng đợi" (Add to queue) button: uses the selected kind/format."""
        return self._add_urls(self.kind_var.get(), self.audio_var.get())

    def add_mp3_from_input(self) -> list[DownloadItem]:
        """The "Tải MP3" (Download MP3) button: always mp3 audio, whatever kind is selected."""
        return self._add_urls("audio", "mp3")

    def _add_urls(self, kind: str, audio_format: str) -> list[DownloadItem]:
        urls, invalid = parse_urls(self.url_text.get("1.0", "end"))
        if invalid:
            messagebox.showwarning(
                "URL không hợp lệ",
                "Bỏ qua các dòng không phải link http(s):\n" + "\n".join(invalid[:10]),
                parent=self.root,
            )
        if not urls:
            if not invalid:
                messagebox.showinfo("Chưa có URL", "Hãy dán ít nhất một link video.",
                                    parent=self.root)
            return []

        try:
            cookies = self.selected_cookies()
        except ValueError as exc:
            messagebox.showwarning("Cookie đăng nhập", str(exc), parent=self.root)
            return []
        out = self.out_var.get().strip() or str(DEFAULT_OUTPUT_DIR)
        quality = self.selected_quality()
        items = [
            self.queue.add(
                url,
                kind=kind,
                quality=quality,
                audio_format=audio_format,
                output_dir=out,
                force=self.force_var.get(),
                audio_quality=self.selected_bitrate(),
                embed_metadata=self.embed_var.get(),
                cookies=cookies,
            )
            for url in urls
        ]
        self.url_text.delete("1.0", "end")
        # The real resolution list only applied to the video just added -> back to the standard list.
        keep = quality if quality in QUALITY_PRESETS else DEFAULT_QUALITY
        self._set_quality_choices(quality_choices(), select=keep)
        self.res_status_var.set("")
        self.notebook.select(self.queue_tab)
        return items

    # -------------------------------- queue ------------------------------ #
    def _selected_queue_items(self) -> list[DownloadItem]:
        items = []
        for iid in self.queue_tree.selection():
            item = self.queue.get(int(iid))
            if item is not None:
                items.append(item)
        return items

    def pause_selected(self) -> None:
        for item in self._selected_queue_items():
            self.queue.pause(item.id)

    def resume_selected(self) -> None:
        for item in self._selected_queue_items():
            if item.status == ItemStatus.PAUSED:
                self.queue.resume(item.id)

    def retry_selected(self) -> None:
        for item in self._selected_queue_items():
            if item.status in (ItemStatus.FAILED, ItemStatus.CANCELLED):
                self.queue.retry(item.id)

    def cancel_selected(self) -> None:
        for item in self._selected_queue_items():
            self.queue.cancel(item.id)

    def remove_finished(self) -> None:
        for item_id in self.queue.remove_finished():
            if self.queue_tree.exists(str(item_id)):
                self.queue_tree.delete(str(item_id))
        self._update_summary()
        self._update_detail()

    def open_selected_queue_folder(self) -> None:
        items = self._selected_queue_items()
        if items:
            item = items[0]
            self._open(item.files[0] if item.files else item.output_dir)

    def _on_workers_change(self) -> None:
        self.queue.set_max_workers(self.workers_var.get())

    def _render_item(self, item: DownloadItem) -> None:
        iid = str(item.id)
        values = format_row(item, self.queue.max_retries + 1)
        tags: tuple[str, ...] = ()
        if item.status == ItemStatus.FAILED:
            tags = ("failed",)
        elif item.status == ItemStatus.COMPLETED:
            tags = ("completed",)
        elif item.status in (ItemStatus.SKIPPED, ItemStatus.CANCELLED, ItemStatus.PAUSED):
            tags = ("muted",)
        if self.queue_tree.exists(iid):
            self.queue_tree.item(iid, values=values, tags=tags)
        else:
            self.queue_tree.insert("", "end", iid=iid, values=values, tags=tags)

    def _update_summary(self) -> None:
        items = [i for i in self.queue.items() if i.status != ItemStatus.CANCELLED]
        if not items:
            self.total_progress["value"] = 0
            self.summary_var.set("Hàng đợi trống")
            return
        self.total_progress["value"] = sum(item_percent(i) for i in items) / len(items)

        def count(*statuses: ItemStatus) -> int:
            return sum(1 for i in items if i.status in statuses)

        self.summary_var.set(
            f"Đang tải {count(ItemStatus.DOWNLOADING, ItemStatus.RETRYING)} · "
            f"Chờ {count(ItemStatus.QUEUED)} · "
            f"Xong {count(ItemStatus.COMPLETED, ItemStatus.SKIPPED)} · "
            f"Lỗi {count(ItemStatus.FAILED)}"
        )

    def _update_detail(self) -> None:
        items = self._selected_queue_items()
        if not items:
            self.detail_var.set("")
            return
        item = items[0]
        parts = [item.url]
        if item.error:
            parts.append(f"Lỗi: {item.error}")
        if item.files:
            parts.append("File: " + ", ".join(str(f) for f in item.files))
        self.detail_var.set("\n".join(parts))

    def _poll(self) -> None:
        while True:  # work sent by background threads (e.g. "Xem độ phân giải" results)
            try:
                call = self._ui_calls.get_nowait()
            except Empty:
                break
            try:
                call()
            except Exception:  # report like Tk does, but keep the _poll loop alive
                self.root.report_callback_exception(*sys.exc_info())

        changed: set[int] = set()
        try:
            while True:
                changed.add(self._updates.get_nowait())
        except Empty:
            pass

        refresh_history = False
        for item_id in changed:
            item = self.queue.get(item_id)
            if item is None:
                continue
            self._render_item(item)
            if item.status == ItemStatus.COMPLETED and item_id not in self._history_refresh_needed:
                self._history_refresh_needed.add(item_id)
                refresh_history = True
        if changed:
            self._update_summary()
            self._update_detail()
        if refresh_history:
            self.refresh_history()
        self._update_record_status()
        if not self._closed:
            self._poll_job = self.after(self.POLL_MS, self._poll)

    # ------------------------------- history ----------------------------- #
    @staticmethod
    def _open_history() -> History | None:
        try:
            return History()
        except (OSError, sqlite3.Error):
            return None

    def refresh_history(self) -> None:
        tree = self.history_tree
        tree.delete(*tree.get_children())
        if self.history is None:
            self.history_info_var.set("Không mở được file lịch sử.")
            return
        records = self.history.list(limit=500, search=self.search_var.get().strip() or None)
        for r in records:
            tree.insert("", "end", iid=str(r.id), values=(
                r.downloaded_at.replace("T", " "), r.title or "?", r.kind, r.filepath or ""))
        self.history_info_var.set(f"{len(records)} mục")

    def _selected_history_ids(self) -> list[int]:
        return [int(iid) for iid in self.history_tree.selection()]

    def open_selected_history_folder(self) -> None:
        ids = self._selected_history_ids()
        if not ids or self.history is None:
            return
        record = self.history.get_by_id(ids[0])
        if record is not None and record.filepath:
            self._open(record.filepath)

    def remove_selected_history(self) -> None:
        ids = self._selected_history_ids()
        if not ids or self.history is None:
            return
        if not messagebox.askyesno(
            "Xóa khỏi lịch sử",
            f"Xóa {len(ids)} mục khỏi lịch sử?\n(File đã tải vẫn được giữ nguyên.)",
            parent=self.root,
        ):
            return
        for record_id in ids:
            self.history.remove(record_id)
        self.refresh_history()

    # -------------------------------- misc ------------------------------- #
    def _open(self, path: Path | str) -> None:
        try:
            open_folder(path)
        except (FileNotFoundError, OSError) as exc:
            messagebox.showerror("Không mở được thư mục", str(exc), parent=self.root)

    def _warn_ffmpeg(self) -> None:
        messagebox.showwarning("Chưa cài ffmpeg", ffmpeg_install_instructions(), parent=self.root)

    def close(self) -> None:
        """Pause running downloads (keeping .part files to resume later), then close the window."""
        if self._closed:
            return
        self._closed = True
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except tk.TclError:
                pass
        if self.recorder is not None and not self._record_stopping:
            try:  # save an active recording before closing
                self.recorder.stop()
            except Exception:
                pass
        self.queue.shutdown(timeout=3)
        self.root.destroy()


def _enable_dpi_awareness() -> None:
    try:  # crisp text on scaled Windows displays
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # type: ignore[attr-defined]
    except Exception:
        pass


def main() -> None:
    _enable_dpi_awareness()
    root = tk.Tk()
    root.title("youtube-vault")
    root.geometry("960x640")
    root.minsize(760, 480)
    app = App(root)
    app.pack(fill="both", expand=True)
    root.protocol("WM_DELETE_WINDOW", app.close)
    root.mainloop()


if __name__ == "__main__":
    main()
