"""Giao diện desktop (Tkinter + ttk): hàng đợi tải và lịch sử tải.

Mọi việc tải chạy trong ``DownloadQueue`` (luồng nền). Luồng nền chỉ đẩy id của
item vào một ``SimpleQueue``; luồng chính của Tk đọc ra định kỳ (``after``) và
cập nhật giao diện, vì Tkinter không an toàn khi gọi từ luồng khác.
"""

from __future__ import annotations

import sqlite3
import tkinter as tk
from pathlib import Path
from queue import Empty, SimpleQueue
from tkinter import filedialog, messagebox, ttk

from .config import (
    AUDIO_FORMATS,
    DEFAULT_AUDIO_FORMAT,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_QUALITY,
    MAX_WORKERS_LIMIT,
    QUALITY_PRESETS,
)
from .download_queue import DownloadItem, DownloadQueue, ItemStatus
from .downloader import ffmpeg_install_instructions, is_ffmpeg_available
from .history import History
from .platform_utils import open_folder

__all__ = ["App", "format_row", "main", "parse_urls"]

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
# Hàm thuần (dễ test, không cần Tk)
# --------------------------------------------------------------------------- #
def parse_urls(text: str) -> tuple[list[str], list[str]]:
    """Tách URL từ ô nhập (mỗi dòng/khoảng trắng một link).

    Trả về ``(hợp_lệ, không_hợp_lệ)``; bỏ trùng, giữ thứ tự, bỏ dòng ``#``.
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


def _format_eta(seconds: int | None) -> str:
    if seconds is None:
        return ""
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
    """Một dòng của bảng hàng đợi."""
    kind = f"Video {item.quality}" if item.kind == "video" else f"Audio {item.audio_format}"

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
# Ứng dụng
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
    ) -> None:
        super().__init__(master, padding=10)
        self.root = master
        self.history = history if history is not None else self._open_history()
        self._updates: SimpleQueue[int] = SimpleQueue()
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

    # ---------------------------- dựng giao diện -------------------------- #
    def _build_input(self) -> None:
        box = ttk.LabelFrame(self, text="Thêm tải xuống", padding=8)
        box.pack(fill="x")
        box.columnconfigure(1, weight=1)

        ttk.Label(box, text="URL (mỗi dòng một link,\ncó thể là playlist):").grid(
            row=0, column=0, sticky="nw", padx=(0, 8)
        )
        self.url_text = tk.Text(box, height=4, wrap="none", undo=True)
        self.url_text.grid(row=0, column=1, columnspan=3, sticky="ew")

        opts = ttk.Frame(box)
        opts.grid(row=1, column=1, columnspan=3, sticky="w", pady=(6, 0))
        self.kind_var = tk.StringVar(value="video")
        ttk.Radiobutton(opts, text="Video (mp4)", value="video", variable=self.kind_var,
                        command=self._on_kind_change).pack(side="left")
        ttk.Radiobutton(opts, text="Chỉ audio", value="audio", variable=self.kind_var,
                        command=self._on_kind_change).pack(side="left", padx=(8, 16))
        self.option_label = ttk.Label(opts, text="Chất lượng:")
        self.option_label.pack(side="left")
        self.quality_var = tk.StringVar(value=DEFAULT_QUALITY)
        self.audio_var = tk.StringVar(value=DEFAULT_AUDIO_FORMAT)
        self.option_combo = ttk.Combobox(opts, width=8, state="readonly",
                                         values=list(QUALITY_PRESETS), textvariable=self.quality_var)
        self.option_combo.pack(side="left", padx=(4, 16))
        self.force_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Tải lại kể cả đã có trong lịch sử",
                        variable=self.force_var).pack(side="left")

        ttk.Label(box, text="Lưu vào:").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.out_var = tk.StringVar(value=str(DEFAULT_OUTPUT_DIR))
        ttk.Entry(box, textvariable=self.out_var).grid(row=2, column=1, sticky="ew", pady=(6, 0))
        ttk.Button(box, text="Chọn…", command=self._choose_dir).grid(
            row=2, column=2, padx=(6, 0), pady=(6, 0))
        self.add_button = ttk.Button(box, text="Thêm vào hàng đợi", command=self.add_from_input)
        self.add_button.grid(row=2, column=3, padx=(6, 0), pady=(6, 0))

    def _build_notebook(self) -> None:
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, pady=(10, 0))
        self.queue_tab = ttk.Frame(self.notebook, padding=6)
        self.history_tab = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(self.queue_tab, text="Hàng đợi")
        self.notebook.add(self.history_tab, text="Lịch sử")
        self._build_queue_tab()
        self._build_history_tab()

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

    # ------------------------------ nhập liệu ----------------------------- #
    def _on_kind_change(self) -> None:
        if self.kind_var.get() == "audio":
            self.option_label.configure(text="Định dạng:")
            self.option_combo.configure(values=list(AUDIO_FORMATS), textvariable=self.audio_var)
        else:
            self.option_label.configure(text="Chất lượng:")
            self.option_combo.configure(values=list(QUALITY_PRESETS), textvariable=self.quality_var)

    def _choose_dir(self) -> None:
        chosen = filedialog.askdirectory(parent=self.root, initialdir=self.out_var.get() or None)
        if chosen:
            self.out_var.set(chosen)

    def add_from_input(self) -> list[DownloadItem]:
        urls, invalid = parse_urls(self.url_text.get("1.0", "end"))
        if invalid:
            messagebox.showwarning(
                "URL không hợp lệ",
                "Bỏ qua các dòng không phải link http(s):\n" + "\n".join(invalid[:10]),
                parent=self.root,
            )
        if not urls:
            if not invalid:
                messagebox.showinfo("Chưa có URL", "Hãy dán ít nhất một link YouTube.",
                                    parent=self.root)
            return []

        out = self.out_var.get().strip() or str(DEFAULT_OUTPUT_DIR)
        items = [
            self.queue.add(
                url,
                kind=self.kind_var.get(),
                quality=self.quality_var.get(),
                audio_format=self.audio_var.get(),
                output_dir=out,
                force=self.force_var.get(),
            )
            for url in urls
        ]
        self.url_text.delete("1.0", "end")
        self.notebook.select(self.queue_tab)
        return items

    # ------------------------------ hàng đợi ------------------------------ #
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
        if not self._closed:
            self._poll_job = self.after(self.POLL_MS, self._poll)

    # ------------------------------- lịch sử ------------------------------ #
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

    # -------------------------------- khác -------------------------------- #
    def _open(self, path: Path | str) -> None:
        try:
            open_folder(path)
        except (FileNotFoundError, OSError) as exc:
            messagebox.showerror("Không mở được thư mục", str(exc), parent=self.root)

    def _warn_ffmpeg(self) -> None:
        messagebox.showwarning("Chưa cài ffmpeg", ffmpeg_install_instructions(), parent=self.root)

    def close(self) -> None:
        """Tạm dừng các mục đang tải (giữ .part để lần sau tải tiếp) rồi đóng cửa sổ."""
        if self._closed:
            return
        self._closed = True
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except tk.TclError:
                pass
        self.queue.shutdown(timeout=3)
        self.root.destroy()


def _enable_dpi_awareness() -> None:
    try:  # chữ sắc nét trên màn hình Windows có scale
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
